export const meta = {
  name: 'gk-diagnose',
  description: 'Diagnose a bug whose cause is unclear: per-lens hypotheses, one evidence test per hypothesis, one adjudication',
  whenToUse:
    'Use when a bug or unexpected behaviour has no known cause. Args: { symptom: string (REQUIRED); evidence?: string[] — log lines, stack traces, observations; files?: string[] — starting points; reproduce?: string — a shell command that reproduces the symptom, if one exists; lenses?: string[] — default [\'data-and-state\',\'control-flow-and-concurrency\',\'environment-and-config\']; scratchpad?: string — absolute dir outside the repository for temporary instrumentation, normally the session\'s scratchpad, which the gk-harness hook adds; without one, each agent that needs a folder makes a temporary one outside the repository with mktemp -d and deletes it when done; dials?: string[] — routing dials: \'fable\' lifts the adjudicator to fable high, \'nofable\' moves it off fable to opus (each stage\'s model and effort come from routes.json); project?: object — the repository\'s .claude/gk-project.json plus {root: its git top level}, normally added by the gk-harness hook: guards (rule lines added to every stage brief, or, written as {text, workflows?, stages?}, only to the stages of the named workflows and phases); without a root the agents work in the git repository containing their working directory; {} runs the generic rules }. Returns structured JSON {hypotheses, verdicts, adjudication, routing}; the calling session writes the synthesis. Nothing is fixed and nothing is committed.',
  phases: [
    { title: 'Hypothesise', detail: 'one read-only agent per lens, at most 4 ranked hypotheses each; merged across lenses by normalised mechanism', model: 'opus' },
    { title: 'Test', detail: 'one agent per merged hypothesis tries to confirm or refute it with evidence', model: 'opus' },
    { title: 'Adjudicate', detail: 'one agent reconciles every verdict into a mechanism, contradictions and open questions', model: 'fable' },
  ],
}
// gk-harness:args-v2

// `args` may arrive as a raw JSON string; normalise so both shapes work.
const ARGS = typeof args === 'string' ? (() => { try { return JSON.parse(args) } catch (e) { return args } })() : args

const DEFAULT_LENSES = ['data-and-state', 'control-flow-and-concurrency', 'environment-and-config']
const MAX_PER_LENS = 4
const SCRATCHPAD_ERROR = 'gk-diagnose: args.scratchpad must be an absolute directory path outside the repo when given'

