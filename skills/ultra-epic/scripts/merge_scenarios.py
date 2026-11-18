#!/usr/bin/env python3
"""Merge lane scenario fragments into the harness scenario catalogue (entry level).

The catalogue is a YAML file with a top-level `scenarios:` list (profile paths.scenarios; default
tests/harness/scenarios.yaml). Lanes never edit it: it conflicted in almost every lane merge of the
first run. Each lane writes <epic dir>/lanes/<X>.scenarios.yaml with a `scenarios:` list:
  - an entry with a NEW id is appended as text, unchanged (full entry required);
  - an entry with an EXISTING id is a patch: it may set drivers, tags, files, tier,
    out_of_scope (for example the driver of a plan stub), and an assertion only when the row
    has none and no other patch in the same call fills it. It may not replace an assertion or
    change status: status flips belong to the harness phase (profile paths.status_only keeps
    gates fresh for them). Only that one entry is re-written; the rest of the file keeps its bytes.
Duplicate new ids, bad YAML, or entries the regex and the YAML parser count differently fail.

Needs PyYAML (pip install pyyaml). The gate tool (tools/sdlc) does not.

Usage: merge_scenarios.py [--file <catalogue, relative to the repo root>] <repo root> <fragment.yaml>...
       merge_scenarios.py --selftest
"""
import os, re, sys, tempfile
import yaml

ENTRY = re.compile(r'(?m)^  - id:\s*"?([^"\n]+)"?\s*$')
PATCHABLE = {"id", "drivers", "tags", "files", "tier", "out_of_scope"}
REQUIRED = {"id", "component", "section", "title", "tier", "status"}


def blocks(text):
    """(id, start, end) of each entry under `scenarios:`, as offsets into text."""
    base = text.index("scenarios:") if "scenarios:" in text else 0
    marks = list(ENTRY.finditer(text, base))
    return [(m.group(1).strip(), m.start(), marks[k + 1].start() if k + 1 < len(marks) else len(text.rstrip("\n")) + 1)
            for k, m in enumerate(marks)]


def dump_entry(e):
    out = yaml.safe_dump([e], sort_keys=False, default_flow_style=None, allow_unicode=True, width=10000)
    return "".join("  " + line + "\n" for line in out.splitlines())


CATALOGUE = "tests/harness/scenarios.yaml"


def merge(root, frags, catalogue=CATALOGUE):
    path = os.path.join(root, catalogue)
    text = open(path).read()
    have = {s["id"]: s for s in yaml.safe_load(text)["scenarios"]}
    add, patches, errs, new = [], {}, [], set()
    for f in frags:
        ftext = open(f).read()
        parsed = (yaml.safe_load(ftext) or {}).get("scenarios") or []
        fb = blocks(ftext)
        if len(parsed) != len(fb):
            errs.append(f"{f}: {len(parsed)} YAML entries but {len(fb)} '  - id:' lines (indent entries by two spaces)")
            continue
        for e, (sid, a, b) in zip(parsed, fb):
            if sid in have:
                has = have[sid].get("assertion") or patches.get(sid, {}).get("assertion")  # an earlier patch in this call
                fill = {"assertion"} if not has and str(e.get("assertion") or "").strip() else set()
                bad = set(e) - PATCHABLE - fill
                if bad:
                    errs.append(f"{f}: patch for {sid} may set only {sorted(PATCHABLE - {'id'})}"
                                f" (and an assertion when the row has none and no other patch fills it), not {sorted(bad)}")
                patches.setdefault(sid, {}).update(e)
            elif sid in new:
                errs.append(f"{f}: id {sid} is added twice")
            else:
                miss = REQUIRED - set(e)
                if miss:
                    errs.append(f"{f}: new id {sid} lacks {sorted(miss)}")
                new.add(sid)
                add.append(ftext[a:b].rstrip() + "\n")
    if errs:
        sys.exit("\n".join(errs))
    for sid, a, b in reversed(blocks(text)):  # back to front, so offsets stay valid
        if sid in patches:
            text = text[:a] + dump_entry({**have[sid], **patches[sid]}) + text[b:]
    out = text.rstrip("\n") + "\n" + "".join(add)
    after = {s["id"]: s for s in yaml.safe_load(out)["scenarios"]}
    for sid in have:
        if after[sid].get("status") != have[sid].get("status"):
            sys.exit(f"internal error: status of {sid} changed")
    open(path, "w").write(out)
    return len(add), len(patches)


