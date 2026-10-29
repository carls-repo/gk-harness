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
// A guard that reaches some stage of gk-review or gk-build: a string, or an object whose
// workflows list (if any) names one of them.
const reachesReviewOrBuild = (g) => typeof g === 'string' || !g.workflows || g.workflows.some((w) => w === 'gk-review' || w === 'gk-build')

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
  for (const target of ['develop..HEAD', 'working-tree']) {
    const old = await runWorkflow(golden('gk-review.js'), { target }, reviewResponders())
    const now = await runWorkflow(neutral('gk-review.js'), { target, project: goodricke() }, reviewResponders())
    for (const g of fixture.guards) {
      const text = typeof g === 'string' ? g : g.text
      const original = replaces.get(text) || text
      const want = old.calls.filter((c) => ws(c.prompt).includes(ws(original))).map((c) => c.label)
      const got = now.calls.filter((c) => c.prompt.includes(`\n- ${text}`)).map((c) => c.label)
      if (replaces.has(text)) {
        // A guard that replaces a golden sentence (see REVIEW_EXCEPTIONS) reaches at least the
        // stages that had the sentence; its own wording names the case it holds for.
        for (const label of want) assert.ok(got.includes(label), `${target} ${label}: ${text}`)
      } else {
        assert.deepEqual(got, want, `${target}: the stages this guard reaches:\n  ${text}`)
      }
    }
  }
  // Every guard has a golden sentence behind it: see "goodricke guards, every guard" for the
  // guards that reach gk-review or gk-build, and design-research-diagnose.test.mjs for the
  // others. Of the guards that reach gk-review or gk-build, only the rules that bind every step
  // in the Goodricke repo reach the other workflows' stages: the review- and build-worded guards
  // are scoped to their own workflow (ruling R49).
  const universal = ['Any database check is dev only;', 'NEVER change the checkout of /home/carls/apps/mike-dev:']
  for (const wf of ['gk-design', 'gk-research', 'gk-diagnose']) {
    for (const stage of scriptPhases(`${wf}.js`).phases) {
      const lines = reach(fixture.guards.filter(reachesReviewOrBuild), wf, stage)
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

// Malformed projects that every workflow rejects (the shared gk-project block), each with the
// field its error names. Sign-off checks follow ruling R52: key and seat are required single
// lines, the other texts optional strings, uiJudge a boolean, anything else malformed; fix
// round 1 adds paths (regular expressions, only with a trigger), check and packages.
const PROJECT_MALFORMED = [
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
  [{ signOff: 'changelog' }, 'signOff'],
  [{ signOff: [null] }, 'signOff[0]'],
  [{ signOff: ['changelog'] }, 'signOff[0]'],
  [{ signOff: [{ seat: 's' }] }, 'signOff[0].key'],
  [{ signOff: [{ key: 'two\nlines', seat: 's' }] }, 'signOff[0].key'],
  [{ signOff: [{ key: 'k' }] }, 'signOff[0].seat'],
  [{ signOff: [{ key: 'k', seat: '' }] }, 'signOff[0].seat'],
  [{ signOff: [{ key: 'k', seat: 'line one\u2028line two' }] }, 'signOff[0].seat'],
  [{ signOff: [{ key: 'k', seat: 's', uiJudge: 'yes' }] }, 'signOff[0].uiJudge'],
  [{ signOff: [{ key: 'k', seat: 's', trigger: 4 }] }, 'signOff[0].trigger'],
  [{ signOff: [{ key: 'k', seat: 's', sharedFile: 7 }] }, 'signOff[0].sharedFile'],
  [{ signOff: [{ key: 'k', seat: 's', implementer: ' ' }] }, 'signOff[0].implementer'],
  [{ signOff: [{ key: 'k', seat: 's', reviewer: ['a'] }] }, 'signOff[0].reviewer'],
  [{ signOff: [{ key: 'k', seat: 's', judge: 'x' }] }, 'signOff[0].judge'],
  [{ signOff: [{ key: 'k', seat: 's' }, { key: 'k2', seat: 's', uiJudge: true, pieces: [] }] }, 'signOff[1].pieces'],
  // paths: a non-empty list of regular expressions that compile, given only with a trigger.
  [{ signOff: [{ key: 'k', seat: 's', trigger: 't', paths: '^a/' }] }, 'signOff[0].paths'],
  [{ signOff: [{ key: 'k', seat: 's', trigger: 't', paths: [] }] }, 'signOff[0].paths'],
  [{ signOff: [{ key: 'k', seat: 's', trigger: 't', paths: ['^a/', 3] }] }, 'signOff[0].paths'],
  [{ signOff: [{ key: 'k', seat: 's', trigger: 't', paths: ['^(a/'] }] }, 'signOff[0].paths'],
  [{ signOff: [{ key: 'k', seat: 's', trigger: 't', paths: ['^a/\n|^b/'] }] }, 'signOff[0].paths'],
  [{ signOff: [{ key: 'k', seat: 's', paths: ['^a/'] }] }, 'signOff[0].paths'],
  // check: one printable line; packages: a non-empty list of printable lines.
  [{ check: '' }, 'check'],
  [{ check: 7 }, 'check'],
  [{ check: 'npm test\nrm -rf build' }, 'check'],
  [{ packages: 'backend' }, 'packages'],
  [{ packages: [] }, 'packages'],
  [{ packages: ['backend', 3] }, 'packages[1]'],
  [{ packages: ['back\nend'] }, 'packages[0]'],
]
// gk-review's own checks: a replaces names a core finder not yet replaced, and finder keys
// (core and project) are unique. Other workflows ignore reviewFinders beyond their shape.
const REVIEW_MALFORMED = [
  [{ reviewFinders: [{ key: 'a', brief: 'b', replaces: 'no-such-finder' }] }, 'reviewFinders[0].replaces'],
  [{ reviewFinders: [{ key: 's1', brief: 'b', replaces: 'security' }, { key: 's2', brief: 'c', replaces: 'security' }] }, 'reviewFinders[1].replaces'],
  [{ reviewFinders: [{ key: 'correctness', brief: 'b' }] }, 'reviewFinders[0].key'],
  [{ reviewFinders: [{ key: 'x', brief: 'b' }, { key: 'x', brief: 'c' }] }, 'reviewFinders[1].key'],
]

// Runs a workflow with a spy that records every agent label, and checks the run stops with the
// malformed error for the field before any agent starts.
async function assertMalformed(file, args, project, field) {
  const seen = []
  const spy = [{ match: /.*/, reply: (prompt, opts) => { seen.push(opts.label); return null } }]
  const wf = file.replace(/\.js$/, '')
  await assert.rejects(
    runWorkflow(neutral(file), { ...args, project }, spy),
    (e) => e.message === `${wf}: args.project is malformed (${field})`,
    `${wf} ${JSON.stringify(project)} → (${field})`,
  )
  assert.deepEqual(seen, [], `${wf} ${JSON.stringify(project)}: no agent ran`)
}

test('project malformed throws, gk-review: before any agent runs, naming the field', async () => {
  for (const [project, field] of [...PROJECT_MALFORMED, ...REVIEW_MALFORMED]) {
    await assertMalformed('gk-review.js', { target: 'main..HEAD' }, project, field)
  }
  // The full Goodricke fixture, a root-only project and {} (the generic rules) are well formed,
  // and so is every sign-off field (ruling R52, plus paths), check and packages, which gk-review
  // checks but does not use.
  const allFields = { root: '/tmp/repo', check: 'make check', packages: ['web'], signOff: [{ key: 'k', seat: 's', sharedFile: 'NOTICE', trigger: 't', paths: ['^web/'], uiJudge: false, implementer: 'i', reviewer: 'r' }] }
  for (const project of [goodricke(), { root: '/tmp/repo' }, {}, allFields]) {
    await runWorkflow(neutral('gk-review.js'), { target: 'main..HEAD', project }, reviewResponders())
  }
})

// ---------------- gk-build ----------------

const PLAN = 'docs/plan.md'
// Three pieces in two waves (c shares a.ts with a, so it waits), b is hard, a names a test file.
// No file is a page root by the golden's path heuristic, so here the page-root route comes only
// from what the plan check or an implementer reports; PAGE_PIECES has page-root paths.
const BUILD_PIECES = [
  { name: 'a', files: ['backend/src/a.ts'], brief: 'Add the a helper.', tests: 'backend/src/a.test.ts' },
  { name: 'b', files: ['frontend/src/components/B.tsx'], brief: 'Add the B panel.', hard: true },
  { name: 'c', files: ['backend/src/c.ts', 'backend/src/a.ts'], brief: 'Call the a helper from c.' },
]
const BUILD_ORDER = [['a', 'b'], ['c']]
// No file under backend/ or frontend/: both scripts skip the check agent.
const DOCS_PIECES = [
  { name: 'a', files: ['docs/a.md'], brief: 'Document a.' },
  { name: 'b', files: ['scripts/b.sh'], brief: 'Script b.' },
]
// p owns a page and r a layout in a route group, so both are page roots by the golden's path
// heuristic (and the fixture's scroll-audit paths); q owns neither (a component under app/, and a
// page.tsx outside app/).
const PAGE_PIECES = [
  { name: 'p', files: ['frontend/src/app/matters/page.tsx'], brief: 'Restyle the matters page.' },
  { name: 'q', files: ['frontend/src/app/matters/Panel.tsx', 'frontend/src/components/page.tsx'], brief: 'Add the matters panel.' },
  { name: 'r', files: ['frontend/src/app/(pages)/settings/layout.jsx'], brief: 'Restyle the settings layout.' },
]
const PAGE_ORDER = [['p', 'q', 'r']]
const changelogLineOf = (name) => `- **Added — piece ${name}.** Detail`
const [CHANGELOG_CHECK, SCROLL_CHECK] = fixture.signOff || []

// The plan check returns the given waves and names planTrig as page-root pieces; implementers
// report every file they own plus CHANGELOG.md, and implTrig report the page-root change. The
// golden and neutral replies differ only where the brief renames a field: changelogLine and
// pageRootChanged (golden) are sharedFileLines and triggered (neutral); pageRootPieces is the
// plan check's triggered. A 'generic' reply has neither (a project with no sign-off checks), and
// touches only the piece's own files. `dead` labels reply null; `judge` answers the judge.
function buildResponders(kind, { implTrig = [], planTrig = [], dead = null, order = BUILD_ORDER, pieces = BUILD_PIECES, judge = null } = {}) {
  const owned = Object.fromEntries(pieces.map((p) => [p.name, p.files]))
  return [
    ...(dead ? [{ match: dead, reply: () => null }] : []),
    {
      match: /^plan-check$/,
      reply: () => (kind === 'golden' ? { ok: true, problems: [], order, pageRootPieces: planTrig }
        : kind === 'neutral' ? { ok: true, problems: [], order, triggered: planTrig.length ? [{ key: 'scroll-audit', pieces: planTrig }] : [] }
        : { ok: true, problems: [], order }),
    },
    {
      match: /^impl:/,
      reply: (prompt, opts) => {
        const name = opts.label.slice('impl:'.length)
        const common = { name, filesTouched: [...owned[name], 'CHANGELOG.md'], testResult: 'npm test: pass', notes: '', blockers: [] }
        return kind === 'golden' ? { ...common, changelogLine: changelogLineOf(name), pageRootChanged: implTrig.includes(name) }
          : kind === 'neutral' ? { ...common, sharedFileLines: [{ file: 'CHANGELOG.md', line: changelogLineOf(name) }], triggered: implTrig.includes(name) ? ['scroll-audit'] : [] }
          : { ...common, filesTouched: owned[name] }
      },
    },
    ...(judge ? [{ match: /^judge$/, reply: judge }] : []),
  ]
}
const buildArgs = (extra = {}) => ({ plan: PLAN, pieces: structuredClone(BUILD_PIECES), ...structuredClone(extra) })
// The judge's model as its route log states it ("Judge on fable (...)" / "Judge on opus").
const judgeOnLog = (logs) => logs.filter((l) => l.startsWith('Judge on ')).map((l) => l.split(' ')[2])
const judgeModel = (calls) => calls.find((c) => c.label === 'judge').model

// gk-build has no security or clientFacing argument: the grid passes them to show they change
// nothing, and adds ui (a gk-build argument) and a project check command.
const BUILD_FLAGS = [{}, { ui: true }, { escalate: true }, { security: true }, { clientFacing: true }, { check: 'npm test' }]
// [name, extra args, responder options]
const BUILD_REPORTED = [
  ['an implementer reports the scroll-audit trigger', {}, { implTrig: ['a'] }],
  ['the plan check names the scroll-audit trigger', {}, { planTrig: ['b'] }],
  ['an implementer reports the trigger, escalated', { escalate: true }, { implTrig: ['c'] }],
  // The golden lists the plan check's pieces first and the implementers' after: [c, a].
  ['the plan check names one piece and an implementer reports another', {}, { planTrig: ['c'], implTrig: ['a'] }],
  ['an implementer dies', {}, { dead: /^impl:b$/ }],
  ['a reviewer dies', {}, { dead: /^review:a$/ }],
  ['the check agent dies', {}, { dead: /^check$/ }],
  ['the judge dies', {}, { dead: /^judge$/ }],
  ['no file under backend/ or frontend/: the check is skipped', { pieces: DOCS_PIECES }, { pieces: DOCS_PIECES, order: [['a', 'b']] }],
  // pathTrig: the golden's page-root heuristic (the fixture's scroll-audit paths) meets the trigger.
  ['page-root files that no agent reports', { pieces: PAGE_PIECES }, { pieces: PAGE_PIECES, order: PAGE_ORDER, pathTrig: true }],
  ['page-root files, one also named by the plan check', { pieces: PAGE_PIECES }, { pieces: PAGE_PIECES, order: PAGE_ORDER, planTrig: ['r'], pathTrig: true }],
  ['page-root files, the judge dies', { pieces: PAGE_PIECES, escalate: true }, { pieces: PAGE_PIECES, order: PAGE_ORDER, pathTrig: true, dead: /^judge$/ }],
]
// The scroll-audit pieces of a neutral [{key, pieces}] list, in its order.
const scrollOf = (list) => { const t = (list || []).find((x) => x.key === 'scroll-audit'); return t ? t.pieces : [] }

test('routing equivalence, gk-build: golden and neutral+Goodricke make the same calls on the same (model, effort)', async () => {
  const cases = []
  for (const profile of PROFILES) {
    for (const flags of BUILD_FLAGS) cases.push([`${profile} ${JSON.stringify(flags)}`, { ...flags, profile }, {}])
    for (const [name, extra, opts] of BUILD_REPORTED) cases.push([`${profile} ${name}`, { ...extra, profile }, opts])
  }
  const judges = new Set()
  const implTiers = new Set()
  for (const [name, flags, opts] of cases) {
    const old = await runWorkflow(golden('gk-build.js'), buildArgs(flags), buildResponders('golden', opts))
    const now = await runWorkflow(neutral('gk-build.js'), { ...buildArgs(flags), project: goodricke() }, buildResponders('neutral', opts))
    assert.ok(old.calls.some((c) => c.label === 'judge'), `${name}: the golden run reaches the judge`)
    assert.deepEqual(route(now.calls), route(old.calls), name)
    assert.deepEqual(judgeOnLog(now.logs), judgeOnLog(old.logs), `${name}: the judge route log`)
    // The same page-root pieces in the same order: the golden's pageRootPieces (the plan
    // check's first, then the path heuristic's and the implementers') are the scroll-audit
    // trigger's pieces, in the plan check's result and in what the seat gets back; a dead
    // judge's stand-in lists them in both.
    const roots = old.result.planCheck.pageRootPieces
    assert.deepEqual(scrollOf(now.result.planCheck.triggered), roots, `${name}: the page-root pieces`)
    assert.deepEqual(scrollOf(now.result.signOff), roots, `${name}: the scroll-audit sign-off`)
    assert.deepEqual(scrollOf(now.result.judge.signOffRequired), old.result.judge.scrollAuditRequired, `${name}: the judge's list`)
    // Without Goodricke's sign-off trigger and package layout, the generic run makes the same
    // calls, except that its check runs (once, from the top level) only when args.check gives it
    // a command: the generic project names no check and no packages.
    if (!opts.implTrig && !opts.planTrig && !opts.pathTrig) {
      const generic = await runWorkflow(neutral('gk-build.js'), { ...buildArgs(flags), project: { root: '/tmp/repo' } }, buildResponders('neutral', opts))
      assert.deepEqual(route(generic.calls), route(old.calls).filter((c) => c[0] !== 'check' || flags.check), `${name} (generic)`)
      assert.deepEqual(judgeOnLog(generic.logs), judgeOnLog(old.logs), `${name} (generic): the judge route log`)
    }
    judges.add(`${judgeModel(old.calls)} ${opts.implTrig || opts.planTrig || opts.pathTrig ? 'triggered' : 'plain'}`)
    for (const c of old.calls.filter((x) => x.label.startsWith('impl:'))) implTiers.add(`${c.model} ${c.effort}`)
  }
  // The grid reaches the judge on both models, fable through the trigger as well as through ui.
  assert.deepEqual([...judges].sort(), ['fable plain', 'fable triggered', 'opus plain'])
  assert.deepEqual([...implTiers].sort(), ['opus high', 'opus xhigh', 'sonnet high', 'sonnet medium'])
})

test('page-root paths, gk-build: a piece owning a page or layout meets the scroll-audit trigger by its path alone, as the golden\'s heuristic did', async () => {
  // The fixture's scroll-audit paths are the golden's PAGE_ROOT_RE.
  const goldenRe = /^const PAGE_ROOT_RE = \/(.*)\/$/m.exec(readFileSync(golden('gk-build.js'), 'utf8'))[1]
  assert.deepEqual(SCROLL_CHECK.paths.map((s) => new RegExp(s).source), [goldenRe])
  const args = () => ({ plan: PLAN, pieces: structuredClone(PAGE_PIECES) })
  const base = { pieces: PAGE_PIECES, order: PAGE_ORDER }
  // No agent reports a page root: the paths alone route the judge to fable, under every profile.
  for (const profile of PROFILES) {
    const old = await runWorkflow(golden('gk-build.js'), { ...args(), profile }, buildResponders('golden', base))
    const now = await runWorkflow(neutral('gk-build.js'), { ...args(), profile, project: goodricke() }, buildResponders('neutral', base))
    assert.deepEqual(route(now.calls), route(old.calls), profile)
    assert.equal(judgeModel(now.calls), 'fable', profile)
    assert.deepEqual(old.result.planCheck.pageRootPieces, ['p', 'r'])
    assert.ok(old.logs.includes('Page-root heuristic added: p, r'), old.logs.join('\n'))
    assert.ok(now.logs.includes('Sign-off path rules added: scroll-audit: p, scroll-audit: r'), now.logs.join('\n'))
    assert.ok(now.logs.includes('Judge on fable (sign-off scroll-audit sets uiJudge)'), now.logs.join('\n'))
    assert.deepEqual(now.result.planCheck.triggered, [{ key: 'scroll-audit', pieces: ['p', 'r'] }])
    assert.deepEqual(now.result.signOff.map((e) => [e.key, e.pieces]), [['changelog', ['p', 'q', 'r']], ['scroll-audit', ['p', 'r']]])
    // The judge sees them as met, from the path rules among the rest.
    const judgePrompt = now.calls.find((c) => c.label === 'judge').prompt
    assert.ok(judgePrompt.includes('\nSign-off triggers met (from the plan check, the path rules and the implementers): [{"key":"scroll-audit","pieces":["p","r"]}]\n'), judgePrompt)
  }
  // A piece the plan check names is not added again; the path rule adds the other. The golden's
  // order holds: the plan check's piece first, then the path rule's (here against piece order).
  const namedOld = await runWorkflow(golden('gk-build.js'), args(), buildResponders('golden', { ...base, planTrig: ['r'] }))
  const named = await runWorkflow(neutral('gk-build.js'), { ...args(), project: goodricke() }, buildResponders('neutral', { ...base, planTrig: ['r'] }))
  assert.deepEqual(namedOld.result.planCheck.pageRootPieces, ['r', 'p'])
  assert.ok(named.logs.includes('Sign-off path rules added: scroll-audit: p'), named.logs.join('\n'))
  assert.deepEqual(named.result.planCheck.triggered, [{ key: 'scroll-audit', pieces: ['r', 'p'] }])
  assert.deepEqual(named.result.signOff.map((e) => [e.key, e.pieces]), [['changelog', ['p', 'q', 'r']], ['scroll-audit', ['r', 'p']]])
  assert.ok(named.logs.includes('Plan check ok: 1 wave(s) — [p, q, r]; sign-off triggers met: scroll-audit: r, p'), named.logs.join('\n'))
  assert.ok(named.calls.find((c) => c.label === 'judge').prompt.includes('\nSign-off triggers met (from the plan check, the path rules and the implementers): [{"key":"scroll-audit","pieces":["r","p"]}]\n'))
  // The judge dies: its stand-in lists the path-matched pieces, as the golden's did.
  const deadOld = await runWorkflow(golden('gk-build.js'), args(), buildResponders('golden', { ...base, dead: /^judge$/ }))
  const deadNow = await runWorkflow(neutral('gk-build.js'), { ...args(), project: goodricke() }, buildResponders('neutral', { ...base, dead: /^judge$/ }))
  assert.deepEqual(deadOld.result.judge.scrollAuditRequired, ['p', 'r'])
  assert.deepEqual(deadNow.result.judge.signOffRequired, [{ key: 'scroll-audit', pieces: ['p', 'r'] }])
  // A project without path rules has no heuristic: the same pieces get an opus judge.
  const generic = await runWorkflow(neutral('gk-build.js'), { ...args(), project: { root: '/tmp/repo' } }, buildResponders('neutral', base))
  assert.equal(judgeModel(generic.calls), 'opus')
  assert.deepEqual(generic.result.signOff, [])
  // Any project's paths: each is a regular expression over the top-level-relative path, with
  // forward slashes, so a piece given under the root or with backslashes is matched too. The
  // files a piece owns count, a test file named by tests among them (the golden matched owned).
  const signOff = [{ key: 'visual', trigger: 'any change to a screen', paths: ['^web/screens/', '\\.css$'], uiJudge: true, seat: 'Look at each screen.' }]
  const pieces = [
    { name: 'a', files: ['/tmp/repo/web/screens/Home.tsx'], brief: 'Restyle home.' },
    { name: 'b', files: ['web\\theme\\main.css'], brief: 'Restyle the theme.' },
    { name: 'c', files: ['web/lib/screens.ts', 'docs/web/screens/x.md'], brief: 'Add c.' },
    { name: 'd', files: ['web/lib/d.ts'], tests: 'web/screens/d.test.tsx', brief: 'Add d.' },
  ]
  const any = await runWorkflow(neutral('gk-build.js'), { plan: PLAN, pieces, project: { root: '/tmp/repo', signOff } }, [
    { match: /^plan-check$/, reply: () => ({ ok: true, problems: [], order: [['a', 'b', 'c', 'd']], triggered: [] }) },
  ])
  assert.deepEqual(any.result.signOff, [{ key: 'visual', seat: 'Look at each screen.', pieces: ['a', 'b', 'd'] }])
  assert.equal(judgeModel(any.calls), 'fable')
})

// Goodricke sentences of the golden that the marker misses, one reason each; checked verbatim.
const BUILD_UNMARKED = [
  {
    sentence: 'Put it under this work\'s `### <Area — topic>` heading if one already exists near the top of [Unreleased], else directly below `## [Unreleased]`; never add headings.',
    reason: 'Goodricke\'s CHANGELOG house rule; it reaches implementers as the changelog sign-off check\'s implementer text',
  },
  {
    sentence: 'It is the one shared file: re-read it immediately before editing, insert only your line, never rewrite other lines; if the edit fails because the file changed, re-read and retry.',
    reason: 'Goodricke\'s CHANGELOG editing rule; part of the changelog implementer text',
  },
  {
    sentence: 'Pure refactors/tests/docs need no line (return "").',
    reason: 'Goodricke\'s CHANGELOG exemption; part of the changelog implementer text ("" is the line of its sharedFileLines entry)',
  },
  {
    sentence: 'Run from the owning package (backend/ or frontend/), e.g. `set -o pipefail; npx vitest run <file> 2>&1 | tail -40`, in the FOREGROUND.',
    reason: 'Goodricke\'s package layout and test runner; a gk-build guard on the Implement stage (the core says to run the tests in the foreground from the folder their runner expects)',
  },
  {
    sentence: 'If the command is "npm run typecheck" and that package has no typecheck script, run `set -o pipefail; npx tsc --noEmit 2>&1 | tail -40; echo "EXIT=$?"` instead and say so.',
    reason: 'Goodricke\'s default check command (the fixture\'s check) and its TypeScript fallback; a gk-build guard on the Check stage',
  },
]

// The implementer report as the neutral script records it: the brief renames changelogLine to
// sharedFileLines and pageRootChanged to triggered.
const neutralReport = ({ changelogLine, pageRootChanged, ...rest }) => ({
  ...rest,
  sharedFileLines: [{ file: 'CHANGELOG.md', line: changelogLine }],
  triggered: pageRootChanged ? ['scroll-audit'] : [],
})
const jsonAfter = (prefix, s) => JSON.parse(s.slice(prefix.length))

// Golden text that does not survive verbatim, one reason each, and the text that replaces it.
// `golden` is the sentence (or a pattern for one whose JSON varies); `replacedBy` lists text that
// must all be in the matching neutral prompt (or computes it from the golden sentence).
const BUILD_EXCEPTIONS = [
  {
    golden: 'Work in /home/carls/apps/mike-dev (no worktree).',
    reason: 'the repository now comes from args.project.root, so the prompt names it as "the git repository at <root>" (as gk-review does)',
    replacedBy: ['Work in the git repository at /home/carls/apps/mike-dev (no worktree).'],
  },
  {
    golden: 'pageRootPieces = pieces touching a page root (a frontend/src/app page or layout, or a component that becomes a page root) so the scroll audit runs.',
    reason: 'the plan check names the pieces that meet each sign-off trigger; Goodricke\'s scroll-audit trigger carries this page-root definition verbatim',
    replacedBy: [
      'triggered = for each sign-off trigger below, the pieces whose work meets it, as {key, pieces} (leave out a trigger no piece meets), so the seat runs that check:',
      '- scroll-audit: any change to a page root (a frontend/src/app page or layout, or a component that becomes a page root)',
    ],
  },
  {
    golden: 'CHANGELOG: if app behaviour changed, the /home/carls/apps/mike-dev/CHANGELOG.md [Unreleased] entry is part of the deliverable — add exactly ONE bullet line in the house form `- **Added|Changed|Fixed|Removed — <summary>.** <detail>` (the Keep a Changelog group is the bullet\'s prefix; read a few existing entries first), British English, no exclamation marks, describing the change itself (never test counts).',
    reason: 'the changelog implementer text lives in the project file, which cannot know the root (the hook adds it per checkout, and Goodricke worktrees have their own), so it names CHANGELOG.md relative to the repository the brief names',
    replacedBy: ['CHANGELOG: if app behaviour changed, the CHANGELOG.md [Unreleased] entry is part of the deliverable — add exactly ONE bullet line in the house form `- **Added|Changed|Fixed|Removed — <summary>.** <detail>` (the Keep a Changelog group is the bullet\'s prefix; read a few existing entries first), British English, no exclamation marks, describing the change itself (never test counts).'],
  },
  {
    golden: 'If you change a page root (a frontend/src/app page/layout, or a component that becomes a page root), say so in notes and set pageRootChanged so the seat runs the scroll audit; page roots use h-full + overflow-y-auto.',
    reason: 'the brief renames pageRootChanged to triggered (the keys of the sign-off triggers a piece met); the page-root definition is verbatim (ruling R34)',
    replacedBy: ['If you change a page root (a frontend/src/app page/layout, or a component that becomes a page root), say so in notes and list scroll-audit in triggered so the seat runs the scroll audit; page roots use h-full + overflow-y-auto.'],
  },
  {
    golden: /^Read the plan, then the change: in \/home\/carls\/apps\/mike-dev run .*; confirm the reported CHANGELOG line is in `git diff -- CHANGELOG\.md`\.$/,
    reason: 'the core confirms each shared file\'s reported line by the file\'s path, quoted like the piece\'s own files',
    replacedBy: (s) => [s.replace('confirm the reported CHANGELOG line is in `git diff -- CHANGELOG.md`.', 'confirm the reported CHANGELOG.md line is in `git diff -- \'CHANGELOG.md\'`.')],
  },
  {
    golden: /^Implementer's report: /,
    reason: 'the report carries sharedFileLines in place of changelogLine (the brief\'s implementer schema)',
    replacedBy: (s) => {
      const { changelogLine, ...rest } = jsonAfter('Implementer\'s report: ', s)
      return [`Implementer's report: ${JSON.stringify({ ...rest, sharedFileLines: [{ file: 'CHANGELOG.md', line: changelogLine }] })}`]
    },
  },
  {
    golden: 'Judge the diff against the brief and the plan: correctness bugs, missed requirements, broken callers, missing/weak tests, a missing or wrong CHANGELOG line for a behaviour change, page roots without h-full + overflow-y-auto, PostgREST list reads without a .range() paging loop.',
    reason: 'the generic list ends at missing/weak tests and each sign-off check\'s reviewer text joins it in project order; the PostgREST item is no sign-off check, so it is a guard on gk-build\'s Review stage',
    replacedBy: [
      'Judge the diff against the brief and the plan: correctness bugs, missed requirements, broken callers, missing/weak tests, a missing or wrong CHANGELOG line for a behaviour change, page roots without h-full + overflow-y-auto.',
      '- Flag PostgREST list reads without a .range() paging loop.',
    ],
  },
  {
    golden: /^Implementation reports: /,
    reason: 'the reports carry sharedFileLines and triggered in place of changelogLine and pageRootChanged (the brief\'s implementer schema)',
    replacedBy: (s) => [`Implementation reports: ${JSON.stringify(jsonAfter('Implementation reports: ', s).map(neutralReport))}`],
  },
  {
    golden: /^Page-root pieces: /,
    reason: 'the judge sees the sign-off triggers met, by key, in place of the page-root pieces (the path rules are the golden\'s page-root heuristic)',
    replacedBy: (s) => {
      const m = /^Page-root pieces: (\[.*?\])( \(this is UI work\))?$/.exec(s)
      const pieces = JSON.parse(m[1])
      return [`Sign-off triggers met (from the plan check, the path rules and the implementers): ${JSON.stringify(pieces.length ? [{ key: 'scroll-audit', pieces }] : [])}${m[2] || ''}`]
    },
  },
  {
    golden: 'ship = false if the check failed, any piece is missing/unreviewed, any confirmed high-severity finding stands, or a behaviour change lacks its CHANGELOG line.',
    reason: 'the core names the shared-file lines its sign-off checks need, each with the check\'s reviewer text',
    replacedBy: ['ship = false if the check failed, any piece is missing/unreviewed, any confirmed high-severity finding stands, or a shared-file line a sign-off check needs is missing or wrong (CHANGELOG.md: a missing or wrong CHANGELOG line for a behaviour change).'],
  },
  {
    golden: 'scrollAuditRequired = page routes (e.g. /matters/:id/workflow) or component names the seat must put through the scroll audit — every page-root piece at minimum.',
    reason: 'the brief\'s judge schema: signOffRequired lists pieces by sign-off key; the scroll-audit seat text tells the seat to audit the pages those pieces touch',
    replacedBy: ['signOffRequired = for each sign-off trigger, the pieces whose work meets it, as {key, pieces} — every piece under "Sign-off triggers met" at minimum.'],
  },
]
const goldenIs = (e, s) => (typeof e.golden === 'string' ? e.golden === s : e.golden.test(s))
const replacementsFor = (e, s) => (typeof e.replacedBy === 'function' ? e.replacedBy(s) : e.replacedBy)

test('goodricke sentences preserved, gk-build: every marked or listed golden sentence is in the matching neutral prompt', async () => {
  const used = new Set()
  const seenUnmarked = new Set()
  let checked = 0
  // With and without a page-root report and ui, so both forms of the page-root line are seen,
  // with page-root files that only the path rules (the golden's heuristic) find, and with one
  // the plan check names ahead of one the path rules add (the golden lists ["r","p"]).
  for (const [flags, opts] of [
    [{ ui: true }, { implTrig: ['a'] }],
    [{}, {}],
    [{ pieces: PAGE_PIECES }, { pieces: PAGE_PIECES, order: PAGE_ORDER }],
    [{ pieces: PAGE_PIECES }, { pieces: PAGE_PIECES, order: PAGE_ORDER, planTrig: ['r'] }],
  ]) {
    const old = await runWorkflow(golden('gk-build.js'), buildArgs(flags), buildResponders('golden', opts))
    const now = await runWorkflow(neutral('gk-build.js'), { ...buildArgs(flags), project: goodricke() }, buildResponders('neutral', opts))
    const byLabel = new Map(now.calls.map((c) => [c.label, ws(c.prompt)]))
    assert.deepEqual([...byLabel.keys()].sort(), old.calls.map((c) => c.label).sort(), 'the same stages run')
    const listed = (x) => BUILD_UNMARKED.some((u) => u.sentence === x)
    const excepted = (x) => BUILD_EXCEPTIONS.find((e) => goldenIs(e, x))
    for (const c of old.calls) {
      const prompt = byLabel.get(c.label)
      for (const s of sentences(c.prompt).filter((x) => MARK.test(x) || listed(x) || excepted(x))) {
        if (listed(s)) seenUnmarked.add(s)
        const exception = excepted(s)
        if (exception) {
          used.add(exception)
          for (const r of replacementsFor(exception, s)) assert.ok(prompt.includes(ws(r)), `${c.label}: the replacement for an excepted sentence is missing:\n  ${r}\n(golden: ${s})`)
          continue
        }
        assert.ok(prompt.includes(s), `${c.label}: golden sentence missing from the neutral prompt:\n  ${s}`)
        checked++
      }
    }
  }
  assert.ok(checked > 0, 'some marked sentences were checked')
  for (const e of BUILD_EXCEPTIONS) assert.ok(used.has(e), `stale exception (no golden prompt has it): ${e.golden}`)
  for (const u of BUILD_UNMARKED) assert.ok(seenUnmarked.has(u.sentence), `stale listed sentence (no golden prompt has it): ${u.sentence}`)
})

test('stage schemas, gk-build: the check and review schemas are the golden\'s; the others change only the renamed fields', async () => {
  const old = await schemasOf(golden('gk-build.js'), buildArgs(), buildResponders('golden'))
  const now = await schemasOf(neutral('gk-build.js'), { ...buildArgs(), project: goodricke() }, buildResponders('neutral'))
  assert.deepEqual([...now.keys()].sort(), [...old.keys()].sort(), 'the same stages run')
  for (const label of ['check', 'review:a', 'review:b', 'review:c']) assert.equal(now.get(label), old.get(label), label)
  const shape = (json) => { const s = JSON.parse(json); return [Object.keys(s.properties), s.required] }
  // pageRootPieces → triggered (plan check); changelogLine → sharedFileLines and pageRootChanged
  // → triggered (implementers); scrollAuditRequired → signOffRequired (judge). Same order, same
  // required-ness as the field each replaces.
  assert.deepEqual(shape(old.get('plan-check')), [['ok', 'problems', 'order', 'pageRootPieces'], ['ok', 'problems', 'order', 'pageRootPieces']])
  assert.deepEqual(shape(now.get('plan-check')), [['ok', 'problems', 'order', 'triggered'], ['ok', 'problems', 'order', 'triggered']])
  assert.deepEqual(shape(old.get('impl:a')), [['name', 'filesTouched', 'changelogLine', 'testResult', 'notes', 'blockers', 'pageRootChanged'], ['name', 'filesTouched', 'changelogLine', 'testResult', 'notes', 'blockers']])
  assert.deepEqual(shape(now.get('impl:a')), [['name', 'filesTouched', 'sharedFileLines', 'testResult', 'notes', 'blockers', 'triggered'], ['name', 'filesTouched', 'sharedFileLines', 'testResult', 'notes', 'blockers']])
  assert.deepEqual(shape(old.get('judge')), [['ship', 'blockers', 'reworkPieces', 'scrollAuditRequired'], ['ship', 'blockers', 'reworkPieces', 'scrollAuditRequired']])
  assert.deepEqual(shape(now.get('judge')), [['ship', 'blockers', 'reworkPieces', 'signOffRequired'], ['ship', 'blockers', 'reworkPieces', 'signOffRequired']])
  const impl = JSON.parse(now.get('impl:a')).properties
  assert.deepEqual(impl.sharedFileLines.items.required, ['file', 'line'])
  assert.deepEqual(impl.triggered.items.enum, ['scroll-audit'])
  const judge = JSON.parse(now.get('judge')).properties
  assert.deepEqual(judge.signOffRequired.items.properties.key.enum, ['scroll-audit'])
  assert.deepEqual(judge.signOffRequired.items.required, ['key', 'pieces'])
})

// Each sign-off text reaches its own stage and no other: an implementer text each implementer,
// once; a reviewer item each reviewer, once, and, for a check with a shared file, the judge's
// ship condition, once, after the file's name; a shared-file confirmation each reviewer, once.
// `rel` gives a shared file as the prompts name it (relative to the top level).
function assertSignOffStages(calls, signOff, rel = (f) => f) {
  const count = (s, sub) => s.split(sub).length - 1
  assert.ok(['plan-check', 'impl:', 'review:', 'judge'].every((l) => calls.some((c) => c.label.startsWith(l))), 'every agent stage ran')
  for (const k of signOff) {
    const item = k.reviewer && k.reviewer.trim().replace(/\.$/, '')
    const file = k.sharedFile && rel(k.sharedFile)
    const confirm = file && `; confirm the reported ${file} line is in \`git diff -- '${file}'\``
    for (const c of calls) {
      const impl = c.label.startsWith('impl:')
      const review = c.label.startsWith('review:')
      const judge = c.label === 'judge'
      if (k.implementer) assert.equal(count(c.prompt, k.implementer.trim()), impl ? 1 : 0, `${c.label}: ${k.key} implementer text`)
      if (item) assert.equal(count(c.prompt, item), review || (judge && file) ? 1 : 0, `${c.label}: ${k.key} reviewer item`)
      if (item && review) assert.ok(c.prompt.includes(`, ${item}`), `${c.label}: ${k.key} reviewer item in the list`)
      if (item && judge && file) assert.ok(c.prompt.includes(`${file}: ${item}`), `${c.label}: ${k.key} reviewer item in the ship condition`)
      if (confirm) assert.equal(count(c.prompt, confirm), review ? 1 : 0, `${c.label}: ${k.key} shared-file confirmation`)
    }
  }
}

test('goodricke project, gk-build: each stage brief ends with the guards that reach it; the sign-off texts reach their stages; the log names the project', async () => {
  const { calls, logs } = await runWorkflow(neutral('gk-build.js'), { ...buildArgs(), project: goodricke() }, buildResponders('neutral'))
  assert.deepEqual([...new Set(calls.map((c) => c.phase))], ['Plan check', 'Implement', 'Check', 'Review', 'Judge'])
  for (const c of calls) {
    const block = rulesBlock(reach(fixture.guards, 'gk-build', c.phase))
    assert.ok(block, `${c.label}: some guard reaches the ${c.phase} stage`)
    assert.ok(c.prompt.endsWith(block), `${c.label}: the brief ends with the project rules for the ${c.phase} stage`)
    assert.equal(c.prompt.split('Project rules:').length, 2, `${c.label}: one project rules block`)
  }
  const triggerLine = `\n- scroll-audit: ${SCROLL_CHECK.trigger}\n`
  for (const c of calls) {
    const p = c.prompt
    if (c.label.startsWith('impl:')) {
      assert.ok(p.includes(`\n${CHANGELOG_CHECK.implementer}\n${SCROLL_CHECK.implementer}\n`), `${c.label}: the implementer texts, in project order`)
      assert.ok(p.includes(triggerLine), `${c.label}: the trigger`)
      assert.ok(p.includes(' — plus the single CHANGELOG.md line below. '), `${c.label}: the shared file`)
    }
    if (c.label.startsWith('review:')) assert.ok(p.includes(`missing/weak tests, ${CHANGELOG_CHECK.reviewer}, ${SCROLL_CHECK.reviewer}. Only real defects`), `${c.label}: the reviewer texts`)
    if (c.label === 'plan-check' || c.label === 'judge') assert.ok(p.includes(triggerLine), `${c.label}: the trigger`)
    // The seat's lines are for the seat, never an agent.
    for (const check of fixture.signOff) assert.ok(!p.includes(check.seat), `${c.label}: no seat text`)
  }
  // ... and the implementer, reviewer and shared-file texts reach no other stage, with or
  // without a page-root piece.
  assertSignOffStages(calls, fixture.signOff)
  const page = await runWorkflow(neutral('gk-build.js'), { plan: PLAN, pieces: structuredClone(PAGE_PIECES), project: goodricke() }, buildResponders('neutral', { pieces: PAGE_PIECES, order: PAGE_ORDER }))
  assertSignOffStages(page.calls, fixture.signOff)
  const n = (k) => (fixture[k] || []).length
  assert.ok(logs.includes(`Project: Goodricke (${n('guards')} guards, ${n('reviewFinders')} finders, ${n('signOff')} sign-off checks)`), logs.join('\n'))
  assert.equal(n('signOff'), 2)
})

test('goodricke guards, gk-build: each scoped guard reaches exactly the stages where the golden has its sentence; string guards reach every stage', async () => {
  // The guard that replaces one item of the golden's review list (see BUILD_EXCEPTIONS).
  const originals = new Map([['Flag PostgREST list reads without a .range() paging loop.', 'PostgREST list reads without a .range() paging loop']])
  const old = await runWorkflow(golden('gk-build.js'), buildArgs(), buildResponders('golden'))
  const now = await runWorkflow(neutral('gk-build.js'), { ...buildArgs(), project: goodricke() }, buildResponders('neutral'))
  const all = now.calls.map((c) => c.label)
  let own = 0
  for (const g of fixture.guards) {
    const text = typeof g === 'string' ? g : g.text
    const got = now.calls.filter((c) => c.prompt.includes(`\n- ${text}`)).map((c) => c.label)
    if (typeof g === 'string') {
      // A string guard binds every step in the repository (ruling R15), golden text or not.
      assert.deepEqual(got, all, `string guard: ${text}`)
      continue
    }
    const want = old.calls.filter((c) => ws(c.prompt).includes(ws(originals.get(text) || text))).map((c) => c.label)
    assert.deepEqual(got, want, `the stages this guard reaches:\n  ${text}`)
    if (g.workflows && g.workflows.includes('gk-build')) { own++; assert.ok(want.length, `a gk-build guard with no golden sentence: ${text}`) }
  }
  assert.equal(own, 6, 'six gk-build guards')
})

test('goodricke guards, every guard that reaches gk-review or gk-build: each has a golden sentence behind it in one of them', async () => {
  const prompts = []
  for (const target of ['develop..HEAD', 'working-tree']) prompts.push(...(await runWorkflow(golden('gk-review.js'), { target }, reviewResponders())).calls.map((c) => ws(c.prompt)))
  prompts.push(...(await runWorkflow(golden('gk-build.js'), buildArgs(), buildResponders('golden'))).calls.map((c) => ws(c.prompt)))
  // A guard that replaces golden text (the listed exceptions) stands for that text.
  const originals = new Map([
    ...PROMPT_EXCEPTIONS.map((e) => [e.replacedBy, e.sentence]),
    ['Flag PostgREST list reads without a .range() paging loop.', 'PostgREST list reads without a .range() paging loop'],
  ])
  // The guards scoped to gk-design, gk-research or gk-diagnose are checked against their own
  // goldens in design-research-diagnose.test.mjs.
  const guards = fixture.guards.filter(reachesReviewOrBuild)
  assert.equal(guards.length, 14, 'two string guards, six gk-review guards and six gk-build guards')
  for (const g of guards) {
    const text = typeof g === 'string' ? g : g.text
    const original = ws(originals.get(text) || text)
    assert.ok(prompts.some((p) => p.includes(original)), `a guard with no golden sentence behind it: ${text}`)
  }
})

// Words that name Goodricke or its build rules, for gk-build: the review list plus the page-root
// and CHANGELOG vocabulary and the sign-off machinery, which a project without sign-off checks
// never sees.
const BUILD_WORDS = new RegExp(`${GOODRICKE_WORDS.source}|page.root|scroll.audit|Unreleased|h-full|npm run dev|\\.next\\b|sign-off|signOff|triggered|sharedFile`, 'i')
// The golden's stack: its test runner, its typecheck and its package folders. No generic prompt
// names the first two; a run whose pieces live elsewhere names no backend or frontend either.
const STACK_WORDS = /vitest|typecheck|\btsc\b|\bnpx\b/i
const LAYOUT_WORDS = /backend|frontend/i
const PY_PIECES = [
  { name: 'a', files: ['src/app/a.py'], brief: 'Add the a helper.', tests: 'tests/test_a.py' },
  { name: 'b', files: ['src/app/b.py', 'docs/b.md'], brief: 'Add b.' },
]

// The generic run's skipped check: no command, so no check ran, and the result and the judge
// say so rather than report a pass.
const NO_COMMAND = 'no check command (args.check is not given and the project names none)'
const SKIPPED_CHECK = { passed: null, skipped: true, output: `skipped: ${NO_COMMAND}` }
const SKIPPED_JUDGE_LINE = `\nProject check: the project-wide check was skipped: ${NO_COMMAND}; judge on the diff and the per-piece tests.\n`

test('generic run is clean, gk-build: no prompt names Goodricke, its stack or a sign-off check, and every prompt names the root', async () => {
  // [pieces, order, args, project fields]; the last case names packages (Python ones).
  for (const [pieces, order, extra, fields] of [
    [BUILD_PIECES, BUILD_ORDER, {}, {}],
    [DOCS_PIECES, [['a', 'b']], {}, {}],
    [PY_PIECES, [['a', 'b']], { check: 'python -m pytest -q' }, {}],
    [PY_PIECES, [['a', 'b']], { check: 'python -m pytest -q' }, { packages: ['src', 'tools'] }],
  ]) {
    const args = { plan: PLAN, pieces: structuredClone(pieces), ui: true, ...extra, project: { root: '/tmp/repo', ...fields } }
    const { calls, logs, result } = await runWorkflow(neutral('gk-build.js'), args, buildResponders('generic', { pieces, order }))
    assert.ok(calls.some((c) => c.label === 'judge'), 'the run reaches the judge')
    const layout = pieces === PY_PIECES
    for (const c of calls) {
      const hit = BUILD_WORDS.exec(c.prompt) || STACK_WORDS.exec(c.prompt) || (layout && LAYOUT_WORDS.exec(c.prompt))
      assert.ok(!hit, `${c.label}: generic prompt names ${hit && hit[0]}`)
      assert.ok(c.prompt.includes('Work in the git repository at /tmp/repo (no worktree).'), `${c.label}: names the root`)
      assert.ok(!c.prompt.includes('Project rules:'), `${c.label}: no project rules without guards`)
      assert.ok(c.prompt.includes('Do NOT commit, stash, reset or checkout.'), `${c.label}: the seat commits, never an agent`)
    }
    // No shared file: the owned list ends the sentence. Tests run from the owning package when
    // the project names packages, else from the top level, with the piece's own test command
    // (an implementer with no named tests uses the project's own runner): no runner is assumed.
    for (const c of calls.filter((x) => x.label.startsWith('impl:'))) {
      const p = c.prompt
      assert.match(p, /You may edit ONLY these files \(create them if missing\): \["[^\]]*"\]\. Other pieces/)
      const from = fields.packages ? 'from the package that owns them (src/ or tools/; the top level when none does)' : 'from the repository\'s top level'
      const named = pieces.find((x) => `impl:${x.name}` === c.label).tests
      const runWith = named ? 'with the test command the piece gives (for a test file, the project\'s own runner)' : 'with the project\'s own test runner'
      assert.ok(p.includes(`. Run them in the FOREGROUND, ${from}, ${runWith}.\n`), p)
    }
    assert.ok(calls.find((c) => c.label === 'plan-check').prompt.includes('Plan/spec file: /tmp/repo/docs/plan.md — read it in full.'))
    // The check runs whenever args.check gives a command: once from the top level without
    // packages (whatever the pieces' folders), from each affected package with them. Without a
    // command it is skipped, and the result and the judge say so rather than report a pass.
    const check = calls.find((c) => c.label === 'check')
    const judge = calls.find((c) => c.label === 'judge').prompt
    if (extra.check && fields.packages) {
      assert.ok(check.prompt.startsWith(`Run the project check and report. For each package in ["src"]: cd /tmp/repo/<package> and run \`set -o pipefail; ${extra.check} 2>&1 | tail -40; echo "EXIT=$?"\``), check.prompt)
    } else if (extra.check) {
      assert.ok(check.prompt.startsWith(`Run the project check and report. From /tmp/repo, run \`set -o pipefail; ${extra.check} 2>&1 | tail -40; echo "EXIT=$?"\` in the FOREGROUND (without pipefail, tail hides the failure). Do not fix anything; edit nothing. passed = it printed EXIT=0 and showed no errors. output = the command run and its tailed output.\nRules:`), check.prompt)
    } else {
      assert.equal(check, undefined)
      assert.deepEqual(result.check, SKIPPED_CHECK)
      assert.ok(judge.includes(SKIPPED_JUDGE_LINE), judge)
      assert.ok(logs.some((l) => l.startsWith('Check skipped; reviews: ')), logs.join('\n'))
    }
    if (check) assert.deepEqual([check.model, check.effort], ['haiku', 'low'])
    if (check) assert.ok(!judge.includes('was skipped'), judge)
    assert.deepEqual(result.signOff, [])
    assert.ok(!('signOffRequired' in result.judge) && !('triggered' in result.planCheck), JSON.stringify(result))
    assert.ok(logs.includes('Project: none (generic rules)'), logs.join('\n'))
  }
  // The usage text is generic too: example paths with no package layout, and the check's
  // default is the project's check or args.check, with no command assumed.
  const src = readFileSync(neutral('gk-build.js'), 'utf8')
  const whenToUse = /^ {2}whenToUse: '((?:[^'\\]|\\.)*)',$/m.exec(src)[1].replace(/\\'/g, '\'')
  const hit = GOODRICKE_WORDS.exec(whenToUse) || STACK_WORDS.exec(whenToUse) || LAYOUT_WORDS.exec(whenToUse)
  assert.ok(!hit, `meta.whenToUse names ${hit && hit[0]}`)
  assert.ok(whenToUse.includes('files: ["src/...", "tests/..."]'), whenToUse)
  assert.ok(whenToUse.includes('check?: "<project-wide check command; default the project\'s check, and with neither the check is skipped'), whenToUse)
})