const isStrArray = v => Array.isArray(v) && v.every(s => typeof s === 'string')
if (!ARGS || typeof ARGS !== 'object') {
  throw new Error('gk-diagnose requires args: { symptom: string, evidence?: string[], files?: string[], reproduce?: string, lenses?: string[], scratchpad?: string, dials?: string[], project?: object }')
}
if (typeof ARGS.symptom !== 'string' || !ARGS.symptom.trim()) {
  throw new Error('gk-diagnose: args.symptom is required and must be a non-empty string describing the bug')
}
if (ARGS.evidence !== undefined && !isStrArray(ARGS.evidence)) throw new Error('gk-diagnose: args.evidence must be an array of strings')
if (ARGS.files !== undefined && !isStrArray(ARGS.files)) throw new Error('gk-diagnose: args.files must be an array of strings')
if (ARGS.reproduce !== undefined && (typeof ARGS.reproduce !== 'string' || !ARGS.reproduce.trim())) {
  throw new Error('gk-diagnose: args.reproduce must be a non-empty shell command string when given')
}
if (ARGS.lenses !== undefined && (!isStrArray(ARGS.lenses) || ARGS.lenses.length === 0 || ARGS.lenses.some(l => !l.trim()))) {
  throw new Error('gk-diagnose: args.lenses must be a non-empty array of non-empty strings when given')
}
// Absolute on any OS: /x, C:\x or C:/x, or a \\server\share path. Whether it lies outside the
// repository is checked once the project's root is known.
if (ARGS.scratchpad !== undefined && (typeof ARGS.scratchpad !== 'string' || !/^(\/|[A-Za-z]:[\\/]|\\\\)/.test(ARGS.scratchpad))) {
  throw new Error(SCRATCHPAD_ERROR)
}

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
const WF = 'gk-diagnose'
const PHASES = ['Hypothesise', 'Test', 'Adjudicate']
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
    "opus-high": {"model": "opus", "effort": "high", "cap": [3, 3]},
    "opus-xhigh": {"model": "opus", "effort": "xhigh", "cap": [3, 4]},
    "fable-medium": {"model": "fable", "effort": "medium", "cap": [4, 2]},
    "fable-high": {"model": "fable", "effort": "high", "cap": [4, 3]}
  },
  "stages": {
    "gk-diagnose.hypothesise": {"tier": "opus-high"},
    "gk-diagnose.test": {"tier": "opus-high"},
    "gk-diagnose.adjudicate": {"tier": "fable-medium", "noFable": "opus-xhigh", "onFail": "rerun", "apex": true}
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

// ---------------- Scratchpad ----------------
// The scratchpad is args.scratchpad: the gk-harness hook passes the session's. It must lie
// outside the repository: with backslashes as slashes, neither the root nor under "root/" (so
// /tmp/repository is not under /tmp/repo). Without one, each agent that needs a folder makes a
// temporary one with mktemp and deletes it when done.
const slashes = s => String(s).replace(/\\/g, '/').replace(/\/+$/, '')
if (ARGS.scratchpad !== undefined && ROOT) {
  const s = slashes(ARGS.scratchpad)
  const r = slashes(ROOT)
  if (s === r || s.startsWith(`${r}/`)) throw new Error(SCRATCHPAD_ERROR)
}
// Backslashes become slashes (a Windows scratchpad), so the paths built on it below have one
// kind of separator; a trailing slash is kept, so a POSIX path is used exactly as given.
const SCRATCHPAD = ARGS.scratchpad === undefined ? null : String(ARGS.scratchpad).replace(/\\/g, '/')
const MKTEMP = 'make a temporary folder outside the repository with `mktemp -d` and delete it when done'
const SCRATCH_RULE = SCRATCHPAD
  ? `Your scratchpad is ${SCRATCHPAD}, outside the repository: any temporary file or tree goes there, never in the repo.`
  : `No scratchpad was given: if you need one, ${MKTEMP}; that folder is your scratchpad.`

const symptom = ARGS.symptom.trim()
const evidence = ARGS.evidence || []
const files = ARGS.files || []
const reproduce = ARGS.reproduce ? ARGS.reproduce.trim() : null
const lenses = ARGS.lenses ? [...new Set(ARGS.lenses.map(l => l.trim()))] : DEFAULT_LENSES

const CONTEXT = `Repository: ${REPO_TEXT}.
SYMPTOM: ${symptom}
EVIDENCE SO FAR:
${evidence.length ? evidence.map(e => `- ${e}`).join('\n') : '- (none supplied)'}
STARTING FILES: ${files.length ? files.join(', ') : '(none supplied; locate them yourself)'}
REPRODUCE COMMAND: ${reproduce || '(none supplied)'}`

const RULES = `RULES:
- Do NOT fix anything. Do NOT edit, create or delete any file in the repo. Do NOT commit.
- Never change the repository's working tree, index, HEAD or branches: no git checkout, switch, stash, reset, restore, clean, merge, rebase or bisect, not even \`checkout --detach\`. To read another revision use \`git show <rev>:<path>\` or \`git grep <pattern> <rev>\`; to trial a merge use \`git merge-tree --write-tree <a> <b>\`; if you truly need a tree on disk, \`git worktree add --detach <a fresh dir under your scratchpad> <rev>\` and remove it when done.
- ${SCRATCH_RULE}
- Never quote secret values (keys, tokens, passwords) in your evidence; name the env var instead.
- Run any command in the FOREGROUND and pipe its output through \`tail -40\`.
- Stay on this symptom; do not expand scope to other bugs you notice (one line in your evidence at most).
- Your final output is consumed by a script, not a human: return only the structured result.`
// Every stage brief ends with RULES (and a stage's own read-only line) and then the project rules
// that reach that stage.

const HYP_SCHEMA = {
  type: 'object',
  properties: {
    hypotheses: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          id: { type: 'string' },
          mechanism: { type: 'string', description: 'The causal chain from trigger to symptom, in one or two sentences' },
          whereToLook: { type: 'array', items: { type: 'string' }, description: 'Repo-relative file paths (optionally :line)' },
          howToTest: { type: 'string', description: 'A concrete check that would confirm or refute it' },
          confidence: { type: 'number', minimum: 0, maximum: 1 },
        },
        required: ['id', 'mechanism', 'whereToLook', 'howToTest', 'confidence'],
      },
    },
  },
  required: ['hypotheses'],
}

const VERDICT_SCHEMA = {
  type: 'object',
  properties: {
    id: { type: 'string' },
    verdict: { type: 'string', enum: ['confirmed', 'refuted', 'undetermined'] },
    evidence: { type: 'array', items: { type: 'string' }, description: 'file:line citations, command output excerpts, observations' },
    contradictsPriorEvidence: { type: 'boolean', description: 'True if what you found conflicts with the supplied evidence' },
  },
  required: ['id', 'verdict', 'evidence', 'contradictsPriorEvidence'],
}

