export const meta = {
  name: 'gk-build',
  description: 'Build a multi-file change from a plan split into pieces with disjoint file sets: plan check, wave-ordered implement, project check, per-piece review, judge',
  whenToUse: 'Use when a written plan/spec is already decomposed into pieces that each own a disjoint set of files. Requires args {plan: "<path to the plan/spec file>" (REQUIRED), pieces: [{name: "<unique piece name>", files: ["src/...", "tests/..."], brief: "<what this piece must do>", tests?: "<test file path (the piece owns it, in any language) or test command>", class?: "spec"|"judgement"|"hard"|"security" (default "judgement": the class picks the implementer\'s tier from routes.json; hard: true is retired, read as class "hard" for one version)}] (REQUIRED, non-empty), check?: "<project-wide check command; default the project\'s check, and with neither the check is skipped>", ui?: true|false, dials?: string[] — routing dials: "fable" puts the plan check and the judge on fable high, "nofable" moves every fable stage to its noFable tier (each stage\'s model and effort come from routes.json; a plan check, review or judge that dies on fable runs once more on opus xhigh; an implementer that dies is returned unfinished, never re-run), project?: object — the repository\'s .claude/gk-project.json plus {root: its git top level}, normally added by the gk-harness hook: guards (rule lines added to every stage brief, or, written as {text, workflows?, stages?}, only to the stages of the named workflows and phases), check (the default check command), packages (folders relative to the top level: the check runs from each one that holds a file some piece owns, and passes without running when none does; without packages it runs once from the top level; implementers run tests from the owning package, else from the top level) and signOff ([{key, seat, sharedFile?, trigger?, paths?, uiJudge?, implementer?, reviewer?}] — the checks the seat runs before sign-off: each implementer may add its one line to each sharedFile, implementer texts go into the implement brief and reviewer texts onto the reviewers\' list, a trigger names the work that needs its check, paths (regular expressions) meet the trigger for every piece that owns a file whose top-level-relative path matches one, and a check that applies (no trigger, or a met one) and sets uiJudge routes the judge to Fable); without a root the agents work in the git repository containing their working directory; {} runs the generic rules}. The plan check confirms each piece\'s class; a disagreement is returned in classDisagreements, never applied. Paths are relative to the repository\'s top level. Edits land uncommitted in the working tree; returns {planCheck, pieces, failed: [{name, class, stage, tier, status: "unfinished"}], check, seatMustRerun, seatMustRerunIn, reviews, judge, classDisagreements: [{name, given, proposed, reason}], signOff: [{key, seat, pieces}], routing: {routes, dials, offTier}} — check is {passed, ran, exitCode, tail} when it ran (passed is exitCode 0, whatever the agent says), {passed: null, skipped: true, output} when there is no check command, {passed: false, output} when its agent returned nothing; seatMustRerun is the check command (null when no check ran) and seatMustRerunIn the package folders the check ran from, in its order (null without packages, meaning the top level, and null when no check ran). The seat re-runs seatMustRerun itself before sign-off, from each folder in seatMustRerunIn (or the top level when it is null), applying the project\'s Check-stage guards; a failed piece is re-run by the seat on the next rung; a sign-off check with no trigger lists every piece, a triggered one the pieces that met it — and the calling session commits, runs each signOff entry\'s seat check for its pieces, and writes the summary.',
  phases: [
    { title: 'Plan check', detail: 'one agent verifies the decomposition, orders pieces into waves, confirms each piece\'s class and names the pieces that meet each project sign-off trigger (fable medium; opus xhigh under nofable)', model: 'fable' },
    { title: 'Implement', detail: 'waves run in sequence; pieces within a wave run in parallel, one agent per piece on its class tier (spec sonnet medium, judgement opus medium, hard and security fable medium, opus high under nofable); a piece whose agent dies comes back unfinished', model: 'opus' },
    { title: 'Check', detail: 'one Haiku agent runs the check command from each affected project package, or once from the top level when the project names no packages, and reports the command, its exit code and the last 40 lines; passed is exit code 0; skipped with no command', model: 'haiku' },
    { title: 'Review', detail: 'one reviewer per piece over git diff of its files: opus high, or the piece\'s class tier when higher (opus xhigh in place of fable under nofable)', model: 'opus' },
    { title: 'Judge', detail: 'one judge over reviews, check and sign-off triggers: fable medium, fable high if ui or an applying sign-off check (no trigger, or a met one) sets uiJudge; opus xhigh under nofable' },
  ],
}
// gk-harness:args-v2

// args may arrive as a raw JSON string depending on the invoking runtime.
const ARGS = typeof args === 'string' ? (() => { try { return JSON.parse(args) } catch (e) { return args } })() : args

