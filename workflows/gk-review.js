export const meta = {
  name: 'gk-review',
  description: 'Review a diff, branch range or the working tree before it is believed: scope, four-dimension find, adversarial refute, one judge',
  whenToUse:
    'Run before believing a change (pre-merge to the shared branch, after a build workflow, before sign-off). Requires args {target: string — a git revision range such as "main..HEAD" or "main..feat/x" (branches are shared across worktrees, so a worktree branch is reviewed by naming it), or the literal "working-tree" for the uncommitted tree of the repository (REQUIRED); focus?: string[] — optional extra review dimensions, one finder each; ui?: boolean; security?: boolean; clientFacing?: boolean — any true makes the change sensitive, which puts the judge on fable high; so does a changed path (not a test file) that matches the project\'s uiPaths or a sign-off check\'s paths, or the Scope pass detecting ui, security or client wording (the Scope pass only raises it); votes?: number — refuters per finding, default 3, integer 1..5; author?: string — the tier that wrote the change (e.g. "fable-medium"), the judge\'s floor; seat?: string — the seat\'s tier, the floor when author is not given (normally added by the gk-harness hook); changedPaths?: string[] — the target\'s changed paths relative to the top level (normally added by the gk-harness hook); dials?: string[] — routing dials: "fable" puts the judge on fable, "nofable" moves a fable judge to opus xhigh (each stage\'s model and effort come from routes.json; a judge that dies on fable runs once more on opus xhigh); project?: object — the repository\'s .claude/gk-project.json plus {root: its git top level}, normally added by the gk-harness hook: guards (rule lines added to every stage brief, or, written as {text, workflows?, stages?}, only to the stages of the named workflows and phases), reviewFinders ([{key, brief, replaces?}] — a finder that replaces a core one takes its place, others run after the core four) and uiPaths (regular expressions over changed paths that raise the judge); without a root the agents work in the git repository containing their working directory; {} runs the generic rules}. Returns {scope, confirmed, rejected, killedByRefuters: [titles], dedupDropped: number, unjudged: [survivors the judge died on or gave no verdict for], routing: {routes, dials, offTier}} as JSON; the calling session writes the synthesis.',
  phases: [
    { title: 'Scope', detail: 'one haiku reader classifies the changed files and flags ui/security/client-wording', model: 'haiku' },
    { title: 'Find', detail: 'four sonnet finders (correctness, security, regressions-and-tests, efficiency-and-simplification; a project finder may take one\'s place or run after them) plus one per focus entry; then dedup', model: 'sonnet' },
    { title: 'Verify', detail: '`votes` sonnet refuters per finding; survives on a strict majority not refuting', model: 'sonnet' },
    { title: 'Judge', detail: 'one judge over all survivors: fable high for sensitive work (ui, security or client wording, by flag, changed path or the Scope pass), else opus high, never below the author\'s tier; opus xhigh under nofable' },
  ],
}
// gk-harness:args-v2

// `args` may arrive as a raw JSON string depending on the invoking runtime.
const ARGS = typeof args === 'string' ? (() => { try { return JSON.parse(args) } catch (e) { return args } })() : args
const DEFAULT_VOTES = 3

