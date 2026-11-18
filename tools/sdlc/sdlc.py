#!/usr/bin/env python3
"""Gated SDLC state machine for epics: Refined -> Planned -> Building -> Review -> Security -> Harness -> Ready -> Released.

Five gates, in this order: plan, unit, review, security, harness. The harness is the last gate. Review,
security and the harness all judge the merged baseline: the epic branch with the main branch merged in.

State lives in GitHub, not here:
  - gate records = hidden-JSON comments on the epic issue (the audit log),
  - mirrored as commit statuses sdlc/<gate> on the epic branch head (shown on the PR).
The phase is DERIVED from the records: the first gate that is not a fresh pass. A pass goes stale when
code (anything outside paths.exempt in the delivery profile) changes after the SHA it passed on.

Project details come from the delivery profile (.claude/delivery-profile.json, or $SDLC_PROFILE).
Python 3 standard library only. Needs git, and gh for the GitHub calls.

Usage:
  sdlc.py status <N>                              gates + phase for epic #N
  sdlc.py next <N>                                next gate and what to run
  sdlc.py advance <N> <gate> [--evidence FILE] [--rerun FILE]...   validate evidence, record pass/fail
  sdlc.py check-harness <N> --evidence F [--rerun R]   the harness gate's checks only, nothing recorded
  sdlc.py gate-tags <N>                           the harness gate run's --tag flags
  sdlc.py lint-lanes <lanes.json>                 the plan's lane rules (lane size, one lane blocking most)
  sdlc.py check <N> | --branch B                  exit 0 only if every gate is a fresh pass
  sdlc.py guard                                   PreToolUse hook: block epic merges/pushes that fail check
  sdlc.py render [--out F]                        delivery board page
  sdlc.py selftest
"""
import argparse, glob, json, os, re, shlex, subprocess, sys, tempfile, time
from datetime import datetime, timezone

ROOT = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip() or os.getcwd()


def load_profile(root):
    p = os.environ.get("SDLC_PROFILE") or os.path.join(root, ".claude", "delivery-profile.json")
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        return json.load(f)


def cfg(path, default=None, prof=None):
    """A dotted key from the profile: cfg('git.main_branch', 'main')."""
    cur = PROFILE if prof is None else prof
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return default if cur is None else cur


