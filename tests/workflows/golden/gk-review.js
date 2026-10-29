export const meta = {
  name: 'gk-review',
  description: 'Review a diff, branch range or the working tree before it is believed: scope, four-dimension find, adversarial refute, one judge',
  whenToUse:
    'Run before believing a change (pre-merge to develop, after a build workflow, before sign-off). Requires args {target: string — a git revision range such as "develop..HEAD" or "develop..feat/x" (branches are shared across worktrees, so a worktree branch is reviewed by naming it), or the literal "working-tree" for the uncommitted tree of /home/carls/apps/mike-dev (REQUIRED); focus?: string[] — optional extra review dimensions, one finder each; ui?: boolean; security?: boolean; clientFacing?: boolean — any true (or the Scope pass detecting ui/security/client wording) routes the judge to fable under the fable profile; under the opus/sonnet profiles only ui/client-facing work routes to fable and security-only work is judged by opus xhigh; votes?: number — refuters per finding, default 3, integer 1..5; profile?: "fable" | "opus" | "sonnet" — the seat\'s tier profile, default "fable"; escalate?: boolean — route the judge to fable under a non-fable profile on Carl\'s word}. Returns {scope, confirmed, rejected, killedByRefuters: [titles], dedupDropped: number, unjudged: [survivors the judge died on or gave no verdict for]} as JSON; the calling session writes the synthesis.',
  phases: [
    { title: 'Scope', detail: 'one haiku reader classifies the changed files and flags ui/security/client-wording', model: 'haiku' },
    { title: 'Find', detail: 'four sonnet finders (correctness, security-and-invariants, regressions-and-tests, efficiency-and-simplification) plus one per focus entry; then dedup', model: 'sonnet' },
    { title: 'Verify', detail: '`votes` sonnet refuters per finding; survives on a strict majority not refuting', model: 'sonnet' },
    { title: 'Judge', detail: 'one judge over all survivors: fable xhigh for ui/security/client-facing work, else opus xhigh (under the opus/sonnet profiles fable only for ui/client-facing work or escalate; security-only work is judged by opus xhigh)' },
  ],
}

// `args` may arrive as a raw JSON string depending on the invoking runtime.
const ARGS = typeof args === 'string' ? (() => { try { return JSON.parse(args) } catch (e) { return args } })() : args
const DEFAULT_VOTES = 3
const REPO = '/home/carls/apps/mike-dev'

if (!ARGS || typeof ARGS !== 'object') {
  throw new Error('gk-review requires args {target: "develop..HEAD" | "working-tree", focus?: string[], ui?: boolean, security?: boolean, clientFacing?: boolean, votes?: 1..5, profile?: "fable" | "opus" | "sonnet", escalate?: boolean}')
}
const target = ARGS.target
if (typeof target !== 'string' || !target.trim()) {
  throw new Error('gk-review: args.target is required — a git revision range such as "develop..HEAD" or the literal "working-tree"')
}
// The target lands in shell commands inside agent prompts: allow only ref-shaped text.
if (target !== 'working-tree' && (!/^[A-Za-z0-9][A-Za-z0-9._\/~^@{}-]*(\.\.\.?[A-Za-z0-9][A-Za-z0-9._\/~^@{}-]*)?$/.test(target) || target.includes('..-'))) {
  throw new Error(`gk-review: args.target ${JSON.stringify(target)} is not a plain git revision range (e.g. "develop..HEAD") or "working-tree"`)
}
const focus = ARGS.focus === undefined ? [] : ARGS.focus
if (!Array.isArray(focus) || focus.some(f => typeof f !== 'string' || !f.trim())) {
  throw new Error('gk-review: args.focus must be an array of non-empty strings when given')
}
for (const k of ['ui', 'security', 'clientFacing']) {
  if (ARGS[k] !== undefined && typeof ARGS[k] !== 'boolean') throw new Error(`gk-review: args.${k} must be a boolean when given`)
}
const votes = ARGS.votes === undefined ? DEFAULT_VOTES : ARGS.votes
if (!Number.isInteger(votes) || votes < 1 || votes > 5) {
  throw new Error(`gk-review: args.votes must be an integer 1..5 (got ${JSON.stringify(ARGS.votes)})`)
}

