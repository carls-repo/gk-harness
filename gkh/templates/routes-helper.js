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
