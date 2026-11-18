export const meta = {
  name: 'ultra-epic-build',
  description: 'Build epic lanes test-first in parallel worktrees as dependencies allow, merge each lane once its unit tests pass and the merge is clean, then QA the merged epic once, write missing harness drivers, and integrate',
  phases: [
    { title: 'Build', detail: 'one implementer per lane, own worktree, test-first' },
    { title: 'Merge', detail: 'serial merger: clean merge + unit tests after the merge' },
    { title: 'QA', detail: 'once, on the merged epic: acceptance + spec checks per lane, scoped to the lane and its tier' },
    { title: 'Harness', detail: 'after every feature lane merged: drivers for plan scenarios still without one' },
    { title: 'Integrate', detail: 'main merged in, full unit and build sweep, completeness critic' },
  ],
}
// args: {epic, slug, repo, wt, main, epicDir, skillDir, profile, head, cap, rigCap?,
//        lanes:[{id, tickets, deps, unit (list or string), components, files?, tier?, rig?, findings?}],
//        merged?: [lane ids merged in an earlier run], ticketLane?: {ticket: laneId}, integrate: bool, label?: 'build'|'fix-r1'...,
//        qa?: bool (default true for feature builds, false for fix lanes)}
const A = args
if (!A.head || !A.cap || !A.wt || !A.epicDir || !A.skillDir || !A.profile) throw new Error('build.js needs args.head, cap, wt, epicDir, skillDir and profile')
const PR = A.profile
const get = (k, d) => { const v = k.split('.').reduce((o, x) => (o && typeof o === 'object') ? o[x] : undefined, PR); return v === undefined || v === null ? d : v }
const MODEL = r => get(`models.${r}`, { plan: 'opus', find: 'opus', build: 'sonnet', check: 'sonnet', sweep: 'haiku' }[r])
const units = l => [].concat(l.unit || [])   // the profile gives one string, waves.py a list
// Refuse a unit command that would test live before any agent starts: the profile's live.markers.
const LIVE_MARKERS = get('live.markers', []).map(m => new RegExp(m))
const liveUnits = A.lanes.flatMap(l => units(l).filter(c => LIVE_MARKERS.some(m => m.test(c))).map(c => `${l.id}: ${c}`))
if (liveUnits.length) throw new Error(`build.js refuses lane unit commands that would test LIVE (profile live.markers): ${liveUnits.join(' | ')}`)
const CAP = Math.min(A.cap, get('capacity.cap', 6))
const RIG_CAP = Math.min(A.rigCap || get('capacity.rig_cap', 4), get('capacity.rig_cap', 4), CAP)
const isRig = l => l.rig !== false
const TAG = A.label || 'build'
const QA = A.qa !== undefined ? A.qa : !A.lanes.some(l => l.findings)
const BRIEF = `${A.skillDir}/lane-brief.md`
const EPIC_BRANCH = `${get('git.epic_prefix', 'epic/')}${A.epic}-${A.slug}`
const LANE_PREFIX = get('git.lane_prefix', 'lane/')
const ENV = get('stack.env_file', '.test-stack/test.env')
const MAX_RUNS = get('harness.max_full_runs_per_lane', 2)
const EXCLUSIVE = get('harness.exclusive_suites', [])
const MIG = get('stack.migrations', '')
const APPLY = get('stack.apply_migration', '')
const ports = () => { const st = PR.stack || {}; const step = st.port_step || 10, slots = st.slots || 20
  return Object.fromEntries(Object.entries(st.ports || {}).map(([k, b]) => [k, b + (A.epic % slots) * step])) }
const render = (tpl, v = {}) => String(tpl).replace(/\{(\w+)\}/g, (m, k) => k in v ? String(v[k]) : k === 'epic' ? String(A.epic)
  : (k.startsWith('port_') && k.slice(5) in ports()) ? String(ports()[k.slice(5)]) : m)

const CWD = 'The Bash cwd may reset between calls: start every Bash call with `cd <your dir> &&` or use `git -C <dir>`, and use absolute paths ' +
  `for every file edit.${A.main ? ` Never run a git command that changes the main checkout ${A.main}.` : ''}`
