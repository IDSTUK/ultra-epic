export const meta = {
  name: 'ultra-epic-plan',
  description: 'Refine every epic ticket in parallel to Definition of Ready, draft plan.md with dependency lanes, check the lane rules, then attack the plan from three angles',
  phases: [
    { title: 'Refine', detail: 'one read-only agent per ticket: criteria, tests, files, state table' },
    { title: 'Draft', detail: 'lanes from waves.py, plan.md with the test tier per lane' },
    { title: 'Lanes', detail: 'lane rules: at most N tickets per lane, no lane blocks most others' },
    { title: 'Critique', detail: 'coverage, lane safety, risk' },
    { title: 'Revise', detail: 'apply confirmed plan defects' },
  ],
}
// args: {epic, slug, repo, wt, main, epicDir, skillDir, profile, tickets:[n...], snapshot}
const A = args
if (!A.wt || !A.epicDir || !A.skillDir || !A.tickets || !A.profile) throw new Error('plan.js needs args.wt, epicDir, skillDir, tickets and profile')
const PR = A.profile
const get = (k, d) => { const v = k.split('.').reduce((o, x) => (o && typeof o === 'object') ? o[x] : undefined, PR); return v === undefined || v === null ? d : v }
const MODEL = r => get(`models.${r}`, { plan: 'opus', find: 'opus', build: 'sonnet', check: 'sonnet', sweep: 'haiku' }[r])
const MAX_TICKETS = get('plan.max_tickets_per_lane', 5)
const MAX_SHARE = get('plan.max_block_share', 0.5)
const PROFILE_PATH = `${A.wt}/.claude/delivery-profile.json`

const REFINED = {
  type: 'object',
  properties: {
    n: { type: 'integer' },
    ready: { type: 'boolean' },
    criteria: { type: 'array', items: { type: 'object', properties: {
      gwt: { type: 'string' }, seam: { type: 'string' }, test: { type: 'string' },
      scenario_id: { type: 'string' }, tier: { type: 'integer' }, exists: { type: 'boolean' } },
      required: ['gwt', 'seam', 'test', 'tier', 'exists'] } },
    files: { type: 'array', items: { type: 'string' } },
    migration: { type: 'boolean' },
    // Definition of Ready (v3): a ticket that changes timing or state answers every combination of its conditions
    changes_state: { type: 'boolean' },
    state_table: { type: 'string' },
    device_only: { type: 'array', items: { type: 'string' } },
    size: { type: 'string', enum: ['S', 'M', 'L'] },
    edges: { type: 'array', items: { type: 'object', properties: {
      blocked: { type: 'integer' }, blocker: { type: 'integer' }, reason: { type: 'string' } },
      required: ['blocked', 'blocker', 'reason'] } },
    questions: { type: 'array', items: { type: 'string' } },
  },
  required: ['n', 'ready', 'criteria', 'files', 'migration', 'changes_state', 'size', 'edges', 'questions'],
}
const CRIT = {
  type: 'object',
  properties: { defects: { type: 'array', items: { type: 'object', properties: {
    where: { type: 'string' }, problem: { type: 'string' }, fix: { type: 'string' },
    severity: { type: 'string', enum: ['blocker', 'major', 'minor'] } },
    required: ['where', 'problem', 'fix', 'severity'] } } },
  required: ['defects'],
}
const LINT = { type: 'object', properties: { exit: { type: 'integer' }, output: { type: 'string' } }, required: ['exit', 'output'] }

const LIVE = `The live environment (${get('live.description', 'production')}) is never tested: plan every test, probe and harness run on the epic's ` +
  `own local test stack (the profile's stack block). Migrations reach live only at DEPLOY, from the approved list.`
const CTX = `Epic #${A.epic} in ${A.repo}. Epic worktree: ${A.wt} (branch ${get('git.epic_prefix', 'epic/')}${A.epic}-${A.slug}). Epic dir: ${A.epicDir}. ` +
  `Board snapshot: ${A.snapshot}. Delivery profile: ${PROFILE_PATH}. ${LIVE} ` +
  `The Bash cwd may reset between calls: start every Bash call with \`cd ${A.wt} &&\` and use absolute paths for every edit. ` +
  `Never change the main checkout${A.main ? ` ${A.main}` : ''}.`

