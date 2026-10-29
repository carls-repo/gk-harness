export const meta = {
  name: 'gk-review',
  description: 'Review a diff, branch range or the working tree before it is believed: scope, four-dimension find, adversarial refute, one judge',
  whenToUse:
    'Run before believing a change (pre-merge to the shared branch, after a build workflow, before sign-off). Requires args {target: string — a git revision range such as "main..HEAD" or "main..feat/x" (branches are shared across worktrees, so a worktree branch is reviewed by naming it), or the literal "working-tree" for the uncommitted tree of the repository (REQUIRED); focus?: string[] — optional extra review dimensions, one finder each; ui?: boolean; security?: boolean; clientFacing?: boolean — any true (or the Scope pass detecting ui/security/client wording) routes the judge to fable under the fable profile; under the opus/sonnet profiles only ui/client-facing work routes to fable and security-only work is judged by opus xhigh; votes?: number — refuters per finding, default 3, integer 1..5; profile?: "fable" | "opus" | "sonnet" — the seat\'s tier profile, default "fable"; escalate?: boolean — route the judge to fable under a non-fable profile on Carl\'s word; project?: object — the repository\'s .claude/gk-project.json plus {root: its git top level}, normally added by the gk-harness hook: guards (rule lines added to every stage brief, or, written as {text, workflows?, stages?}, only to the stages of the named workflows and phases) and reviewFinders ([{key, brief, replaces?}] — a finder that replaces a core one takes its place, others run after the core four); without a root the agents work in the git repository containing their working directory; {} runs the generic rules}. Returns {scope, confirmed, rejected, killedByRefuters: [titles], dedupDropped: number, unjudged: [survivors the judge died on or gave no verdict for]} as JSON; the calling session writes the synthesis.',
  phases: [
    { title: 'Scope', detail: 'one haiku reader classifies the changed files and flags ui/security/client-wording', model: 'haiku' },
    { title: 'Find', detail: 'four sonnet finders (correctness, security, regressions-and-tests, efficiency-and-simplification; a project finder may take one\'s place or run after them) plus one per focus entry; then dedup', model: 'sonnet' },
    { title: 'Verify', detail: '`votes` sonnet refuters per finding; survives on a strict majority not refuting', model: 'sonnet' },
    { title: 'Judge', detail: 'one judge over all survivors: fable xhigh for ui/security/client-facing work, else opus xhigh (under the opus/sonnet profiles fable only for ui/client-facing work or escalate; security-only work is judged by opus xhigh)' },
  ],
}

// `args` may arrive as a raw JSON string depending on the invoking runtime.
const ARGS = typeof args === 'string' ? (() => { try { return JSON.parse(args) } catch (e) { return args } })() : args
const DEFAULT_VOTES = 3

