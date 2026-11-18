"""The /ultra-epic workflow scripts, run under node with stubbed agents.

The runner gives a script the globals the Workflow tool gives it (args, agent, parallel, phase, log) and
answers each agent() call from a stub list matched on the agent's label (a regex).
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF = os.path.join(ROOT, "skills", "ultra-epic", "workflows")
NODE = shutil.which("node")

RUNNER = r"""
const fs = require('fs')
const [path, argsJson, stubsJson] = process.argv.slice(2)
const src = fs.readFileSync(path, 'utf8').replace(/^export const meta/m, 'const meta')
const stubs = JSON.parse(stubsJson)
const calls = []
const agent = async (prompt, opts = {}) => {
  calls.push({ label: opts.label || '', prompt, schema: opts.schema || null, model: opts.model || null })
  const s = stubs.find(x => new RegExp(x.match).test(opts.label || ''))
  return s ? s.value : null
}
const parallel = async fns => Promise.all(fns.map(f => f()))
const AsyncFunction = (async () => {}).constructor
new AsyncFunction('args', 'agent', 'parallel', 'phase', 'log', src)(JSON.parse(argsJson), agent, parallel, () => {}, () => {})
  .then(r => console.log(JSON.stringify({ ok: true, result: r, calls })))
  .catch(e => console.log(JSON.stringify({ ok: false, error: String((e && e.message) || e), calls })))
