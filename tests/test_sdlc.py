"""tools/sdlc: the gate tool's own selftest, the guard hook end to end, and the profile settings it reads."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SDLC = os.path.join(ROOT, "tools", "sdlc", "sdlc.py")
GUARD = os.path.join(ROOT, "tools", "sdlc", "guard.sh")


def git(d, *a):
    return subprocess.run(["git", "-C", d, *a], capture_output=True, text=True, check=True).stdout.strip()


def temp_repo(profile=None):
    d = tempfile.mkdtemp()
    git(d, "init", "-q", "-b", "main")
    git(d, "config", "user.email", "t@t")
    git(d, "config", "user.name", "t")
    git(d, "config", "commit.gpgsign", "false")
    if profile is not None:
        os.makedirs(os.path.join(d, ".claude"))
        with open(os.path.join(d, ".claude", "delivery-profile.json"), "w") as f:
            json.dump(profile, f)
    with open(os.path.join(d, "a.txt"), "w") as f:
        f.write("a\n")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    return d


def hook(cmd, cwd):
    env = {k: v for k, v in os.environ.items() if k != "SDLC_PROFILE"}
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})
    return subprocess.run(["sh", GUARD], input=payload, capture_output=True, text=True, cwd=cwd, timeout=40, env=env)


class Selftest(unittest.TestCase):
    def test_selftest_passes(self):
        with tempfile.TemporaryDirectory() as d:  # outside any repo: no profile, defaults only
            r = subprocess.run([sys.executable, SDLC, "selftest"], capture_output=True, text=True, cwd=d, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-2000:])
        self.assertIn("all selftests passed", r.stdout)
        self.assertEqual([l for l in r.stdout.splitlines() if l.startswith("FAIL ")], [])

    def test_stdlib_only(self):
        with open(SDLC) as f:
            src = f.read()
        for mod in ("yaml", "requests"):
            self.assertNotIn(f"import {mod}", src)


class GuardHook(unittest.TestCase):
    def test_harmless_commands_pass_fast(self):
        d = temp_repo({"repo": "acme/app"})
        t = time.monotonic()
        for cmd in ("ls -la", "git status", "echo hello"):
            r = hook(cmd, d)
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(time.monotonic() - t, 10)

    def test_merging_an_epic_without_gates_is_blocked(self):
        d = temp_repo({"repo": "acme/app"})
        r = hook("git merge --no-ff epic/5-thing", d)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("SDLC gate blocked this command", r.stderr)

    def test_main_branch_comes_from_the_profile(self):
        d = temp_repo({"repo": "acme/app", "git": {"main_branch": "trunk", "epic_prefix": "feat/"}})
        git(d, "checkout", "-qb", "trunk")
        self.assertEqual(hook("git push origin feat/5-x:trunk", d).returncode, 2, "push of an epic to the profile's main branch")
        self.assertEqual(hook("git push origin feat/5-x:main", d).returncode, 0, "main is just a branch here")
        self.assertEqual(hook("git merge epic/5-x", d).returncode, 0, "epic/ is not this project's epic prefix")
        self.assertEqual(hook("git merge feat/5-x", d).returncode, 2)


class ProfileSettings(unittest.TestCase):
    def run_py(self, d, code):
        r = subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {os.path.dirname(SDLC)!r}); import sdlc\n{code}"],
                           capture_output=True, text=True, cwd=d, timeout=30,
                           env={k: v for k, v in os.environ.items() if k != "SDLC_PROFILE"})
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_defaults_without_a_profile(self):
        d = temp_repo()
        out = self.run_py(d, "print(sdlc.MAIN_REF, sdlc.EPIC_PREFIX, sdlc.EPICS_DIR, bool(sdlc.EXEMPT.search('docs/x.md')))")
        self.assertEqual(out, "origin/main epic/ .claude/epics True")

    def test_profile_overrides(self):
        d = temp_repo({"repo": "acme/app", "git": {"main_branch": "trunk", "remote": "up"},
                       "paths": {"exempt": ["^notes/"], "epics": "delivery/epics"}, "live": {"markers": ["--prod"]}})
        out = self.run_py(d, "print(sdlc.REPO, sdlc.MAIN_REF, sdlc.EPICS_DIR, bool(sdlc.EXEMPT.search('docs/x.md')), "
                             "bool(sdlc.EXEMPT.search('notes/x')), sdlc.reaches_live('pytest --prod'), sdlc.reaches_live('pytest'))")
        self.assertEqual(out, "acme/app up/trunk delivery/epics False True True False")

    def test_lint_lanes_command(self):
        d = temp_repo({"repo": "acme/app", "plan": {"max_tickets_per_lane": 2}})
        p = os.path.join(d, "lanes.json")
        with open(p, "w") as f:
            json.dump({"lanes": [{"id": "A", "tickets": [1, 2, 3], "deps": []}]}, f)
        r = subprocess.run([sys.executable, SDLC, "lint-lanes", p], capture_output=True, text=True, cwd=d,
                           env={k: v for k, v in os.environ.items() if k != "SDLC_PROFILE"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("lane A has 3 tickets (max 2)", r.stdout)


if __name__ == "__main__":
    unittest.main()