if (!ARGS || typeof ARGS !== 'object') throw new Error('gk-build requires args {plan, pieces:[{name, files, brief, tests?, class?: spec|judgement|hard|security}], check?, ui?, dials?, project?}')
if (typeof ARGS.plan !== 'string' || !ARGS.plan.trim()) throw new Error('gk-build: args.plan (path to the plan/spec file) is required')
if (!Array.isArray(ARGS.pieces) || ARGS.pieces.length === 0) throw new Error('gk-build: args.pieces must be a non-empty array of {name, files, brief, tests?, class?}')
if (ARGS.check !== undefined && (typeof ARGS.check !== 'string' || !ARGS.check.trim())) throw new Error('gk-build: args.check, when given, must be a non-empty command string')
if (ARGS.ui !== undefined && typeof ARGS.ui !== 'boolean') throw new Error('gk-build: args.ui, when given, must be a boolean')

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
const WF = 'gk-build'
const PHASES = ['Plan check', 'Implement', 'Check', 'Review', 'Judge']
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
    "sonnet-medium": {"model": "sonnet", "effort": "medium", "cap": [2, 2]},
    "opus-medium": {"model": "opus", "effort": "medium", "cap": [3, 2]},
    "opus-high": {"model": "opus", "effort": "high", "cap": [3, 3]},
    "opus-xhigh": {"model": "opus", "effort": "xhigh", "cap": [3, 4]},
    "fable-medium": {"model": "fable", "effort": "medium", "cap": [4, 2]},
    "fable-high": {"model": "fable", "effort": "high", "cap": [4, 3]}
  },
  "stages": {
    "gk-build.plan-check": {"tier": "fable-medium", "noFable": "opus-xhigh", "onFail": "rerun", "apex": true},
    "gk-build.implement": {"tier": "byClass", "byClass": {"spec": "sonnet-medium", "judgement": "opus-medium", "hard": "fable-medium", "security": "fable-medium"}, "noFable": {"hard": "opus-high", "security": "opus-high"}, "onFail": "unfinished"},
    "gk-build.check": {"tier": "haiku-low"},
    "gk-build.review": {"tier": "opus-high", "atLeast": "implementer", "noFable": "opus-xhigh", "onFail": "rerun"},
    "gk-build.judge": {"tier": "fable-medium", "when": {"ui": "fable-high"}, "noFable": "opus-xhigh", "onFail": "rerun", "apex": true}
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
// A dial is a bare word ('nofable'); one leading '#' ('#nofable') is stripped first.
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
  for (const raw of given) {
    // A dial typed as in Carl's prompt ('#nofable') counts as the bare word.
    const d = typeof raw === 'string' && raw.startsWith('#') ? raw.slice(1) : raw
    if (KNOWN_DIALS.includes(d)) { if (!kept.includes(d)) kept.push(d) } else log(`routes: unknown dial ${JSON.stringify(raw)} dropped`)
  }
  return kept
})()
const offTier = []
// An own entry of a routes table, or undefined: a prototype key ('constructor', '__proto__',
// 'toString') names no tier, stage or class.
const routeOwn = (table, key) => (table !== null && typeof table === 'object' && Object.prototype.hasOwnProperty.call(table, key) ? table[key] : undefined)
const routeTier = (name) => {
  const t = routeOwn(ROUTES.tiers, name)
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
  if (typeof name === 'string' && routeOwn(ROUTES.tiers, name)) return name
  log(`routes: ${what} ${JSON.stringify(name)} is not a tier this workflow knows; ignored`)
  return null
}
function routeStage(stage) {
  const s = routeOwn(ROUTES.stages, stage)
  if (!s) throw new Error(`routes: unknown stage ${JSON.stringify(stage)}`)
  return s
}
// The stage's noFable entry for this class: a tier, 'none', or undefined.
function routeNoFable(s, cls) {
  return s.noFable !== null && typeof s.noFable === 'object' ? routeOwn(s.noFable, cls) : s.noFable
}
function routeOf(stage, ctx = {}) {
  const s = routeStage(stage)
  const c = ctx || {}
  const cls = c.cls || 'judgement'
  let tier = s.tier === 'byClass' ? routeOwn(s.byClass, cls) : s.tier
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

// ---------------- Paths ----------------
// Paths are relative to the repository's top level. Backslashes become slashes (git's separator
// on every OS), then a path under the root loses the root ("root/", so /tmp/repository is not
// under /tmp/repo).
const slashes = s => String(s).replace(/\\/g, '/')
const ROOT_PREFIX = ROOT ? slashes(ROOT).replace(/\/+$/, '') + '/' : null
const inRepo = rel => (ROOT_PREFIX ? ROOT_PREFIX + rel : rel)
const ROOT_DIR = ROOT || 'the repository\'s top level'
const normPath = p => {
  if (typeof p !== 'string' || !p.trim()) throw new Error(`gk-build: invalid file path ${JSON.stringify(p)}`)
  let q = slashes(p.trim())
  if (ROOT_PREFIX && q.startsWith(ROOT_PREFIX)) q = q.slice(ROOT_PREFIX.length)
  if (q.startsWith('/') || /^[A-Za-z]:/.test(q) || q.startsWith('-') || /(^|\/)\.\.(\/|$)/.test(q)) throw new Error(`gk-build: file path ${JSON.stringify(p)} must be inside ${ROOT || 'the repository'} with no traversal`)
  if (/['\n\r]/.test(q)) throw new Error(`gk-build: file path ${JSON.stringify(p)} must not contain quotes or newlines`)
  return q.replace(/^\.\//, '')
}
const normPathSafe = f => { try { return normPath(f) } catch (e) { return f } }
// The file a tests value names, or null when it names none. A file has no whitespace and its
// last segment ends in a file extension: a.test.ts, tests/test_a.py, pkg/foo_test.go. A pytest
// node id names its file (tests/test_a.py::test_x is tests/test_a.py). A folder (tests/unit,
// tests/, ./...) or a test command (npm test, pytest -q, go test ./...) names none.
const testFileOf = t => { const q = slashes(t.trim()).replace(/::.*$/, ''); return !/\s/.test(q) && /\w\.\w+$/.test(q.slice(q.lastIndexOf('/') + 1)) ? q : null }
// Route groups such as frontend/src/app/(pages)/ are a bash syntax error unquoted.
const shq = list => list.map(f => `'${f}'`).join(' ')

// ---------------- Sign-off checks ----------------
// The project's sign-off checks (args.project.signOff) are what the seat runs before sign-off:
// a check with no trigger for every piece, a triggered one for the pieces that met its trigger.
// Each implementer may add its one line to each sharedFile; implementer texts go into the
// implement brief and reviewer texts onto the reviewers' list; a met trigger whose check sets
// uiJudge (or a check with no trigger that sets it) routes the judge to fable. A trigger is met
// by the pieces the agents name, and by every piece that owns a file whose path (relative to
// the top level, with forward slashes) matches one of its check's paths.
const SIGN_OFF = (PROJECT ? PROJECT.signOff : []).map((c, i) => {
  let sharedFile = null
  if (c.sharedFile !== undefined) {
    try { sharedFile = normPath(c.sharedFile) } catch (e) { projectMalformed(`signOff[${i}].sharedFile`) }
  }
  const text = k => (c[k] === undefined ? null : c[k].trim())
  const paths = (c.paths || []).map(s => new RegExp(s))
  return { key: c.key.trim(), seat: c.seat.trim(), sharedFile, trigger: text('trigger'), paths, uiJudge: c.uiJudge === true, implementer: text('implementer'), reviewer: text('reviewer') }
})
// Agents name a met trigger by its key, so keys are unique.
SIGN_OFF.forEach((c, i) => { if (SIGN_OFF.findIndex(x => x.key === c.key) < i) projectMalformed(`signOff[${i}].key`) })
const SHARED_FILES = [...new Set(SIGN_OFF.filter(c => c.sharedFile).map(c => c.sharedFile))]
const TRIGGERS = SIGN_OFF.filter(c => c.trigger)
const TRIGGER_KEYS = TRIGGERS.map(c => c.key)
const TRIGGER_LIST = TRIGGERS.map(c => `- ${c.key}: ${c.trigger}`).join('\n')
// A reviewer text is one item of the reviewers' list.
const reviewerItem = c => c.reviewer.replace(/\.$/, '')
const KEYED_PIECES = {
  type: 'array',
  items: { type: 'object', properties: { key: { type: 'string', enum: TRIGGER_KEYS }, pieces: { type: 'array', items: { type: 'string' } } }, required: ['key', 'pieces'] },
}
const PATH_CHECKS = SIGN_OFF.filter(c => c.paths.length)

// ---------------- Check command and packages ----------------
// The check command is args.check, else the project's check; with neither, the check is
// skipped. With packages (folders relative to the top level), the check runs from each package
// that holds a file some piece owns, and passes without running (output says skipped) when none
// does; without packages it runs once from the top level.
const PACKAGES = PROJECT && PROJECT.packages
  ? [...new Set(PROJECT.packages.map((k, i) => {
    let q = ''
    try { q = normPath(k).replace(/\/+$/, '') } catch (e) { projectMalformed(`packages[${i}]`) }
    if (!q || q === '.') projectMalformed(`packages[${i}]`)
    return q
  }))]
  : null
const CHECK = ARGS.check || (PROJECT && PROJECT.check) || null

// A piece's class picks its implementer's tier (routes.json gk-build.implement byClass); with no
// class a piece is judgement. The review gets the class tier before any dial as its implementer
// (ruling R4), so a hard piece's review is at least fable medium (opus xhigh under nofable).
const CLASSES = ['spec', 'judgement', 'hard', 'security']
const classTier = cls => ROUTES.stages['gk-build.implement'].byClass[cls]

let PLAN
try { PLAN = normPath(ARGS.plan) } catch (e) { throw new Error(`gk-build: args.plan — ${e.message}`) }
const PIECES = ARGS.pieces.map((p, i) => {
  if (!p || typeof p.name !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(p.name)) throw new Error(`gk-build: pieces[${i}].name must be a plain identifier`)
  if (!Array.isArray(p.files) || p.files.length === 0) throw new Error(`gk-build: pieces[${i}] (${p.name}).files must be a non-empty array`)
  if (typeof p.brief !== 'string' || !p.brief.trim()) throw new Error(`gk-build: pieces[${i}] (${p.name}).brief is required`)
  if (p.tests !== undefined && typeof p.tests !== 'string') throw new Error(`gk-build: pieces[${i}] (${p.name}).tests must be a string`)
  if (p.hard !== undefined && typeof p.hard !== 'boolean') throw new Error(`gk-build: pieces[${i}] (${p.name}).hard, when given, must be a boolean`)
  if (p.hard !== undefined && p.class !== undefined) throw new Error(`gk-build: pieces[${i}] (${p.name}) gives both hard and class; give class only`)
  if (p.class !== undefined && !CLASSES.includes(p.class)) throw new Error(`gk-build: pieces[${i}] (${p.name}).class must be one of ${CLASSES.join('|')} (got ${JSON.stringify(p.class)})`)
  // hard is retired: accepted for one version, read as class hard (hard: false is the default).
  if (p.hard !== undefined) log(`pieces[${i}].hard is retired; use class`)
  const cls = p.class !== undefined ? p.class : p.hard === true ? 'hard' : 'judgement'
  const files = p.files.map(normPath)
  // The file a tests value names (testFileOf), in any language, is owned by the piece like its
  // files (and, like them, must lie inside the repository); a folder or a command is not.
  const tf = p.tests ? testFileOf(p.tests) : null
  const owned = tf ? [...new Set([...files, normPath(tf)])] : files
  return { name: p.name, files, owned, brief: p.brief, tests: p.tests || '', cls }
})
const byName = Object.fromEntries(PIECES.map(p => [p.name, p]))
if (Object.keys(byName).length !== PIECES.length) throw new Error('gk-build: piece names must be unique')
const NAMES = PIECES.map(p => p.name)
// A file's package is the longest package folder it lies under (web/admin/x.ts is web/admin's,
// not web's).
const pkgOf = f => PACKAGES.filter(k => f.startsWith(k + '/')).reduce((a, k) => (a && a.length >= k.length ? a : k), null)
const AFFECTED = PACKAGES ? PACKAGES.filter(k => PIECES.some(p => p.owned.some(f => pkgOf(f) === k))) : []
const orList = xs => (xs.length > 1 ? `${xs.slice(0, -1).join(', ')} or ${xs[xs.length - 1]}` : xs.join(''))
// The check result when the check agent does not run, or null when it runs. With no command no
// check exists, so the result says skipped (passed: null, never a pass) and the judge is told to
// judge without it. With packages and no piece in any, no file the check covers changed, so it
// passes without running, as the golden's did.
const NO_COMMAND = CHECK ? null : 'no check command (args.check is not given and the project names none)'
const CHECK_SKIPPED = NO_COMMAND ? { passed: null, skipped: true, output: `skipped: ${NO_COMMAND}` }
  : PACKAGES && !AFFECTED.length ? { passed: true, output: `skipped: no files under ${orList(PACKAGES.map(k => `${k}/`))}` }
  : null
// Where implementers run tests: from the owning package when the project names packages, else
// from the top level, with the piece's own test command (no test runner is assumed).
const TESTS_FROM = PACKAGES
  ? `from the package that owns them (${orList(PACKAGES.map(k => `${k}/`))}; the top level when none does)`
  : 'from the repository\'s top level'

const WORK_IN = ROOT
  ? `Work in ${REPO_TEXT} (no worktree).`
  : `Work in ${REPO_TEXT}, not in a worktree; the paths in this brief are relative to its top level.`
const RULES = `
Rules: your final text is consumed by a script, not a human. ${WORK_IN} Do NOT commit, stash, reset or checkout. Any test or build runs in the FOREGROUND with output piped through \`tail -40\` under \`set -o pipefail\` (so the exit status survives tail), never in the background. Do not expand scope.`
// Every stage brief ends with RULES and then the project rules that reach that stage.

const describe = p => `- ${p.name}: files ${JSON.stringify(p.owned)}${p.tests ? `; tests: ${p.tests}` : ''}\n  brief: ${p.brief}`

// ---------------- Plan check ----------------
phase('Plan check')
log(`Plan check over ${PIECES.length} pieces; ${PACKAGES ? `affected packages: ${AFFECTED.join(', ') || 'none'}` : `check: ${CHECK ? 'from the top level' : 'none'}`}`)
const SHARED_PLAN = !SHARED_FILES.length ? ''
  : SHARED_FILES.length === 1 ? ` (plus one appended ${SHARED_FILES[0]} line)`
  : ` (plus one appended line in each of ${SHARED_FILES.join(', ')})`
// The plan check confirms each piece's class; the seat decides on a disagreement, so none is applied.
const CLASS_TEXT = `Piece classes given (each picks its implementer's tier): ${PIECES.map(p => `${p.name}: ${p.cls}`).join(', ')}. classes = for each piece, {name, class, agree, reason}: class is the class you judge right for it — spec (a scoped change from an exact spec: a known-cause fix, tests for existing behaviour, CRUD), judgement (a feature or change that needs judgement; the default), hard (hard reasoning: a subtle correctness, concurrency or cross-cutting change) or security (security-sensitive code: authentication, access control, secrets, PII); agree = it is the class given; reason = one sentence. A disagreement goes to the seat and changes nothing here.`
const planCheckRaw = await runStage('gk-build.plan-check', {},
  `Verify the decomposition of a build plan before parallel implementation.
Plan/spec file: ${inRepo(PLAN)} — read it in full. Pieces:
${PIECES.map(describe).join('\n')}

Check: (1) each piece is self-contained — its brief can be implemented editing only its listed files${SHARED_PLAN}; (2) file sets are DISJOINT — if two pieces share a file, or one piece depends on another's output, put them in different waves in dependency order; (3) together the pieces cover the plan. Return ok=false with concrete problems only for real defects (missing files, an unimplementable split, plan coverage gaps); an overlap fixable by ordering is not a problem. order = waves, each a list of piece names; every piece appears exactly once; pieces in one wave run concurrently. ${CLASS_TEXT}${TRIGGERS.length ? ` triggered = for each sign-off trigger below, the pieces whose work meets it, as {key, pieces} (leave out a trigger no piece meets), so the seat runs that check:\n${TRIGGER_LIST}\n` : ' '}Read-only: edit nothing.${RULES}${projectRules('Plan check')}`,
  {
    label: 'plan-check', phase: 'Plan check',
    schema: {
      type: 'object',
      properties: {
        ok: { type: 'boolean' },
        problems: { type: 'array', items: { type: 'string' } },
        order: { type: 'array', items: { type: 'array', items: { type: 'string' } } },
        classes: {
          type: 'array',
          items: {
            type: 'object',
            properties: { name: { type: 'string' }, class: { type: 'string', enum: CLASSES }, agree: { type: 'boolean' }, reason: { type: 'string' } },
            required: ['name', 'class', 'agree', 'reason'],
          },
        },
        ...(TRIGGERS.length ? { triggered: KEYED_PIECES } : {}),
      },
      required: ['ok', 'problems', 'order', 'classes', ...(TRIGGERS.length ? ['triggered'] : [])],
    },
  },
)
// The plan check as the seat gets it back: the agent's reply less its classes, which come back
// as classDisagreements.
const planCheck = planCheckRaw && (({ classes, ...rest }) => rest)(planCheckRaw)
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
// Sign-off triggers met, by key: the plan check names pieces now, the path rules add theirs
// next, the implementers and the judge theirs later. Each key lists its pieces in the order
// they were met, as the golden listed page roots: the plan check's first, then the rest. addMet
// takes an agent's [{key, pieces}] and returns what it had to drop.
const met = new Map(TRIGGER_KEYS.map(k => [k, new Set()]))
const metPieces = key => [...met.get(key)]
const metList = () => TRIGGER_KEYS.map(key => ({ key, pieces: metPieces(key) })).filter(t => t.pieces.length)
const addMet = list => {
  const dropped = []
  for (const t of Array.isArray(list) ? list : []) {
    const key = t && t.key
    if (!met.has(key)) { dropped.push(String(key)); continue }
    for (const n of Array.isArray(t.pieces) ? t.pieces : []) {
      if (NAMES.includes(n)) met.get(key).add(n)
      else dropped.push(`${key}: ${n}`)
    }
  }
  return dropped
}
const metText = () => metList().map(t => `${t.key}: ${t.pieces.join(', ')}`).join('; ') || 'none'
if (TRIGGERS.length) {
  const strays = addMet(planCheck.triggered)
  if (strays.length) log(`Plan check named unknown sign-off triggers or pieces (dropped): ${strays.join(', ')}`)
}
// Path rules: a piece that owns a file matching a check's paths meets its trigger, whatever the
// agents report.
const pathAdded = []
for (const c of PATH_CHECKS) {
  for (const p of PIECES) {
    if (met.get(c.key).has(p.name) || !p.owned.some(f => c.paths.some(re => re.test(f)))) continue
    met.get(c.key).add(p.name)
    pathAdded.push(`${c.key}: ${p.name}`)
  }
}
if (pathAdded.length) log(`Sign-off path rules added: ${pathAdded.join(', ')}`)
// Class disagreements: a piece whose class the plan check disputes (agree false, or another
// class). Returned for the seat; the given class stays.
const classDisagreements = []
const strayClasses = []
for (const c of Array.isArray(planCheckRaw.classes) ? planCheckRaw.classes : []) {
  const name = c && c.name
  if (!NAMES.includes(name)) { strayClasses.push(String(name)); continue }
  if (classDisagreements.some(d => d.name === name)) continue
  const given = byName[name].cls
  if (c.agree === false || c.class !== given) classDisagreements.push({ name, given, proposed: c.class, reason: typeof c.reason === 'string' ? c.reason : '' })
}
if (strayClasses.length) log(`Plan check named unknown pieces in classes (dropped): ${strayClasses.join(', ')}`)
if (classDisagreements.length) log(`Plan check disputes piece classes (not applied): ${classDisagreements.map(d => `${d.name} ${d.given} → ${d.proposed}`).join(', ')}`)
log(`Plan check ok: ${WAVES.length} wave(s) — ${WAVES.map(w => `[${w.join(', ')}]`).join(' → ')}${TRIGGERS.length ? `; sign-off triggers met: ${metText()}` : ''}`)

// ---------------- Implement ----------------
phase('Implement')
const IMPL_SCHEMA = {
  type: 'object',
  properties: {
    name: { type: 'string' },
    filesTouched: { type: 'array', items: { type: 'string' } },
    ...(SHARED_FILES.length ? {
      sharedFileLines: {
        type: 'array',
        description: `Your line in each shared file (${SHARED_FILES.join(', ')}): the file and the exact line added, or "" if you added none`,
        items: { type: 'object', properties: { file: { type: 'string' }, line: { type: 'string' } }, required: ['file', 'line'] },
      },
    } : {}),
    testResult: { type: 'string', description: 'Command run and the pass/fail summary line' },
    notes: { type: 'string', description: `Deviations, ${TRIGGERS.length ? 'sign-off triggers met, ' : ''}anything the seat must know` },
    blockers: { type: 'array', items: { type: 'string' } },
    ...(TRIGGERS.length ? { triggered: { type: 'array', description: 'The keys of the sign-off triggers your change meets', items: { type: 'string', enum: TRIGGER_KEYS } } } : {}),
  },
  required: ['name', 'filesTouched', ...(SHARED_FILES.length ? ['sharedFileLines'] : []), 'testResult', 'notes', 'blockers'],
}
const SHARED_EDIT = !SHARED_FILES.length ? ''
  : SHARED_FILES.length === 1 ? ` — plus the single ${SHARED_FILES[0]} line below`
  : ` — plus your single line in each of ${SHARED_FILES.join(', ')}, as below`
const IMPLEMENTER_TEXT = SIGN_OFF.filter(c => c.implementer).map(c => `${c.implementer}\n`).join('')
const TRIGGER_IMPL = TRIGGERS.length ? `Sign-off triggers: set triggered to the keys of those your change meets ([] when none):\n${TRIGGER_LIST}\n` : ''
const implemented = []
// Each piece's implementer route, by its class (the tier it ran on, for result.failed).
const IMPL_ROUTE = Object.fromEntries(PIECES.map(p => [p.name, routeOf('gk-build.implement', { cls: p.cls })]))
// Waves are sequential (later waves build on earlier ones); pieces inside a wave run concurrently.
for (let w = 0; w < WAVES.length; w++) {
  log(`Implement wave ${w + 1}/${WAVES.length}: ${WAVES[w].map(n => `${n} (${byName[n].cls}: ${IMPL_ROUTE[n].model} ${IMPL_ROUTE[n].effort})`).join(', ')}`)
  const res = await pipeline(WAVES[w], (n, _o, i) => {
    const p = byName[n]
    // onFail unfinished: an implementer that dies is never re-run on a half-edited tree.
    return runStage('gk-build.implement', { cls: p.cls },
      `Implement piece "${p.name}" (wave ${w + 1}, slot ${i + 1}) of the plan at ${inRepo(PLAN)} — read the plan first for context, then implement ONLY this piece.
Brief: ${p.brief}
You may edit ONLY these files (create them if missing): ${JSON.stringify(p.owned)}${SHARED_EDIT}. Other pieces are editing other files concurrently; never touch them.
Tests: ${p.tests ? `write/update and run ${p.tests}` : 'none named — run the existing tests nearest the touched files, if any'}. Run them in the FOREGROUND, ${TESTS_FROM}, ${p.tests ? 'with the test command the piece gives (for a test file, the project\'s own runner)' : 'with the project\'s own test runner'}.
${IMPLEMENTER_TEXT}Failures in files you do not own may come from a concurrent piece: report them in notes, do not fix them.
${TRIGGER_IMPL}Report anything you could not do in blockers.${RULES}${projectRules('Implement')}`,
      { label: `impl:${p.name}`, phase: 'Implement', schema: IMPL_SCHEMA },
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
// A piece with no implementation result comes back unfinished, for the seat to re-run on the
// next rung; the workflow never retries it.
const FAILED = MISSING_IMPL.map(n => ({ name: n, class: byName[n].cls, stage: 'implement', tier: IMPL_ROUTE[n].tier, status: 'unfinished' }))
// A shared file is every piece's to add its line to; any other file outside the piece is not.
const OUT_OF_SCOPE = implemented.flatMap(r => r.filesTouched.map(normPathSafe).filter(f => f && !SHARED_FILES.includes(f) && !byName[r.name].owned.includes(f)).map(f => `${r.name}: ${f}`))
if (TRIGGERS.length) {
  const before = metList()
  const strays = addMet(implemented.flatMap(r => (Array.isArray(r.triggered) ? r.triggered : []).map(key => ({ key, pieces: [r.name] }))))
  const late = metList().flatMap(t => t.pieces.filter(n => !before.some(b => b.key === t.key && b.pieces.includes(n))).map(n => `${t.key}: ${n}`))
  if (late.length) log(`Implementers reported sign-off triggers: ${late.join(', ')} — added`)
  if (strays.length) log(`Implementers named unknown sign-off triggers (dropped): ${strays.join(', ')}`)
}
if (OUT_OF_SCOPE.length) log(`Out-of-scope edits reported: ${OUT_OF_SCOPE.join('; ')}`)
// The triggers met by the plan check and the implementers: what the judge starts from.
const MET = metList()

// ---------------- Check + Review ----------------
// Barrier: the judge needs the check result and every review together; check and reviews run concurrently.
phase('Check')
const CHECK_WHERE = !CHECK ? 'no command — skipped'
  : PACKAGES ? `"${CHECK}" in ${AFFECTED.join(', ') || `(no ${PACKAGES.join('/')} files — skipped)`}`
  : `"${CHECK}" from the top level`
log(`Check: ${CHECK_WHERE}; Review: ${implemented.length} piece(s)`)
phase('Review') // check and reviews run concurrently; each agent names its own phase group
const fileOwners = f => PIECES.filter(p => p.owned.includes(f)).map(p => p.name)
const SHARED_CONFIRM = SHARED_FILES.map(f => `; confirm the reported ${f} line is in \`git diff -- ${shq([f])}\``).join('')
const REVIEW_ITEMS = SIGN_OFF.filter(c => c.reviewer).map(c => `, ${reviewerItem(c)}`).join('')
// The check is decided by its exit code alone: passed is exitCode 0, whatever the agent says.
// The seat re-runs the command itself before sign-off (seatMustRerun).
const checkOf = r => (r ? { passed: r.exitCode === 0, ran: typeof r.ran === 'string' ? r.ran : '', exitCode: Number.isInteger(r.exitCode) ? r.exitCode : null, tail: typeof r.tail === 'string' ? r.tail : '' } : null)
const [check, reviewsRaw] = await parallel([
  () => CHECK_SKIPPED
    ? Promise.resolve(CHECK_SKIPPED)
    : runStage('gk-build.check', {},
      `Run the project check and report. ${PACKAGES
        ? `For each package in ${JSON.stringify(AFFECTED)}: cd ${inRepo('<package>')} and run \`set -o pipefail; ${CHECK} 2>&1 | tail -40; echo "EXIT=$?"\` in the FOREGROUND (without pipefail, tail hides the failure). Do not fix anything; edit nothing. ran = each exact command you ran, with its package, one per line. exitCode = the first non-zero number a package printed after EXIT=, else 0. tail = per package, the last 40 lines of its output, verbatim.`
        : `From ${ROOT_DIR}, run \`set -o pipefail; ${CHECK} 2>&1 | tail -40; echo "EXIT=$?"\` in the FOREGROUND (without pipefail, tail hides the failure). Do not fix anything; edit nothing. ran = the exact command you ran. exitCode = the number it printed after EXIT=. tail = the last 40 lines of its output, verbatim.`} Copy them; do not summarise or judge the output: the exit code alone decides whether the check passed.${RULES}${projectRules('Check')}`,
      {
        label: 'check', phase: 'Check',
        schema: { type: 'object', properties: { ran: { type: 'string' }, exitCode: { type: 'integer' }, tail: { type: 'string' } }, required: ['ran', 'exitCode', 'tail'] },
      },
    ).then(checkOf),
  () => pipeline(implemented, (r, _o, i) => {
    const p = byName[r.name]
    const shared = p.owned.filter(f => fileOwners(f).length > 1)
    // The review is at least as capable as the piece's implementer: its class tier, before any dial.
    return runStage('gk-build.review', { implementer: classTier(p.cls) },
      `Review piece "${p.name}" (reviewer ${i + 1}) of the plan at ${inRepo(PLAN)}. Read the plan, then the change: in ${ROOT_DIR} run \`git diff -- ${shq(p.owned)}\` and \`git status --porcelain -- ${shq(p.owned)}\` (keep the quotes); read any new untracked file in full (git diff omits them)${SHARED_CONFIRM}.
Brief the implementer had: ${p.brief}
Implementer's report: ${JSON.stringify({ testResult: r.testResult, notes: r.notes, blockers: r.blockers, ...(SHARED_FILES.length ? { sharedFileLines: r.sharedFileLines || [] } : {}) })}
${shared.length ? `Files shared with other pieces (their edits may also appear): ${shared.join(', ')}.` : ''}
Judge the diff against the brief and the plan: correctness bugs, missed requirements, broken callers, missing/weak tests${REVIEW_ITEMS}. Only real defects; cite file and line. verdict = rework if any finding is high severity or the brief is not met, else accept. Read-only: edit nothing.${RULES}${projectRules('Review')}`,
      {
        label: `review:${p.name}`, phase: 'Review',
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
const SEAT_MUST_RERUN = CHECK && !CHECK_SKIPPED ? CHECK : null
// Where the seat re-runs it: the affected packages, in the order the check stage used; null
// without packages (the top level) or when no check ran.
const SEAT_MUST_RERUN_IN = SEAT_MUST_RERUN && PACKAGES ? AFFECTED.slice() : null
const reviews = (reviewsRaw || []).filter(Boolean)
const unreviewed = implemented.map(r => r.name).filter(n => !reviews.some(v => v.name === n))
if (unreviewed.length) log(`No review returned for: ${unreviewed.join(', ')} — passed to the judge as unreviewed`)
log(`Check ${!check ? 'returned nothing' : NO_COMMAND ? 'skipped' : check.passed ? 'passed' : 'FAILED'}; reviews: ${reviews.filter(v => v.verdict === 'accept').length} accept, ${reviews.filter(v => v.verdict === 'rework').length} rework`)

// ---------------- Judge ----------------
phase('Judge')
// UI sign-off rule, every profile: ui work, or a sign-off check that applies (no trigger, or a
// met one) and sets uiJudge.
const UI_CHECKS = SIGN_OFF.filter(c => c.uiJudge && (!c.trigger || MET.some(t => t.key === c.key))).map(c => c.key)
const FABLE_JUDGE = ARGS.ui === true || UI_CHECKS.length > 0
const fableWhy = [ARGS.ui === true ? 'ui work' : '', UI_CHECKS.length ? `sign-off ${UI_CHECKS.join(', ')} sets uiJudge` : ''].filter(Boolean).join('; ')
// routes.json puts a ui judge on its `when` tier; the flag must be boolean true.
const JUDGE_CTX = { ui: FABLE_JUDGE === true }
const judgeRoute = routeOf('gk-build.judge', JUDGE_CTX)
log(`Judge on ${judgeRoute.model} ${judgeRoute.effort}${FABLE_JUDGE ? ` (${fableWhy})` : ''}`)
const SHIP_SHARED = !SHARED_FILES.length ? ', or any confirmed high-severity finding stands'
  : `, any confirmed high-severity finding stands, or a shared-file line a sign-off check needs is missing or wrong (${SIGN_OFF.filter(c => c.sharedFile).map(c => (c.reviewer ? `${c.sharedFile}: ${reviewerItem(c)}` : c.sharedFile)).join('; ')})`
const TRIGGERS_JUDGE = TRIGGERS.length
  ? `\nSign-off triggers (key: the work that needs the seat's check):\n${TRIGGER_LIST}\nSign-off triggers met (from the plan check${PATH_CHECKS.length ? ', the path rules' : ''} and the implementers): ${JSON.stringify(MET)}${ARGS.ui ? ' (this is UI work)' : ''}`
  : ARGS.ui ? '\nThis is UI work.' : ''
// A check that never ran is not a pass: the judge is told so in words. Whether it ran is the
// workflow's own decision (NO_COMMAND), never a field of the check agent's reply.
const CHECK_JUDGE = NO_COMMAND
  ? `the project-wide check was skipped: ${NO_COMMAND}; judge on the diff and the per-piece tests.`
  : JSON.stringify(check || { passed: false, output: 'check agent returned nothing' })
const judge = await runStage('gk-build.judge', JUDGE_CTX,
  `Decide whether this build ships. Plan: ${inRepo(PLAN)} (read it). Pieces:
${PIECES.map(describe).join('\n')}
Implementation reports: ${JSON.stringify(implemented)}
Pieces with no implementation result: ${JSON.stringify(MISSING_IMPL)}; with no review: ${JSON.stringify(unreviewed)}
Edits reported outside a piece's files: ${JSON.stringify(OUT_OF_SCOPE)}
Project check: ${CHECK_JUDGE}
Reviews: ${JSON.stringify(reviews)}${TRIGGERS_JUDGE}
Weigh each review finding yourself — spot-check the diff (\`git diff -- <file>\` in ${ROOT_DIR}) where a finding is disputed or severity is unclear; reviewers can be wrong both ways. ship = false if the check failed, any piece is missing/unreviewed${SHIP_SHARED}. blockers = concrete reasons not to ship. reworkPieces = piece names needing another pass.${TRIGGERS.length ? ' signOffRequired = for each sign-off trigger, the pieces whose work meets it, as {key, pieces} — every piece under "Sign-off triggers met" at minimum.' : ''} Read-only: edit nothing.${RULES}${projectRules('Judge')}`,
  {
    label: 'judge', phase: 'Judge',
    schema: {
      type: 'object',
      properties: {
        ship: { type: 'boolean' },
        blockers: { type: 'array', items: { type: 'string' } },
        reworkPieces: { type: 'array', items: { type: 'string' } },
        ...(TRIGGERS.length ? { signOffRequired: KEYED_PIECES } : {}),
      },
      required: ['ship', 'blockers', 'reworkPieces', ...(TRIGGERS.length ? ['signOffRequired'] : [])],
    },
  },
)
log(`Judge: ${judge ? (judge.ship ? 'SHIP' : `HOLD — rework ${judge.reworkPieces.join(', ') || 'none named'}`) : 'returned nothing'}`)
// The judge's list joins the triggers already met; a dead judge's stand-in lists every one met.
if (judge && TRIGGERS.length) {
  const strays = addMet(judge.signOffRequired)
  if (strays.length) log(`Judge named unknown sign-off triggers or pieces (dropped): ${strays.join(', ')}`)
}
// What the seat runs before sign-off, in the project's order.
const SIGN_OFF_RESULT = SIGN_OFF
  .map(c => ({ key: c.key, seat: c.seat, pieces: c.trigger ? metPieces(c.key) : NAMES }))
  .filter(e => e.pieces.length)
if (SIGN_OFF.length) log(`Sign-off for the seat: ${SIGN_OFF_RESULT.map(e => `${e.key} (${e.pieces.join(', ')})`).join('; ') || 'none'}`)

return {
  planCheck: { ...planCheck, ...(TRIGGERS.length ? { triggered: MET } : {}) },
  pieces: implemented,
  failed: FAILED,
  check: check || { passed: false, output: 'check agent returned nothing' },
  seatMustRerun: SEAT_MUST_RERUN,
  seatMustRerunIn: SEAT_MUST_RERUN_IN,
  reviews,
  judge: judge || { ship: false, blockers: ['judge agent returned nothing'], reworkPieces: [], ...(TRIGGERS.length ? { signOffRequired: MET } : {}) },
  classDisagreements,
  signOff: SIGN_OFF_RESULT,
  routing: routingResult(),
}
