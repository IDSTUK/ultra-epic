export const meta = {
  name: 'ultra-epic-review',
  description: 'Review gate on one frozen epic head: review lenses and an optional cross-model panel in parallel, three read-only sceptics per finding, review.md bound to the head',
  phases: [
    { title: 'Find', detail: 'review lenses from the profile + the panel skill if set' },
    { title: 'Verify', detail: '3 read-only sceptics per finding; 2 refutations kill it' },
    { title: 'Evidence', detail: 'review.md with Reviewed-SHA and GATE line; non-blocking findings become tickets' },
  ],
}
// args: {epic, slug, repo, wt, main, epicDir, skillDir, profile, head, focus, round, previous?}
const A = args
if (!A.head || !A.wt || !A.epicDir || !A.profile) throw new Error('review.js needs args.head, wt, epicDir and profile')
const PR = A.profile
const get = (k, d) => { const v = k.split('.').reduce((o, x) => (o && typeof o === 'object') ? o[x] : undefined, PR); return v === undefined || v === null ? d : v }
const MODEL = r => get(`models.${r}`, { plan: 'opus', find: 'opus', build: 'sonnet', check: 'sonnet', sweep: 'haiku' }[r])
const R = A.round || 1
const MAX_ROUNDS = get('review.max_rounds', 3)
// Circuit breaker (v3): past the last allowed round the workflow runs nothing and hands the decision to the human
if (R > MAX_ROUNDS) return { head: A.head, pass: false, askHuman: true, why: `review round ${R} is past review.max_rounds (${MAX_ROUNDS}): ask the human` }
const SFX = R > 1 ? `-r${R}` : ''
const H = A.head
const MAIN_REF = `${get('git.remote', 'origin')}/${get('git.main_branch', 'main')}`
const ENV = get('stack.env_file', '.test-stack/test.env')
const LOCAL = `Never test against the live environment (${get('live.description', 'production')}): prove a finding by reading the code, or on the epic's ` +
  `own local stack only (\`. ${A.wt}/${ENV} &&\` in the same Bash call).`
const CTX = `Epic #${A.epic} in ${A.repo}. Epic worktree: ${A.wt}. Epic dir: ${A.epicDir}. Frozen head H = ${H}. ${LOCAL} ` +
  `The Bash cwd may reset between calls: start every Bash call with \`cd <dir> &&\` or use \`git -C <dir>\`; change nothing${A.main ? ` in ${A.main}` : ''}. ` +
  `Diff under review: \`git -C ${A.wt} diff ${MAIN_REF}...${H} -- . ':!${get('paths.epics', '.claude/epics')}'\`: the epic's own code, including its tests and harness drivers` +
  `${A.previous ? `. Round ${R}: a DELTA review of the epic's own changes since the last review: \`cd ${A.wt} && git diff ${A.previous} ${H} -- $(git diff --name-only ${MAIN_REF} ${H})\` ` +
    '(the pathspec drops what a main merge brought in). A finding anywhere still counts' : ''}.`
const FINDINGS = { type: 'object', properties: { findings: { type: 'array', items: { type: 'object', properties: {
  file: { type: 'string' }, line: { type: 'integer' }, severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
  category: { type: 'string' }, summary: { type: 'string' }, scenario: { type: 'string' },
  regression: { type: 'boolean' }, security: { type: 'boolean' } },
  required: ['file', 'severity', 'category', 'summary', 'scenario', 'regression', 'security'] } } }, required: ['findings'] }
const VOTE = { type: 'object', properties: { refuted: { type: 'boolean' }, reason: { type: 'string' } }, required: ['refuted', 'reason'] }

const LENSES = get('review.lenses', [
  ['correctness', 'logic errors, wrong conditions, null paths, error handling, concurrency, time handling'],
  ['acceptance', 'each child issue\'s Given/When/Then against the code: anything asked for and not done, or done differently'],
  ['tests', 'tests that cannot fail, expected values copied from the code, missing negative cases'],
])
const RULES = 'Report only findings with a concrete failure scenario (inputs, then the wrong result). regression = true when behaviour that worked on ' +
  `${MAIN_REF} breaks; security = true when it is exploitable. Exclude style, DoS and rate limits, and "should validate" with no exploit path.`
const find = (label, prompt, model) => () => agent(`${CTX}\n${prompt}\n${RULES}`, { label, phase: 'Find', model: model || MODEL('find'), schema: FINDINGS })
const PANEL = get('review.panel_skill', null)
const FINDERS = [
  ...LENSES.map(([k, l]) => [`review:${k}`, find(`review:${k}`, `Read-only. Code review through ONE lens: ${l}. Review focus from plan.md: ${A.focus || '(none)'}`)]),
  ...(PANEL ? [['panel', find('panel', `Run the ${PANEL} skill's review steps only (write nothing into the epic dir, never run sdlc.py, edit and commit nothing). ` +
    `Base ${A.previous || MAIN_REF}, head ${H}${A.previous ? ', diff limited to the delta pathspec above' : ''}, focus "${A.focus || ''}". Work dir /tmp/${PANEL}-${A.epic}${SFX}. ` +
    'Try every seat and drop only one whose real call fails. Long API calls: run them in the background and wait with Monitor. Return the CONFIRMED and ' +
    'PLAUSIBLE findings (scenario = the finding text).', MODEL('check'))]] : []),
]

