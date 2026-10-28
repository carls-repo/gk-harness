export const meta = {
  name: 'gk-build',
  description: 'Build a multi-file Goodricke change from a plan split into pieces with disjoint file sets: plan check, wave-ordered implement, project check, per-piece review, judge',
  whenToUse: 'Use when a written plan/spec is already decomposed into pieces that each own a disjoint set of files. Requires args {plan: "<path to the plan/spec file>" (REQUIRED), pieces: [{name: "<unique piece name>", files: ["backend/src/...", "frontend/src/..."], brief: "<what this piece must do>", tests?: "<test file path or test command>", hard?: true|false}] (REQUIRED, non-empty), check?: "<project-wide check command, default "npm run typecheck", run from each affected package (backend/ and/or frontend/)>", ui?: true|false, profile?: "fable"|"opus"|"sonnet" (default "fable"), escalate?: true|false (route the judge to Fable under a non-fable profile, on Carl\'s word)}. A hard piece implements on opus xhigh (fable profile), opus high (opus profile) or sonnet high (sonnet profile). Paths are relative to /home/carls/apps/mike-dev. Edits land uncommitted in the dev checkout; returns {planCheck, pieces, check, reviews, judge} and the calling session commits, runs the scroll audit when judge.scrollAuditRequired is non-empty, and writes the summary.',
  phases: [
    { title: 'Plan check', detail: 'one Fable agent verifies the decomposition, orders pieces into waves, names page-root pieces (fable; opus under the opus/sonnet profiles)', model: 'fable' },
    { title: 'Implement', detail: 'waves run in sequence; pieces within a wave run in parallel, one agent per piece (opus high, hard pieces opus xhigh; sonnet medium under the opus/sonnet profiles, hard pieces opus high / sonnet high)', model: 'opus' },
    { title: 'Check', detail: 'one Haiku agent runs the check command in each affected package', model: 'haiku' },
    { title: 'Review', detail: 'one Opus xhigh reviewer per piece over git diff of its files', model: 'opus' },
    { title: 'Judge', detail: 'one judge over reviews, check and page roots: Fable if ui or page roots (every profile) or escalate, else Opus' },
  ],
}

// args may arrive as a raw JSON string depending on the invoking runtime.
const ARGS = typeof args === 'string' ? (() => { try { return JSON.parse(args) } catch (e) { return args } })() : args
const REPO = '/home/carls/apps/mike-dev'
const DEFAULT_CHECK = 'npm run typecheck'
const PACKAGES = ['backend', 'frontend']
const PAGE_ROOT_RE = /^frontend\/src\/app\/(.*\/)?(page|layout)\.[jt]sx?$/
const TEST_PATH_RE = /^[\w./@-]+\.(test|spec)\.[cm]?[jt]sx?$/

if (!ARGS || typeof ARGS !== 'object') throw new Error('gk-build requires args {plan, pieces:[{name, files, brief, tests?, hard?}], check?, ui?, profile?: fable|opus|sonnet, escalate?}')
if (typeof ARGS.plan !== 'string' || !ARGS.plan.trim()) throw new Error('gk-build: args.plan (path to the plan/spec file) is required')
if (!Array.isArray(ARGS.pieces) || ARGS.pieces.length === 0) throw new Error('gk-build: args.pieces must be a non-empty array of {name, files, brief, tests?, hard?}')
if (ARGS.check !== undefined && (typeof ARGS.check !== 'string' || !ARGS.check.trim())) throw new Error('gk-build: args.check, when given, must be a non-empty command string')
if (ARGS.ui !== undefined && typeof ARGS.ui !== 'boolean') throw new Error('gk-build: args.ui, when given, must be a boolean')

