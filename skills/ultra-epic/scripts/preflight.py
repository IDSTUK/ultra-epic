#!/usr/bin/env python3
"""Preflight for an /ultra-epic run. Fails closed: every check prints PASS or FAIL, exit 1 on any FAIL.

Checks (v3):
  profile      the delivery profile is valid (delivery_profile.py check)
  load         1-minute load below capacity.max_load_per_cpu x CPU count (default 2.0)
  disk         free disk at least capacity.min_disk_gb (default 15)
  main         the main checkout is on the main branch, and main equals the remote main after a fetch
  branch       at most one <epic_prefix><N>-* branch exists (a second one breaks every gate and the guard)
  epic         the issue has the label epic and at least one sub-issue
  stack        the epic's isolated test stack ports are free, or stack.free (rendered) exits 0
  quota        GitHub GraphQL quota at least capacity.min_graphql (default 2000)

Usage: preflight.py <epic> --main <main checkout> [--skip check,...]
       preflight.py --selftest
"""
import json, os, shutil, socket, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import delivery_profile as P  # noqa: E402


def run(cmd, cwd=None):
    r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, cwd=cwd, stdin=subprocess.DEVNULL)
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def port_free(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", int(port))) != 0


def checks(epic, main, prof, sh=run, free=port_free, load=os.getloadavg, cpus=os.cpu_count, disk=shutil.disk_usage):
    cap = prof.get("capacity") or {}
    git = prof.get("git") or {}
    mb, remote, pre = git.get("main_branch", "main"), git.get("remote", "origin"), git.get("epic_prefix", "epic/")
    out = []

    def add(name, ok, detail):
        out.append((name, bool(ok), detail))

    probs = P.check(prof)
    add("profile", not probs, "; ".join(probs) or "valid")
    l1, n = load()[0], cpus() or 1
    lim = float(cap.get("max_load_per_cpu", 2.0)) * n
    add("load", l1 < lim, f"load {l1:.1f}, limit {lim:.1f} ({n} CPUs)")
    gb = disk(main).free / 1e9
    add("disk", gb >= float(cap.get("min_disk_gb", 15)), f"{gb:.0f} GB free, need {cap.get('min_disk_gb', 15)}")
    sh(["git", "-C", main, "fetch", "-q", remote])
    _, cur, _ = sh(["git", "-C", main, "rev-parse", "--abbrev-ref", "HEAD"])
    _, counts, _ = sh(["git", "-C", main, "rev-list", "--left-right", "--count", f"{mb}...{remote}/{mb}"])
    ahead, behind = (counts.split() + ["?", "?"])[:2]
    add("main", cur == mb and (ahead, behind) == ("0", "0"),
        f"checkout on {cur!r}, {mb} ahead {ahead} behind {behind} of {remote}/{mb}")
    _, br, _ = sh(["git", "-C", main, "branch", "-a", "--format=%(refname:short)", "--list", f"{pre}{epic}-*", f"{remote}/{pre}{epic}-*"])
    names = sorted({b.removeprefix(f"{remote}/") for b in br.splitlines() if b})
    add("branch", len(names) <= 1, f"{names or 'none yet'}")
    rc, iss, err = sh(["gh", "issue", "view", str(epic), "-R", prof.get("repo", ""), "--json", "labels"])
    labels = [l.get("name") for l in (json.loads(iss).get("labels") or [])] if rc == 0 and iss.startswith("{") else []
    rc2, subs, _ = sh(["gh", "api", f"repos/{prof.get('repo', '')}/issues/{epic}/sub_issues"])
    nsub = len(json.loads(subs)) if rc2 == 0 and subs.startswith("[") else 0
    add("epic", "epic" in labels and nsub > 0, f"labels {labels}, {nsub} sub-issue(s)" + (f" ({err[:80]})" if rc else ""))
    if (prof.get("stack") or {}).get("free"):
        cmd = P.render(prof, "stack.free", epic)
        rc, o, e = sh(cmd, cwd=main)
        add("stack", rc == 0, f"{cmd}: exit {rc} {(o + e)[-120:]}")
    else:
        ps = P.ports(prof, epic)
        taken = [f"{k}:{v}" for k, v in ps.items() if not free(v)]
        add("stack", not taken, f"ports {ps}" + (f", taken: {taken}" if taken else ", free"))
    rc, rl, _ = sh(["gh", "api", "rate_limit"])
    left = json.loads(rl).get("resources", {}).get("graphql", {}).get("remaining", 0) if rc == 0 and rl.startswith("{") else 0
    add("quota", left >= int(cap.get("min_graphql", 2000)), f"GraphQL remaining {left}, need {cap.get('min_graphql', 2000)}")
    return out


def selftest():
    prof = {"repo": "a/b", "live": {"markers": ["--prod"]}, "components": {"API": {"paths": ["api/"], "unit": "pytest"}},
            "stack": {"up": "up", "env_file": ".env", "ports": {"db": 55400}}, "capacity": {"min_disk_gb": 1}}

    class D:
        free = 50e9

    def fake(answers):
        def sh(cmd, cwd=None):
            key = cmd if isinstance(cmd, str) else " ".join(cmd)
            for k, v in answers.items():
                if k in key:
                    return v
            return 0, "", ""
        return sh
    good = {"rev-parse --abbrev-ref": (0, "main", ""), "--left-right": (0, "0\t0", ""), "branch -a": (0, "", ""),
            "issue view": (0, '{"labels":[{"name":"epic"}]}', ""), "sub_issues": (0, '[{"number":2}]', ""),
            "rate_limit": (0, '{"resources":{"graphql":{"remaining":4000}}}', "")}
    ok = lambda **kw: {n: o for n, o, _ in checks(7, "/m", prof, **{"sh": fake(good), "free": lambda p: True,
                                                                    "load": lambda: (1.0,), "cpus": lambda: 8, "disk": lambda p: D, **kw})}
    assert all(ok().values()), ok()
    assert not ok(load=lambda: (40.0,))["load"]
    assert not ok(free=lambda p: False)["stack"]
    bad = lambda k, v: {n: o for n, o, _ in checks(7, "/m", prof, sh=fake({**good, k: v}), free=lambda p: True,
                                                    load=lambda: (1.0,), cpus=lambda: 8, disk=lambda p: D)}
    assert not bad("--left-right", (0, "1\t0", ""))["main"], "main ahead of the remote"
    assert not bad("--left-right", (0, "0\t2", ""))["main"], "main behind the remote"
    assert not bad("rev-parse --abbrev-ref", (0, "feature", ""))["main"], "main checkout not on main"
    assert not bad("branch -a", (0, "epic/7-a\nepic/7-b", ""))["branch"], "two epic branches"
    assert bad("branch -a", (0, "epic/7-a\norigin/epic/7-a", ""))["branch"], "one branch, local and remote"
    assert not bad("rate_limit", (0, '{"resources":{"graphql":{"remaining":10}}}', ""))["quota"]
    assert not bad("rate_limit", (1, "", "no network"))["quota"], "fails closed"
    assert not bad("sub_issues", (0, "[]", ""))["epic"]
    assert not bad("issue view", (0, '{"labels":[]}', ""))["epic"]
    p2 = {**prof, "stack": {**prof["stack"], "free": "stack-free --port {port_db}"}}
    seen = []
    sh = fake(good)
    r = {n: o for n, o, _ in checks(7, "/m", p2, sh=lambda c, cwd=None: (seen.append(c), sh(c, cwd))[1],
                                    free=lambda p: True, load=lambda: (1.0,), cpus=lambda: 8, disk=lambda p: D)}
    assert r["stack"] and "stack-free --port 55470" in seen, seen
    print("preflight.py selftest: ok")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a == ["--selftest"]:
        selftest()
        sys.exit(0)
    if len(a) < 3 or a[1] != "--main":
        sys.exit(__doc__)
    epic, main = int(a[0]), os.path.abspath(a[2])
    skip = set(a[a.index("--skip") + 1].split(",")) if "--skip" in a else set()
    prof = P.load(os.environ.get("SDLC_PROFILE") or os.path.join(main, ".claude", "delivery-profile.json"))
    res = [r for r in checks(epic, main, prof) if r[0] not in skip]
    for n, ok, detail in res:
        print(f"{'PASS' if ok else 'FAIL'} {n:<8} {detail}")
    sys.exit(0 if all(ok for _, ok, _ in res) else 1)
