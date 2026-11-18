#!/usr/bin/env python3
"""Turn an epic's ticket graph into build lanes for /ultra-epic.

Input : a board snapshot (scripts/snapshot.py) and, optionally, a JSON map {"<ticket>": ["path", ...]}
        of the files each ticket will edit (from plan research).
Output: JSON on stdout:
  {"epic", "lanes": [{"id", "tickets", "deps", "components", "unit", "rig", "migration"}],
   "order": [lane ids, topological], "waves": [[ticket, ...], ...],
   "external": {ticket: [blockers outside the epic]}, "done": [closed children],
   "cycles": [tickets left in a dependency cycle], "cap", "rig_cap", "problems": [lane rule breaks]}

Rules:
  - edges are GitHub's native blocked-by links only; working order is sub-issue order;
  - tickets that share a file go in one lane, in order; tickets in the same component with
    no file list also share a lane (we cannot prove they are disjoint);
  - a lane depends on another when any of its tickets is blocked by one of the other's;
    lanes that would depend on each other are merged into one;
  - lane rules (v3): at most plan.max_tickets_per_lane tickets per lane (default 5), and no lane
    blocks more than plan.max_block_share of the other lanes (default 0.5). A break is listed in
    "problems"; the plan step must fix it (split by file, cut an edge) before STOP 1.
  - cap / rig_cap come from the profile's capacity block. A "rig" lane uses the shared test stack or
    a device (profile components[c].rig).

Usage: waves.py <epic> --snapshot F [--files F] [--profile .claude/delivery-profile.json]
       waves.py --selftest
"""
import argparse, json, os, sys


def topo_waves(nodes, deps):
    """Kahn layering. Returns (waves, leftover nodes in a cycle)."""
    left, waves = {n: set(deps[n]) for n in nodes}, []
    while left:
        ready = [n for n in nodes if n in left and not left[n]]
        if not ready:
            break
        waves.append(ready)
        for n in ready:
            del left[n]
        for s in left.values():
            s.difference_update(ready)
    return waves, [n for n in nodes if n in left]


def lane_problems(lanes, max_tickets=5, max_block_share=0.5):
    """The same rule as tools/sdlc/sdlc.py lane_problems (tests keep them equal)."""
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


def plan(epic, snap, files=None, profile=None):
    files, profile = files or {}, profile or {}
    comps = profile.get("components", {})
    capc = profile.get("capacity", {})
    pl = profile.get("plan", {})
    mig = (profile.get("stack", {}) or {}).get("migrations") or "migrations/"
    idx = {i["number"]: i for i in snap}
    if epic not in idx:
        sys.exit(f"epic #{epic} not in snapshot")
    subs = idx[epic]["subs"]
    nodes = [n for n in subs if n in idx and idx[n]["state"] == "OPEN"]
    done = [n for n in subs if n not in nodes]
    order = {n: k for k, n in enumerate(subs)}
    deps = {n: [b for b in idx[n].get("open_blockers", []) if b in nodes] for n in nodes}
    external = {n: [b for b in idx[n].get("open_blockers", []) if b not in nodes and b not in done] for n in nodes}
    external = {n: v for n, v in external.items() if v}
    waves, cycles = topo_waves(nodes, deps)
    comp = {n: (idx[n].get("fields") or {}).get("Component", "") for n in nodes}

    parent = {n: n for n in nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)
    owner = {}
    for n in nodes:
        fl = files.get(str(n)) or []
        for f in fl or [f"component:{comp[n]}"]:
            if f in owner:
                union(n, owner[f])
            owner[f] = n
    for n in cycles:  # a cycle cannot be split: one lane, a human looks at it
        union(n, cycles[0])

    def lane_sets():
        g = {}
        for n in nodes:
            g.setdefault(find(n), []).append(n)
        return g

    while True:  # merge lanes that depend on each other until the lane graph is a DAG
        g = lane_sets()
        ldeps = {r: {find(b) for n in ms for b in deps[n]} - {r} for r, ms in g.items()}
        lw, lcyc = topo_waves(sorted(g), ldeps)
        if not lcyc:
            break
        for r in lcyc:
            union(r, lcyc[0])

    wave_of = {n: k for k, w in enumerate(waves) for n in w}
    roots = [r for w in lw for r in w]
    ids = {r: chr(ord("A") + k) if k < 26 else f"L{k}" for k, r in enumerate(roots)}
    lanes = []
    for r in roots:
        ts = sorted(g[r], key=lambda n: (wave_of.get(n, 99), order[n]))
        cs = sorted({comp[n] for n in ts if comp[n]})
        fl = [f for n in ts for f in files.get(str(n)) or []]
        lanes.append({
            "id": ids[r], "tickets": ts, "deps": sorted(ids[d] for d in ldeps[r]), "components": cs,
            "files": sorted(set(fl)),
            "unit": [comps[c]["unit"] for c in cs if comps.get(c, {}).get("unit")],
            "rig": any(comps.get(c, {}).get("rig", True) for c in cs) if cs else True,
            "migration": any(f.startswith(mig) for f in fl),
        })
    return {"epic": epic, "lanes": lanes, "order": [ids[r] for r in roots], "waves": waves,
            "external": {str(k): v for k, v in external.items()}, "waiting": sorted(external),
            "done": done, "cycles": cycles, "cap": capc.get("cap", 6), "rig_cap": capc.get("rig_cap", 4),
            "problems": lane_problems(lanes, pl.get("max_tickets_per_lane", 5), pl.get("max_block_share", 0.5))}


