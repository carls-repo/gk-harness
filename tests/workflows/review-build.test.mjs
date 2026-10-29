// gk-review (and, from Task 10b, gk-build) against their golden Goodricke copies: the same
// routing under every profile and flag, Goodricke's sentences carried by the project file,
// and a generic run with no trace of Goodricke.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { runWorkflow, minimalFromSchema } from './harness.mjs'

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

const REVIEW_FLAGS = [{}, { ui: true }, { security: true }, { escalate: true }, { clientFacing: true }, { focus: ['accessibility'] }, { escalate: true, security: true }]
// [name, extra args, responder options]
const REVIEW_DETECTED = [
  ['scope detects ui', {}, { detected: { touchesUi: true } }],
  ['scope detects security', {}, { detected: { touchesSecurity: true } }],
  ['scope detects security, escalated', { escalate: true }, { detected: { touchesSecurity: true } }],
  ['scope detects client wording', {}, { detected: { touchesClientWording: true } }],
  ['scope agent dies', {}, { scopeDead: true }],
]
// The log lines that state the judge's route; golden and neutral must state the same one.
const judgeRouteLogs = (logs) => logs.filter((l) => /^(Judge: \w+ xhigh over|Security-sensitive scope)/.test(l))

test('routing equivalence, gk-review: golden and neutral+Goodricke make the same calls on the same (model, effort)', async () => {
  const cases = []
  for (const profile of PROFILES) {
    for (const flags of REVIEW_FLAGS) cases.push([`${profile} ${JSON.stringify(flags)}`, { ...flags, profile }, {}])
    for (const [name, extra, opts] of REVIEW_DETECTED) cases.push([`${profile} ${name}`, { ...extra, profile }, opts])
  }
  let fableJudges = 0
  const escalatedSecurity = []
  for (const [name, flags, opts] of cases) {
    const args = { target: 'develop..HEAD', ...flags }
    const old = await runWorkflow(golden('gk-review.js'), structuredClone(args), reviewResponders(opts))
    const now = await runWorkflow(neutral('gk-review.js'), { ...structuredClone(args), project: goodricke() }, reviewResponders(opts))
    assert.ok(old.calls.some((c) => c.label === 'judge'), `${name}: the golden run reaches the judge`)
    assert.deepEqual(route(now.calls), route(old.calls), name)
    assert.deepEqual(judgeRouteLogs(now.logs), judgeRouteLogs(old.logs), `${name}: the judge route log`)
    // A project changes words, never routing: the generic run keeps every (phase, model, effort).
    const generic = await runWorkflow(neutral('gk-review.js'), { ...structuredClone(args), target: 'main..HEAD', project: { root: '/tmp/repo' } }, reviewResponders(opts))
    assert.deepEqual(generic.calls.map((c) => [c.phase, c.model, c.effort]), old.calls.map((c) => [c.phase, c.model, c.effort]), `${name} (generic)`)
    assert.deepEqual(judgeRouteLogs(generic.logs), judgeRouteLogs(old.logs), `${name} (generic): the judge route log`)
    if (old.calls.some((c) => c.label === 'judge' && c.model === 'fable')) fableJudges++
    if (old.logs.some((l) => l.endsWith('(security route, escalated on Carl\'s word)'))) escalatedSecurity.push(name)
  }
  assert.ok(fableJudges > 0 && fableJudges < cases.length, 'the grid exercises both judge routes')
  // The escalated security route exists only under a non-fable profile, flagged or detected.
  assert.deepEqual(escalatedSecurity, [
    'opus {"escalate":true,"security":true}', 'opus scope detects security, escalated',
    'sonnet {"escalate":true,"security":true}', 'sonnet scope detects security, escalated',
  ])
})

// The golden is mike-dev 8302903ad (golden/README.md, ruling R48), which carries the
// read-at-the-tip and never-change-the-checkout lines; the tests below check them like any
// other golden text.

// Sentences of the golden prompts that name Goodricke or its rules (the plan's marker list).
const MARK = /mike-dev|supabase|prod|:8080|CHANGELOG|scratch scripts|Goodricke|Principal|canAct|ethical wall|page root|de-id|checkout/i
// A sentence ends at . ! or ? before whitespace, but not in "e.g." or "i.e.".
const sentences = (prompt) => prompt.split(/\n+|(?<=[.!?])(?<!\b(?:e\.g|i\.e)\.)\s+/).map(ws).filter(Boolean)