// ---------------- Profile (three-seat policy, 2026-10-13) ----------------
// 'fable' = Mode A (default, unchanged behaviour); 'opus' = Mode B; 'sonnet' = Mode C.
const PROFILE = ARGS.profile === undefined ? 'fable' : ARGS.profile
if (!['fable', 'opus', 'sonnet'].includes(PROFILE)) throw new Error(`gk-review: args.profile must be one of fable|opus|sonnet (got ${JSON.stringify(ARGS.profile)})`)
if (ARGS.escalate !== undefined && typeof ARGS.escalate !== 'boolean') throw new Error('gk-review: args.escalate, when given, must be a boolean')
const ESCALATE = ARGS.escalate === true  // Carl's word: route the apex judge to fable under a non-fable profile
const APEX = PROFILE === 'fable' ? 'fable' : 'opus'   // the top of the ladder for this profile
log(`Profile: ${PROFILE} (apex ${APEX}${ESCALATE ? ', escalated to fable on Carl\'s word' : ''})`)

const WT = target === 'working-tree'
const DIFF_CMDS = WT
  ? '`git diff --stat HEAD`, `git diff HEAD` (staged and unstaged together) and `git status --short`'
  : `\`git diff --stat ${target}\` and \`git diff ${target}\``
// For a range, the files on disk are the shared checkout's branch, not the reviewed tip — agents
// that read them see the wrong version and have reached for `git checkout` to fix it.
const TIP = WT ? null : (target.split(/\.{2,3}/).pop() || 'HEAD')
const READ_AT_TIP = WT
  ? 'The files on disk are the version under review.'
  : `The files on disk are NOT the reviewed version (${REPO} is a shared checkout on another branch). Read the new version of a file with \`git show ${TIP}:<path>\`, search it with \`git grep <pattern> ${TIP}\`, and list files with \`git ls-tree -r --name-only ${TIP}\`. If you truly need a tree on disk (e.g. to run a test), use \`git worktree add --detach <a fresh dir under /tmp> ${TIP}\` and remove it when done.`
const COMMON = `
Repository: ${REPO} (run every command there). Review target: ${WT ? 'the uncommitted working tree' : `the git range ${target}`}; read it with ${DIFF_CMDS}. ${READ_AT_TIP}
NEVER change the checkout of ${REPO}: no git checkout, switch, stash, reset, restore, clean, merge, rebase or bisect, not even \`checkout --detach\`. Other sessions and the running dev stack use that working tree; changing it breaks them.
READ-ONLY: do not edit, create or delete any file; do not commit. Ignore the repo's many untracked scratch scripts (backend/scripts/_*, scripts/demo/_*, root-level scratch files) unless the diff itself adds them as part of the change. If you run a test, run it in the FOREGROUND and pipe output through \`tail -40\`. Any database check is dev only; never prod (never source a prod .env). Stay inside the diff and the code it touches — no scope expansion. Your final output is consumed by a script, not a human: return only the structured result.`