if (!ARGS || typeof ARGS !== 'object') {
  throw new Error('gk-review requires args {target: "main..HEAD" | "working-tree", focus?: string[], ui?: boolean, security?: boolean, clientFacing?: boolean, votes?: 1..5, author?: string, seat?: string, changedPaths?: string[], dials?: string[], project?: object}')
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
// The target's changed paths, which the gk-harness hook adds; they feed the judge's floor.
if (ARGS.changedPaths !== undefined && (!Array.isArray(ARGS.changedPaths) || !ARGS.changedPaths.every(f => typeof f === 'string'))) {
  throw new Error('gk-review: args.changedPaths must be an array of strings when given')
}
// author and seat name tiers; one that names no tier is logged and ignored by routeOf.

// ================= gk-project block: BEGIN (copy verbatim; only WF and PHASES differ) =================
// Every gk-* workflow carries this same block (scripts cannot import): the args.project
// validator (gk-project.json schema v1, plus the root the hook adds) and the guard selection
// that builds each stage's "Project rules:" block. WF is this workflow's name and PHASES its
// meta.phases titles, in order.
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
// the guard's reach. A name that can match nothing — a workflow not in KNOWN_WORKFLOWS, or, in
// a guard that names this workflow, a stage that is not one of its PHASES — logs one warning
// line per name, and the guard is kept as written.
//
// A sign-off check is {key, seat, sharedFile?, trigger?, paths?, uiJudge?, implementer?,
// reviewer?}: key and seat are printable lines, the other texts non-empty strings, uiJudge a
// boolean, and paths a non-empty list of regular expressions (printable lines that compile),
// given only with a trigger; any other key is malformed. check is one printable line and
// packages a non-empty list of printable lines. uiPaths is a non-empty list of regular
// expressions, checked like a sign-off check's paths. gk-build uses the sign-off checks, check
// (its default check command) and packages (the folders it runs that check from); gk-review
// uses uiPaths and the sign-off checks' paths (a changed file that is not a test file and
// matches one raises its judge); the other workflows only check their shape.
const WF = 'gk-review'
const PHASES = ['Scope', 'Find', 'Verify', 'Judge']
const KNOWN_WORKFLOWS = ['gk-review', 'gk-build', 'gk-design', 'gk-research', 'gk-diagnose']
const projectMalformed = (field) => { throw new Error(`${WF}: args.project is malformed (${field})`) }
const PROJECT = (() => {
  const p = ARGS.project
  if (p === undefined) return null
  const isObject = (v) => v !== null && typeof v === 'object' && !Array.isArray(v)
  const isText = (v) => typeof v === 'string' && v.trim() !== ''
  // One printable line: no C0 or C1 control character, no DEL, no Unicode line or paragraph separator.
  const isLine = (v) => isText(v) && !/[\u0000-\u001f\u007f-\u009f\u2028\u2029]/.test(v)
  const compiles = (v) => { try { return new RegExp(v) instanceof RegExp } catch (e) { return false } }
  const list = (field, check) => {
    if (p[field] === undefined) return []
    if (!Array.isArray(p[field])) projectMalformed(field)
    p[field].forEach((v, i) => check(v, `${field}[${i}]`))
    return p[field]
  }
  if (!isObject(p)) projectMalformed('not an object')
  if (p.root !== undefined && !isLine(p.root)) projectMalformed('root')
  if (p.name !== undefined && !isText(p.name)) projectMalformed('name')
  if (p.check !== undefined && !isLine(p.check)) projectMalformed('check')
  if (Array.isArray(p.packages) && !p.packages.length) projectMalformed('packages')
  const packages = list('packages', (k, at) => { if (!isLine(k)) projectMalformed(at) })
  if (Array.isArray(p.uiPaths) && !p.uiPaths.length) projectMalformed('uiPaths')
  const uiPaths = list('uiPaths', (s, at) => { if (!isLine(s) || !compiles(s)) projectMalformed(at) })
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
    for (const k of Object.keys(c)) if (!['key', 'seat', 'sharedFile', 'trigger', 'paths', 'uiJudge', 'implementer', 'reviewer'].includes(k)) projectMalformed(`${at}.${k}`)
    for (const k of ['key', 'seat']) if (!isLine(c[k])) projectMalformed(`${at}.${k}`)
    for (const k of ['sharedFile', 'trigger', 'implementer', 'reviewer']) if (c[k] !== undefined && !isText(c[k])) projectMalformed(`${at}.${k}`)
    // A path rule is one more way to meet a trigger, so it needs one.
    if (c.paths !== undefined && (c.trigger === undefined || !Array.isArray(c.paths) || !c.paths.length || !c.paths.every(s => isLine(s) && compiles(s)))) projectMalformed(`${at}.paths`)
    if (c.uiJudge !== undefined && typeof c.uiJudge !== 'boolean') projectMalformed(`${at}.uiJudge`)
  })
  return {
    root: p.root === undefined ? null : p.root,
    name: p.name === undefined ? null : p.name,
    guards,
    reviewFinders,
    signOff,
    check: p.check === undefined ? null : p.check,
    packages: p.packages === undefined ? null : packages,
    uiPaths,
  }
})()
const ROOT = PROJECT ? PROJECT.root : null
const GUARDS = PROJECT ? PROJECT.guards : []
log(PROJECT && (PROJECT.name || PROJECT.guards.length || PROJECT.reviewFinders.length || PROJECT.signOff.length)
  ? `Project: ${PROJECT.name || '(unnamed)'} (${PROJECT.guards.length} guards, ${PROJECT.reviewFinders.length} finders, ${PROJECT.signOff.length} sign-off checks)`
  : 'Project: none (generic rules)')
