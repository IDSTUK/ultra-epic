# Worked example: Flying Start

ultra-epic was built on one real product: a sailing race-timing system with an iOS race officer app,
an Android sailor app, a React admin site, a Supabase backend and serverless functions. This folder
keeps what only made sense there, so the core stays generic.

| File | What it shows |
|---|---|
| `delivery-profile.json` | The whole profile for a mobile + web + Postgres project: four components, a local Supabase stack with three ports per epic, live markers for the hosted project, simulator rules, the deploy steps. The repo name and the database container name are placeholders. |
| `capacity_sim.sh` | Simulator capacity on one Mac: one `simctl boot` at a time across all agents (parallel boots silently do nothing), a cap on booted simulators, never `shutdown all`. The profile points lanes at it through `capacity.device_rules`. |
| `serve_web_app.sh` | Serve a Vite app from a worktree for a browser suite, on the epic's stack only, on one port only (`--strictPort`), with its own pid file and cache dir. Generalised: the app dir, env file and functions URL are settings. |

## What the project had that the example does not ship

These were project tools, not part of ultra-epic. The profile still names them, so you can see where
your own tools plug in:

- `tools/localstack/up.sh`, `functions.sh`, `apply.sh`: start the local Supabase stack and functions,
  apply one migration and record it. Your `stack.up` and `stack.apply_migration`.
- `tests/harness/run.py`: the harness runner. It wrote the summary contract `sdlc.py` reads
  (`profile/delivery-profile.md`), refused a tier 1+ run off the local stack, and recorded which
  migrations the local database held (`missing_migrations`, `extra_migrations`). Your `harness.command`.
- `tools/rls_gate.py --local`: a row-level-security check on the local stack. One of `security.commands`.
- A private backlog skill with `board.py` (board snapshot, field writes). The generic route is
  `scripts/snapshot.py` and `board.set_field`.

## Numbers from the two runs

| | Epic 10 (v1) | Epic 14 (v2) |
|---|---|---|
| Tickets | 15 | 48 |
| Models | all Opus | mostly Sonnet (Opus plans and finds) |
| Tokens | 3.52 bn | 2.47 bn |
| Tokens per ticket | about 235 m | about 51 m |

Epic 14 came to roughly 22 to 30 US dollars per ticket at API prices. The low price per token
suggests most of those tokens were cached context that many agents read again.