const ADJ_SCHEMA = {
  type: 'object',
  properties: {
    mechanism: { type: ['string', 'null'], description: 'The mechanism, only if one hypothesis is confirmed and consistent with all evidence; else null' },
    confirmed: { type: 'array', items: { type: 'string' } },
    refuted: { type: 'array', items: { type: 'string' } },
    undetermined: { type: 'array', items: { type: 'string' } },
    contradictions: { type: 'array', items: { type: 'string' } },
    suggestedFixScope: { type: 'array', items: { type: 'string' }, description: 'Repo-relative files a fix would touch' },
  },
  required: ['mechanism', 'confirmed', 'refuted', 'undetermined', 'contradictions', 'suggestedFixScope'],
}

// ---- Hypothesise: one agent per lens -------------------------------------
phase('Hypothesise')
log(`Hypothesising across ${lenses.length} lenses: ${lenses.join(', ')}`)

const perLens = await pipeline(lenses, (lens, _orig, i) =>
  runStage('gk-diagnose.hypothesise', {},
    `You are diagnosing a bug through ONE lens: "${lens}" (lens ${i + 1} of ${lenses.length}). Other agents cover the other lenses (${lenses.filter(l => l !== lens).join(', ') || 'none'}); stay inside yours.

${CONTEXT}

TASK: read the relevant code paths (read-only) and propose at most ${MAX_PER_LENS} distinct hypotheses for the mechanism that produces this symptom, seen through the "${lens}" lens. Rank them by confidence, highest first. Each needs: a short id (e.g. "${lens.slice(0, 3)}-1"), the mechanism (trigger to symptom), whereToLook (repo-relative files), howToTest (a concrete confirm-or-refute check), and confidence 0..1. Prefer specific, falsifiable mechanisms over vague categories.

${RULES}
- Read-only: no instrumentation, no commands beyond read-only inspection (grep, cat, git log).${projectRules('Hypothesise')}`,
    { label: `hypothesise:${lens}`, phase: 'Hypothesise', schema: HYP_SCHEMA },
  ).then(r => (r ? { lens, hypotheses: r.hypotheses || [] } : null)),
)

const lensResults = perLens.filter(Boolean)
const missingLenses = lenses.filter(l => !lensResults.some(r => r.lens === l))
if (missingLenses.length) log(`No hypotheses returned for lens(es): ${missingLenses.join(', ')} (agent skipped or errored)`)

// BARRIER (justified): hypotheses from different lenses often name the same
// mechanism; merge them across ALL lenses before spending a test agent on each.
const norm = s => String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim()
const merged = new Map()
const blank = []
for (const { lens, hypotheses } of lensResults) {
  const ranked = [...hypotheses].sort((a, b) => (b.confidence || 0) - (a.confidence || 0))
  if (ranked.length > MAX_PER_LENS) {
    log(`Lens ${lens}: kept top ${MAX_PER_LENS} of ${ranked.length}; dropped: ${ranked.slice(MAX_PER_LENS).map(h => h.id).join(', ')}`)
  }
  for (const h of ranked.slice(0, MAX_PER_LENS)) {
    const key = norm(h.mechanism)
    if (!key) { blank.push(`${lens}/${h.id}`); continue }
    const prev = merged.get(key)
    if (prev) {
      prev.lenses.push(lens)
      prev.sourceIds.push(`${lens}/${h.id}`)
      prev.whereToLook = [...new Set([...prev.whereToLook, ...(h.whereToLook || [])])]
      prev.confidence = Math.max(prev.confidence, h.confidence || 0)
      if (h.howToTest && !prev.howToTest.includes(h.howToTest)) prev.howToTest += ` | ALSO: ${h.howToTest}`
    } else {
      merged.set(key, {
        mechanism: h.mechanism, whereToLook: [...(h.whereToLook || [])], howToTest: h.howToTest || '',
        confidence: h.confidence || 0, lenses: [lens], sourceIds: [`${lens}/${h.id}`],
      })
    }
  }
}
const hypotheses = [...merged.values()]
  .sort((a, b) => b.confidence - a.confidence)
  .map((h, i) => ({ id: `H${i + 1}`, ...h }))
const rawCount = lensResults.reduce((n, r) => n + Math.min(r.hypotheses.length, MAX_PER_LENS), 0)
if (blank.length) log(`Dropped ${blank.length} hypothesis(es) with an empty mechanism: ${blank.join(', ')}`)
log(`Hypothesise done: ${rawCount} hypotheses merged to ${hypotheses.length} distinct mechanisms`)

