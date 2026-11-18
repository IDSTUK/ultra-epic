export const meta = {
  name: 'ultra-epic-final',
  description: 'On the merged epic head: the unit/build sweep and the security review first, then (only when they pass) the harness gate run last; sceptics on security findings; security.md and harness.md',
  phases: [
    { title: 'Setup', detail: 'one detached worktree at the merged head, the epic stack checked' },
    { title: 'Security', detail: 'sweep groups, security lenses, checklist and profile commands' },
    { title: 'Verify', detail: '3 read-only sceptics per security finding' },
    { title: 'Harness', detail: 'only after security passed: the harness gate run, checked by sdlc.py check-harness' },
    { title: 'Evidence', detail: 'status flips, harness.md, security.md bound to the head' },
  ],
}
// args: {epic, slug, repo, wt, main, epicDir, skillDir, profile, head (merged epic head), tags:[what `sdlc.py gate-tags <N>` prints],
//        components:[touched], round, previous?}
const A = args
if (!A.head || !A.wt || !A.profile || !A.tags || !A.tags.length) throw new Error('final.js needs args.head, wt, profile and tags')
if (![`epic${A.epic}`, `e${A.epic}`].every(t => A.tags.includes(t))) throw new Error(`final.js needs args.tags = what \`sdlc.py ` +
  `gate-tags ${A.epic}\` prints (epic${A.epic} and e${A.epic} are missing from ${JSON.stringify(A.tags)})`)
const PR = A.profile
const get = (k, d) => { const v = k.split('.').reduce((o, x) => (o && typeof o === 'object') ? o[x] : undefined, PR); return v === undefined || v === null ? d : v }
const MODEL = r => get(`models.${r}`, { plan: 'opus', find: 'opus', build: 'sonnet', check: 'sonnet', sweep: 'haiku' }[r])
const ports = () => { const st = PR.stack || {}; const step = st.port_step || 10, slots = st.slots || 20
  return Object.fromEntries(Object.entries(st.ports || {}).map(([k, b]) => [k, b + (A.epic % slots) * step])) }
const render = (tpl, v = {}) => String(tpl).replace(/\{(\w+)\}/g, (m, k) => k in v ? String(v[k]) : k === 'epic' ? String(A.epic)
  : (k.startsWith('port_') && k.slice(5) in ports()) ? String(ports()[k.slice(5)]) : m)
const TAG_FLAGS = A.tags.map(t => `--tag ${t}`).join(' ')
const R = A.round || 1
const SFX = R > 1 ? `-r${R}` : ''
const H = A.head
const HWT = `${A.wt}-h`
const MAIN_REF = `${get('git.remote', 'origin')}/${get('git.main_branch', 'main')}`
const ENV = get('stack.env_file', '.test-stack/test.env')
const LOCAL_URL = get('live.local_url_prefix', 'http://127.0.0.1')
const EREL = A.epicDir.startsWith(A.wt + '/') ? A.epicDir.slice(A.wt.length + 1) : `${get('paths.epics', '.claude/epics')}/${A.epic}-${A.slug}`
const WREL = `${EREL}/local-waivers.json`
const CWD = `The Bash cwd may reset between calls: start every Bash call with \`cd <dir> &&\` or use \`git -C <dir>\`; change nothing${A.main ? ` in ${A.main}` : ''}.`
const LOCAL = `Never test against the live environment (${get('live.description', 'production')}): every test, probe and proof runs on the epic's own stack, ` +
  `with \`. ${HWT}/${ENV} &&\` in the same Bash call, or reads the code.`
const CTX = `Epic #${A.epic} in ${A.repo}. Epic worktree: ${A.wt}. Epic dir: ${A.epicDir}. H = ${H}: the epic branch with ${MAIN_REF} merged in ` +
  `(the merged baseline). ${CWD} ${LOCAL}`
const RUN_IN = `Work in the detached worktree ${HWT} (already at H, set up): every command starts with \`cd ${HWT} &&\`. ` +
  'Never commit, merge, reset or edit tracked files there. Commands longer than 10 minutes: run_in_background and wait with Monitor.'
const FINDINGS = { type: 'object', properties: { findings: { type: 'array', items: { type: 'object', properties: {
  file: { type: 'string' }, line: { type: 'integer' }, severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
  category: { type: 'string' }, summary: { type: 'string' }, scenario: { type: 'string' } },
  required: ['file', 'severity', 'category', 'summary', 'scenario'] } } }, required: ['findings'] }
