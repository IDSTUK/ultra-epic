"""skills/ultra-epic/scripts: selftests, the lane rule kept equal in waves.py and sdlc.py, the suite lock and lane_env.sh."""
import importlib.util
import json
import os
import random
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S = os.path.join(ROOT, "skills", "ultra-epic", "scripts")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def has_yaml():
    return importlib.util.find_spec("yaml") is not None


class Selftests(unittest.TestCase):
    def selftest(self, script):
        r = subprocess.run([sys.executable, os.path.join(S, script), "--selftest"], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("selftest: ok", r.stdout)

    def test_waves(self):
        self.selftest("waves.py")

    def test_delivery_profile(self):
        self.selftest("delivery_profile.py")

    def test_preflight(self):
        self.selftest("preflight.py")

    def test_snapshot(self):
        self.selftest("snapshot.py")

    @unittest.skipUnless(has_yaml(), "PyYAML not installed (merge_scenarios.py needs it; the gate tool does not)")
    def test_merge_scenarios(self):
        self.selftest("merge_scenarios.py")

    def test_example_profile_is_valid(self):
        env = {**os.environ, "SDLC_PROFILE": os.path.join(ROOT, "profile", "delivery-profile.example.json")}
        r = subprocess.run([sys.executable, os.path.join(S, "delivery_profile.py"), "check"], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_flying_start_profile_is_valid(self):
        env = {**os.environ, "SDLC_PROFILE": os.path.join(ROOT, "examples", "flying-start", "delivery-profile.json")}
        r = subprocess.run([sys.executable, os.path.join(S, "delivery_profile.py"), "check"], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class LaneRuleIsOneRule(unittest.TestCase):
    """waves.py (plan time) and sdlc.py (plan gate) apply the same lane rule."""

    def test_same_answers_on_random_graphs(self):
        sys.path.insert(0, os.path.join(ROOT, "tools", "sdlc"))
        with tempfile.TemporaryDirectory() as d:
            cwd = os.getcwd()
            os.chdir(d)
            try:
                sdlc = load("sdlc_mod", os.path.join(ROOT, "tools", "sdlc", "sdlc.py"))
            finally:
                os.chdir(cwd)
        waves = load("waves_mod", os.path.join(S, "waves.py"))
        rnd = random.Random(7)
        for _ in range(300):
            n = rnd.randint(1, 8)
            ids = [chr(65 + i) for i in range(n)]
            lanes = [{"id": x, "tickets": list(range(rnd.randint(1, 7))),
                      "deps": sorted({y for y in ids[:i] if rnd.random() < 0.4})} for i, x in enumerate(ids)]
            mt, share = rnd.randint(3, 6), rnd.choice([0.3, 0.5, 0.7])
            self.assertEqual(sdlc.lane_problems(lanes, mt, share), waves.lane_problems(lanes, mt, share), lanes)


class SuiteLock(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.env = {**os.environ, "UE_LOCK_DIR": self.dir}

    def test_two_holders_never_overlap(self):
        log = os.path.join(self.dir, "log")
        cmd = f'echo start >> {log}; sleep 1; echo end >> {log}'
        ps = [subprocess.Popen(["sh", os.path.join(S, "suite_lock.sh"), "e2e", "sh", "-c", cmd], env=self.env) for _ in range(3)]
        for p in ps:
            self.assertEqual(p.wait(timeout=30), 0)
        with open(log) as f:
            self.assertEqual(f.read().split(), ["start", "end"] * 3, "each run ends before the next starts")

    def test_different_suites_run_together(self):
        t = time.monotonic()
        ps = [subprocess.Popen(["sh", os.path.join(S, "suite_lock.sh"), s, "sleep", "1"], env=self.env) for s in ("a", "b")]
        for p in ps:
            p.wait(timeout=30)
        self.assertLess(time.monotonic() - t, 1.9)

    def test_exit_code_passes_through_and_lock_is_freed(self):
        r = subprocess.run(["sh", os.path.join(S, "suite_lock.sh"), "e2e", "sh", "-c", "exit 3"], env=self.env)
        self.assertEqual(r.returncode, 3)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "e2e.lock")))

    def test_dead_holder_is_cleared(self):
        os.makedirs(os.path.join(self.dir, "e2e.lock"))
        dead = subprocess.Popen(["true"])
        dead.wait()
        with open(os.path.join(self.dir, "e2e.lock", "pid"), "w") as f:
            f.write(str(dead.pid))
        r = subprocess.run(["sh", os.path.join(S, "suite_lock.sh"), "e2e", "true"], env=self.env, timeout=20)
        self.assertEqual(r.returncode, 0)

    def test_gives_up_with_75(self):
        os.makedirs(os.path.join(self.dir, "e2e.lock"))
        with open(os.path.join(self.dir, "e2e.lock", "pid"), "w") as f:
            f.write(str(os.getpid()))  # a live holder
        r = subprocess.run(["sh", os.path.join(S, "suite_lock.sh"), "e2e", "true"], env={**self.env, "UE_LOCK_WAIT": "1"},
                           capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 75, r.stderr)


class Capacity(unittest.TestCase):
    def test_check_prints_the_numbers(self):
        r = subprocess.run(["sh", os.path.join(S, "capacity.sh"), "check"], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("CPU idle", r.stdout)

    def test_bg_runs_the_command(self):
        r = subprocess.run(["sh", os.path.join(S, "capacity.sh"), "bg", "echo", "hi"], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.stdout.strip(), "hi")


class LaneEnv(unittest.TestCase):
    def git(self, d, *a):
        return subprocess.run(["git", "-C", d, *a], capture_output=True, text=True, check=True).stdout.strip()

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.main = os.path.join(self.tmp, "main")
        os.makedirs(os.path.join(self.main, ".claude"))
        self.git(self.main, "init", "-q", "-b", "main")
        self.git(self.main, "config", "user.email", "t@t")
        self.git(self.main, "config", "user.name", "t")
        self.git(self.main, "config", "commit.gpgsign", "false")
        with open(os.path.join(self.main, ".claude", "delivery-profile.json"), "w") as f:
            json.dump({"git": {"links": ["web/node_modules"]}, "stack": {"env_file": ".stack/test.env"}}, f)
        os.makedirs(os.path.join(self.main, "web"))
        with open(os.path.join(self.main, "web", "package-lock.json"), "w") as f:
            f.write("{}\n")
        self.git(self.main, "add", "-A")
        self.git(self.main, "commit", "-qm", "init")
        os.makedirs(os.path.join(self.main, "web", "node_modules"))
        self.epic = os.path.join(self.tmp, "epic")
        self.lane = os.path.join(self.tmp, "lane")
        self.git(self.main, "worktree", "add", "-q", "-b", "epic/9-x", self.epic)
        self.git(self.main, "worktree", "add", "-q", "-b", "lane/9-A", self.lane)

    def env(self, wt, *extra):
        return subprocess.run(["sh", os.path.join(S, "lane_env.sh"), wt, self.main, self.epic, *extra], capture_output=True, text=True, timeout=30)

    def test_refuses_the_main_checkout(self):
        self.assertNotEqual(self.env(self.main).returncode, 0)

    def test_no_stack_yet_is_an_error_for_a_lane(self):
        r = self.env(self.lane)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("stack.up", r.stderr)

    def test_links_installs_and_the_epic_stack(self):
        os.makedirs(os.path.join(self.epic, ".stack"))
        with open(os.path.join(self.epic, ".stack", "test.env"), "w") as f:
            f.write("export DB_URL=http://127.0.0.1:55490\n")
        r = self.env(self.lane)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.islink(os.path.join(self.lane, "web", "node_modules")))
        self.assertEqual(os.path.realpath(os.path.join(self.lane, ".stack", "test.env")),
                         os.path.realpath(os.path.join(self.epic, ".stack", "test.env")))
        self.assertEqual(self.git(self.lane, "status", "--porcelain"), "", "links are excluded, not untracked code")
        r = self.env(self.lane, "--remove")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.lexists(os.path.join(self.lane, "web", "node_modules")))
        self.assertFalse(os.path.lexists(os.path.join(self.lane, ".stack", "test.env")))

    def test_no_link_when_the_lockfile_differs(self):
        os.makedirs(os.path.join(self.epic, ".stack"))
        open(os.path.join(self.epic, ".stack", "test.env"), "w").close()
        with open(os.path.join(self.lane, "web", "package-lock.json"), "w") as f:
            f.write('{"changed": true}\n')
        r = self.env(self.lane)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.lexists(os.path.join(self.lane, "web", "node_modules")))
        self.assertIn("differs", r.stdout)


if __name__ == "__main__":
    unittest.main()