phase('Find')
const found = await parallel(FINDERS.map(f => f[1]))
const dead = found.map((r, i) => r ? null : FINDERS[i][0]).filter(Boolean)
if (dead.length) log(`finders that returned nothing (round cannot pass): ${dead.join(', ')}`)
const seen = []  // dedup: same file within 5 lines and same category
for (const f of found.flatMap((r, i) => r ? r.findings.map(x => ({ ...x, by: FINDERS[i][0] })) : [])) {
  const twin = seen.find(s => s.file === f.file && Math.abs((s.line || 0) - (f.line || 0)) <= 5 && s.category === f.category)
  if (twin) { twin.votes = (twin.votes || 1) + 1; twin.by += `, ${f.by}`; twin.regression = twin.regression || f.regression; twin.security = twin.security || f.security }
  else seen.push({ ...f, id: `R${R}.${seen.length + 1}` })
}
log(`${seen.length} distinct finding(s)`)

phase('Verify')
const ANGLES = ['trace the exact code path', 'check whether existing code or tests already prevent it',
  `try to show it with read-only means: \`git show\`, a query inside a rolled-back transaction on the epic's LOCAL stack only, or a test written in a scratch copy outside the repo`]
const judged = await parallel(seen.map(f => () =>
  parallel(ANGLES.map((angle, k) => () => agent(`${CTX}\nRead-only: edit no repo file and write nothing to any database. Try to REFUTE this finding ` +
    `by reading the code at H (\`git -C ${A.wt} show ${H}:<path>\`). Sceptic ${k + 1} of 3: ${angle}. Set refuted=true only when you can show from ` +
    `the code that the failure cannot happen.\nFinding: ${JSON.stringify(f)}`,
    { label: `verify:${f.id}#${k + 1}`, phase: 'Verify', model: MODEL('check'), schema: VOTE })))
    .then(vs => ({ ...f, real: vs.filter(v => v && v.refuted).length < 2, reasons: vs.filter(Boolean).map(v => v.reason) }))))
const confirmed = judged.filter(Boolean).filter(f => f.real)
const refuted = judged.filter(Boolean).filter(f => !f.real)
// v3 rule: only regressions, high-severity and security findings block. Everything else becomes a ticket.
const blocks = f => f.regression === true || f.security === true || f.severity === 'critical' || f.severity === 'high'
const blocking = confirmed.filter(blocks)
const followUps = confirmed.filter(f => !blocks(f))
const pass = !blocking.length && !dead.length
const askHuman = !pass && R >= MAX_ROUNDS

phase('Evidence')
const ev = await agent(`${CTX}\nEvery command: \`cd ${A.wt} && …\`. Write ${A.epicDir}/review${SFX}.md: lenses and panel seats used, a table ` +
  '# | Verdict | Sev | Regression | Security | Votes | By | File | Summary for every finding (BLOCKING / TICKET / REFUTED, with the sceptics\' reason for ' +
  `refuted ones), the review focus checked against the code${A.previous ? `, and that this is a delta review of ${A.previous}..${H}` : ''}, then as the LAST ` +
  `two lines \`Reviewed-SHA: ${H}\` and \`GATE: ${pass ? 'PASS' : 'FAIL'}\`. Never write both a PASS and a FAIL line in one file.\n` +
  `Blocking: ${JSON.stringify(blocking)}\nTickets (non-blocking, confirmed): ${JSON.stringify(followUps)}\n` +
  `Refuted: ${JSON.stringify(refuted.map(f => ({ id: f.id, summary: f.summary, reasons: f.reasons })))}\nFinders that returned nothing: ${JSON.stringify(dead)}\n` +
  `git add only that file; commit "evidence: epic #${A.epic} review round ${R}" (an exempt path: gates stay fresh). Return its absolute path.`,
  { label: 'evidence', phase: 'Evidence', model: MODEL('check') })
return { head: H, pass, askHuman, blocking, followUps, dead, refutedCount: refuted.length, evidence: ev, round: R, maxRounds: MAX_ROUNDS }