const VOTE = { type: 'object', properties: { refuted: { type: 'boolean' }, reason: { type: 'string' } }, required: ['refuted', 'reason'] }
const HARN = { type: 'object', properties: {
  run_id: { type: 'string' }, summary_json: { type: 'string' }, tests: { type: 'integer' }, failures: { type: 'integer' },
  errors: { type: 'integer' }, skipped: { type: 'integer' }, git_sha: { type: 'string' }, failing: { type: 'array', items: { type: 'string' } },
  left_out: { type: 'array', items: { type: 'string' } }, waived: { type: 'object', additionalProperties: { type: 'integer' } },
  infra: { type: 'string' } }, required: ['run_id', 'summary_json', 'tests', 'failures', 'errors', 'skipped', 'git_sha', 'failing', 'left_out', 'waived'] }
const RES = { type: 'object', properties: { results: { type: 'array', items: { type: 'object', properties: {
  cmd: { type: 'string' }, exit: { type: 'integer' }, counts: { type: 'string' }, tail: { type: 'string' } },
  required: ['cmd', 'exit', 'counts'] } } }, required: ['results'] }
const SETUP = { type: 'object', properties: { ok: { type: 'boolean' }, note: { type: 'string' }, waivers: { type: 'boolean' } },
  required: ['ok', 'note', 'waivers'] }
const CMP = { type: 'object', properties: { green: { type: 'boolean' }, note: { type: 'string' },
  gate_exit: { type: 'integer' }, gate_output: { type: 'string' } }, required: ['green', 'note', 'gate_exit', 'gate_output'] }

phase('Setup')
const setup = await agent(`${CTX}\n\`git -C ${A.wt} worktree remove --force ${HWT} 2>/dev/null; git -C ${A.wt} worktree add --detach ${HWT} ${H}\`, ` +
  `then \`sh ${A.skillDir}/scripts/lane_env.sh ${HWT} ${A.main || A.wt} ${A.wt}\` (it links ${HWT}/${ENV} to the epic stack's). Check \`git -C ${HWT} rev-parse HEAD\` ` +
  `is H and \`git -C ${HWT} status --porcelain -uno\` is empty. The epic's stack: reuse it, NEVER start, stop or reset it here. In one Bash call: ` +
  `\`. ${HWT}/${ENV}\` and check every URL it exports starts with ${LOCAL_URL}${get('stack.check') ? `, then \`cd ${A.wt} && ${render(get('stack.check'), { wt: A.wt })}\` exits 0` : ''}. ` +
  `Then \`cd ${A.wt} && python3 tools/sdlc/sdlc.py gate-tags ${A.epic}\` must print the same set of flags as \`${TAG_FLAGS}\` (else ok=false with both lists). ` +
  'ok=false with the reason on any failure. Last, waivers = whether the epic\'s local waivers are committed at H: ' +
  `\`git -C ${HWT} ls-files --error-unmatch ${WREL}\` exits 0 (true) or not (false).`, { label: 'setup', phase: 'Setup', model: MODEL('sweep'), schema: SETUP })
if (!setup || !setup.ok) return { head: H, pass: false, harnessGreen: false, setupFailed: setup ? setup.note : 'setup returned nothing' }

const comps = A.components || []
const COMPS = get('components', {})
const SWEEP = comps.filter(c => COMPS[c]).map(c => [c, [COMPS[c].unit, COMPS[c].build].filter(Boolean)])
const sweep = [...SWEEP, ...(get('harness.tier0_command') ? [['tier0', [get('harness.tier0_command')]]] : [])]
  .map(([k, cmds]) => [`sweep:${k}`, () => agent(`${CTX}\nTest sweep group "${k}" on the merged head. ${RUN_IN}\nRun, one at a time: ${JSON.stringify(cmds)}. ` +
    'Report each command with its exit code and test counts. Do not fix anything.', { label: `sweep:${k}`, phase: 'Security', model: MODEL('sweep'), schema: RES })])
const SEC = `Diff under review: \`git -C ${A.wt} diff ${MAIN_REF}...${H} -- . ':!${get('paths.epics', '.claude/epics')}'\` (the epic's own code on the merged baseline)` +
  `${A.previous ? `; round ${R}: focus on \`git -C ${A.wt} diff ${A.previous}..${H}\`` : ''}.`
const SEC_LENSES = get('security.lenses', [
  ['authz', 'access control, privileged functions, cross-tenant reach, new tables or endpoints without access rules'],
  ['injection', 'XSS sinks, SQL built from strings, URL or host built from input, open redirects'],
  ['secrets-privacy', 'secrets in code or logs, personal data in URLs or analytics, delete paths'],
])
const PROOF = `Prove each finding only by reading the code at H, or on the epic's LOCAL stack (\`. ${HWT}/${ENV} && …\`, a query inside a rolled-back transaction). ` +
  'Never through the live environment.'
