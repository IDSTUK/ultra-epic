# Changelog

## v3: generic release (2026-11-18)

The first public version. Everything project-specific moved out of the core.

- **Delivery profile.** One JSON file (`.claude/delivery-profile.json`) holds the components, unit and
  build commands, what live is and how to spot a command that reaches it, the test stack, the harness,
  capacity limits and the deploy steps. `scripts/delivery_profile.py` checks and renders it.
- **Gate tool made generic.** `tools/sdlc/sdlc.py` keeps the five gates, evidence files, staleness on
  code change, the merged-baseline rules, local waivers and the guard hook. Main branch, epic prefix,
  exempt paths, live markers and the harness summary checks come from the profile. Standard library
  only.
- **v2 lessons built in:**
  - build and merge every lane first, then QA once on the merged epic, then security, then the
    harness last; QA gaps never unmerge a lane;
  - QA checkers get the lane's scope and test tier, so a lane fails only for work it owns;
  - lanes of at most 5 tickets, and no lane may block most others (`sdlc.py lint-lanes`, the plan gate);
  - harness lanes start only after every feature lane merged, and stop after 2 full suite runs;
  - only regressions, high-severity and security findings block a review; the rest become tickets;
    a circuit breaker asks the human after `review.max_rounds`;
  - Definition of Ready: a ticket that changes timing or state needs a full state table;
  - one suite lock (`scripts/suite_lock.sh`) and one isolated test stack per epic;
  - preflight (`scripts/preflight.py`): machine load, main equals the remote main, stack free, API quota.
- **New scripts:** `preflight.py`, `snapshot.py` (board snapshot from GitHub sub-issues and blocked-by
  links), `suite_lock.sh`, `delivery_profile.py`.
- **Moved to `examples/flying-start/`:** the project's profile, the simulator capacity script and the
  web-app server script (generalised).
- Plugin manifest, MIT licence, tests for the workflows, the gate tool and the scripts.

## v2: Epic 14 (48 tickets, mostly Sonnet, 2.47 bn tokens)

Run on the same product as v1. The skill as it stood after Epic 14 had, among others:

- Every test on a local stack; nothing tests live. Migrations reach live only at DEPLOY, from the
  approved list.
- Review, security and the harness judge the merged baseline (main merged into the epic). The harness
  became the last gate. A main merge keeps review and security fresh for files taken unchanged from main.
- Builders and checkers on Sonnet; Opus for planning and finding.
- A frontier scheduler with a cap of 6 lanes, 4 of them on the shared stack or a device, and a
  capacity guard that makes agents wait when the machine is full.
- Three read-only sceptics per review and security finding; two refutations kill it.
- The final workflow asks the gate tool itself (`sdlc.py check-harness`) for the harness verdict, and
  flips scenario statuses only after it passes.
- Lanes write scenario fragments; one merger applies them (the catalogue conflicted in almost every
  lane merge before).
- Local waivers: a suite that cannot run locally yet is a recorded gap with an open ticket, approved
  with the plan.

## v1: Epic 10 (15 tickets, all Opus, 3.52 bn tokens)

The first run of one orchestrator session over a whole epic.

- A plan with a lane graph, lanes built test-first in git worktrees, one merger.
- Gates recorded against commits by `sdlc.py`; the guard hook blocks an epic reaching main until every
  gate is a fresh pass.
- Two human stops: the plan, and DEPLOY.
