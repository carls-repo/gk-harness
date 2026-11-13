// gk-design, gk-research and gk-diagnose against their golden Goodricke copies: the same
// routing under every profile and flag, Goodricke's sentences carried by the project file's
// guards, a generic run with no trace of Goodricke, and gk-diagnose's scratchpad taken from
// args (the gk-harness hook passes the session's) or, without one, made with mktemp.
//
// Small helpers are duplicated from review-build.test.mjs: importing a test file would run its
// tests again in this process.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { runWorkflow, minimalFromSchema } from './harness.mjs'

const here = dirname(fileURLToPath(import.meta.url))
const golden = (f) => join(here, 'golden', f)
const neutral = (f) => join(here, '..', '..', 'workflows', f)
const fixture = JSON.parse(readFileSync(join(here, 'fixtures', 'goodricke.gk-project.json'), 'utf8'))
const GOODRICKE_ROOT = '/home/carls/apps/mike-dev'
const goodricke = () => ({ ...structuredClone(fixture), root: GOODRICKE_ROOT })

const PROFILES = ['fable', 'opus', 'sonnet']
const route = (calls) => calls.map((c) => [c.label, c.phase, c.model, c.effort])
const ws = (s) => String(s).replace(/\s+/g, ' ').trim()
// The log, less the lines about the project (the golden has none).
const runLog = (logs) => logs.filter((l) => !l.startsWith('Project:'))

// Sentences of the golden prompts that name Goodricke or its rules (the plan's marker list, as
// in review-build.test.mjs). A sentence ends at . ! or ? before whitespace, but not in "e.g."
// or "i.e.".
const MARK = /mike-dev|supabase|prod|:8080|CHANGELOG|scratch scripts|Goodricke|Principal|canAct|ethical wall|page root|de-id|checkout/i
const sentences = (prompt) => prompt.split(/\n+|(?<=[.!?])(?<!\b(?:e\.g|i\.e)\.)\s+/).map(ws).filter(Boolean)

// The guard lines that reach one stage (ruling R49), and the block that closes its brief.
const reach = (guards, workflow, stage) => guards
  .filter((g) => typeof g === 'string' || ((!g.workflows || g.workflows.includes(workflow)) && (!g.stages || g.stages.includes(stage))))
  .map((g) => (typeof g === 'string' ? g : g.text))
const rulesBlock = (lines) => (lines.length ? `\nProject rules:\n${lines.map((t) => `- ${t}`).join('\n')}` : '')
const guardText = (g) => (typeof g === 'string' ? g : g.text)
const scopedTo = (wf) => fixture.guards.filter((g) => typeof g !== 'string' && g.workflows && g.workflows.includes(wf))

// Words that name Goodricke, its repo, its stack, its modules or its house rules (as in
// review-build.test.mjs, except that CHANGELOG, Goodricke's sign-off file, is matched in capitals
// only: a changelog in general is any repository's, and gk-research's generic by-history angle
// reads one).
const GOODRICKE_WORDS = /Goodricke|mike-dev|supabase|:8080|\bdevelop\b|\/home\/carls|docreview|ethical ?walls?|\bde-?id|mcpAuth|engagement|canAct|\bPrincipal\b|PostgREST|scratch scripts|dev only/i
// ... and the ones these three goldens add: the users, the design system, the page-root rule,
// the stack's services, the API-spend rule, the golden's scratchpad and its package folders.
const MORE_WORDS = /lawyers|CorpOS|liquid.glass|backdrop-filter|globals\.css|design-system\.md|h-full|overflow-hidden|page.root|npm run dev|systemd|sidecar|paid LLM|API spend|\/tmp\/claude|claude-1000|backend\/|frontend\/|\bprod\b/i
const CHANGELOG_FILE = /CHANGELOG/

const goldenIs = (e, s) => (typeof e.golden === 'string' ? e.golden === s : e.golden.test(s))
const replacementsFor = (e, s) => (typeof e.replacedBy === 'function' ? e.replacedBy(s) : e.replacedBy)

// ---------------- responders ----------------

// gk-design: every proposer answers with its own title; each judge scores every proposal it is
// shown (P2 highest) and names P2 the winner, grafting one idea from P1; the sign-off approves.
// `dead` labels reply null.
function designResponders({ dead = null } = {}) {
  return [
    ...(dead ? [{ match: dead, reply: () => null }] : []),
    { match: /^propose:/, reply: (prompt, opts) => ({ ...minimalFromSchema(opts.schema), title: `Plan ${opts.label.slice('propose:'.length)}` }) },
    {
      match: /^judge:/,
      reply: (prompt) => {
        const keys = [...new Set([...prompt.matchAll(/^=== (P\d) /gm)].map((m) => m[1]))].sort()
        return {
          scores: keys.map((k) => ({ title: k, fit: k === 'P2' ? 9 : 6, simplicity: 7, risk: 7, design: 0, reason: 'fit: f; simplicity: s; risk: r' })),
          winner: 'P2',
          grafts: [{ from: 'P1', idea: 'keep the cache' }],
        }
      },
    },
    { match: /^sign-off$/, reply: () => ({ approved: true, concerns: [], mustChange: [] }) },
  ]
}
const DESIGN_BRIEF = 'Add a dashboard of open items to the home page.'
const designArgs = (extra = {}) => ({ brief: DESIGN_BRIEF, ...structuredClone(extra) })