const find = (label, prompt, model) => [label, () => agent(`${CTX}\n${SEC}\n${prompt}\n${PROOF}\nReport only findings with a concrete failure scenario. ` +
  'Exclude style, DoS and rate limits, and "should validate" with no exploit path.', { label, phase: 'Security', model: model || MODEL('find'), schema: FINDINGS })]
const finders = [
  ...SEC_LENSES.map(([k, l]) => find(`security:${k}`, `Read-only. Security review through ONE lens: ${l}. Use the Skill tool with security-review ` +
    'if it is available; otherwise apply its method: exploitable issues only, confidence 8/10 or higher.')),
  ...(get('security.checklist') ? [find('checklist', `Walk ${get('security.checklist')} for the touched components (${comps.join(', ')}). Each item: pass / N/A / ` +
    `finding with evidence. Return only the findings; ALSO write the full table to ${A.epicDir}/checklist${SFX}.md (exempt path; the evidence step commits it).`, MODEL('check'))] : []),
  ...get('security.commands', []).map((c, i) => find(`security-cmd:${i}`, `Read-only. Run \`cd ${HWT} && ${render(c, { wt: HWT })}\`. A non-zero exit is a critical ` +
    `finding (file = the command's tool, scenario = the failing output).`, MODEL('sweep'))),
]

phase('Security')
const [sweepOut, found] = await Promise.all([parallel(sweep.map(s => s[1])), parallel(finders.map(f => f[1]))])
const deadSweep = sweep.filter((s, i) => !sweepOut[i]).map(s => s[0])
const deadSec = finders.filter((f, i) => !found[i]).map(f => f[0])
const rows = sweepOut.filter(Boolean).flatMap(s => s.results)
const sweepGreen = !deadSweep.length && rows.length > 0 && rows.every(r => r.exit === 0)
const seen = []
for (const f of found.flatMap((r, i) => r ? r.findings.map(x => ({ ...x, by: finders[i][0] })) : [])) {
  const twin = seen.find(s => s.file === f.file && Math.abs((s.line || 0) - (f.line || 0)) <= 5 && s.category === f.category)
  if (twin) { twin.votes = (twin.votes || 1) + 1; twin.by += `, ${f.by}` } else seen.push({ ...f, id: `S${R}.${seen.length + 1}` })
}
log(`sweep rows ${rows.length} (${sweepGreen ? 'green' : 'red'}); ${seen.length} security finding(s)`)

phase('Verify')
const ANGLES = ['trace the exact code path', 'check whether existing code, access rules or tests already prevent it',
  'try to show it with read-only means: `git show`, a query inside a rolled-back transaction on the LOCAL stack only, or a test in a scratch copy outside the repo']
const judged = await parallel(seen.map(f => () =>
  parallel(ANGLES.map((angle, k) => () => agent(`${CTX}\nRead-only: edit no repo file and write nothing to any database. Try to REFUTE this ` +
    `security finding by reading the code at H (\`git -C ${A.wt} show ${H}:<path>\`). Sceptic ${k + 1} of 3: ${angle}. Set refuted=true only when you ` +
    `can show from the code that the failure cannot happen.\nFinding: ${JSON.stringify(f)}`,
    { label: `verify:${f.id}#${k + 1}`, phase: 'Verify', model: MODEL('check'), schema: VOTE })))
    .then(vs => ({ ...f, real: vs.filter(v => v && v.refuted).length < 2, reasons: vs.filter(Boolean).map(v => v.reason) }))))
const confirmed = judged.filter(Boolean).filter(f => f.real)
const refuted = judged.filter(Boolean).filter(f => !f.real)
const blocking = confirmed.filter(f => f.severity !== 'low')
const lows = confirmed.filter(f => f.severity === 'low')
const securityPass = !blocking.length && !deadSec.length && sweepGreen