const SCOPE_SCHEMA = {
  type: 'object',
  properties: {
    files: { type: 'array', items: { type: 'object', properties: { path: { type: 'string' }, kind: { type: 'string', enum: ['ui', 'backend', 'migration', 'docs', 'test', 'other'] } }, required: ['path', 'kind'] } },
    touchesUi: { type: 'boolean' },
    touchesSecurity: { type: 'boolean' },
    touchesClientWording: { type: 'boolean' },
    summary: { type: 'string', description: 'Two or three sentences: what the change does' },
  },
  required: ['files', 'touchesUi', 'touchesSecurity', 'touchesClientWording', 'summary'],
}
const FINDING = {
  type: 'object',
  properties: {
    file: { type: 'string' }, line: { type: 'number', description: 'Line in the new version; 0 when the finding is file-level (e.g. a missing CHANGELOG entry)' }, title: { type: 'string' },
    claim: { type: 'string' }, failureScenario: { type: 'string', description: 'Concrete inputs/state leading to the wrong outcome' },
    severity: { type: 'string', enum: ['high', 'medium', 'low'] },
  },
  required: ['file', 'line', 'title', 'claim', 'failureScenario', 'severity'],
}
const FINDINGS_SCHEMA = { type: 'object', properties: { findings: { type: 'array', items: FINDING } }, required: ['findings'] }
const VOTE_SCHEMA = { type: 'object', properties: { refuted: { type: 'boolean' }, reason: { type: 'string' } }, required: ['refuted', 'reason'] }
const JUDGE_SCHEMA = {
  type: 'object',
  properties: {
    confirmed: { type: 'array', items: { type: 'object', properties: { ...FINDING.properties, fix: { type: 'string' } }, required: [...FINDING.required, 'fix'] } },
    rejected: { type: 'array', items: { type: 'object', properties: { title: { type: 'string' }, reason: { type: 'string' } }, required: ['title', 'reason'] } },
  },
  required: ['confirmed', 'rejected'],
}

phase('Scope')
log(`gk-review: ${WT ? 'working tree' : target}; ${votes} refuter(s) per finding; ${focus.length} extra focus dimension(s)`)
const scopeRaw = await agent(
  `Scope a code change for review. Run ${DIFF_CMDS}.
Return every changed file (excluding untracked scratch) with a kind judged from its path: frontend/src/app and frontend/src/components = ui; backend/migrations = migration; *.test.ts / *.spec.* / test dirs = test; docs/, *.md = docs; other backend/src = backend; anything else = other.
touchesUi: any ui file changed. touchesSecurity: any change under backend/src/lib/docreview*, auth / mcpAuth / middleware, deid / deident, permissions / accessControl / access.ts, ethicalWalls, audit. touchesClientWording: any change to code or text that renders client-visible wording — reports (docreviewReport*, report stores), engagement letters/packs (engagement*), LLM prompts or rubrics, document templates.
summary: two or three sentences on what the change does.${COMMON}`,
  { label: 'scope', phase: 'Scope', model: 'haiku', effort: 'high', schema: SCOPE_SCHEMA },
)
// A dead Scope agent must not sink the review: the finders read the diff themselves.
// An unknown scope cannot rule out ui/security/client wording, so the judge keeps the fable route.
const scope = scopeRaw || { files: [], touchesUi: true, touchesSecurity: true, touchesClientWording: true, summary: '(unavailable: the Scope agent returned nothing; read the diff yourself)', unavailable: true }
log(scopeRaw
  ? `Scope: ${scope.files.length} file(s); ui=${scope.touchesUi} security=${scope.touchesSecurity} clientWording=${scope.touchesClientWording}`
  : 'Scope: agent returned nothing; reviewing without a file list, judge on the fable route')
if (scopeRaw && !scope.files.length) {
  log('Scope: no changed files; nothing to review')
  return { scope, confirmed: [], rejected: [], killedByRefuters: [], dedupDropped: 0, unjudged: [] }
}
// Untracked new files are invisible to `git diff HEAD`, so the finders get the scoped list.
const FILES = scope.files.length
  ? `\nChanged files (from the Scope pass): ${scope.files.map(f => `${f.path} [${f.kind}]`).join(', ')}.${WT ? ' Any of these that git status shows as untracked (??) are new files of the change: read them in full.' : ''}`
  : ''