// gk-research: each reader returns one cited and one uncited fact; the first merge finds one
// contradiction and one gap, the second one gap; deep-read settles the contradiction only; the
// critic names one missing item.
function researchResponders({ dead = null } = {}) {
  return [
    ...(dead ? [{ match: dead, reply: () => null }] : []),
    {
      match: /^sweep r/,
      reply: (prompt, opts) => ({
        facts: [
          { claim: `A fact from ${opts.label}`, citation: 'src/a.ts:12', confidence: 'high' },
          { claim: 'An uncited fact', citation: 'somewhere', confidence: 'low' },
        ],
        omitted: 2,
      }),
    },
    { match: /^merge r1$/, reply: () => ({ facts: [{ claim: 'Merged', citation: 'src/a.ts:12', confidence: 'high' }], contradictions: [{ a: 'x [src/a.ts:1]', b: 'y [src/b.ts:2]', why: 'w' }], gaps: ['Who calls a?'] }) },
    { match: /^merge r2$/, reply: () => ({ facts: [{ claim: 'Merged again', citation: 'src/a.ts:12', confidence: 'high' }], contradictions: [], gaps: ['Still open'] }) },
    { match: /^deep-read /, reply: (prompt, opts) => ({ item: 'i', resolved: opts.label.endsWith('contradiction'), answer: 'a', citations: ['src/a.ts:3'] }) },
    { match: /^critic$/, reply: () => ({ missing: ['Look for the callers of a in src/.'], unverifiedClaims: [] }) },
  ]
}
const QUESTION = 'Where is the retry limit set?'
const SOURCES = { paths: ['src/a.ts'], docs: ['docs/retry.md'], urls: ['https://example.com/retry'] }
const researchArgs = (extra = {}) => ({ question: QUESTION, ...structuredClone(extra) })

// gk-diagnose: every lens proposes the same cache mechanism and one of its own, so the merge
// has work to do; H1 is confirmed and the rest refuted; the adjudicator names H1. `none`
// returns no hypotheses.
function diagnoseResponders({ dead = null, none = false } = {}) {
  return [
    ...(dead ? [{ match: dead, reply: () => null }] : []),
    {
      match: /^hypothesise:/,
      reply: (prompt, opts) => (none ? { hypotheses: [] } : {
        hypotheses: [
          { id: 'a-1', mechanism: 'A stale cache entry survives the reload', whereToLook: ['src/cache.ts'], howToTest: 'Read cache.ts', confidence: 0.6 },
          { id: 'b-1', mechanism: `A race seen through ${opts.label.slice('hypothesise:'.length)}`, whereToLook: ['src/race.ts'], howToTest: 'Trace it', confidence: 0.4 },
        ],
      }),
    },
    { match: /^test:/, reply: (prompt, opts) => ({ id: 'x', verdict: opts.label === 'test:H1' ? 'confirmed' : 'refuted', evidence: ['src/cache.ts:4'], contradictsPriorEvidence: false }) },
    { match: /^adjudicate$/, reply: () => ({ mechanism: 'A stale cache entry survives the reload', confirmed: ['H1'], refuted: ['H2'], undetermined: [], contradictions: [], suggestedFixScope: ['src/cache.ts'] }) },
  ]
}
const SYMPTOM = 'The list shows a deleted item until the second reload.'
const diagnoseArgs = (extra = {}) => ({ symptom: SYMPTOM, ...structuredClone(extra) })

// Per workflow: its file, args builder, responders, and the run that reaches every stage and
// every prompt variant (ui under a non-fable profile reaches gk-design's sign-off; thorough
// with sources reaches gk-research's second round; a reproduce command and a scratchpad fill
// gk-diagnose's test method).
const WORKFLOWS = {
  'gk-design': { args: designArgs, responders: designResponders, full: { ui: true, profile: 'opus', context: ['docs/home.md'], constraints: ['No new dependencies.'] }, phases: ['Propose', 'Judge', 'Sign-off'] },
  'gk-research': { args: researchArgs, responders: researchResponders, full: { depth: 'thorough', sources: SOURCES }, phases: ['Sweep', 'Merge', 'Deep-read', 'Critic'] },
  'gk-diagnose': { args: diagnoseArgs, responders: diagnoseResponders, full: { reproduce: 'npm test -- list', evidence: ['GET /items returns the item'], files: ['src/list.ts'], scratchpad: '/tmp/s' }, phases: ['Hypothesise', 'Test', 'Adjudicate'] },
}
const fullRun = (wf, project) => {
  const w = WORKFLOWS[wf]
  const args = w.args(w.full)
  if (project !== undefined) args.project = project
  return runWorkflow(project === undefined ? golden(`${wf}.js`) : neutral(`${wf}.js`), args, w.responders())
}

// ---------------- routing equivalence ----------------

// Runs the golden and the neutral script (with the Goodricke project, and with a generic one) on
// the same args and replies, and checks they make the same calls on the same (model, effort),
// log the same lines and return the same result.
async function assertSameRouting(wf, name, args, responders) {
  const old = await runWorkflow(golden(`${wf}.js`), structuredClone(args), responders())
  const now = await runWorkflow(neutral(`${wf}.js`), { ...structuredClone(args), project: goodricke() }, responders())
  assert.deepEqual(route(now.calls), route(old.calls), `${wf} ${name}`)
  assert.deepEqual(runLog(now.logs), old.logs, `${wf} ${name}: the log`)
  assert.deepEqual(now.result, old.result, `${wf} ${name}: the result`)
  // A project changes words, never routing.
  const generic = await runWorkflow(neutral(`${wf}.js`), { ...structuredClone(args), project: { root: '/tmp/repo' } }, responders())
  assert.deepEqual(route(generic.calls), route(old.calls), `${wf} ${name} (generic)`)
  assert.deepEqual(runLog(generic.logs), old.logs, `${wf} ${name} (generic): the log`)
  assert.deepEqual(generic.result, old.result, `${wf} ${name} (generic): the result`)
  return old
}

// profiles x {ui: true|false} x {escalate: true|false}
const GRID = PROFILES.flatMap((profile) => [true, false].flatMap((ui) => [true, false].map((escalate) => ({ profile, ui, escalate }))))

