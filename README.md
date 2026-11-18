# ultra-epic

An attempt to automate the software development life cycle.

One Claude Code session, the orchestrator, takes a Ready epic and drives it through every gate in
order. It plans, builds in parallel, checks its own work, reviews it with several models, runs
security and the full test harness, and opens a pull request with the evidence. A human answers the
plan's decisions at the first stop and types `DEPLOY` at the second. Nothing reaches main before that.

It is not magic. It needs well-written tickets, a local test stack, and someone who reads the plan.
It ran on two real epics (15 and 48 tickets) before this release. This is the third version, made
generic.

## How a run goes

```
preflight ─► STOP 1: plan ─► build lanes ─► QA once ─► unit gate ─► main merged in
                 ▲                                                         │
             the human                                                     ▼
             approves                         review ─► security ─► harness (last)
                                                                           │
             the human            STOP 2: PR with evidence ◄───────────────┘
             types DEPLOY ──────► deploy steps, push to main
```

| Step | What happens |
|---|---|
| Preflight | Machine load, free disk, main equals the remote main, one epic branch, the epic's test stack free, GitHub API quota. Fails closed. |
| STOP 1: plan | One agent per ticket refines it to Definition of Ready, in parallel. Tickets become lanes from their files and their GitHub blocked-by links. Three critics attack the plan. The human gets a lane graph and numbered decisions, each with a recommendation, and approves. |
| Build | One lane per git worktree, test first, started as soon as the lanes it depends on have merged. A lane merges when its unit tests pass and the merge is clean. |
| QA | Once, on the merged epic. An acceptance checker and a spec reviewer per lane, each scoped to the lane's own tickets and test tier. Gaps go to the lane that owns them. |
| Unit gate | The gate tool runs every touched component's unit command itself. |
| Review | Main merged into the epic first. Six review lenses and, optionally, a cross-model panel, in parallel. Three read-only sceptics try to refute each finding. Only regressions, high-severity and security findings block; the rest become tickets. |
| Security, then harness | On the merged baseline, local stack only, never live. Security first. The harness runs last, and only when security passed. |
| STOP 2 | A PR with the evidence table. The human types `DEPLOY`. |
| Deploy | The profile's deploy steps: approved migrations to live, then the push to main. |

## The gates

Five gates: `plan`, `unit`, `review`, `security`, `harness`. `tools/sdlc/sdlc.py` records each one as
a hidden-JSON comment on the epic issue and as an `sdlc/<gate>` commit status, against the commit it
judged. Any code change after a pass makes that gate stale, so the epic drops back to that phase.
Changes to docs and the epic's own evidence do not.

`tools/sdlc/guard.sh` is a PreToolUse hook. It blocks any merge or push of an epic into main until all
five gates are fresh passes. It reads `git merge`, `git push`, `gh pr merge` and `gh api` merge calls,
with the bypass forms the earlier runs found (`git -C`, `HEAD:refs/heads/main`, a temp branch carrying
an epic merge). It fails closed: a lookup it cannot make blocks the command.

## Agent roles and models

| Role | Model |
|---|---|
| Plan drafting, review and security finders | Opus |
| Ticket refinement, lane implementers, mergers, QA checkers, sceptics | Sonnet |
| Setup, test sweeps, lint | Haiku |

The profile's `models` block changes this.

## Requirements

- Claude Code with the **Workflow tool** (the workflows use its script API: `agent()`, `parallel()`,
  `phase()`, `log()` and `export const meta`).
- The `gh` CLI, signed in, with access to the repo's issues, sub-issues and dependencies.
- git with worktrees.
- Python 3 (standard library only for the gate tool; `merge_scenarios.py` needs PyYAML if you use a
  scenario catalogue).