GUARDS.forEach((g, i) => {
  for (const w of g.workflows || []) if (!KNOWN_WORKFLOWS.includes(w)) log(`Project: guard ${i} names unknown workflow '${w}'`)
  if (g.workflows && g.workflows.includes(WF)) for (const s of g.stages || []) if (!PHASES.includes(s)) log(`Project: guard ${i} names unknown stage '${s}'`)
})
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
// <gk-routes v2> generated by gkh gen from routes.json v2; do not edit
const ROUTES = {
  "version": 2,
  "tiers": {
    "haiku-low": {"model": "haiku", "effort": "low", "cap": [1, 1]},
    "haiku-medium": {"model": "haiku", "effort": "medium", "cap": [1, 2]},
    "sonnet-medium": {"model": "sonnet", "effort": "medium", "cap": [2, 2]},
    "sonnet-high": {"model": "sonnet", "effort": "high", "cap": [2, 3]},
    "opus-medium": {"model": "opus", "effort": "medium", "cap": [3, 2]},
    "opus-high": {"model": "opus", "effort": "high", "cap": [3, 3]},
    "opus-xhigh": {"model": "opus", "effort": "xhigh", "cap": [3, 4]},
    "fable-medium": {"model": "fable", "effort": "medium", "cap": [4, 2]},
    "fable-high": {"model": "fable", "effort": "high", "cap": [4, 3]}
  },
  "stages": {
    "gk-review.scope": {"tier": "haiku-medium"},
    "gk-review.find": {"tier": "sonnet-medium"},
    "gk-review.refute": {"tier": "sonnet-medium"},
    "gk-review.judge": {"tier": "opus-high", "when": {"sensitive": "fable-high"}, "authorFloor": true, "noFable": "opus-xhigh", "onFail": "rerun", "apex": true}
  }
}
// The routing helper, identical in every gk workflow (gkh/templates/routes-helper.js). It reads
// ROUTES (above) and ARGS.dials; the only tiers it names itself are ROUTE_FLOOR and ROUTE_APEX.
//   routeOf(stage, ctx) -> {tier, model, effort, off}, ctx = {cls, ui, sensitive, author, implementer}:
//     1. the stage's tier, or its byClass[ctx.cls || 'judgement'];
//     2. a `when` condition whose ctx flag is true sets the tier (the highest, if several);
//     3. atLeast: the highest of the tier, ctx.implementer and ROUTE_FLOOR;
//     4. authorFloor: ctx.author, when it is above ROUTE_FLOOR and above the tier;
//     5. the fable dial puts an apex stage on ROUTE_APEX (under nofable too: rule 6 then moves
//        it to its noFable tier, so nofable wins over fable; every apex stage has one);
//     6. the nofable dial moves a Fable tier to the stage's noFable (its class entry for a
//        byClass stage), off 'nofable'; noFable "none" gives tier null: the stage does not run.
//   runStage(stage, ctx, prompt, opts) runs the stage's agent on its route and applies onFail
//     when the agent returns null: rerun (once, on the noFable tier, only from a Fable tier),
//     unfinished (null) or hold (null, recorded).
//   routingResult() -> {routes, dials, offTier}: every stage that ran off its tier, or did not run.
const ROUTE_FLOOR = 'opus-high'
const ROUTE_APEX = 'fable-high'
const KNOWN_DIALS = ['fable', 'nofable', 'deep', 'solo']
const DIALS = (() => {
  const given = ARGS !== null && typeof ARGS === 'object' ? ARGS.dials : undefined
  if (given === undefined) return []
  if (!Array.isArray(given)) {
    log(`routes: args.dials is not a list; no dials apply (got ${JSON.stringify(given)})`)
    return []
  }
  const kept = []
  for (const d of given) {
    if (KNOWN_DIALS.includes(d)) { if (!kept.includes(d)) kept.push(d) } else log(`routes: unknown dial ${JSON.stringify(d)} dropped`)
  }
  return kept
})()
const offTier = []
const routeTier = (name) => {
  const t = ROUTES.tiers[name]
  if (!t) throw new Error(`routes: tier ${JSON.stringify(name)} is not in this workflow's routes block`)
  return t
}
// a above b by capability: model rank, then effort.
const routeAbove = (a, b) => {
  const x = routeTier(a).cap
  const y = routeTier(b).cap
  return x[0] !== y[0] ? x[0] > y[0] : x[1] > y[1]
}
const routeHigher = (a, b) => (routeAbove(b, a) ? b : a)
// A tier name the caller passed, or null when it names no tier in this block (logged).
const routeKnown = (name, what) => {
  if (name === undefined || name === null) return null
  if (typeof name === 'string' && ROUTES.tiers[name]) return name
  log(`routes: ${what} ${JSON.stringify(name)} is not a tier this workflow knows; ignored`)
  return null
}
function routeStage(stage) {
  const s = ROUTES.stages[stage]
  if (!s) throw new Error(`routes: unknown stage ${JSON.stringify(stage)}`)
  return s
}
// The stage's noFable entry for this class: a tier, 'none', or undefined.
function routeNoFable(s, cls) {
  return s.noFable !== null && typeof s.noFable === 'object' ? s.noFable[cls] : s.noFable
}
function routeOf(stage, ctx = {}) {
  const s = routeStage(stage)
  const c = ctx || {}
  const cls = c.cls || 'judgement'
  let tier = s.tier === 'byClass' ? (s.byClass || {})[cls] : s.tier
  if (!tier) throw new Error(`routes: stage ${stage} has no tier for class ${JSON.stringify(cls)}`)
  routeTier(tier)
  const met = Object.entries(s.when || {}).filter(([cond]) => c[cond] === true).map(([, t]) => t)
  if (met.length) tier = met.reduce(routeHigher)
  if (s.atLeast === 'implementer') {
    const impl = routeKnown(c.implementer, 'ctx.implementer')
    tier = routeHigher(routeHigher(tier, ROUTE_FLOOR), impl || tier)
  }
  if (s.authorFloor === true) {
    const author = routeKnown(c.author, 'ctx.author')
    if (author && routeAbove(author, ROUTE_FLOOR)) tier = routeHigher(tier, author)
  }
  const nofable = DIALS.includes('nofable')
  if (DIALS.includes('fable') && s.apex === true) tier = ROUTE_APEX
  let off = null
  if (nofable && routeTier(tier).model === 'fable') {
    const nf = routeNoFable(s, cls)
    if (nf === 'none') return { tier: null, model: null, effort: null, off: 'nofable' }
    if (!nf) throw new Error(`routes: stage ${stage} runs on ${tier} and has no noFable tier`)
    tier = nf
    off = 'nofable'
  }
  const t = routeTier(tier)
  return { tier, model: t.model, effort: t.effort, off }
}
async function runStage(stage, ctx, prompt, opts = {}) {
  const s = routeStage(stage)
  const cls = (ctx && ctx.cls) || 'judgement'
  const label = opts.label
  const r = routeOf(stage, ctx)
  if (r.off) offTier.push({ stage, label, tier: r.tier, reason: r.off })
  if (r.tier === null) return null
  const result = await agent(prompt, { ...opts, model: r.model, effort: r.effort })
  if (result !== null && result !== undefined) return result
  if (s.onFail === 'hold') {
    offTier.push({ stage, label, tier: r.tier, reason: 'hold' })
    return null
  }
  const nf = routeNoFable(s, cls)
  if (s.onFail !== 'rerun' || r.model !== 'fable' || !nf || nf === 'none') return null
  const t = routeTier(nf)
  offTier.push({ stage, label, tier: nf, reason: 'onFail' })
  return agent(prompt, { ...opts, model: t.model, effort: t.effort })
}
const routingResult = () => ({ routes: ROUTES.version, dials: DIALS.slice(), offTier: offTier.slice() })
// </gk-routes>
// profile and escalate are retired: every stage takes its model and effort from ROUTES.
if (ARGS.profile !== undefined || ARGS.escalate !== undefined) log(`profile is retired; routes come from routes.json v${ROUTES.version}`)

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