test('generic run without a root, gk-build: prompts point at the working directory\'s repository and its relative paths', async () => {
  for (const project of [undefined, {}]) {
    const args = buildArgs({ check: 'npm test' })
    if (project !== undefined) args.project = project
    const { calls, logs } = await runWorkflow(neutral('gk-build.js'), args, buildResponders('neutral'))
    assert.equal(calls.length, 9)
    for (const c of calls) {
      assert.ok(c.prompt.includes('Work in the git repository containing your working directory (run `git rev-parse --show-toplevel` first), not in a worktree; the paths in this brief are relative to its top level.'), `${JSON.stringify(project)} ${c.label}`)
      assert.ok(!c.prompt.includes('the git repository at'), `${JSON.stringify(project)} ${c.label}`)
    }
    assert.ok(calls.find((c) => c.label === 'plan-check').prompt.includes('Plan/spec file: docs/plan.md — read it in full.'))
    assert.ok(calls.find((c) => c.label === 'check').prompt.includes('Run the project check and report. From the repository\'s top level, run `set -o pipefail; npm test 2>&1'))
    assert.ok(calls.find((c) => c.label === 'review:a').prompt.includes('Read the plan, then the change: in the repository\'s top level run `git diff --'))
    assert.ok(logs.includes('Project: none (generic rules)'), logs.join('\n'))
  }
})