- A local test stack you can start per epic, and a harness runner that writes the summary contract.
- Optional: a multi-model review skill such as [panel-review](https://github.com/IDSTUK/panel-review),
  named in the profile's `review.panel_skill`.
- Node, only to run this repo's workflow tests.

## Install

In the target repo:

```sh
mkdir -p .claude/skills tools
cp -R <this repo>/skills/ultra-epic .claude/skills/ultra-epic
cp -R <this repo>/skills/sdlc .claude/skills/sdlc
cp -R <this repo>/tools/sdlc tools/sdlc
cp <this repo>/profile/delivery-profile.example.json .claude/delivery-profile.json
```

Edit `.claude/delivery-profile.json` (see below), then check it:

```sh
python3 .claude/skills/ultra-epic/scripts/delivery_profile.py check
python3 tools/sdlc/sdlc.py selftest
```

Register the guard hook in `.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [{ "type": "command", "command": "\"$CLAUDE_PROJECT_DIR\"/tools/sdlc/guard.sh", "timeout": 30 }]
      }
    ]
  }
}
```

The repo also has a `.claude-plugin/` manifest. A plugin install gives you the two skills; you still
copy `tools/sdlc` into the project and register the hook, because the gates run the project's own copy.

Then, in a Claude Code session: `/ultra-epic #<epic number>`.

## The delivery profile

Everything project-specific lives in `.claude/delivery-profile.json`. The core never names a database,
a device, a port or a path. The profile answers six questions:

| Question | Keys |
|---|---|
| How do I start the local test stack? | `stack.up`, `stack.check`, `stack.env_file`, `stack.ports` |
| How do I run unit tests? | `components.<name>.unit` |
| How do I run the harness? | `harness.command`, `harness.suite_command` |
| How do I check capacity? | `capacity.*`, `capacity.device_rules` |
| What is live, that tests must never touch? | `live.description`, `live.markers` |
| What are the deploy steps? | `deploy.steps` |

Every key, and the harness summary contract, is in [profile/delivery-profile.md](profile/delivery-profile.md).
A full worked example for a mobile, web and Postgres product is in
[examples/flying-start/](examples/flying-start/).

## What a Ready epic looks like

- An issue labelled `epic` with its tickets as GitHub sub-issues.
- Each ticket INVEST-sized, with acceptance criteria as Given/When/Then, each naming the test that
  will prove it.
- Dependencies as GitHub blocked-by links, not prose.
- A ticket that changes timing or state carries a state table that answers every combination of its
  conditions.

The plan step refines tickets, but it cannot invent product decisions. Thin tickets come back as
questions at STOP 1.

## Cost guidance from real runs

| | Epic 10 (v1) | Epic 14 (v2) |
|---|---|---|
| Tickets | 15 | 48 |
| Models | all Opus | mostly Sonnet |
| Tokens | 3.52 bn | 2.47 bn |
| Tokens per ticket | about 235 m | about 51 m |
| API-equivalent cost | roughly 90 to 110 US dollars per ticket | roughly 22 to 30 US dollars per ticket |

Tokens per ticket fell by about four fifths between the runs; v2 moved builders and checkers to
Sonnet and kept Opus for planning and finding. At the Epic 14 rate, a 40-ticket epic costs around a
thousand dollars at API prices. A subscription plan changes the sums but not the
volume: plan for long runs and the rate limits that come with them.

## What changed since v2

| Lesson from the second run | v3 | Where |
|---|---|---|
| QA before each merge held lanes back for gaps that were not theirs. | Build and merge every lane first (a lane merges when its unit tests pass and the merge is clean), then QA once on the merged epic, then security, then the harness last. QA gaps never unmerge a lane. | code: `build.js`, `final.js` |
| Checkers failed lanes for work another lane owned, or for tests above the lane's tier. | Each QA checker gets its lane's tickets, files, components and the plan's test tier. A gap in another lane's work goes to that lane. | code: `build.js` |
| One long lane blocked most others and turned the build into a queue. | Lanes of at most 5 tickets; a plan where one lane blocks more than half the others is refused. | code: `waves.py`, `plan.js`, `sdlc.py lint-lanes` and the plan gate |
| Harness lanes started before the features they test were in, and kept chasing failures. | Harness lanes start only after every feature lane merged, and stop after 2 full suite runs to report. | code: `build.js` (start rule, report); prompt: the run limit |
| Medium and low review findings blocked the gate and forced extra rounds. | Only regressions, high-severity and security findings block; the rest become tickets. A circuit breaker asks the human after `review.max_rounds`. | code: `review.js` |
| Timing and state tickets were built against half-specified behaviour. | Definition of Ready: a ticket that changes timing or state needs a state table that answers every combination. No table, not Ready. | code: `plan.js` |
| Two lanes could run the same simulator or E2E suite at once. One shared stack let one tree's migrations show up in another's runs. | One suite lock for exclusive suites. One isolated test stack (database and functions ports) per epic. | code: `suite_lock.sh`, `delivery_profile.py ports`; prompt: lanes must use the lock |
| Runs started on a busy machine, a stale main, a taken stack or a nearly spent API quota. | Preflight checks machine load, main equals the remote main, the test stack is free, and the API quota. | code: `preflight.py` |

"Prompt" means the rule is in the agent's instructions and the code cannot force it. The suite lock,
for example, works only if each lane calls it.

## Known limits

- **v3 has not run on a real epic yet.** v1 and v2 ran on real epics; this generic version is covered by
  its tests (stubbed agents, a stubbed `gh`), not yet by a production run. Expect rough edges in your profile.
- **One epic per machine today.** Each epic has its own stack, but the capacity guard and the suite
  lock are per machine, and two orchestrators would each think they own it.
- **It needs Ready tickets.** Vague tickets come back as questions. That is the plan step doing its
  job, but it means the run waits on you.
- **Attention is still needed.** The two stops are real decisions. Between them, a circuit breaker or a
  migration that went red stops the run and waits.
- **The workflows' prompts are long.** They carry rules learned the hard way. Trim them for your
  project, but read the test that guards a rule before you remove it.
- **The guard sees only Bash commands in Claude Code.** A merge in the GitHub web UI bypasses it. Add
  branch protection where your plan allows.
- **The harness summary contract is yours to meet.** `sdlc.py` judges the summary; your runner must
  write it honestly (local target, not narrowed, not dirty, every failure named).

## Tests

```sh
python3 -m unittest discover -s tests -v
```

The workflow tests run each workflow under Node with stubbed agents. The gate tool has its own
`python3 tools/sdlc/sdlc.py selftest` (118 checks), which the tests also run.

## Layout

```
skills/ultra-epic/          the orchestrator skill
  SKILL.md                  the run, step by step
  lane-brief.md             what every lane implementer reads
  summary-template.md       the PR body
  workflows/                plan.js, build.js, review.js, final.js (Workflow tool scripts)
  scripts/                  preflight, waves, snapshot, merge_scenarios, delivery_profile,
                            capacity, suite_lock, lane_env
skills/sdlc/SKILL.md        the gate model
tools/sdlc/                 sdlc.py (the gate tool), guard.sh (the hook), page.html (board page)
profile/                    the profile reference and an example
examples/flying-start/      the worked example the tool was built on
tests/                      unittest suites
```

## Licence

MIT. See [LICENSE](LICENSE).
