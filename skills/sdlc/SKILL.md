---
name: sdlc
description: >
  Guide for moving an epic through the gated delivery pipeline
  (Refined → Planned → Building → Review → Security → Harness → Ready → Released).
  Shows where an epic is, names the next gate, records gates with tools/sdlc/sdlc.py,
  and renders the delivery board page. Use on "/sdlc", "/sdlc #N", "where is epic N",
  "next phase", "delivery board", "advance epic N".
---

# sdlc: the gate model

One epic moves through five gates. The state lives in GitHub: a gate record is a hidden-JSON comment
on the epic issue, mirrored as an `sdlc/<gate>` commit status on the epic branch head. `tools/sdlc/sdlc.py`
holds the rules. Never record a gate by hand. Never edit gate comments.

Project details (main branch, epic prefix, exempt paths, components and unit commands, live markers,
harness summary rules) come from `.claude/delivery-profile.json`. See `profile/delivery-profile.md`.

| Phase | Gate | Evidence (in `<paths.epics>/<N>-<slug>/`) | What sdlc.py checks |
|---|---|---|---|
| Refined → Planned | `plan` | `plan.md` with a `## Test plan` and an `Approved:` line | the line exists; `lanes.json` keeps the lane rules |
| Building | `unit` | none: sdlc.py runs the touched components' unit commands | each exits 0; none names a live marker; children in a ready status (optional) |
| Review | `review` | `review.md` with `Reviewed-SHA:` and `GATE: PASS` | bound to the head (or an ancestor with only exempt changes since) |
| Security | `security` | `security.md` with `Reviewed-SHA:` and `SECURITY: PASS` | bound to the head; `stack.drift_check` and `security.commands` pass |
| Harness | `harness` | the run's `summary.json` (+ `--rerun` summaries after a fix) | the summary contract: clean, on the head, local, not narrowed, contains main |
| Ready → Released | none | release notes, PR | the guard lets the merge to main through |

**Order.** Plan, then build and merge every lane, then QA once on the merged epic, then the unit gate.
Then the main branch is merged into the epic, and review, security and the harness all judge that
merged baseline, in that order. The harness is last: it is slow, so it runs once, on code review and
security have passed.

**Staleness.** A code change after a gate passed makes that gate stale, and the epic goes back to that
phase. Changes only to `paths.exempt` (default `.claude/epics/`, `.claude/handoffs/`, `docs/`) keep
gates fresh. A change to a `paths.status_only` file that touches only `status:` lines (the harness
marking scenarios green after its run) keeps gates fresh too. The plan never goes stale.

A merge of the main branch into the epic makes `unit` stale (main's code can break the epic's tests),
but not `review` or `security` for files the merge took unchanged from main. A file both the epic and
main changed makes them stale: do a delta review and a delta security check.

**A harness defect fix re-runs only the failed tests.** Fix test-first, then `unit`, a delta review and
a delta security check pass again. Then run only the failed suites on the new head and pass that
summary with `--rerun` on `advance harness`, next to the full run. The gate accepts it only when every
test the full run failed passed on head.

## Commands

| Say | Do |
|---|---|
| `/sdlc` | `python3 tools/sdlc/sdlc.py render`, publish the page if the project does, list each epic past Refined with its phase. |
| `/sdlc #N` | `python3 tools/sdlc/sdlc.py next N`: the gate table and the next step. |
| "advance #N <gate>" | Only through the phase-end protocol below. |
| `/ultra-epic #N` | The whole epic in one session with parallel lanes: `../ultra-epic/SKILL.md`. |

Other commands: `status N`, `check N` (exit 0 only when all five gates are fresh passes),
`check-harness N --evidence F` (the harness checks, nothing recorded), `gate-tags N` (the harness run's
`--tag` flags), `lint-lanes <lanes.json>` (the plan's lane rules), `selftest`.

## Phase-end protocol

1. Write the evidence doc in the epic dir. Commit everything.
2. Push the **epic branch only**. This does not deploy. Never push main here.
3. `python3 tools/sdlc/sdlc.py advance <N> <gate> --evidence <doc>` (no `--evidence` for `plan` and
   `unit`). It checks the evidence, comments on the epic, posts the `sdlc/<gate>` status and, when the
   profile has `board.set_field`, sets the Phase field. FAIL means the phase is not done: fix and run
   it again. Never edit the evidence to make it pass.
4. Report the gate result and the next step.

## Rules

- The guard hook (`tools/sdlc/guard.sh`, a PreToolUse hook on Bash) blocks a merge or push of an epic
  into the main branch until all five gates are fresh passes. It fails closed: a lookup it cannot make
  blocks. Do not work around it. If it blocks, run `sdlc.py next N` and do the phase it names.
- Never merge an epic in the GitHub web UI: the hook cannot see it. Add branch protection or a
  post-merge check on main if your plan allows it.
- Gate records count only from the repo owner, `github-actions[bot]` and the profile's `gate_authors`.
- Chores and fixes outside an epic go to main the project's usual way.