test('check stage, gk-build: the check runs from each affected project package, once from the top level without packages, and not at all without a command', async () => {
  const checkOf = (r) => r.calls.find((c) => c.label === 'check')
  const logOf = (r, prefix) => r.logs.filter((l) => l.startsWith(prefix))
  const head = (p) => p.slice(0, p.indexOf('\nRules:'))
  // Goodricke (check "npm run typecheck", packages backend and frontend): the golden's check
  // stage, its prompt word for word but for the TypeScript fallback, which is a Check-stage guard.
  const fallback = ' If the command is "npm run typecheck" and that package has no typecheck script, run `set -o pipefail; npx tsc --noEmit 2>&1 | tail -40; echo "EXIT=$?"` instead and say so.'
  for (const [pieces, order, extra] of [[BUILD_PIECES, BUILD_ORDER, {}], [BUILD_PIECES, BUILD_ORDER, { check: 'npm test' }], [PAGE_PIECES, PAGE_ORDER, {}], [DOCS_PIECES, [['a', 'b']], { check: 'npm test' }]]) {
    const name = `${pieces.map((p) => p.name).join('')} ${JSON.stringify(extra)}`
    const old = await runWorkflow(golden('gk-build.js'), { plan: PLAN, pieces: structuredClone(pieces), ...extra }, buildResponders('golden', { pieces, order }))
    const now = await runWorkflow(neutral('gk-build.js'), { plan: PLAN, pieces: structuredClone(pieces), ...extra, project: goodricke() }, buildResponders('neutral', { pieces, order }))
    assert.deepEqual(now.result.check, old.result.check, name)
    assert.deepEqual(logOf(now, 'Check: '), logOf(old, 'Check: '), name)
    assert.deepEqual(logOf(now, 'Check '), logOf(old, 'Check '), name)
    assert.deepEqual(logOf(now, 'Plan check over '), logOf(old, 'Plan check over '), name)
    // The judge reads the check exactly as the golden's did (Goodricke's check always has a
    // command, so it is never reported as skipped).
    const checkLine = (r) => r.calls.find((c) => c.label === 'judge').prompt.split('\n').find((l) => l.startsWith('Project check: '))
    assert.equal(checkLine(now), checkLine(old), name)
    assert.equal(Boolean(checkOf(now)), Boolean(checkOf(old)), name)
    if (checkOf(old)) {
      assert.ok(head(checkOf(old).prompt).includes(fallback), name)
      assert.equal(head(checkOf(now).prompt), head(checkOf(old).prompt).replace(fallback, ''), name)
    }
  }
  // Any project's check and packages: three pieces, one in each package folder and one in docs.
  const pieces = [
    { name: 'a', files: ['web/src/a.ts'], brief: 'Add a.' },
    { name: 'b', files: ['svc/api/b.go'], brief: 'Add b.' },
    { name: 'c', files: ['docs/c.md'], brief: 'Add c.' },
  ]
  const run = (project, extra = {}, ps = pieces) => runWorkflow(neutral('gk-build.js'), { plan: PLAN, pieces: structuredClone(ps), ...extra, project }, buildResponders('generic', { pieces: ps, order: [ps.map((p) => p.name)] }))
  // The project's check is the default command. Packages given under the root, with a trailing
  // slash or with backslashes are folders relative to the top level; the affected ones keep the
  // project's order.
  const pk = await run({ root: '/tmp/repo', check: 'make check', packages: ['tools', '/tmp/repo/web/', 'svc\\api'] })
  assert.ok(checkOf(pk).prompt.startsWith('Run the project check and report. For each package in ["web","svc/api"]: cd /tmp/repo/<package> and run `set -o pipefail; make check 2>&1 | tail -40; echo "EXIT=$?"` in the FOREGROUND'), checkOf(pk).prompt)
  assert.ok(pk.logs.includes('Plan check over 3 pieces; affected packages: web, svc/api'), pk.logs.join('\n'))
  // args.check wins over the project's check.
  const over = await run({ root: '/tmp/repo', check: 'make check', packages: ['web'] }, { check: 'make lint' })
  assert.ok(checkOf(over).prompt.includes('For each package in ["web"]: cd /tmp/repo/<package> and run `set -o pipefail; make lint 2>&1'), checkOf(over).prompt)
  // Nested packages: a file belongs to the longest package folder it lies under, whatever the
  // project's order, so web/admin's check runs for web/admin/x.ts and web's only for a file of
  // web outside web/admin. The affected ones keep the project's order.
  const nested = [
    { name: 'n', files: ['web/admin/x.ts'], brief: 'Add n.' },
    { name: 'o', files: ['web/o.ts'], brief: 'Add o.' },
    { name: 'w', files: ['web/administration/w.ts'], brief: 'Add w.' },
  ]
  for (const packages of [['web', 'web/admin'], ['web/admin', 'web']]) {
    const only = await run({ root: '/tmp/repo', check: 'make check', packages }, {}, [nested[0]])
    assert.ok(checkOf(only).prompt.startsWith('Run the project check and report. For each package in ["web/admin"]: '), checkOf(only).prompt)
    assert.ok(only.logs.includes('Plan check over 1 pieces; affected packages: web/admin'), only.logs.join('\n'))
    // web/administration is under web, not web/admin (the "/" boundary).
    const both = await run({ root: '/tmp/repo', check: 'make check', packages }, {}, nested)
    assert.ok(checkOf(both).prompt.startsWith(`Run the project check and report. For each package in ${JSON.stringify(packages)}: `), checkOf(both).prompt)
    const under = await run({ root: '/tmp/repo', check: 'make check', packages }, {}, [nested[2]])
    assert.ok(checkOf(under).prompt.startsWith('Run the project check and report. For each package in ["web"]: '), checkOf(under).prompt)
  }
  // No piece owns a file in a package: nothing the check covers changed, so it passes without
  // running, as the golden's did, naming the packages.
  const outside = await run({ root: '/tmp/repo', check: 'make check', packages: ['tools', 'web', 'svc/api'] }, {}, [pieces[2]])
  assert.equal(checkOf(outside), undefined)
  assert.deepEqual(outside.result.check, { passed: true, output: 'skipped: no files under tools/, web/ or svc/api/' })
  // No packages: once from the top level, whatever folders the pieces own.
  const top = await run({ root: '/tmp/repo', check: 'make check' }, {}, [pieces[2]])
  assert.ok(checkOf(top).prompt.startsWith('Run the project check and report. From /tmp/repo, run `set -o pipefail; make check 2>&1'), checkOf(top).prompt)
  assert.ok(top.logs.includes('Check: "make check" from the top level; Review: 1 piece(s)'), top.logs.join('\n'))
  assert.ok(top.logs.includes('Plan check over 1 pieces; check: from the top level'), top.logs.join('\n'))
  // No command, with packages or without: no check runs, so the result says skipped (passed is
  // null, not true), and the judge is told to judge on the diff and the per-piece tests.
  for (const project of [{ root: '/tmp/repo' }, { root: '/tmp/repo', packages: ['web'] }]) {
    const r = await run(project)
    assert.equal(checkOf(r), undefined, JSON.stringify(project))
    assert.deepEqual(r.result.check, SKIPPED_CHECK)
    const judge = r.calls.find((c) => c.label === 'judge').prompt
    assert.ok(judge.includes(SKIPPED_JUDGE_LINE), judge)
    assert.ok(!judge.includes('"passed"'), judge)
    assert.ok(r.logs.includes('Check: no command — skipped; Review: 3 piece(s)'), r.logs.join('\n'))
    assert.ok(r.logs.some((l) => l.startsWith('Check skipped; reviews: ')), r.logs.join('\n'))
  }
})

