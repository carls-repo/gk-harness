export const meta = {
  name: 'gk-diagnose',
  description: 'Diagnose a bug whose cause is unclear: per-lens hypotheses, one evidence test per hypothesis, one adjudication',
  whenToUse:
    'Use when a Goodricke bug or unexpected behaviour has no known cause. Args: { symptom: string (REQUIRED); evidence?: string[] — log lines, stack traces, observations; files?: string[] — starting points; reproduce?: string — a shell command that reproduces the symptom, if one exists; lenses?: string[] — default [\'data-and-state\',\'control-flow-and-concurrency\',\'environment-and-config\']; scratchpad?: string — absolute dir outside the repo for temporary instrumentation, default this session\'s scratchpad (pass your own session\'s); profile?: \'fable\'|\'opus\'|\'sonnet\' — the seat\'s orchestration profile, default \'fable\' (routing is identical in every profile); escalate?: boolean — Carl\'s word to route the apex judge to fable (no effect here) }. Returns structured JSON {hypotheses, verdicts, adjudication}; the calling session writes the synthesis. Nothing is fixed and nothing is committed.',
  phases: [
    { title: 'Hypothesise', detail: 'one read-only agent per lens, at most 4 ranked hypotheses each; merged across lenses by normalised mechanism', model: 'opus' },
    { title: 'Test', detail: 'one agent per merged hypothesis tries to confirm or refute it with evidence', model: 'opus' },
    { title: 'Adjudicate', detail: 'one agent reconciles every verdict into a mechanism, contradictions and open questions', model: 'opus' },
  ],
}

// `args` may arrive as a raw JSON string; normalise so both shapes work.
const ARGS = typeof args === 'string' ? (() => { try { return JSON.parse(args) } catch (e) { return args } })() : args

const DEFAULT_LENSES = ['data-and-state', 'control-flow-and-concurrency', 'environment-and-config']
const MAX_PER_LENS = 4
const DEFAULT_SCRATCHPAD = '/tmp/claude-1000/-home-carls-apps-mike-dev/97d27023-7cee-4353-b32f-4ea2e68a3f58/scratchpad'
const REPO = '/home/carls/apps/mike-dev'
const PROD_WORKTREE = '/home/carls/apps/mike'