if (!hypotheses.length) {
  log('No hypotheses to test; skipping Test and Adjudicate')
  return { hypotheses, verdicts: [], adjudication: null, routing: routingResult() }
}

// ---- Test: one agent per merged hypothesis --------------------------------
phase('Test')
// Instrumentation goes in the scratchpad (a folder per hypothesis there), or, without one, in a
// temporary folder each test agent makes and deletes.
const instrumentIn = h => (SCRATCHPAD
  ? `put it ONLY in ${SCRATCHPAD}/gk-diagnose-${h.id} (mkdir -p it; other test agents share the scratchpad)`
  : `${MKTEMP}; put it ONLY there (each test agent makes its own)`)
const verdictsRaw = await pipeline(hypotheses, (h, _orig, i) =>
  runStage('gk-diagnose.test', {},
    `You are testing ONE hypothesis (${h.id}, ${i + 1} of ${hypotheses.length}) for a bug. Try hard to CONFIRM OR REFUTE it with evidence; do not lean towards confirming it.

${CONTEXT}

HYPOTHESIS ${h.id} (from lens(es): ${h.lenses.join(', ')}):
Mechanism: ${h.mechanism}
Where to look: ${h.whereToLook.join(', ') || '(unspecified)'}
How to test: ${h.howToTest || '(unspecified; devise a check)'}

METHOD:
1. Trace the code path the mechanism claims, citing file:line.
2. ${reproduce ? `Run the reproduce command in the FOREGROUND, from ${ROOT || 'the repository\'s top level'}: \`( ${reproduce} ) 2>&1 | tail -40\`. Note whether its output fits the mechanism.` : 'No reproduce command was supplied; rely on code reading and safe read-only checks.'}
3. If you need instrumentation, ${instrumentIn(h)}, e.g. a probe script there that imports repo code; never add logging or edits to repo files. Name what you created in your evidence.
4. Verdict: 'confirmed' only with direct evidence the mechanism occurs AND produces this symptom; 'refuted' with evidence it cannot; otherwise 'undetermined'. Set contradictsPriorEvidence=true if anything you found conflicts with the supplied evidence, and say what in your evidence list.
Return id "${h.id}".

${RULES}${projectRules('Test')}`,
    { label: `test:${h.id}`, phase: 'Test', schema: VERDICT_SCHEMA },
  ).then(r => (r ? { ...r, id: h.id } : null)),
)
const verdicts = verdictsRaw.filter(Boolean)
const untested = hypotheses.filter(h => !verdicts.some(v => v.id === h.id)).map(h => h.id)
if (untested.length) log(`Untested (agent skipped or errored): ${untested.join(', ')}`)
log(`Test done: ${['confirmed', 'refuted', 'undetermined'].map(k => `${verdicts.filter(v => v.verdict === k).length} ${k}`).join(', ')}`)

// ---- Adjudicate: one agent over every verdict -----------------------------
phase('Adjudicate')
let adjudication = null
try {
  adjudication = await runStage('gk-diagnose.adjudicate', {},
    `You are adjudicating a bug diagnosis. Several independent agents each tested one hypothesis; reconcile their verdicts.

${CONTEXT}

HYPOTHESES:
${JSON.stringify(hypotheses, null, 2)}

VERDICTS:
${JSON.stringify(verdicts, null, 2)}
${untested.length ? `\nNEVER TESTED (count them as undetermined): ${untested.join(', ')}` : ''}

TASK:
- Spot-check the decisive evidence yourself (re-read the cited file:line) rather than trusting a verdict's wording.
- Name the mechanism ONLY if one hypothesis is confirmed AND consistent with all supplied evidence and with the other verdicts; otherwise mechanism = null.
- Flag every contradiction between verdicts (e.g. two mutually exclusive mechanisms both 'confirmed', or a verdict with contradictsPriorEvidence=true).
- List confirmed, refuted and undetermined hypothesis ids (H-numbers).
- suggestedFixScope: the repo-relative files a fix would most likely touch (empty if no mechanism).

${RULES}
- Read-only: no instrumentation, no commands beyond read-only inspection.${projectRules('Adjudicate')}`,
    { label: 'adjudicate', phase: 'Adjudicate', schema: ADJ_SCHEMA },
  )
} catch (e) {
  log(`Adjudicate agent threw (${e && e.message}); returning hypotheses and verdicts without an adjudication`)
}
log(adjudication ? `Adjudicate done: mechanism ${adjudication.mechanism ? 'named' : 'not established'}` : 'Adjudicate agent returned nothing')

return { hypotheses, verdicts, adjudication: adjudication || null, routing: routingResult() }
