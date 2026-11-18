---
name: ultra-epic
description: >
  Deliver a whole Ready epic in one orchestrator session with parallel agents, through every SDLC gate
  in order: preflight → plan (STOP 1: the human approves) → build and merge every lane → QA once on the
  merged epic → unit gate → main merged in → review → security → harness last → a PR with evidence
  (STOP 2: the human types DEPLOY). Works out ticket dependencies into build lanes, builds each lane
  test-first in its own git worktree, and records every gate against a commit with tools/sdlc/sdlc.py.
  Use on "/ultra-epic #N", "ultra epic N", "build epic N end to end", and on "DEPLOY" after an
  /ultra-epic summary.
---

# ultra-epic

The main session is the **orchestrator**. It runs four workflow scripts from this skill
(`workflows/*.js`, through the Workflow tool with `scriptPath` and `args`), records every gate itself
with `tools/sdlc/sdlc.py`, and owns the two human stops. Workflows do the work; the orchestrator does
git state, gates and reports. Read `../sdlc/SKILL.md` once for the gate model.

Needs the Workflow tool. Without it, say so and offer the one-session-per-phase route in the sdlc skill.

## The delivery profile

Everything project-specific lives in `.claude/delivery-profile.json` in the target repo
(`profile/delivery-profile.md` in this repo documents every key). Read it once at the start and pass
it whole as `args.profile` to every workflow. The profile says: the components and their unit and build
commands, what "live" is and how to spot a command that would reach it, how to start the epic's own
test stack, how to run the harness, the capacity limits, and the deploy steps. If a step below needs a
profile key that is missing, stop and ask; never guess a command.

## Fixed names

| Thing | Value |
|---|---|
| Skill dir `S` | the directory of this file |
| Main checkout `M` | the human's checkout. Only step 8 (DEPLOY) changes it. |
| Epic worktree `W` | `<git.worktree_parent>/<repo name>-e<N>` on `<epic_prefix><N>-<slug>`: the only place gates are recorded |
| Epic dir `E` | `W/<paths.epics>/<N>-<slug>` |
| Run state | `E/ultra.json`: `{head, pr, merged_lanes, review_round, review_head, reviewed_head, final_round, final_head, base_main, applied_local}`. Commit each change (exempt path). |
| Test stack | ONE isolated stack per epic (database, functions and any other ports from `stack.ports`, shifted by the epic number: `scripts/delivery_profile.py ports <N>`), owned by `W`. Lanes reach it through `lane_env.sh`'s link to `W/<stack.env_file>`. |
| Tags | what `cd W && python3 tools/sdlc/sdlc.py gate-tags <N>` prints: the touched components' harness tags, `epic<N>` and `e<N>`. |

Every Bash call: `cd W && …` (or `git -C`). Before every `sdlc.py advance`, `git status --porcelain`
must be empty. `advance unit` and `advance security` can run long: start them with
`run_in_background` and wait with Monitor.

Workflow `args` always include `{epic, slug, repo, wt: W, main: M, epicDir: E, skillDir: S, profile}`.
`build.js` also needs `head` (`git -C W rev-parse HEAD`) and `cap` (from `E/lanes.json`), and
`rigCap`; it refuses to start without them.

## Resume

`/ultra-epic #N` starts with `cd W && python3 tools/sdlc/sdlc.py next N` and reads `E/ultra.json`,
then goes to the first step that is not done. A build that stopped part way runs again with only the
lanes not yet merged, and `merged: ultra.json merged_lanes`. A workflow that stopped in this session
resumes with `resumeFromRunId`.

## 0. Preflight (fail closed; report each failure, then stop)

`python3 S/scripts/preflight.py N --main M`. It checks, and prints PASS or FAIL for each:

- the profile is valid;
- machine load below `capacity.max_load_per_cpu` × CPUs, and free disk at least `capacity.min_disk_gb`;
- `M` is on the main branch and main equals the remote main after a fetch (ahead 0, behind 0).
  Otherwise ask the human: the run builds on the remote main and DEPLOY fast-forwards it;
- at most one `<epic_prefix><N>-*` branch (a second one breaks every gate and the guard);
- the issue has the label `epic` and at least one sub-issue;
- the epic's test stack is free (its ports, or `stack.free`);
- the GitHub GraphQL quota is at least `capacity.min_graphql`.

Then:

- `W`: if `git -C M worktree list --porcelain` shows the epic branch, use that path and its slug.
  Else `git -C M worktree add W -b <epic_prefix><N>-<slug> <remote>/<main>` (slug = epic title in kebab
  case, 3–4 words). Copy the profile into `W` if it is not tracked.