test('routing equivalence, gk-design: golden and neutral+Goodricke make the same calls on the same (model, effort)', async () => {
  const cases = GRID.map((flags) => [JSON.stringify(flags), flags, {}])
  for (const profile of PROFILES) {
    cases.push(
      [`${profile} four approaches, three judges`, { profile, approaches: 4, judges: 3 }, {}],
      [`${profile} ui, two approaches, one judge`, { profile, ui: true, approaches: 2, judges: 1 }, {}],
      [`${profile} ui, context and constraints`, { profile, ui: true, context: ['docs/home.md'], constraints: ['No new dependencies.'] }, {}],
      [`${profile} ui, a proposer dies`, { profile, ui: true }, { dead: /^propose:user-first$/ }],
      [`${profile} ui, one proposal survives`, { profile, ui: true }, { dead: /^propose:(risk|user)-first$/ }],
      [`${profile} ui, every judge dies`, { profile, ui: true }, { dead: /^judge:/ }],
      [`${profile} ui, the sign-off dies`, { profile, ui: true }, { dead: /^sign-off$/ }],
    )
  }
  const seen = new Set()
  for (const [name, flags, opts] of cases) {
    const old = await assertSameRouting('gk-design', name, designArgs(flags), () => designResponders(opts))
    for (const c of old.calls) seen.add(`${c.phase} ${c.model} ${c.effort}`)
  }
  // The grid reaches every route the golden has.
  assert.deepEqual([...seen].sort(), ['Judge fable xhigh', 'Judge opus xhigh', 'Propose fable xhigh', 'Propose opus xhigh', 'Sign-off fable xhigh'])
})

// ---------------- behaviour parity (gk-research, gk-diagnose) ----------------

// Their routing comes from routes.json (routes.test.mjs checks it); everything else is the
// golden's. Runs the golden and the neutral script (with the Goodricke project, and with a
// generic one) on the same args and replies, and checks they make the same calls by
// [label, phase], log the same lines and return the same result. The neutral script's only
// additions are left out of the comparison: result.routing, and the log lines about the
// project, the profile (the golden's "Profile:") and the retired profile.
// `expect(oldCalls)` gives the neutral calls a case expects when they legitimately differ.
const step = (calls) => calls.map((c) => [c.label, c.phase])
const RETIRED = /^profile is retired; routes come from routes\.json v\d+$/
const behaviourLog = (logs) => runLog(logs).filter((l) => !l.startsWith('Profile:') && !RETIRED.test(l))
const withoutRouting = (r) => {
  if (!r || typeof r !== 'object') return r
  const { routing, ...rest } = r
  return rest
}
async function assertSameBehaviour(wf, name, args, responders, expect = (calls) => step(calls)) {
  const old = await runWorkflow(golden(`${wf}.js`), structuredClone(args), responders())
  for (const [kind, project] of [['goodricke', goodricke()], ['generic', { root: '/tmp/repo' }]]) {
    const now = await runWorkflow(neutral(`${wf}.js`), { ...structuredClone(args), project }, responders())
    assert.deepEqual(step(now.calls), expect(old.calls), `${wf} ${name} (${kind}): the calls`)
    assert.deepEqual(behaviourLog(now.logs), behaviourLog(old.logs), `${wf} ${name} (${kind}): the log`)
    assert.ok(now.result && now.result.routing, `${wf} ${name} (${kind}): the result carries routing`)
    assert.deepEqual(withoutRouting(now.result), old.result, `${wf} ${name} (${kind}): the result`)
  }
  return old
}

const PARITY_GRID = PROFILES.flatMap((profile) => [true, false].map((escalate) => ({ profile, escalate })))

test('behaviour parity, gk-research: golden and neutral make the same calls, log the same lines and return the same result on every path', async () => {
  const cases = []
  for (const { profile, escalate } of PARITY_GRID) {
    for (const depth of ['quick', 'thorough']) {
      for (const sources of [undefined, SOURCES, { paths: ['src/a.ts'] }]) {
        const flags = { profile, escalate, depth, ...(sources ? { sources } : {}) }
        cases.push([JSON.stringify(flags), flags, {}])
      }
    }
    cases.push(
      [`${profile} custom angles`, { profile, escalate, angles: ['by-test (what the tests pin)'] }, {}],
      [`${profile} thorough, the first merger dies`, { profile, depth: 'thorough' }, { dead: /^merge r1$/ }],
      [`${profile} thorough, a deep-read dies`, { profile, depth: 'thorough' }, { dead: /^deep-read 1:/ }],
      [`${profile} thorough, the critic dies`, { profile, depth: 'thorough' }, { dead: /^critic$/ }],
      [`${profile} a reader dies`, { profile }, { dead: /^sweep r1: 2 / }],
    )
  }
  // No profile or escalate at all: the neutral script logs no retired line.
  cases.push(['no profile', {}, {}], ['no profile, thorough with sources', { depth: 'thorough', sources: SOURCES }, {}])
  const seen = new Set()
  for (const [name, flags, opts] of cases) {
    const old = await assertSameBehaviour('gk-research', name, researchArgs(flags), () => researchResponders(opts))
    for (const c of old.calls) seen.add(c.phase)
  }
  assert.deepEqual([...seen].sort(), ['Critic', 'Deep-read', 'Merge', 'Sweep'])
})

test('behaviour parity, gk-diagnose: golden and neutral make the same calls, log the same lines and return the same result on every path', async () => {
  const cases = PARITY_GRID.map((flags) => [JSON.stringify(flags), flags, {}])
  for (const profile of PROFILES) {
    cases.push(
      [`${profile} reproduce, evidence, files and a scratchpad`, { profile, ...WORKFLOWS['gk-diagnose'].full }, {}],
      [`${profile} custom lenses`, { profile, lenses: ['state', ' timing ', 'state'] }, {}],
      [`${profile} no hypotheses`, { profile }, { none: true }],
      [`${profile} a lens dies`, { profile }, { dead: /^hypothesise:environment-and-config$/ }],
      [`${profile} a test dies`, { profile }, { dead: /^test:H2$/ }],
    )
  }
  cases.push(['no profile', {}, {}], ['no profile, no hypotheses', {}, { none: true }])
  const seen = new Set()
  for (const [name, flags, opts] of cases) {
    const old = await assertSameBehaviour('gk-diagnose', name, diagnoseArgs(flags), () => diagnoseResponders(opts))
    for (const c of old.calls) seen.add(c.phase)
  }
  assert.deepEqual([...seen].sort(), ['Adjudicate', 'Hypothesise', 'Test'])
  // The early "no hypotheses" return carries routing too, and runs no Test or Adjudicate.
  const none = await runWorkflow(neutral('gk-diagnose.js'), diagnoseArgs({ project: { root: '/tmp/repo' } }), diagnoseResponders({ none: true }))
  assert.deepEqual(none.result.verdicts, [])
  assert.equal(none.result.adjudication, null)
  assert.deepEqual(none.result.routing.offTier, [])
  assert.ok(none.calls.every((c) => c.phase === 'Hypothesise'))
  // The adjudicator dies: the one legitimate difference in the calls. The golden ran it once on
  // opus xhigh; the neutral script runs it on fable medium and, when that dies, once more on
  // its noFable tier (onFail rerun), so the neutral calls have one extra adjudicate call. Here
  // both die, so the results (less routing) and the logs still match.
  for (const profile of [...PROFILES, undefined]) {
    const flags = profile ? { profile } : {}
    await assertSameBehaviour('gk-diagnose', `${profile || 'no profile'} the adjudicator dies`, diagnoseArgs(flags),
      () => diagnoseResponders({ dead: /^adjudicate$/ }),
      (calls) => step(calls).flatMap((c) => (c[0] === 'adjudicate' ? [c, c] : [c])))
  }
})

