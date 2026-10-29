// gk-review (and, from Task 10b, gk-build) against their golden Goodricke copies: the same
// routing under every profile and flag, Goodricke's sentences carried by the project file,
// and a generic run with no trace of Goodricke.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { runWorkflow } from './harness.mjs'

const here = dirname(fileURLToPath(import.meta.url))
const workflowsDir = join(here, '..', '..', 'workflows')
const golden = (f) => join(here, 'golden', f)
const neutral = (f) => join(workflowsDir, f)
const fixture = JSON.parse(readFileSync(join(here, 'fixtures', 'goodricke.gk-project.json'), 'utf8'))
const GOODRICKE_ROOT = '/home/carls/apps/mike-dev'
const goodricke = () => ({ ...structuredClone(fixture), root: GOODRICKE_ROOT })

const PROFILES = ['fable', 'opus', 'sonnet']
const route = (calls) => calls.map((c) => [c.label, c.phase, c.model, c.effort])
const ws = (s) => String(s).replace(/\s+/g, ' ').trim()

// ---------------- gk-review ----------------

// Scope sees one changed file and reports the given detection flags (or dies); every finder
// reports one distinct finding, so Verify and Judge always run. Refuters and the judge get the
// harness's minimal reply (refuted: false, so every finding survives).
function reviewResponders({ detected = {}, scopeDead = false } = {}) {
  return [
    {
      match: /^scope$/,
      reply: () => (scopeDead ? null : {
        files: [{ path: 'backend/src/a.ts', kind: 'backend' }],
        touchesUi: false, touchesSecurity: false, touchesClientWording: false,
        ...detected,
        summary: 'Changes a.ts.',
      }),
    },
    {
      match: /^find:/,
      reply: (prompt, opts) => {
        const key = opts.label.slice('find:'.length)
        return { findings: [{ file: `backend/src/${key}.ts`, line: 7, title: `${key} issue`, claim: 'c', failureScenario: 'f', severity: 'medium' }] }
      },
    },
  ]
}

const REVIEW_FLAGS = [{}, { ui: true }, { security: true }, { escalate: true }, { clientFacing: true }, { focus: ['accessibility'] }]
const REVIEW_DETECTED = [
  ['scope detects ui', { detected: { touchesUi: true } }],
  ['scope detects security', { detected: { touchesSecurity: true } }],
  ['scope detects client wording', { detected: { touchesClientWording: true } }],
  ['scope agent dies', { scopeDead: true }],
]

test('routing equivalence, gk-review: golden and neutral+Goodricke make the same calls on the same (model, effort)', async () => {
  const cases = []
  for (const profile of PROFILES) {
    for (const flags of REVIEW_FLAGS) cases.push([`${profile} ${JSON.stringify(flags)}`, { ...flags, profile }, {}])
    for (const [name, opts] of REVIEW_DETECTED) cases.push([`${profile} ${name}`, { profile }, opts])
  }
  let fableJudges = 0
  for (const [name, flags, opts] of cases) {
    const args = { target: 'develop..HEAD', ...flags }
    const old = await runWorkflow(golden('gk-review.js'), structuredClone(args), reviewResponders(opts))
    const now = await runWorkflow(neutral('gk-review.js'), { ...structuredClone(args), project: goodricke() }, reviewResponders(opts))
    assert.ok(old.calls.some((c) => c.label === 'judge'), `${name}: the golden run reaches the judge`)
    assert.deepEqual(route(now.calls), route(old.calls), name)
    // A project changes words, never routing: the generic run keeps every (phase, model, effort).
    const generic = await runWorkflow(neutral('gk-review.js'), { ...structuredClone(args), target: 'main..HEAD', project: { root: '/tmp/repo' } }, reviewResponders(opts))
    assert.deepEqual(generic.calls.map((c) => [c.phase, c.model, c.effort]), old.calls.map((c) => [c.phase, c.model, c.effort]), `${name} (generic)`)
    if (old.calls.some((c) => c.label === 'judge' && c.model === 'fable')) fableJudges++
  }
  assert.ok(fableJudges > 0 && fableJudges < cases.length, 'the grid exercises both judge routes')
})

// Sentences of the golden prompts that name Goodricke or its rules (the plan's marker list).
const MARK = /mike-dev|supabase|prod|:8080|CHANGELOG|scratch scripts|Goodricke|Principal|canAct|ethical wall|page root|de-id|checkout/i
const sentences = (prompt) => prompt.split(/\n+|(?<=[.!?])\s+/).map(ws).filter(Boolean)