const LOCAL = `Never test against the live environment (${get('live.description', 'production')}). Every tier 1+ command runs on the epic's own test ` +
  `stack, with \`. <worktree>/${ENV} &&\` in the same Bash call (lane_env.sh links it from the epic worktree; every URL in it must start with ` +
  `${get('live.local_url_prefix', 'http://127.0.0.1')}). This epic's stack ports: ${JSON.stringify(ports())}. Never start, stop or reset the stack: ` +
  'every lane shares the epic\'s one stack, and only the merger applies migrations to it.'
const LOCKS = EXCLUSIVE.length ? `Exclusive suites (${EXCLUSIVE.join(', ')}) run only through the suite lock, so two lanes never run one at the same ` +
  `time: \`sh ${A.skillDir}/scripts/suite_lock.sh <suite> <command>\` (exit 75 = still held after 9 minutes: call it again). ` : ''
const CAPACITY = `Other agents run in parallel on this machine. Run \`sh ${A.skillDir}/scripts/capacity.sh wait\` before each build or tier 1+ test ` +
  `run, and run light side work (lint, drift checks, log scans) as \`sh ${A.skillDir}/scripts/capacity.sh bg <cmd>\`. ${LOCKS}` +
  (get('capacity.device_rules') ? get('capacity.device_rules') + ' ' : '') + 'Use your own run id: other agents use the same stack.'
const CTX = `Epic #${A.epic} in ${A.repo}. Integrator worktree W (${EPIC_BRANCH}): ${A.wt}. Epic dir: ${A.epicDir}. Skill dir: ${A.skillDir}. ` +
  `Delivery profile: ${A.wt}/.claude/delivery-profile.json. ${CWD} ${LOCAL} ${CAPACITY}`
const LANE = {
  type: 'object',
  properties: {
    worktree: { type: 'string' }, branch: { type: 'string' }, head: { type: 'string' },
    items: { type: 'array', items: { type: 'object', properties: {
      ref: { type: 'string' }, status: { type: 'string', enum: ['done', 'already-fixed', 'question', 'blocked'] },
      commit: { type: 'string' }, tests: { type: 'array', items: { type: 'string' } },
      scenarios: { type: 'array', items: { type: 'string' } }, note: { type: 'string' } },
      required: ['ref', 'status'] } },
    unit_ok: { type: 'boolean' }, unit_summary: { type: 'string' },
    // suites that need this lane's own migration: the shared stack has it only after the merge
    deferred_suites: { type: 'array', items: { type: 'string' } },
    suite_runs: { type: 'integer' }, failing: { type: 'array', items: { type: 'string' } },
  },
  required: ['worktree', 'branch', 'head', 'items', 'unit_ok'],
}
// owned: false = a gap in work another lane (or no lane) owns; it never fails this lane (v3)
const VERDICT = {
  type: 'object',
  properties: { pass: { type: 'boolean' }, gaps: { type: 'array', items: { type: 'object', properties: {
    ref: { type: 'string' }, problem: { type: 'string' }, severity: { type: 'string', enum: ['critical', 'important', 'minor'] },
    owned: { type: 'boolean' }, owner_ticket: { type: 'integer' } },
    required: ['ref', 'problem', 'severity', 'owned'] } } },
  required: ['pass', 'gaps'],
}
const MERGED = {
  type: 'object',
  properties: { ok: { type: 'boolean' }, head: { type: 'string' }, note: { type: 'string' },
    applied_local: { type: 'array', items: { type: 'string' } } },
  required: ['ok', 'head', 'note'],
}

let head = A.head
let chain = Promise.resolve()
const serial = fn => { const p = chain.then(fn); chain = p.catch(() => {}); return p }
const PORT_BASE = get('stack.lane_port_base', null)
const portOf = {}  // each lane keeps one port across its repair rounds; QA fix lanes get the next free ones
const lanePort = l => portOf[l.id] !== undefined ? portOf[l.id] : (portOf[l.id] = PORT_BASE + Object.keys(portOf).length)
const PORT_LINE = l => PORT_BASE ? `Your lane port: ${lanePort(l)} (for a dev server a suite needs; a taken port is an error, never another port).` : ''

const SAFE_REMOVE = wt => `Remove the lane worktree ONLY if \`${wt}\` is not ${A.wt}${A.main ? ` and not ${A.main}` : ''}: ` +
  `\`sh ${A.skillDir}/scripts/lane_env.sh ${wt} ${A.main || A.wt} ${A.wt} --remove; git -C ${A.wt} worktree remove --force ${wt}\`.`