// gk-research takes no ui argument: both scripts reject it, true or false, before any agent.
// (Routing for gk-research and gk-diagnose is checked against routes.json in routes.test.mjs.)
test('gk-research rejects ui: golden and neutral stop on args.ui before any agent', async () => {
  for (const ui of [true, false]) {
    for (const file of [golden('gk-research.js'), neutral('gk-research.js')]) {
      const seenLabels = []
      const spy = [{ match: /.*/, reply: (prompt, opts) => { seenLabels.push(opts.label); return null } }]
      await assert.rejects(runWorkflow(file, researchArgs({ ui }), spy), /^Error: gk-research: args\.ui is not a recognised field/, file)
      assert.deepEqual(seenLabels, [])
    }
  }
})

// ---------------- Goodricke sentences ----------------

// Golden sentences the marker misses, checked verbatim like the marked ones. Each is a
// Goodricke rule that reaches its stages as a guard, unless the reason says the core keeps it.
const UNMARKED = {
  'gk-design': [
    {
      sentence: 'The project design language lives in docs/design-system.md and frontend/src/app/globals.css (CorpOS tokens with the liquid-glass look; nested backdrop-filter does not render).',
      reason: 'Goodricke\'s design system; a gk-design guard (the core names no design files)',
    },
  ],
  'gk-research': [
    {
      sentence: '- You should not need a database.',
      reason: 'the first half of Goodricke\'s database rule; the guard keeps both sentences together',
    },
  ],
  'gk-diagnose': [
    {
      sentence: 'To read another revision use `git show <rev>:<path>` or `git grep <pattern> <rev>`; to trial a merge use `git merge-tree --write-tree <a> <b>`; if you truly need a tree on disk, `git worktree add --detach <a fresh dir under your scratchpad> <rev>` and remove it when done.',
      reason: 'the how-to of Carl\'s never-change-the-checkout rule; generic, so the core keeps it (the core says what "your scratchpad" is)',
    },
    {
      sentence: '- Never run `npm run dev` or restart services; systemd owns the stacks.',
      reason: 'Goodricke\'s stack; a gk-diagnose guard',
    },
    {
      sentence: '- No paid LLM API calls (Anthropic/OpenAI/Gemini) from anything you write or run, other than the supplied reproduce command; standing rule: API spend needs Carl\'s prior approval.',
      reason: 'the API-spend rule; a gk-diagnose guard (the brief moves it to guards)',
    },
  ],
}

// The generic design-language text: the core of a UI brief names no files.
const DESIGN_LANGUAGE_CORE = 'The project design language lives in its own files in the repository (design-system docs, theme tokens, global styles, shared components); find them.'