def repo_from_remote(root):
    url = subprocess.run(["git", "-C", root, "remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip()
    m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", url)
    return m.group(1) if m else ""


PROFILE = load_profile(ROOT)


def configure():
    """(Re)read the module settings from PROFILE. The selftest calls it after it swaps PROFILE."""
    global REPO, MAIN, REMOTE, MAIN_REF, EPIC_PREFIX, EPICS_DIR, EXEMPT, STATUS_ONLY, STATUS_LINE, LIVE_MARKERS
    REPO = PROFILE.get("repo") or repo_from_remote(ROOT)
    MAIN = cfg("git.main_branch", "main")
    REMOTE = cfg("git.remote", "origin")
    MAIN_REF = f"{REMOTE}/{MAIN}"
    EPIC_PREFIX = cfg("git.epic_prefix", "epic/")
    EPICS_DIR = cfg("paths.epics", ".claude/epics")
    EXEMPT = re.compile("|".join(cfg("paths.exempt", [r"^\.claude/epics/", r"^\.claude/handoffs/", r"^docs/"])))
    STATUS_ONLY = tuple(cfg("paths.status_only.files", []))
    STATUS_LINE = re.compile(cfg("paths.status_only.line", r'^[+-]\s*"?status"?:\s'))
    LIVE_MARKERS = [re.compile(m) for m in cfg("live.markers", [])]


configure()
GATES = ["plan", "unit", "review", "security", "harness"]
PHASE = {"plan": "Refined", "unit": "Building", "review": "Review", "security": "Security", "harness": "Harness"}
NEXT = {"plan": "plan the epic (/ultra-epic #{n}, step 1) and get the human's Approved: line",
        "unit": "build and merge the lanes, then `sdlc.py advance {n} unit` (after only a main merge: just the advance)",
        "review": "merge the main branch into the epic branch, re-run unit, then the review workflow",
        "security": "the security part of the final workflow on the merged baseline",
        "harness": "the harness run on the merged baseline (last gate)",
        None: "write the PR summary and wait for DEPLOY"}
HEAD_SHA = re.compile(r"(?im)^(?:[^\n]*\bHead\b[^\n]*?@\s*|(?:Reviewed-SHA|Head-SHA|SHA):\s*)`?([0-9a-f]{7,40})\b")
OWNER_ALLOWED = ("github-actions[bot]",)
FROM_MAIN_OK = ("review", "security")  # they judge the epic's own code; files merged in unchanged from main were judged there
MARK = re.compile(r"<!-- sdlc (\{.*?\}) -->")
GUARD_BUDGET = 20  # seconds; the PreToolUse hook times out at 30 and a timed-out hook lets the command run (fail open)
DEADLINE = None    # set by guard: every later call gets what is left, and a call past it fails (exit 124): fail closed


def sh(cmd, cwd=None, check=False, env=None):
    left = None if DEADLINE is None else max(0.1, DEADLINE - time.monotonic())
    try:  # stdin closed: no child waits on input (the guard's stdin is the hook payload)
        r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, cwd=cwd or ROOT, env=env,
                           stdin=subprocess.DEVNULL, timeout=left)
    except subprocess.TimeoutExpired:
        r = subprocess.CompletedProcess(cmd, 124, "", f"timed out: the guard has {GUARD_BUDGET}s for all lookups")
    if check and r.returncode:
        sys.exit(f"{cmd} failed: {r.stderr[:400]}")
    return r


def git(*a, cwd=None):
    return sh(["git", *a], cwd=cwd).stdout.strip()


# ---------------------------------------------------------------- pure state logic

def fresh(rec, head, cwd=None):
    """A pass stays fresh while every change since its SHA is exempt. The plan never goes stale."""
    if rec["gate"] == "plan":
        return True
    if sh(["git", "merge-base", "--is-ancestor", rec["sha"], head], cwd=cwd).returncode:
        return False
    # --no-renames: a move reports its source path too (a move into an exempt dir is not exempt)
    changed = [p for p in git("diff", "--no-renames", "--name-only", rec["sha"], head, cwd=cwd).splitlines() if p]
    own = not_from_main(rec["sha"], head, cwd) if rec["gate"] in FROM_MAIN_OK and changed else None
    return all(EXEMPT.search(p) or (p in STATUS_ONLY and status_only(rec["sha"], head, p, cwd))
               or (own is not None and p not in own) for p in changed)


def names(*a, cwd=None):
    return set(x for x in git(*a, "--no-renames", "--name-only", cwd=cwd).splitlines() if x)


def not_from_main(sha, head, cwd=None):
    """Paths changed in sha..head by anything other than a main merge. A path is excused only when every
    first-parent commit after sha that touched it is a merge whose second parent is on the main ref and which
    took the path unchanged from that parent. None when the main ref is unknown: nothing excused."""
    if sh(["git", "rev-parse", "-q", "--verify", MAIN_REF + "^{commit}"], cwd=cwd).returncode:
        return None
    own = set()
    for line in git("log", "--first-parent", "--format=%H %P", f"{sha}..{head}", cwd=cwd).splitlines():
        c, *parents = line.split()
        touched = names("diff", parents[0], c, cwd=cwd) if parents else set()
        if len(parents) > 1 and not sh(["git", "merge-base", "--is-ancestor", parents[1], MAIN_REF], cwd=cwd).returncode:
            touched &= names("diff", parents[1], c, cwd=cwd)  # keep only what differs from main's side
        own |= touched
    return own


def main_merge_only(rec, head, cwd=None):
    """True when every code change since rec's SHA came in unchanged from a main merge."""
    own = not_from_main(rec["sha"], head, cwd)
    return own is not None and not any(not EXEMPT.search(p) for p in own)


def status_only(base, head, path, cwd=None):
    lines = git("diff", "-U0", base, head, "--", path, cwd=cwd).splitlines()
    return all(STATUS_LINE.match(x) for x in lines if x[:1] in "+-" and not x.startswith(("+++", "---")))


def gate_states(records, head, cwd=None):
    """{gate: pass|stale|fail|missing} from the latest record of each gate."""
    latest = {}
    for r in records:
        latest[r["gate"]] = r
    out = {}
    for g in GATES:
        r = latest.get(g)
        out[g] = "missing" if not r else "fail" if r["status"] != "pass" else "pass" if fresh(r, head, cwd) else "stale"
    return out, latest


def derive(records, head, merged=False, cwd=None):
    """(phase, next_gate, states). next_gate None = all gates fresh."""
    states, latest = gate_states(records, head, cwd)
    nxt = next((g for g in GATES if states[g] != "pass"), None)
    if nxt is None:
        return ("Released" if merged else "Ready"), None, states
    if nxt == "unit" and "plan" in latest and fresh({**latest["plan"], "gate": "x"}, head, cwd):
        return "Planned", nxt, states  # plan approved, no code yet
    if nxt == "unit" and latest.get("unit", {}).get("status") == "pass" and main_merge_only(latest["unit"], head, cwd):
        # only a main merge since the unit pass: re-run unit, and the epic stays in the phase it is in
        return PHASE[next((g for g in GATES[2:] if states[g] != "pass"), "harness")], nxt, states
    return PHASE[nxt], nxt, states


def legal(gate, nxt):
    """A gate may be recorded only once every earlier gate is a fresh pass (re-running an earlier gate is fine)."""
    if gate not in GATES:
        return False, f"unknown gate '{gate}'. Gates: {GATES}"
    if nxt is not None and GATES.index(gate) > GATES.index(nxt):
        return False, f"illegal transition: '{gate}' needs '{nxt}' to pass first"
    return True, ""


# ---------------------------------------------------------------- plan rules (v3)

def lane_problems(lanes, max_tickets=5, max_block_share=0.5):
    """Lane rules from the second run: a lane holds at most max_tickets tickets, and no lane blocks (directly or
    through others) more than max_block_share of the other lanes. One long lane that blocks most others turns the
    parallel build into a queue. Returns a list of problems; empty means the lane graph is fine."""
    out = []
    ids = [l["id"] for l in lanes]
    deps = {l["id"]: set(l.get("deps") or []) for l in lanes}
    for l in lanes:
        if len(l.get("tickets") or []) > max_tickets:
            out.append(f"lane {l['id']} has {len(l['tickets'])} tickets (max {max_tickets}): split it by file")
    if len(ids) > 2:
        for x in ids:
            seen, todo = set(), [y for y in ids if x in deps[y]]
            while todo:
                y = todo.pop()
                if y not in seen:
                    seen.add(y)
                    todo += [z for z in ids if y in deps[z]]
            if len(seen) > max_block_share * (len(ids) - 1):
                out.append(f"lane {x} blocks {len(seen)} of the {len(ids) - 1} other lanes (max share {max_block_share}): "
                           "split it or cut a dependency")
    return out


# ---------------------------------------------------------------- evidence validators -> (ok, detail)

def v_plan(epic_dir, evidence, head, cwd=None):
    p = evidence or os.path.join(epic_dir, "plan.md")
    if not os.path.exists(p):
        return False, f"missing evidence: {p}"
    t = open(p).read()
    if not re.search(r"(?im)^#+\s*test plan", t):
        return False, "plan.md has no '## Test plan' section"
    if not re.search(r"(?m)^Approved:\s*\S", t):
        return False, "plan.md has no 'Approved: <who, date>' line (a human approves the plan)"
    lj = os.path.join(epic_dir, "lanes.json")
    if os.path.exists(lj):
        probs = lane_problems(json.load(open(lj)).get("lanes", []), cfg("plan.max_tickets_per_lane", 5),
                              cfg("plan.max_block_share", 0.5))
        if probs:
            return False, "lanes.json breaks the lane rules: " + "; ".join(probs)
    return True, p


def components():
    return cfg("components", {})


def touched_components(base=None, cwd=None):
    files = git("diff", "--name-only", f"{base or MAIN_REF}...HEAD", cwd=cwd).splitlines()
    return [c for c, v in components().items()
            if any(f.startswith(tuple(v.get("paths", []))) for f in files) and v.get("unit")]


def reaches_live(cmd):
    """A unit command that names a live marker from the profile (live.markers): no gate tests live."""
    return any(m.search(cmd) for m in LIVE_MARKERS)


def v_unit(epic_dir, evidence, head, n=None, cwd=None):
    comps = touched_components(cwd=cwd)
    if not comps:
        return False, "no touched component has a unit command in the delivery profile"
    live = [f"{c}: {components()[c]['unit']}" for c in comps if reaches_live(components()[c]["unit"])]
    if live:
        return False, "unit command(s) would test the live environment (the gates run on the local stack only): " + " | ".join(live)
    log, ok = os.path.join(epic_dir, "unit.log"), True
    os.makedirs(epic_dir, exist_ok=True)
    env = {**os.environ, **{k: str(v) for k, v in cfg("unit.env", {}).items()}}
    with open(log, "w") as f:
        for c in comps:
            r = sh(components()[c]["unit"], env=env, cwd=cwd)
            f.write(f"## {c}: exit {r.returncode}\n{r.stdout[-4000:]}\n{r.stderr[-4000:]}\n")
            ok &= r.returncode == 0
    if not ok:
        return False, f"unit commands failed: see {log}"
    if n and cfg("board.check_children", False):
        ready = cfg("board.ready_statuses", ["In Testing", "Done"])
        open_kids = [k for k in children(n) if k["state"] == "OPEN" and k["status"] not in ready]
        if open_kids:
            return False, f"child tickets not in {ready}: " + ", ".join(f"#{k['number']}" for k in open_kids)
    return True, log


def evidence_bound(text, head, cwd=None):
    """Review and security evidence must name the SHA it covers: the head, or an ancestor with only exempt changes since."""
    shas = HEAD_SHA.findall(text)
    if not shas:
        return False, "evidence names no SHA: add a line 'Reviewed-SHA: <sha>'"
    full = git("rev-parse", "--verify", "-q", shas[-1] + "^{commit}", cwd=cwd)
    if not full:
        return False, f"evidence SHA {shas[-1]} is not a commit in this repo"
    if full == head or fresh({"gate": "x", "sha": full}, head, cwd):
        return True, ""
    return False, f"evidence covers {full[:8]}, but code changed since (head {head[:8]}): run it again on the head"


def issue_open(n):
    """(True, "") only for an open issue (not a pull request) in REPO. Fails closed."""
    r = sh(["gh", "api", f"repos/{REPO}/issues/{n}"])
    if r.returncode:
        return False, f"#{n}: gh api failed: {(r.stdout + r.stderr).strip()[:200]}"
    try:
        d = json.loads(r.stdout)
    except ValueError:
        return False, f"#{n}: gh api gave no JSON"
    if not isinstance(d, dict) or "pull_request" in d:
        return False, f"#{n} is not an issue"
    if d.get("state") != "open":
        return False, f"#{n} is {d.get('state')!r}, not open"
    return True, ""


def approval_commit(sha, plan_rel, cwd=None):
    """The commit, at or before sha, that last wrote an `Approved:` line into plan.md (only a human's yes writes it).
    None when there is none, or when the last change to such a line removed it."""
    c = git("log", "-1", "--format=%H", "-G", "^Approved:", sha, "--", plan_rel, cwd=cwd)
    return c if c and re.search(r"(?m)^\+Approved:", git("show", "--format=", c, "--", plan_rel, cwd=cwd)) else None


def v_waivers(x, epic_dir, cwd=None):
    """A left-out suite passes only as a recorded gap: the run's local_waivers file is the epic's own
    local-waivers.json, committed at the run's commit exactly as it was when the human approved the plan (the
    commit that last wrote plan.md's Approved: line), with an "approved" line; it names the suite with the same
    issue; and the issue is open."""
    rid, sha, waived = x.get("run_id"), x["git_sha"], x.get("waived")
    if not isinstance(waived, dict):
        return False, f"run {rid}: waived is not a {{suite: issue}} map"
    if set(waived) - set(x.get("left_out") or []):
        return False, f"run {rid}: waived suites that were not left out: {', '.join(sorted(set(waived) - set(x.get('left_out') or [])))}"
    top = git("rev-parse", "--show-toplevel", cwd=cwd) or ROOT
    rel = os.path.relpath(os.path.join(os.path.realpath(epic_dir), "local-waivers.json"), os.path.realpath(top))
    if rel.startswith("..") or x.get("local_waivers") != rel:
        return False, f"run {rid} waived {', '.join(sorted(waived))} through {x.get('local_waivers')!r}, not this epic's {rel}"
    r = sh(["git", "show", f"{sha}:{rel}"], cwd=cwd)
    if r.returncode:
        return False, f"{rel} is not committed at the run's commit {sha[:8]}: a waiver is approved and committed before the run"
    prel = os.path.join(os.path.dirname(rel), "plan.md")
    ap = approval_commit(sha, prel, cwd)
    if not ap:
        return False, f"run {rid}: no Approved: line was written into {prel} up to {sha[:8]}: the human approves the waivers with the plan"
    p = sh(["git", "show", f"{ap}:{rel}"], cwd=cwd)
    if p.returncode or p.stdout != r.stdout:
        return False, (f"{rel} at {sha[:8]} is not the one approved at {ap[:8]} (the last change to the Approved: line): "
                       "a changed waiver list needs a new Approved: line")
    try:
        f = json.loads(r.stdout)
    except ValueError:
        return False, f"{rel} at {sha[:8]} is not JSON"
    if not isinstance(f, dict) or not isinstance(f.get("approved"), str) or not f["approved"].strip():
        return False, f'{rel} at {sha[:8]} has no "approved"'
    fs = f.get("suites") if isinstance(f.get("suites"), dict) else {}
    wrong = sorted(n for n, i in waived.items() if type(i) is not int or fs.get(n) != i)
    if wrong:
        return False, f"run {rid}: waived {', '.join(wrong)} not as {rel} at {sha[:8]} has them"
    for n in sorted(set(waived.values())):
        ok, why = issue_open(n)
        if not ok:
            return False, f"waiver ticket {why} (every waived suite needs an open ticket)"
    return True, ""


def v_harness(epic_dir, evidence, head, cwd=None, reruns=()):
    """One full run, plus re-runs that pass every test the full run failed. The summary contract is in
    profile/delivery-profile.md. A re-run is on the full run's SHA or, after a defect fix, on head."""
    if not evidence or not os.path.exists(evidence):
        return False, "missing evidence: pass --evidence <the run's summary.json>"
    s = json.load(open(evidence))
    rs = []
    for r in reruns:
        if not os.path.exists(r):
            return False, f"missing re-run evidence {r}"
        rs.append(json.load(open(r)))
    t = s.get("totals", {})
    if not t.get("tests"):
        return False, "harness not clean: no tests ran"
    if t.get("skipped", 0):
        return False, f"harness not clean: {{'skipped': {t['skipped']}}}"
    if s.get("narrowed") is not False:
        return False, (f"run {s.get('run_id')}: the full run must say narrowed: false (a run picked by --suite or --tier is a "
                       "re-run, never the gate run)")
    must = cfg("harness.summary_must_equal", {"target": "local"})
    for x in [s] + rs:
        rid = x.get("run_id")
        if not x.get("git_sha"):
            return False, "summary has no git_sha: the run is not tied to a commit"
        if x.get("dirty") is not False:
            return False, f"run {rid} does not say dirty: false (a run on a dirty tree proves nothing)"
        if x["git_sha"] != s["git_sha"] and sh(["git", "merge-base", "--is-ancestor", s["git_sha"], x["git_sha"]], cwd=cwd).returncode:
            return False, f"re-run {rid} is on {x['git_sha'][:8]}, not the full run's {s['git_sha'][:8]} or a later commit"
        for k, want in must.items():
            if x.get(k) != want:
                return False, f"run {rid}: {k} is {x.get(k)!r}, the profile needs {want!r} (harness.summary_must_equal)"
        left, waived = x.get("left_out") or [], x.get("waived") or {}
        if not isinstance(left, list) or not isinstance(waived, dict):
            return False, f"run {rid}: left_out must be a list and waived a map"
        if [n for n in left if n not in waived]:
            return False, f"run {rid} left out suites, so they were not tested: " + ", ".join(n for n in left if n not in waived)
        quiet = [u.get("name") for u in x.get("suites", []) if u.get("passed") is False and not u.get("failures", 0) + u.get("errors", 0)]
        if quiet:
            return False, f"run {rid}: suite(s) failed with no test result: {', '.join(quiet)}"
    need = sorted({tag for c in touched_components(cwd=cwd) for tag in components()[c].get("harness_tags") or []})
    if isinstance(s.get("tags"), list) and set(need) - set(s["tags"]):
        return False, f"run {s.get('run_id')} tags {s['tags']} miss the touched components' harness tags: " + ", ".join(sorted(set(need) - set(s["tags"])))
    for x in [s] + rs:
        if x.get("waived"):
            ok, why = v_waivers(x, epic_dir, cwd)
            if not ok:
                return False, why
    on_head = lambda sha: head.startswith(sha[:7]) or fresh({"gate": "x", "sha": sha}, head, cwd)
    moved = not on_head(s["git_sha"])
    bad = t.get("failures", 0) + t.get("errors", 0)
    if bad:
        failed = set(s.get("failed") or [])
        if len(failed) != bad:
            return False, f"harness not clean: {bad} failures/errors, and the summary does not name them all for a re-run"
        for x in rs:
            xt = x.get("totals", {})
            if xt.get("failures", 0) or xt.get("errors", 0) or xt.get("skipped", 0) or not xt.get("tests"):
                return False, f"re-run {x.get('run_id')} is not clean: {xt}"
        left = sorted(failed - {p for x in rs if not moved or on_head(x["git_sha"]) for p in x.get("passed") or []})
        if left:
            return False, "harness not clean: no passing re-run of " + ", ".join(left) + (f" on head {head[:8]}" if moved else "")
    elif moved:
        return False, f"harness ran on {s['git_sha'][:8]}, code has changed since (head {head[:8]})"
    run_sha = git("rev-parse", "--verify", "-q", s["git_sha"] + "^{commit}", cwd=cwd)
    if "main_sha" in s and not s["main_sha"]:
        return False, f"run {s.get('run_id')} could not read {MAIN_REF} when it started (main_sha empty): run it again"
    sh(["git", "fetch", "-q", REMOTE, MAIN], cwd=cwd)
    if s.get("main_sha"):
        if not run_sha or sh(["git", "merge-base", "--is-ancestor", s["main_sha"], run_sha], cwd=cwd).returncode:
            return False, f"harness ran on {s['git_sha'][:8]}, which does not contain main {s['main_sha'][:8]} (main when the run started)"
    elif run_sha and not sh(["git", "rev-parse", "-q", "--verify", MAIN_REF + "^{commit}"], cwd=cwd).returncode \
            and sh(["git", "merge-base", "--is-ancestor", MAIN_REF, run_sha], cwd=cwd).returncode:
        return False, f"harness ran on {s['git_sha'][:8]}, which does not contain {MAIN_REF}: merge it and run on the merged baseline"
    return True, evidence


def v_review(epic_dir, evidence, head, cwd=None):
    if not evidence or not os.path.exists(evidence):
        return False, "missing evidence: pass --evidence <epic dir>/review.md"
    t = open(evidence).read()
    ok, why = evidence_bound(t, head, cwd)
    if not ok:
        return False, why
    if re.search(r"(?m)^GATE:\s*PASS", t) and not re.search(r"(?m)^GATE:\s*FAIL", t):
        return True, evidence
    return False, "review evidence has no 'GATE: PASS' line (or has a FAIL line too)"


def v_security(epic_dir, evidence, head, cwd=None):
    if not evidence or not os.path.exists(evidence):
        return False, "missing evidence: pass --evidence <epic dir>/security.md"
    t = open(evidence).read()
    if not re.search(r"(?m)^SECURITY:\s*PASS", t) or re.search(r"(?m)^SECURITY:\s*FAIL", t):
        return False, "security.md has no 'SECURITY: PASS' line (or has a FAIL line too)"
    ok, why = evidence_bound(t, head, cwd)
    if not ok:
        return False, why
    # stack.drift_check: the local stack holds exactly the head's schema, before and after the commands
    drift = cfg("stack.drift_check")
    cmds = ([drift] if drift else []) + list(cfg("security.commands", [])) + ([drift] if drift else [])
    for cmd in cmds:
        r = sh(cmd, cwd=cwd)
        if r.returncode:
            return False, f"{cmd} failed:\n" + (r.stdout + r.stderr)[-800:]
    return True, evidence


VALIDATE = {"plan": v_plan, "unit": v_unit, "harness": v_harness, "review": v_review, "security": v_security}


# ---------------------------------------------------------------- GitHub I/O

def gh(args):
    r = sh(["gh", *args])
    if r.returncode:
        sys.exit(f"gh {' '.join(args[:3])} failed: {(r.stdout + r.stderr)[:400]}")
    return r.stdout


def parse_records(pages, repo=None):
    """Gate records from issue comments. Only the repo owner, github-actions[bot] and profile gate_authors count."""
    ok = {(repo or REPO).split("/")[0].lower(), *OWNER_ALLOWED, *[a.lower() for a in cfg("gate_authors", [])]}
    return [json.loads(m.group(1)) for page in pages for c in page
            if ((c.get("user") or {}).get("login") or "").lower() in ok
            for m in [MARK.search(c.get("body") or "")] if m]


def records(n):
    return parse_records(json.loads(gh(["api", f"repos/{REPO}/issues/{n}/comments", "--paginate", "--slurp"]) or "[]"))


def children(n):
    field = cfg("board.status_field", "Status")
    q = '''query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){issue(number:$n){subIssues(first:100){nodes{number state
      projectItems(first:5){nodes{fieldValues(first:20){nodes{... on ProjectV2ItemFieldSingleSelectValue{name field{... on ProjectV2FieldCommon{name}}}}}}}}}}}}'''
    o, r = REPO.split("/")
    d = json.loads(gh(["api", "graphql", "-f", f"query={q}", "-f", f"o={o}", "-f", f"r={r}", "-F", f"n={n}"]))
    kids = d["data"]["repository"]["issue"]["subIssues"]["nodes"]
    status = lambda k: next((v["name"] for it in k["projectItems"]["nodes"] for v in it["fieldValues"]["nodes"]
                             if v and v.get("field", {}).get("name") == field), None)
    return [{"number": k["number"], "state": k["state"], "status": status(k)} for k in kids]


def merged(branch):
    out = sh(["gh", "pr", "list", "-R", REPO, "--head", branch, "--state", "merged", "--json", "number"]).stdout
    return bool(out.strip() not in ("", "[]"))


def post_status(sha, gate, state, desc):
    r = sh(["gh", "api", f"repos/{REPO}/statuses/{sha}", "-f", f"state={state}", "-f", f"context=sdlc/{gate}",
            "-f", f"description={desc[:140]}"])
    return r.returncode == 0


def set_fields(n, values):
    """board.set_field in the profile: a command template with {n} {field} {value}. None: the board is not written."""
    tpl = cfg("board.set_field")
    if not tpl:
        return
    for k, v in values.items():
        r = sh(tpl.format(n=n, field=shlex.quote(k), value=shlex.quote(v)))
        if r.returncode:
            print(f"note: board field {k} not set on #{n}: {(r.stdout + r.stderr).strip()[:200]}")


def field_fixes(fields, phase):
    """Board field writes that make the board match the derived phase. A hand-set Blocked stays."""
    fix = {}
    if fields.get("Phase") not in (phase, "Blocked"):
        fix["Phase"] = phase
    if phase in ("Building", "Harness", "Review", "Security", "Ready") and fields.get("Status") not in ("In Progress", "In Testing", "Blocked", "Done"):
        fix["Status"] = "In Progress"
    return fix


# ---------------------------------------------------------------- epic lookup

def epic_branches(n):
    out = git("branch", "-a", "--format=%(refname:short)", "--list", f"{EPIC_PREFIX}{n}-*", f"{REMOTE}/{EPIC_PREFIX}{n}-*")
    return sorted({re.sub(rf"^{re.escape(REMOTE)}/", "", b) for b in out.splitlines() if b})


def epic_branch(n):
    found = epic_branches(n)
    if len(found) != 1:
        sys.exit(f"epic #{n}: expected one branch {EPIC_PREFIX}{n}-*, found {found or 'none'}")
    return found[0]


def epic_dir(n):
    d = glob.glob(os.path.join(ROOT, EPICS_DIR, f"{n}-*"))
    return d[0] if d else os.path.join(ROOT, EPICS_DIR, str(n))


def head_of(branch):
    return git("rev-parse", "-q", "--verify", branch) or git("rev-parse", "-q", "--verify", f"{REMOTE}/{branch}")


def state(n):
    b = epic_branch(n)
    h = head_of(b)
    phase, nxt, st = derive(records(n), h, merged(b))
    return b, h, phase, nxt, st


ICON = {"pass": "✓", "stale": "↻", "fail": "✕", "missing": "·"}


def show(n, b, h, phase, st):
    print(f"epic #{n}  {b} @ {h[:8]}  phase: {phase}")
    for g in GATES:
        print(f"  {ICON[st[g]]} {g:<9} {st[g]}")


# ---------------------------------------------------------------- commands

def cmd_status(a):
    b, h, phase, nxt, st = state(a.n)
    show(a.n, b, h, phase, st)


def cmd_next(a):
    b, h, phase, nxt, st = state(a.n)
    show(a.n, b, h, phase, st)
    print(f"\nnext: {NEXT[nxt].format(n=a.n)}")


def dirty_code(cwd=None):
    """Uncommitted non-exempt paths. Not via git(): its strip() eats the first porcelain line's leading space."""
    return [l[3:] for l in sh(["git", "status", "--porcelain"], cwd=cwd).stdout.splitlines() if not EXEMPT.search(l[3:])]


def cmd_advance(a):
    b = epic_branch(a.n)
    if git("rev-parse", "--abbrev-ref", "HEAD") != b:
        sys.exit(f"check out {b} first")
    dirty = dirty_code()
    if dirty:
        sys.exit("uncommitted code changes: commit first so the gate records a real SHA:\n  " + "\n  ".join(dirty[:10]))
    h = head_of(b)
    recs = records(a.n)
    _, nxt, _ = derive(recs, h)
    ok, why = legal(a.gate, nxt)
    if not ok:
        sys.exit(why)
    d = epic_dir(a.n)
    v = VALIDATE[a.gate]
    if a.gate == "unit":
        ok, detail = v(d, a.evidence, h, a.n)
    elif a.gate == "harness":
        ok, detail = v(d, a.evidence, h, reruns=a.rerun or ())
    else:
        ok, detail = v(d, a.evidence, h)
    rec = {"v": 3, "gate": a.gate, "status": "pass" if ok else "fail", "sha": h,
           "evidence": os.path.relpath(detail, ROOT) if ok and os.path.exists(str(detail)) else None,
           "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    body = (f"**SDLC gate `{a.gate}`: {rec['status'].upper()}** on `{h[:8]}`\n\n"
            + (f"Evidence: `{rec['evidence']}`" if ok else f"```\n{detail}\n```") + f"\n\n<!-- sdlc {json.dumps(rec)} -->")
    gh(["issue", "comment", str(a.n), "-R", REPO, "--body", body])
    posted = post_status(h, a.gate, "success" if ok else "failure", rec["evidence"] or str(detail).splitlines()[0])
    phase, nxt2, st = derive(recs + [rec], h)
    set_fields(a.n, {"Phase": phase})
    show(a.n, b, h, phase, st)
    if not posted:
        print(f"note: commit status not posted ({h[:8]} not on GitHub yet): it will be on the next advance after a push")
    if not ok:
        sys.exit(f"\ngate {a.gate} FAILED: {detail}")
    print(f"\nnext: {NEXT[nxt2].format(n=a.n)}")


def cmd_check_harness(a):
    """The harness gate's checks on a run, with nothing recorded."""
    b = epic_branch(a.n)
    if git("rev-parse", "--abbrev-ref", "HEAD") != b:
        sys.exit(f"check out {b} first")
    ok, detail = v_harness(epic_dir(a.n), a.evidence, head_of(b), reruns=a.rerun or ())
    print(f"harness gate: {'PASS' if ok else 'FAIL'}: {detail}")
    sys.exit(0 if ok else 1)


def gate_tags(n, comps):
    tags = sorted({t for c in comps for t in components()[c].get("harness_tags") or []})
    return tags + [t for t in (f"epic{n}", f"e{n}") if t not in tags]


def cmd_gate_tags(a):
    print(" ".join(f"--tag {t}" for t in gate_tags(a.n, touched_components())))


def cmd_lint_lanes(a):
    probs = lane_problems(json.load(open(a.file)).get("lanes", []), cfg("plan.max_tickets_per_lane", 5), cfg("plan.max_block_share", 0.5))
    print("\n".join(probs) or "lanes ok")
    sys.exit(1 if probs else 0)


def check(n):
    b, h, phase, nxt, st = state(n)
    if nxt is None:
        return True, f"epic #{n}: all gates pass on {h[:8]}"
    bad = ", ".join(f"{g}={st[g]}" for g in GATES if st[g] != "pass")
    return False, f"epic #{n} ({b}) is in phase {phase}, not Ready. Gates not passing on {h[:8]}: {bad}. Run: sdlc.py next {n}"


def cmd_check(a):
    n = a.n or (re.match(rf"{re.escape(EPIC_PREFIX)}(\d+)-", a.branch or "") or sys.exit(f"not an epic branch: {a.branch}")).group(1)
    ok, msg = check(n)
    print(msg)
    sys.exit(0 if ok else 1)


# ---------------------------------------------------------------- guard (PreToolUse hook)

def pr_head(arg="", repo=None):
    """Head branch of a PR. Fails closed: a failed or empty lookup stops the guard (SystemExit)."""
    r = sh(["gh", "pr", "view", *([arg] if arg else []), "-R", repo or REPO, "--json", "headRefName", "-q", ".headRefName"])
    head = r.stdout.strip()
    if r.returncode or not head:
        sys.exit(f"gh pr view {arg or '(current branch)'} failed: {r.stderr.strip()[:200] or 'empty answer'}")
    return head


def current_branch():
    return git("rev-parse", "--abbrev-ref", "HEAD")


def unpushed_epic_merges(src=None):
    """Merge subjects on the ref being pushed that the remote main does not have yet."""
    return git("log", "--merges", "--format=%s", f"{MAIN_REF}..{src or MAIN}")


GIT_CMD = r"\bgit(?:\s+(?:-[Cc]\s+\S+|--(?:git-dir|work-tree|namespace|exec-path|super-prefix|config-env)\s+\S+|--[\w-]+=\S+|--[\w-]+|-p))*\s+"
GH_MERGE_VALUE_FLAGS = {"-R", "--repo", "-b", "--body", "-t", "--subject", "-F", "--body-file", "-A", "--author-email", "--match-head-commit"}
PUSH_VALUE_FLAGS = {"--repo", "-o", "--push-option", "--receive-pack", "--exec"}


def epics_in(cmd, pr_head=pr_head, cur=current_branch, unpushed=unpushed_epic_merges):
    """Epic numbers a shell command would merge into / push to main. Raises SystemExit when it cannot tell (fail closed)."""
    ep = re.escape(EPIC_PREFIX)
    epic_ref = re.compile(rf"^{ep}(\d+)-")
    eps = set()
    cmd = re.sub(r"\s+", " ", cmd)
    for m in re.finditer(r"\bgh pr merge\b([^;&|]*)", cmd):
        try:  # quoted flag values with spaces stay one token
            toks = shlex.split(m.group(1))
        except ValueError:  # unbalanced quote: the words sit inside a quoted string (a commit message, say)
            toks = m.group(1).split()
        arg, repo, skip = "", None, False
        for i, w in enumerate(toks):
            if skip:
                skip = False
            elif w.startswith("-"):
                if w in GH_MERGE_VALUE_FLAGS and i + 1 < len(toks):
                    skip = True
                    if w in ("-R", "--repo"):
                        repo = toks[i + 1]
                elif w.startswith("--repo="):
                    repo = w.split("=", 1)[1]
            elif not arg:
                arg = w.strip("'\"")
        if not arg or arg.lstrip("#").isdigit() or "/pull/" in arg:  # current branch, number or URL: ask GitHub
            eps |= set(epic_ref.findall(pr_head(arg, repo)))
        else:
            eps |= set(re.findall(rf"(?:^|:){ep}(\d+)-", arg))
    for m in re.finditer(r"\bgh api\b([^;&|]*)", cmd):
        if re.search(r"\bgraphql\b", m.group(1)) and re.search(r"\bmerge(PullRequest|Branch)\b", m.group(1)):
            sys.exit("gh api graphql merge mutation: cannot tell which branch it merges")
        merges = re.search(r"\brepos/[^/\s]+/[^/\s]+/merges\b", m.group(1))
        pr = re.search(r"\brepos/[^/\s]+/[^/\s]+/pulls/(\d+)/merge\b", m.group(1))
        if pr and not re.search(r"(?:-X|--method)[\s=]*PUT\b", m.group(1), re.I):
            pr = None  # GET .../pulls/N/merge only asks whether it is merged
        if not merges and not pr:
            continue
        found = set(re.findall(rf"{ep}(\d+)-", m.group(1))) | (set(epic_ref.findall(pr_head(pr.group(1)))) if pr else set())
        if not pr and not found:
            sys.exit("gh api merge call: cannot tell which branch it merges")
        eps |= found
    for m in re.finditer(GIT_CMD + r"(merge|pull)\b([^;&|]*)", cmd):
        eps |= set(re.findall(rf"{ep}(\d+)-", m.group(2)))
    for m in re.finditer(GIT_CMD + r"push\b([^;&|]*)", cmd):
        toks, pos, skip = m.group(1).split(), [], False
        for w in toks:
            if skip:
                skip = False
            elif w.startswith("-"):
                skip = w in PUSH_VALUE_FLAGS
            else:
                pos.append(w.strip("'\""))
        here, srcs = cur(), []
        specs = pos[1:] + [w for w in pos[:1] if ":" in w]
        to_main = any(w in ("--all", "--mirror") for w in toks) or (not specs and here == MAIN)
        for w in specs:
            src, _, dst = w.lstrip("+").partition(":")
            dst = (dst or src).removeprefix("refs/heads/")
            src = src.removeprefix("refs/heads/")
            dst, src = (here if dst == "HEAD" else dst), (here if src == "HEAD" else src)
            if dst == MAIN:
                to_main = True
                srcs.append(src)
                eps |= set(epic_ref.findall(src))
        if to_main:  # also catches an epic merge commit that is not on the remote main yet
            for src in sorted(set(srcs) | {MAIN}):
                eps |= set(re.findall(rf"{ep}(\d+)-", unpushed(src)))
    return sorted(eps)


def cmd_guard(a):
    global DEADLINE
    DEADLINE = time.monotonic() + GUARD_BUDGET
    os.environ.update(GH_PROMPT_DISABLED="1", GIT_TERMINAL_PROMPT="0")
    try:
        cmd = json.load(sys.stdin).get("tool_input", {}).get("command", "")
    except Exception:
        return

    def safe_check(n):  # fail closed: any lookup error blocks
        try:
            return check(n)
        except SystemExit as e:
            return False, f"epic #{n}: cannot verify gates ({e})"
    try:
        eps = epics_in(cmd)
    except SystemExit as e:
        eps, fails = [], [f"cannot resolve the epic for this command ({e})"]
    else:
        fails = []
    fails += [msg for n in eps for ok, msg in [safe_check(n)] if not ok]
    if fails:
        print("SDLC gate blocked this command:\n" + "\n".join(fails), file=sys.stderr)
        sys.exit(2)


# ---------------------------------------------------------------- render (delivery board page)

COLUMNS = ["Refined", "Planned", "Building", "Review", "Security", "Harness", "Ready", "Released"]
NEEDS = {"Refined": "needs: approved plan", "Planned": "next: build", "Building": "gate: unit", "Review": "gate: review",
         "Security": "gate: security", "Harness": "gate: harness", "Ready": "merge + release", "Released": "last 30 days"}
EPICS_Q = '''query($q:String!,$after:String){search(type:ISSUE,query:$q,first:50,after:$after){pageInfo{hasNextPage endCursor}
 nodes{... on Issue{number title url state closedAt subIssuesSummary{total completed}
  projectItems(first:5){nodes{fieldValues(first:20){nodes{... on ProjectV2ItemFieldSingleSelectValue{name field{... on ProjectV2FieldCommon{name}}}}}}}}}}}'''


def epics():
    since = datetime.fromordinal(datetime.now(timezone.utc).toordinal() - 30).date().isoformat()
    out = []
    for q in (f"repo:{REPO} is:issue label:epic is:open", f"repo:{REPO} is:issue label:epic is:closed closed:>={since}"):
        after = None
        while True:
            args = ["api", "graphql", "-f", f"query={EPICS_Q}", "-f", f"q={q}"] + (["-f", f"after={after}"] if after else [])
            d = json.loads(gh(args))["data"]["search"]
            out += d["nodes"]
            if not d["pageInfo"]["hasNextPage"]:
                break
            after = d["pageInfo"]["endCursor"]
    for e in out:
        e["fields"] = {v["field"]["name"]: v["name"] for it in e["projectItems"]["nodes"] for v in it["fieldValues"]["nodes"] if v and v.get("field")}
    return out


def card_model(e):
    n = e["number"]
    branches = epic_branches(n)
    m = {"n": n, "title": e["title"], "url": e["url"], "kids": e["subIssuesSummary"], "states": {g: "missing" for g in GATES},
         "latest": {}, "branch": None, "since": None, "nxt": "plan"}
    if len(branches) == 1:
        b = branches[0]
        h = head_of(b)
        recs = records(n)
        phase, nxt, st = derive(recs, h, e["state"] == "CLOSED" or merged(b))
        m.update(branch=b, states=st, latest=gate_states(recs, h)[1], nxt=nxt, since=recs[-1]["at"][:10] if recs else None)
    else:
        phase = "Released" if e["state"] == "CLOSED" else "Refined"
    if e["state"] == "CLOSED":
        phase, m["nxt"] = "Released", None
    m["blocked"] = e["fields"].get("Phase") == "Blocked"
    m["phase"] = phase
    m["fields"] = e["fields"]
    return m


def render_card(m):
    import html as H
    k = m["kids"]
    pct = round(100 * k["completed"] / k["total"]) if k["total"] else 0
    gates = []
    for g in GATES:
        st, r = m["states"][g], m["latest"].get(g)
        ev = r.get("evidence") if r else None
        href = f"https://github.com/{REPO}/blob/{r['sha']}/{ev}" if ev else (m["url"] if r else None)
        label = f'{ICON[st]} <b>{g}</b>'
        tip = f'{g}: {st}' + (f' on {r["sha"][:8]}' if r else "")
        gates.append(f'<a class="g {st}" href="{href}" title="{tip}">{label}</a>' if href else f'<span class="g {st}" title="{tip}">{label}</span>')
    meta = [f'<a href="{m["url"]}">#{m["n"]}</a>']
    if m["since"]:
        meta.append(f'<span>since {m["since"]}</span>')
    if m["blocked"]:
        meta.append('<span class="chip" style="color:var(--bad)">Blocked</span>')
    nxt = NEXT.get(m.get("nxt"), "").format(n=m["n"]) if m["phase"] != "Released" else ""
    return (f'<article class="card{" blocked" if m["blocked"] else ""}"><h3>{H.escape(m["title"])}</h3>'
            f'<div class="meta">{"".join(meta)}</div>'
            + (f'<div class="prog"><span class="bar" role="img" aria-label="{k["completed"]} of {k["total"]} tickets done"><i style="width:{pct}%"></i></span>'
               f'<span class="num">{k["completed"]}/{k["total"]}</span></div>' if k["total"] else "")
            + f'<div class="gates">{"".join(gates)}</div>'
            + (f'<div class="cmd">next: {H.escape(nxt)}</div>' if nxt else "") + "</article>")


def cmd_render(a):
    import html as H
    models = [card_model(e) for e in epics()]
    for m in models:
        fix = field_fixes(m["fields"], m["phase"])
        if fix:
            set_fields(m["n"], fix)
    cols, strip = [], []
    for c in COLUMNS:
        ms = sorted((m for m in models if m["phase"] == c), key=lambda m: m["n"])
        strip.append(f'<a class="{"" if ms else "zero"}" href="#p-{c.lower()}"><span class="n">{len(ms)}</span><span class="l">{c}</span></a>')
        body = "".join(render_card(m) for m in ms) or '<p class="empty">None</p>'
        live = " live" if ms and c not in ("Refined", "Released") else ""
        cols.append(f'<div class="col{live}" id="p-{c.lower()}"><div class="colhead"><div><h2>{c}</h2><small>{NEEDS[c]}</small></div>'
                    f'<span class="num">{len(ms)}</span></div>{body}</div>')
    name = PROFILE.get("name") or REPO
    links = "".join(f'<a href="{H.escape(u)}">{H.escape(t)} ↗</a>' for t, u in (cfg("board.links", {}) or {}).items())
    tpl = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "page.html")).read()
    page = (tpl.replace("{{DATE}}", datetime.now(timezone.utc).date().isoformat())
               .replace("{{NAME_UPPER}}", H.escape(name.upper())).replace("{{NAME}}", H.escape(name))
               .replace("{{PROJECT}}", cfg("board.project_url", f"https://github.com/{REPO}"))
               .replace("{{EXTRA_LINKS}}", links)
               .replace("{{STRIP}}", "".join(strip)).replace("{{COLUMNS}}", "".join(cols)))
    out = a.out or os.path.join(ROOT, ".claude", "sdlc-board.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w").write(page)
    print(out)