// Golden sentences that do not survive verbatim, one reason each, and the text that replaces them.
const REVIEW_EXCEPTIONS = [
  {
    sentence: 'Repository: /home/carls/apps/mike-dev (run every command there).',
    reason: 'the repository now comes from args.project.root, so the prompt names it as "the git repository at <root>"',
    replacedBy: 'Repository: the git repository at /home/carls/apps/mike-dev; run every command there.',
  },
]

test('goodricke sentences preserved, gk-review: every marked golden sentence is in the matching neutral prompt', async () => {
  const used = new Set()
  let checked = 0
  for (const target of ['develop..HEAD', 'working-tree']) {
    const old = await runWorkflow(golden('gk-review.js'), { target }, reviewResponders())
    const now = await runWorkflow(neutral('gk-review.js'), { target, project: goodricke() }, reviewResponders())
    const byLabel = new Map(now.calls.map((c) => [c.label, ws(c.prompt)]))
    assert.deepEqual([...byLabel.keys()].sort(), old.calls.map((c) => c.label).sort(), `${target}: the same stages run`)
    for (const c of old.calls) {
      const prompt = byLabel.get(c.label)
      for (const s of sentences(c.prompt).filter((x) => MARK.test(x))) {
        const exception = REVIEW_EXCEPTIONS.find((e) => e.sentence === s)
        if (exception) {
          used.add(exception.sentence)
          assert.ok(prompt.includes(exception.replacedBy), `${target} ${c.label}: the replacement for an excepted sentence is missing: ${exception.replacedBy}`)
          continue
        }
        assert.ok(prompt.includes(s), `${target} ${c.label}: golden sentence missing from the neutral prompt:\n  ${s}`)
        checked++
      }
    }
  }
  assert.ok(checked > 0, 'some marked sentences were checked')
  for (const e of REVIEW_EXCEPTIONS) assert.ok(used.has(e.sentence), `stale exception (no golden prompt has it): ${e.sentence}`)
})

test('goodricke scope hints, gk-review: the golden scope classification for Goodricke paths reaches the neutral scope agent', async () => {
  const fragments = [
    'frontend/src/app and frontend/src/components = ui; backend/migrations = migration',
    'other backend/src = backend',
    'backend/src/lib/docreview*, auth / mcpAuth / middleware, deid / deident, permissions / accessControl / access.ts, ethicalWalls, audit',
    'reports (docreviewReport*, report stores), engagement letters/packs (engagement*), LLM prompts or rubrics, document templates',
  ]
  const old = await runWorkflow(golden('gk-review.js'), { target: 'develop..HEAD' }, reviewResponders())
  const now = await runWorkflow(neutral('gk-review.js'), { target: 'develop..HEAD', project: goodricke() }, reviewResponders())
  const scopeOf = (r) => ws(r.calls.find((c) => c.label === 'scope').prompt)
  for (const f of fragments) {
    assert.ok(scopeOf(old).includes(f), `the golden scope prompt has: ${f}`)
    assert.ok(scopeOf(now).includes(f), `the neutral Goodricke scope prompt has: ${f}`)
  }
})

test('goodricke project, gk-review: guards reach every stage under "Project rules:" and the log names the project', async () => {
  for (const target of ['develop..HEAD', 'working-tree']) {
    const { calls, logs } = await runWorkflow(neutral('gk-review.js'), { target, project: goodricke() }, reviewResponders())
    const rules = `Project rules:\n${fixture.guards.map((g) => `- ${g}`).join('\n')}`
    for (const c of calls) assert.ok(c.prompt.includes(rules), `${target} ${c.label}: the project rules block`)
    assert.deepEqual(calls.filter((c) => c.label.startsWith('find:')).map((c) => c.label),
      ['find:correctness', 'find:security-and-invariants', 'find:regressions-and-tests', 'find:efficiency-and-simplification'])
    const n = (k) => (fixture[k] || []).length
    assert.ok(logs.includes(`Project: Goodricke (${n('guards')} guards, ${n('reviewFinders')} finders, ${n('signOff')} sign-off checks)`), logs.join('\n'))
  }
})

// Words that name Goodricke, its repo, its stack or its modules.
const GOODRICKE_WORDS = /Goodricke|mike-dev|CHANGELOG|supabase|:8080|\bdevelop\b|\/home\/carls|docreview|ethical ?walls?|\bde-?id|mcpAuth|engagement|canAct|\bPrincipal\b|PostgREST|scratch scripts|dev only/i