// The harness is last (v3): it runs only when security and the sweep passed on this head. A red security round costs no harness hour.
let h = null, cmp = null
if (securityPass) {
  phase('Harness')
  const RUN_ID = `epic${A.epic}-${H.slice(0, 7)}${SFX}`
  const WAIVE = setup.waivers === true ? ` --local-waivers ${WREL}` : ''
  const cmd = render(get('harness.command', '<profile harness.command>'), { wt: HWT, run_id: RUN_ID, tags: TAG_FLAGS + WAIVE })
  h = await agent(`${CTX}\nYou run the harness GATE run. ${RUN_IN}\nIn the background, watched with Monitor (it can take over an hour):\n\`cd ${HWT} && ${cmd}\`.\n` +
    'A suite the run leaves out FAILS the gate unless the epic\'s approved local-waivers.json (passed above when it is committed at H) names it. Report every ' +
    'left-out and waived suite; never mark a suite runnable, add a waiver, or point a suite at live to make the run pass. A plainly infrastructure failure ' +
    `(device boot timeout, stack down): run once more with run id ${RUN_ID}b and say so in infra. Do NOT fix code. Copy the final summary to ` +
    `${A.epicDir}/harness-summary${SFX}.json and return that ABSOLUTE path as summary_json, with its totals, git_sha, left_out, waived ({} when none) and each failing case.`,
    { label: 'harness-run', phase: 'Harness', model: MODEL('build'), schema: HARN })
  // the verdict is the gate tool's own check (sdlc.py check-harness), not a copy of its rules here
  cmp = await agent(`Adversarial check of the harness result, read-only. Default to green=false when unsure. Harness: ${JSON.stringify(h)}. ` +
    `1) Does the summary git_sha match H (${H})? 2) Is every count present and non-zero tests? 3) The gate's own check, read-only (it records nothing): ` +
    `\`cd ${A.wt} && python3 tools/sdlc/sdlc.py check-harness ${A.epic} --evidence ${(h && h.summary_json) || '<no summary_json reported>'}\`. Return its exit ` +
    'code as gate_exit (no summary_json, or it did not run: 1) and its last line as gate_output. Say in note where the summary differs from the report.',
    { label: 'compare', phase: 'Harness', model: MODEL('check'), schema: CMP })
} else log('harness not run: security or the sweep did not pass on this head')
const harnessGreen = !!h && !!cmp && cmp.gate_exit === 0
const waived = (h && h.waived && typeof h.waived === 'object') ? h.waived : {}
const statusFiles = get('paths.status_only.files', [])

phase('Evidence')
const ev = await agent(`${CTX}\nEvery command: \`cd ${A.wt} && …\`. Evidence docs live in an exempt path, so committing them keeps gates fresh. ` +
  'git add only the files named here.\n' +
  `Sweep: ${JSON.stringify(rows)}. Harness: ${JSON.stringify(h)}. Gate check: ${JSON.stringify(cmp)}. Blocking security findings: ${JSON.stringify(blocking)}. ` +
  `Deferred lows: ${JSON.stringify(lows)}. Refuted: ${JSON.stringify(refuted.map(f => ({ id: f.id, summary: f.summary, reasons: f.reasons })))}. ` +
  `Dead agents: ${JSON.stringify([...deadSec, ...deadSweep])}.\n` +
  (harnessGreen && statusFiles.length
    ? `1. The harness gate check passed. In ${statusFiles.join(' and ')} change ONLY \`status:\` lines: each scenario id in plan.md proven by run ${h.run_id} → green. ` +
      `Check \`git diff -U0 -- ${statusFiles.join(' ')}\` shows only status lines (else stop and report: any other change makes every gate stale). `
    : harnessGreen ? '1. The harness gate check passed. ' : '1. The harness did not pass or did not run: write no harness.md and flip nothing.\n') +
  (harnessGreen ? `Write ${A.epicDir}/harness${SFX}.md: merged baseline H ${H}, command, run id, counts, scenarios flipped, the waived suites with their tickets ` +
    `(${JSON.stringify(waived)}; each is a gap the gate did not test), device checks.\n` : '') +
  `2. Write ${A.epicDir}/security${SFX}.md: # Security, epic #${A.epic}; the sweep table; Findings (each with status); the checklist table if ` +
  `${A.epicDir}/checklist${SFX}.md exists; then as the LAST two lines \`Reviewed-SHA: ${H}\` and \`SECURITY: ${securityPass ? 'PASS' : 'FAIL'}\`.\n` +
  `3. git add the evidence files written (harness-summary${SFX}.json, checklist${SFX}.md, harness${SFX}.md, security${SFX}.md) and the status files if changed; ` +
  `commit "evidence: epic #${A.epic} final round ${R}". \`git status --porcelain\` must show nothing. Return the paths written.`,
  { label: 'evidence', phase: 'Evidence', model: MODEL('check') })
return { head: H, sweepGreen, securityPass, harnessRan: !!h, harness: h, harnessGreen, compare: cmp, blocking, lows,
  dead: [...deadSec, ...deadSweep], refutedCount: refuted.length, evidence: ev, pass: securityPass && harnessGreen }