const MERGE_RULES = `You are the only merger. Every git command: \`git -C ${A.wt}\`. First check \`git -C ${A.wt} branch --show-current\` is ` +
  `${EPIC_BRANCH} and that no merge is in progress (\`git -C ${A.wt} rev-parse -q --verify MERGE_HEAD\` prints nothing). ` +
  'Merge with the DEFAULT message (no -m). If the merge does not apply cleanly and you cannot resolve the conflict test-first: `git merge --abort`, ' +
  'check `git status --porcelain -uno` is empty, return ok=false.'
const SCEN = get('paths.scenarios', '')
const scenarioStep = id => SCEN ? `If ${A.epicDir}/lanes/${id}.scenarios.yaml exists: \`cd ${A.wt} && python3 ${A.skillDir}/scripts/merge_scenarios.py ` +
  `--file ${SCEN} ${A.wt} ${A.epicDir}/lanes/${id}.scenarios.yaml\`${get('paths.scenarios_export') ? ` && ${get('paths.scenarios_export')}` : ''}, ` +
  `then commit those files ("chore(harness): epic #${A.epic} lane ${id} scenarios").` : 'No scenario catalogue in the profile: skip this step.'

function work(l) {
  return l.findings
    ? `Fix lane ${l.id}: fix these CONFIRMED findings test-first (each needs a test that fails before the fix):\n${JSON.stringify(l.findings, null, 1)}`
    : `Build lane ${l.id}: tickets ${l.tickets.map(n => '#' + n).join(', ')}, in this order.`
}

async function mergeLane(l, r) {
  return serial(() => agent(`${CTX}\n${MERGE_RULES}\n` +
    `1. \`git -C ${A.wt} merge --no-ff ${r.branch}\`. Store PRE = the head before the merge.\n` +
    `2. ${scenarioStep(l.id)}\n` +
    `3. Test AFTER the merge: run ${JSON.stringify(units(l))} in ${A.wt} (the parts that need this lane's new migration run after step 4). On red: one ` +
    'test-first fix commit if the merge caused it; else `git reset --keep PRE` (your own unpushed commits) and return ok=false with the error.\n' +
    (MIG && APPLY
      ? `4. New migrations = \`git -C ${A.wt} diff --diff-filter=A --name-only PRE HEAD -- ${MIG}\`. Apply them to the epic's LOCAL stack only, never ` +
        `live (live gets them at DEPLOY), in number order: \`cd ${A.wt} && ${render(APPLY, { file: '<file>', wt: A.wt })}\`, and verify each with a read. ` +
        'List every file applied in applied_local. After ANY local apply never reset (the stack keeps the migration): fix forward test-first, or ' +
        'stop and return ok=false with applied_local so the orchestrator can report it. Then run the lane\'s DB-dependent unit commands.\n'
      : '4. No migrations in the profile: skip this step.\n') +
    `5. Run each suite the lane deferred because it needs its own migration (${JSON.stringify(r.deferred_suites || [])}), each with ` +
    `\`${render(get('harness.suite_command', '<the profile harness.suite_command>'), { wt: A.wt, suite: '<s>' })}\`${EXCLUSIVE.length ? ' (exclusive suites through the suite lock)' : ''}. ` +
    'Each must pass. On red when step 4 applied nothing: as in step 3 (`git reset --keep PRE`). On red after a step 4 apply: never reset; ' +
    'fix forward test-first, or return ok=false with applied_local.\n' +
    `6. ${SAFE_REMOVE(r.worktree)} Keep the lane branch. Return the epic head SHA.`,
    { label: `merge:${l.id}`, phase: 'Merge', model: MODEL('build'), schema: MERGED }))
}