// ---------------- Profile (three-seat policy, 2026-10-13) ----------------
// 'fable' = Mode A (default, unchanged behaviour); 'opus' = Mode B; 'sonnet' = Mode C.
const PROFILE = ARGS.profile === undefined ? 'fable' : ARGS.profile
if (!['fable', 'opus', 'sonnet'].includes(PROFILE)) throw new Error(`gk-build: args.profile must be one of fable|opus|sonnet (got ${JSON.stringify(ARGS.profile)})`)
if (ARGS.escalate !== undefined && typeof ARGS.escalate !== 'boolean') throw new Error('gk-build: args.escalate, when given, must be a boolean')
const ESCALATE = ARGS.escalate === true  // Carl's word: route the apex judge to fable under a non-fable profile
const APEX = PROFILE === 'fable' ? 'fable' : 'opus'   // the top of the ladder for this profile
log(`Profile: ${PROFILE} (apex ${APEX}${ESCALATE ? ', escalated to fable on Carl\'s word' : ''})`)
// Implementer tier per profile; a piece flagged hard goes one step up.
const IMPL_TIER = {
  fable: { normal: { model: 'opus', effort: 'high' }, hard: { model: 'opus', effort: 'xhigh' } },
  opus: { normal: { model: 'sonnet', effort: 'medium' }, hard: { model: 'opus', effort: 'high' } },
  sonnet: { normal: { model: 'sonnet', effort: 'medium' }, hard: { model: 'sonnet', effort: 'high' } },
}[PROFILE]