def selftest():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "tests/harness"))
    p = os.path.join(d, "tests/harness/scenarios.yaml")
    base = ('scenarios:\n  - id: "a.one"\n    component: "x"\n    section: "s"\n    title: "t"\n    tier: 0\n    status: green\n'
            '  - id: "a.stub"\n    component: "x"\n    section: "s"\n    title: "stub"\n    tier: 1\n    status: missing\n'
            '    assertion: "Given a, Then b"\n')
    open(p, "w").write(base)
    fa = os.path.join(d, "A.yaml")
    open(fa, "w").write('scenarios:\n  - id: "b.two"\n    component: "x"\n    section: "s"\n    title: "t2"\n    tier: 0\n    status: missing\n'
                        '  - id: "a.stub"\n    drivers:\n      - mode: "unit"\n        suite: "unit-core"\n    tags: ["x", "epic9"]\n')
    assert merge(d, [fa]) == (1, 1)
    got = {s["id"]: s for s in yaml.safe_load(open(p))["scenarios"]}
    assert list(got) == ["a.one", "a.stub", "b.two"], got
    assert got["a.stub"]["drivers"][0]["suite"] == "unit-core" and got["a.stub"]["status"] == "missing"
    assert got["a.stub"]["assertion"] == "Given a, Then b" and "epic9" in got["a.stub"]["tags"]
    assert open(p).read().startswith(base.split("  - id: \"a.stub\"")[0]), "untouched entries keep their bytes"
    # A row with no assertion gets one from a patch; a row that has one keeps it.
    for sid in ("a.bare", "a.none"):
        open(p, "a").write(f'  - id: "{sid}"\n    component: "x"\n    section: "s"\n    title: "bare"\n    tier: 2\n    status: missing\n')
    open(fa, "w").write('scenarios:\n  - id: "a.bare"\n    assertion: "Given c, When d, Then e"\n')
    assert merge(d, [fa]) == (0, 1)
    got = {s["id"]: s for s in yaml.safe_load(open(p))["scenarios"]}
    assert got["a.bare"]["assertion"] == "Given c, When d, Then e" and got["a.bare"]["status"] == "missing", got["a.bare"]
    for bad in ['scenarios:\n  - id: "b.two"\n    component: "x"\n    section: "s"\n    title: "t"\n    tier: 0\n    status: green\n',  # duplicate new
                'scenarios:\n  - id: "a.stub"\n    assertion: "Given x, Then y"\n',  # an assertion is never replaced
                'scenarios:\n  - id: "a.none"\n    assertion: " "\n',            # nor filled with nothing
                'scenarios:\n  - id: "a.stub"\n    status: green\n',            # status flip in a patch
                'scenarios:\n- id: "c"\n  component: "x"\n',                     # wrong indent
                'scenarios:\n  - id: "c.new"\n    title: "no component"\n']:      # incomplete new entry
        open(fa, "w").write(bad)
        try:
            merge(d, [fa])
            raise AssertionError(f"must fail: {bad!r}")
        except SystemExit:
            pass
    # Two patches in one call (two fragments, or one fragment with the id twice) may not both fill one
    # empty row; the second one would silently replace the first, as a second call already refuses.
    fb = os.path.join(d, "B.yaml")
    open(fb, "w").write('scenarios:\n  - id: "a.none"\n    assertion: "Given lane B, Then b"\n')
    twice = 'scenarios:\n  - id: "a.none"\n    assertion: "Given lane A, Then a"\n  - id: "a.none"\n    assertion: "Given lane B, Then b"\n'
    for frags in (["A", fb], [twice]):
        open(fa, "w").write('scenarios:\n  - id: "a.none"\n    assertion: "Given lane A, Then a"\n' if frags[0] == "A" else frags[0])
        before = open(p).read()
        try:
            merge(d, [fa] + frags[1:])
            raise AssertionError(f"must fail: two fills of a.none in one call ({len(frags)} fragment(s))")
        except SystemExit:
            pass
        assert open(p).read() == before, "a refused merge writes nothing"
    print("merge_scenarios.py selftest: ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        selftest()
    elif len(sys.argv) >= 3:
        argv, cat = sys.argv[1:], CATALOGUE
        if argv[0] == "--file":
            cat, argv = argv[1], argv[2:]
        n_add, n_patch = merge(argv[0], argv[1:], cat)
        print(f"{n_add} scenario(s) appended, {n_patch} patched")
    else:
        sys.exit(__doc__)