// A lane merges when its unit tests pass and the merge is clean (v3). QA comes once, after every lane merged.
async function runLane(l, tag = TAG) {
  const base = head
  let r = await agent(`${CTX}\nRead and follow ${BRIEF}.\n${work(l)}\nStart SHA: ${base}. Lane id: ${l.id}. Lane branch: ${LANE_PREFIX}${A.epic}-${l.id}. ` +
    `Lane unit commands: ${JSON.stringify(units(l))}. ${PORT_LINE(l)}`,
    { label: `${tag}:${l.id}`, phase: 'Build', model: MODEL('build'), isolation: 'worktree', schema: LANE })
  if (!r) return { id: l.id, merged: false, why: 'implementer returned nothing' }
  for (let round = 1; !r.unit_ok; round++) {
    if (round > 2) return { id: l.id, merged: false, why: 'lane unit commands still red after 2 repair rounds', report: r }
    log(`lane ${l.id}: unit red; repair round ${round}`)
    r = await agent(`${CTX}\nRead and follow ${BRIEF}. Continue lane ${l.id} in the EXISTING worktree ${r.worktree} on ${r.branch} ` +
      `(every Bash call starts with \`cd ${r.worktree} &&\`; do not create a branch). The lane unit commands ${JSON.stringify(units(l))} are red: ` +
      `${r.unit_summary || 'see your last run'}. Fix the cause test-first, then run them again. ${PORT_LINE(l)}`,
      { label: `repair:${l.id}#${round}`, phase: 'Build', model: MODEL('build'), schema: LANE }) || r
  }
  const m = await mergeLane(l, r)
  if (m && m.ok) head = m.head
  return { id: l.id, merged: !!(m && m.ok), why: m ? m.note : 'merger returned nothing', applied_local: (m && m.applied_local) || [], report: r }
}

// frontier scheduler: a lane starts as soon as every lane it depends on is merged (CAP at once, RIG_CAP of them rig)
phase('Build')
const state = {}, results = {}, running = new Map()
const known = new Set(A.lanes.map(l => l.id))
const byId = Object.fromEntries(A.lanes.map(l => [l.id, l]))
const rigRunning = () => [...running.keys()].filter(id => isRig(byId[id])).length
for (const id of A.merged || []) state[id] = 'merged'
for (const l of A.lanes) for (const d of l.deps || []) {
  if (!known.has(d) && state[d] !== 'merged') throw new Error(`lane ${l.id} depends on ${d}, which is neither in this run nor in args.merged`)
}
for (;;) {
  for (const l of A.lanes) {
    if (state[l.id] || running.has(l.id)) continue
    if ((l.deps || []).some(d => state[d] === 'failed' || state[d] === 'skipped')) {
      state[l.id] = 'skipped'
      log(`lane ${l.id} skipped: a lane it depends on failed`)
      continue
    }
    if (running.size >= CAP) break
    if (isRig(l) && rigRunning() >= RIG_CAP) continue  // a later non-rig lane may still start
    if ((l.deps || []).every(d => state[d] === 'merged')) {
      log(`lane ${l.id} starts from ${head.slice(0, 8)}`)
      running.set(l.id, runLane(l).catch(e => ({ id: l.id, merged: false, why: String(e) })))
    }
  }
  if (!running.size) break
  const res = await Promise.race(running.values())
  running.delete(res.id)
  results[res.id] = res
  state[res.id] = res.merged ? 'merged' : 'failed'
  log(`lane ${res.id}: ${state[res.id]}${res.merged ? '' : ' (' + res.why + ')'}`)
}
const failed = A.lanes.filter(l => state[l.id] !== 'merged')
  .map(l => ({ id: l.id, state: state[l.id] || 'unscheduled', why: (results[l.id] && results[l.id].why) || 'never started',
    applied_local: (results[l.id] && results[l.id].applied_local) || [], report: results[l.id] && results[l.id].report }))
const mergedLanes = [...new Set([...(A.merged || []), ...A.lanes.filter(l => state[l.id] === 'merged').map(l => l.id)])]
const appliedLocal = Object.values(results).flatMap(r => r.applied_local || [])