const DIMENSIONS = [
  { key: 'correctness', brief: 'logic errors, wrong conditions, off-by-one, null/undefined paths, broken async/await, wrong types at runtime, mismatched API contracts between backend routes and frontend callers.' },
  { key: 'security-and-invariants', brief: 'the Goodricke invariants: ethical walls enforced on every document/matter read; Principal / canAct checks on every mutating route; audit rows written exactly once per action (never zero, never twice on retry); PII and client identities never logged or sent unmasked past the de-id gateway; PostgREST silently caps .select() at 1000 rows, so any list read that can grow needs a .range() paging loop; supabase-js stays pinned at exact 2.102.1.' },
  { key: 'regressions-and-tests', brief: 'behaviour the change breaks for existing callers, removed or weakened tests, new behaviour with no test, tests that assert rule text rather than measured outcomes, a page root that lost h-full + overflow-y-auto, a behavioural change with no CHANGELOG.md [Unreleased] entry.' },
  { key: 'efficiency-and-simplification', brief: 'needless work in hot paths, N+1 queries, repeated LLM calls, duplicated logic that an existing helper already covers, dead code, over-complicated control flow.' },
  ...focus.map(f => ({ key: f.trim(), brief: `the caller's extra focus: ${f.trim()}.` })),
]

phase('Find')
// Finders are independent, so they run as a pipeline; awaiting the whole set is
// the BARRIER the dedup below needs (it compares findings across all finders).
const finderRuns = await pipeline(DIMENSIONS, (d, _o, i) =>
  agent(
    `You are finder #${i + 1} on a code review, dimension "${d.key}". Look only for: ${d.brief}
Change summary: ${scope.summary}${FILES}
Read the full diff yourself, then the surrounding code as needed. Report only defects introduced or exposed by this change, each with file, line (in the new version), a short title, the claim, a concrete failure scenario and a severity. An empty list is a valid answer; do not pad.${COMMON}`,
    { label: `find:${d.key}`, phase: 'Find', model: 'sonnet', effort: 'medium', schema: FINDINGS_SCHEMA },
  ).then(r => (r ? r.findings.map(f => ({ ...f, dimension: d.key })) : null)),
)
const deadFinders = DIMENSIONS.filter((d, i) => !finderRuns[i]).map(d => d.key)
if (deadFinders.length) log(`Find: ${deadFinders.length} finder(s) returned nothing; dimension(s) NOT covered: ${deadFinders.join(', ')}`)
const found = finderRuns.filter(Boolean).flat()