// Golden text that does not survive verbatim, one reason each, and the text that replaces it
// (each item must be in the matching neutral prompt). `golden` is the sentence or a pattern.
const EXCEPTIONS = {
  'gk-design': [
    {
      golden: '- Repo: /home/carls/apps/mike-dev (Goodricke).',
      reason: 'the repository now comes from args.project.root, named as "the git repository at <root>" as in gk-review and gk-build; the core does not name the project',
      replacedBy: ['- Repo: the git repository at /home/carls/apps/mike-dev.'],
    },
    {
      golden: /^You are proposal author \d of \d on a Goodricke design question\.$/,
      reason: 'the core does not name the project',
      replacedBy: (s) => [s.replace('on a Goodricke design question.', 'on a design question.')],
    },
    {
      golden: /^You are judge \d of \d on a panel deciding a Goodricke design question\.$/,
      reason: 'the core does not name the project',
      replacedBy: (s) => [s.replace('deciding a Goodricke design question.', 'deciding a design question.')],
    },
    {
      golden: 'You are the UI sign-off reviewer for a Goodricke design decision.',
      reason: 'the core does not name the project',
      replacedBy: ['You are the UI sign-off reviewer for a design decision.'],
    },
    {
      golden: 'Other authors take other angles; yours is USER-FIRST: Optimise for the experience of the lawyers and staff who will use it: flows, clarity, fewest steps, sensible defaults.',
      reason: 'Goodricke\'s users are a firm\'s lawyers and staff; the user-first angle reaches one proposer only, which no guard can target, so the core names the people who will use it',
      replacedBy: ['Other authors take other angles; yours is USER-FIRST: Optimise for the experience of the people who will use it: flows, clarity, fewest steps, sensible defaults.'],
    },
    {
      golden: 'METHOD: read the context files and the code the winner touches; check the flows, clarity, defaults, accessibility and fit with the design language, including the page-root scroll rule.',
      reason: 'the brief: the UI sign-off method drops "including the page-root scroll rule"; the rule itself is a gk-design guard',
      replacedBy: [
        'METHOD: read the context files and the code the winner touches; check the flows, clarity, defaults, accessibility and fit with the design language.',
        '- New page roots must use h-full + overflow-y-auto because the app shell <main> is md:overflow-hidden.',
      ],
    },
    {
      golden: 'Design quality is judged against the project\'s existing design language: The project design language lives in docs/design-system.md and frontend/src/app/globals.css (CorpOS tokens with the liquid-glass look; nested backdrop-filter does not render).',
      reason: 'the judge\'s copy of Goodricke\'s design-language sentence, run on after a colon; the core gives the generic text and the sentence is a gk-design guard',
      replacedBy: [
        `Design quality is judged against the project's existing design language: ${DESIGN_LANGUAGE_CORE}`,
        '- The project design language lives in docs/design-system.md and frontend/src/app/globals.css (CorpOS tokens with the liquid-glass look; nested backdrop-filter does not render).',
      ],
    },
  ],
  'gk-research': [
    {
      golden: 'Scope: Search the repository in your working directory: code, docs/, CHANGELOG.md and git history.',
      reason: 'CHANGELOG.md is Goodricke\'s file; the rules name the repository (its root) and the core names a changelog generically',
      replacedBy: ['Scope: Search the repository: its code, docs, changelog and git history.'],
    },
    {
      golden: 'Source groups read: ["Search the repository in your working directory: code, docs/, CHANGELOG.md and git history.',
      reason: 'the critic sees the round-1 scope above as JSON',
      replacedBy: ['Source groups read: ["Search the repository: its code, docs, changelog and git history.'],
    },
    {
      golden: /^Scope: Follow the missing item wherever it points: the repo in your working directory \(code, docs\/, CHANGELOG\.md, git history\)/,
      reason: 'as for the round-1 scope: the core names a changelog generically',
      replacedBy: (s) => [s.replace('the repo in your working directory (code, docs/, CHANGELOG.md, git history)', 'the repository (its code, docs, changelog and git history)')],
    },
  ],
  'gk-diagnose': [
    {
      golden: 'Repository: /home/carls/apps/mike-dev (Goodricke; backend/ and frontend/).',
      reason: 'the repository now comes from args.project.root; the project\'s name and folders are not part of the core',
      replacedBy: ['Repository: the git repository at /home/carls/apps/mike-dev.'],
    },
    {
      // The golden's default scratchpad is one session's folder, whose name contains mike-dev.
      golden: /^If you need instrumentation, put it ONLY in \/tmp\/claude-1000\/[^ ]+\/scratchpad\/gk-diagnose-H\d+ \(mkdir -p it; other test agents share the scratchpad\), e\.g\. a probe script there that imports repo code; never add logging or edits to repo files\.$/,
      reason: 'the brief deletes DEFAULT_SCRATCHPAD (one session\'s folder): without args.scratchpad each test agent makes its own temporary folder with mktemp (the run with a scratchpad keeps the golden sentence with its path)',
      replacedBy: ['If you need instrumentation, make a temporary folder outside the repository with `mktemp -d` and delete it when done; put it ONLY there (each test agent makes its own), e.g. a probe script there that imports repo code; never add logging or edits to repo files.'],
    },
  ],
}

// The runs whose prompts are compared: between them every prompt variant of the golden.
const PRESERVED_RUNS = {
  'gk-design': [{}, { ui: true }, WORKFLOWS['gk-design'].full],
  'gk-research': [{}, { depth: 'thorough' }, WORKFLOWS['gk-research'].full],
  'gk-diagnose': [{}, WORKFLOWS['gk-diagnose'].full],
}

