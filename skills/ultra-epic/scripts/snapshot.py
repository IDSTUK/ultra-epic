#!/usr/bin/env python3
"""Board snapshot for one epic, in the shape waves.py reads. GitHub only, through the gh CLI.

  [{"number", "state": "OPEN"|"CLOSED", "subs": [child numbers in sub-issue order],
    "open_blockers": [open blocked-by issue numbers], "fields": {"Component": "<name>"}}, ...]

Sub-issues come from the sub-issues API, dependencies from GitHub's native blocked-by links
(issues/<n>/dependencies/blocked_by). The component is the first label that starts with the profile's
board.component_label_prefix (default "component:"). A project that keeps its board elsewhere replaces
this script with its own command that writes the same shape.

Usage: snapshot.py <epic> --repo owner/name [--prefix component:] --out F
       snapshot.py --selftest
"""
import json, subprocess, sys


def gh_json(args):
    r = subprocess.run(["gh", "api", *args], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if r.returncode:
        sys.exit(f"gh api {' '.join(args)} failed: {(r.stdout + r.stderr)[:300]}")
    return json.loads(r.stdout or "null")


def pages(api, path):
    """--paginate --slurp gives a list of pages; flatten them."""
    out = api([path, "--paginate", "--slurp"])
    return [x for page in out or [] for x in (page if isinstance(page, list) else [page])]


def component(issue, prefix):
    for l in issue.get("labels") or []:
        name = l.get("name", "") if isinstance(l, dict) else str(l)
        if name.startswith(prefix):
            return name[len(prefix):].strip()
    return ""


def snapshot(epic, repo, prefix="component:", api=gh_json):
    base = f"repos/{repo}/issues"
    e = api([f"{base}/{epic}"])
    kids = pages(api, f"{base}/{epic}/sub_issues")
    out = [{"number": epic, "state": e["state"].upper(), "subs": [k["number"] for k in kids], "open_blockers": [],
            "fields": {"Component": component(e, prefix)}}]
    for k in kids:
        bl = pages(api, f"{base}/{k['number']}/dependencies/blocked_by")
        out.append({"number": k["number"], "state": k["state"].upper(), "subs": [],
                    "open_blockers": [b["number"] for b in bl if b.get("state") == "open"],
                    "fields": {"Component": component(k, prefix)}})
    return out


def selftest():
    data = {"repos/a/b/issues/1": {"number": 1, "state": "open", "labels": [{"name": "epic"}]},
            "repos/a/b/issues/1/sub_issues": [[{"number": 2, "state": "open", "labels": [{"name": "component:API"}]},
                                              {"number": 3, "state": "closed", "labels": []}]],
            "repos/a/b/issues/2/dependencies/blocked_by": [[{"number": 9, "state": "open"}, {"number": 8, "state": "closed"}]],
            "repos/a/b/issues/3/dependencies/blocked_by": [[]]}
    api = lambda args: data[args[0]]
    s = snapshot(1, "a/b", api=api)
    assert s[0] == {"number": 1, "state": "OPEN", "subs": [2, 3], "open_blockers": [], "fields": {"Component": ""}}, s[0]
    assert s[1]["open_blockers"] == [9] and s[1]["fields"]["Component"] == "API", s[1]
    assert s[2]["state"] == "CLOSED"
    print("snapshot.py selftest: ok")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a == ["--selftest"]:
        selftest()
        sys.exit(0)
    if len(a) < 1 or "--repo" not in a or "--out" not in a:
        sys.exit(__doc__)
    prefix = a[a.index("--prefix") + 1] if "--prefix" in a else "component:"
    snap = snapshot(int(a[0]), a[a.index("--repo") + 1], prefix)
    with open(a[a.index("--out") + 1], "w") as f:
        json.dump(snap, f, indent=1)
    print(f"{len(snap) - 1} child issue(s) written")