- Test stack: `cd W && <stack.up rendered for N>` (`scripts/delivery_profile.py render stack.up --epic N --set wt=W`).
  Then `stack.check` must pass, and every URL in `W/<stack.env_file>` must start with
  `live.local_url_prefix`. A lane never starts, stops or resets the stack.
- `python3 S/scripts/waves.py --selftest && python3 tools/sdlc/sdlc.py selftest`.

## 1. Plan → gate `plan` → **STOP 1 (the human approves)**

1. Workflow `S/workflows/plan.js`, args + `{tickets: [open child numbers], snapshot: <scratch>/snap-<N>.json}`.
   It refines every ticket in parallel to Definition of Ready, writes `plan.md`, `files.json` and
   `lanes.json`, checks the lane rules with `sdlc.py lint-lanes`, and three critics attack the plan.
   Read `E/plan.md` and `E/lanes.json`. If `missingCritics` is not empty, resume the workflow before
   you show it.
2. **Definition of Ready** (the workflow enforces it; `unready` lists each failure):
   - every acceptance criterion is Given/When/Then and names the test that will prove it;
   - dependencies are GitHub blocked-by links;
   - a ticket that changes timing or state has a **state table** that answers every combination of
     its conditions. No table, not Ready. Ask the human for the missing answers; never invent them.
3. **Lane rules** (`laneRules.ok` must be true): a lane holds at most `plan.max_tickets_per_lane`
   tickets (default 5), and no lane blocks more than `plan.max_block_share` of the other lanes
   (default half). One long lane that blocks most others turns the parallel build into a queue.
   The plan gate refuses a `lanes.json` that breaks them.
4. Show the human: the lane graph (one small table or Mermaid), the cap, each lane's test tier, the
   test plan size, migrations, external blockers, cycles, device checks, any `unready` tickets, the
   **Decisions** list (numbered, a recommendation on each), and this authorisation question:

   > Approve the plan and an ultra-epic run: one session, no stops until the summary; every test on
   > the epic's local stack; apply migrations <list or none> to live at DEPLOY, before the push; merge
   > onto main locally and run review, security and the harness there; no push until you say DEPLOY.

   Then **stop**. Do not continue on silence.

   **Local waivers.** A harness suite that cannot run on the local stack yet is a recorded gap, never
   a silent pass: list each one at STOP 1 with an open ticket to port it. On the human's yes, write
   `E/local-waivers.json` = `{"approved": "<who> <date> (chat, STOP 1)", "suites": {"<suite>": <issue>}}`
   and commit it before the `Approved:` line. `sdlc.py` binds the waivers to the commit that writes
   that line, so a changed list needs a new yes and a new `Approved:` line.
5. On the human's yes (in chat, this session): apply the answers to the decisions, then append the last
   line of `plan.md`:
   `Approved: <who> <YYYY-MM-DD> (chat): ultra-epic run; live migrations at DEPLOY: <list|none>; local merge onto main; push only on DEPLOY`.
   Commit `plan: epic #N approved`, push the epic branch (an epic branch push does not deploy),
   `sdlc.py advance N plan`. Never write an `Approved:` line without the human's yes in chat.

From here to step 7 there are no stops except the ones in **Stop rules**.

## 2. Build, merge, QA once → gate `unit`

1. Workflow `S/workflows/build.js`, args + `{head, cap, rigCap, lanes: lanes.json lanes,
   ticketLane: {ticket: lane id}, integrate: true}`. The order inside it:
   - **Build and merge every lane first.** A frontier scheduler starts a lane from the current epic
     head as soon as every lane it depends on has merged: up to `cap` at once, of which up to
     `rigCap` use the shared stack or a device. Each lane: implementer in its own worktree
     (`lane-brief.md`, test first), up to 2 repair rounds while its unit commands are red, then the
     single merger. **A lane merges when its unit tests pass and the merge is clean.** The merger
     tests again after the merge, applies the lane's new migrations to the epic's local stack
     (`stack.apply_migration`), never to live, and never resets after an apply.
   - **QA once, on the merged epic.** After every lane merged: an acceptance checker and a spec and
     quality reviewer per lane, in parallel. Each gets its lane's scope (tickets, files, components)
     and the plan's test tier for that lane, so a lane fails only for work it owns. A gap in another
     lane's work goes to that lane. QA gaps never unmerge a lane: each owning lane gets one QA fix
     lane on the merged head. What is still short comes back in `failed` with `state: "qa"`.
   - **Harness lanes last.** Only after every feature lane merged: one lane per group of plan
     scenarios that still lack a driver. A harness lane stops after
     `harness.max_full_runs_per_lane` (2) full suite runs and reports; it does not chase failures.
     A failing case it reports is a finding for a fix round, not for the harness lane.
   - **Integrate.** The remote main merged in, the full unit and build sweep, receipts and ledger,
     a completeness critic.
   The harness drivers are **written** here, before the review, so the review sees the tests too.
   They **run** in step 5, last, on the merged baseline.