test('build returns signOff: an untriggered check lists every piece, a triggered one the pieces that met it', async () => {
  const pieces = [
    { name: 'a', files: ['frontend/src/components/A.tsx'], brief: 'Make A the matters page root.' },
    { name: 'b', files: ['backend/src/b.ts'], brief: 'Add b.' },
  ]
  const run = (opts) => runWorkflow(neutral('gk-build.js'), { plan: PLAN, pieces: structuredClone(pieces), project: goodricke() }, buildResponders('neutral', { pieces, order: [['a', 'b']], ...opts }))
  const changelog = (names) => ({ key: 'changelog', seat: CHANGELOG_CHECK.seat, pieces: names })
  const scroll = (names) => ({ key: 'scroll-audit', seat: SCROLL_CHECK.seat, pieces: names })

  // One piece triggers the scroll audit and one does not.
  const one = await run({ implTrig: ['a'] })
  assert.deepEqual(one.result.signOff, [changelog(['a', 'b']), scroll(['a'])])
  assert.deepEqual(one.result.planCheck.triggered, [{ key: 'scroll-audit', pieces: ['a'] }])
  assert.ok(!('scrollAuditRequired' in one.result.judge))
  assert.ok(one.logs.includes('Sign-off for the seat: changelog (a, b); scroll-audit (a)'), one.logs.join('\n'))
  // No trigger met: the triggered check is not listed; the changelog check always is.
  const none = await run({})
  assert.deepEqual(none.result.signOff, [changelog(['a', 'b'])])
  assert.equal(judgeModel(none.calls), 'opus')
  // The plan check and the implementers both count; the judge may add a piece, and what names
  // no trigger or no piece is dropped and logged.
  const judgeAdds = () => ({ ship: true, blockers: [], reworkPieces: [], signOffRequired: [{ key: 'scroll-audit', pieces: ['b', 'zz'] }, { key: 'changelog', pieces: ['a'] }] })
  const added = await run({ planTrig: ['a'], judge: judgeAdds })
  assert.deepEqual(added.result.signOff, [changelog(['a', 'b']), scroll(['a', 'b'])])
  assert.deepEqual(added.result.planCheck.triggered, [{ key: 'scroll-audit', pieces: ['a'] }])
  assert.ok(added.logs.includes('Judge named unknown sign-off triggers or pieces (dropped): scroll-audit: zz, changelog'), added.logs.join('\n'))
  // The judge dies: its stand-in lists every trigger met, and so does signOff.
  const dead = await run({ implTrig: ['b'], dead: /^judge$/ })
  assert.deepEqual(dead.result.judge, { ship: false, blockers: ['judge agent returned nothing'], reworkPieces: [], signOffRequired: [{ key: 'scroll-audit', pieces: ['b'] }] })
  assert.deepEqual(dead.result.signOff, [changelog(['a', 'b']), scroll(['b'])])
  // An untriggered check lists every piece, even one with no implementation result.
  const lost = await run({ implTrig: ['a'], dead: /^impl:b$/ })
  assert.deepEqual(lost.result.signOff, [changelog(['a', 'b']), scroll(['a'])])
  // Implementers that report an unknown trigger have it dropped and logged.
  const odd = await runWorkflow(neutral('gk-build.js'), { plan: PLAN, pieces: structuredClone(pieces), project: goodricke() }, [
    { match: /^plan-check$/, reply: () => ({ ok: true, problems: [], order: [['a', 'b']], triggered: [{ key: 'nope', pieces: ['a'] }] }) },
    { match: /^impl:a$/, reply: () => ({ name: 'a', filesTouched: [], sharedFileLines: [], testResult: 't', notes: '', blockers: [], triggered: ['scroll-audit', 'perf'] }) },
  ])
  assert.deepEqual(odd.result.signOff, [changelog(['a', 'b']), scroll(['a'])])
  assert.ok(odd.logs.includes('Plan check named unknown sign-off triggers or pieces (dropped): nope'), odd.logs.join('\n'))
  assert.ok(odd.logs.includes('Implementers reported sign-off triggers: scroll-audit: a — added'), odd.logs.join('\n'))
  assert.ok(odd.logs.includes('Implementers named unknown sign-off triggers (dropped): perf'), odd.logs.join('\n'))
})

