# gk-harness: route by task — design

Date: 12 November 2026. Status: draft, revised after the builder check and the Fable 5.2
review (section 16). No questions are open. Next: Carl's approval of this spec, then a plan.
Published copy (revise this one, not a new doc): https://claude.ai/artifact/4HppTtscZrjZgGg9X1LZq2
Source: the suggestions doc "gk-harness for Fable 5.2 — suggestions"
(https://claude.ai/artifact/RMze1tuiGyVFXUUMDkbWjf) and Carl's seven answers of 12 November.

## 1. Why

Fable 5.2 has arrived, and Anthropic's guide "Which Claude model should you build with?"
(Addy Osmani, claude.dev, 9 November 2026) gives clear advice that today's harness does not follow:

- Opus 5.5 at `medium` is the default starting point for coding and knowledge work.
- Past `high`, try the next model up at a lower effort instead of more effort. Fable 5.2 at
  `high` matched Opus 5.5 at `max` for about half the cost per task.
- Escalate in this order: add a check, raise effort one level, then move up a model.
- Small models only where mistakes are cheap to spot (search, logs, test runs). Code edits and
  judgement stay on the main model.
- Orchestration pays for wide fan-out, not for one chain of dependent steps.
- Compare cost per finished task, not price per token.

Today the harness picks one of three modes from the model in the seat (Fable → A, Opus → B,
Sonnet → C), and each mode has its own tier map. The tier facts live in six places at once:

- the policy prose;
- five workflow scripts;
- `hooks/gk_model.py`;
- the seat card;
- eight agent files;
- the installer's agent list.

A tuning change means editing all of them by hand.

Carl also wants to try legal work: document review and regulatory advice.

## 2. What Carl decided (12 November 2026)

1. **Direction:** route by task from one table. Modes A, B and C retire.
2. **Default seat:** Opus 5.5 at `medium`. Fable 5.2 at `medium` is a launch option.
3. **Ultracode:** off by default. It can be turned on per session for audits and broad sweeps.
4. **Legal data:** the legal lab starts on public and synthetic material only. De-identified
   material is a later decision, with one rule for every model.
5. **Legal sources:** public sources only for now. A paid service may come later, for the gaps
   the lab shows.
6. **Eval set:** Claude drafts it (section 10). Carl approves it before anything runs.
7. **Review:** a Fable 5.2 reviewer at `high` reads this spec before anything is built. That
   review is done; section 16 records it.
8. **Switch, then measure** (decided after the review): the new design goes live on Anthropic's
   evidence. It is measured afterwards against a tagged copy of today's harness, and any move that
   does worse reverts.
9. **Lease review standard** (decided after the review): the lease eval uses a synthetic tenant
   playbook, not Goodricke's.

## 3. Goals, non-goals and anti-goals

**Goals**

- **One data file decides the routing.** `routes.json` says which model and effort does each
  kind of work. The agents, the policy's routing table, the workflows' stage tiers and the seat
  card are generated from it.
- **Opus is the default seat.** The default seat is Opus 5.5 at `medium`. Fable 5.2 does the
  work the guide gives it: the hardest builds, plan checks, judging Opus work, design panels and
  regulatory analysis.
- **The guide's escalation ladder** replaces our `xhigh` and `max` rungs.
- **A short policy** of about 120 lines that Fable 5.2 can follow without stalling. It has one
  list of what needs Carl's word, one list of what needs no approval, and a default for
  everything else.
- **Measurement after the switch.** The new design goes live on Anthropic's evidence (decision
  8). It is then measured against a tagged copy of today's harness. Each stage move is measured
  on its own where an instrument exists, and recorded as unmeasured where none does. A move that
  does worse reverts.
- **A legal lab** with two pipelines. Their checkable parts (quotes, citations, currency,
  provenance) are checked by code, and a checker's result is never relayed by a model.

**Non-goals**

- No classifier hook that guesses the task class from the prompt. The seat decides the class.
- No new hook that runs on every prompt.
- No change to the peer-coordination hooks, the publish gate or `gk-project.json`, beyond what
  this spec names.
- No client or de-identified material in the legal lab.
- No paid legal databases.
- No change to Goodricke's own `CLAUDE.md`. Trimming it is a separate job.

**Anti-goals** (failure even if it technically works)

- A route that gets cheaper by quietly getting worse.
- A Fable outage, or a model substitution by the platform, that silently downgrades a legal
  output.
- Eval runs that can reach the live install, the shared checkout, another session's worktree,
  the app directories or production.
- A policy so full of cautions that the seat asks for approvals it does not need.
- Two sources of truth for any tier fact.

## 4. The routing table: `routes.json`

### 4.1 Where it lives and who reads it

`routes.json` sits at the top of the gk-harness repo and is installed to
`~/.claude/gk-harness/routes.json`. A new command, `gkh gen`, writes everything derived from it
into the repo, and `gkh gen --check` (run in CI) fails when a derived file is stale.

The derived files are:

- `agents/worker-*.md`, one per tier;
- the installer's agent list;
- the routing table inside `policy.md`, between generated-block markers;
- a routes block inside each workflow script, including its `meta.phases` model labels, between
  markers such as `// <gk-routes v1>` … `// </gk-routes>`;
- the seat facts used by `hooks/gk_model.py`.

Workflow scripts cannot read files or import modules, so each one carries its own generated copy
of the stage tiers it uses.

### 4.2 Shape

The builder may choose the exact field names. The file must hold:

- **`version`**: an integer, bumped on every route change.
- **`models`**: for each alias (`haiku`, `sonnet`, `opus`, `fable`):
  - its full model id, used for `modelSettings` keys and pricing only. Agent files and workflow
    scripts use the alias.
  - a capability rank: haiku 1, sonnet 2, opus 3, fable 4.
  - its prices per million tokens (input, output, cache write, cache read), with the date they
    were copied. Haiku's price above a 100K-token prompt is listed too.
- **`efforts`**: the order `low` < `medium` < `high` < `xhigh` < `max`.
- **`tiers`**: named pairs, for example `opus-high` → {model `opus`, effort `high`}. Each tier
  generates `agents/worker-<tier>.md` with its description and tool list.
- **`ladder`**: the escalation order (section 6.1).
- **`classes`**: the task classes, each with a tier, who does it (seat, worker or workflow) and
  its usual outcome check.
