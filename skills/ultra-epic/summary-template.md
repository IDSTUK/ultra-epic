# Ready-to-deploy summary (PR body + chat report)

The PR body and the chat report use the same text. Shape: the repo's PR template if it has one,
then the sections below. No preamble, short prose, the project's own domain words, the smallest
visual that makes the point. No emoji.

```markdown
Epic: #<N>
Closes #a, Closes #b, …            <!-- one "Closes #n" per child done; the # is required -->

## Summary
<one visual: call tree, component tree, diff sketch, shallow file tree with `# role` comments,
or a Mermaid sequence. One or two, never all.>
<two or three short sentences next to it>

## Evidence
| Check | Unit gate `<sha7>` | Merged baseline `<sha7>` |
|---|---|---|
| Unit (<components>) | <pass/total> | <pass/total> |
| QA (once, merged epic) | <lanes passed>/<lanes>, <k> gaps fixed | — |
| Harness `--tag …` | — | <n> tests, 0 fail, 0 skip (run `<id>`) |
| Builds (<apps>) | pass | pass |
| Security commands (<profile security.commands>) | — | pass |

**Before:** <the failing test or the old behaviour, as pseudocode or one output line>
**After:** <the same test passing / the new behaviour>

Acceptance: <k>/<k> criteria proven by a test that fails with the fix reverted (`receipts.md`).
State tables: <t> tables, <r> rows, each row a test. Harness: <before> → <after> green scenarios.

## Definition of Done
- [x] `sdlc.py status <N>`: plan ✓ unit ✓ review ✓ security ✓ harness ✓ on `<sha7>`
- [x] plan: [plan.md](…), approved by <who> <date>
- [x] unit: [receipts.md](…)
- [x] review: [review.md](…), `GATE: PASS` (lenses + panel: <seats>, <k> blocking fixed, <t> tickets raised, <r> refuted)
- [x] security: [security.md](…), `SECURITY: PASS`
- [x] harness: [harness.md](…), on the merged baseline, 0 failures, 0 errors, 0 skips
- [x] Release notes

## Merge Danger
**Door:** <one-way (migration applied to live at DEPLOY, data rewrite) | two-way (revert the merge)>
**Blast radius:** <one word>
<what can break, for whom, and the rollback>

## Release
On DEPLOY: <the profile's deploy steps that apply, in one line each>.
Migrations DEPLOY applies to live (one transaction each, no test on live):
<each file; mark "NOT APPROVED at STOP 1: approve?" on each one not in plan.md's `Approved:` line, or "none">

## For the human
- Device checks (step by step): …
- Decisions made for you (Rulings): …
- Follow-up tickets raised (non-blocking review findings, QA notes): …
```

Chat report after the PR body: one line "Main is merged into the epic at `<sha7>` and every gate
passed on that merged code. Main is not changed. Say DEPLOY to push." and the PR link.