phase('Refine')
const refined = (await parallel(A.tickets.map(n => () => agent(
  `${CTX}\nRefine ticket #${n} to Definition of Ready, read-only (edit nothing). Read \`gh issue view ${n} -R ${A.repo} --comments\` and the code it touches. ` +
  'Return: every acceptance criterion as Given/When/Then with the test seam, the test file or harness scenario id that will prove it, ' +
  'its tier (0 unit, 1 integration on the local stack, 2 end to end, 3 device) and whether that test exists; the exact files the change will edit ' +
  '(repo-relative, from the code, not the ticket text); migration yes or no; changes_state = true when the ticket changes timing, a state machine, ' +
  'a lifecycle or anything with combined conditions; then state_table = a Markdown table that answers EVERY combination of those conditions ' +
  '(rows = combinations, columns = conditions and the expected result), taken from the ticket or derived and marked "derived"; ' +
  'checks only a physical device can do; size; missing blocked-by edges with a concrete reason (shared file, schema, API, migration before code); ' +
  'and questions only a human can answer (product scope, pricing, anything irreversible). ready=false when a question blocks the build, ' +
  'when a criterion names no test, or when changes_state is true and the state table has a combination with no answer.',
  { label: `refine:#${n}`, phase: 'Refine', model: MODEL('check'), schema: REFINED })))).filter(Boolean)
const missing = A.tickets.filter(n => !refined.some(r => r.n === n))
if (missing.length) log(`refine failed for ${missing.map(n => '#' + n).join(', ')}: the draft agent refines them inline`)
// Definition of Ready, enforced here, not left to the agent: a state change with no state table is not Ready
for (const r of refined) {
  if (r.changes_state && !(r.state_table || '').includes('|')) {
    r.ready = false
    r.questions = [...(r.questions || []), `#${r.n} changes timing or state but has no state table answering every combination of its conditions`]
  }
  const untested = (r.criteria || []).filter(c => !(c.test || '').trim())
  if (untested.length) {
    r.ready = false
    r.questions = [...(r.questions || []), `#${r.n}: ${untested.length} criterion/criteria name no test`]
  }
}

phase('Draft')
const SNAP = String(get('board.snapshot', `python3 ${A.skillDir}/scripts/snapshot.py {epic} --repo ${A.repo} --prefix ${get('board.component_label_prefix', 'component:')} --out {file}`))
  .replace(/\{file\}/g, A.snapshot).replace(/\{epic\}/g, String(A.epic))
const draft = await agent(
  `${CTX}\nYou write the plan for an /ultra-epic run. Refinement results (JSON): ${JSON.stringify(refined)}.\n` +
  `${missing.length ? `Tickets with no refinement result (refine them yourself): ${missing.join(', ')}.\n` : ''}` +
  `Steps:\n1. Add missing blocked-by edges from the results on GitHub (mechanical edges only; log each as a Ruling). Then snapshot: \`${SNAP}\`.\n` +
  `2. Write ${A.epicDir}/files.json ({"<ticket>": [files]}) and run \`cd ${A.wt} && python3 ${A.skillDir}/scripts/waves.py ${A.epic} --snapshot ${A.snapshot} ` +
  `--files ${A.epicDir}/files.json --profile ${PROFILE_PATH} > ${A.epicDir}/lanes.json\`. Read it. Report cycles and external blockers; never hide them.\n` +
  `3. Lane rules (v3): a lane holds at most ${MAX_TICKETS} tickets, and no lane may block more than ${Math.round(MAX_SHARE * 100)}% of the other lanes ` +
  '(lanes.json "problems" lists every break). Fix each break before you go on: give tickets that share no file their own file lists, split a ' +
  'long lane at a natural seam, or cut an edge that is not real. Run waves.py again until "problems" is empty. A break you cannot fix goes to ' +
  'Decisions with your recommendation.\n' +
  `4. Add "tier" to each lane in lanes.json: the highest test tier its tickets' criteria need (from the refinement). The QA checkers judge each lane ` +
  'against its own tier only.\n' +
  `5. Write ${A.epicDir}/plan.md: Goal, Definition of Done, Out of scope; ## Lanes (table: Lane | Tickets | Depends on | Components | Tier | Migrations | Main files) ` +
  'and the start rule "a lane starts from the epic head once its dependencies are merged"; ## Test plan (one row per criterion: ticket, Given/When/Then, ' +
  'seam, test or scenario id, tier; name ONLY scenario ids the harness gate run can turn green; a device-only check goes under Device checks); ' +
  '## State tables (each ticket that changes timing or state, its full table); ## Review focus (3-5 failure modes no planned test catches); ' +
  'Data and migrations; ## Device checks; ## Decisions (every question, numbered, with your recommendation); ## Context budget. ' +
  'Do NOT write an Approved: line: only the human\'s yes in chat does.\n' +
  `6. If the profile has paths.scenarios, add every NEW scenario id there as a complete stub (id, component, section "epic #${A.epic}", title, tier, ` +
  `status: missing, tags [<component harness tag>, "epic${A.epic}"], assertion = the Given/When/Then).\n` +
  `7. git add only plan.md, files.json, lanes.json and the scenario file; commit "plan: epic #${A.epic}". ` +
  'Return a 15-line summary: lanes and their order, cap, migrations, external blockers, cycles, decisions.',
  { label: 'draft-plan', phase: 'Draft', model: MODEL('plan') })