if (!ARGS || typeof ARGS !== 'object') {
  throw new Error('gk-review requires args {target: "main..HEAD" | "working-tree", focus?: string[], ui?: boolean, security?: boolean, clientFacing?: boolean, votes?: 1..5, profile?: "fable" | "opus" | "sonnet", escalate?: boolean, project?: object}')
}
const target = ARGS.target
if (typeof target !== 'string' || !target.trim()) {
  throw new Error('gk-review: args.target is required — a git revision range such as "main..HEAD" or the literal "working-tree"')
}
// The target lands in shell commands inside agent prompts: allow only ref-shaped text.
if (target !== 'working-tree' && (!/^[A-Za-z0-9][A-Za-z0-9._\/~^@{}-]*(\.\.\.?[A-Za-z0-9][A-Za-z0-9._\/~^@{}-]*)?$/.test(target) || target.includes('..-'))) {
  throw new Error(`gk-review: args.target ${JSON.stringify(target)} is not a plain git revision range (e.g. "main..HEAD") or "working-tree"`)
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

// ================= gk-project block: BEGIN (copy verbatim; only WF differs) =================
// Every gk-* workflow carries this same block (scripts cannot import): the args.project
// validator (gk-project.json schema v1, plus the root the hook adds) and the guard selection
// that builds each stage's "Project rules:" block.
//
// The project is absent, or an object whose known fields have the v1 shapes; anything else,
// null included, stops the run before any agent starts. Unknown top-level fields are ignored.
// To run the generic rules inside a project repository, pass project: {} (the hook adds one
// only when args has no project key).
//
// A guard is one printable line, which reaches every stage of every workflow, or an object
// {text, workflows?, stages?}: text is that line; workflows lists workflow names and stages
// lists phase titles, both matched exactly, and the guard reaches only the stages whose
// workflow and phase both match (a list that is absent does not restrict). A guard object with
// any other key, or with an empty list, is malformed: either would silently widen or empty
// the guard's reach.
const WF = 'gk-review'
const projectMalformed = (field) => { throw new Error(`${WF}: args.project is malformed (${field})`) }
const PROJECT = (() => {
  const p = ARGS.project
  if (p === undefined) return null
  const isObject = (v) => v !== null && typeof v === 'object' && !Array.isArray(v)
  const isText = (v) => typeof v === 'string' && v.trim() !== ''
  // One printable line: no C0 or C1 control character, no DEL, no Unicode line or paragraph separator.
  const isLine = (v) => isText(v) && !/[\u0000-\u001f\u007f-\u009f\u2028\u2029]/.test(v)
  const list = (field, check) => {
    if (p[field] === undefined) return []
    if (!Array.isArray(p[field])) projectMalformed(field)
    p[field].forEach((v, i) => check(v, `${field}[${i}]`))
    return p[field]
  }
  if (!isObject(p)) projectMalformed('not an object')
  if (p.root !== undefined && !isLine(p.root)) projectMalformed('root')
  if (p.name !== undefined && !isText(p.name)) projectMalformed('name')
  const guards = list('guards', (g, at) => {
    if (typeof g === 'string') { if (!isLine(g)) projectMalformed(at); return }
    if (!isObject(g)) projectMalformed(at)
    for (const k of Object.keys(g)) if (!['text', 'workflows', 'stages'].includes(k)) projectMalformed(`${at}.${k}`)
    if (!isLine(g.text)) projectMalformed(`${at}.text`)
    for (const k of ['workflows', 'stages']) {
      if (g[k] !== undefined && (!Array.isArray(g[k]) || !g[k].length || !g[k].every(isLine))) projectMalformed(`${at}.${k}`)
    }
  }).map(g => (typeof g === 'string'
    ? { text: g, workflows: null, stages: null }
    : { text: g.text, workflows: g.workflows || null, stages: g.stages || null }))
  const reviewFinders = list('reviewFinders', (f, at) => {
    if (!isObject(f)) projectMalformed(at)
    if (!isLine(f.key)) projectMalformed(`${at}.key`)   // the key becomes the finder's agent label
    if (!isText(f.brief)) projectMalformed(`${at}.brief`)
    if (f.replaces !== undefined && !isText(f.replaces)) projectMalformed(`${at}.replaces`)
  })
  const signOff = list('signOff', (c, at) => {
    if (!isObject(c)) projectMalformed(at)
    if (!isText(c.key)) projectMalformed(`${at}.key`)
    for (const k of ['sharedFile', 'trigger', 'implementer', 'reviewer', 'seat']) if (c[k] !== undefined && !isText(c[k])) projectMalformed(`${at}.${k}`)
    if (c.uiJudge !== undefined && typeof c.uiJudge !== 'boolean') projectMalformed(`${at}.uiJudge`)
  })
  return { root: p.root === undefined ? null : p.root, name: p.name === undefined ? null : p.name, guards, reviewFinders, signOff }
})()
const ROOT = PROJECT ? PROJECT.root : null
const GUARDS = PROJECT ? PROJECT.guards : []
log(PROJECT && (PROJECT.name || PROJECT.guards.length || PROJECT.reviewFinders.length || PROJECT.signOff.length)
  ? `Project: ${PROJECT.name || '(unnamed)'} (${PROJECT.guards.length} guards, ${PROJECT.reviewFinders.length} finders, ${PROJECT.signOff.length} sign-off checks)`
  : 'Project: none (generic rules)')
const REPO_TEXT = ROOT
  ? `the git repository at ${ROOT}`
  : 'the git repository containing your working directory (run `git rev-parse --show-toplevel` first)'
// Guard selection: the guard lines that reach one stage of this workflow (stage = the phase
// title), and the "Project rules:" block that closes that stage's brief ('' when none reach it).
const stageGuards = (stage) => GUARDS
  .filter(g => (!g.workflows || g.workflows.includes(WF)) && (!g.stages || g.stages.includes(stage)))
  .map(g => g.text)
const projectRules = (stage) => {
  const lines = stageGuards(stage)
  return lines.length ? `\nProject rules:\n${lines.map(t => `- ${t}`).join('\n')}` : ''
}
// ================= gk-project block: END =================

const WT = target === 'working-tree'
const DIFF_CMDS = WT
  ? '`git diff --stat HEAD`, `git diff HEAD` (staged and unstaged together) and `git status --short`'
  : `\`git diff --stat ${target}\` and \`git diff ${target}\``
// For a range, the files on disk are whatever the working tree has checked out, which need not
// be the reviewed tip; agents that read them see the wrong version. A single revision is
// diffed against the working tree, so its new version is the one on disk. A project whose
// checkout is known to be on another branch says so in a guard (Goodricke does).
const TIP = WT || !target.includes('..') ? null : (target.split(/\.{2,3}/).pop() || 'HEAD')
const READ_AT_TIP = !TIP
  ? 'The files on disk are the version under review.'
  : `The files on disk may not be the reviewed version (the working tree can be on another branch). Read the new version of a file with \`git show ${TIP}:<path>\`, search it with \`git grep <pattern> ${TIP}\`, and list files with \`git ls-tree -r --name-only ${TIP}\`. If you truly need a tree on disk (e.g. to run a test), use \`git worktree add --detach <a fresh temporary folder outside the repository> ${TIP}\` and remove it when done.`
const COMMON = `
Repository: ${REPO_TEXT}; run every command there. Review target: ${WT ? 'the uncommitted working tree' : `the git range ${target}`}; read it with ${DIFF_CMDS}. ${READ_AT_TIP}
READ-ONLY: do not edit, create or delete any file in the repository's working tree; do not commit, stash, checkout or reset. Never change the repository's working tree, index, HEAD or branches: no git checkout, switch, stash, reset, restore, clean, merge, rebase or bisect, not even \`checkout --detach\`. If you run a test, run it in the FOREGROUND and pipe output through \`tail -40\`. Stay inside the diff and the code it touches — no scope expansion. Your final output is consumed by a script, not a human: return only the structured result.`
// Every stage brief ends with COMMON and then the project rules that reach that stage.

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
    file: { type: 'string' }, line: { type: 'number', description: 'Line in the new version; 0 when the finding is file-level (e.g. a missing test)' }, title: { type: 'string' },
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

// The core finders, in this order. A project finder with `replaces: K` takes K's place (same
// position, its own key); the other project finders run after the core ones, and the caller's
// focus finders last. Built before any agent runs, so a bad `replaces` stops the run at once.
const CORE_FINDERS = [
  { key: 'correctness', brief: 'logic errors, wrong conditions, off-by-one, null/undefined paths, broken async/await, wrong types at runtime, mismatched API contracts between backend routes and frontend callers.' },
  { key: 'security', brief: 'injection, authorisation on every mutating path, secrets in code or logs, unsafe deserialisation, missing input validation at trust boundaries.' },
  { key: 'regressions-and-tests', brief: 'behaviour the change breaks for existing callers, removed or weakened tests, new behaviour with no test, tests that assert rule text rather than measured outcomes.' },
  { key: 'efficiency-and-simplification', brief: 'needless work in hot paths, N+1 queries, repeated LLM calls, duplicated logic that an existing helper already covers, dead code, over-complicated control flow.' },
]
const PROJECT_DIMENSIONS = (() => {
  const dims = CORE_FINDERS.map(d => ({ ...d }))
  const added = []
  ;(PROJECT ? PROJECT.reviewFinders : []).forEach((f, i) => {
    const d = { key: f.key.trim(), brief: f.brief.trim(), from: i }
    if (f.replaces === undefined) { added.push(d); return }
    const at = dims.findIndex(x => x.from === undefined && x.key === f.replaces.trim())
    if (at < 0) projectMalformed(`reviewFinders[${i}].replaces`)   // no such core finder, or already replaced
    dims[at] = d
  })
  const byKey = new Map()
  for (const d of [...dims, ...added]) {
    const first = byKey.get(d.key)
    if (first) projectMalformed(`reviewFinders[${d.from !== undefined ? d.from : first.from}].key`)   // two finders, one label
    byKey.set(d.key, d)
  }
  return [...dims, ...added].map(({ key, brief }) => ({ key, brief }))
})()
// A focus entry whose key a finder already has (or an earlier focus entry) is dropped and
// logged: two finders with one key would make two agent calls with one label.
const FOCUS_DIMENSIONS = (() => {
  const keys = new Set(PROJECT_DIMENSIONS.map(d => d.key))
  const kept = []
  const repeated = []
  for (const f of focus) {
    const key = f.trim()
    if (keys.has(key)) { repeated.push(key); continue }
    keys.add(key)
    kept.push({ key, brief: `the caller's extra focus: ${key}.` })
  }
  if (repeated.length) log(`Focus: dropped ${repeated.length} entr${repeated.length === 1 ? 'y' : 'ies'} whose key a finder already has: ${repeated.join(', ')}`)
  return kept
})()
const DIMENSIONS = [...PROJECT_DIMENSIONS, ...FOCUS_DIMENSIONS]

phase('Scope')
log(`gk-review: ${WT ? 'working tree' : target}; ${votes} refuter(s) per finding; ${FOCUS_DIMENSIONS.length} extra focus dimension(s)`)
const scopeRaw = await agent(
  `Scope a code change for review. Run ${DIFF_CMDS}.
Return every changed file (excluding untracked scratch) with a kind judged from its path: user-interface code (pages, components, views, styles) = ui; database migrations = migration; *.test.* / *.spec.* / test dirs = test; docs/, *.md = docs; other application or library source = backend; anything else = other.
touchesUi: any ui file changed. touchesSecurity: any change to authentication, authorisation or access control, permissions, middleware, session or secret handling, PII handling or audit logging. touchesClientWording: any change to code or text that renders client-visible wording — reports, letters and other generated documents, LLM prompts or rubrics, document templates.${stageGuards('Scope').length ? ' Where the project rules below give this repository\'s own file kinds, or its own touchesUi, touchesSecurity or touchesClientWording rule, use those instead of the matching definitions above (they replace them; they do not add to them).' : ''}
summary: two or three sentences on what the change does.${COMMON}${projectRules('Scope')}`,
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

phase('Find')
// Finders are independent, so they run as a pipeline; awaiting the whole set is
// the BARRIER the dedup below needs (it compares findings across all finders).
const finderRuns = await pipeline(DIMENSIONS, (d, _o, i) =>
  agent(
    `You are finder #${i + 1} on a code review, dimension "${d.key}". Look only for: ${d.brief}
Change summary: ${scope.summary}${FILES}
Read the full diff yourself, then the surrounding code as needed. Report only defects introduced or exposed by this change, each with file, line (in the new version), a short title, the claim, a concrete failure scenario and a severity. An empty list is a valid answer; do not pad.${COMMON}${projectRules('Find')}`,
    { label: `find:${d.key}`, phase: 'Find', model: 'sonnet', effort: 'medium', schema: FINDINGS_SCHEMA },
  ).then(r => (r ? r.findings.map(f => ({ ...f, dimension: d.key })) : null)),
)
const deadFinders = DIMENSIONS.filter((d, i) => !finderRuns[i]).map(d => d.key)
if (deadFinders.length) log(`Find: ${deadFinders.length} finder(s) returned nothing; dimension(s) NOT covered: ${deadFinders.join(', ')}`)
const found = finderRuns.filter(Boolean).flat()

const norm = s => String(s).toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim()
// A finder on Windows may report C:\r\src\a.ts: backslashes become slashes before the root
// prefix (root plus "/", so /tmp/repository is not under /tmp/repo) and a leading ./ go.
const slashes = s => String(s).replace(/\\/g, '/')
const ROOT_PREFIX = ROOT ? slashes(ROOT).replace(/\/+$/, '') + '/' : null
const normFile = s => {
  const f = slashes(s)
  return (ROOT_PREFIX && f.startsWith(ROOT_PREFIX) ? f.slice(ROOT_PREFIX.length) : f).replace(/^\.\//, '')
}
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
Read the diff and the code at that location. Set refuted=false only if you confirmed the defect is real and introduced or exposed by this change; give the reason in one or two sentences.${COMMON}${projectRules('Verify')}`,
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
The findings below each survived ${votes} adversarial refuter(s). For each one, read the diff and the code yourself and decide: confirmed (real, caused or exposed by this change) or rejected. For confirmed findings keep every field, set the severity you judge correct, and give a concrete minimal fix (what to change, where). For rejected ones give the title and the reason. Return exactly one verdict per finding and keep each title verbatim. Weigh client-visible wording and security invariants heavily.

Findings (JSON):
${JSON.stringify(survivors, null, 2)}${COMMON}${projectRules('Judge')}`,
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