# ---------------------------------------------------------------- selftest

def cmd_selftest(a):
    global PROFILE, sh, issue_open, touched_components, DEADLINE
    fails = []

    def t(name, cond):
        print(("ok   " if cond else "FAIL ") + name)
        cond or fails.append(name)

    real_profile = PROFILE
    PROFILE = {"repo": "acme/app", "paths": {"status_only": {"files": ["tests/scenarios.yaml"]}},
               "live": {"markers": ["--allow-prod", "ALLOW_PROD=1"]},
               "components": {"API": {"paths": ["src/"], "unit": "true", "harness_tags": ["api"]}}}
    configure()
    with tempfile.TemporaryDirectory() as d:
        g = lambda *x: git(*x, cwd=d)
        g("init", "-q", "-b", "main")
        g("config", "user.email", "t@t")
        g("config", "user.name", "t")
        g("config", "commit.gpgsign", "false")
        open(os.path.join(d, ".git", "info", "exclude"), "w").write("summary.json\nreview.md\nsecurity.md\n")

        def commit(path, msg):
            os.makedirs(os.path.dirname(os.path.join(d, path)) or d, exist_ok=True)
            open(os.path.join(d, path), "a").write(msg + "\n")
            g("add", "-A")
            g("commit", "-qm", msg)
            return g("rev-parse", "HEAD")
        s0 = commit(".claude/epics/9-x/plan.md", "plan")
        s1 = commit("src/a.py", "code")
        rec = lambda gate, sha, status="pass": {"gate": gate, "sha": sha, "status": status}
        R = [rec("plan", s0), rec("unit", s1)]

        ph, nxt, st = derive([rec("plan", s0)], s0, cwd=d)
        t("plan only, no code yet -> Planned", ph == "Planned" and nxt == "unit")
        ph, nxt, st = derive(R, s1, cwd=d)
        t("plan+unit -> Review", ph == "Review" and nxt == "review")
        t("illegal transition unit->security refused", not legal("security", nxt)[0])
        t("legal transition to next gate allowed", legal("review", nxt)[0])
        t("harness before security refused", not legal("harness", "security")[0])
        t("re-running an earlier gate allowed", legal("unit", nxt)[0])
        t("unknown gate refused", not legal("qa", nxt)[0])

        s2 = commit("docs/notes.md", "docs only")
        t("docs-only commit keeps gate fresh", derive(R, s2, cwd=d)[2]["unit"] == "pass")
        t("handoff commit keeps gate fresh", derive([rec("plan", s0), rec("unit", s2)], commit(".claude/handoffs/x.md", "h"), cwd=d)[2]["unit"] == "pass")
        t("a .md file outside the exempt dirs stales the gate", derive([rec("plan", s0), rec("unit", s2)], commit("src/prompt.md", "p"), cwd=d)[2]["unit"] == "stale")
        sc = "tests/scenarios.yaml"
        s2a = commit(sc, "scenarios:\n  - id: x\n    status: missing")
        R = [rec("plan", s0), rec("unit", s2a)]
        open(os.path.join(d, sc), "w").write("scenarios:\n  - id: x\n    status: green\n")
        g("commit", "-qam", "flip")
        t("status-only scenario flip keeps gate fresh", derive(R, g("rev-parse", "HEAD"), cwd=d)[2]["unit"] == "pass")
        open(os.path.join(d, sc), "w").write("scenarios:\n  - id: x\n    status: green\n    tier: 2\n")
        g("commit", "-qam", "edit")
        t("other scenario edit makes gate stale", derive(R, g("rev-parse", "HEAD"), cwd=d)[2]["unit"] == "stale")

        eb, base = g("rev-parse", "--abbrev-ref", "HEAD"), g("rev-parse", "HEAD")
        RV = [rec("plan", s0), rec("unit", base), rec("review", base)]
        g("checkout", "-qb", "mainline", s0)
        commit("src/m.py", "main work")
        m1 = g("rev-parse", "HEAD")
        g("update-ref", "refs/remotes/origin/main", m1)
        g("checkout", "-q", eb)
        g("merge", "-q", "--no-edit", "origin/main")
        mh = g("rev-parse", "HEAD")
        ph, nxt, st = derive(RV, mh, cwd=d)
        t("main merge keeps review fresh", st["review"] == "pass")
        t("main merge makes unit stale, phase stays Security", st["unit"] == "stale" and nxt == "unit" and ph == "Security")

        def case(name, steps):
            g("checkout", "-qb", name, mh)
            steps()
            h = g("rev-parse", "HEAD")
            g("checkout", "-q", eb)
            return h
        t("epic edit to a main file after the merge stales review",
          derive(RV, case("t-edit", lambda: commit("src/m.py", "epic edit")), cwd=d)[2]["review"] == "stale")

        def lane():
            g("checkout", "-qb", "lane/9-A")
            commit("src/l.py", "lane")
            g("checkout", "-q", "t-lane")
            g("merge", "-q", "--no-ff", "--no-edit", "lane/9-A")
        t("a lane merge after the review stales review", derive(RV, case("t-lane", lane), cwd=d)[2]["review"] == "stale")
        t("a move into an exempt dir stales unit", derive([rec("plan", s0), rec("unit", mh)],
          case("t-mv", lambda: (g("mv", "src/a.py", "docs/a.py"), g("commit", "-qm", "mv"))), cwd=d)[2]["unit"] == "stale")

        sj0 = os.path.join(d, "s0.json")
        RUN = {"target": "local", "dirty": False, "narrowed": False, "left_out": []}
        json.dump({"totals": {"tests": 1}, "git_sha": base, **RUN}, open(sj0, "w"))
        t("harness run without the main branch merged refused", not v_harness(os.path.join(d, "none"), sj0, base, cwd=d)[0])
        json.dump({"totals": {"tests": 1}, "git_sha": mh, **RUN}, open(sj0, "w"))
        t("harness run on the merged baseline accepted", v_harness(os.path.join(d, "none"), sj0, mh, cwd=d)[0])
        g("update-ref", "-d", "refs/remotes/origin/main")
        t("no main ref: main merge makes review stale", derive(RV, mh, cwd=d)[2]["review"] == "stale")
        s3 = commit("src/b.py", "more code")
        ph, nxt, st = derive(R, s3, cwd=d)
        t("code commit makes gate stale -> back to Building", st["unit"] == "stale" and ph == "Building")
        t("plan never goes stale", st["plan"] == "pass")
        t("failed record -> fail", derive(R + [rec("unit", s3, "fail")], s3, cwd=d)[2]["unit"] == "fail")
        all5 = [rec(x, s3) for x in GATES]
        t("all gates fresh -> Ready", derive(all5, s3, cwd=d)[:2] == ("Ready", None))
        t("all gates fresh + merged -> Released", derive(all5, s3, merged=True, cwd=d)[0] == "Released")
        t("stale Phase field is corrected", field_fixes({"Phase": "Planned", "Status": "Backlog"}, "Building") == {"Phase": "Building", "Status": "In Progress"})
        t("hand-set Blocked and Done are kept", field_fixes({"Phase": "Blocked", "Status": "Done"}, "Building") == {})

        ed = os.path.join(d, ".claude/epics/9-x")
        sj = os.path.join(d, "summary.json")

        def run(path, sha=s3, **kw):
            json.dump({"git_sha": sha, **RUN, **kw}, open(path, "w"))
            return path
        T = lambda f=0, e=0, sk=0: {"tests": 5, "failures": f, "errors": e, "skipped": sk}
        t("missing harness evidence refused", not v_harness(ed, None, s3)[0])
        t("harness failures:1 fails gate", not v_harness(ed, run(sj, totals=T(f=1)), s3, cwd=d)[0])
        t("harness skipped:1 fails gate", not v_harness(ed, run(sj, totals=T(sk=1)), s3, cwd=d)[0])
        t("clean harness summary on head passes", v_harness(ed, run(sj, totals=T()), s3, cwd=d)[0])
        t("summary without git_sha refused", not v_harness(ed, run(sj, sha=None, totals=T()), s3, cwd=d)[0])
        t("summary with dirty:true refused", not v_harness(ed, run(sj, totals=T(), dirty=True), s3, cwd=d)[0])
        t("summary without a dirty field refused (fails closed)",
          not v_harness(ed, (json.dump({"git_sha": s3, "target": "local", "narrowed": False, "totals": T()}, open(sj, "w")), sj)[1], s3, cwd=d)[0])
        t("summary on an older code SHA refused", not v_harness(ed, run(sj, sha=s1, totals=T()), s3, cwd=d)[0])
        t("harness run on live refused", not v_harness(ed, run(sj, totals=T(), target="live"), s3, cwd=d)[0])
        t("narrowed full run refused", not v_harness(ed, run(sj, totals=T(), narrowed=True), s3, cwd=d)[0])
        t("run that left a suite out refused", not v_harness(ed, run(sj, totals=T(), left_out=["e2e"]), s3, cwd=d)[0])
        t("suite that failed with no testcase refused", not v_harness(ed, run(sj, totals=T(), suites=[{"name": "c", "passed": False}]), s3, cwd=d)[0])
        full = run(sj, totals=T(f=1), failed=["b::x"], passed=["b::y"])
        rr = run(os.path.join(d, "rr.json"), totals=T(), passed=["b::x", "b::y"], narrowed=True)
        t("full run + clean re-run of its failure passes", v_harness(ed, full, s3, cwd=d, reruns=[rr])[0])
        t("re-run without the failed test refused", not v_harness(ed, full, s3, cwd=d, reruns=[run(os.path.join(d, "r2.json"), totals=T(), passed=["b::y"])])[0])
        t("re-run that itself fails refused", not v_harness(ed, full, s3, cwd=d, reruns=[run(os.path.join(d, "r4.json"), totals=T(f=1), passed=["b::x"])])[0])
        t("live re-run cannot excuse a failure", not v_harness(ed, full, s3, cwd=d, reruns=[run(os.path.join(d, "r6.json"), totals=T(), passed=["b::x"], target="live")])[0])
        t("failures the summary does not name cannot be re-run", not v_harness(ed, run(sj, totals=T(f=2), failed=["b::x"]), s3, cwd=d, reruns=[rr])[0])
        t("run containing the main it started from passes", v_harness(ed, run(sj, totals=T(), main_sha=m1), s3, cwd=d)[0])
        g("checkout", "-q", "mainline")
        m2 = commit("src/m.py", "main again")
        g("checkout", "-q", eb)
        t("run missing the main it started from refused", not v_harness(ed, run(sj, totals=T(), main_sha=m2), s3, cwd=d)[0])
        t("empty main_sha refused", not v_harness(ed, run(sj, totals=T(), main_sha=""), s3, cwd=d)[0])
        t("unknown main_sha refused (fails closed)", not v_harness(ed, run(sj, totals=T(), main_sha="0" * 40), s3, cwd=d)[0])
        PROFILE["harness"] = {"summary_must_equal": {"target": "local", "extra_migrations": []}}
        t("profile summary_must_equal: a missing field refused", not v_harness(ed, run(sj, totals=T()), s3, cwd=d)[0])
        t("profile summary_must_equal: the field present passes", v_harness(ed, run(sj, totals=T(), extra_migrations=[]), s3, cwd=d)[0])
        del PROFILE["harness"]

        # waivers: approved with the plan, committed, ticketed
        real_io = issue_open
        states = {601: "open", 602: "closed"}
        issue_open = lambda n: (states.get(n) == "open", f"#{n} is {states.get(n)!r}")
        g("checkout", "-qb", "t-waive", s3)

        def put(path, body):
            os.makedirs(os.path.dirname(os.path.join(d, path)), exist_ok=True)
            open(os.path.join(d, path), "w").write(body if isinstance(body, str) else json.dumps(body))
            g("add", path)
            g("commit", "-qm", path)
            return g("rev-parse", "HEAD")
        wrel, prel = ".claude/epics/9-x/local-waivers.json", ".claude/epics/9-x/plan.md"
        PLAN, OK_LINE = "## Test plan\n- x\n", "Approved: Human 2026-01-01 (chat)\n"
        put(prel, PLAN)
        sw = put(wrel, {"approved": "Human 2026-01-01", "suites": {"legacy": 601, "old": 602}})
        sa = put(prel, PLAN + OK_LINE)
        W = {"local_waivers": wrel, "left_out": ["legacy"], "waived": {"legacy": 601}}
        wv = lambda sha=sa, **kw: v_harness(ed, run(os.path.join(d, "w.json"), sha=sha, **{"totals": T(), **W, **kw}), sha, cwd=d)
        no = lambda why, **kw: (lambda r: not r[0] and why in r[1])(wv(**kw))
        t("approved, committed, ticketed waiver passes", wv()[0])
        t("waiver through another file refused", no("not this epic's", local_waivers="tmp/w.json"))
        t("waived issue that differs from the file refused", no("not as", waived={"legacy": 603}))
        t("waived suite that was not left out refused", no("not left out", left_out=[], waived={"legacy": 601}))
        t("waivers with no Approved line refused", no("Approved", sha=sw))
        sw2 = put(wrel, {"approved": "x", "suites": {"legacy": 601, "new": 601}})
        t("a waiver list changed after approval refused", no("not the one approved", sha=sw2))
        sa2 = put(prel, PLAN + "Approved: Human 2026-01-02 (chat, new waivers)\n")
        t("a new Approved line approves the changed list", wv(sha=sa2)[0])
        put(wrel, {"approved": "x", "suites": {"old": 602}})
        sc2 = put(prel, PLAN + "Approved: Human 2026-01-03\n")
        t("waiver with a closed ticket refused", no("#602", sha=sc2, left_out=["old"], waived={"old": 602}))
        issue_open = real_io

        g("checkout", "-q", eb)
        stub_sh = sh
        sh = lambda cmd, cwd=None, check=False, env=None: subprocess.CompletedProcess(cmd, 1, "", "no network")
        t("waiver ticket lookup fails closed when gh fails", not issue_open(601)[0])
        for body, want in ((json.dumps({"state": "open"}), True), (json.dumps({"state": "closed"}), False),
                           (json.dumps({"state": "open", "pull_request": {}}), False), ("<html>", False)):
            sh = lambda cmd, cwd=None, check=False, env=None, b=body: subprocess.CompletedProcess(cmd, 0, b, "")
            t(f"ticket lookup {'accepts' if want else 'refuses'} {body[:30]}", issue_open(601)[0] is want)
        sh = stub_sh

        # unit gate: live markers refused before any command runs
        real_tc = touched_components
        touched_components = lambda base=None, cwd=None: ["API"]
        ran = []
        sh = lambda cmd, cwd=None, check=False, env=None: real_sh(cmd, cwd, check, env) if isinstance(cmd, list) else (
            ran.append((cmd, env)) or subprocess.CompletedProcess(cmd, 0, "", ""))
        for unit, live in (("npm test -- --allow-prod", True), ("ALLOW_PROD=1 pytest", True), ("npm test", False)):
            PROFILE["components"]["API"]["unit"] = unit
            PROFILE["unit"] = {"env": {"STRICT": "1"}}
            ran.clear()
            ok, why = v_unit(ed, None, s3, cwd=d)
            t(f"unit command {'refused before it runs' if live else 'runs'}: {unit}",
              (not ok and "live" in why and not ran) if live else (ok and [c for c, _ in ran] == [unit]))
        t("unit gate passes the profile's unit.env", ran and ran[0][1].get("STRICT") == "1")
        sh = real_sh
        t("run whose tags miss a touched component's harness tags refused",
          "harness tags" in v_harness(ed, run(sj, totals=T(), tags=["epic9"]), s3, cwd=d)[1])
        t("run with the touched components' tags passes", v_harness(ed, run(sj, totals=T(), tags=["api", "epic9"]), s3, cwd=d)[0])
        t("gate-tags: component tags, then epic<N> and e<N>", gate_tags(9, ["API"]) == ["api", "epic9", "e9"])
        sh = lambda cmd, cwd=None, check=False, env=None: real_sh(cmd, cwd, check, env) if isinstance(cmd, list) else (
            ran.append((cmd, env)) or subprocess.CompletedProcess(cmd, 0, "", ""))
        touched_components = real_tc

        # security gate: drift check before and after the commands
        PROFILE["stack"] = {"drift_check": "drift"}
        PROFILE["security"] = {"commands": ["scan"]}
        sv = os.path.join(d, "security.md")
        open(sv, "w").write(f"Reviewed-SHA: {s3}\nSECURITY: PASS\n")
        ran.clear()
        t("security gate runs drift, commands, drift", v_security(ed, sv, s3, cwd=d)[0] and [c for c, _ in ran] == ["drift", "scan", "drift"])
        open(sv, "w").write("SECURITY: PASS\n")
        t("security evidence with no SHA refused", not v_security(ed, sv, s3, cwd=d)[0])
        open(sv, "w").write(f"Reviewed-SHA: {s3}\nSECURITY: PASS\nSECURITY: FAIL\n")
        t("security evidence with PASS and FAIL refused", not v_security(ed, sv, s3, cwd=d)[0])
        sh = real_sh

        t("missing plan.md refused", not v_plan(os.path.join(d, "nope"), None, s3)[0])
        os.makedirs(ed, exist_ok=True)
        open(os.path.join(ed, "plan.md"), "w").write("## Test plan\n- x\n")
        t("unapproved plan refused", not v_plan(ed, None, s3)[0])
        open(os.path.join(ed, "plan.md"), "a").write("Approved: Human 2026-01-01\n")
        t("approved plan accepted", v_plan(ed, None, s3)[0])
        big = [{"id": "A", "tickets": [1, 2, 3, 4, 5, 6], "deps": []}, {"id": "B", "tickets": [7], "deps": ["A"]}]
        json.dump({"lanes": big}, open(os.path.join(ed, "lanes.json"), "w"))
        t("plan with a lane of 6 tickets refused", "6 tickets" in v_plan(ed, None, s3)[1])
        os.remove(os.path.join(ed, "lanes.json"))
        rv = os.path.join(d, "review.md")
        open(rv, "w").write("findings...\nGATE: FAIL\n")
        t("review GATE: FAIL refused", not v_review(ed, rv, s3)[0])
        open(rv, "w").write(f"Reviewed-SHA: {s3}\nGATE: PASS\n")
        t("review GATE: PASS accepted", v_review(ed, rv, s3, cwd=d)[0])
        open(rv, "w").write("GATE: PASS\n")
        t("review evidence with no SHA refused", not v_review(ed, rv, s3, cwd=d)[0])
        open(rv, "w").write(f"Head `epic/9-x` @ {s1[:8]}\nGATE: PASS\n")
        t("review evidence naming an older code SHA refused", not v_review(ed, rv, s3, cwd=d)[0])

    # lane rules
    chain = [{"id": "A", "tickets": [1], "deps": []}, {"id": "B", "tickets": [2], "deps": ["A"]},
             {"id": "C", "tickets": [3], "deps": ["B"]}, {"id": "D", "tickets": [4], "deps": []}]
    t("a lane that blocks most others (directly or through others) is a problem", any("lane A blocks 2" in p for p in lane_problems(chain)))
    t("a balanced lane graph is fine", lane_problems([{"id": x, "tickets": [1], "deps": []} for x in "ABCD"]) == [])
    t("two lanes: one may block the other", lane_problems(chain[:2]) == [])

    pages = [[{"user": {"login": "acme"}, "body": '<!-- sdlc {"gate": "unit"} -->'},
              {"user": {"login": "mallory"}, "body": '<!-- sdlc {"gate": "security"} -->'},
              {"user": {"login": "github-actions[bot]"}, "body": '<!-- sdlc {"gate": "plan"} -->'},
              {"user": None, "body": '<!-- sdlc {"gate": "review"} -->'}]]
    t("gate comment by another author ignored", [r["gate"] for r in parse_records(pages, "acme/app")] == ["unit", "plan"])

    t("guard ignores ordinary commands", epics_in("git status && ls") == [])
    t("guard sees git merge of epic branch", epics_in("git merge --no-ff epic/42-crew-sync") == ["42"])
    t("guard sees push of epic branch to main", epics_in("git push origin epic/7-x:main", cur=lambda: "main", unpushed=lambda src=None: "") == ["7"])

    def fake_pr(heads):
        def f(arg="", repo=None):
            if arg not in heads:
                sys.exit(f"gh pr view {arg} failed")
            return heads[arg]
        return f

    def gi(cmd, cur="main", merges=""):
        try:
            return epics_in(cmd, pr_head=fake_pr({"123": "epic/5-x", "": "epic/5-x", "9": "feature/y"}), cur=lambda: cur, unpushed=lambda src=None: merges)
        except SystemExit:
            return "FAILCLOSED"
    M = "mer" + "ge"
    for name, c, cur in [("refs/heads/main", "git push origin epic/5-x:refs/heads/main", "main"),
                         ("HEAD:refs/heads/main from epic", "git push origin HEAD:refs/heads/main", "epic/5-x"),
                         ("git -C", f"git -C . {M} epic/5-x", "main"), ("git -c k=v", f"git -c a=b {M} --no-ff epic/5-x", "main"),
                         ("git pull", "git pull origin epic/5-x", "main"), ("extra spaces", f"git   {M}   epic/5-x", "main"),
                         ("--merge 123", f"gh pr {M} --merge 123", "main"),
                         ("-R value then number", f"gh pr {M} -R acme/app 123", "main"),
                         ("gh api pulls merge", f"gh api -X PUT repos/acme/app/pulls/123/{M}", "main"),
                         ("gh api merges", f"gh api repos/acme/app/{M}s -f head=epic/5-x", "main"),
                         ("pr merge no number on epic branch", f"gh pr {M} --merge", "epic/5-x"),
                         ("quoted multi-word flag", f'gh pr {M} -t "two words" 123', "main"),
                         ("--work-tree", f"git --work-tree /tmp/w {M} epic/5-x", "main")]:
        t(f"guard blocks bypass form: {name}", gi(c, cur) == ["5"])
    t("guard fails closed when gh pr view fails", gi(f"gh pr {M} 777") == "FAILCLOSED")
    t("guard fails closed on gh api merge of unknown branch", gi(f"gh api repos/a/b/{M}s -f head=lane") == "FAILCLOSED")
    t("guard fails closed on a GraphQL merge mutation", gi(f"gh api graphql -f query='mutation {{ {M}PullRequest(input: {{}}) {{ x }} }}'") == "FAILCLOSED")
    t("guard: gh pr merge of non-epic PR passes", gi(f"gh pr {M} --squash 9") == [])
    um = "Merge branch 'epic/8-z'"
    t("guard: bare push of main with unpushed epic merge blocked", gi("git push", "main", um) == ["8"])
    t("guard: push --all with unpushed epic merge blocked", gi("git push --all origin", "main", um) == ["8"])
    t("guard: epic branch pushed to its own name allowed", gi("git push -u origin epic/5-x", "epic/5-x", um) == [])
    for c in ["git status", f"git {M} main", "git push origin feature/a", "git log --merges", "gh pr view 123",
              "git push origin HEAD", f'git commit -m "fix: gh pr {M} now fails closed"', f"gh api repos/acme/app/pulls/123/{M}"]:
        t(f"guard allows harmless command: {c[:40]!r}", gi(c, "epic/5-x") == [])

    DEADLINE, t0 = time.monotonic() + 0.5, time.monotonic()
    r = sh(["sh", "-c", "sleep 5"])
    t("guard deadline stops a hung call with a failure", r.returncode == 124 and time.monotonic() - t0 < 3)
    DEADLINE, t0 = time.monotonic() + 5, time.monotonic()
    t("child process never waits on stdin", sh(["cat"]).returncode == 0 and time.monotonic() - t0 < 3)
    DEADLINE = None
    PROFILE = real_profile
    configure()
    print(f"\n{'FAILED: ' + ', '.join(fails) if fails else 'all selftests passed'}")
    sys.exit(1 if fails else 0)


real_sh = sh


def build_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    s = p.add_subparsers(dest="cmd", required=True)
    for c in ("status", "next"):
        s.add_parser(c).add_argument("n", type=int)
    x = s.add_parser("advance")
    x.add_argument("n", type=int)
    x.add_argument("gate")
    x.add_argument("--evidence")
    x.add_argument("--rerun", action="append", help="harness: a re-run summary that passes the full run's failures")
    x = s.add_parser("check-harness")
    x.add_argument("n", type=int)
    x.add_argument("--evidence", required=True)
    x.add_argument("--rerun", action="append")
    x.set_defaults(cmd="check_harness")
    x = s.add_parser("gate-tags")
    x.add_argument("n", type=int)
    x.set_defaults(cmd="gate_tags")
    x = s.add_parser("lint-lanes")
    x.add_argument("file")
    x.set_defaults(cmd="lint_lanes")
    x = s.add_parser("check")
    x.add_argument("n", type=int, nargs="?")
    x.add_argument("--branch")
    s.add_parser("guard")
    s.add_parser("selftest")
    s.add_parser("render").add_argument("--out")
    return p


def main():
    a = build_parser().parse_args()
    globals()["cmd_" + a.cmd](a)


if __name__ == "__main__":
    main()
