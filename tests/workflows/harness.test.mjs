import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, writeFileSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { runWorkflow } from './harness.mjs'

const here = dirname(fileURLToPath(import.meta.url))
const golden = (f) => join(here, 'golden', f)

function tmpScript(body) {
  const p = join(mkdtempSync(join(tmpdir(), 'gkwf-')), 'wf.js')
  writeFileSync(p, body)
  return p
}

test('harness: golden gk-review runs under the fable profile and records every agent call with model and effort', async () => {
  // Without a changed file or a finding the real script stops early, so seed one of each.
  const responders = [
    { match: /^scope$/, reply: () => ({ files: [{ path: 'a.ts', kind: 'backend' }], touchesUi: false, touchesSecurity: false, touchesClientWording: false, summary: 's' }) },
    { match: /^find:correctness$/, reply: () => ({ findings: [{ file: 'a.ts', line: 1, title: 't', claim: 'c', failureScenario: 'f', severity: 'high' }] }) },
  ]
  const { result, calls, logs } = await runWorkflow(golden('gk-review.js'), { target: 'develop..HEAD', profile: 'fable' }, responders)
  assert.ok(result && typeof result === 'object')
  assert.ok(calls.length >= 9, `expected scope + 4 finders + 3 refuters + judge, got ${calls.length}`)
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
    const a = await agent('p', {label: 'alpha', model: 'haiku', effort: 'low', schema: {type: 'object', properties: {n: {type: 'number'}, s: {type: 'string'}, b: {type: 'boolean'}, l: {type: 'array'}, o: {type: 'string'}}, required: ['n', 's', 'b', 'l']}})
    const b = await agent('p', {label: 'beta', model: 'haiku', effort: 'low'})
    const c = await agent('p', {label: 'gamma', model: 'haiku', effort: 'low', schema: {type: 'object', properties: {k: {type: 'string'}}, required: ['k']}})
    return {a, b, c}
  `)
  const { result } = await runWorkflow(p, {}, [{ match: /^gamma$/, reply: () => ({ k: 'custom' }) }])
  assert.deepEqual(result, { a: { n: 1, s: 'x', b: false, l: [] }, b: '', c: { k: 'custom' } })
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

test('harness: injects phase, log, args, budget and a working child workflow stub', async () => {
  const p = tmpScript(`
    phase('One'); log('hello')
    return {args, total: budget.total, remaining: budget.remaining(), spent: budget.spent(), hasWorkflow: typeof workflow}
  `)
  const { result, logs } = await runWorkflow(p, { a: 1 })
  assert.deepEqual(result, { args: { a: 1 }, total: null, remaining: Infinity, spent: 0, hasWorkflow: 'function' })
  assert.deepEqual(logs, ['hello'])
})

test('harness: Date.now and Math.random throw like the real runtime', async () => {
  await assert.rejects(runWorkflow(tmpScript('return Date.now()'), {}), /Date\.now/)
  await assert.rejects(runWorkflow(tmpScript('return Math.random()'), {}), /Math\.random/)
})

test('selftest echoes', async () => {
  const script = join(here, '..', '..', 'workflows', 'gk-selftest.js')
  const full = await runWorkflow(script, { profile: 'opus', project: { name: 'P', root: '/r', guards: [1, 2] }, scratchpad: '/s' })
  assert.deepEqual(full.result, { profile: 'opus', project: { name: 'P', root: '/r', guards: 2 }, scratchpad: '/s' })
  assert.equal(full.calls.length, 0)
  const bare = await runWorkflow(script, undefined)
  assert.deepEqual(bare.result, { profile: null, project: null, scratchpad: null })
  const part = await runWorkflow(script, { project: {} })
  assert.deepEqual(part.result.project, { name: null, root: null, guards: 0 })
  const fixture = JSON.parse(readFileSync(join(here, 'fixtures', 'goodricke.gk-project.json'), 'utf8'))
  assert.equal(fixture.name, 'Goodricke')
})