test('sign-off checks, gk-build: shared files, implementer and reviewer texts, triggers and uiJudge from any project', async () => {
  const signOff = [
    { key: 'notice', sharedFile: 'NOTICE', implementer: 'Add each new dependency to NOTICE.', reviewer: 'a new dependency missing from NOTICE.', seat: 'Check NOTICE.' },
    { key: 'docs', sharedFile: '/tmp/repo/docs/CHANGES.md', seat: 'Check docs/CHANGES.md.' },
    { key: 'perf', trigger: 'any change to a hot path', implementer: 'Benchmark the hot paths you touch.', seat: 'Run the benchmarks.' },
    { key: 'visual', trigger: 'any change to a screen', uiJudge: true, seat: 'Look at each screen.' },
  ]
  const pieces = [{ name: 'a', files: ['src/a.ts'], brief: 'Add a.' }, { name: 'b', files: ['src/b.ts'], brief: 'Add b.' }]
  const run = (triggered, project = { root: '/tmp/repo', signOff }) => runWorkflow(neutral('gk-build.js'), { plan: PLAN, pieces: structuredClone(pieces), project }, [
    { match: /^plan-check$/, reply: () => ({ ok: true, problems: [], order: [['a', 'b']], triggered: [] }) },
    {
      match: /^impl:/,
      reply: (prompt, opts) => {
        const name = opts.label.slice('impl:'.length)
        return { name, filesTouched: [`src/${name}.ts`, 'NOTICE', 'docs/CHANGES.md', 'CHANGELOG.md'], sharedFileLines: [{ file: 'NOTICE', line: `${name} 1.0` }], testResult: 't', notes: '', blockers: [], triggered: triggered[name] || [] }
      },
    },
  ])
  const { calls, logs, result } = await run({ a: ['perf'] })
  assertSignOffStages(calls, signOff, (f) => f.replace('/tmp/repo/', ''))
  const impl = calls.find((c) => c.label === 'impl:a').prompt
  assert.ok(impl.includes('You may edit ONLY these files (create them if missing): ["src/a.ts"] — plus your single line in each of NOTICE, docs/CHANGES.md, as below. Other pieces'), impl)
  assert.ok(impl.includes('\nAdd each new dependency to NOTICE.\nBenchmark the hot paths you touch.\nFailures in files'), impl)
  assert.ok(impl.includes('\n- perf: any change to a hot path\n- visual: any change to a screen\nReport anything'), impl)
  const review = calls.find((c) => c.label === 'review:a').prompt
  assert.ok(review.includes('(git diff omits them); confirm the reported NOTICE line is in `git diff -- \'NOTICE\'`; confirm the reported docs/CHANGES.md line is in `git diff -- \'docs/CHANGES.md\'`.\n'), review)
  assert.ok(review.includes('missing/weak tests, a new dependency missing from NOTICE. Only real defects'), review)
  assert.ok(review.includes('"sharedFileLines":[{"file":"NOTICE","line":"a 1.0"}]'), review)
  const judgePrompt = calls.find((c) => c.label === 'judge').prompt
  assert.ok(judgePrompt.includes('or a shared-file line a sign-off check needs is missing or wrong (NOTICE: a new dependency missing from NOTICE; docs/CHANGES.md).'), judgePrompt)
  // Shared files are not out of scope; any other file outside the piece is.
  assert.ok(logs.includes('Out-of-scope edits reported: a: CHANGELOG.md; b: CHANGELOG.md'), logs.join('\n'))
  // perf (no uiJudge) is met: the judge stays on opus; visual (uiJudge) is not met.
  assert.equal(judgeModel(calls), 'opus')
  assert.deepEqual(result.signOff, [
    { key: 'notice', seat: 'Check NOTICE.', pieces: ['a', 'b'] },
    { key: 'docs', seat: 'Check docs/CHANGES.md.', pieces: ['a', 'b'] },
    { key: 'perf', seat: 'Run the benchmarks.', pieces: ['a'] },
  ])
  // A met trigger whose check sets uiJudge routes the judge to fable, under every profile.
  for (const profile of PROFILES) {
    const viaTrigger = await runWorkflow(neutral('gk-build.js'), { plan: PLAN, pieces: structuredClone(pieces), profile, project: { root: '/tmp/repo', signOff } }, [
      { match: /^plan-check$/, reply: () => ({ ok: true, problems: [], order: [['a', 'b']], triggered: [{ key: 'visual', pieces: ['b'] }] }) },
    ])
    assert.equal(judgeModel(viaTrigger.calls), 'fable', profile)
    assert.ok(viaTrigger.logs.includes('Judge on fable (sign-off visual sets uiJudge)'), viaTrigger.logs.join('\n'))
  }
  // A check with no trigger applies to every piece, so its uiJudge always routes to fable.
  const always = await run({}, { root: '/tmp/repo', signOff: [{ key: 'design', uiJudge: true, seat: 'Review the design.' }] })
  assert.equal(judgeModel(always.calls), 'fable')
  assert.deepEqual(always.result.signOff, [{ key: 'design', seat: 'Review the design.', pieces: ['a', 'b'] }])
})

