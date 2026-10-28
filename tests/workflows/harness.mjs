// Stub runtime for Workflow scripts: runs a script's body with fake agent/parallel/pipeline/
// phase/log/args/budget/workflow, records every agent() call, and never touches a model.
import { readFileSync } from 'node:fs'

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor

// The required properties only: strings "x", numbers 1, booleans false, arrays [].
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

// Date and Math as the real runtime exposes them: the non-deterministic parts throw.
function runtimeDate() {
  const Real = Date
  function GuardedDate(...a) {
    if (!new.target) return Real(...a)
    if (a.length === 0) throw new Error('new Date() without arguments is not available in a workflow script')
    return new Real(...a)
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

export async function runWorkflow(scriptPath, args, responders = []) {
  const source = readFileSync(scriptPath, 'utf8').replace(/^(\s*)export\s+const\s+meta\b/m, '$1const meta')
  const calls = []
  const logs = []

  async function agent(prompt, opts = {}) {
    calls.push({ label: opts.label, phase: opts.phase, model: opts.model, effort: opts.effort, prompt })
    for (const r of responders) {
      if (r.match.test(opts.label ?? '')) return await r.reply(prompt, opts)
    }
    return minimalFromSchema(opts.schema)
  }
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

  const body = new AsyncFunction('agent', 'parallel', 'pipeline', 'phase', 'log', 'args', 'budget', 'workflow', 'Date', 'Math', source)
  const result = await body(agent, parallel, pipeline, () => {}, (m) => { logs.push(String(m)) }, args, budget, workflow, runtimeDate(), runtimeMath())
  return { result, calls, logs }
}