def selftest():
    def t(n, comp, blockers=(), state="OPEN"):
        return {"number": n, "state": state, "subs": [], "open_blockers": list(blockers), "fields": {"Component": comp}}
    epic = {"number": 1, "state": "OPEN", "subs": [2, 3, 4, 5, 6, 7], "open_blockers": [], "fields": {}}
    snap = [epic, t(2, "Web"), t(3, "Web", [2]), t(4, "Android"), t(5, "iOS", [4]), t(6, "API", [99]), t(7, "Ops", [5])]
    prof = {"components": {"Web": {"unit": "npm test", "rig": False}, "API": {"unit": "pytest", "rig": True}}}
    r = plan(1, snap, profile=prof)
    lanes = {tuple(l["tickets"]): l for l in r["lanes"]}
    assert (2, 3) in lanes, r                       # same component, no files -> one lane, in order
    assert lanes[(2, 3)]["unit"] == ["npm test"] and lanes[(2, 3)]["rig"] is False, lanes[(2, 3)]
    assert r["waves"][0] == [2, 4, 6], r["waves"]   # 6's blocker is outside the epic
    assert r["external"] == {"6": [99]} and (r["cap"], r["rig_cap"]) == (6, 4), r
    ios, android = lanes[(5,)], lanes[(4,)]
    assert android["id"] in ios["deps"], r          # lane deps follow ticket deps
    assert r["order"].index(android["id"]) < r["order"].index(ios["id"])
    r2 = plan(1, snap, files={"2": ["web/a.tsx"], "3": ["web/b.tsx"]})
    assert any(l["tickets"] == [2] for l in r2["lanes"]) and any(l["tickets"] == [3] for l in r2["lanes"]), r2
    r3 = plan(1, snap, files={"4": ["x"], "7": ["x"], "5": ["y"]})
    assert any(set(l["tickets"]) >= {4, 5, 7} for l in r3["lanes"]), r3
    cyc = [dict(epic, subs=[2, 3]), t(2, "A", [3]), t(3, "B", [2])]
    r4 = plan(1, cyc)
    assert r4["cycles"] == [2, 3] and len(r4["lanes"]) == 1, r4
    r5 = plan(1, [dict(epic, subs=[2, 3]), t(2, "A", state="CLOSED"), t(3, "A")])
    assert r5["done"] == [2] and r5["lanes"][0]["tickets"] == [3], r5
    # v3 lane rules: six tickets in one component and no file list is one lane of six: a problem
    big = [dict(epic, subs=list(range(2, 8)))] + [t(n, "Web") for n in range(2, 8)]
    r6 = plan(1, big)
    assert any("6 tickets" in p for p in r6["problems"]), r6["problems"]
    r7 = plan(1, big, files={str(n): [f"web/{n}.tsx"] for n in range(2, 8)})
    assert r7["problems"] == [] and len(r7["lanes"]) == 6, r7
    # one root lane that every other lane waits on blocks most others
    hub = [dict(epic, subs=[2, 3, 4, 5])] + [t(2, "API")] + [t(n, f"C{n}", [2]) for n in (3, 4, 5)]
    assert any("blocks 3" in p for p in plan(1, hub)["problems"]), plan(1, hub)
    mig = plan(1, [dict(epic, subs=[2]), t(2, "API")], files={"2": ["db/migrations/1.sql"]},
               profile={"stack": {"migrations": "db/migrations/"}})
    assert mig["lanes"][0]["migration"] is True
    print("waves.py selftest: ok")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("epic", nargs="?", type=int)
    ap.add_argument("--snapshot")
    ap.add_argument("--files")
    ap.add_argument("--profile", default=os.environ.get("SDLC_PROFILE", ".claude/delivery-profile.json"))
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        sys.exit(0)
    if not (a.epic and a.snapshot):
        ap.error("epic and --snapshot are required")
    prof = json.load(open(a.profile)) if os.path.exists(a.profile) else {}
    print(json.dumps(plan(a.epic, json.load(open(a.snapshot)), json.load(open(a.files)) if a.files else None, prof), indent=1))
