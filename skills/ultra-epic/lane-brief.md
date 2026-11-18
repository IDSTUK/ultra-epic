# Lane brief: /ultra-epic implementer

You build ONE lane of an epic in your own git worktree. Your prompt gives: the epic number, the lane
id `X`, the lane branch, the start SHA, the tickets (or findings) in order, the epic dir, the
integrator worktree `W` and the delivery profile path. Pointers, not pasted text: read the sources.

## Start

1. `pwd` (your isolated worktree; work only there). If `git worktree list --porcelain` shows your
   lane branch checked out in an older worktree (a failed earlier run), remove that worktree first
   (`git worktree remove --force <that path>`). Then `git checkout -B <lane branch> <start SHA>` and
   `git merge-base --is-ancestor <start SHA> HEAD`. Never name a branch with the epic prefix.
2. `sh <skill dir>/scripts/lane_env.sh "$PWD" <main checkout> <W>`: it links the installs the profile
   lists (`git.links`) and the epic's test stack env file (`stack.env_file`) from `W`. It never links
   the main checkout's own env or settings files: they can hold live credentials.
3. Read: `<epic dir>/plan.md` (your lane's row, its tier, the test plan rows and state tables for your
   tickets), each ticket with `gh issue view <n> --comments`, and the project's own rules
   (CLAUDE.md, CONTRIBUTING.md).

## Per ticket: red → green → refactor

Use the `tdd` skill if it is available; the rules below win where they differ.

1. **Red.** Write the tests for every Given/When/Then at the seam plan.md names. For a ticket with a
   state table, one test per row. Run them and see them fail for the right reason. Expected values
   come from the ticket, the state table, the spec or a known-good literal, never from the code.
2. **Green.** The minimal change. Match the surrounding code. YAGNI. Stay inside the ticket's files;
   if you must go outside, say so in your lane file.
3. **Refactor** while green.
4. **Bug tickets:** revert the fix, the test must fail; restore it, the test must pass.
5. **Harness:** for each scenario id plan.md gives your ticket, write or extend the driver so the
   scenario can run, and run that suite once on the epic's stack with the profile's
   `harness.suite_command`. Never edit the scenario catalogue (`paths.scenarios`) directly: write
   `<epic dir>/lanes/<X>.scenarios.yaml` (a `scenarios:` list, entries indented two spaces); the merger
   applies it with `scripts/merge_scenarios.py`. An existing id is a patch (`id`, `drivers`, `tags`
   with `epic<N>` added, optionally `files`, `tier`; never `status`). A new id is a full entry with
   `status: missing`.
   **Deferred suites:** the shared stack does not have your new migration until the merger applies it.
   A suite that needs it cannot pass in your lane: run it to show it fails for that reason only, and
   name it in `deferred_suites`. The merger runs it after the merge.
6. Run every lane unit command your prompt lists. `unit_ok` is true only when every red case is in a
   deferred suite and fails only for the missing migration (say so per case).
7. **Commit** one per ticket: `<type>(<area>): <summary> (#n)`. `git add <paths>` only (never `-A`).
8. Record red output, green output and the revert proof (trimmed) in `<epic dir>/lanes/<X>.md`. Do not
   edit `plan.md`, `receipts.md` or `ledger.md`.
9. Comment on the issue: `gh api repos/<repo>/issues/<n>/comments -f body="Epic #<N> lane <X>: <sha>. Tests: … red → green."`
   Do not close issues. Do not set board fields (the integrator does).

**Fix lanes** (findings instead of tickets): the same loop. Each finding gets a test that fails before
the fix. Commit `fix(<area>): <finding id> <summary> (#<N>)`.

**Harness lanes:** write drivers only. At most `harness.max_full_runs_per_lane` (2) full runs of the
suite. After the last one, stop and report each failing case with your best guess at the cause. Do not
chase failures: a failing case is a finding for the orchestrator's fix round.

## Stop conditions

- A product decision you cannot make: do not build that ticket. Write
  `QUESTION: #n <question> (recommendation: …; cost if wrong: …)` in your lane file, go on.
- Already fixed: prove it with a test, commit the test, say so.
- 5 failed fix attempts on one ticket: stop that ticket, report `blocked` with the error.

## Hard rules (each one cost an outage or a lost night in an earlier run)

- Never `git stash`, `git push`, `git rebase`, or merge anything. Never touch the main checkout or `W`.
- Never test live. Every tier 1+ command sources `<worktree>/<stack.env_file>` in the same Bash call,
  and every URL in it starts with the profile's `live.local_url_prefix`. No production URL, no
  preview deploy that talks to production data. A command that matches a `live.markers` pattern is
  refused by the build workflow and the unit gate.
- Never start, stop or reset the epic's test stack: every lane shares it. Never apply a migration to
  any database: the merger applies it to the local stack after your lane merges; live gets it at
  DEPLOY. Write migrations idempotent (`IF NOT EXISTS` and the like), using the number plan.md
  reserved for you. Test one only inside a rolled-back transaction on the local stack.
- Up to `capacity.cap` agents share this machine. Run `sh <skill dir>/scripts/capacity.sh wait` before
  each build or tier 1+ run, and light side work as `sh <skill dir>/scripts/capacity.sh bg <cmd>`.
- Exclusive suites (`harness.exclusive_suites`: device and browser E2E suites) run only through
  `sh <skill dir>/scripts/suite_lock.sh <suite> <command>`. Exit 75 means it is still held: call again.
- Follow the profile's `capacity.device_rules` when it has them (simulators, emulators).
- Before any install where an install dir is a link (`node_modules`), run
  `sh <skill dir>/scripts/lane_env.sh "$PWD" <main checkout> <W> --remove`: an install through the
  link would change the main checkout's packages.
- Stop if `df -h /` shows less than 5 GB free.
- Heredocs for file writes from the shell. Subagents in the foreground. Wait with Monitor, never with
  detached `sleep` loops.
- The Bash cwd may reset between calls: start every Bash call with `cd <your worktree> &&` and use
  absolute paths for edits.

## Report (your final answer, structured)

Worktree path, branch, head SHA; per ticket: status (done / already-fixed / question / blocked),
commit, test ids, scenario ids, one-line note; the unit command results; `deferred_suites`; for a
harness lane, `suite_runs` and `failing`.