- **`stages`**: every workflow stage, keyed `<workflow>.<stage>`, with:
  - its tier;
  - any conditional tier (for example UI work, or a piece's class);
  - `noFable`: the tier used when Fable is off (section 6.3). Required on every Fable stage.
  - `onFail`: `rerun:<tier>`, `unfinished` or `hold` (section 6.3);
  - `apex: true` if the `#fable` dial lifts it.
- **`judges`**: who judges whom (section 6.2).
- **`seat`**: the default seat tier, the launchers, and the effort each seat model gets.
- **`evidence`**: for each route, the id of the eval record that justified it, or `"guide"` for
  a route adopted on Anthropic's guidance unmeasured. The records themselves live in
  `docs/evals/`, not in `routes.json`.

### 4.3 Capability order

A tier's capability is ordered first by model rank, then by effort. So `fable-medium` is above
`opus-xhigh`, `opus-xhigh` is above `opus-high`, and `opus-high` is above `opus-medium`.

This one order is used everywhere: the seat rule (section 5.1), the judges test (section 6.2) and
the ladder.

### 4.4 Every route: today, new, and how each move is measured

"Today" is Carl's live setup: a Fable seat at `high`, Mode A, with ultracode on. "New" is the
design. The last column names the instrument that measures the move (section 10).

**Seat and classes**

| Class | Examples | Today (Mode A) | New | Instrument |
| --- | --- | --- | --- | --- |
| seat | the main session | Fable `high`, orchestrates everything | Opus `medium`, inline first (5.1) | E2E |
| chat, lookup | conversation, one file or fact | seat | seat | — |
| search | codebase search with no file list | built-in Explore on the seat model (Fable) | Explore on `haiku-medium` | SEARCH, E2E |
| run-report | run tests, builds, audits or logs, and report | `haiku-low` | `haiku-low` | — |
| mechanical | renames, find-and-replace, boilerplate from an exact spec, dependency bumps, changelog and doc touch-ups | `haiku-low` / `haiku-high` | `sonnet-medium` | E2E |
| known-cause | a fix with a known cause, a scoped feature from a spec, tests for existing behaviour, CRUD | `sonnet-medium` | `sonnet-medium` | — |
| feature | a feature, a multi-file change, an unclear bug, a refactor that needs intent, code review | `opus-high` | `opus-medium`, then `opus-high` | E2E |
| ui-build | UI built to a fixed design | `fable-xhigh` | `opus-high` | E2E (2 UI tasks) |
| hard | migrations, performance, concurrency, architecture, security-sensitive code, a new visual direction | `fable-xhigh` | `fable-medium`, then `fable-high` | E2E (1 task) |
| legal-doc | review of one document that fits one context | — | the seat at `opus-medium` or above, then an `opus-high` check | legal smoke |

**Workflow stages**

| Stage | Today (Fable profile) | New | `noFable` | Instrument |
| --- | --- | --- | --- | --- |
| gk-review scope | `haiku-high` | `haiku-medium` | — | none (lifts only) |
| gk-review find, refute | `sonnet-medium` | `sonnet-medium` | — | — |
| gk-review judge | `fable-xhigh` for UI, client wording or security; else `opus-xhigh` | `opus-high`, raised to the author's tier (6.2); `fable-high` for UI, client wording or security | `opus-xhigh` | JUDGE |
| gk-build plan check | `fable-xhigh` | `fable-medium` | `opus-xhigh` | PLAN |
| gk-build implement | `opus-high`; hard `opus-xhigh` | by the piece's class (8.2): spec `sonnet-medium`, judgement `opus-medium`, hard and security `fable-medium` | hard and security `opus-high` | E2E |
| gk-build check | `haiku-low` | `haiku-low`, decided by exit code | — | — |
| gk-build per-piece review | `opus-xhigh` | the implementer's tier, and never below `opus-high` | `opus-xhigh` | JUDGE |
| gk-build judge | `fable-xhigh` for UI; else `opus-xhigh` | `fable-medium`; `fable-high` for UI | `opus-xhigh` | JUDGE |
| gk-diagnose hypothesise, test | `opus-high` | `opus-high` | — | — |
| gk-diagnose adjudicate | `opus-xhigh` | `fable-medium` | `opus-xhigh` | unmeasured (guide) |
| gk-research sweep | `haiku-high` | `haiku-medium` | — | unmeasured (guide) |
| gk-research merge, deep-read | `opus-high` | `opus-high` | — | — |
| gk-research critic | `opus-xhigh` | `opus-high` | — | unmeasured (guide) |
| gk-design proposals | `opus-xhigh`; UI `fable-xhigh` | `opus-high`; UI `fable-medium` | UI `opus-high` | unmeasured (guide) |
| gk-design judges | `fable-xhigh` | `fable-high` | `opus-xhigh` | unmeasured (guide) |
| gk-design UI sign-off | `fable-xhigh` | `fable-high` | `opus-xhigh` | unmeasured (guide) |

Agent names say their tier (`worker-opus-high`, `worker-haiku-medium`), so a name never quietly
changes tier.

Opus `xhigh` stays as a tier, but only as the `noFable` fallback for judge stages. It is replaced
by `opus-high` only if a replay set shows that `opus-high` matches it. Max effort is not routed.

## 5. The seat

### 5.1 How the seat routes

- **Classify.** The seat reads the request, picks its class from the table, and takes that
  class's tier.
- **Inline first.** The seat does the work itself only when both of these hold:
  - its model and current effort are at or above the class tier, in the section 4.3 order;
  - the work is one chain of steps that fits its context.

  Otherwise it delegates to a worker at the class's tier. An Opus `medium` seat therefore does
  feature work itself, but hands `ui-build` to `worker-opus-high`, unless Carl has raised the
  seat with `/effort high`.
- **Verbose reading always goes to a worker.** Searches with no file list, long logs and big
  test output go to a worker, so the seat's context stays lean.
- **A Fable seat** also delegates separable implementation chunks to their class tier. This is
  the guide's "planner and orchestrator" use of Fable.
- **A gk workflow runs only when the work fans out:**
  - three or more independent pieces;
  - a review before merging to the shared branch, when the diff is over about 150 changed lines
    or touches a security or UI path by the project's rules. A smaller diff gets one
    `worker-opus-high` reviewer in a fresh context.
  - a diagnosis after two failed attempts;
  - a multi-source research sweep;
  - multi-document legal review;
  - regulatory advice.

  The `#deep` dial and ultracode also send work to a workflow.

### 5.2 The route line

When a reply delegates, runs a workflow, or does work whose class tier sits above the seat, it
opens with one line:

`Route: <class> → <who> (<model> <effort>) · check: <outcome check>`

The line lets Carl correct a wrong call from his phone. `gkh report` reads route lines on a best
effort basis only (section 9).

### 5.3 Launching and settings

- **`gk`** = `claude --model opus --effort medium`.
- **`gk-fable`** = `claude --model fable --effort medium`. `/effort high` gives a hard turn more.
- **`gk-ultra`** = `gk` plus `--settings '{"ultracode":true}'`.
- **`gk-opus` and `gk-sonnet`** retire.
- **Desktop app.** A desktop-app session uses `#deep` per turn. Stage 0 finds out whether the app
  exposes a per-session ultracode switch.
- **`gkh`** is a shell function that runs `python3 <root>/install.py "$@"`. PowerShell gets the
  equivalent.

**Settings the installer owns.** The installer writes each key below only when it is absent or
still holds the value the installer last wrote, as recorded in its manifest. A value Carl changed
by hand, for example through `/model`, is left alone and reported by `gkh status`. Prior values
are recorded for uninstall.

- `ultracode`: `false`.
- `model`: `"opus"`, so new desktop-app sessions start on the default seat. The seat card says
  when the saved default is not Opus.
- `modelSettings.<model id>.effortLevel`, from `routes.json` `seat`. Stage 0 finds out how this
  ranks against the top-level `effortLevel`, and the installer handles both.
- `env.CLAUDE_CODE_SUBAGENT_MODEL`: `"opus"`. It is set for every session rather than only in
  `gk-fable`, because most sessions start in the desktop app, where launchers don't apply. Under
  an Opus seat it changes nothing. A test checks that `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` and
  `CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL` are unset, since either would override every agent's own
  model.

### 5.4 Dials and the Fable switch

The seat recognises a dial only as a whole word at the start or end of Carl's prompt, so pasted
text can't trigger one. No new hook is added. The seat passes dials to workflows in
`args.dials`, as it passes `escalate` today.

| Dial | Effect |
| --- | --- |
| `#nofable` | No Fable stage or worker this turn. Every Fable stage runs on its `noFable` tier, and the route line says so. |
| `#fable` | Every `apex` stage runs on `fable-high` (this replaces `escalate: true`). |
| `#deep` | Run the matching gk workflow even where the inline-first rule would work solo. |
| `#solo` | No workers or workflows this turn. |

**A switch that stays set.** `gkh fable off` and `gkh fable on` write a state file. The Workflow
hook (today's `gk-profile.py`, renamed `gk-route.py`) reads it and adds `nofable` to every gk
workflow's dials. The seat card shows "Fable off" while it is set. This covers a Fable usage
limit that lasts hours or days, which a one-turn dial cannot.

### 5.5 Seat card and state

- `hooks/gk_model.py` drops `MODE`, `RANK`, `profile_for_model` and `resolve_profile`.
- The card reads: `gk-harness seat: <model> at <effort> · routes v<N>`. It adds a note when:
  - the effort differs from `routes.json`'s value for that model;
  - the saved default model is not Opus;
  - Fable is switched off.
- `save_seat` stops recording a profile.
- The Workflow hook injects the seat's tier as `args.seat`, which gk-review uses for its judge
  floor (section 6.2).

## 6. Escalation, judging and failure

### 6.1 Escalation

1. **Check.** Run or add the outcome check: tests, a build, a reproduction, a quote check or a
   citation check. Retry once on the same tier, with the failure in the brief.
2. **Effort +1** on the same model. The seat cannot raise its own effort, so for seat work this
   step means delegating to the worker one rung up.
3. **Next model up, at `medium`.**

The ladder: `haiku-medium` → `sonnet-medium` → `sonnet-high` → `opus-medium` → `opus-high` →
`fable-medium` → `fable-high`.

- gk-build does not retry a failed piece. It returns the piece as failed, and the seat re-runs it
  on the next rung.
- A failed attempt's report goes verbatim into the next brief.

### 6.2 Judging

- **The outcome check decides wherever one exists.** A model's statement that tests pass never
  counts.
- **The judge is at least as capable as the work it judges.** A test over `routes.json` `judges`
  checks every judged pair in the section 4.3 order. Where the judged tier varies (gk-build's
  per-piece review), the rule is "the implementer's tier, never below `opus-high`".
- **gk-review's judge** is raised to the author's tier when the diff came from above `opus-high`.
  The author's tier is the `author` argument when the caller gives it, and otherwise the seat's
  tier injected by the hook. So a diff written by a Fable seat gets at least a `fable-medium`
  judge.
- **The seat** judges a worker's output only when the seat's tier is at or above the worker's.
  Higher-tier work gets a judge from the table.
- **Nothing sits above Fable 5.2 at `high`.** For legal work, Carl is the final judge.

### 6.3 Fable off, and failed stages

- **When Fable is off** (`#nofable` or `gkh fable off`), every Fable stage runs on its `noFable`
  tier, and the workflow's structured result lists those stages. For example:
  - judges, plan checks and design judges run on `opus-xhigh`;
  - Fable implementers run on `opus-high`;
  - legal analysis runs on `opus-high`, and the memo header names the model.
- **When a stage fails.** `agent()` returns `null` when an agent dies, for example on a usage
  limit. Each stage's `onFail` then applies:
  - **`rerun:<tier>`** (read-only stages): re-run once on that tier. The result reports the drop.
  - **`unfinished`** (implementers): return the piece unfinished. Never re-run on a half-edited
    tree.
  - **`hold`** (legal analysis and legal judges): stop and report HELD. Carl may re-run with
    Fable off, and the output then says which model did the work.
- **Platform substitution.** Claude Code has paths that answer on another model without failing
  the call: a fallback model setting, an `availableModels` allow-list that falls back to the
  parent model, and a no-fallback guard variable. Stage 0 proves what actually happens. Legal
  runs check which model answered (section 11.5).

### 6.4 Two fixes the current workflows need

- **gk-build's check stage** returns the command's exit code and its last 40 lines verbatim. The
  workflow takes "passed" from the exit code. Before sign-off, the seat re-runs the check command
  itself.
- **gk-review's judge floor comes from code, not from Haiku.**
  - The Workflow hook has git access. It computes the target's changed paths with
    `git diff --name-only` and injects them.
  - The script applies only the project's own path rules: `signOff[].paths`, plus a new
    `uiPaths` list in `gk-project.json`. There are no built-in patterns, and test files never
    count.
  - The Haiku scope pass can only raise the judge, never lower it.

## 7. Policy text

`policy.md` is rewritten to about 120 lines. It keeps the generated-block install into
`~/.claude/CLAUDE.md` and the existing policy tests: under 40,000 characters, the banned box
strings, and `develop` exactly once.

Outline:

1. **What this is** (about 5 lines). The harness, with `routes.json` as the authority.
2. **Routing** (about 30 lines):
   - the generated class table;
   - the inline-first rule;
   - the review size rule;
   - the route line;
   - the dials and the Fable switch;
   - Haiku workers other than Explore get briefs that name their files and stay under a
     100K-token prompt.
3. **When to orchestrate** (about 15 lines):
   - the fan-out list;
   - briefs are self-contained;
   - independent work runs in parallel;
   - fan-out stays at 6 to 12 agents, because of the shared rate bucket;
   - every `agent()` call in a hand-written workflow names its model and effort from the table.
4. **Escalation and judging** (about 15 lines). Section 6 in short.
5. **Briefs and reports** (about 20 lines):
   - workers return about 20 lines;
   - test runs are foreground only;
   - Anthropic's "Finish the whole task" text goes in the seat section for a Fable seat at
     `medium`, and in worker briefs at `low` and `medium`;
   - the "write for a low-context reader" text goes in final reports.

   The exact wording of both texts is in the claude-api skill's `shared/model-migration.md`
   (Fable 5.2 section). The sentence "This codebase is yours" is left out.
6. **Approvals** (about 20 lines):
   - **Needs Carl's word:**
     - the production database or a deploy;
     - a push to `main`, or any force-push;
     - pay-per-token API spend;
     - client or de-identified material into any model;
     - sending anything outside the box to people (email, posts, messages);
     - deleting unpushed work;
     - editing `routes.json` or the policy outside an approved plan.
   - **No approval needed:**
     - tests, builds and reads;
     - commits on a feature branch within a task Carl has started;
     - gk workflows, including every Fable stage the table names;
     - a re-run on a declared `onFail` or `noFable` tier;
     - publishing a Claude Doc to Carl;
     - messaging peer sessions.
   - **The default:** anything on neither list proceeds, subject to the project's own
     instructions.

   Each exception is written inside its rule. The UI sign-off rule keeps its own exclusions
   (internal surfaces, mechanical changes) inside it.
7. **Working beside other sessions** (about 10 lines). Today's four peer rules, shortened.
8. **House rules** (about 5 lines):
   - questions to Carl one at a time;
   - specs, plans and reports are published as a Claude Doc in the same turn.

**Dropped:**

- the mode sections, the lineup prices and the dated history;
- "think less" and token-budget lines;
- the "Fable on Carl's word" clauses;
- the closed inline list and "Everything else starts as a Workflow".

`docs/policy-coverage.md` is rewritten as a map from each old rule to its new home, or to
"dropped, because …".

## 8. Workflows

### 8.1 What changes in every gk workflow

- Each script takes its stage tiers from its generated routes block. No tier literal remains
  elsewhere, and a test checks this.
- Each script applies `args.dials`, `noFable` and `onFail`, and lists every stage that ran off its
  normal tier in its structured result.
- `profile` and `escalate` are accepted for one version. When either is present, it is ignored
  with the log line "profile is retired; routes come from routes.json v<N>". The `whenToUse` and
  `meta` texts, and gk-research's allow-list, change to match.
- The hook marker moves from `gk-harness:args-v1` to `args-v2`. The hook injects:
  - `project`, with `legal` always taken from the file, never from the caller;
  - `scratchpad`;
  - `seat`;
  - `dials`, from the Fable switch;
  - `changedPaths`, for gk-review.

### 8.2 gk-build pieces carry a class

The boolean `hard` is replaced by `class`: `spec`, `judgement`, `hard` or `security`. The caller
sets it, and the plan check confirms it. The class picks the implementer's tier, as in the
section 4.4 table. A piece with no class is treated as `judgement`.

### 8.3 Tests

- **Stage 1** leaves the scripts untouched, so the golden tests (`tests/workflows/golden`) pass
  trivially.
- **Stage 2** retires the golden copies, because they pin per-profile routing by design. They
  are replaced by table-driven tests. Each workflow runs in the fake runtime
  (`tests/workflows/harness.mjs`), and every `agent()` call's model and effort must equal the
  tier `routes.json` names for that stage. This is checked under:
  - each dial and the Fable switch;
  - each conditional (UI, piece class, author tier);
  - each `onFail` path.
- **`gkh gen --check`** runs in CI.
- **The judges test** (section 6.2).
- **Installer.** The agent set comes from `routes.json`. The `WORKERS` tuple in `gkh/cli.py` and
  the `EIGHT` set in `tests/test_policy.py` go.
- **Seat, hook and install tests** are rewritten for the new card and the injected keys.

## 9. Measuring: `gkh report`

`gkh report [--since DATE] [--session ID] [--config-dir DIR] [--json]` reads, after the fact:

- session transcripts;
- subagent transcripts;
- workflow journals.

For every model call it takes the model and the token usage (input, output, cache write, cache
read), and prices them from `routes.json`. It reports by:

- session and day;
- workflow run and stage label;
- agent name;
- Fable's share;
- Haiku calls over the 100K-token prompt.

Cost per class, from route lines, is best effort and is labelled as such.

It adds no hook, and uses the standard library only. On a subscription, "cost" is a proxy for
Carl's share of the usage limits, and the report's header says so. `--config-dir` lets it read
eval runs.

## 10. The eval (draft for Carl's approval)

### 10.1 What it can and cannot tell

- **End-to-end runs (E2E)** compare today with the new design as a whole: the seat, the class
  routes and the inline-first rule. Each task is compared with itself across the two setups.
  Twelve tasks run twice each catch large regressions only.
  - A difference of two runs or fewer counts as noise.
  - Cost within 15% counts as equal.
- **Stage replay** measures one stage's tier at a time. It uses frozen inputs and known answers,
  with at least 20 cases per set (sections 10.4 and 10.5).
- **Stage moves with no instrument** are adopted on the guide's evidence and recorded as
  `"guide"` in `routes.json` `evidence`, as section 4.4 marks them. Carl may reject any of them.
- **The legal set** is a smoke test. It gates nothing (section 10.7).

### 10.2 Isolation

Every eval run happens inside a Docker container on the box. The container has:

- no mounts of `/home/carls/apps`, `~/.claude` or any app directory;
- no published ports;
- a Claude config directory of its own, holding an install of the harness version under test.
  `CLAUDE_CONFIG_DIR` and `GK_CLAUDE_DIR` are set together.

This one step isolates four things:

- the credentials;
- production;
- the live install;
- the other sessions' checkouts.

There is no fallback that touches the live install.

- **Login.** Carl does the login inside the container himself. Claude never handles the
  credentials. Carl is at the box for the first setup, because Docker on this box is part of the
  self-host estate.
- **Sessions** run headless (`claude -p`) with `--strict-mcp-config` and no MCP servers.
- **Dependencies** are installed once per parent commit and reused.
- **Concurrency** is capped at two runs, because evals share the subscription's rate limits with
  Carl's live sessions.
- **Void runs.** A run where any stage fell to its `noFable` or `onFail` tier is void and runs
  again.
- **Spend.** Each eval sitting states its size first (variants × tasks × repeats, and how much
  of it runs on Fable), then waits for Carl's yes. The baseline of today's setup runs on a Fable
  seat at `high` with ultracode on, and will use a lot of Fable allowance.

### 10.3 Coding tasks (E2E): 12 from Goodricke develop

- **Fresh export.** Each task is a fresh export of the parent commit's tree, made with
  `git archive` into a new directory with its own `git init`. It has no shared history, so the
  fix cannot be found in it.
  - The runner removes `.mcp.json` and the project's `.claude/settings*.json` hooks.
  - It keeps `CLAUDE.md` and `gk-project.json` for realism. They are harmless in the container.
- **The task statement** gives the symptom and the public interface the test will call: the file,
  the exported names and the signatures. It does not give the cause or the fix. Carl reads each
  statement for leaks before the set is approved.
- **Hidden test.** The commit's own test is hidden during the run and copied in afterwards. Where
  the commit changed an existing test file, only the commit's new test cases are added, so the
  solver's own tests are not overwritten.
- **Admission.** Three checks before a task joins the set:
  1. the test fails at the parent;
  2. it passes at the commit;
  3. one blind solve on today's setup passes it.

  Any task that fails a check is swapped for an alternate. This makes the set a regression
  detector: it finds where the new design loses tasks that today's setup solves.

| # | Commit | Class | The symptom, in short |
| --- | --- | --- | --- |
| 1 | `bcf791ae2` | known-cause | The encoding sniffer accepts a buffer that ends mid-way through a UTF-8 sequence. |
| 2 | `7d388a85a` | known-cause (floor) | Control characters reach a tracked-change author name. |
| 3 | `0d349557a` | known-cause | An assistant file edit that cannot become the current version is left half-applied. |
| 4 | `6320a76c4` | feature | A playbook-file diff should run against a copy's own base. |
| 5 | `6c579356c` | feature (frontend) | Studio fit should take a registered spec. Dropped if the interface cannot be given without the design. |
| 6 | `f41f171c9` | feature | A second board-paper slug, with an allow-list fallback. |
| 7 | `8b3a56dbf` | feature (unclear bug) | Node crashes on a wall reload when the database client cannot be built. |
| 8 | `3280fcead` | feature (unclear bug) | A de-id refusal on a stopped turn is reported as something else. |
| 9 | `3d79e0430` | ui-build | Alt+Arrow on an immovable tab navigates the browser. |
| 10 | `c45ed44f5` | ui-build | Pressing a change in the Playbook Studio checklist jumps to the top of the report. |
| 11 | `d9fb89355` | hard (security) | Playbook import's parse step does not ask the write rule. |
| 12 | `32a69409d` | hard (race and security) | A saved but unloaded wall does not fail closed, and an older load can win. Dropped if the interface cannot be given without the design. |

The alternates are:

- `62956d309` (de-id);
- `3600f6894` (UI and security);
- `4db13a7eb` (known-cause);
- `e949e9eb9` (security, walls).

### 10.4 Judge replay sets (JUDGE)

For each judging stage (gk-review judge, gk-build per-piece review, gk-build judge), the inputs
are frozen as the stage would receive them:

- a survivors list from one real finder and refuter run over a realistic multi-commit range of
  10 or more changed files, with the tests kept;
- at least 20 cases per set, half with planted true defects and half with planted false findings;
- the true defects are reverted fixes from the commits above, embedded in the range;
- the false findings are plausible but wrong, so the right answer is known.

Only the judge is replayed, once on each candidate tier. The measures are:

- the share of true defects confirmed;
- the share of false findings rejected;
- the cost.

A tier "matches" another if it is within one case on each share.

### 10.5 Plan replay set (PLAN) and search set (SEARCH)

- **PLAN:** 20 plans with known planted flaws (missing step, wrong interface, a piece class set
  too low, a test that asserts nothing), replayed through gk-build's plan check on each tier.
  The measure is how many flaws are caught.
- **SEARCH:** three codebase searches in a Goodricke export with known complete answers, for
  example every caller of a function. Each runs on the new Explore and on today's. The measure
  is missed hits. The new Explore must list the commands and patterns it ran, and say when a
  search was cut short.

### 10.6 Keep or revert

The switch comes before measurement (decision 8). Afterwards:

- **E2E** compares the tagged `pre-routing` harness with the live routes. A loss beyond noise is
  traced to the route that causes it, and that route reverts.
- **A JUDGE or PLAN move that does worse** than today's tier on its replay set reverts to today's
  tier, at the nearest non-`xhigh` equivalent, or `opus-xhigh` where that is today's.
- **A move that matches or beats** today's tier keeps its route. Its `evidence` changes from
  `"guide"` to the record id.

Records are written to `docs/evals/<id>.md`. A revert is a route change, and needs Carl's go like
any other.

### 10.7 Legal smoke set

**Documents.** Carl writes or corrects each answer key.

- **CLLS Certificate of Title, Eighth Edition, 2026 update.** Public, in `Brand/`.
  - Task: extraction narrowed to one part, the form of certificate and Schedule 1 (definitions
    and assumptions). Every assumption and confirmation, with clause references and quotes.
  - The key should take about an hour.
  - The CLLS states no licence. Use is private evaluation only.
- **A synthetic commercial lease.** About 8,000 to 12,000 words.
  - Claude drafts it with planted tenant-unfriendly provisions. Carl plants or amends at least a
    third of the issues himself, so the set doesn't flatter Claude's recall.
  - The review standard is a synthetic tenant playbook. Claude drafts it from standard tenant
    positions, and Carl checks it (decision 9).
- **The Cabinet Office Short Form Contract.** gov.uk, Open Government Licence v3.0, under about
  15,000 words. Task: a supplier-side review. Key: Carl's issues list.

**Regulatory questions.** Hypothetical facts and public sources. Carl writes a short answer to
each.

1. A UK law firm wants to use a cloud AI tool to summarise inbound client emails. What is the
   lawful basis under the UK GDPR and the Data Protection Act 2018 as amended, and is a DPIA
   required? This tests currency: the Data (Use and Access) Act 2025 is being commenced in
   stages.
2. A consumer credit firm uses an AI chat assistant for arrears conversations. Which Consumer
   Duty and CONC provisions apply, and what must it evidence?
3. A UK legal-tech company offers an AI contract-review tool to EU law firms. Is it high-risk
   under the EU AI Act, what does it owe as a provider, and from when? This tests currency
   against staged application dates and any later amendments.

**Running it.** Each item runs once. Carl grades the outputs himself. There is no model grader.

## 11. The legal lab

### 11.1 Place and data gate

`~/legal-lab` is a local git repository with no remote for now. It holds:

- `inputs/public/` and `inputs/synthetic/`, each with a `MANIFEST.json`;
- `playbooks/`;
- `runs/<date>-<slug>/`;
- `tools/`;
- `docs/superpowers/reports/`, so the existing publish gate applies;
- `.claude/gk-project.json`, with `"legal": true` and its guards.

**The gate sits in `convert.py`.** That tool is the first step on both paths, and it runs as code
with file access. It refuses any input that:

- lies outside `~/legal-lab/inputs/<class>/`, where the class is `public` or `synthetic`;
- has no manifest entry. A public entry gives the source URL, the licence and the date fetched.
  A synthetic entry says who generated it and when.
- has a SHA-256 that does not match the manifest.

Later tools accept only converter output, checked by its hash.

The gate checks declaration and provenance, not content. It cannot tell a client document that
someone copied into `inputs/public/` and wrote a false manifest entry for. The spec says so
plainly.

The legal workflows refuse to run unless `args.project.legal` is true. The hook always sets that
value from the file.

**Brief rules.** Every legal brief says:

- English law (England and Wales) unless the frame says otherwise;
- no connectors;
- no WebFetch;
- web searches are built from the issues, never from document text.

### 11.2 Tools (checks done by code)

- **`convert.py`.** Turns a .docx into plain text with paragraph ids (`p0001` …). Its contract:
  - clause numbers are computed from the paragraph styles and `numbering.xml`;
  - table cells come out in reading order;
  - footnotes are inlined with markers;
  - tracked insertions are kept and deletions dropped, with a count reported;
  - a construct it does not handle is refused, never guessed.

  A PDF goes through `pdftotext`. A PDF with no text layer is refused. Goodricke's
  `backend/src/lib/docxClauseNumbers.ts` is either reused through a small Node script or ported.
  Either way, a test shows both give the same clause numbers on the three eval documents.
  Pinpoints are paragraph ids, and clause numbers are shown as computed.
- **`fetch_source.py URL`.** Fetches an allow-listed official source as raw text. Legislation is
  fetched at provision level. It records in the run's source manifest:
  - the URL;
  - the retrieval date;
  - the version status (for legislation.gov.uk, the in-force and "changes not yet applied"
    status from its XML);
  - the SHA-256.

  It is not WebFetch, which passes pages through a small model that can paraphrase a quote.
  - **Allowed hosts:** legislation.gov.uk, ico.org.uk, handbook.fca.org.uk, fca.org.uk,
    eur-lex.europa.eu, gov.uk and assets.publishing.service.gov.uk.
  - **Find Case Law stays off** until Carl holds a Computational Analysis Licence.
  - **Stage 5's first task** proves that a raw fetch returns provision text from the FCA
    Handbook and EUR-Lex. It also records each host's reuse terms and its terms for automated
    access.
- **`quote_check.py`.** Every quote must appear verbatim, after whitespace and quotation marks
  are normalised, in a manifest source whose hash still matches. The quote must also sit at its
  paragraph id or pinpoint. Failures are flagged, never dropped.
- **`cite_check.py`.** For each proposition, it checks that:
  - the source is in the manifest;
  - the hash matches;
  - the quote matches;
  - the pinpoint is in the source;
  - the source's status is current at the as-at date.

  It rejects an EUR-Lex source for a proposition about UK law, unless the proposition is about EU
  law.
- **`render.py`.** Builds the report or memo from the structured findings, so the published text
  is never re-typed by a model. It:
  - stamps the file with its SHA-256 and the check results;
  - puts the DRAFT header first;
  - adds the coverage box (section 11.4);
  - marks an "absent" playbook item as "not verified by code" unless a keyword sweep from the
    playbook found nothing either.

**Tool limits for legal agents.** They run with Write, Edit and WebFetch disallowed, and Bash
limited to the lab's tools. The runtime has per-agent tool limits; Stage 0 proves them. So an
agent cannot plant a source file or edit a checked output.

**Publishing.** The seat publishes from the rendered file, exports the published Doc, and re-runs
`quote_check.py` on the export. Any difference is reported.

### 11.3 `gk-docreview` (multi-document review)

1. **Convert.** `convert.py` on each input, which also applies the gate.
2. **Extract.** Two independent `sonnet-medium` extractors work through every playbook item. For
   each item, each returns:
   - a clause reference;
   - paragraph ids;
   - a verbatim quote;
   - whether the item is present or absent.

   Disagreements are listed.
3. **Quote check.** `quote_check.py`.
4. **Assess.** `opus-high` assesses each finding against the playbook. Findings are returned as
   structured data that cites extract ids.
5. **Challenge.** Two `sonnet-medium` refuters each look for problems:
   - one in definitions and cross-references;
   - the other in carve-outs, provisos and schedules.
6. **Judge.** `opus-high` by default. A `judge` argument may raise it to `fable-high`. The judge
   may make one revision pass in answer to the challenges. Any challenge left standing is appended
   verbatim. `onFail`: `hold`.
7. **Render and check.** `render.py`, then a final `quote_check.py`, which the seat runs itself.

One document that fits one context needs no workflow. The seat (at `opus-medium` or above) runs:

1. `convert.py`;
2. the method;
3. `quote_check.py`;
4. one `worker-opus-high` check;
5. `render.py`.

### 11.4 `gk-regadvice` (regulatory advice)

1. **Frame.** The seat sets out the question, the jurisdiction (England and Wales by default), the
   regimes, the as-at date and the assumed facts.
2. **Source list.** One `opus-medium` agent writes the list of sources to consult from the frame:
   legislation, commencement orders, regulator guidance and EU texts where relevant. Choosing
   sources is where an omission stays invisible, so this step does not run on Haiku.
3. **Fetch.** `haiku-medium` agents fetch only what the list names, with `fetch_source.py`.
4. **Analyse.** One `fable-high` agent. The analysis is one chain of dependent steps, so it does
   not fan out. It may ask for further fetches through `fetch_source.py`. Every proposition
   carries:
   - a source, a pinpoint and a verbatim quote;
   - a confidence grade;
   - what would change the answer.

   `onFail`: `hold`.
5. **Check.** `cite_check.py`. The analyst gets one repair pass. Anything still failing is marked
   UNSUPPORTED and cannot carry a conclusion.
6. **Challenge.** Two `opus-high` agents, each in a fresh context, each able to request fetches:
   - one looks for counter-readings and contrary guidance;
   - one checks currency: commencement, transitional provisions, assimilated law after the
     Retained EU Law (Revocation and Reform) Act 2023, and UK–EU divergence.

   Their objections are appended verbatim. Any objection that leads to a change goes back
   through step 5.
7. **Render and publish.** `render.py`. The memo opens with:
   - the DRAFT header;
   - the models that answered;
   - a coverage box listing the sources consulted, the source classes not consulted (case law,
     paid commentary) and the as-at date.

   It ends with a sources table and a list of questions for a solicitor. Carl is the final judge.

### 11.5 Which model answered

For every legal stage, a checker reads that stage's subagent transcript, confirms which model
answered, and the memo header prints it. If Stage 0 shows the platform can substitute a model
silently, legal-lab sessions set the platform's no-fallback variable.

### 11.6 Plugin skills

The installed legal plugin skills are written mostly around US practice, and some drive outside
connectors. A skill may supply a method only once Stage 5 has read it for US assumptions and
connector use and listed it as permitted. Candidates:

- `commercial-legal:review`;
- `privacy-legal:dpa-review`;
- `corporate-legal:tabular-review`.

## 12. Stages

Each stage ends with:

- the whole test suite passing (Python `unittest` and `node --test`);
- CI green on its matrix;
- a short report published to Carl.

Stages 0 and 1 change nothing about how Carl's live sessions route. Stage 2 is the switch.

### Stage 0: drift fixes and proofs (one sitting, by hand plus one small repo change)

**By hand on the box, recorded in the ledger:**

- `modelSettings.claude-fable-5-2.effortLevel = "high"`. This restores today's intended Fable
  seat. Stage 2's installer adopts the key.

**In the repo:**

- agent descriptions and `policy.md` say Fable 5.2 at $8/$40.

**Proofs, in a scratch session, each recorded with what was observed:**

1. Whether a user agent named `Explore` replaces the built-in one.
2. That `CLAUDE_CODE_SUBAGENT_MODEL=opus` gives an Agent call with no model Opus under a Fable
   seat, and that an agent's own `model` wins.
3. What a Fable-limited `agent()` call returns, and whether the platform can substitute another
   model silently.
4. That per-agent tool limits (disallowed tools, a Bash limit) work in workflow `agent()` calls.
5. How `modelSettings` effort ranks against the top-level `effortLevel`.
6. Whether the desktop app has a per-session ultracode switch.

### Stage 1: one source of truth, no behaviour change

- `routes.json`, holding today's tiers.
- `gkh gen` and `gkh gen --check`, generating the eight agent files (same content as today) and
  the installer's agent list.
- The installer installs `routes.json`.
- The workflow scripts are untouched.
- **Done when** the generated agents match today's and every test passes unchanged.

### Stage 2: the new design, switched live on Carl's go

**Built on a branch:**

- **Report:** `gkh report`. It also reads transcripts from before the switch, so real cost before
  and after is visible from the first day.
- **Policy:** the new `policy.md` and `policy-coverage.md`.
- **Seat:** the seat card, the launchers and the settings ownership.
- **Routes:**
  - routes blocks in the scripts, with the generator owning `meta.phases`;
  - profiles retired, with `profile` and `escalate` ignored for one version;
  - `noFable`, `onFail` and the Fable switch;
  - the injected `seat` and `changedPaths`;
  - gk-review's judge floor and its size rule;
  - gk-build's piece classes and the check fix.
- **Agents:** tier-named agents. The new Explore.
- **Tests:** table-driven tests, replacing the golden copies.

**Then:**

- **Tag for rollback.** Before the switch, the current harness is tagged `pre-routing`.
  Installing that tag restores today's behaviour, and the installer restores the settings it
  recorded.
- **Evidence.** Every new route starts with `evidence: "guide"` until Stage 3 measures it.
- **Switch over, after Carl's go:**
  - `install.sh update` refuses while any peer session is busy (the peer state already exists).
  - The old agent names stay for one version, each mapped to its nearest new tier and marked
    retired in its description.
  - The three memory notes that would contradict the new policy are rewritten at the switch:
    the standing posture, the three-seat policy and the Fable-credit fallback.
- **Done when** a live session on each launcher shows the new card, a dial reaches a workflow's
  log, and Carl has read the new policy.

### Stage 3: measurement, after the switch

- The eval container and runner (section 10.2), with Carl at the box for setup and login.
- Task admission (section 10.3).
- The JUDGE, PLAN and SEARCH sets.
- Carl approves the final eval set, including the task statements.
- After Carl's yes to the size, the tagged `pre-routing` harness and the live routes run side by
  side in the container.
- Keep or revert, as section 10.6 sets out.
- `gkh report` compares real usage before and after the switch.
- **Done when** every route has either an eval record or `"guide"` evidence that Carl has
  accepted.

### Stage 4: tuning (optional)

- Replay `opus-high` against `opus-xhigh` as the `noFable` judge tier.
- Try "low-then-rerun" on gk-build's implement stage.
- Retire the old agent names.

### Stage 5: the legal lab

- `~/legal-lab` with its manifests.
- The tools in section 11.2, and the source-host proof.
- The two workflows, with their stages in `routes.json`.
- The model check.
- The permitted-skills list.
- The synthetic lease and its synthetic tenant playbook.
- **Done when** each legal smoke item has run once and Carl has graded it.

### After Stage 2

Tasks 14 (the macOS laptop) and 15 (the cloud spike) from the October plan run once, against the
new harness.

## 13. Constraints

- **Git.** No agent runs `git checkout`, `switch`, `stash` or `reset` in a shared checkout. Eval
  material is exported, never checked out.
- **The live install** changes only by `install.sh update` at the end of a stage. It never changes
  during an eval, and never while a peer session is busy.
- **The harness stays standard-library Python 3.9+**, and Node 20 for the workflow tests. CI keeps
  its matrix. The legal lab may use Node for the converter.
- **Policy tests.** `policy.md` keeps its test limits.
- **Model ids.** Agent files and workflow scripts use model aliases only.
- **No dev stack.** Nothing here runs `npm run dev`, touches a database or deploys.
- **Commits and pushes.** Commits happen within an approved plan. They end with the house
  co-author line. gk-harness is pushed to `origin` when a stage is complete.

## 14. Left to the builder

- `routes.json` field names and the generator's internals.
- The marker syntax.
- `gkh report`'s layout.
- The container's construction and the runner's mechanics, within section 10.2.
- How the replay sets are recorded and built, within sections 10.4 and 10.5.
- The legal tools' internals, within section 11.2.
- The policy wording, within section 7.
- The two Anthropic texts are copied, not rewritten.

## 15. Questions for Carl

### 15.1 Decided after the review

**(a) Measure first?** Decided 12 November: switch, then measure (decision 8).

**(b) The lease review standard.** Decided 12 November: a synthetic tenant playbook written for
the lab (decision 9).

### 15.2 Later (not needed to build)

- De-identified material in the lab: one rule for every model.
- A paid legal source, once the lab shows the gaps.
- A Find Case Law Computational Analysis Licence (free to apply for; a research licence usually
  runs for a year).
- A remote backup for `~/legal-lab`.
- Trimming the remaining cautions in the memory notes and in Goodricke's `CLAUDE.md`.
- The CLLS's terms, if the eval set is ever shared.

## 16. Review record

**The builder check** (Opus `high`) asked seven questions. **The Fable 5.2 review** (`high`)
gave the verdict "sound with fixes": 3 blockers, 14 major findings and 7 minor ones. It ran the
harness suite (416 Python tests, 68 Node tests, all passing) and confirmed from the Claude Code
2.1.322 binary that an agent's own model overrides `CLAUDE_CODE_SUBAGENT_MODEL`.

Where each point landed:

- **Stage 2 was a route change for Carl's Fable setup, and unmeasured.** The spec now says
  plainly that it is a route change. Carl chose to switch first and measure after (decision 8).
  The switch is recorded as adopted on the guide's evidence, a tagged copy of today's harness is
  the baseline and the rollback, and any move that does worse reverts (10.6, 12).
- **The eval could not measure the stage moves, and 24 runs cannot separate routes.** There are
  now replay sets per stage, a stated noise band and unmeasured moves marked "guide" (10.1 and
  10.4 to 10.6).
- **The eval leaked its answers.** It now uses a fresh export with no history, statements that
  give the interface without the cause, a blind-solve admission check, and adds only the new
  test cases (10.3).
- **The seeded-defect set could not tell judges apart.** There are now frozen survivors in
  realistic ranges, with planted false findings (10.4).
- **Eval isolation was unsafe.** Evals now run in a container, with no fallback (10.2).
- **The Stage 2 cut-over risked live sessions and contradicting memory.** The update now refuses
  while a peer is busy, old names stay for one version, and the memory notes are rewritten at
  the switch (Stage 2).
- **`#nofable` and `onFail` conflicted, and a one-turn dial was too short for a Fable limit.**
  There is now a `noFable` tier per stage, the `gkh fable off` switch, and `opus-xhigh` kept as
  the fallback for judges (4.4, 5.4, 6.3).
- **Silent model substitution.** It is now a Stage 0 proof, plus a model check on legal runs
  (6.3, 11.5).
- **The data gate missed the inline path and was only a string check.** The gate now sits in
  `convert.py`, with a manifest and hashes, and `legal` is taken from the file (11.1).
- **The firm playbook versus decision 4.** Carl chose a synthetic tenant playbook (decision 9).
- **Checks were relayed by models, and agents could plant sources.** There are now hashed
  manifests, tool limits, `render.py`, and a re-check on the export (11.2).
- **The converter was underspecified.** It now has a contract and a clause-number equivalence
  test (11.2).
- **Source selection ran on Haiku.** An Opus agent now writes the source list, the analyst and
  challengers can fetch, legislation is fetched by provision, and the memo has a coverage box
  (11.4).
- **The seat rule was stated two ways.** The full tier order is now used everywhere, and
  legal-doc has a floor of `opus-medium` (4.3, 5.1).
- **The class table, stage table and stage list disagreed.** There is now one table with today,
  new, `noFable` and instrument columns, and gk-build's pieces carry a class (4.4, 8.2).
- **gk-review's path floor had no source and used built-in patterns.** The hook now computes the
  changed paths, and only the project's rules apply (6.4).
- **The policy had no default for unlisted actions.** It now has a default sentence, the commit
  wording is fixed, and the sign-off exclusions sit inside the rule (7).
- **The installer would override Carl's own settings choices.** It now writes each key only
  while the key still holds the installer's value. The seat card notes a non-Opus default, and a
  test checks that the override variables are unset (5.3). I kept the subagent default for every
  session, because the desktop app does not use launchers. *(My call.)*
- **Dials were too heavy.** The new hooks are dropped. The seat recognises a dial only at the
  start or end of the prompt and passes dials to workflows (5.4).
- **Stage 1 built per-profile scaffolding that was thrown away later.** Stage 1 is now agents and
  the installer list only. The scripts change in Stage 2, and the generator owns `meta` (12).
- **Per-class cost relied on route lines, and eval history sat in `routes.json`.** The report now
  groups by session, workflow, stage and agent. Records live in `docs/evals/` (9).
- **Every pre-merge review ran the full fan-out.** There is now a size rule (5.1).
- **The legal set could not compare routes.** It is now called a smoke test and gates nothing.
  Carl plants a third of the lease issues, the CLLS task is narrowed, and `forClient` is removed
  in favour of a `judge` argument (10.7, 11.3).
- **Explore on Haiku was unmeasured, and plugin skills are US-centric.** There is now a SEARCH set
  and a reporting rule for Explore, and a permitted-skills list (10.5, 11.6).
- **The builder's question on eval worktrees carrying Goodricke's guards** is answered by the
  container and the export (10.2, 10.3).
- **The builder's question on Stage 0 by hand or by the installer** is answered: by hand, then
  Stage 2's installer adopts the keys (12).