// QA once on the merged epic (v3). Each checker gets its lane's scope and the plan's test tier, so a lane fails only for work it owns.
phase('QA')
const qaGaps = {}
const qaNotes = []
if (QA) {
  const merged = A.lanes.filter(l => state[l.id] === 'merged')
  const scope = l => `Lane ${l.id} owns ONLY: tickets ${(l.tickets || []).map(n => '#' + n).join(', ') || '(fix lane)'}; components ${JSON.stringify(l.components || [])}; ` +
    `files ${JSON.stringify(l.files || [])}. Plan test tier for this lane: ${l.tier === undefined ? 'see plan.md ## Lanes' : l.tier} (judge tests up to this tier; a ` +
    'missing higher-tier test is not this lane\'s gap). Ticket to lane map for the epic: ' + JSON.stringify(A.ticketLane || {}) + '. ' +
    'A gap in work another lane owns, or no lane owns: report it with owned=false and owner_ticket; it does not fail this lane.'
  const checks = await parallel(merged.map(l => () => parallel([
    () => agent(`${CTX}\nQA on the merged epic head ${head} in ${A.wt}, read-only (edit nothing; prove by reading code and running tests). ${scope(l)}\n` +
      'You are an adversarial acceptance checker; default to pass=false when unsure. For each ticket in this lane (read `gh issue view <n>` yourself), ' +
      'list each Given/When/Then, find the test that proves it, and check its expected values come from the ticket, not the code. For a bug ticket, ' +
      `revert proof in a scratch worktree (\`git -C ${A.wt} worktree add --detach <tmp> ${head}\`; reverse-apply only the non-test part of the fix; the test ` +
      'must FAIL; remove the scratch worktree). A criterion in this lane without a proving test at the lane\'s tier is a critical gap.',
      { label: `qa-accept:${l.id}`, phase: 'QA', model: MODEL('check'), schema: VERDICT }),
    () => agent(`${CTX}\nQA on the merged epic head ${head} in ${A.wt}, read-only. ${scope(l)}\nReview the lane's diff ` +
      `(\`git -C ${A.wt} log --merges --format=%H --grep '${LANE_PREFIX}${A.epic}-${l.id}'\` finds its merge; diff its first parent against it) against the ticket ` +
      'bodies only (no session history). Two verdicts in one list: spec compliance (does it do what was asked, nothing more) and quality (the project ' +
      'rules in CLAUDE.md or CONTRIBUTING.md, YAGNI, matches surrounding code, no secrets, migrations idempotent). Critical and important gaps only.',
      { label: `qa-review:${l.id}`, phase: 'QA', model: MODEL('check'), schema: VERDICT }),
  ]).then(vs => ({ l, vs }))))
  for (const { l, vs } of checks) {
    // a dead checker is a gap of this lane, never a pass
    const all = vs.map((v, i) => v || { pass: false, gaps: [{ ref: l.id, problem: `${i ? 'review' : 'acceptance'} checker returned nothing`, severity: 'critical', owned: true }] })
      .flatMap(v => v.gaps).filter(g => g.severity !== 'minor')
    for (const g of all) {
      const owner = g.owned === false ? (A.ticketLane || {})[g.owner_ticket] : l.id
      if (owner && byId[owner] && state[owner] === 'merged') (qaGaps[owner] = qaGaps[owner] || []).push(g)
      else qaNotes.push({ from: l.id, ...g })  // no merged owner in this run: the orchestrator raises a ticket or a fix lane
    }
  }
  // one QA fix round per lane that owns gaps, on the merged head; a lane that is still short is reported, not unmerged
  const fixLanes = Object.entries(qaGaps).map(([id, gaps]) => ({ id: `Q${id}`, findings: gaps, deps: [], unit: units(byId[id]),
    components: byId[id].components, files: byId[id].files }))
  if (fixLanes.length) log(`QA: ${fixLanes.length} lane(s) own gaps: ${fixLanes.map(f => f.id).join(', ')}`)
  for (const res of await parallel(fixLanes.slice(0, CAP).map(f => () => runLane(f, 'qa-fix').catch(e => ({ id: f.id, merged: false, why: String(e) }))))) {
    if (res.merged) appliedLocal.push(...(res.applied_local || []))
    else failed.push({ id: res.id, state: 'qa', why: res.why, gaps: qaGaps[res.id.slice(1)], applied_local: res.applied_local || [] })
  }
  for (const f of fixLanes.slice(CAP)) failed.push({ id: f.id, state: 'qa', why: 'more QA fix lanes than cap: run them as a fix round', gaps: f.findings })
}
if (!A.integrate) return { head, mergedLanes, failed, appliedLocal, qaNotes, results }