// A finder on Windows may report C:\r\src\a.ts: backslashes become slashes before the root
// prefix (root plus "/", so /tmp/repository is not under /tmp/repo) and a leading ./ go.
const slashes = s => String(s).replace(/\\/g, '/')
const ROOT_PREFIX = ROOT ? slashes(ROOT).replace(/\/+$/, '') + '/' : null
const normFile = s => {
  const f = slashes(s)
  return (ROOT_PREFIX && f.startsWith(ROOT_PREFIX) ? f.slice(ROOT_PREFIX.length) : f).replace(/^\.\//, '')
}

// ---------------- The judge's floor, from code ----------------
// The change is sensitive (routes.json puts a sensitive judge on its `when` tier) when the
// caller says ui, security or clientFacing, when a changed path matches one of the project's
// own path rules (its uiPaths and every sign-off check's paths; there are no built-in patterns),
// or when the Scope pass detects ui, security or client wording. The Scope pass only raises it.
// A test file never counts: TEST_FILE is the Scope pass's own test rule (TEST_KINDS, which its
// prompt states) as code.
const TEST_KINDS = '*.test.* / *.spec.* / test dirs'
const TEST_FILE = /\.(test|spec)\.[^/]*$|(^|\/)(tests?|__tests__)\//
const PATH_RULES = PROJECT ? [...PROJECT.uiPaths, ...PROJECT.signOff.flatMap(c => c.paths || [])].map(s => new RegExp(s)) : []
const PATH_HITS = (ARGS.changedPaths || []).map(normFile).filter(f => !TEST_FILE.test(f) && PATH_RULES.some(re => re.test(f)))
const FLAGGED = ARGS.ui === true || ARGS.security === true || ARGS.clientFacing === true

phase('Scope')
log(`gk-review: ${WT ? 'working tree' : target}; ${votes} refuter(s) per finding; ${FOCUS_DIMENSIONS.length} extra focus dimension(s)`)
const scopeRaw = await runStage('gk-review.scope', {},
  `Scope a code change for review. Run ${DIFF_CMDS}.
Return every changed file (excluding untracked scratch) with a kind judged from its path: user-interface code (pages, components, views, styles) = ui; database migrations = migration; ${TEST_KINDS} = test; docs/, *.md = docs; other application or library source = backend; anything else = other.
touchesUi: any ui file changed. touchesSecurity: any change to authentication, authorisation or access control, permissions, middleware, session or secret handling, PII handling or audit logging. touchesClientWording: any change to code or text that renders client-visible wording — reports, letters and other generated documents, LLM prompts or rubrics, document templates.${stageGuards('Scope').length ? ' Where the project rules below give this repository\'s own file kinds, or its own touchesUi, touchesSecurity or touchesClientWording rule, use those instead of the matching definitions above (they replace them; they do not add to them).' : ''}
summary: two or three sentences on what the change does.${COMMON}${projectRules('Scope')}`,
  { label: 'scope', phase: 'Scope', schema: SCOPE_SCHEMA },
)
// A dead Scope agent must not sink the review: the finders read the diff themselves.
// An unknown scope cannot rule out ui/security/client wording, so the judge is sensitive.
const scope = scopeRaw || { files: [], touchesUi: true, touchesSecurity: true, touchesClientWording: true, summary: '(unavailable: the Scope agent returned nothing; read the diff yourself)', unavailable: true }
log(scopeRaw
  ? `Scope: ${scope.files.length} file(s); ui=${scope.touchesUi} security=${scope.touchesSecurity} clientWording=${scope.touchesClientWording}`
  : 'Scope: agent returned nothing; reviewing without a file list, judge on the fable route')
if (scopeRaw && !scope.files.length) {
  log('Scope: no changed files; nothing to review')
  return { scope, confirmed: [], rejected: [], killedByRefuters: [], dedupDropped: 0, unjudged: [], routing: routingResult() }
}
// Untracked new files are invisible to `git diff HEAD`, so the finders get the scoped list.
const FILES = scope.files.length
  ? `\nChanged files (from the Scope pass): ${scope.files.map(f => `${f.path} [${f.kind}]`).join(', ')}.${WT ? ' Any of these that git status shows as untracked (??) are new files of the change: read them in full.' : ''}`
  : ''

phase('Find')
// Finders are independent, so they run as a pipeline; awaiting the whole set is
// the BARRIER the dedup below needs (it compares findings across all finders).
const finderRuns = await pipeline(DIMENSIONS, (d, _o, i) =>
  runStage('gk-review.find', {},
    `You are finder #${i + 1} on a code review, dimension "${d.key}". Look only for: ${d.brief}
Change summary: ${scope.summary}${FILES}
Read the full diff yourself, then the surrounding code as needed. Report only defects introduced or exposed by this change, each with file, line (in the new version), a short title, the claim, a concrete failure scenario and a severity. An empty list is a valid answer; do not pad.${COMMON}${projectRules('Find')}`,
    { label: `find:${d.key}`, phase: 'Find', schema: FINDINGS_SCHEMA },
  ).then(r => (r ? r.findings.map(f => ({ ...f, dimension: d.key })) : null)),
)
const deadFinders = DIMENSIONS.filter((d, i) => !finderRuns[i]).map(d => d.key)
if (deadFinders.length) log(`Find: ${deadFinders.length} finder(s) returned nothing; dimension(s) NOT covered: ${deadFinders.join(', ')}`)
const found = finderRuns.filter(Boolean).flat()

const norm = s => String(s).toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim()
const seen = new Set()
const deduped = found.filter(f => {
  const k = `${normFile(f.file)}|${f.line}|${norm(f.title)}`
  if (seen.has(k)) return false
  seen.add(k)
  return true
})
const dedupDropped = found.length - deduped.length
log(`Find: ${found.length} raw finding(s) from ${DIMENSIONS.length - deadFinders.length}/${DIMENSIONS.length} finder(s); dedup dropped ${dedupDropped}; ${deduped.length} go to Verify`)
if (!deduped.length) return { scope, confirmed: [], rejected: [], killedByRefuters: [], dedupDropped, unjudged: [], routing: routingResult() }

phase('Verify')
const LENSES = ['does it actually reproduce', 'is the code path reachable in practice', 'is it already handled elsewhere', 'is the claimed line/behaviour read correctly', 'is the severity justified']
const verdicts = (await pipeline(deduped, (f, _o, i) =>
  parallel(Array.from({ length: votes }, (_, v) => () =>
    runStage('gk-review.refute', {},
      `You are refuter ${v + 1}/${votes} for finding #${i + 1}. Your job is to REFUTE this review finding; default to refuted=true if uncertain. Lens: ${LENSES[v % LENSES.length]}.
Finding (${f.dimension}, ${f.severity}): ${f.title}
File: ${f.file}:${f.line}
Claim: ${f.claim}
Failure scenario: ${f.failureScenario}
Read the diff and the code at that location. Set refuted=false only if you confirmed the defect is real and introduced or exposed by this change; give the reason in one or two sentences.${COMMON}${projectRules('Verify')}`,
      { label: `refute:${i + 1}.${v + 1}`, phase: 'Verify', schema: VOTE_SCHEMA },
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
if (!survivors.length) return { scope, confirmed: [], rejected: [], killedByRefuters, dedupDropped, unjudged: [], routing: routingResult() }

phase('Judge')
const detected = scope.touchesUi === true || scope.touchesSecurity === true || scope.touchesClientWording === true
const sensitive = FLAGGED || PATH_HITS.length > 0 || detected
if (PATH_HITS.length) log(`Judge floor: ${PATH_HITS.length} changed path(s) match the project's path rules: ${PATH_HITS.join(', ')}`)
// The route's reason, for the log: ui or client wording first, then security, then a path rule.
const uiRoute = ARGS.ui === true || ARGS.clientFacing === true || scope.touchesUi === true || scope.touchesClientWording === true
const securityRoute = ARGS.security === true || scope.touchesSecurity === true
const judgeRoute = uiRoute ? ' (ui/client-facing sign-off route)' : securityRoute ? ' (security route)' : PATH_HITS.length ? ' (project path route)' : ''
// The author's tier is the judge's floor: args.author, else the seat's tier.
const author = ARGS.author || ARGS.seat
const judgeRouted = routeOf('gk-review.judge', { sensitive, author })
log(`Judge: ${judgeRouted.model} ${judgeRouted.effort} over ${survivors.length} survivor(s)${judgeRoute}`)
// routeOf has logged an author that names no tier; runStage routes again, so it gets the author
// only when it names one (the same route, and the line once).
const judged = await runStage('gk-review.judge', { sensitive, author: Object.keys(ROUTES.tiers).includes(author) ? author : null },
  `You are the final judge on a code review. Change summary: ${scope.summary}
The findings below each survived ${votes} adversarial refuter(s). For each one, read the diff and the code yourself and decide: confirmed (real, caused or exposed by this change) or rejected. For confirmed findings keep every field, set the severity you judge correct, and give a concrete minimal fix (what to change, where). For rejected ones give the title and the reason. Return exactly one verdict per finding and keep each title verbatim. Weigh client-visible wording and security invariants heavily.

Findings (JSON):
${JSON.stringify(survivors, null, 2)}${COMMON}${projectRules('Judge')}`,
  { label: 'judge', phase: 'Judge', schema: JUDGE_SCHEMA },
)
// A dead judge must not discard the verified survivors: hand them back unjudged.
if (!judged) {
  log(`Judge: agent returned nothing; ${survivors.length} survivor(s) returned unjudged`)
  return { scope, confirmed: [], rejected: [], killedByRefuters, dedupDropped, unjudged: survivors, routing: routingResult() }
}
const decided = new Set([...judged.confirmed, ...judged.rejected].map(x => norm(x.title)))
const unjudged = survivors.filter(s => !decided.has(norm(s.title)))
log(`Judge: ${judged.confirmed.length} confirmed, ${judged.rejected.length} rejected${unjudged.length ? `; ${unjudged.length} survivor(s) matched no verdict by title (left out or retitled): ${unjudged.map(s => s.title).join('; ')}` : ''}`)

return { scope, confirmed: judged.confirmed, rejected: judged.rejected, killedByRefuters, dedupDropped, unjudged, routing: routingResult() }