const norm = s => String(s).toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim()
const normFile = s => String(s).replace(/^\/home\/carls\/apps\/mike-dev\//, '').replace(/^\.\//, '')
const seen = new Set()
const deduped = found.filter(f => {
  const k = `${normFile(f.file)}|${f.line}|${norm(f.title)}`
  if (seen.has(k)) return false
  seen.add(k)
  return true
})
const dedupDropped = found.length - deduped.length
log(`Find: ${found.length} raw finding(s) from ${DIMENSIONS.length - deadFinders.length}/${DIMENSIONS.length} finder(s); dedup dropped ${dedupDropped}; ${deduped.length} go to Verify`)
if (!deduped.length) return { scope, confirmed: [], rejected: [], killedByRefuters: [], dedupDropped, unjudged: [] }

phase('Verify')
const LENSES = ['does it actually reproduce', 'is the code path reachable in practice', 'is it already handled elsewhere', 'is the claimed line/behaviour read correctly', 'is the severity justified']
const verdicts = (await pipeline(deduped, (f, _o, i) =>
  parallel(Array.from({ length: votes }, (_, v) => () =>
    agent(
      `You are refuter ${v + 1}/${votes} for finding #${i + 1}. Your job is to REFUTE this review finding; default to refuted=true if uncertain. Lens: ${LENSES[v % LENSES.length]}.
Finding (${f.dimension}, ${f.severity}): ${f.title}
File: ${f.file}:${f.line}
Claim: ${f.claim}
Failure scenario: ${f.failureScenario}
Read the diff and the code at that location. Set refuted=false only if you confirmed the defect is real and introduced or exposed by this change; give the reason in one or two sentences.${COMMON}`,
      { label: `refute:${i + 1}.${v + 1}`, phase: 'Verify', model: 'sonnet', effort: 'medium', schema: VOTE_SCHEMA },
    ),
  )).then(vs => {
    const ok = vs.filter(Boolean)
    const upheld = ok.filter(x => !x.refuted).length
    // Strict majority of the requested votes; a dead refuter counts as a refutation.
    return { finding: f, survives: upheld * 2 > votes, upheld, dead: votes - ok.length }
  }),
)).filter(Boolean)

const survivors = verdicts.filter(v => v.survives).map(v => ({ ...v.finding, upheldVotes: v.upheld }))
const killedByRefuters = verdicts.filter(v => !v.survives).map(v => v.finding.title)
const deadVotes = verdicts.reduce((n, v) => n + v.dead, 0)
const lost = deduped.length - verdicts.length
log(`Verify: ${survivors.length} survived, ${killedByRefuters.length} killed by refuters${deadVotes ? `; ${deadVotes} refuter(s) died and counted as refutations` : ''}${lost ? `; ${lost} finding(s) lost to a failed verify stage` : ''}`)
if (!survivors.length) return { scope, confirmed: [], rejected: [], killedByRefuters, dedupDropped, unjudged: [] }

phase('Judge')
const signOff = !!(ARGS.ui || ARGS.clientFacing || scope.touchesUi || scope.touchesClientWording)   // UI sign-off rule, all profiles
const securityOnly = !signOff && !!(ARGS.security || scope.touchesSecurity)
const judgeModel = (PROFILE === 'fable' && (signOff || securityOnly)) || signOff || ESCALATE ? 'fable' : 'opus'
if (securityOnly && judgeModel !== 'fable') log('Security-sensitive scope judged by opus xhigh under the ' + PROFILE + ' profile — escalate to fable on Carl\'s word (args.escalate: true)')
const judgeRoute = signOff ? ' (ui/client-facing sign-off route)'
  : securityOnly && judgeModel === 'fable' ? (PROFILE === 'fable' ? ' (security route)' : ' (security route, escalated on Carl\'s word)')
  : securityOnly ? ' (security route, judged by opus under the ' + PROFILE + ' profile)'
  : ESCALATE && judgeModel === 'fable' ? ' (escalated on Carl\'s word)'
  : ''
log(`Judge: ${judgeModel} xhigh over ${survivors.length} survivor(s)${judgeRoute}`)
const judged = await agent(
  `You are the final judge on a code review. Change summary: ${scope.summary}
The findings below each survived ${votes} adversarial refuter(s). For each one, read the diff and the code yourself and decide: confirmed (real, caused or exposed by this change) or rejected. For confirmed findings keep every field, set the severity you judge correct, and give a concrete minimal fix (what to change, where). For rejected ones give the title and the reason. Return exactly one verdict per finding and keep each title verbatim. Weigh client-visible wording, ethical walls, de-id and audit invariants heavily.

Findings (JSON):
${JSON.stringify(survivors, null, 2)}${COMMON}`,
  { label: 'judge', phase: 'Judge', model: judgeModel, effort: 'xhigh', schema: JUDGE_SCHEMA },
)
// A dead judge must not discard the verified survivors: hand them back unjudged.
if (!judged) {
  log(`Judge: agent returned nothing; ${survivors.length} survivor(s) returned unjudged`)
  return { scope, confirmed: [], rejected: [], killedByRefuters, dedupDropped, unjudged: survivors }
}
const decided = new Set([...judged.confirmed, ...judged.rejected].map(x => norm(x.title)))
const unjudged = survivors.filter(s => !decided.has(norm(s.title)))
log(`Judge: ${judged.confirmed.length} confirmed, ${judged.rejected.length} rejected${unjudged.length ? `; ${unjudged.length} survivor(s) matched no verdict by title (left out or retitled): ${unjudged.map(s => s.title).join('; ')}` : ''}`)

return { scope, confirmed: judged.confirmed, rejected: judged.rejected, killedByRefuters, dedupDropped, unjudged }