test('dead judge, gk-build: with two triggered checks met, the stand-in lists every one with its pieces, in the order they were met', async () => {
  const signOff = [
    { key: 'perf', trigger: 'any change to a hot path', seat: 'Run the benchmarks.' },
    { key: 'visual', trigger: 'any change to a screen', paths: ['^src/screens/'], uiJudge: true, seat: 'Look at each screen.' },
    { key: 'notice', sharedFile: 'NOTICE', seat: 'Check NOTICE.' },
    { key: 'unmet', trigger: 'any change to the schema', seat: 'Check the schema.' },
  ]
  const pieces = [
    { name: 'a', files: ['src/a.ts'], brief: 'Add a.' },
    { name: 'b', files: ['src/screens/B.tsx'], brief: 'Add B.' },
    { name: 'c', files: ['src/c.ts'], brief: 'Add c.' },
  ]
  // The plan check names c for both; the path rule adds b to visual; implementer a reports both.
  const { calls, logs, result } = await runWorkflow(neutral('gk-build.js'), { plan: PLAN, pieces, project: { root: '/tmp/repo', signOff } }, [
    { match: /^judge$/, reply: () => null },
    { match: /^plan-check$/, reply: () => ({ ok: true, problems: [], order: [['a', 'b', 'c']], triggered: [{ key: 'perf', pieces: ['c'] }, { key: 'visual', pieces: ['c'] }] }) },
    {
      match: /^impl:/,
      reply: (prompt, opts) => {
        const name = opts.label.slice('impl:'.length)
        return { name, filesTouched: [], sharedFileLines: [], testResult: 't', notes: '', blockers: [], triggered: name === 'a' ? ['perf', 'visual'] : [] }
      },
    },
  ])
  const met = [{ key: 'perf', pieces: ['c', 'a'] }, { key: 'visual', pieces: ['c', 'b', 'a'] }]
  assert.equal(judgeModel(calls), 'fable')
  assert.ok(logs.includes('Judge: returned nothing'), logs.join('\n'))
  assert.deepEqual(result.judge, { ship: false, blockers: ['judge agent returned nothing'], reworkPieces: [], signOffRequired: met })
  assert.deepEqual(result.planCheck.triggered, met)
  assert.deepEqual(result.signOff, [
    { key: 'perf', seat: 'Run the benchmarks.', pieces: ['c', 'a'] },
    { key: 'visual', seat: 'Look at each screen.', pieces: ['c', 'b', 'a'] },
    { key: 'notice', seat: 'Check NOTICE.', pieces: ['a', 'b', 'c'] },
  ])
  assert.ok(logs.includes('Sign-off for the seat: perf (c, a); visual (c, b, a); notice (a, b, c)'), logs.join('\n'))
})