2. Store `merged_lanes` (the result's `mergedLanes`), add the result's `appliedLocal` to `ultra.json`
   `applied_local` (never replace the list), and `base_main` (`baseMain`). Then:
   - `failed` entries with `state` `failed` or `skipped`, an open question, or `critic.complete == false`:
     apply **Stop rules**;
   - `state: "qa"` or `"harness"`: a **Fix round** (they do not stop the run);
   - `qaNotes` (gaps with no merged owner): a ticket each with the human's yes, or a fix lane.
3. Push the epic branch. First time: `gh pr create --draft --base <main> --head <epic branch>` with
   `Epic: #N` and one `Closes #n` line per child; store `pr`.
4. `sdlc.py advance N unit` (background + Monitor). It runs the touched components' unit commands
   itself and refuses one that names a `live.markers` pattern. FAIL → read `E/unit.log`, a **Fix round**,
   repeat.

## 3. Merge main into the epic branch (the merged baseline)

Runs before the review, and again before step 5 only when main moved since.

The guard blocks every merge of the epic into main until all five gates pass, so the merge goes the
other way: the remote main into the epic branch. The epic head is then exactly the code that ships.

1. `git -C W fetch`; store `base_main` = `git -C W rev-parse <remote>/<main>`.
2. If main is not an ancestor of HEAD: `git -C W merge <remote>/<main>` (default message), resolve
   conflicts test-first, commit, push. Apply each migration the merge added to the epic's local stack
   in number order with `stack.apply_migration`.
3. `sdlc.py next N`. A main merge makes `unit` stale: run `advance N unit` again. `review` and
   `security` stay fresh for files the merge took unchanged from main. A file both the epic and main
   changed makes them stale: then the review runs as a delta review.

## 4. Review → gate `review`

1. Freeze `H` = epic head. Workflow `S/workflows/review.js`, args + `{head: H, round, focus: plan.md
   Review focus, previous: last reviewed head or null}`: the profile's review lenses and, when
   `review.panel_skill` is set, a cross-model panel seat, in parallel; three read-only sceptics per
   finding (2 refutations kill it; a dead sceptic is not a refutation); `E/review[-rK].md` bound to `H`.
2. **What blocks.** Only regressions, high or critical severity, and security findings block. Every
   other confirmed finding is in `followUps`: raise a ticket for each (whole-board check first) and
   list them in the summary. They do not hold the epic.
3. `pass` → push, `sdlc.py advance N review --evidence <review path>`. Store `reviewed_head` = `H`.
4. Not `pass` → **Fix rounds**, then step 2.4 (unit) and step 4.1 with `round + 1` and `previous: H`.
5. **Circuit breaker.** `askHuman: true` (the last allowed round, `review.max_rounds`, failed, or the
   round is past it): stop and ask the human, with the blocking findings and a recommendation each.

## 5. Security, then the harness last → gates `security`, `harness`

1. `H` = epic head (the merged baseline). Workflow `S/workflows/final.js`, args + `{head: H, round,
   tags, components, previous: last final head or null}`. On a detached worktree at `H`, in this order:
   - the unit and build sweep of the touched components, the security lenses, the checklist
     (`security.checklist`) and the profile's `security.commands`, in parallel; three sceptics per
     security finding;
   - **only when the sweep and security passed**: the harness gate run (`harness.command`), judged by
     `sdlc.py check-harness N` (the gate's own check, nothing recorded). A red security round costs
     no harness hour;
   - status flips only when that check passed; `security.md`; `harness.md`.
2. `pass` → push, then in this order, with no code commit between them:
   - `sdlc.py advance N security --evidence E/security[-rK].md` (background + Monitor)
   - `sdlc.py advance N harness --evidence E/harness-summary[-rK].json` (the last gate)
   Confirmed `lows`: tickets, as in step 4.2. Go to step 6.
3. `setupFailed` → no fix round: no code is at fault. Repair what the note names (the tags, the env
   file, the stack). Then step 5.1 again with the same `round`. The same note twice: stop and report.
4. Not `pass` and no `setupFailed` → **Fix rounds**, then step 2.4 (unit), step 4.1 as a delta
   review of the fixes, and step 5.1 with `round + 1`.

## Fix rounds

Group the findings and failing cases by component into fix lanes `{id: "F<U|Q|R|S|H><round>k<k>",
findings, deps: [], unit, components}` (U = unit, Q = QA, R = review, S = security, H = harness;
`unit` = the component's unit command) and run `build.js` with `{head, cap, rigCap, lanes,
integrate: true, label: "fix-<phase>-r<round>"}`. Fix lanes skip the QA pass (their findings are the
checks). Store `base_main` and add `appliedLocal` as in step 2.2. Every fix is code: the unit gate and
the review go stale, and the delta review covers only the fix diff.

## 6. Release docs (exempt paths only, so the gates stay fresh)

- Release notes from the children's "So that…" lines and the fixed bugs' symptoms, plain English, in
  `E/release-notes.md`.
- `ledger.md`: Rulings, estimate against actual, and a 3-bullet retro.
- Commit, push, `sdlc.py check N` must exit 0.

## 7. PR summary → **STOP 2 (the human types DEPLOY)**

- Migrations DEPLOY will apply: `git -C W diff --diff-filter=A --name-only base_main HEAD -- <stack.migrations>`.
  Mark each one not in plan.md's `Approved:` line (a fix lane can add one).
- Write the PR body from `summary-template.md`. `gh pr edit <pr> --body-file …`, `gh pr ready <pr>`.
- `sdlc.py render` for the delivery board page, if the project publishes one.
- Remove `W-h` (`lane_env.sh <W-h> M W --remove`, then `git worktree remove --force`).
- Report: the summary, the PR link, the migrations DEPLOY will apply to live (the unapproved ones
  marked, with "approve these too?"), and "Main is merged into the epic at `<sha7>` and every gate
  passed on that merged code. Main is not changed. Say DEPLOY to push." Stop.

## 8. DEPLOY (only on the human's word "DEPLOY", in chat)

1. `git -C M fetch`. The remote main must still equal `base_main`; if it moved, go back to step 3.
   `M` must be on main; fast-forward it to `base_main` first if it is behind.
2. `git -C M merge --no-ff <epic branch>` with the DEFAULT message (the guard finds epic merges by
   the branch name and checks all five gates here). Then `git -C M diff --quiet <epic branch> HEAD`
   must succeed: the merge commit holds exactly the tested tree. If not, `git -C M reset --keep
   base_main` (your own unpushed merge) and go back to step 3.
3. The profile's `deploy.steps`, in order. Migrations to live come before the dependent code, each
   approved one in one transaction, verified with a read. No test, probe or test insert ever runs on
   live. A red step after a live apply: stop and report the live state; never reset past it.
4. `git -C M push <remote> <main>` (the guard checks the gates once more), when the steps put it here.
5. `sdlc.py check N`, close the epic with a summary comment (shipped, deferred, harness growth).
   `cd W && <stack.down>`, remove `W`, delete the merged lane branches.
6. Report: deploy states, and any step that stays with the human (store submissions and the like).

## Stop rules (the only stops between STOP 1 and STOP 2)

- A product decision, an open question, or a ticket that cannot be built: batch the questions with a
  recommendation each and stop. Do not drop a child from the epic without the human's yes.
- **Circuit breaker:** the review's `askHuman`, final round `review.max_rounds + 1`, the same lane
  failing twice after repairs, the final setup failing twice with the same note, a merge conflict the
  merger could not resolve, or an integrate sweep still red.
- A migration applied to the local stack and then a red step (a `failed` entry with a non-empty
  `applied_local`): stop and report the lane and its files; never reset past it.
- Secrets, settings, hook installs, anything outward-facing.
- Disk below 5 GB, a test stack outage, or GraphQL quota below 500: pause, report, and wait.
- Context near 70%: commit, update `ultra.json`, write a handoff naming `/ultra-epic #N`.

## Never

- Write `Approved:` without the human's yes; edit evidence to make a gate pass; record gates by hand.
- Push main, run `gh pr merge`, change `M`, or merge in the GitHub UI before DEPLOY.
- Run the harness gate on code that does not contain the remote main.
- `git stash`; `git add -A`; an install through a linked `node_modules` (run `lane_env.sh --remove` first).
- Name a lane with the epic prefix; let a lane edit the scenario catalogue directly; raise `cap` or
  `rigCap` above the profile's (build.js clamps them).
- Run two lanes on one exclusive suite at once: `scripts/suite_lock.sh` is the only way to run one.
- Run any test, probe or harness suite against live. Every tier 1+ command sources the epic's env file
  in the same Bash call. Start, stop or reset the epic's stack while a lane uses it.
- Run two epics on one machine at once (today's limit: the capacity guard is per machine, not per epic).