// Harness lanes start only after every feature lane merged (v3), and stop after MAX_RUNS full suite runs and report.
phase('Harness')
const GAPS = { type: 'object', properties: { groups: { type: 'array', items: { type: 'object', properties: {
  name: { type: 'string' }, scenarios: { type: 'array', items: { type: 'string' } }, suite: { type: 'string' } },
  required: ['name', 'scenarios'] } } }, required: ['groups'] }
const featureOpen = failed.filter(f => f.state !== 'qa').length
let groups = []
if (featureOpen) log(`harness lanes not started: ${featureOpen} feature lane(s) did not merge`)
else {
  const hg = await agent(`${CTX}\nRead-only. In ${A.wt}: list every scenario id named in ${A.epicDir}/plan.md's test plan. For each, find its ` +
    `driver${SCEN ? ` (${SCEN}: drivers[].suite)` : ''} and check the harness runner knows that suite and the scenario has the tag epic${A.epic}. ` +
    'Group the ids that still lack a driver by the suite or driver file that should hold them (one group = one file to write).',
    { label: 'harness-gaps', phase: 'Harness', model: MODEL('sweep'), schema: GAPS })
  if (!hg) failed.push({ id: 'harness-gaps', state: 'failed', why: 'harness gap finder returned nothing' })
  groups = ((hg && hg.groups) || []).map((g, i) => ({ ...g, id: `H${i + 1}${TAG === 'build' ? '' : TAG.replace(/\W/g, '')}` }))
}
let nextGroup = 0
const harnessReports = []
const runGroup = async g => {
  const r = await agent(`${CTX}\nRead and follow ${BRIEF} (harness lane). ` +
    `Harness lane ${g.id}: write the driver(s) for scenarios ${g.scenarios.join(', ')}${g.suite ? ` in suite ${g.suite}` : ''} and register them with the ` +
    `harness runner. Run the suite on the epic's stack with \`${render(get('harness.suite_command', '<profile harness.suite_command>'), { wt: '<your worktree>', suite: '<suite>' })}\`. ` +
    `HARD LIMIT: at most ${MAX_RUNS} full runs of the suite. After run ${MAX_RUNS}, stop: do not chase failures. Report suite_runs, unit_ok (green on the ` +
    'last run) and each failing case in failing, with your best guess at the cause. A failing case that is a product defect is a finding for the ' +
    `orchestrator, not yours to fix here. Start SHA: ${head}. Lane id: ${g.id}.`,
    { label: `harness:${g.id}`, phase: 'Harness', model: MODEL('build'), isolation: 'worktree', schema: LANE })
  if (r) harnessReports.push({ id: g.id, suite_runs: r.suite_runs, failing: r.failing || [] })
  if (r && (r.suite_runs || 0) > MAX_RUNS) log(`harness lane ${g.id} ran the suite ${r.suite_runs} times (limit ${MAX_RUNS})`)
  if (!r || !r.unit_ok) return { id: g.id, ok: false, why: r ? `driver not green after ${r.suite_runs || '?'} run(s): ${(r.failing || []).join(', ')}` : 'harness lane returned nothing' }
  const m = await serial(() => agent(`${CTX}\n${MERGE_RULES}\n\`git -C ${A.wt} merge --no-ff ${r.branch}\` (a conflict in the runner's suite list: keep both entries). ` +
    `Then ${scenarioStep(g.id)} On red: \`git reset --keep <head before the merge>\` and return ok=false. ${SAFE_REMOVE(r.worktree)} Return the head.`,
    { label: `merge:${g.id}`, phase: 'Harness', model: MODEL('build'), schema: MERGED }))
  if (m && m.ok) head = m.head
  return { id: g.id, ok: !!(m && m.ok), why: m ? m.note : 'merger returned nothing' }
}
await parallel(Array.from({ length: Math.min(RIG_CAP, groups.length) }, () => async () => {
  while (nextGroup < groups.length) {
    const g = groups[nextGroup++]
    log(`harness group ${g.id} starts`)
    const o = await runGroup(g).catch(e => ({ id: g.id, ok: false, why: String(e) }))
    if (!o.ok) failed.push({ id: g.id, state: 'harness', why: o.why })
  }
}))