test('generic run is clean, gk-review: no prompt names Goodricke and every prompt names the root', async () => {
  for (const target of ['main..HEAD', 'working-tree']) {
    const { calls, logs } = await runWorkflow(neutral('gk-review.js'), { target, project: { root: '/tmp/repo' } }, reviewResponders())
    assert.ok(calls.some((c) => c.label === 'judge'), `${target}: the run reaches the judge`)
    for (const c of calls) {
      const hit = GOODRICKE_WORDS.exec(c.prompt)
      assert.equal(hit, null, `${target} ${c.label}: generic prompt names ${hit && hit[0]}`)
      assert.ok(c.prompt.includes('the git repository at /tmp/repo'), `${target} ${c.label}: names the root`)
      assert.ok(!c.prompt.includes('Project rules:'), `${target} ${c.label}: no project rules without guards`)
    }
    assert.deepEqual(calls.filter((c) => c.label.startsWith('find:')).map((c) => c.label),
      ['find:correctness', 'find:security', 'find:regressions-and-tests', 'find:efficiency-and-simplification'])
    const security = calls.find((c) => c.label === 'find:security').prompt
    assert.ok(security.includes('injection, authorisation on every mutating path, secrets in code or logs, unsafe deserialisation, missing input validation at trust boundaries'))
    assert.ok(logs.includes('Project: none (generic rules)'), logs.join('\n'))
  }
})

test('generic run without a root, gk-review: prompts point at the working directory\'s repository', async () => {
  for (const project of [undefined, null, {}]) {
    const args = { target: 'main..HEAD' }
    if (project !== undefined) args.project = project
    const { calls, logs } = await runWorkflow(neutral('gk-review.js'), args, reviewResponders())
    assert.ok(calls.length >= 9)
    for (const c of calls) {
      assert.ok(c.prompt.includes('the git repository containing your working directory (run `git rev-parse --show-toplevel` first)'), `${JSON.stringify(project)} ${c.label}`)
      assert.ok(!c.prompt.includes('the git repository at'), `${JSON.stringify(project)} ${c.label}`)
    }
    assert.ok(logs.includes('Project: none (generic rules)'), logs.join('\n'))
  }
})

test('review target, gk-review: a range is read at its tip; the working tree and a single revision are read from disk', async () => {
  const promptsFor = async (target) => (await runWorkflow(neutral('gk-review.js'), { target, project: { root: '/tmp/repo' } }, reviewResponders())).calls.map((c) => c.prompt)
  for (const p of await promptsFor('main..feat/x')) {
    assert.ok(p.includes('`git show feat/x:<path>`') && p.includes('`git grep <pattern> feat/x`') && p.includes('`git ls-tree -r --name-only feat/x`'), p)
  }
  for (const p of await promptsFor('main...HEAD')) assert.ok(p.includes('`git show HEAD:<path>`'), p)
  for (const target of ['working-tree', 'main']) {
    for (const p of await promptsFor(target)) {
      assert.ok(p.includes('The files on disk are the version under review.'), `${target}: ${p}`)
      assert.ok(!p.includes('git show'), target)
    }
  }
  const wt = await promptsFor('working-tree')
  assert.ok(wt.every((p) => p.includes('Review target: the uncommitted working tree')))
})

test('project finders, gk-review: a replacing finder takes the replaced one\'s place; others go after the core and before focus', async () => {
  const project = {
    root: '/tmp/repo',
    reviewFinders: [
      { key: 'licensing', brief: 'third-party licence headers.' },
      { key: 'house-security', replaces: 'security', brief: 'the house security rules.' },
    ],
  }
  const { calls, logs } = await runWorkflow(neutral('gk-review.js'), { target: 'main..HEAD', focus: ['accessibility'], project }, reviewResponders())
  assert.deepEqual(calls.filter((c) => c.label.startsWith('find:')).map((c) => c.label),
    ['find:correctness', 'find:house-security', 'find:regressions-and-tests', 'find:efficiency-and-simplification', 'find:licensing', 'find:accessibility'])
  assert.ok(calls.find((c) => c.label === 'find:house-security').prompt.includes('Look only for: the house security rules.'))
  assert.ok(calls.find((c) => c.label === 'find:licensing').prompt.includes('dimension "licensing". Look only for: third-party licence headers.'))
  assert.ok(logs.includes('Project: (unnamed) (0 guards, 2 finders, 0 sign-off checks)'), logs.join('\n'))
})