phase('Lanes')
// the lane rules are checked by the gate tool itself, not by the agent's word
let lint = null
for (let round = 0; round < 3; round++) {
  lint = await agent(`${CTX}\nRead-only. Run \`cd ${A.wt} && python3 tools/sdlc/sdlc.py lint-lanes ${A.epicDir}/lanes.json\`. Return its exit code and output.`,
    { label: `lint-lanes#${round}`, phase: 'Lanes', model: MODEL('sweep'), schema: LINT })
  if (!lint || lint.exit === 0) break
  log(`lane rules broken: ${lint.output}`)
  if (round === 2) break
  await agent(`${CTX}\nThe lane rules fail (at most ${MAX_TICKETS} tickets per lane; no lane blocks more than ${Math.round(MAX_SHARE * 100)}% of the others):\n${lint.output}\n` +
    `Fix lanes.json, files.json and plan.md's ## Lanes table as in the draft step 3 (split by file, cut an edge that is not real), re-run waves.py, commit ` +
    `"plan: epic #${A.epic} lane rules". A break you cannot fix: add it to ## Decisions with a recommendation.`,
    { label: `fix-lanes#${round}`, phase: 'Lanes', model: MODEL('plan') })
}
const laneRules = lint ? { ok: lint.exit === 0, output: lint.output } : { ok: false, output: 'lane lint returned nothing' }

phase('Critique')
const LENSES = [
  ['coverage', 'Every acceptance criterion of every child (read each issue yourself) has a test-plan row with a seam, a test and a tier; every ticket that changes timing or state has a complete state table in plan.md; device-only checks are listed.'],
  ['lanes', 'Lane safety: compare lanes.json and files.json with the real code. Find two lanes that will edit the same file, a missing blocked-by edge (migration before code, API before client), a lane that should be split for parallelism, migration number clashes, and a lane whose tier is lower than its tickets need.'],
  ['risk', 'Failure modes: load on the shared test stack, migrations applied to the local stack before the harness and to live only at DEPLOY, irreversible steps, things only a human can decide, and Review focus items that are weak.'],
]
const critiques = await parallel(LENSES.map(([k, lens]) => () => agent(
  `${CTX}\nRead ${A.epicDir}/plan.md, lanes.json, files.json and the child issues. Find what is wrong with this plan through ONE lens: ${lens} ` +
  'Read-only. Report only defects you checked against the code or the issues; no style notes.',
  { label: `critic:${k}`, phase: 'Critique', model: MODEL('check'), schema: CRIT })))
const missingCritics = LENSES.filter((l, i) => !critiques[i]).map(l => l[0])
if (missingCritics.length) log(`plan critics that returned nothing: ${missingCritics.join(', ')}`)
const defects = critiques.filter(Boolean).flatMap(c => c.defects).filter(d => d.severity !== 'minor')

phase('Revise')
let revised = 'no blocker or major defects'
if (defects.length) {
  revised = await agent(
    `${CTX}\nPlan critics found these defects (JSON): ${JSON.stringify(defects)}. Check each against the code. Fix the real ones in ` +
    'plan.md / files.json (re-run waves.py if lanes change, and keep the lane rules) and board links. Reject false ones with a one-line reason ' +
    `in plan.md under "## Plan review". Commit "plan: epic #${A.epic} review fixes". Return what you changed and rejected.`,
    { label: 'revise-plan', phase: 'Revise', model: MODEL('plan') })
}
return { draft, defects: defects.length, missingCritics, revised, laneRules,
  unready: refined.filter(r => !r.ready).map(r => ({ n: r.n, questions: r.questions })) }