for (const wf of Object.keys(WORKFLOWS)) {
  test(`goodricke sentences preserved, ${wf}: every marked or listed golden sentence is in the matching neutral prompt`, async () => {
    const { args, responders } = WORKFLOWS[wf]
    const unmarked = UNMARKED[wf].map((u) => u.sentence)
    const exceptions = EXCEPTIONS[wf]
    const used = new Set()
    const seenUnmarked = new Set()
    let checked = 0
    for (const extra of PRESERVED_RUNS[wf]) {
      const old = await runWorkflow(golden(`${wf}.js`), args(extra), responders())
      const now = await runWorkflow(neutral(`${wf}.js`), { ...args(extra), project: goodricke() }, responders())
      const byLabel = new Map(now.calls.map((c) => [c.label, ws(c.prompt)]))
      assert.deepEqual([...byLabel.keys()].sort(), old.calls.map((c) => c.label).sort(), `${JSON.stringify(extra)}: the same stages run`)
      for (const c of old.calls) {
        const prompt = byLabel.get(c.label)
        for (const s of sentences(c.prompt)) {
          const listed = unmarked.includes(s)
          const exception = exceptions.find((e) => goldenIs(e, s))
          if (!MARK.test(s) && !listed && !exception) continue
          if (listed) seenUnmarked.add(s)
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
    for (const e of exceptions) assert.ok(used.has(e), `stale exception (no golden prompt has it): ${e.golden}`)
    for (const s of unmarked) assert.ok(seenUnmarked.has(s), `stale listed sentence (no golden prompt has it): ${s}`)
  })
}

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

test('stage schemas, design/research/diagnose: every stage gets the golden\'s output schema', async () => {
  for (const [wf, { args, responders, full }] of Object.entries(WORKFLOWS)) {
    const old = await schemasOf(golden(`${wf}.js`), args(full), responders())
    const now = await schemasOf(neutral(`${wf}.js`), { ...args(full), project: goodricke() }, responders())
    assert.deepEqual([...now.keys()].sort(), [...old.keys()].sort(), `${wf}: the same stages run`)
    for (const [label, json] of old) assert.equal(now.get(label), json, `${wf} ${label}`)
  }
})

// ---------------- the project's guards ----------------

test('goodricke project, design/research/diagnose: each stage brief ends with the guards that reach it, and the log names the project', async () => {
  for (const [wf, { phases }] of Object.entries(WORKFLOWS)) {
    const { calls, logs } = await fullRun(wf, goodricke())
    assert.deepEqual([...new Set(calls.map((c) => c.phase))], phases, `${wf}: every phase runs`)
    for (const c of calls) {
      const block = rulesBlock(reach(fixture.guards, wf, c.phase))
      assert.ok(block, `${wf} ${c.label}: some guard reaches the ${c.phase} stage`)
      assert.ok(c.prompt.endsWith(block), `${wf} ${c.label}: the brief ends with the project rules for the ${c.phase} stage`)
      assert.equal(c.prompt.split('Project rules:').length, 2, `${wf} ${c.label}: one project rules block`)
    }
    const n = (k) => (fixture[k] || []).length
    assert.ok(logs.includes(`Project: Goodricke (${n('guards')} guards, ${n('reviewFinders')} finders, ${n('signOff')} sign-off checks)`), logs.join('\n'))
  }
})

test('goodricke guards, design/research/diagnose: each guard scoped to one reaches exactly the stages where its golden has the sentence; string guards reach every stage', async () => {
  const counts = {}
  for (const wf of Object.keys(WORKFLOWS)) {
    const old = await fullRun(wf)
    const now = await fullRun(wf, goodricke())
    const all = now.calls.map((c) => c.label)
    for (const g of fixture.guards) {
      const text = guardText(g)
      const got = now.calls.filter((c) => c.prompt.includes(`\n- ${text}`)).map((c) => c.label)
      if (typeof g === 'string') {
        // A string guard binds every step in the repository (ruling R15).
        assert.deepEqual(got, all, `${wf} string guard: ${text}`)
      } else if (g.workflows && g.workflows.includes(wf)) {
        const want = old.calls.filter((c) => ws(c.prompt).includes(ws(text))).map((c) => c.label)
        assert.ok(want.length, `${wf}: a guard with no golden sentence behind it: ${text}`)
        assert.deepEqual(got, want, `${wf}: the stages this guard reaches:\n  ${text}`)
        counts[wf] = (counts[wf] || 0) + 1
      } else {
        assert.deepEqual(got, [], `${wf}: a guard scoped elsewhere: ${text}`)
      }
    }
  }
  assert.deepEqual(counts, { 'gk-design': 4, 'gk-research': 2, 'gk-diagnose': 5 })
  // A guard cannot follow ui: gk-design's design-language guards reach a non-UI run's stages
  // too, where the golden had no design-language text.
  const plain = await runWorkflow(neutral('gk-design.js'), { ...designArgs(), project: goodricke() }, designResponders())
  for (const g of scopedTo('gk-design')) assert.ok(plain.calls.every((c) => c.prompt.includes(`\n- ${g.text}`)), g.text)
})

test('scoped guards, design/research/diagnose: a string guard reaches every stage; an object guard only the stages both of its lists match', async () => {
  for (const [wf, { phases }] of Object.entries(WORKFLOWS)) {
    const [first, ...rest] = phases
    const guards = [
      'Everywhere.',
      { text: 'This workflow.', workflows: [wf] },
      { text: 'This workflow, first stage.', workflows: [wf], stages: [first] },
      { text: 'Any workflow, other stages.', stages: rest },
      { text: 'Another workflow.', workflows: [wf === 'gk-design' ? 'gk-research' : 'gk-design'] },
    ]
    const { calls, logs } = await fullRun(wf, { root: '/tmp/repo', guards })
    for (const c of calls) {
      const want = c.phase === first ? ['Everywhere.', 'This workflow.', 'This workflow, first stage.'] : ['Everywhere.', 'This workflow.', 'Any workflow, other stages.']
      assert.ok(c.prompt.endsWith(rulesBlock(want)), `${wf} ${c.label}: ${c.prompt.slice(c.prompt.indexOf('Project rules:'))}`)
    }
    assert.ok(logs.includes(`Project: (unnamed) (${guards.length} guards, 0 finders, 0 sign-off checks)`), logs.join('\n'))
    // Guards that reach no stage of this workflow leave no block at all.
    const none = await fullRun(wf, { root: '/tmp/repo', guards: guards.slice(4) })
    for (const c of none.calls) assert.ok(!c.prompt.includes('Project rules:'), `${wf} ${c.label}`)
  }
})

test('guard names, design/research/diagnose: this workflow with a phase it lacks logs one warning; the Goodricke fixture logs none', async () => {
  const typo = { 'gk-design': 'Proposals', 'gk-research': 'Deep read', 'gk-diagnose': 'Hypothesis' }
  for (const wf of Object.keys(WORKFLOWS)) {
    const guards = [
      { text: 'Stage typo.', workflows: [wf], stages: [typo[wf], WORKFLOWS[wf].phases[0]] },
      { text: 'Misspelt workflow.', workflows: ['gk-desgin', wf] },
    ]
    const { logs } = await fullRun(wf, { root: '/tmp/repo', guards })
    assert.deepEqual(logs.filter((l) => / names unknown (workflow|stage) /.test(l)), [`Project: guard 0 names unknown stage '${typo[wf]}'`, "Project: guard 1 names unknown workflow 'gk-desgin'"], wf)
    const g = await fullRun(wf, goodricke())
    assert.deepEqual(g.logs.filter((l) => / names unknown /.test(l)), [], `${wf}: the fixture names only real workflows and phases`)
  }
})

// ---------------- generic runs ----------------

const GENERIC_RUNS = {
  'gk-design': [{}, WORKFLOWS['gk-design'].full],
  'gk-research': [{ depth: 'thorough' }, WORKFLOWS['gk-research'].full],
  'gk-diagnose': [{ reproduce: 'make test' }, WORKFLOWS['gk-diagnose'].full],
}

test('generic run is clean, design/research/diagnose: no prompt names Goodricke and every prompt names the root', async () => {
  for (const [wf, { args, responders, phases }] of Object.entries(WORKFLOWS)) {
    const reached = new Set()
    for (const extra of GENERIC_RUNS[wf]) {
      const { calls, logs } = await runWorkflow(neutral(`${wf}.js`), { ...args(extra), project: { root: '/tmp/repo' } }, responders())
      for (const c of calls) {
        reached.add(c.phase)
        for (const re of [GOODRICKE_WORDS, MORE_WORDS, CHANGELOG_FILE]) {
          const hit = re.exec(c.prompt)
          assert.equal(hit, null, `${wf} ${c.label}: generic prompt names ${hit && hit[0]}`)
        }
        assert.ok(c.prompt.includes('the git repository at /tmp/repo'), `${wf} ${c.label}: names the root`)
        assert.ok(!c.prompt.includes('Project rules:'), `${wf} ${c.label}: no project rules without guards`)
        // The proposer asks for the open questions only the user can answer, not a named person.
        if (c.phase === 'Propose') assert.ok(!c.prompt.includes('Carl can answer'), `${wf} ${c.label}`)
      }
      assert.ok(logs.includes('Project: none (generic rules)'), logs.join('\n'))
      if (wf === 'gk-design') {
        const proposers = calls.filter((c) => c.phase === 'Propose')
        assert.ok(proposers.length >= 2, 'the proposers ran')
        for (const c of proposers) assert.ok(c.prompt.includes('the open questions only the user can answer.'), c.label)
      }
    }
    assert.deepEqual([...reached], phases, `${wf}: every phase ran`)
  }
})

test('generic run without a root, design/research/diagnose: prompts point at the working directory\'s repository', async () => {
  for (const [wf, { args, responders, full }] of Object.entries(WORKFLOWS)) {
    for (const project of [undefined, {}]) {
      const a = args(full)
      if (project !== undefined) a.project = project
      const { calls, logs } = await runWorkflow(neutral(`${wf}.js`), a, responders())
      assert.ok(calls.length > 3, wf)
      for (const c of calls) {
        assert.ok(c.prompt.includes('the git repository containing your working directory (run `git rev-parse --show-toplevel` first)'), `${wf} ${JSON.stringify(project)} ${c.label}`)
        assert.ok(!c.prompt.includes('the git repository at'), `${wf} ${JSON.stringify(project)} ${c.label}`)
      }
      assert.ok(logs.includes('Project: none (generic rules)'), logs.join('\n'))
    }
  }
  // gk-diagnose runs the reproduce command from the top level.
  const { calls } = await runWorkflow(neutral('gk-diagnose.js'), diagnoseArgs({ reproduce: 'make test' }), diagnoseResponders())
  for (const c of calls.filter((x) => x.label.startsWith('test:'))) {
    assert.ok(c.prompt.includes('Run the reproduce command in the FOREGROUND, from the repository\'s top level: `( make test ) 2>&1 | tail -40`.'), c.label)
  }
})

// ---------------- gk-diagnose's scratchpad ----------------

const MKTEMP = 'make a temporary folder outside the repository with `mktemp -d` and delete it when done'

test('diagnose uses injected scratchpad: with args.scratchpad every prompt that mentions a scratchpad names it', async () => {
  for (const project of [{ root: '/tmp/repo' }, goodricke(), undefined]) {
    const a = diagnoseArgs({ scratchpad: '/tmp/s', reproduce: 'make test' })
    if (project !== undefined) a.project = project
    const { calls } = await runWorkflow(neutral('gk-diagnose.js'), a, diagnoseResponders())
    assert.ok(calls.some((c) => c.label.startsWith('test:')), 'the test stage ran')
    for (const c of calls) {
      if (/scratchpad/i.test(c.prompt)) assert.ok(c.prompt.includes('/tmp/s'), `${c.label}: mentions a scratchpad without naming /tmp/s`)
      assert.ok(c.prompt.includes('/tmp/s'), `${c.label}: every prompt names the scratchpad`)
      assert.ok(!c.prompt.includes('mktemp'), `${c.label}: no mktemp when a scratchpad is given`)
      assert.ok(!/\/tmp\/claude|claude-1000/.test(c.prompt), `${c.label}: no golden default scratchpad`)
    }
    for (const c of calls.filter((x) => x.label.startsWith('test:'))) {
      const id = c.label.slice('test:'.length)
      assert.ok(c.prompt.includes(`If you need instrumentation, put it ONLY in /tmp/s/gk-diagnose-${id} (mkdir -p it; other test agents share the scratchpad)`), c.label)
    }
  }
  // The hook passes the session's scratchpad as the OS spells it; on Windows a drive path,
  // which the prompts give with forward slashes, so the paths built on it use one separator.
  const win = await runWorkflow(neutral('gk-diagnose.js'), diagnoseArgs({ scratchpad: 'C:\\Users\\Carl Selby\\AppData\\Local\\Temp\\s', project: { root: 'C:/work/repo' } }), diagnoseResponders())
  for (const c of win.calls) assert.ok(c.prompt.includes('C:/Users/Carl Selby/AppData/Local/Temp/s'), c.label)
})

test('diagnose Windows scratchpad: the instrumentation path has forward slashes only', async () => {
  const { calls } = await runWorkflow(neutral('gk-diagnose.js'), diagnoseArgs({ scratchpad: 'C:\\Users\\x\\scratch', reproduce: 'make test', project: { root: '/r' } }), diagnoseResponders())
  const t = calls.find((c) => c.label === 'test:H1')
  assert.ok(t, 'the test stage ran')
  assert.ok(t.prompt.includes('C:/Users/x/scratch/gk-diagnose-H1'), t.prompt)
  assert.ok(!t.prompt.includes('\\'), t.prompt)
  // A POSIX scratchpad, trailing slash and all, is used exactly as given.
  const posix = await runWorkflow(neutral('gk-diagnose.js'), diagnoseArgs({ scratchpad: '/tmp/s/', reproduce: 'make test', project: { root: '/r' } }), diagnoseResponders())
  assert.ok(posix.calls.find((c) => c.label === 'test:H1').prompt.includes('put it ONLY in /tmp/s//gk-diagnose-H1 '))
})

test('diagnose core rules: every prompt forbids changing the checkout, with no project rules too', async () => {
  const LINE = "Never change the repository's working tree, index, HEAD or branches: no git checkout, switch, stash, reset, restore, clean, merge, rebase or bisect, not even `checkout --detach`."
  for (const project of [{}, { root: '/r' }]) {
    const { calls } = await runWorkflow(neutral('gk-diagnose.js'), diagnoseArgs({ reproduce: 'make test', project }), diagnoseResponders())
    assert.deepEqual([...new Set(calls.map((c) => c.phase))], ['Hypothesise', 'Test', 'Adjudicate'], JSON.stringify(project))
    for (const c of calls) {
      assert.ok(c.prompt.includes(LINE), `${JSON.stringify(project)} ${c.label}`)
      assert.ok(!c.prompt.includes('Project rules:'), `${c.label}: no guards, no project rules`)
    }
  }
})

test('diagnose without scratchpad says mktemp: every prompt makes its own temporary folder outside the repository', async () => {
  for (const project of [{ root: '/tmp/repo' }, goodricke(), undefined]) {
    const a = diagnoseArgs({ reproduce: 'make test' })
    if (project !== undefined) a.project = project
    const { calls } = await runWorkflow(neutral('gk-diagnose.js'), a, diagnoseResponders())
    assert.ok(calls.some((c) => c.label.startsWith('test:')), 'the test stage ran')
    for (const c of calls) {
      assert.ok(c.prompt.includes(MKTEMP), `${c.label}: the mktemp rule`)
      assert.ok(!/\/tmp\/claude|claude-1000/.test(c.prompt), `${c.label}: no golden default scratchpad`)
      assert.ok(!c.prompt.includes('/tmp/s'), c.label)
    }
    for (const c of calls.filter((x) => x.label.startsWith('test:'))) {
      assert.ok(c.prompt.includes(`If you need instrumentation, ${MKTEMP}; put it ONLY there (each test agent makes its own)`), c.label)
    }
  }
  // The golden's default (Carl's session of 2026-10-28) is gone from the script.
  const src = readFileSync(neutral('gk-diagnose.js'), 'utf8')
  assert.doesNotMatch(src, /DEFAULT_SCRATCHPAD|PROD_WORKTREE|\/tmp\/claude|\/home\/carls/)
})

test('diagnose scratchpad check: an absolute folder outside the repository, on any OS; otherwise the run stops before any agent', async () => {
  const run = (scratchpad, project) => {
    const seen = []
    const spy = [{ match: /.*/, reply: (prompt, opts) => { seen.push(opts.label); return minimalFromSchema(opts.schema) } }]
    const a = diagnoseArgs({ scratchpad })
    if (project !== undefined) a.project = project
    return { seen, done: runWorkflow(neutral('gk-diagnose.js'), a, spy) }
  }
  const bad = [
    ['scratch', undefined], ['./scratch', { root: '/tmp/repo' }], ['', undefined], [7, undefined],
    ['/tmp/repo', { root: '/tmp/repo' }], ['/tmp/repo/s', { root: '/tmp/repo' }], ['/tmp/repo/s', { root: '/tmp/repo/' }],
    ['C:\\work\\repo\\s', { root: 'C:/work/repo' }], ['C:/work/repo/s/', { root: 'C:/work/repo' }],
  ]
  for (const [scratchpad, project] of bad) {
    const { seen, done } = run(scratchpad, project)
    await assert.rejects(done, (e) => e.message === 'gk-diagnose: args.scratchpad must be an absolute directory path outside the repo when given', JSON.stringify([scratchpad, project]))
    assert.deepEqual(seen, [], JSON.stringify([scratchpad, project]))
  }
  const good = [
    ['/tmp/s', undefined], ['/tmp/s', { root: '/tmp/repo' }], ['/tmp/repository/s', { root: '/tmp/repo' }],
    ['C:\\Users\\c\\Temp\\s', { root: 'C:/work/repo' }], ['C:/work/repo2/s', { root: 'C:/work/repo' }], ['D:/s', undefined],
  ]
  for (const [scratchpad, project] of good) {
    const { seen, done } = run(scratchpad, project)
    await done
    assert.ok(seen.length > 0, JSON.stringify([scratchpad, project]))
  }
})

// ---------------- the shared gk-project block ----------------

// A sample of the malformed projects every workflow rejects (the full list runs against
// gk-review and gk-build in review-build.test.mjs; the block is verbatim in all five scripts).
const PROJECT_MALFORMED = [
  [null, 'not an object'],
  [[], 'not an object'],
  [{ root: '' }, 'root'],
  [{ guards: ['line one\nline two'] }, 'guards[0]'],
  [{ guards: [{ text: 'ok', stages: [] }] }, 'guards[0].stages'],
  [{ guards: ['ok', { text: 'ok', stage: ['Judge'] }] }, 'guards[1].stage'],
  [{ reviewFinders: [{ key: 'a' }] }, 'reviewFinders[0].brief'],
  [{ signOff: [{ key: 'k' }] }, 'signOff[0].seat'],
  [{ packages: [] }, 'packages'],
]

test('project malformed throws, design/research/diagnose: before any agent runs, naming the field; well-formed projects run', async () => {
  for (const [wf, { args, responders, full }] of Object.entries(WORKFLOWS)) {
    for (const [project, field] of PROJECT_MALFORMED) {
      const seen = []
      const spy = [{ match: /.*/, reply: (prompt, opts) => { seen.push(opts.label); return null } }]
      await assert.rejects(
        runWorkflow(neutral(`${wf}.js`), { ...args(full), project }, spy),
        (e) => e.message === `${wf}: args.project is malformed (${field})`,
        `${wf} ${JSON.stringify(project)} → (${field})`,
      )
      assert.deepEqual(seen, [], `${wf} ${JSON.stringify(project)}: no agent ran`)
    }
    // The fixture, a root-only project, {} and every field of schema v1 are well formed.
    const allFields = { version: 1, name: 'X', sharedBranch: 'main', root: '/tmp/repo', check: 'make check', packages: ['web'], guards: ['g'], reviewFinders: [{ key: 'k', brief: 'b' }], signOff: [{ key: 'k', seat: 's', sharedFile: 'NOTICE', trigger: 't', paths: ['^web/'], uiJudge: false, implementer: 'i', reviewer: 'r' }] }
    for (const project of [goodricke(), { root: '/tmp/repo' }, {}, allFields]) {
      const { calls } = await runWorkflow(neutral(`${wf}.js`), { ...args(full), project }, responders())
      assert.ok(calls.length > 3, `${wf} ${JSON.stringify(project).slice(0, 60)}`)
    }
  }
})
