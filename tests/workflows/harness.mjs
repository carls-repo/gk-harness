// Stub runtime for Workflow scripts: runs a script's body with fake agent/parallel/pipeline/
// phase/log/args/budget/workflow, records every agent() call, and never touches a model.
//
// Responders: runWorkflow(path, args, [{match: RegExp, reply: (prompt, opts) => any}]).
// The first responder whose `match` tests opts.label answers the call (a g or y flag is
// harmless: lastIndex is reset before each test). To simulate a dead or skipped agent, as
// the live runtime reports one, have `reply` return null, or throw: a reply that throws makes
// agent() return null, and the call is still recorded.
//
// What the harness enforces, to reject what the live runtime rejects:
// - agent() validates opts.schema when one is given: the root must be {type: 'object',
//   properties: {...}}, and at every level `required` must be an array of names that are all
//   in that level's `properties`. A bad schema makes agent() throw an Error naming the label,
//   and the call is NOT recorded.
// - The script body sees no Node API: process, require, module, exports, globalThis, global
//   and Function are shadowed as undefined, and `this` is an empty object. Date throws when
//   called without `new` and on `new Date()` with no arguments; Date.now() and Math.random()
//   throw. `new Date(<arguments>)` works. Plain JS cannot block everything: dynamic import()
//   is syntax and cannot be shadowed, and the Function constructor stays reachable through
//   (() => {}).constructor. Don't rely on the harness to catch those.
// - phase(title) sets the current phase; an agent() call without opts.phase is recorded
//   under it, as the runtime groups it.
import { readFileSync } from 'node:fs'

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor

// The required properties only: strings "x", numbers and integers 1, booleans false,
// arrays [], and an enum's first value (enum[0]) whatever its type.
export function minimalFromSchema(schema) {
  if (!schema || typeof schema !== 'object') return ''
  if (Array.isArray(schema.enum) && schema.enum.length) return schema.enum[0]
  switch (schema.type) {
    case 'object': {
      const out = {}
      const props = schema.properties || {}
      for (const key of schema.required || []) out[key] = minimalFromSchema(props[key])
      return out
    }
    case 'array': return []
    case 'string': return 'x'
    case 'number':
    case 'integer': return 1
    case 'boolean': return false
    default: return 'x'
  }
}

const isPlainObject = (v) => v !== null && typeof v === 'object' && !Array.isArray(v)

// Returns a problem description, or null when the schema is acceptable.
export function schemaProblem(schema) {
  if (!isPlainObject(schema)) return 'the schema must be an object'
  if (schema.type !== 'object') return `the root schema must have type 'object' (got ${JSON.stringify(schema.type)})`
  if (!isPlainObject(schema.properties)) return 'the root schema must have a properties object'
  return nestedProblem(schema, '$')
}

function nestedProblem(s, path) {
  if (!isPlainObject(s)) return null
  if (s.properties !== undefined && !isPlainObject(s.properties)) return `${path}.properties must be an object`
  const props = s.properties || {}
  if (s.required !== undefined) {
    if (!Array.isArray(s.required)) return `${path}.required must be an array`
    const missing = s.required.filter((n) => typeof n !== 'string' || !Object.prototype.hasOwnProperty.call(props, n))
    if (missing.length) return `${path}.required names ${missing.map((n) => JSON.stringify(n)).join(', ')} not in ${path}.properties`
  }
  for (const [k, v] of Object.entries(props)) {
    const p = nestedProblem(v, `${path}.properties.${k}`)
    if (p) return p
  }
  if (Array.isArray(s.items)) {
    for (let i = 0; i < s.items.length; i++) {
      const p = nestedProblem(s.items[i], `${path}.items[${i}]`)
      if (p) return p
    }
  } else if (s.items !== undefined) {
    const p = nestedProblem(s.items, `${path}.items`)
    if (p) return p
  }
  if (isPlainObject(s.additionalProperties)) {
    const p = nestedProblem(s.additionalProperties, `${path}.additionalProperties`)
    if (p) return p
  }
  for (const key of ['anyOf', 'oneOf', 'allOf']) {
    if (!Array.isArray(s[key])) continue
    for (let i = 0; i < s[key].length; i++) {
      const p = nestedProblem(s[key][i], `${path}.${key}[${i}]`)
      if (p) return p
    }
  }
  return null
}

// Date and Math as the real runtime exposes them: the non-deterministic parts throw.
function runtimeDate() {
  const Real = Date
  function GuardedDate(...a) {
    // Date(...) without `new` ignores its arguments and returns the current time as a string.
    if (!new.target) throw new Error('Date() without new is not available in a workflow script')
    if (a.length === 0) throw new Error('new Date() without arguments is not available in a workflow script')
    return Reflect.construct(Real, a, new.target)
  }
  GuardedDate.prototype = Real.prototype
  GuardedDate.parse = Real.parse
  GuardedDate.UTC = Real.UTC
  GuardedDate.now = () => { throw new Error('Date.now() is not available in a workflow script') }
  return GuardedDate
}

function runtimeMath() {
  const m = Object.create(Math)
  m.random = () => { throw new Error('Math.random() is not available in a workflow script') }
  return m
}

// Names the script body sees as undefined, so no Node or global-object access gets through.
const SHADOWED = ['process', 'require', 'module', 'exports', 'globalThis', 'global', 'Function']

export async function runWorkflow(scriptPath, args, responders = []) {
  const source = readFileSync(scriptPath, 'utf8').replace(/^(\s*)export\s+const\s+meta\b/m, '$1const meta')
  const calls = []
  const logs = []
  let currentPhase

  async function agent(prompt, opts = {}) {
    if (opts.schema !== undefined) {
      const problem = schemaProblem(opts.schema)
      if (problem) throw new Error(`agent(${JSON.stringify(opts.label)}): invalid schema: ${problem}`)
    }
    const phaseTitle = opts.phase !== undefined ? opts.phase : currentPhase
    calls.push({ label: opts.label, phase: phaseTitle, model: opts.model, effort: opts.effort, prompt })
    for (const r of responders) {
      r.match.lastIndex = 0
      if (r.match.test(opts.label ?? '')) {
        try { return await r.reply(prompt, opts) } catch (e) { return null }
      }
    }
    return minimalFromSchema(opts.schema)
  }
  const phase = (title) => { currentPhase = title }
  const parallel = (thunks) => Promise.all(thunks.map(async (t) => { try { return await t() } catch (e) { return null } }))
  const pipeline = (items, ...stages) => Promise.all(items.map(async (item, i) => {
    let prev = item
    try {
      for (const stage of stages) prev = await stage(prev, item, i)
      return prev
    } catch (e) { return null }
  }))
  const budget = { total: null, spent: () => 0, remaining: () => Infinity }
  const workflow = async (nameOrRef) => { throw new Error(`workflow(${JSON.stringify(nameOrRef)}) is not stubbed by the harness`) }

  const body = new AsyncFunction('agent', 'parallel', 'pipeline', 'phase', 'log', 'args', 'budget', 'workflow', 'Date', 'Math', ...SHADOWED, source)
  const self = Object.freeze(Object.create(null))
  const result = await body.call(self, agent, parallel, pipeline, phase, (m) => { logs.push(String(m)) }, args, budget, workflow, runtimeDate(), runtimeMath(), ...SHADOWED.map(() => undefined))
  return { result, calls, logs }
}