test('paths, gk-build: a path under the root, with backslashes or a leading ./ is made relative; one outside the root is refused', async () => {
  // The plan is given under the root too; prompts name it as root + "/" + its relative path.
  const ownedOf = async (root, files) => {
    const pieces = [{ name: 'a', files, brief: 'Add a.' }]
    const rootSlash = root.replace(/\\/g, '/').replace(/\/+$/, '')
    const { calls } = await runWorkflow(neutral('gk-build.js'), { plan: `${rootSlash}/docs/plan.md`, pieces, project: { root } }, buildResponders('generic', { pieces, order: [['a']] }))
    const impl = calls.find((c) => c.label === 'impl:a').prompt
    return [JSON.parse(/\(create them if missing\): (\[[^\]]*\])/.exec(impl)[1]), impl.includes(`of the plan at ${rootSlash}/docs/plan.md —`)]
  }
  assert.deepEqual(await ownedOf('/tmp/repo', ['/tmp/repo/src/a.ts', './src/b.ts', 'src/c.ts']), [['src/a.ts', 'src/b.ts', 'src/c.ts'], true])
  assert.deepEqual(await ownedOf('/tmp/repo/', ['/tmp/repo/src/a.ts']), [['src/a.ts'], true])
  // Windows: the hook passes the root with forward slashes; a caller may write either.
  assert.deepEqual(await ownedOf('C:/r', ['C:\\r\\src\\a.ts', 'src\\b.ts', '.\\src\\c.ts']), [['src/a.ts', 'src/b.ts', 'src/c.ts'], true])
  assert.deepEqual(await ownedOf('C:\\r', ['C:/r/src/a.ts']), [['src/a.ts'], true])
  // Outside the root (a sibling whose name starts with the root's included), absolute, or traversing.
  for (const [root, file] of [['/tmp/repo', '/tmp/repository/a.ts'], ['C:/r', 'C:\\rr\\a.ts'], ['/tmp/repo', 'D:/a.ts'], ['/tmp/repo', 'src/../../a.ts']]) {
    await assert.rejects(
      runWorkflow(neutral('gk-build.js'), { plan: 'docs/plan.md', pieces: [{ name: 'a', files: [file], brief: 'b' }], project: { root } }, []),
      (e) => e.message === `gk-build: file path ${JSON.stringify(file)} must be inside ${root} with no traversal`,
      `${root} ${file}`,
    )
  }
})

test('test files, gk-build: a tests value that is a file path is owned by the piece in any language; a test command is not', async () => {
  const ownedIn = (prompt) => JSON.parse(/\(create them if missing\): (\[[^\]]*\])/.exec(prompt)[1])
  // [tests value, the file the piece then owns besides src/x.ext, or null]
  const values = [
    ['tests/test_a.py', 'tests/test_a.py'],
    ['pkg/foo/foo_test.go', 'pkg/foo/foo_test.go'],
    ['spec/c_spec.rb', 'spec/c_spec.rb'],
    ['test_b.py', 'test_b.py'],
    ['/tmp/repo/tests/test_d.py', 'tests/test_d.py'],
    ['tests\\test_e.py', 'tests/test_e.py'],
    ['pytest -q', null],
    ['go test ./...', null],
    ['npm test', null],
    ['pytest', null],
    ['tests/', null],
    ['./...', null],
  ]
  const pieces = values.map(([tests], i) => ({ name: `p${i}`, files: [`src/x${i}.py`], tests, brief: 'Add it.' }))
  const { calls } = await runWorkflow(neutral('gk-build.js'), { plan: PLAN, pieces, project: { root: '/tmp/repo' } }, buildResponders('generic', { pieces, order: [pieces.map((p) => p.name)] }))
  values.forEach(([tests, file], i) => {
    const want = [`src/x${i}.py`, ...(file ? [file] : [])]
    assert.deepEqual(ownedIn(calls.find((c) => c.label === `impl:p${i}`).prompt), want, tests)
    // The reviewer's diff covers the owned test file too.
    assert.ok(calls.find((c) => c.label === `review:p${i}`).prompt.includes(`\`git diff -- ${want.map((f) => `'${f}'`).join(' ')}\``), tests)
  })
  // Owned, so two pieces in one wave cannot share it, and one outside the repository is refused.
  const twice = [{ name: 'a', files: ['src/a.py'], tests: 'tests/test_ab.py', brief: 'Add a.' }, { name: 'b', files: ['src/b.py'], tests: 'tests/test_ab.py', brief: 'Add b.' }]
  await assert.rejects(
    runWorkflow(neutral('gk-build.js'), { plan: PLAN, pieces: twice, project: { root: '/tmp/repo' } }, buildResponders('generic', { pieces: twice, order: [['a', 'b']] })),
    /gk-build: pieces a and b share tests\/test_ab\.py but were placed in the same wave/,
  )
  await assert.rejects(
    runWorkflow(neutral('gk-build.js'), { plan: PLAN, pieces: [{ name: 'a', files: ['src/a.py'], tests: '/tmp/other/test_a.py', brief: 'Add a.' }], project: { root: '/tmp/repo' } }, []),
    (e) => e.message === 'gk-build: file path "/tmp/other/test_a.py" must be inside /tmp/repo with no traversal',
  )
  // Goodricke's JS/TS names and test commands are owned, or not, as the golden had them.
  const js = [
    ['backend/src/a.test.ts', 'backend/src/a.test.ts'],
    ['frontend/src/B.spec.tsx', 'frontend/src/B.spec.tsx'],
    ['c.test.mjs', 'c.test.mjs'],
    ['npx vitest run backend/src/a.test.ts', null],
    ['npm run test', null],
  ]
  const jsPieces = js.map(([tests], i) => ({ name: `j${i}`, files: [`backend/src/j${i}.ts`], tests, brief: 'Add it.' }))
  const jsOpts = { pieces: jsPieces, order: [jsPieces.map((p) => p.name)] }
  const old = await runWorkflow(golden('gk-build.js'), { plan: PLAN, pieces: structuredClone(jsPieces) }, buildResponders('golden', jsOpts))
  const now = await runWorkflow(neutral('gk-build.js'), { plan: PLAN, pieces: structuredClone(jsPieces), project: goodricke() }, buildResponders('neutral', jsOpts))
  js.forEach(([tests, file], i) => {
    const want = [`backend/src/j${i}.ts`, ...(file ? [file] : [])]
    assert.deepEqual(ownedIn(old.calls.find((c) => c.label === `impl:j${i}`).prompt), want, `golden ${tests}`)
    assert.deepEqual(ownedIn(now.calls.find((c) => c.label === `impl:j${i}`).prompt), want, tests)
  })
})