// Golden sentences the marker misses, one reason each. They are checked like the marked ones:
// verbatim in the matching neutral prompt (Goodricke's reach it as guards, generic ones are in
// the core). `scopeRule` marks Goodricke's own Scope classification rules.
const REVIEW_UNMARKED = [
  {
    scopeRule: true,
    sentence: 'Return every changed file (excluding untracked scratch) with a kind judged from its path: frontend/src/app and frontend/src/components = ui; backend/migrations = migration; *.test.ts / *.spec.* / test dirs = test; docs/, *.md = docs; other backend/src = backend; anything else = other.',
    reason: 'Goodricke\'s directory layout; it decides touchesUi, and so the judge route (a frontend/src/components/*.test.tsx file is ui, not test)',
  },
  {
    scopeRule: true,
    sentence: 'touchesSecurity: any change under backend/src/lib/docreview*, auth / mcpAuth / middleware, deid / deident, permissions / accessControl / access.ts, ethicalWalls, audit.',
    reason: 'Goodricke\'s security-sensitive modules; it decides touchesSecurity, and so the judge route',
  },
  {
    scopeRule: true,
    sentence: 'touchesClientWording: any change to code or text that renders client-visible wording — reports (docreviewReport*, report stores), engagement letters/packs (engagement*), LLM prompts or rubrics, document templates.',
    reason: 'Goodricke\'s client-facing modules; it decides touchesClientWording, and so the judge route',
  },
  {
    sentence: 'Other sessions and the running dev stack use that working tree; changing it breaks them.',
    reason: 'the second sentence of Goodricke\'s never-change-the-checkout rule; the marker catches only the first',
  },
  {
    sentence: 'The files on disk are the version under review.',
    reason: 'the working-tree half of the read-at-the-tip rule; generic, so the core carries it',
  },
  {
    sentence: 'Read the new version of a file with `git show HEAD:<path>`, search it with `git grep <pattern> HEAD`, and list files with `git ls-tree -r --name-only HEAD`.',
    reason: 'the range half of the read-at-the-tip rule (the tip of develop..HEAD is HEAD); generic, so the core carries it',
  },
]

// Golden text that does not survive verbatim, one reason each, and the text that replaces it.
// `where: 'schema'` marks text in a stage's output schema rather than in its prompt.
const REVIEW_EXCEPTIONS = [
  {
    sentence: 'Repository: /home/carls/apps/mike-dev (run every command there).',
    reason: 'the repository now comes from args.project.root, so the prompt names it as "the git repository at <root>"',
    replacedBy: 'Repository: the git repository at /home/carls/apps/mike-dev; run every command there.',
  },
  {
    sentence: 'The files on disk are NOT the reviewed version (/home/carls/apps/mike-dev is a shared checkout on another branch).',
    reason: 'the core cannot know the checkout is on another branch, so it says "may not be"; Goodricke\'s stronger claim is a guard, and a guard reaches every review target, so it names the case it holds for',
    replacedBy: 'For a git range, the files on disk are NOT the reviewed version (/home/carls/apps/mike-dev is a shared checkout on another branch).',
  },
  {
    sentence: 'If you truly need a tree on disk (e.g. to run a test), use `git worktree add --detach <a fresh dir under /tmp> HEAD` and remove it when done.',
    reason: '/tmp is not a temporary folder on Windows; the core names any temporary folder outside the repository',
    replacedBy: 'If you truly need a tree on disk (e.g. to run a test), use `git worktree add --detach <a fresh temporary folder outside the repository> HEAD` and remove it when done.',
  },
  {
    where: 'schema',
    sentence: '0 when the finding is file-level (e.g. a missing CHANGELOG entry)',
    reason: 'the FINDING schema\'s line hint reaches the model in every repository, and CHANGELOG.md is a Goodricke sign-off file; the generic hint names a missing test, which any repository can have',
    replacedBy: '0 when the finding is file-level (e.g. a missing test)',
  },
]
const PROMPT_EXCEPTIONS = REVIEW_EXCEPTIONS.filter((e) => e.where !== 'schema')
const SCHEMA_EXCEPTIONS = REVIEW_EXCEPTIONS.filter((e) => e.where === 'schema')