"""

H = "a" * 40
PROFILE = {
    "repo": "acme/app",
    "git": {"main_branch": "main", "remote": "origin", "epic_prefix": "epic/", "lane_prefix": "lane/"},
    "components": {"API": {"paths": ["api/"], "unit": "pytest api", "build": "make api", "harness_tags": ["api"]}},
    "live": {"description": "production", "markers": ["--allow-prod", "ALLOW_PROD=1"], "local_url_prefix": "http://127.0.0.1"},
    "stack": {"env_file": ".stack/test.env", "ports": {"db": 55400, "functions": 8801}, "port_step": 10, "slots": 20,
              "migrations": "db/migrations/", "apply_migration": "tools/apply.sh {file}"},
    "harness": {"command": ". {wt}/.stack/test.env && run-harness --run-id {run_id} {tags}",
                "suite_command": ". {wt}/.stack/test.env && run-harness --suite {suite}",
                "max_full_runs_per_lane": 2, "exclusive_suites": ["e2e-browser"]},
    "review": {"max_rounds": 3},
    "capacity": {"cap": 6, "rig_cap": 4},
}
LANE_OK = {"worktree": "/tmp/lane", "branch": "lane/9-A", "head": "b" * 40, "items": [], "unit_ok": True}
PASS = {"pass": True, "gaps": []}
MERGE_OK = {"ok": True, "head": "c" * 40, "note": "", "applied_local": []}
INTEG = {"head": "d" * 40, "green": True, "results": [], "children": [], "questions": [], "base_main": "e" * 40}
DONE = {"complete": True, "missing": []}


def run_wf(name, args, stubs):
    with tempfile.TemporaryDirectory() as d:
        runner = os.path.join(d, "runner.js")
        with open(runner, "w") as f:
            f.write(RUNNER)
        out = subprocess.run([NODE, runner, os.path.join(WF, name), json.dumps(args), json.dumps(stubs)],
                             capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def base(**kw):
    return {"epic": 9, "slug": "x", "repo": "acme/app", "wt": "/tmp/w", "main": "/tmp/m", "epicDir": "/tmp/w/.claude/epics/9-x",
            "skillDir": "/tmp/s", "profile": PROFILE, **kw}


def build_args(lanes, integrate=False, **kw):
    return base(head=H, cap=2, integrate=integrate, lanes=lanes, **kw)


def lane(i, unit, deps=(), tickets=(1,), **kw):
    return {"id": i, "tickets": list(tickets), "deps": list(deps), "unit": unit, "components": ["API"], **kw}


def labels(r):
    return [c["label"] for c in r["calls"]]


def prompt(r, label):
    return next(c["prompt"] for c in r["calls"] if c["label"] == label)


@unittest.skipUnless(NODE, "node not found")
class BuildRefusesLiveUnitCommands(unittest.TestCase):
    def refused(self, unit):
        r = run_wf("build.js", build_args([lane("A", ["pytest"]), lane("B", unit)]), [])
        self.assertFalse(r["ok"], f"build.js accepted {unit}")
        self.assertIn("LIVE", r["error"])
        self.assertIn("B:", r["error"])
        self.assertEqual(r["calls"], [], "no agent may start before the unit commands are checked")

    def test_live_flag(self):
        self.refused(["pytest --allow-prod"])

    def test_live_env(self):
        self.refused(["ALLOW_PROD=1 pytest"])

    def test_unit_as_a_string(self):
        self.refused("pytest --allow-prod")

    def test_needs_the_profile(self):
        args = build_args([lane("A", ["pytest"])])
        del args["profile"]
        r = run_wf("build.js", args, [])
        self.assertFalse(r["ok"])
        self.assertIn("profile", r["error"])


STUBS = [{"match": "^build:", "value": LANE_OK}, {"match": "^qa-(accept|review):", "value": PASS},
         {"match": "^merge:", "value": MERGE_OK}]


@unittest.skipUnless(NODE, "node not found")
class BuildMergesFirstThenQaOnce(unittest.TestCase):
    """v3: a lane merges when its unit tests pass and the merge is clean. QA runs once, on the merged epic."""

    def test_every_lane_merges_before_any_qa(self):
        r = run_wf("build.js", build_args([lane("A", ["pytest"]), lane("B", ["pytest"], deps=["A"])]), STUBS)
        self.assertTrue(r["ok"], r.get("error"))
        ls = labels(r)
        self.assertEqual(r["result"]["mergedLanes"], ["A", "B"])
        first_qa = min(i for i, l in enumerate(ls) if l.startswith(("qa-accept", "qa-review")))
        last_merge = max(i for i, l in enumerate(ls) if l.startswith("merge:"))
        self.assertLess(last_merge, first_qa, ls)
        self.assertEqual(sum(l.startswith("qa-accept:") for l in ls), 2, "one acceptance check per merged lane, once")

    def test_red_unit_gets_two_repairs_then_fails_without_merge(self):
        stubs = [{"match": "^build:", "value": {**LANE_OK, "unit_ok": False}}, {"match": "^repair:", "value": {**LANE_OK, "unit_ok": False}}] + STUBS[1:]
        r = run_wf("build.js", build_args([lane("A", ["pytest"])]), stubs)
        self.assertEqual(r["result"]["mergedLanes"], [])
        self.assertEqual([l for l in labels(r) if l.startswith("repair:")], ["repair:A#1", "repair:A#2"])
        self.assertNotIn("merge:A", labels(r))

    def test_unclean_merge_fails_the_lane_and_skips_its_dependants(self):
        stubs = [STUBS[0], STUBS[1], {"match": "^merge:A$", "value": {"ok": False, "head": H, "note": "conflict"}}]
        r = run_wf("build.js", build_args([lane("A", ["pytest"]), lane("B", ["pytest"], deps=["A"])]), stubs)
        states = {f["id"]: f["state"] for f in r["result"]["failed"]}
        self.assertEqual(states, {"A": "failed", "B": "skipped"})

    def test_qa_gaps_do_not_unmerge_a_lane(self):
        gap = {"pass": False, "gaps": [{"ref": "#1", "problem": "no test for GWT 2", "severity": "critical", "owned": True}]}
        stubs = [STUBS[0], {"match": "^qa-accept:A$", "value": gap}, {"match": "^qa-(accept|review):", "value": PASS},
                 {"match": "^qa-fix:", "value": None}, {"match": "^merge:", "value": MERGE_OK}]
        r = run_wf("build.js", build_args([lane("A", ["pytest"])]), stubs)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(r["result"]["mergedLanes"], ["A"], "the lane stays merged")
        qa = [f for f in r["result"]["failed"] if f["state"] == "qa"]
        self.assertEqual([f["id"] for f in qa], ["QA"], "the QA fix lane that did not land is reported")
        fix = next(c["prompt"] for c in r["calls"] if c["label"] == "qa-fix:QA")
        self.assertIn("no test for GWT 2", fix)

    def test_checker_gets_lane_scope_and_tier(self):
        r = run_wf("build.js", build_args([lane("A", ["pytest"], tickets=[11, 12], files=["api/x.py"], tier=1)],
                                          ticketLane={"11": "A", "12": "A"}), STUBS)
        for label in ("qa-accept:A", "qa-review:A"):
            p = prompt(r, label)
            self.assertIn("Lane A owns ONLY: tickets #11, #12", p)
            self.assertIn('files ["api/x.py"]', p)
            self.assertIn("Plan test tier for this lane: 1", p)
            self.assertIn("owned=false", p)

    def test_a_gap_another_lane_owns_goes_to_that_lane(self):
        other = {"pass": False, "gaps": [{"ref": "#22", "problem": "B's endpoint lacks a test", "severity": "important",
                                          "owned": False, "owner_ticket": 22}]}
        stubs = [STUBS[0], {"match": "^qa-accept:A$", "value": other}, {"match": "^qa-(accept|review):", "value": PASS},
                 {"match": "^qa-fix:", "value": LANE_OK}, {"match": "^merge:", "value": MERGE_OK}]
        r = run_wf("build.js", build_args([lane("A", ["pytest"], tickets=[21]), lane("B", ["pytest"], tickets=[22])],
                                          ticketLane={"21": "A", "22": "B"}), stubs)
        ls = labels(r)
        self.assertIn("qa-fix:QB", ls, "B owns the gap")
        self.assertNotIn("qa-fix:QA", ls, "A does not fail for B's work")
        self.assertEqual(r["result"]["failed"], [])

    def test_dead_checker_is_a_gap_never_a_pass(self):
        stubs = [STUBS[0], {"match": "^qa-review:A$", "value": None}, {"match": "^qa-(accept|review):", "value": PASS},
                 {"match": "^qa-fix:", "value": None}, {"match": "^merge:", "value": MERGE_OK}]
        r = run_wf("build.js", build_args([lane("A", ["pytest"])]), stubs)
        self.assertIn("checker returned nothing", prompt(r, "qa-fix:QA"))

    def test_merged_lanes_keep_the_earlier_runs(self):
        r = run_wf("build.js", {**build_args([lane("A", ["pytest"])]), "merged": ["X"]}, STUBS)
        self.assertEqual(r["result"]["mergedLanes"], ["X", "A"])

    def test_failed_lane_keeps_its_local_migrations(self):
        stubs = [STUBS[0], STUBS[1], {"match": "^merge:A$", "value": {"ok": False, "head": H, "note": "red", "applied_local": ["7_a.sql"]}}]
        r = run_wf("build.js", build_args([lane("A", ["pytest"])]), stubs)
        self.assertEqual(r["result"]["failed"][0]["applied_local"], ["7_a.sql"])

    def test_merger_applies_migrations_locally_and_never_resets_after(self):
        r = run_wf("build.js", build_args([lane("A", ["pytest"])]), STUBS)
        m = prompt(r, "merge:A")
        self.assertIn("tools/apply.sh <file>", m)
        self.assertIn("never reset", m)
        self.assertIn("db/migrations/", m)

    def test_exclusive_suites_go_through_the_suite_lock(self):
        r = run_wf("build.js", build_args([lane("A", ["pytest"])]), STUBS)
        self.assertIn("sh /tmp/s/scripts/suite_lock.sh <suite> <command>", prompt(r, "build:A"))
        self.assertIn("e2e-browser", prompt(r, "build:A"))

    def test_one_isolated_stack_per_epic(self):
        r = run_wf("build.js", build_args([lane("A", ["pytest"])]), STUBS)
        self.assertIn('{"db":55490,"functions":8891}', prompt(r, "build:A"), "epic 9's own ports")
        self.assertIn("Never start, stop or reset the stack", prompt(r, "build:A"))


@unittest.skipUnless(NODE, "node not found")
class BuildHarnessAndIntegrate(unittest.TestCase):
    def stubs(self, groups=None, harness=LANE_OK):
        return STUBS + [{"match": "^harness-gaps$", "value": {"groups": groups or []}}, {"match": "^harness:", "value": harness},
                        {"match": "^integrate#", "value": INTEG}, {"match": "^completeness$", "value": DONE}]

    def test_integrate_returns_base_main(self):
        r = run_wf("build.js", build_args([lane("A", ["pytest"])], integrate=True), self.stubs())
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(r["result"]["baseMain"], "e" * 40)
        schema = next(c["schema"] for c in r["calls"] if c["label"].startswith("integrate#"))
        self.assertIn("base_main", schema["required"])

    def test_harness_lanes_start_only_after_every_feature_lane_merged(self):
        stubs = [STUBS[0], STUBS[1], {"match": "^merge:B$", "value": {"ok": False, "head": H, "note": "conflict"}},
                 {"match": "^merge:", "value": MERGE_OK}] + self.stubs([{"name": "a", "scenarios": ["s1"]}])[3:]
        r = run_wf("build.js", build_args([lane("A", ["pytest"]), lane("B", ["pytest"])], integrate=True), stubs)
        self.assertNotIn("harness-gaps", labels(r))
        self.assertFalse(any(l.startswith("harness:") for l in labels(r)))
        ls = self.stubs([{"name": "a", "scenarios": ["s1"]}])
        r = run_wf("build.js", build_args([lane("A", ["pytest"])], integrate=True), ls)
        ls = labels(r)
        self.assertLess(max(i for i, l in enumerate(ls) if l == "merge:A"), ls.index("harness:H1"))
        self.assertLess(max(i for i, l in enumerate(ls) if l.startswith(("qa-accept", "qa-review"))), ls.index("harness:H1"), "after QA too")

    def test_harness_lane_stops_after_two_full_runs_and_reports(self):
        red = {**LANE_OK, "unit_ok": False, "suite_runs": 2, "failing": ["s1::case"]}
        r = run_wf("build.js", build_args([lane("A", ["pytest"])], integrate=True), self.stubs([{"name": "a", "scenarios": ["s1"]}], red))
        p = prompt(r, "harness:H1")
        self.assertIn("HARD LIMIT: at most 2 full runs", p)
        self.assertIn("do not chase failures", p)
        f = next(f for f in r["result"]["failed"] if f["id"] == "H1")
        self.assertEqual(f["state"], "harness")
        self.assertIn("s1::case", f["why"])
        self.assertEqual(r["result"]["harnessReports"], [{"id": "H1", "suite_runs": 2, "failing": ["s1::case"]}])
        self.assertNotIn("merge:H1", labels(r), "a red harness lane is reported, not merged")


@unittest.skipUnless(NODE, "node not found")
class ReviewBlocksOnlyWhatMatters(unittest.TestCase):
    def f(self, sev, reg=False, sec=False, line=1):
        return {"file": f"a{line}.py", "line": line, "severity": sev, "category": f"c{line}", "summary": sev, "scenario": "x",
                "regression": reg, "security": sec}

    def review(self, findings, round=1):
        stubs = [{"match": "^review:correctness$", "value": {"findings": findings}}, {"match": "^review:", "value": {"findings": []}},
                 {"match": "^verify:", "value": {"refuted": False, "reason": "real"}}, {"match": "^evidence$", "value": "/tmp/r.md"}]
        return run_wf("review.js", base(head=H, round=round, focus=""), stubs)

    def test_medium_and_low_become_tickets(self):
        r = self.review([self.f("medium", line=1), self.f("low", line=20)])
        self.assertTrue(r["result"]["pass"])
        self.assertEqual(len(r["result"]["followUps"]), 2)

    def test_high_regression_and_security_block(self):
        for f in (self.f("high"), self.f("medium", reg=True), self.f("low", sec=True), self.f("critical")):
            r = self.review([f])
            self.assertFalse(r["result"]["pass"], f)
            self.assertEqual(len(r["result"]["blocking"]), 1)

    def test_refuted_findings_do_not_block(self):
        stubs = [{"match": "^review:correctness$", "value": {"findings": [self.f("high")]}}, {"match": "^review:", "value": {"findings": []}},
                 {"match": "^verify:", "value": {"refuted": True, "reason": "guarded"}}, {"match": "^evidence$", "value": "x"}]
        r = run_wf("review.js", base(head=H, round=1), stubs)
        self.assertTrue(r["result"]["pass"])
        self.assertEqual(r["result"]["refutedCount"], 1)

    def test_circuit_breaker(self):
        r = self.review([self.f("high")], round=4)
        self.assertTrue(r["result"]["askHuman"])
        self.assertEqual(r["calls"], [], "past max_rounds nothing runs")
        r = self.review([self.f("high")], round=3)
        self.assertTrue(r["result"]["askHuman"], "the last allowed round fails: ask the human")
        r = self.review([self.f("high")], round=2)
        self.assertFalse(r["result"]["askHuman"])

    def test_dead_finder_fails_the_round(self):
        stubs = [{"match": "^review:correctness$", "value": None}, {"match": "^review:", "value": {"findings": []}},
                 {"match": "^evidence$", "value": "x"}]
        self.assertFalse(run_wf("review.js", base(head=H, round=1), stubs)["result"]["pass"])

    def test_panel_seat_only_when_the_profile_names_a_skill(self):
        r = self.review([])
        self.assertNotIn("panel", labels(r))
        prof = {**PROFILE, "review": {"max_rounds": 3, "panel_skill": "panel-review"}}
        stubs = [{"match": "^(review:|panel)", "value": {"findings": []}}, {"match": "^evidence$", "value": "x"}]
        r = run_wf("review.js", base(head=H, round=1, profile=prof), stubs)
        self.assertIn("Run the panel-review skill's review steps only", prompt(r, "panel"))


@unittest.skipUnless(NODE, "node not found")
class FinalRunsSecurityThenHarness(unittest.TestCase):
    ARGS = base(head=H, tags=["api", "epic9", "e9"], components=["API"], round=1)
    HARN = {"run_id": "epic9-aaaaaaa", "summary_json": "/tmp/w/.claude/epics/9-x/harness-summary.json", "tests": 5, "failures": 0,
            "errors": 0, "skipped": 0, "git_sha": H, "failing": [], "left_out": [], "waived": {}}

    def final(self, sec=(), sweep_exit=0, gate_exit=0, waivers=False):
        stubs = [{"match": "^setup$", "value": {"ok": True, "note": "", "waivers": waivers}},
                 {"match": "^sweep:", "value": {"results": [{"cmd": "x", "exit": sweep_exit, "counts": "1"}]}},
                 {"match": "^security:authz$", "value": {"findings": list(sec)}}, {"match": "^security", "value": {"findings": []}},
                 {"match": "^verify:", "value": {"refuted": False, "reason": "real"}},
                 {"match": "^harness-run$", "value": self.HARN},
                 {"match": "^compare$", "value": {"green": True, "note": "", "gate_exit": gate_exit, "gate_output": "x"}},
                 {"match": "^evidence$", "value": "done"}]
        r = run_wf("final.js", self.ARGS, stubs)
        self.assertTrue(r["ok"], r.get("error"))
        return r

    def test_harness_is_last_and_runs_only_after_security_passed(self):
        r = self.final()
        ls = labels(r)
        self.assertTrue(r["result"]["pass"])
        self.assertLess(max(i for i, l in enumerate(ls) if l.startswith(("security", "sweep", "verify"))), ls.index("harness-run"))
        bad = {"file": "a", "line": 1, "severity": "high", "category": "authz", "summary": "x", "scenario": "y"}
        r = self.final(sec=[bad])
        self.assertFalse(r["result"]["securityPass"])
        self.assertNotIn("harness-run", labels(r), "no harness hour on a red security round")
        r = self.final(sweep_exit=1)
        self.assertNotIn("harness-run", labels(r), "nor on a red sweep")

    def test_green_only_when_the_gate_check_passes(self):
        r = self.final(gate_exit=0)
        self.assertIn("python3 tools/sdlc/sdlc.py check-harness 9 --evidence", prompt(r, "compare"))
        self.assertTrue(r["result"]["harnessGreen"])
        r = self.final(gate_exit=1)
        self.assertFalse(r["result"]["harnessGreen"])
        self.assertIn("write no harness.md and flip nothing", prompt(r, "evidence"))

    def test_harness_command_from_the_profile_with_tags_and_waivers(self):
        run = prompt(self.final(), "harness-run")
        self.assertIn(". /tmp/w-h/.stack/test.env && run-harness --run-id epic9-aaaaaaa --tag api --tag epic9 --tag e9", run)
        self.assertNotIn("--local-waivers", run)
        run = prompt(self.final(waivers=True), "harness-run")
        self.assertIn("--tag e9 --local-waivers .claude/epics/9-x/local-waivers.json", run)

    def test_tags_must_be_the_gate_tags(self):
        r = run_wf("final.js", {**self.ARGS, "tags": ["api", "epic9"]}, [])
        self.assertFalse(r["ok"])
        self.assertIn("gate-tags", r["error"])
        self.assertEqual(r["calls"], [])

    def test_setup_failure_is_not_a_code_fault(self):
        r = run_wf("final.js", self.ARGS, [{"match": "^setup$", "value": {"ok": False, "note": "env file not local", "waivers": False}}])
        self.assertEqual(r["result"]["setupFailed"], "env file not local")
        self.assertEqual(labels(r), ["setup"])


@unittest.skipUnless(NODE, "node not found")
class PlanEnforcesReadyAndLaneRules(unittest.TestCase):
    def refined(self, n, **kw):
        return {"n": n, "ready": True, "criteria": [{"gwt": "Given a When b Then c", "seam": "api", "test": "t.py", "tier": 0, "exists": False}],
                "files": ["api/x.py"], "migration": False, "changes_state": False, "size": "S", "edges": [], "questions": [], **kw}

    def plan(self, refined, lint=(0,)):
        stubs = [{"match": f"^refine:#{r['n']}$", "value": r} for r in refined] + \
                [{"match": f"^lint-lanes#{i}$", "value": {"exit": e, "output": "lane A has 6 tickets" if e else "lanes ok"}} for i, e in enumerate(lint)] + \
                [{"match": "^critic:", "value": {"defects": []}}, {"match": "^(draft-plan|fix-lanes)", "value": "ok"}]
        return run_wf("plan.js", base(tickets=[r["n"] for r in refined], snapshot="/tmp/snap.json"), stubs)

    def test_state_change_without_a_state_table_is_not_ready(self):
        r = self.plan([self.refined(1, changes_state=True), self.refined(2, changes_state=True, state_table="| a | b |\n|---|---|\n| on | x |"),
                       self.refined(3)])
        un = {u["n"]: u for u in r["result"]["unready"]}
        self.assertEqual(set(un), {1})
        self.assertIn("state table", un[1]["questions"][-1])
        self.assertIn("state_table", json.dumps(next(c["schema"] for c in r["calls"] if c["label"] == "refine:#1")))

    def test_a_criterion_with_no_test_is_not_ready(self):
        r = self.plan([self.refined(1, criteria=[{"gwt": "g", "seam": "s", "test": "", "tier": 0, "exists": False}])])
        self.assertEqual([u["n"] for u in r["result"]["unready"]], [1])

    def test_lane_rules_checked_by_the_gate_tool_and_fixed(self):
        r = self.plan([self.refined(1)], lint=(1, 0))
        ls = labels(r)
        self.assertEqual([l for l in ls if l.startswith(("lint", "fix"))], ["lint-lanes#0", "fix-lanes#0", "lint-lanes#1"])
        self.assertIn("python3 tools/sdlc/sdlc.py lint-lanes", prompt(r, "lint-lanes#0"))
        self.assertTrue(r["result"]["laneRules"]["ok"])
        self.assertIn("at most 5 tickets", prompt(r, "draft-plan"))
        r = self.plan([self.refined(1)], lint=(1, 1, 1))
        self.assertFalse(r["result"]["laneRules"]["ok"], "still broken after the fix rounds: STOP 1 shows it")


if __name__ == "__main__":
    unittest.main()
