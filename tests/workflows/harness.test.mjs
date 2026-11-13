import test, { afterEach } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, writeFileSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { runWorkflow } from './harness.mjs'

const here = dirname(fileURLToPath(import.meta.url))
const golden = (f) => join(here, 'golden', f)

const tmpDirs = []
function tmpScript(body) {
  const dir = mkdtempSync(join(tmpdir(), 'gkwf-'))
  tmpDirs.push(dir)
  const p = join(dir, 'wf.js')
  writeFileSync(p, body)
  return p
}
afterEach(() => {
  while (tmpDirs.length) rmSync(tmpDirs.pop(), { recursive: true, force: true })
})

test('harness: golden gk-review runs under the fable profile and records every agent call with model and effort', async () => {
  // Without a changed file or a finding the real script stops early, so seed one of each.
  const responders = [
    { match: /^scope$/, reply: () => ({ files: [{ path: 'a.ts', kind: 'backend' }], touchesUi: false, touchesSecurity: false, touchesClientWording: false, summary: 's' }) },
    { match: /^find:correctness$/, reply: () => ({ findings: [{ file: 'a.ts', line: 1, title: 't', claim: 'c', failureScenario: 'f', severity: 'high' }] }) },
  ]
  const { result, calls, logs } = await runWorkflow(golden('gk-review.js'), { target: 'develop..HEAD', profile: 'fable' }, responders)
  assert.ok(result && typeof result === 'object')
  assert.equal(calls.length, 9, 'scope + 4 finders + 3 refuters + judge')
  // A plain scope (no ui, security or client wording) under the fable profile is judged by opus xhigh.
  const judge = calls.filter((c) => c.label === 'judge')
  assert.equal(judge.length, 1)
  assert.deepEqual([judge[0].phase, judge[0].model, judge[0].effort], ['Judge', 'opus', 'xhigh'])
  assert.ok(calls.some((c) => c.label === 'scope' && c.model === 'haiku' && c.effort === 'high'))
  for (const c of calls) {
    assert.ok(c.model, `call ${c.label} has a model`)
    assert.ok(c.effort, `call ${c.label} has an effort`)
    assert.equal(typeof c.prompt, 'string')
  }
  assert.ok(calls.some((c) => c.model === 'haiku'))
  assert.ok(calls.some((c) => c.model === 'sonnet'))
  assert.ok(logs.some((l) => l.includes('Profile: fable')))
})

test('harness: responders are matched on the label and replace the minimal reply', async () => {
  const p = tmpScript(`
    const a = await agent('p', {label: 'alpha', model: 'haiku', effort: 'low', schema: {type: 'object', properties: {n: {type: 'number'}, i: {type: 'integer'}, s: {type: 'string'}, b: {type: 'boolean'}, l: {type: 'array'}, e: {type: 'string', enum: ['first', 'second']}, o: {type: 'string'}}, required: ['n', 'i', 's', 'b', 'l', 'e']}})
    const b = await agent('p', {label: 'beta', model: 'haiku', effort: 'low'})
    const c = await agent('p', {label: 'gamma', model: 'haiku', effort: 'low', schema: {type: 'object', properties: {k: {type: 'string'}}, required: ['k']}})
    const d = await agent('p', {label: 'gamma', model: 'haiku', effort: 'low'})
    return {a, b, c, d}
  `)
  const responders = [
    { match: /^gamma$/g, reply: () => ({ k: 'custom' }) },
    { match: /^gam/, reply: () => ({ k: 'second responder' }) },
  ]
  const { result } = await runWorkflow(p, {}, responders)
  // The first matching responder wins, and a g-flag regex matches on every call, not every other one.
  assert.deepEqual(result, { a: { n: 1, i: 1, s: 'x', b: false, l: [], e: 'first' }, b: '', c: { k: 'custom' }, d: { k: 'custom' } })
})

