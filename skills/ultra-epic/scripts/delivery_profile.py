#!/usr/bin/env python3
"""Read the delivery profile and render its command hooks.

The profile is <repo>/.claude/delivery-profile.json (or $SDLC_PROFILE). See profile/delivery-profile.md.

Placeholders in a command template:
  {epic} {wt} {main} {file} {suite} {tags} {run_id}   given with --set key=value
  {port_<name>}                                       the epic's own port for stack.ports.<name>

One isolated test stack per epic: each port is stack.ports.<name> + (epic % stack.slots) * stack.port_step,
so two epics on one machine never share a database or a functions port (v3).

Usage:
  delivery_profile.py get <dotted.key>                     print a value (JSON for objects and lists)
  delivery_profile.py render <dotted.key> --epic N [--set k=v]...   print a command with its placeholders filled
  delivery_profile.py ports <epic>                         print `export PORT_<NAME>=<port>` lines for the epic
  delivery_profile.py check                                validate the profile (exit 1 with the problems)
  delivery_profile.py --selftest
"""
import json, os, re, subprocess, sys

REQUIRED = ["repo", "components", "live.markers", "stack.up", "stack.env_file"]


def root():
    return subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip() or os.getcwd()


def load(path=None):
    p = path or os.environ.get("SDLC_PROFILE") or os.path.join(root(), ".claude", "delivery-profile.json")
    if not os.path.exists(p):
        sys.exit(f"no delivery profile at {p}: copy profile/delivery-profile.example.json there and edit it")
    with open(p) as f:
        return json.load(f)


def get(prof, key, default=None):
    cur = prof
    for k in key.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def ports(prof, epic):
    st = prof.get("stack") or {}
    step, slots = int(st.get("port_step", 10)), int(st.get("slots", 20))
    return {name: int(base) + (int(epic) % slots) * step for name, base in (st.get("ports") or {}).items()}


class Keep(dict):
    def __missing__(self, k):
        return "{" + k + "}"


def render(prof, key, epic=None, values=None):
    tpl = get(prof, key)
    if not isinstance(tpl, str):
        sys.exit(f"profile key {key} is not a command string")
    vals = Keep(values or {})
    if epic is not None:
        vals["epic"] = str(epic)
        vals.update({f"port_{k}": str(v) for k, v in ports(prof, epic).items()})
    return tpl.format_map(vals)


def check(prof):
    out = [f"missing {k}" for k in REQUIRED if get(prof, k) in (None, "", [], {})]
    for c, v in (prof.get("components") or {}).items():
        if not v.get("paths"):
            out.append(f"component {c} has no paths")
        if not v.get("unit"):
            out.append(f"component {c} has no unit command")
        for m in get(prof, "live.markers", []) or []:
            if v.get("unit") and re.search(m, v["unit"]):
                out.append(f"component {c} unit command names live marker {m!r}")
    ps = ports(prof, 0)
    if len(set(ps.values())) != len(ps):
        out.append("stack.ports: two names share a base port")
    step, slots = get(prof, "stack.port_step", 10), get(prof, "stack.slots", 20)
    bases = sorted(ps.values())
    for a, b in zip(bases, bases[1:]):
        if b - a < step * slots and a % step == b % step:
            out.append(f"stack.ports {a} and {b} collide for some epic: put them in different residues of port_step")
    return out


def selftest():
    prof = {"repo": "a/b", "live": {"markers": ["--prod"]}, "components": {"API": {"paths": ["api/"], "unit": "pytest"}},
            "stack": {"up": "up.sh --db {port_db} --fn {port_functions} --wt {wt}", "env_file": ".stack/env",
                      "ports": {"db": 55400, "functions": 8801}, "port_step": 10, "slots": 20}}
    assert ports(prof, 7) == {"db": 55470, "functions": 8871}, ports(prof, 7)
    assert ports(prof, 27) == ports(prof, 7), "slots wrap"
    assert ports(prof, 7) != ports(prof, 8)
    assert render(prof, "stack.up", 7, {"wt": "/w"}) == "up.sh --db 55470 --fn 8871 --wt /w"
    assert render(prof, "stack.up", 7) == "up.sh --db 55470 --fn 8871 --wt {wt}", "unknown placeholders stay"
    assert check(prof) == [], check(prof)
    bad = {**prof, "components": {"API": {"paths": ["api/"], "unit": "pytest --prod"}}}
    assert any("live marker" in p for p in check(bad)), check(bad)
    clash = {**prof, "stack": {**prof["stack"], "ports": {"db": 8800, "functions": 8900}}}
    assert any("collide" in p for p in check(clash)), check(clash)
    assert any("missing repo" in p for p in check({}))
    print("delivery_profile.py selftest: ok")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a == ["--selftest"]:
        selftest()
    elif len(a) == 2 and a[0] == "get":
        v = get(load(), a[1])
        print(v if isinstance(v, str) else json.dumps(v))
    elif len(a) == 2 and a[0] == "ports":
        for k, v in ports(load(), a[1]).items():
            print(f"export PORT_{k.upper()}={v}")
    elif len(a) == 1 and a[0] == "check":
        probs = check(load())
        print("\n".join(probs) or "profile ok")
        sys.exit(1 if probs else 0)
    elif len(a) >= 2 and a[0] == "render":
        key, epic, vals, rest = a[1], None, {}, a[2:]
        while rest:
            if rest[0] == "--epic":
                epic, rest = rest[1], rest[2:]
            elif rest[0] == "--set" and "=" in rest[1]:
                k, v = rest[1].split("=", 1)
                vals[k], rest = v, rest[2:]
            else:
                sys.exit(__doc__)
        print(render(load(), key, epic, vals))
    else:
        sys.exit(__doc__)