test('check, gk-build: a check that ran and failed is a failure whatever its reply says about skipping', async () => {
  // Skipped is the workflow's own decision (no command), never a field of the agent's reply.
  const pieces = PY_PIECES
  const responders = [{ match: /^check$/, reply: () => ({ passed: false, skipped: true, output: 'boom' }) }, ...buildResponders('generic', { pieces, order: [['a', 'b']] })]
  const { calls, logs } = await runWorkflow(neutral('gk-build.js'), { plan: PLAN, pieces: structuredClone(pieces), check: 'python -m pytest -q', project: { root: '/tmp/repo' } }, responders)
  assert.ok(calls.some((c) => c.label === 'check'), 'the check ran')
  const judge = calls.find((c) => c.label === 'judge').prompt
  assert.ok(judge.includes('"passed":false'), judge)
  assert.ok(!judge.includes('was skipped'), judge)
  assert.ok(logs.some((l) => l.includes('Check FAILED')), logs.join('\n'))
})

test('project malformed throws, gk-build: before any agent runs, naming the field', async () => {
  // gk-build's own checks: a sharedFile is a path inside the repository and a package a folder
  // inside it; sign-off keys are unique.
  const BUILD_MALFORMED = [
    [{ packages: ['../x'] }, 'packages[0]'],
    [{ packages: ['/srv/app'] }, 'packages[0]'],
    [{ root: '/tmp/repo', packages: ['web', '/tmp/repository/web'] }, 'packages[1]'],
    [{ packages: ['web', '.'] }, 'packages[1]'],
    [{ packages: ['./'] }, 'packages[0]'],
    [{ packages: ['it\'s'] }, 'packages[0]'],
    [{ signOff: [{ key: 'k', seat: 's', sharedFile: '../CHANGELOG.md' }] }, 'signOff[0].sharedFile'],
    [{ signOff: [{ key: 'k', seat: 's', sharedFile: '/etc/motd' }] }, 'signOff[0].sharedFile'],
    [{ root: '/tmp/repo', signOff: [{ key: 'k', seat: 's', sharedFile: '/tmp/repository/NOTICE' }] }, 'signOff[0].sharedFile'],
    [{ signOff: [{ key: 'k', seat: 's', sharedFile: 'C:/NOTICE' }] }, 'signOff[0].sharedFile'],
    [{ signOff: [{ key: 'k', seat: 's', sharedFile: 'it\'s.md' }] }, 'signOff[0].sharedFile'],
    [{ signOff: [{ key: 'k', seat: 's' }, { key: ' k ', seat: 't' }] }, 'signOff[1].key'],
  ]
  for (const [project, field] of [...PROJECT_MALFORMED, ...BUILD_MALFORMED]) await assertMalformed('gk-build.js', buildArgs(), project, field)
  // Each script's own checks bind only that script: gk-build ignores how finders replace one
  // another, and gk-review ignores sign-off paths and repeated keys.
  for (const [project] of REVIEW_MALFORMED) await runWorkflow(neutral('gk-build.js'), { ...buildArgs(), project }, buildResponders('neutral'))
  for (const [project] of BUILD_MALFORMED) await runWorkflow(neutral('gk-review.js'), { target: 'main..HEAD', project }, reviewResponders())
  // The fixture, a root-only project, {} and a sharedFile given under the root are well formed.
  for (const project of [goodricke(), { root: '/tmp/repo' }, {}, { root: '/tmp/repo/', signOff: [{ key: 'k', seat: 's', sharedFile: '/tmp/repo/NOTICE' }] }]) {
    await runWorkflow(neutral('gk-build.js'), { ...buildArgs(), project }, buildResponders('neutral'))
  }
})

// ---------------- both scripts: the shared gk-project block ----------------

// A workflow script's meta.phases titles and its PHASES list, read from the source text.
function scriptPhases(file) {
  const src = readFileSync(neutral(file), 'utf8')
  const metaPhases = src.slice(src.indexOf('  phases: ['), src.indexOf('\n  ],\n}'))
  const titles = [...metaPhases.matchAll(/\{ title: '([^']+)'/g)].map((m) => m[1])
  const phases = [.../^const PHASES = \[(.*)\]$/m.exec(src)[1].matchAll(/'([^']+)'/g)].map((m) => m[1])
  return { titles, phases }
}
// The marked block with its two per-script lines (WF and PHASES) taken out.
function projectBlock(file) {
  const src = readFileSync(neutral(file), 'utf8')
  const begin = src.indexOf('// ================= gk-project block: BEGIN')
  const end = src.indexOf('// ================= gk-project block: END')
  assert.ok(begin >= 0 && end > begin, `${file}: the marked block`)
  return src.slice(begin, end).split('\n').filter((l) => !/^const (WF|PHASES) = /.test(l)).join('\n')
}

test('gk-project block, every script: one block, verbatim, whose PHASES are the script\'s meta.phases titles', () => {
  const phases = {
    'gk-review.js': ['Scope', 'Find', 'Verify', 'Judge'],
    'gk-build.js': ['Plan check', 'Implement', 'Check', 'Review', 'Judge'],
    'gk-design.js': ['Propose', 'Judge', 'Sign-off'],
    'gk-research.js': ['Sweep', 'Merge', 'Deep-read', 'Critic'],
    'gk-diagnose.js': ['Hypothesise', 'Test', 'Adjudicate'],
  }
  for (const [file, titles] of Object.entries(phases)) {
    assert.equal(projectBlock(file), projectBlock('gk-build.js'), `${file}: the block is gk-build's, verbatim`)
    assert.deepEqual(scriptPhases(file), { titles, phases: titles }, file)
    assert.ok(readFileSync(neutral(file), 'utf8').includes(`\nconst WF = '${file.replace(/\.js$/, '')}'\n`), `${file}: WF is its own name`)
  }
})

const nameWarnings = (logs) => logs.filter((l) => / names unknown (workflow|stage) /.test(l))

test('guard names, both scripts: an unknown workflow, or this workflow with a phase it lacks, logs one warning and the guard is kept', async () => {
  const guards = [
    { text: 'Misspelt workflow.', workflows: ['gk-reveiw', 'gk-review', 'gk-build'] },
    { text: 'Review stage typo.', workflows: ['gk-review'], stages: ['judge', 'Judge'] },
    { text: 'Build stage typo.', workflows: ['gk-build'], stages: ['Reveiw', 'Review'] },
    { text: 'Another workflow\'s phase.', workflows: ['gk-design'], stages: ['Proposals'] },
    { text: 'A stage of no workflow named.', stages: ['Nowhere'] },
    'Everywhere.',
  ]
  const review = await runWorkflow(neutral('gk-review.js'), { target: 'main..HEAD', project: { root: '/tmp/repo', guards } }, reviewResponders())
  assert.deepEqual(nameWarnings(review.logs), ["Project: guard 0 names unknown workflow 'gk-reveiw'", "Project: guard 1 names unknown stage 'judge'"])
  const build = await runWorkflow(neutral('gk-build.js'), { ...buildArgs(), project: { root: '/tmp/repo', guards } }, buildResponders('neutral'))
  assert.deepEqual(nameWarnings(build.logs), ["Project: guard 0 names unknown workflow 'gk-reveiw'", "Project: guard 2 names unknown stage 'Reveiw'"])
  // Kept as written: each still reaches the stages its known names match.
  const rulesOf = (calls, label) => calls.find((c) => c.label === label).prompt.split('Project rules:\n')[1].split('\n').map((l) => l.slice(2))
  assert.deepEqual(rulesOf(review.calls, 'judge'), ['Misspelt workflow.', 'Review stage typo.', 'Everywhere.'])
  assert.deepEqual(rulesOf(review.calls, 'scope'), ['Misspelt workflow.', 'Everywhere.'])
  assert.deepEqual(rulesOf(build.calls, 'review:a'), ['Misspelt workflow.', 'Build stage typo.', 'Everywhere.'])
  assert.deepEqual(rulesOf(build.calls, 'judge'), ['Misspelt workflow.', 'Everywhere.'])
  // Each line names one name; the warnings follow the Project line.
  assert.ok(build.logs.indexOf("Project: guard 0 names unknown workflow 'gk-reveiw'") > build.logs.findIndex((l) => l.startsWith('Project: (unnamed)')))
  // The Goodricke fixture names only real workflows and phases.
  const g1 = await runWorkflow(neutral('gk-review.js'), { target: 'develop..HEAD', project: goodricke() }, reviewResponders())
  const g2 = await runWorkflow(neutral('gk-build.js'), { ...buildArgs(), project: goodricke() }, buildResponders('neutral'))
  assert.deepEqual([...nameWarnings(g1.logs), ...nameWarnings(g2.logs)], [])
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
  assert.deepEqual(files, ['gk-build.js', 'gk-design.js', 'gk-diagnose.js', 'gk-research.js', 'gk-review.js', 'gk-selftest.js'])
  for (const f of files) {
    const code = codeOnly(readFileSync(join(workflowsDir, f), 'utf8'))
    for (const [name, re] of FORBIDDEN) assert.doesNotMatch(code, re, `${f} names ${name}`)
  }
})