test('harness: a reply that throws makes agent() return null and the call is still recorded', async () => {
  const p = tmpScript(`
    const r = await agent('p', {label: 'dies', model: 'opus', effort: 'high', schema: {type: 'object', properties: {k: {type: 'string'}}, required: ['k']}})
    return {r}
  `)
  const { result, calls } = await runWorkflow(p, {}, [{ match: /^dies$/, reply: () => { throw new Error('agent died') } }])
  assert.deepEqual(result, { r: null })
  assert.deepEqual(calls.map((c) => c.label), ['dies'])
})

test('harness: agent() rejects a schema the runtime rejects, naming the label', async () => {
  const bad = [
    ['root not an object type', `{type: 'array', items: {type: 'string'}}`],
    ['root without properties', `{type: 'object', required: []}`],
    ['required not in properties', `{type: 'object', properties: {a: {type: 'string'}}, required: ['a', 'b']}`],
    ['nested required not in properties', `{type: 'object', properties: {xs: {type: 'array', items: {type: 'object', properties: {a: {type: 'string'}}, required: ['z']}}}, required: ['xs']}`],
  ]
  for (const [what, schema] of bad) {
    const { calls } = await runWorkflow(tmpScript(`
      let err = null
      try { await agent('p', {label: 'bad-one', model: 'haiku', effort: 'low', schema: ${schema}}) } catch (e) { err = e.message }
      if (!err || !err.includes('bad-one')) throw new Error('${what}: expected a schema error naming the label, got ' + err)
      return err
    `), {})
    assert.equal(calls.length, 0, `${what}: a rejected call is not recorded`)
  }
  const ok = await runWorkflow(tmpScript(`
    return await agent('p', {label: 'good', model: 'haiku', effort: 'low', schema: {type: 'object', properties: {xs: {type: 'array', items: {type: 'object', properties: {a: {type: 'string'}}, required: ['a']}}}, required: ['xs']}})
  `), {})
  assert.deepEqual(ok.result, { xs: [] })
})

test('harness: phase() sets the phase recorded for agent() calls without opts.phase', async () => {
  const p = tmpScript(`
    await agent('p', {label: 'before', model: 'haiku', effort: 'low'})
    phase('One')
    await agent('p', {label: 'inherits', model: 'haiku', effort: 'low'})
    await agent('p', {label: 'explicit', phase: 'Other', model: 'haiku', effort: 'low'})
    phase('Two')
    await agent('p', {label: 'later', model: 'haiku', effort: 'low'})
  `)
  const { calls } = await runWorkflow(p, {})
  assert.deepEqual(calls.map((c) => [c.label, c.phase]), [['before', undefined], ['inherits', 'One'], ['explicit', 'Other'], ['later', 'Two']])
})

test('harness: pipeline has no barrier between stages', async () => {
  const p = tmpScript(`
    return await pipeline(['fast', 'slow'],
      (x) => agent('p', {label: 's1:' + x, model: 'haiku', effort: 'low'}),
      (prev, x) => agent('p', {label: 's2:' + x, model: 'haiku', effort: 'low'}))
  `)
  const done = []
  const reply = (delay) => async (prompt, opts) => {
    if (delay) await new Promise((r) => setTimeout(r, delay))
    done.push(opts.label)
    return opts.label
  }
  const { result } = await runWorkflow(p, {}, [{ match: /^s1:slow$/, reply: reply(50) }, { match: /./, reply: reply(0) }])
  assert.deepEqual(result, ['s2:fast', 's2:slow'])
  assert.ok(done.indexOf('s2:fast') < done.indexOf('s1:slow'), `the fast item's last stage finishes before the slow item's first: ${done}`)
})

test('harness: a stage throw drops the item', async () => {
  const p = tmpScript(`
    const piped = await pipeline([1, 2, 3],
      async (x) => { if (x === 2) throw new Error('boom'); return x * 10 },
      async (prev, item, i) => [prev, item, i])
    const par = await parallel([async () => 'ok', async () => { throw new Error('no') }])
    return {piped, par}
  `)
  const { result } = await runWorkflow(p, {})
  assert.deepEqual(result, { piped: [[10, 1, 0], null, [30, 3, 2]], par: ['ok', null] })
})