test('goodricke sentences preserved, gk-review: every marked or listed golden sentence is in the matching neutral prompt', async () => {
  const used = new Set()
  const seenUnmarked = new Set()
  let checked = 0
  for (const target of ['develop..HEAD', 'working-tree']) {
    const old = await runWorkflow(golden('gk-review.js'), { target }, reviewResponders())
    const now = await runWorkflow(neutral('gk-review.js'), { target, project: goodricke() }, reviewResponders())
    const byLabel = new Map(now.calls.map((c) => [c.label, ws(c.prompt)]))
    assert.deepEqual([...byLabel.keys()].sort(), old.calls.map((c) => c.label).sort(), `${target}: the same stages run`)
    const listed = (x) => REVIEW_UNMARKED.some((u) => u.sentence === x)
    const excepted = (x) => PROMPT_EXCEPTIONS.find((e) => e.sentence === x)
    for (const c of old.calls) {
      const prompt = byLabel.get(c.label)
      for (const s of sentences(c.prompt).filter((x) => MARK.test(x) || listed(x) || excepted(x))) {
        if (listed(s)) seenUnmarked.add(s)
        const exception = excepted(s)
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
  for (const e of PROMPT_EXCEPTIONS) assert.ok(used.has(e.sentence), `stale exception (no golden prompt has it): ${e.sentence}`)
  for (const u of REVIEW_UNMARKED) assert.ok(seenUnmarked.has(u.sentence), `stale listed sentence (no golden prompt has it): ${u.sentence}`)
})

// Each stage's output schema by label: a catch-all responder records it, then answers as the
// first matching responder would, or with the harness's minimal reply.
async function schemasOf(script, args, responders) {
  const schemas = new Map()
  const recorder = {
    match: /^/,
    reply: (prompt, opts) => {
      schemas.set(opts.label, JSON.stringify(opts.schema))
      const r = responders.find((x) => { x.match.lastIndex = 0; return x.match.test(opts.label) })
      return r ? r.reply(prompt, opts) : minimalFromSchema(opts.schema)
    },
  }
  await runWorkflow(script, args, [recorder])
  return schemas
}

test('stage schemas, gk-review: every stage gets the golden\'s output schema, except the listed changes', async () => {
  const used = new Set()
  for (const target of ['develop..HEAD', 'working-tree']) {
    const old = await schemasOf(golden('gk-review.js'), { target }, reviewResponders())
    const now = await schemasOf(neutral('gk-review.js'), { target, project: goodricke() }, reviewResponders())
    assert.deepEqual([...now.keys()].sort(), [...old.keys()].sort(), `${target}: the same stages run`)
    for (const [label, json] of old) {
      let want = json
      for (const e of SCHEMA_EXCEPTIONS) {
        if (!want.includes(e.sentence)) continue
        used.add(e.sentence)
        want = want.split(e.sentence).join(e.replacedBy)
      }
      assert.equal(now.get(label), want, `${target} ${label}: the schema differs from the golden's beyond the listed changes`)
    }
  }
  for (const e of SCHEMA_EXCEPTIONS) assert.ok(used.has(e.sentence), `stale exception (no golden schema has it): ${e.sentence}`)
})

// With project rules, the generic Scope definitions give way to the project's own: the golden
// Goodricke rules decide each file's kind and each touches flag, exactly as today.
const SCOPE_REPLACES = 'Where the project rules below give this repository\'s own file kinds, or its own touchesUi, touchesSecurity or touchesClientWording rule, use those instead of the matching definitions above (they replace them; they do not add to them).'

test('goodricke scope rules, gk-review: the project\'s Scope rules replace the generic definitions, not add to them', async () => {
  const scopeOf = async (project) => ws((await runWorkflow(neutral('gk-review.js'), { target: 'main..HEAD', project }, reviewResponders())).calls.find((c) => c.label === 'scope').prompt)
  const scope = await scopeOf(goodricke())
  assert.ok(scope.includes(SCOPE_REPLACES), scope)
  assert.doesNotMatch(scope, /count too|as well as/i)
  for (const u of REVIEW_UNMARKED.filter((x) => x.scopeRule)) assert.ok(scope.includes(`- ${u.sentence}`), `the Goodricke Scope rule is a project rule:\n  ${u.sentence}`)
  // With no project rule in the Scope stage there is nothing to replace the defaults, and no
  // sentence about it: no project, no guards, or guards that reach other stages only.
  const elsewhere = { root: '/tmp/repo', guards: [{ text: 'A Judge rule.', stages: ['Judge'] }, { text: 'A build rule.', workflows: ['gk-build'] }] }
  for (const project of [{ root: '/tmp/repo' }, undefined, elsewhere]) assert.ok(!(await scopeOf(project)).includes('Where the project rules below'), JSON.stringify(project))
})

// The guard lines that reach one stage, read straight from a project's guards (ruling R49): a
// string reaches every stage of every workflow; an object only the stages whose workflow and
// phase title its lists name, where a list is given.
const reach = (guards, workflow, stage) => guards
  .filter((g) => typeof g === 'string' || ((!g.workflows || g.workflows.includes(workflow)) && (!g.stages || g.stages.includes(stage))))
  .map((g) => (typeof g === 'string' ? g : g.text))
const rulesBlock = (lines) => (lines.length ? `\nProject rules:\n${lines.map((t) => `- ${t}`).join('\n')}` : '')

test('goodricke project, gk-review: each stage brief ends with the guards that reach it, and the log names the project', async () => {
  for (const target of ['develop..HEAD', 'working-tree']) {
    const { calls, logs } = await runWorkflow(neutral('gk-review.js'), { target, project: goodricke() }, reviewResponders())
    for (const c of calls) {
      const block = rulesBlock(reach(fixture.guards, 'gk-review', c.phase))
      assert.ok(block, `${target} ${c.label}: some guard reaches the ${c.phase} stage`)
      assert.ok(c.prompt.endsWith(block), `${target} ${c.label}: the brief ends with the project rules for the ${c.phase} stage`)
      assert.equal(c.prompt.split('Project rules:').length, 2, `${target} ${c.label}: one project rules block`)
    }
    assert.deepEqual(calls.filter((c) => c.label.startsWith('find:')).map((c) => c.label),
      ['find:correctness', 'find:security-and-invariants', 'find:regressions-and-tests', 'find:efficiency-and-simplification'])
    const n = (k) => (fixture[k] || []).length
    assert.ok(logs.includes(`Project: Goodricke (${n('guards')} guards, ${n('reviewFinders')} finders, ${n('signOff')} sign-off checks)`), logs.join('\n'))
  }
})

test('goodricke guards, gk-review: each guard reaches exactly the stages where the golden has its sentence', async () => {
  const replaces = new Map(PROMPT_EXCEPTIONS.map((e) => [e.replacedBy, e.sentence]))
  const inGolden = new Set()
  for (const target of ['develop..HEAD', 'working-tree']) {
    const old = await runWorkflow(golden('gk-review.js'), { target }, reviewResponders())
    const now = await runWorkflow(neutral('gk-review.js'), { target, project: goodricke() }, reviewResponders())
    for (const g of fixture.guards) {
      const text = typeof g === 'string' ? g : g.text
      const original = replaces.get(text) || text
      const want = old.calls.filter((c) => ws(c.prompt).includes(ws(original))).map((c) => c.label)
      const got = now.calls.filter((c) => c.prompt.includes(`\n- ${text}`)).map((c) => c.label)
      if (want.length) inGolden.add(text)
      if (replaces.has(text)) {
        // A guard that replaces a golden sentence (see REVIEW_EXCEPTIONS) reaches at least the
        // stages that had the sentence; its own wording names the case it holds for.
        for (const label of want) assert.ok(got.includes(label), `${target} ${label}: ${text}`)
      } else {
        assert.deepEqual(got, want, `${target}: the stages this guard reaches:\n  ${text}`)
      }
    }
  }
  for (const g of fixture.guards) {
    const text = typeof g === 'string' ? g : g.text
    assert.ok(inGolden.has(text), `a guard with no golden sentence behind it: ${text}`)
  }
  // Outside gk-review, only the rules that bind every step in the Goodricke repo reach a stage:
  // the review-worded guards are scoped to gk-review (ruling R49).
  const universal = ['Any database check is dev only;', 'NEVER change the checkout of /home/carls/apps/mike-dev:']
  for (const wf of ['gk-build', 'gk-design', 'gk-research', 'gk-diagnose']) {
    for (const stage of ['Scope', 'Find', 'Verify', 'Judge', 'Implement']) {
      const lines = reach(fixture.guards, wf, stage)
      assert.deepEqual(lines.map((l) => universal.find((u) => l.startsWith(u)) || l), universal, `${wf} ${stage}`)
    }
  }
})

test('scoped guards, gk-review: a string guard reaches every stage; an object guard only the stages both of its lists match', async () => {
  const guards = [
    'Everywhere.',
    { text: 'Review, every stage.', workflows: ['gk-review'] },
    { text: 'Review Scope only.', workflows: ['gk-review'], stages: ['Scope'] },
    { text: 'Any workflow, Judge only.', stages: ['Judge'] },
    { text: 'Review Find and Verify.', workflows: ['gk-build', 'gk-review'], stages: ['Find', 'Verify'] },
    { text: 'Another workflow only.', workflows: ['gk-build'] },
    { text: 'Another workflow\'s Judge.', workflows: ['gk-build'], stages: ['Judge'] },
    { text: 'Phase titles match exactly.', workflows: ['gk-review'], stages: ['judge'] },
  ]
  const { calls, logs } = await runWorkflow(neutral('gk-review.js'), { target: 'main..HEAD', project: { root: '/tmp/repo', guards } }, reviewResponders())
  const want = {
    Scope: ['Everywhere.', 'Review, every stage.', 'Review Scope only.'],
    Find: ['Everywhere.', 'Review, every stage.', 'Review Find and Verify.'],
    Verify: ['Everywhere.', 'Review, every stage.', 'Review Find and Verify.'],
    Judge: ['Everywhere.', 'Review, every stage.', 'Any workflow, Judge only.'],
  }
  assert.deepEqual([...new Set(calls.map((c) => c.phase))], ['Scope', 'Find', 'Verify', 'Judge'])
  for (const c of calls) {
    assert.ok(c.prompt.endsWith(rulesBlock(want[c.phase])), `${c.label}: ${c.prompt.slice(c.prompt.indexOf('Project rules:'))}`)
    assert.equal(c.prompt.split('Project rules:').length, 2, `${c.label}: one project rules block`)
  }
  assert.ok(logs.includes(`Project: (unnamed) (${guards.length} guards, 0 finders, 0 sign-off checks)`), logs.join('\n'))
  // Guards that reach no stage of this workflow leave no block at all.
  const none = await runWorkflow(neutral('gk-review.js'), { target: 'main..HEAD', project: { root: '/tmp/repo', guards: guards.slice(5) } }, reviewResponders())
  for (const c of none.calls) assert.ok(!c.prompt.includes('Project rules:'), c.label)
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
      // Read-only means the repository's tree: a detached worktree outside it is allowed.
      assert.ok(c.prompt.includes('READ-ONLY: do not edit, create or delete any file in the repository\'s working tree;'), `${target} ${c.label}: the read-only line`)
    }
    assert.deepEqual(calls.filter((c) => c.label.startsWith('find:')).map((c) => c.label),
      ['find:correctness', 'find:security', 'find:regressions-and-tests', 'find:efficiency-and-simplification'])
    const security = calls.find((c) => c.label === 'find:security').prompt
    assert.ok(security.includes('injection, authorisation on every mutating path, secrets in code or logs, unsafe deserialisation, missing input validation at trust boundaries'))
    assert.ok(logs.includes('Project: none (generic rules)'), logs.join('\n'))
  }
})

test('generic run without a root, gk-review: prompts point at the working directory\'s repository', async () => {
  for (const project of [undefined, {}]) {
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

test('focus, gk-review: an entry whose key a finder already has is dropped and named in one log line', async () => {
  const findersOf = (calls) => calls.filter((c) => c.label.startsWith('find:')).map((c) => c.label)
  const { calls, logs } = await runWorkflow(neutral('gk-review.js'), { target: 'main..HEAD', focus: ['correctness', 'accessibility', ' accessibility ', 'security'], project: { root: '/tmp/repo' } }, reviewResponders())
  assert.deepEqual(findersOf(calls), ['find:correctness', 'find:security', 'find:regressions-and-tests', 'find:efficiency-and-simplification', 'find:accessibility'])
  assert.equal(new Set(calls.map((c) => c.label)).size, calls.length, 'every agent label is unique')
  assert.deepEqual(logs.filter((l) => l.startsWith('Focus:')), ['Focus: dropped 3 entries whose key a finder already has: correctness, accessibility, security'])
  assert.ok(logs.includes('gk-review: main..HEAD; 3 refuter(s) per finding; 1 extra focus dimension(s)'), logs.join('\n'))
  // Under Goodricke, security-and-invariants has replaced security, so only the former repeats.
  const g = await runWorkflow(neutral('gk-review.js'), { target: 'develop..HEAD', focus: ['security-and-invariants', 'security'], project: goodricke() }, reviewResponders())
  assert.deepEqual(findersOf(g.calls), ['find:correctness', 'find:security-and-invariants', 'find:regressions-and-tests', 'find:efficiency-and-simplification', 'find:security'])
  assert.deepEqual(g.logs.filter((l) => l.startsWith('Focus:')), ['Focus: dropped 1 entry whose key a finder already has: security-and-invariants'])
  // Nothing repeated, nothing logged.
  const clean = await runWorkflow(neutral('gk-review.js'), { target: 'main..HEAD', focus: ['accessibility'] }, reviewResponders())
  assert.deepEqual(clean.logs.filter((l) => l.startsWith('Focus:')), [])
})

test('normFile, gk-review: findings that differ only by the root prefix, a leading ./ or the slash direction are deduplicated', async () => {
  // One finder reports every path with the same line and title, so dedup turns on normFile alone.
  const dropped = async (project, ...files) => {
    const responders = [
      { match: /^scope$/, reply: () => ({ files: [{ path: 'a.ts', kind: 'backend' }], touchesUi: false, touchesSecurity: false, touchesClientWording: false, summary: 's' }) },
      { match: /^find:correctness$/, reply: () => ({ findings: files.map((file) => ({ file, line: 3, title: 'Same bug', claim: 'c', failureScenario: 'f', severity: 'high' })) }) },
      { match: /^find:/, reply: () => ({ findings: [] }) },
    ]
    const args = { target: 'main..HEAD' }
    if (project !== undefined) args.project = project
    return (await runWorkflow(neutral('gk-review.js'), args, responders)).result.dedupDropped
  }
  assert.equal(await dropped({ root: '/tmp/repo' }, '/tmp/repo/src/a.ts', './src/a.ts', 'src/a.ts'), 2)
  assert.equal(await dropped({ root: '/tmp/repo/' }, '/tmp/repo/src/a.ts', 'src/a.ts'), 1, 'a root with a trailing slash')
  // /tmp/repository is not under /tmp/repo: its path keeps every character, so it equals none
  // of its own proper suffixes, which are all reported too.
  const sibling = '/tmp/repository/src/a.ts'
  const suffixes = Array.from({ length: sibling.length - 1 }, (_, i) => sibling.slice(i + 1))
  assert.equal(await dropped({ root: '/tmp/repo' }, sibling, ...suffixes), 0, 'a sibling folder whose name starts with the root\'s is not stripped')
  // Windows: the hook passes the root with forward slashes; a finder may write either.
  assert.equal(await dropped({ root: 'C:/r' }, 'C:/r/src/a.ts', 'C:\\r\\src\\a.ts', 'src/a.ts', '.\\src\\a.ts'), 3)
  assert.equal(await dropped({ root: 'C:\\r' }, 'C:/r/src/a.ts', 'src/a.ts'), 1, 'a root written with backslashes')
  assert.equal(await dropped({ root: 'C:/r' }, 'C:\\rr\\src\\a.ts', 'r\\src\\a.ts', 'src/a.ts'), 0, 'C:\\rr is not under C:/r')
  // No root: only a leading ./ (or .\) is stripped.
  assert.equal(await dropped(undefined, './src/a.ts', '.\\src\\a.ts', 'src/a.ts', '/tmp/repo/src/a.ts'), 2)
})

test('project malformed throws, gk-review: before any agent runs, naming the field', async () => {
  const bad = [
    [null, 'not an object'],
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
    [{ guards: ['line one\u2028line two'] }, 'guards[0]'],
    [{ guards: [['a rule']] }, 'guards[0]'],
    [{ guards: [{}] }, 'guards[0].text'],
    [{ guards: [{ text: 'line one\nline two' }] }, 'guards[0].text'],
    [{ guards: [{ text: 'ok', workflows: 'gk-review' }] }, 'guards[0].workflows'],
    [{ guards: [{ text: 'ok', workflows: [] }] }, 'guards[0].workflows'],
    [{ guards: [{ text: 'ok', stages: [] }] }, 'guards[0].stages'],
    [{ guards: [{ text: 'ok', stages: ['Scope', 3] }] }, 'guards[0].stages'],
    [{ guards: [{ text: 'ok', stages: ['Sco\npe'] }] }, 'guards[0].stages'],
    [{ guards: ['ok', { text: 'ok', stage: ['Scope'] }] }, 'guards[1].stage'],
    [{ reviewFinders: {} }, 'reviewFinders'],
    [{ reviewFinders: ['security'] }, 'reviewFinders[0]'],
    [{ reviewFinders: [{ brief: 'b' }] }, 'reviewFinders[0].key'],
    [{ reviewFinders: [{ key: 'two\nlines', brief: 'b' }] }, 'reviewFinders[0].key'],
    [{ reviewFinders: [{ key: 'two\u2028lines', brief: 'b' }] }, 'reviewFinders[0].key'],
    [{ reviewFinders: [{ key: 'a\u0085b', brief: 'b' }] }, 'reviewFinders[0].key'],
    [{ reviewFinders: [{ key: 'tab\tkey', brief: 'b' }] }, 'reviewFinders[0].key'],
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
  // The full Goodricke fixture, a root-only project and {} (the generic rules) are well formed.
  for (const project of [goodricke(), { root: '/tmp/repo' }, {}]) {
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

// Rulings R46 and R50: names the harness cannot hide from a script (or that the live runtime
// lacks), and the other ways to reach the clock or the global object. new Date(<arguments>) is
// allowed: it is not the clock.
const FORBIDDEN = [
  ['process', /\bprocess\b/], ['require', /\brequire\b/], ['import(', /\bimport\s*\(/],
  ['performance', /\bperformance\b/], ['crypto', /\bcrypto\b/], ['fetch', /\bfetch\b/],
  ['Buffer', /\bBuffer\b/], ['setImmediate', /\bsetImmediate\b/], ['Date.now', /\bDate\s*\.\s*now\b/],
  ['new Date()', /\bnew\s+Date\s*\(\s*\)/], ['new Date', /\bnew\s+Date\b(?!\s*\()/],
  ['Date() without new', /(?<![\w$.]|\bnew\s+)\bDate\s*\(/], ['globalThis', /\bglobalThis\b/],
  ['Math.random', /\bMath\s*\.\s*random\b/],
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

test('static guard: catches new Date without parentheses, Date() without new and globalThis[...] in code only', () => {
  const named = (src) => FORBIDDEN.filter(([, re]) => re.test(codeOnly(src))).map(([n]) => n)
  const cases = [
    ['const t = new Date;', ['new Date']],
    ['const t = new Date\nlog(t)', ['new Date']],
    ['const t = new Date()', ['new Date()']],
    ['const t = Date()', ['Date() without new']],
    ['const t = Date ()', ['Date() without new']],
    ['const t = String(Date())', ['Date() without new']],
    ["const p = globalThis['pro' + 'cess']", ['globalThis']],
    ['const g = globalThis[k]', ['globalThis']],
    // Allowed: a date from arguments, Date's own static helpers, a method named Date, prompt text.
    ['const d = new Date(0); const e = new  Date(x, 1)', []],
    ['const p = Date.parse(s) + Date.UTC(2026, 0)', []],
    ['const v = obj.Date() + $Date(1)', []],
    ["const t = `new Date; Date() globalThis[x] ${n}` + 'Date()' // new Date; Date() globalThis", []],
  ]
  for (const [src, want] of cases) assert.deepEqual(named(src), want, src)
})

test('static guard: no workflow script names a Node API, the clock or randomness (rulings R46, R50)', () => {
  const files = readdirSync(workflowsDir).filter((f) => f.endsWith('.js')).sort()
  assert.ok(files.includes('gk-review.js') && files.includes('gk-selftest.js'), files.join(', '))
  for (const f of files) {
    const code = codeOnly(readFileSync(join(workflowsDir, f), 'utf8'))
    for (const [name, re] of FORBIDDEN) assert.doesNotMatch(code, re, `${f} names ${name}`)
  }
})
