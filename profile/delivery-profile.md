# The delivery profile

Everything project-specific lives in one JSON file in the target repo:
`.claude/delivery-profile.json` (or the path in `$SDLC_PROFILE`). The skill, the workflows, the
scripts and the gate tool all read it. Start from `delivery-profile.example.json` in this folder, or
the worked example in `examples/flying-start/`.

Check it with `python3 .claude/skills/ultra-epic/scripts/delivery_profile.py check`.

JSON has no comments, so this page is the reference. Keys starting with `_` are ignored.

## Placeholders

Command strings can use these. Unknown placeholders are left as they are.

| Placeholder | Value |
|---|---|
| `{epic}` | the epic number |
| `{wt}` | the worktree the command runs in |
| `{main}` | the main checkout |
| `{file}` | a migration file, or the snapshot output file |
| `{suite}` | one harness suite |
| `{tags}` | the harness gate run's `--tag` flags (and `--local-waivers` when the epic has waivers) |
| `{run_id}` | the harness run id |
| `{port_<name>}` | the epic's own port for `stack.ports.<name>` |

`python3 scripts/delivery_profile.py render <key> --epic N --set wt=/path` prints a rendered command.

## The hooks

These are the questions the profile answers. Each one maps to keys below.

| Question | Keys |
|---|---|
| How do I start the local test stack? | `stack.up`, `stack.check`, `stack.env_file`, `stack.ports` |
| How do I run unit tests? | `components.<name>.unit`, `unit.env` |
| How do I run the harness? | `harness.command`, `harness.suite_command`, `harness.summary` |
| How do I check capacity? | `capacity.*`, `capacity.device_rules` |
| What is live, that tests must never touch? | `live.description`, `live.markers`, `live.local_url_prefix` |
| What are the deploy steps? | `deploy.steps` |

## Keys

### Top level

| Key | Meaning |
|---|---|
| `name` | Display name, for the delivery board page. |
| `repo` | `owner/name` on GitHub. Without it, sdlc.py reads the `origin` remote. |
| `gate_authors` | Extra GitHub logins whose gate comments count (the repo owner and `github-actions[bot]` always count). |

### `git`

| Key | Default | Meaning |
|---|---|---|
| `main_branch` | `main` | The branch that deploys. The guard protects it. |
| `remote` | `origin` | The remote main lives on. |
| `epic_prefix` | `epic/` | Epic branches are `<prefix><N>-<slug>`. The guard finds epics by this. |
| `lane_prefix` | `lane/` | Lane branches are `<prefix><N>-<lane id>`. Never the epic prefix. |
| `worktree_parent` | `..` | Where the epic worktree `<repo>-e<N>` goes, relative to the main checkout. |
| `links` | `[]` | Paths `lane_env.sh` links from the main checkout into a cold worktree (installs such as `node_modules`, local SDK config). A link to an install is made only while the lockfile next to it matches. |

### `paths`

| Key | Default | Meaning |
|---|---|---|
| `epics` | `.claude/epics` | Where each epic's dir (`<N>-<slug>/`) lives: plan, lanes, evidence. |
| `exempt` | epics, handoffs, `docs/` | Regexes. A change only to matching paths keeps every gate fresh. Keep it narrow: a `.md` file the app reads at runtime is code. |
| `status_only.files` | `[]` | Files where a change to `status:` lines only keeps gates fresh (the scenario catalogue and its export). |
| `status_only.line` | `status:` lines | The regex for such a line in a diff. |
| `scenarios` | none | The harness scenario catalogue (YAML, a top-level `scenarios:` list). Lanes write fragments; `merge_scenarios.py` merges them. |
| `scenarios_export` | none | A command run after each merge of the catalogue (for example a JSON export). |

### `components`

One entry per component. A component is touched when a changed file starts with one of its `paths`.

| Key | Meaning |
|---|---|
| `paths` | Path prefixes that belong to it. |
| `unit` | The unit command. The unit gate runs it in the repo root. Tier 1+ commands source the stack env file themselves. |
| `build` | The build command, for the integrate sweep and the final sweep. |
| `harness_tags` | Harness tags the gate run must include when the component is touched. |
| `rig` | `true` when its tests use the shared stack or a device. Rig lanes count against `capacity.rig_cap`. Default `true`. |

### `unit`

| Key | Meaning |
|---|---|
| `env` | Extra environment for the unit gate's commands (for example a strict-migrations flag). |

### `live`

| Key | Meaning |
|---|---|
| `description` | One line naming the live environment. It goes into every agent prompt. |
| `markers` | Regexes. A unit command that matches one would reach live: `build.js` refuses to start, and the unit gate refuses to record. Add every flag, env var and host that can point a test at production. |
| `local_url_prefix` | Every URL in the stack env file must start with this (default `http://127.0.0.1`). |

### `stack`: one isolated test stack per epic

Each epic gets its own stack, so two epics never share a database or a functions port. A port is
`stack.ports.<name> + (epic % slots) * port_step`. `delivery_profile.py check` warns when two bases
collide for some epic.