test('harness: injects phase, log, args and budget; workflow() exists but throws "not stubbed"', async () => {
  const p = tmpScript(`
    phase('One'); log('hello')
    let child = null
    try { await workflow('gk-review') } catch (e) { child = e.message }
    return {args, total: budget.total, remaining: budget.remaining(), spent: budget.spent(), hasWorkflow: typeof workflow, child}
  `)
  const { result, logs } = await runWorkflow(p, { a: 1 })
  assert.match(result.child, /not stubbed/)
  delete result.child
  assert.deepEqual(result, { args: { a: 1 }, total: null, remaining: Infinity, spent: 0, hasWorkflow: 'function' })
  assert.deepEqual(logs, ['hello'])
})

test('harness: the script has no Node API, clock or randomness, like the real runtime', async () => {
  await assert.rejects(runWorkflow(tmpScript('return Date.now()'), {}), /Date\.now/)
  await assert.rejects(runWorkflow(tmpScript('return Math.random()'), {}), /Math\.random/)
  await assert.rejects(runWorkflow(tmpScript('return Date()'), {}), /Date\(\) without new/)
  await assert.rejects(runWorkflow(tmpScript('return new Date()'), {}), /new Date\(\) without arguments/)
  for (const expr of ['process.cwd()', 'process.env', "require('node:fs')", 'globalThis.process', 'global.process', "Function('return 1')()", 'module.exports', 'exports.x', 'this.process.cwd()']) {
    await assert.rejects(runWorkflow(tmpScript(`return ${expr}`), {}), TypeError, expr)
  }
  const { result } = await runWorkflow(tmpScript('const d = new Date(0); return [d.toISOString(), d instanceof Date]'), {})
  assert.deepEqual(result, ['1970-01-01T00:00:00.000Z', true])
})

test('selftest echoes', async () => {
  const script = join(here, '..', '..', 'workflows', 'gk-selftest.js')
  const full = await runWorkflow(script, { profile: 'opus', seat: 'opus-medium', dials: ['nofable'], project: { name: 'P', root: '/r', guards: [1, 2] }, scratchpad: '/s' })
  assert.deepEqual(full.result, { profile: 'opus', seat: 'opus-medium', dials: ['nofable'], project: { name: 'P', root: '/r', guards: 2 }, scratchpad: '/s' })
  assert.equal(full.calls.length, 0)
  const bare = await runWorkflow(script, undefined)
  assert.deepEqual(bare.result, { profile: null, seat: null, dials: null, project: null, scratchpad: null })
  const part = await runWorkflow(script, { project: {} })
  assert.deepEqual(part.result.project, { name: null, root: null, guards: 0 })
  const fixture = JSON.parse(readFileSync(join(here, 'fixtures', 'goodricke.gk-project.json'), 'utf8'))
  assert.equal(fixture.name, 'Goodricke')
})

test('selftest echoes string args', async () => {
  // Args can arrive as a JSON string, as the five gk-* scripts already allow.
  const script = join(here, '..', '..', 'workflows', 'gk-selftest.js')
  const str = await runWorkflow(script, JSON.stringify({ seat: 'fable-medium', dials: [], project: { name: 'X', root: '/r', guards: ['g'] } }))
  assert.deepEqual(str.result, { profile: null, seat: 'fable-medium', dials: [], project: { name: 'X', root: '/r', guards: 1 }, scratchpad: null })
  assert.equal(str.calls.length, 0)
  for (const odd of ['not json', 'null', '5', '"opus"']) {
    const r = await runWorkflow(script, odd)
    assert.deepEqual(r.result, { profile: null, seat: null, dials: null, project: null, scratchpad: null }, odd)
  }
})