test('normFile, gk-review: findings that differ only by the root prefix are deduplicated', async () => {
  const twoFinders = (path1, path2) => [
    { match: /^scope$/, reply: () => ({ files: [{ path: 'a.ts', kind: 'backend' }], touchesUi: false, touchesSecurity: false, touchesClientWording: false, summary: 's' }) },
    { match: /^find:correctness$/, reply: () => ({ findings: [{ file: path1, line: 3, title: 'Same bug', claim: 'c', failureScenario: 'f', severity: 'high' }] }) },
    { match: /^find:security$/, reply: () => ({ findings: [{ file: path2, line: 3, title: 'same  bug!', claim: 'c', failureScenario: 'f', severity: 'high' }] }) },
  ]
  const withRoot = await runWorkflow(neutral('gk-review.js'), { target: 'main..HEAD', project: { root: '/tmp/repo' } }, twoFinders('/tmp/repo/src/a.ts', './src/a.ts'))
  assert.equal(withRoot.result.dedupDropped, 1)
  const otherPrefix = await runWorkflow(neutral('gk-review.js'), { target: 'main..HEAD', project: { root: '/tmp/repo' } }, twoFinders('/tmp/repository/src/a.ts', 'src/a.ts'))
  assert.equal(otherPrefix.result.dedupDropped, 0, 'a path outside the root keeps its prefix')
  const noRoot = await runWorkflow(neutral('gk-review.js'), { target: 'main..HEAD' }, twoFinders('./src/a.ts', 'src/a.ts'))
  assert.equal(noRoot.result.dedupDropped, 1)
})

test('project malformed throws, gk-review: before any agent runs, naming the field', async () => {
  const bad = [
    ['Goodricke', 'not an object'],
    [[], 'not an object'],
    [7, 'not an object'],
    [{ root: '' }, 'root'],
    [{ root: 3 }, 'root'],
    [{ root: '/tmp/a\nb' }, 'root'],
    [{ name: 3 }, 'name'],
    [{ guards: 'never prod' }, 'guards'],
    [{ guards: ['ok', 3] }, 'guards[1]'],
    [{ guards: ['line one\nline two'] }, 'guards[0]'],
    [{ reviewFinders: {} }, 'reviewFinders'],
    [{ reviewFinders: ['security'] }, 'reviewFinders[0]'],
    [{ reviewFinders: [{ brief: 'b' }] }, 'reviewFinders[0].key'],
    [{ reviewFinders: [{ key: 'a' }] }, 'reviewFinders[0].brief'],
    [{ reviewFinders: [{ key: 'a', brief: 'b', replaces: 3 }] }, 'reviewFinders[0].replaces'],
    [{ reviewFinders: [{ key: 'a', brief: 'b', replaces: 'no-such-finder' }] }, 'reviewFinders[0].replaces'],
    [{ reviewFinders: [{ key: 's1', brief: 'b', replaces: 'security' }, { key: 's2', brief: 'c', replaces: 'security' }] }, 'reviewFinders[1].replaces'],
    [{ reviewFinders: [{ key: 'correctness', brief: 'b' }] }, 'reviewFinders[0].key'],
    [{ reviewFinders: [{ key: 'x', brief: 'b' }, { key: 'x', brief: 'c' }] }, 'reviewFinders[1].key'],
    [{ signOff: 'changelog' }, 'signOff'],
    [{ signOff: [{ seat: 's' }] }, 'signOff[0].key'],
    [{ signOff: [{ key: 'k', uiJudge: 'yes' }] }, 'signOff[0].uiJudge'],
    [{ signOff: [{ key: 'k', trigger: 4 }] }, 'signOff[0].trigger'],
  ]
  for (const [project, field] of bad) {
    const seen = []
    const spy = [{ match: /.*/, reply: (prompt, opts) => { seen.push(opts.label); return null } }]
    await assert.rejects(
      runWorkflow(neutral('gk-review.js'), { target: 'main..HEAD', project }, spy),
      (e) => e.message === `gk-review: args.project is malformed (${field})`,
      `${JSON.stringify(project)} → (${field})`,
    )
    assert.deepEqual(seen, [], `${JSON.stringify(project)}: no agent ran`)
  }
  // The full Goodricke fixture and a root-only project are well formed.
  for (const project of [goodricke(), { root: '/tmp/repo' }, {}, null]) {
    await runWorkflow(neutral('gk-review.js'), { target: 'main..HEAD', project }, reviewResponders())
  }
})

// ---------------- every workflow script ----------------