| Key | Meaning |
|---|---|
| `env_file` | The env file `stack.up` writes in the epic worktree. Lanes get a link to it. |
| `ports` | `{name: base port}`. |
| `port_step`, `slots` | Default 10 and 20. |
| `lane_port_base` | Optional. A port per lane for a dev server a suite needs. |
| `up` | Starts the epic's stack in the epic worktree. Runs once, in preflight. |
| `down` | Stops it, after DEPLOY. |
| `check` | Exits 0 when the stack answers. |
| `free` | Optional. Exits 0 when the epic's ports are free. Without it, preflight checks the ports itself. |
| `apply_migration` | Applies one migration (`{file}`) to the local stack. Only the merger runs it. |
| `migrations` | The migrations directory prefix. |
| `drift_check` | Optional. Exits 0 when the local stack holds exactly the head's migrations. The security gate runs it before and after `security.commands`. |

### `harness`

| Key | Meaning |
|---|---|
| `command` | The full gate run. Gets `{wt}`, `{run_id}`, `{tags}`. |
| `suite_command` | One suite (`{suite}`), for lanes and re-runs. |
| `tier0_command` | Optional. The fast unit-level harness tier, for the sweeps. |
| `summary` | Where the run writes its summary (`{run_id}`). |
| `summary_must_equal` | `{key: value}` every summary (full and re-runs) must hold, for project checks such as `{"target": "local"}` or `{"extra_migrations": []}`. Default `{"target": "local"}`. |
| `max_full_runs_per_lane` | A harness lane stops after this many full suite runs and reports (default 2). |
| `exclusive_suites` | Suites that must never run twice at once on one machine (device and browser E2E). Lanes run them through `scripts/suite_lock.sh`. |

**The summary contract.** `sdlc.py` reads the harness summary JSON. Your harness runner must write:

```json
{
  "run_id": "epic14-ab12cd3",
  "git_sha": "<full sha the run tested>",
  "dirty": false,
  "main_sha": "<remote main sha when the run started>",
  "narrowed": false,
  "tags": ["api", "epic14", "e14"],
  "totals": {"tests": 120, "failures": 0, "errors": 0, "skipped": 0},
  "failed": [],
  "passed": ["suite::case", "..."],
  "suites": [{"name": "api-int", "passed": true, "failures": 0, "errors": 0}],
  "left_out": [],
  "waived": {},
  "local_waivers": null
}
```

Plus every key in `summary_must_equal`. `narrowed` is true for a run picked by suite or tier (a
re-run). `failed` must name every failure, so a re-run can clear it. `left_out` lists suites the run
could not run on the local stack; each one fails the gate unless the epic's approved
`local-waivers.json` names it and the run says so in `waived` and `local_waivers`.

### `security`

| Key | Meaning |
|---|---|
| `commands` | Commands the security gate runs; each must exit 0 (for example a row-level-security check on the local stack). |
| `checklist` | Optional. A checklist file the final workflow walks for the touched components. |
| `lenses` | `[[name, focus], ...]`: one security reviewer per lens. |

### `review`

| Key | Meaning |
|---|---|
| `lenses` | `[[name, focus], ...]`: one reviewer per lens. |
| `panel_skill` | Optional. A multi-model review skill (for example `panel-review`) run as one more seat. |
| `max_rounds` | Circuit breaker: after this many failing rounds, ask the human (default 3). |

Only regressions, high or critical severity and security findings block. Every other confirmed
finding becomes a ticket.

### `plan`

| Key | Default | Meaning |
|---|---|---|
| `max_tickets_per_lane` | 5 | The plan gate refuses a lane with more. |
| `max_block_share` | 0.5 | The plan gate refuses a lane that blocks more than this share of the other lanes. |

### `capacity`

| Key | Default | Meaning |
|---|---|---|
| `cap` | 6 | Lanes at once. |
| `rig_cap` | 4 | Rig lanes at once. |
| `min_idle_pct`, `min_free_pct` | 15, 10 | `capacity.sh wait` holds an agent until CPU idle and free memory are at least these. |
| `max_load_per_cpu` | 2.0 | Preflight fails above this load per CPU. |
| `min_disk_gb` | 15 | Preflight fails below this. |
| `min_graphql` | 2000 | Preflight fails below this GitHub GraphQL quota. |
| `device_rules` | none | One paragraph for every lane prompt about devices (simulators, emulators). |

### `board`

| Key | Meaning |
|---|---|
| `status_field` | The GitHub Project field the unit gate reads for each child (default `Status`). |
| `ready_statuses` | Child statuses that count as built (default `In Testing`, `Done`). |
| `check_children` | `true`: the unit gate refuses while an open child is not in a ready status. |
| `set_field` | Optional command `{n} {field} {value}` that writes a board field. Without it, nothing is written. |
| `snapshot` | Optional command (`{file}`, `{epic}`) that writes the board snapshot. Default: `scripts/snapshot.py`. |
| `component_label_prefix` | `scripts/snapshot.py` reads a child's component from the first label with this prefix (default `component:`). |
| `project_url`, `links` | For the delivery board page. |

### `models`

Which model does which role. Defaults: `plan` and `find` Opus (planning, finding review and security
issues), `build` and `check` Sonnet (lanes, mergers, checkers, sceptics), `sweep` Haiku (setup, test
sweeps, lint).

### `deploy`

| Key | Meaning |
|---|---|
| `steps` | The DEPLOY steps, in order, in plain words or commands. Live migrations come before the dependent code. No step tests on live. |