const isStrArray = v => Array.isArray(v) && v.every(s => typeof s === 'string')
if (!ARGS || typeof ARGS !== 'object') {
  throw new Error('gk-diagnose requires args: { symptom: string, evidence?: string[], files?: string[], reproduce?: string, lenses?: string[], scratchpad?: string, profile?: \'fable\'|\'opus\'|\'sonnet\', escalate?: boolean }')
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
if (ARGS.scratchpad !== undefined && (typeof ARGS.scratchpad !== 'string' || !ARGS.scratchpad.startsWith('/') || ARGS.scratchpad.startsWith(REPO))) {
  throw new Error('gk-diagnose: args.scratchpad must be an absolute directory path outside the repo when given')
}

// ---------------- Profile (three-seat policy, 2026-10-13) ----------------
// 'fable' = Mode A (default, unchanged behaviour); 'opus' = Mode B; 'sonnet' = Mode C.
// Routing here is the same in every profile (opus high / opus high / opus xhigh); the contract is
// present so the profile argument is accepted and reported.
const PROFILE = ARGS.profile === undefined ? 'fable' : ARGS.profile
if (!['fable', 'opus', 'sonnet'].includes(PROFILE)) throw new Error(`gk-diagnose: args.profile must be one of fable|opus|sonnet (got ${JSON.stringify(ARGS.profile)})`)
if (ARGS.escalate !== undefined && typeof ARGS.escalate !== 'boolean') throw new Error('gk-diagnose: args.escalate, when given, must be a boolean')
const ESCALATE = ARGS.escalate === true  // Carl's word: route the apex judge to fable under a non-fable profile
const APEX = PROFILE === 'fable' ? 'fable' : 'opus'   // the top of the ladder for this profile
log(`Profile: ${PROFILE} (apex ${APEX}${ESCALATE ? ', escalate noted' : ''}; no fable stage in this workflow — routing is the same in every profile)`)

const symptom = ARGS.symptom.trim()
const evidence = ARGS.evidence || []
const files = ARGS.files || []
const reproduce = ARGS.reproduce ? ARGS.reproduce.trim() : null
const lenses = ARGS.lenses ? [...new Set(ARGS.lenses.map(l => l.trim()))] : DEFAULT_LENSES
const SCRATCHPAD = ARGS.scratchpad || DEFAULT_SCRATCHPAD

const CONTEXT = `Repository: ${REPO} (Goodricke; backend/ and frontend/).
SYMPTOM: ${symptom}
EVIDENCE SO FAR:
${evidence.length ? evidence.map(e => `- ${e}`).join('\n') : '- (none supplied)'}
STARTING FILES: ${files.length ? files.join(', ') : '(none supplied; locate them yourself)'}
REPRODUCE COMMAND: ${reproduce || '(none supplied)'}`

const RULES = `RULES:
- Do NOT fix anything. Do NOT edit, create or delete any file in the repo. Do NOT commit.
- Dev only; never prod. Any database read goes through the supabase-dev MCP; never source a prod .env, never touch :8080 or the prod worktree (${PROD_WORKTREE}).
- NEVER change the checkout: no git checkout/switch/stash/reset/restore/clean/merge/rebase/bisect, not even \`checkout --detach\`. Other sessions, the dev stack and the shared de-id sidecar run from this working tree. To read another revision use \`git show <rev>:<path>\` or \`git grep <pattern> <rev>\`; to trial a merge use \`git merge-tree --write-tree <a> <b>\`; if you truly need a tree on disk, \`git worktree add --detach <a fresh dir under your scratchpad> <rev>\` and remove it when done.
- Never run \`npm run dev\` or restart services; systemd owns the stacks.
- No paid LLM API calls (Anthropic/OpenAI/Gemini) from anything you write or run, other than the supplied reproduce command; standing rule: API spend needs Carl's prior approval.
- Never quote secret values (keys, tokens, passwords) in your evidence; name the env var instead.
- Ignore the repo's many untracked scratch scripts (backend/scripts/_*.ts and similar); they are not the app.
- Run any command in the FOREGROUND and pipe its output through \`tail -40\`.
- Stay on this symptom; do not expand scope to other bugs you notice (one line in your evidence at most).
- Your final output is consumed by a script, not a human: return only the structured result.`

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
  agent(
    `You are diagnosing a bug through ONE lens: "${lens}" (lens ${i + 1} of ${lenses.length}). Other agents cover the other lenses (${lenses.filter(l => l !== lens).join(', ') || 'none'}); stay inside yours.

${CONTEXT}

TASK: read the relevant code paths (read-only) and propose at most ${MAX_PER_LENS} distinct hypotheses for the mechanism that produces this symptom, seen through the "${lens}" lens. Rank them by confidence, highest first. Each needs: a short id (e.g. "${lens.slice(0, 3)}-1"), the mechanism (trigger to symptom), whereToLook (repo-relative files), howToTest (a concrete confirm-or-refute check), and confidence 0..1. Prefer specific, falsifiable mechanisms over vague categories.

${RULES}
- Read-only: no instrumentation, no commands beyond read-only inspection (grep, cat, git log).`,
    { label: `hypothesise:${lens}`, phase: 'Hypothesise', model: 'opus', effort: 'high', schema: HYP_SCHEMA },
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
  return { hypotheses, verdicts: [], adjudication: null }
}

// ---- Test: one agent per merged hypothesis --------------------------------
phase('Test')
const verdictsRaw = await pipeline(hypotheses, (h, _orig, i) =>
  agent(
    `You are testing ONE hypothesis (${h.id}, ${i + 1} of ${hypotheses.length}) for a bug. Try hard to CONFIRM OR REFUTE it with evidence; do not lean towards confirming it.

${CONTEXT}

HYPOTHESIS ${h.id} (from lens(es): ${h.lenses.join(', ')}):
Mechanism: ${h.mechanism}
Where to look: ${h.whereToLook.join(', ') || '(unspecified)'}
How to test: ${h.howToTest || '(unspecified; devise a check)'}

METHOD:
1. Trace the code path the mechanism claims, citing file:line.
2. ${reproduce ? `Run the reproduce command in the FOREGROUND, from ${REPO}: \`( ${reproduce} ) 2>&1 | tail -40\`. Note whether its output fits the mechanism.` : 'No reproduce command was supplied; rely on code reading and safe read-only checks.'}
3. If you need instrumentation, put it ONLY in ${SCRATCHPAD}/gk-diagnose-${h.id} (mkdir -p it; other test agents share the scratchpad), e.g. a probe script there that imports repo code; never add logging or edits to repo files. Name what you created in your evidence.
4. Verdict: 'confirmed' only with direct evidence the mechanism occurs AND produces this symptom; 'refuted' with evidence it cannot; otherwise 'undetermined'. Set contradictsPriorEvidence=true if anything you found conflicts with the supplied evidence, and say what in your evidence list.
Return id "${h.id}".

${RULES}`,
    { label: `test:${h.id}`, phase: 'Test', model: 'opus', effort: 'high', schema: VERDICT_SCHEMA },
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
  adjudication = await agent(
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
- Read-only: no instrumentation, no commands beyond read-only inspection.`,
    { label: 'adjudicate', phase: 'Adjudicate', model: 'opus', effort: 'xhigh', schema: ADJ_SCHEMA },
  )
} catch (e) {
  log(`Adjudicate agent threw (${e && e.message}); returning hypotheses and verdicts without an adjudication`)
}
log(adjudication ? `Adjudicate done: mechanism ${adjudication.mechanism ? 'named' : 'not established'}` : 'Adjudicate agent returned nothing')

return { hypotheses, verdicts, adjudication: adjudication || null }