const normPath = p => {
  if (typeof p !== 'string' || !p.trim()) throw new Error(`gk-build: invalid file path ${JSON.stringify(p)}`)
  let q = p.trim()
  if (q.startsWith(REPO + '/')) q = q.slice(REPO.length + 1)
  if (q.startsWith('/') || q.startsWith('-') || /(^|\/)\.\.(\/|$)/.test(q)) throw new Error(`gk-build: file path ${JSON.stringify(p)} must be inside ${REPO} with no traversal`)
  if (/['\n\r]/.test(q)) throw new Error(`gk-build: file path ${JSON.stringify(p)} must not contain quotes or newlines`)
  return q.replace(/^\.\//, '')
}
const normPathSafe = f => { try { return normPath(f) } catch (e) { return f } }
// Route groups such as frontend/src/app/(pages)/ are a bash syntax error unquoted.
const shq = list => list.map(f => `'${f}'`).join(' ')
let PLAN
try { PLAN = normPath(ARGS.plan) } catch (e) { throw new Error(`gk-build: args.plan — ${e.message}`) }
const PIECES = ARGS.pieces.map((p, i) => {
  if (!p || typeof p.name !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(p.name)) throw new Error(`gk-build: pieces[${i}].name must be a plain identifier`)
  if (!Array.isArray(p.files) || p.files.length === 0) throw new Error(`gk-build: pieces[${i}] (${p.name}).files must be a non-empty array`)
  if (typeof p.brief !== 'string' || !p.brief.trim()) throw new Error(`gk-build: pieces[${i}] (${p.name}).brief is required`)
  if (p.tests !== undefined && typeof p.tests !== 'string') throw new Error(`gk-build: pieces[${i}] (${p.name}).tests must be a string`)
  if (p.hard !== undefined && typeof p.hard !== 'boolean') throw new Error(`gk-build: pieces[${i}] (${p.name}).hard, when given, must be a boolean`)
  const files = p.files.map(normPath)
  // A tests value that is a test-file path is owned by the piece like its files.
  const owned = p.tests && TEST_PATH_RE.test(normPathSafe(p.tests)) ? [...new Set([...files, normPath(p.tests)])] : files
  return { name: p.name, files, owned, brief: p.brief, tests: p.tests || '', hard: p.hard === true }
})
const byName = Object.fromEntries(PIECES.map(p => [p.name, p]))
if (Object.keys(byName).length !== PIECES.length) throw new Error('gk-build: piece names must be unique')
const CHECK = ARGS.check || DEFAULT_CHECK
const pkgOf = f => PACKAGES.find(k => f.startsWith(k + '/'))
const AFFECTED = PACKAGES.filter(k => PIECES.some(p => p.owned.some(f => pkgOf(f) === k)))

const RULES = `
Rules: your final text is consumed by a script, not a human. Work in ${REPO} (no worktree). Do NOT commit, stash, reset or checkout. Ignore the repo's many untracked scratch scripts (backend/scripts/_*.ts etc.). Any test or build runs in the FOREGROUND with output piped through \`tail -40\` under \`set -o pipefail\` (so the exit status survives tail), never in the background. The dev stack serves this checkout live: never run npm run dev, restart services or delete .next. Database work is dev only; never prod. Do not expand scope.`

const describe = p => `- ${p.name}: files ${JSON.stringify(p.owned)}${p.tests ? `; tests: ${p.tests}` : ''}\n  brief: ${p.brief}`

// ---------------- Plan check ----------------
phase('Plan check')
log(`Plan check over ${PIECES.length} pieces; affected packages: ${AFFECTED.join(', ') || 'none'}`)
const planCheck = await agent(
  `Verify the decomposition of a build plan before parallel implementation.
Plan/spec file: ${REPO}/${PLAN} — read it in full. Pieces:
${PIECES.map(describe).join('\n')}

Check: (1) each piece is self-contained — its brief can be implemented editing only its listed files (plus one appended CHANGELOG.md line); (2) file sets are DISJOINT — if two pieces share a file, or one piece depends on another's output, put them in different waves in dependency order; (3) together the pieces cover the plan. Return ok=false with concrete problems only for real defects (missing files, an unimplementable split, plan coverage gaps); an overlap fixable by ordering is not a problem. order = waves, each a list of piece names; every piece appears exactly once; pieces in one wave run concurrently. pageRootPieces = pieces touching a page root (a frontend/src/app page or layout, or a component that becomes a page root) so the scroll audit runs. Read-only: edit nothing.${RULES}`,
  {
    label: 'plan-check', phase: 'Plan check', model: APEX, effort: 'xhigh',
    schema: {
      type: 'object',
      properties: {
        ok: { type: 'boolean' },
        problems: { type: 'array', items: { type: 'string' } },
        order: { type: 'array', items: { type: 'array', items: { type: 'string' } } },
        pageRootPieces: { type: 'array', items: { type: 'string' } },
      },
      required: ['ok', 'problems', 'order', 'pageRootPieces'],
    },
  },
)
if (!planCheck) throw new Error('gk-build: plan check agent returned nothing')
if (!planCheck.ok) throw new Error(`gk-build: plan check failed:\n- ${planCheck.problems.join('\n- ')}`)

// Deterministic guard on the agent's ordering: every piece exactly once, and no shared file inside a wave.
const seen = planCheck.order.flat()
const unknown = seen.filter(n => !byName[n])
const missing = PIECES.map(p => p.name).filter(n => !seen.includes(n))
const dupes = seen.filter((n, i) => seen.indexOf(n) !== i)
if (unknown.length || missing.length || dupes.length) throw new Error(`gk-build: plan check order invalid — unknown: ${unknown.join(',') || '-'}; missing: ${missing.join(',') || '-'}; duplicated: ${dupes.join(',') || '-'}`)
const WAVES = planCheck.order.filter(w => w.length)
for (const w of WAVES) {
  const owner = {}
  for (const n of w) for (const f of byName[n].owned) {
    if (owner[f]) throw new Error(`gk-build: pieces ${owner[f]} and ${n} share ${f} but were placed in the same wave`)
    owner[f] = n
  }
}
const heuristicRoots = PIECES.filter(p => p.owned.some(f => PAGE_ROOT_RE.test(f))).map(p => p.name)
const added = heuristicRoots.filter(n => !planCheck.pageRootPieces.includes(n))
if (added.length) log(`Page-root heuristic added: ${added.join(', ')}`)
const strayRoots = planCheck.pageRootPieces.filter(n => !byName[n])
if (strayRoots.length) log(`Plan check named unknown page-root pieces (dropped): ${strayRoots.join(', ')}`)
const PAGE_ROOT_PIECES = [...new Set([...planCheck.pageRootPieces.filter(n => byName[n]), ...added])]
log(`Plan check ok: ${WAVES.length} wave(s) — ${WAVES.map(w => `[${w.join(', ')}]`).join(' → ')}; page roots: ${PAGE_ROOT_PIECES.join(', ') || 'none'}`)

// ---------------- Implement ----------------
phase('Implement')
const IMPL_SCHEMA = {
  type: 'object',
  properties: {
    name: { type: 'string' },
    filesTouched: { type: 'array', items: { type: 'string' } },
    changelogLine: { type: 'string', description: 'The exact line appended to CHANGELOG.md [Unreleased], or "" if no behaviour changed' },
    testResult: { type: 'string', description: 'Command run and the pass/fail summary line' },
    notes: { type: 'string', description: 'Deviations, page-root changes, anything the seat must know' },
    blockers: { type: 'array', items: { type: 'string' } },
    pageRootChanged: { type: 'boolean' },
  },
  required: ['name', 'filesTouched', 'changelogLine', 'testResult', 'notes', 'blockers'],
}
const implemented = []
// Waves are sequential (later waves build on earlier ones); pieces inside a wave run concurrently.
for (let w = 0; w < WAVES.length; w++) {
  log(`Implement wave ${w + 1}/${WAVES.length}: ${WAVES[w].map(n => (byName[n].hard ? `${n} (hard: ${IMPL_TIER.hard.model} ${IMPL_TIER.hard.effort})` : n)).join(', ')}${PROFILE !== 'fable' || WAVES[w].some(n => byName[n].hard) ? ` — default ${IMPL_TIER.normal.model} ${IMPL_TIER.normal.effort}` : ''}`)
  const res = await pipeline(WAVES[w], (n, _o, i) => {
    const p = byName[n]
    const tier = p.hard ? IMPL_TIER.hard : IMPL_TIER.normal
    return agent(
      `Implement piece "${p.name}" (wave ${w + 1}, slot ${i + 1}) of the plan at ${REPO}/${PLAN} — read the plan first for context, then implement ONLY this piece.
Brief: ${p.brief}
You may edit ONLY these files (create them if missing): ${JSON.stringify(p.owned)} — plus the single CHANGELOG.md line below. Other pieces are editing other files concurrently; never touch them.
Tests: ${p.tests ? `write/update and run ${p.tests}` : 'none named — run the existing tests nearest the touched files, if any'}. Run from the owning package (backend/ or frontend/), e.g. \`set -o pipefail; npx vitest run <file> 2>&1 | tail -40\`, in the FOREGROUND.
CHANGELOG: if app behaviour changed, the ${REPO}/CHANGELOG.md [Unreleased] entry is part of the deliverable — add exactly ONE bullet line in the house form \`- **Added|Changed|Fixed|Removed — <summary>.** <detail>\` (the Keep a Changelog group is the bullet's prefix; read a few existing entries first), British English, no exclamation marks, describing the change itself (never test counts). Put it under this work's \`### <Area — topic>\` heading if one already exists near the top of [Unreleased], else directly below \`## [Unreleased]\`; never add headings. It is the one shared file: re-read it immediately before editing, insert only your line, never rewrite other lines; if the edit fails because the file changed, re-read and retry. Pure refactors/tests/docs need no line (return "").
Failures in files you do not own may come from a concurrent piece: report them in notes, do not fix them.
If you change a page root (a frontend/src/app page/layout, or a component that becomes a page root), say so in notes and set pageRootChanged so the seat runs the scroll audit; page roots use h-full + overflow-y-auto. Report anything you could not do in blockers.${RULES}`,
      { label: `impl:${p.name}`, phase: 'Implement', model: tier.model, effort: tier.effort, schema: IMPL_SCHEMA },
    ).then(r => (r ? { ...r, name: p.name } : null))
  })
  const done = res.filter(Boolean)
  const lost = WAVES[w].filter(n => !done.some(r => r.name === n))
  if (lost.length) log(`Wave ${w + 1}: no result from ${lost.join(', ')} — carried to the judge as missing`)
  const blocked = done.filter(r => r.blockers.length).map(r => r.name)
  if (blocked.length) log(`Wave ${w + 1}: blockers reported by ${blocked.join(', ')}; later waves continue`)
  implemented.push(...done)
}
const MISSING_IMPL = PIECES.map(p => p.name).filter(n => !implemented.some(r => r.name === n))
const OUT_OF_SCOPE = implemented.flatMap(r => r.filesTouched.map(normPathSafe).filter(f => f && f !== 'CHANGELOG.md' && !byName[r.name].owned.includes(f)).map(f => `${r.name}: ${f}`))
const lateRoots = implemented.filter(r => r.pageRootChanged && !PAGE_ROOT_PIECES.includes(r.name)).map(r => r.name)
if (lateRoots.length) { PAGE_ROOT_PIECES.push(...lateRoots); log(`Implementers reported page-root changes: ${lateRoots.join(', ')} — added to page roots`) }
if (OUT_OF_SCOPE.length) log(`Out-of-scope edits reported: ${OUT_OF_SCOPE.join('; ')}`)

// ---------------- Check + Review ----------------
// Barrier: the judge needs the check result and every review together; check and reviews run concurrently.
phase('Check')
log(`Check: "${CHECK}" in ${AFFECTED.join(', ') || '(no backend/frontend files — skipped)'}; Review: ${implemented.length} piece(s)`)
phase('Review') // check and reviews run concurrently; each agent names its own phase group
const fileOwners = f => PIECES.filter(p => p.owned.includes(f)).map(p => p.name)
const [check, reviewsRaw] = await parallel([
  () => AFFECTED.length === 0
    ? Promise.resolve({ passed: true, output: 'skipped: no files under backend/ or frontend/' })
    : agent(
      `Run the project check and report. For each package in ${JSON.stringify(AFFECTED)}: cd ${REPO}/<package> and run \`set -o pipefail; ${CHECK} 2>&1 | tail -40; echo "EXIT=$?"\` in the FOREGROUND (without pipefail, tail hides the failure). If the command is "${DEFAULT_CHECK}" and that package has no typecheck script, run \`set -o pipefail; npx tsc --noEmit 2>&1 | tail -40; echo "EXIT=$?"\` instead and say so. Do not fix anything; edit nothing. passed = every package printed EXIT=0 and showed no errors. output = per package, the command run and its tailed output.${RULES}`,
      {
        label: 'check', phase: 'Check', model: 'haiku', effort: 'low',
        schema: { type: 'object', properties: { passed: { type: 'boolean' }, output: { type: 'string' } }, required: ['passed', 'output'] },
      },
    ),
  () => pipeline(implemented, (r, _o, i) => {
    const p = byName[r.name]
    const shared = p.owned.filter(f => fileOwners(f).length > 1)
    return agent(
      `Review piece "${p.name}" (reviewer ${i + 1}) of the plan at ${REPO}/${PLAN}. Read the plan, then the change: in ${REPO} run \`git diff -- ${shq(p.owned)}\` and \`git status --porcelain -- ${shq(p.owned)}\` (keep the quotes); read any new untracked file in full (git diff omits them); confirm the reported CHANGELOG line is in \`git diff -- CHANGELOG.md\`.
Brief the implementer had: ${p.brief}
Implementer's report: ${JSON.stringify({ testResult: r.testResult, notes: r.notes, blockers: r.blockers, changelogLine: r.changelogLine })}
${shared.length ? `Files shared with other pieces (their edits may also appear): ${shared.join(', ')}.` : ''}
Judge the diff against the brief and the plan: correctness bugs, missed requirements, broken callers, missing/weak tests, a missing or wrong CHANGELOG line for a behaviour change, page roots without h-full + overflow-y-auto, PostgREST list reads without a .range() paging loop. Only real defects; cite file and line. verdict = rework if any finding is high severity or the brief is not met, else accept. Read-only: edit nothing.${RULES}`,
      {
        label: `review:${p.name}`, phase: 'Review', model: 'opus', effort: 'xhigh',
        schema: {
          type: 'object',
          properties: {
            name: { type: 'string' },
            verdict: { type: 'string', enum: ['accept', 'rework'] },
            findings: {
              type: 'array',
              items: {
                type: 'object',
                properties: { file: { type: 'string' }, line: { type: 'number' }, issue: { type: 'string' }, severity: { type: 'string', enum: ['high', 'medium', 'low'] } },
                required: ['file', 'issue', 'severity'],
              },
            },
          },
          required: ['name', 'verdict', 'findings'],
        },
      },
    ).then(v => (v ? { ...v, name: p.name } : null))
  }),
])
const reviews = (reviewsRaw || []).filter(Boolean)
const unreviewed = implemented.map(r => r.name).filter(n => !reviews.some(v => v.name === n))
if (unreviewed.length) log(`No review returned for: ${unreviewed.join(', ')} — passed to the judge as unreviewed`)
log(`Check ${check ? (check.passed ? 'passed' : 'FAILED') : 'returned nothing'}; reviews: ${reviews.filter(v => v.verdict === 'accept').length} accept, ${reviews.filter(v => v.verdict === 'rework').length} rework`)

// ---------------- Judge ----------------
phase('Judge')
const FABLE_JUDGE = ARGS.ui === true || PAGE_ROOT_PIECES.length > 0
log(`Judge on ${FABLE_JUDGE ? 'fable (ui/page-root work)' : ESCALATE ? 'fable (escalated on Carl\'s word)' : 'opus'}`)
const judge = await agent(
  `Decide whether this build ships. Plan: ${REPO}/${PLAN} (read it). Pieces:
${PIECES.map(describe).join('\n')}
Implementation reports: ${JSON.stringify(implemented)}
Pieces with no implementation result: ${JSON.stringify(MISSING_IMPL)}; with no review: ${JSON.stringify(unreviewed)}
Edits reported outside a piece's files: ${JSON.stringify(OUT_OF_SCOPE)}
Project check: ${JSON.stringify(check || { passed: false, output: 'check agent returned nothing' })}
Reviews: ${JSON.stringify(reviews)}
Page-root pieces: ${JSON.stringify(PAGE_ROOT_PIECES)}${ARGS.ui ? ' (this is UI work)' : ''}
Weigh each review finding yourself — spot-check the diff (\`git diff -- <file>\` in ${REPO}) where a finding is disputed or severity is unclear; reviewers can be wrong both ways. ship = false if the check failed, any piece is missing/unreviewed, any confirmed high-severity finding stands, or a behaviour change lacks its CHANGELOG line. blockers = concrete reasons not to ship. reworkPieces = piece names needing another pass. scrollAuditRequired = page routes (e.g. /matters/:id/workflow) or component names the seat must put through the scroll audit — every page-root piece at minimum. Read-only: edit nothing.${RULES}`,
  {
    label: 'judge', phase: 'Judge', model: FABLE_JUDGE || ESCALATE ? 'fable' : 'opus', effort: 'xhigh',
    schema: {
      type: 'object',
      properties: {
        ship: { type: 'boolean' },
        blockers: { type: 'array', items: { type: 'string' } },
        reworkPieces: { type: 'array', items: { type: 'string' } },
        scrollAuditRequired: { type: 'array', items: { type: 'string' } },
      },
      required: ['ship', 'blockers', 'reworkPieces', 'scrollAuditRequired'],
    },
  },
)
log(`Judge: ${judge ? (judge.ship ? 'SHIP' : `HOLD — rework ${judge.reworkPieces.join(', ') || 'none named'}`) : 'returned nothing'}`)

return {
  planCheck: { ...planCheck, pageRootPieces: PAGE_ROOT_PIECES },
  pieces: implemented,
  check: check || { passed: false, output: 'check agent returned nothing' },
  reviews,
  judge: judge || { ship: false, blockers: ['judge agent returned nothing'], reworkPieces: [], scrollAuditRequired: PAGE_ROOT_PIECES },
}