phase('Integrate')
const INT = { type: 'object', properties: {
  head: { type: 'string' }, green: { type: 'boolean' }, base_main: { type: 'string' },
  results: { type: 'array', items: { type: 'object', properties: { cmd: { type: 'string' }, exit: { type: 'integer' }, counts: { type: 'string' } },
    required: ['cmd', 'exit'] } },
  children: { type: 'array', items: { type: 'object', properties: { n: { type: 'integer' }, status: { type: 'string' } }, required: ['n', 'status'] } },
  questions: { type: 'array', items: { type: 'string' } } }, required: ['head', 'green', 'base_main', 'results', 'children', 'questions'] }
const MAIN_REF = `${get('git.remote', 'origin')}/${get('git.main_branch', 'main')}`
const SET = get('board.set_field', null)
let integ = null
for (let round = 0; round < 3; round++) {
  const next = await agent(`${CTX}\nEvery command: \`cd ${A.wt} && …\`. Check the branch is ${EPIC_BRANCH}. ` +
    `${round ? `Round ${round}: the last sweep was red: ${JSON.stringify(integ && integ.results.filter(x => x.exit))}. Fix each cause test-first (one commit per fix), then sweep again.\n` : ''}` +
    `1. \`git fetch ${get('git.remote', 'origin')}\` and, if ${MAIN_REF} is not an ancestor of HEAD, \`git merge ${MAIN_REF}\` (default message); resolve conflicts test-first. ` +
    `base_main = \`git rev-parse ${MAIN_REF}\`. ` +
    (MIG && APPLY ? `Apply each migration the merge added (\`git diff --diff-filter=A --name-only <head before the merge> HEAD -- ${MIG}\`) to the epic's LOCAL stack in number order with \`${render(APPLY, { file: '<file>', wt: A.wt })}\`. ` : '') +
    '\n2. Sweep on this head: every touched component\'s unit and build command from the profile (touched = paths in ' +
    `\`git diff --name-only ${MAIN_REF}...HEAD\`)${get('harness.tier0_command') ? `, and \`${get('harness.tier0_command')}\`` : ''}. Commands that run longer than 10 minutes: run_in_background and wait with Monitor.\n` +
    `3. Consolidate ${A.epicDir}/lanes/*.md into receipts.md (| When | SHA | Gate | Command | Exit | Counts |), every Ruling and question into ledger.md, ` +
    'and every device-only check into plan.md "## Device checks".\n' +
    (SET ? `4. For each child of epic #${A.epic} built with a passing test: \`${SET.replace('{n}', '<n>').replace('{field}', get('board.status_field', 'Status')).replace('{value}', `"${get('board.ready_statuses', ['In Testing'])[0]}"`)}\` (do not hide errors). Children with an open question stay as they are.\n`
      : '4. No board.set_field in the profile: list each child and whether it is built with a passing test.\n') +
    '5. Commit. `git status --porcelain` must be empty. Return the head SHA, base_main, every command with exit code and counts, each child\'s status, and the open questions.',
    { label: `integrate#${round}`, phase: 'Integrate', model: MODEL('build'), schema: INT })
  if (!next) break
  integ = next
  head = integ.head
  if (integ.green) break
}
if (!integ || !integ.green) failed.push({ id: 'integrate', state: 'failed', why: integ ? 'sweep still red after 3 rounds' : 'integrator returned nothing' })
const CRITIC = { type: 'object', properties: { complete: { type: 'boolean' }, missing: { type: 'array', items: { type: 'string' } } },
  required: ['complete', 'missing'] }
const critic = await agent(`${CTX}\nCompleteness critic, read-only, on ${A.wt} at ${head}. Default to complete=false when unsure. Check: every open ` +
  `child of epic #${A.epic} is built with a passing test or has an open question in ledger.md; every test-plan row in plan.md has a test file that exists or ` +
  `a scenario with a driver and the tag epic${A.epic}; receipts.md has red and green runs for each ticket; no lane branch has unmerged commits ` +
  `(\`git -C ${A.wt} branch --no-merged HEAD --list '${LANE_PREFIX}${A.epic}-*'\`); \`git -C ${A.wt} status --porcelain\` is empty. List what is missing.`,
  { label: 'completeness', phase: 'Integrate', model: MODEL('check'), schema: CRITIC })
return { head, mergedLanes, failed, appliedLocal, qaNotes, harnessReports, baseMain: (integ && integ.base_main) || null, integrate: integ,
  critic: critic || { complete: false, missing: ['completeness critic returned nothing'] } }