// The script with comments, string and template text, and regex literals blanked out, so a
// name that only appears in prompt text is not mistaken for code. Template ${...} expressions
// stay, since they are code.
function codeOnly(src) {
  let out = ''
  let i = 0
  let last = '' // the last significant code character: tells a regex literal from division
  const braces = [] // one open-brace counter per ${ ... } we are inside
  const KEYWORD_BEFORE_VALUE = /(?:^|[^\w$])(return|typeof|instanceof|in|of|new|delete|void|throw|case|do|else|yield|await)\s*$/
  const regexAllowed = () => !last || !/[\w$)\]]/.test(last) || KEYWORD_BEFORE_VALUE.test(out)
  const template = () => { // i is just past a backtick or the } closing a ${ ... }
    while (i < src.length) {
      if (src[i] === '\\') { i += 2; continue }
      if (src[i] === '`') { i++; out += '`` '; last = ')'; return }
      if (src[i] === '$' && src[i + 1] === '{') { i += 2; braces.push(0); out += ' ${ '; last = '{'; return }
      i++
    }
  }
  while (i < src.length) {
    const c = src[i]
    const d = src[i + 1]
    if (c === '/' && d === '/') { while (i < src.length && src[i] !== '\n') i++; continue }
    if (c === '/' && d === '*') { const e = src.indexOf('*/', i + 2); i = e < 0 ? src.length : e + 2; out += ' '; continue }
    if (c === '"' || c === "'") {
      i++
      while (i < src.length && src[i] !== c && src[i] !== '\n') i += src[i] === '\\' ? 2 : 1
      i++
      out += '"" '
      last = ')' // a string is a value: a / after it divides
      continue
    }
    if (c === '`') { i++; template(); continue }
    if (c === '/' && regexAllowed()) {
      i++
      let inClass = false
      while (i < src.length && src[i] !== '\n') {
        if (src[i] === '\\') { i += 2; continue }
        if (src[i] === '[') inClass = true
        else if (src[i] === ']') inClass = false
        else if (src[i] === '/' && !inClass) break
        i++
      }
      i++
      while (i < src.length && /[a-z]/.test(src[i])) i++
      out += ' /r/ '
      last = ')'
      continue
    }
    if (braces.length && c === '{') braces[braces.length - 1]++
    if (braces.length && c === '}') {
      if (braces[braces.length - 1] === 0) { braces.pop(); i++; template(); continue }
      braces[braces.length - 1]--
    }
    out += c
    if (!/\s/.test(c)) last = c
    i++
  }
  return out
}

// Ruling R46: names the harness cannot hide from a script (or that the live runtime lacks).
const FORBIDDEN = [
  ['process', /\bprocess\b/], ['require', /\brequire\b/], ['import(', /\bimport\s*\(/],
  ['performance', /\bperformance\b/], ['crypto', /\bcrypto\b/], ['fetch', /\bfetch\b/],
  ['Buffer', /\bBuffer\b/], ['setImmediate', /\bsetImmediate\b/], ['Date.now', /\bDate\s*\.\s*now\b/],
  ['new Date()', /\bnew\s+Date\s*\(\s*\)/], ['Math.random', /\bMath\s*\.\s*random\b/],
]

test('static guard: the code scanner tells code from prompt text, comments and regexes', () => {
  const src = [
    "const a = 'process ' + \"fetch(x)\" + `crypto ${Buffer.byteLength(q)} Math.random() ${`require ${n}`}` // import('x')",
    '/* performance */ const r = /Date.now()|new Date()[/`]/g; const half = total / 2 / setImmediate',
    'if (ok) return /fetch/.test(s); return x.split(/\\//)',
  ].join('\n')
  const code = codeOnly(src)
  const named = FORBIDDEN.filter(([, re]) => re.test(code)).map(([n]) => n)
  assert.deepEqual(named, ['Buffer', 'setImmediate'], code)
  assert.match(code, /\bq\b/, 'a ${} expression is kept as code')
  assert.match(code, /\bn\b/, 'a nested template expression is kept as code')
  assert.match(code, /\btotal\b.*\bhalf\b|\bhalf\b.*\btotal\b/s, 'division is code, not a regex')
})

test('static guard: no workflow script names a Node API, the clock or randomness (ruling R46)', () => {
  const files = readdirSync(workflowsDir).filter((f) => f.endsWith('.js')).sort()
  assert.ok(files.includes('gk-review.js') && files.includes('gk-selftest.js'), files.join(', '))
  for (const f of files) {
    const code = codeOnly(readFileSync(join(workflowsDir, f), 'utf8'))
    for (const [name, re] of FORBIDDEN) assert.doesNotMatch(code, re, `${f} names ${name}`)
  }
})
