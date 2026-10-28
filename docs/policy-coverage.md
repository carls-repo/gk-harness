# Policy coverage: `CLAUDE.md.next` → `policy.md`

Every rule or bullet of `~/.claude/profiles/CLAUDE.md.next` (17,977 characters, 239 lines, the
version of 2026-10-28 with the one-question rule) and where it lives now. Line numbers are the
source file's. A "Home" is a `policy.md` heading, Goodricke's `CLAUDE.md` (with its
`.claude/gk-project.json`, both written in Task 12), or "dropped" with the reason. A row with no
home would be a defect; there are none.

Status words: **kept** = same text; **reworded** = same rule, wording changed as stated;
**moved** = left the global policy for Goodricke; **dropped** = no longer a rule anywhere.

## Which mode am I in? (source lines 1–28)

| Src line(s) | Rule | Home | Status |
|---|---|---|---|
| 1 | Title "Model orchestration policy" | `policy.md` first line (the installer's old-policy detection keys on it) | kept |
| 3–6 | Read the model named in the system prompt; apply that mode; shared rules bind every mode | Which mode am I in? (read first) | kept |
| 10 | Fable 5.1 → Mode A, apex seat, its remit | Which mode am I in? — mode table | kept |
| 11 | Opus 5.5 → Mode B, middle seat, its remit | Which mode am I in? — mode table | kept |
| 12 | Sonnet 5.5 → Mode C, routine seat, its remit | Which mode am I in? — mode table | kept |
| 13 | Any other model → Mode C rules, and say so to Carl in the first reply | Which mode am I in? — mode table (the seat card repeats it for an off-lineup model) | kept |
| 15–17 | Mode chosen at launch by `gk` / `gk-opus` / `gk-sonnet` overlays in `~/.claude/profiles/` that set `GK_PROFILE` for the Workflow hook | Which mode am I in? — "The mode follows the running model…" | reworded: overlays and `GK_PROFILE` are gone (Tasks 3–6); the seat card at start and after `/model` names model, mode and profile; the profile hook injects `profile` and `project` as a safety net; `gk*` are launch shortcuts for `claude --model …` only |
| 17–18 | After a mid-session `/model`, pass `profile` explicitly on every Workflow call (env guard stale) | Which mode am I in? — "**the seat passes `profile` explicitly on every Workflow call**" | reworded: now unconditional (every call, not only after `/model`); "re-read the model and switch mode" after `/model` added (design §4.2 layer 1) |
| 18–19 | Policy approved by Carl 2026-10-13 (Claude Doc); Mode A is the 2026-10-08 standing posture | Which mode am I in? — last sentence of that paragraph | kept |
| 21–26 | Lineup: four models, ids, prices, default efforts | Which mode am I in? — Lineup quote | kept |
| 26–28 | Worker agents use aliases `haiku`/`sonnet`/`opus`/`fable`; never pin dated ids in agents or workflow scripts | Which mode am I in? — Lineup quote; pinned for the agents by `tests/test_policy.py::test_agents_are_the_eight` | kept |

## Shared rules (source lines 30–38)

| Src line(s) | Rule | Home | Status |
|---|---|---|---|
| 32–35 | The seat orchestrates (plans, triages, briefs, judges, integrates); cheapest tier that does it excellently; Agent or Workflow; escalate on failure | Shared rules (all modes) | kept |
| 35–36 | Seat tokens go on judgement, not muscle | Shared rules (all modes) | kept |
| 36–37 | Ultracode is on in every mode (`~/.claude/settings.json`) | Shared rules (all modes) | kept (the Task 8 installer sets `ultracode: true`) |
| 37–38 | Cost control in Modes B/C comes from the tier map, not fewer workflows | Shared rules (all modes) | kept |
| 38 | Every seat runs at `high` | Shared rules (all modes) | kept |

## First decision (source lines 40–53)

| Src line(s) | Rule | Home | Status |
|---|---|---|---|
| 42 | The inline list is closed | First decision on every prompt | kept |
| 44 | Inline 1: conversational reply, opinion, quick question | First decision on every prompt | kept |
| 45 | Inline 2: single-file lookup | First decision on every prompt | kept |
| 46 | Inline 3: one trivial edit | First decision on every prompt | kept |
| 47–49 | Inline 4: the apex judgement under discussion; Mode C only within remit, else ask for a mode switch | First decision on every prompt | kept |
| 51–53 | Everything else is a Workflow; a single Agent is the one-stage fallback; when unsure, Workflow | First decision on every prompt | kept |

## Saved workflows (source lines 55–72)

| Src line(s) | Rule | Home | Status |
|---|---|---|---|
| 57–58 | Five canonical pipelines live in the repo at `.claude/workflows/`, invoked as `Workflow({name, args})` | Saved workflows | reworded: they are installed at user level, `~/.claude/workflows/`, by gk-harness (plan Task 8 deploy table; Task 12 deletes Goodricke's repo copies) |
| 58 | Author a bespoke script only when none fits | Saved workflows | kept |
| 58–60 | Every one takes `profile` (default `fable`); the hook injects the launch profile; pass it explicitly after `/model` | Saved workflows | reworded: "the hook injects the seat's profile as a safety net; the seat passes it explicitly on every call" |
| 60–61 | `escalate: true` = Carl's word to route the apex judge to Fable under a non-Fable profile | Saved workflows | kept |
| 65 | `gk-review` row: use and per-profile stage routing | Saved workflows — table | kept |
| 66 | `gk-diagnose` row | Saved workflows — table | kept |
| 67 | `gk-build` row; judge goes to Fable "when UI or a page root is touched" | Saved workflows — table | reworded: "when UI is touched or a triggered project sign-off check sets `uiJudge`". Goodricke's page-root trigger is its `scroll-audit` sign-off entry with `uiJudge: true` (plan Task 10), so Goodricke routing is unchanged |
| 68 | `gk-research` row | Saved workflows — table | kept |
| 69 | `gk-design` row | Saved workflows — table | kept |
| 71–72 | Workflows return structured results; the seat writes the synthesis; no seat-synthesis stage | Saved workflows | kept |
| — | (new) `.claude/gk-project.json`: fields, the hook injects it as `args.project`, `Project: none` recovery, gk-build's `signOff` result | Project extension file | added (brief Task 7; schema from plan Task 10) |

## Hard rules for every workflow stage (source lines 74–90)

| Src line(s) | Rule | Home | Status |
|---|---|---|---|
| 76–78 | Every `agent()` names `model` and `effort`; omitted model inherits the seat | Hard rules for every workflow stage | kept |
| 79–81 | Judge ≥ judged: Opus-xhigh judges Sonnet/Haiku; Fable judges Opus-xhigh, UI/UX, security, client-facing in Mode A | Hard rules for every workflow stage | kept |
| 81–83 | Modes B/C: Opus-xhigh output judged by a second, independent Opus-xhigh pass | Hard rules for every workflow stage | kept |
| 83–87 | Hard boundary: security-sensitive code, client-facing wording/surfaces, architecture escalate the judge to Fable on Carl's word only; name the stage, say why, wait; never on own initiative; record an Opus judge if Carl declines | Hard rules for every workflow stage | reworded: the security examples "(auth, ethical walls, PII, the de-id gateway)" became "(auth, access control, secrets, PII)". Ethical walls are access control and the de-id gateway is PII, so Goodricke's cases stay inside the rule; the named subsystems live in Goodricke's `security-and-invariants` review finder (`.claude/gk-project.json`) |
| 88–90 | Fable stages die under usage limits; a Fable stage is last; on death read the journal, re-run that stage on `worker-opus-xhigh`, say so in the synthesis | Hard rules for every workflow stage | kept |

## UI/UX sign-off rule (source lines 92–101)

| Src line(s) | Rule | Home | Status |
|---|---|---|---|
| 94 | Fable signs off anything that reaches a user's screen, as a rule | UI/UX sign-off rule (all modes) | kept |
| 95–96 | Triggers: new or restyled page; new component on a client-facing route; client-facing wording | UI/UX sign-off rule (all modes) | kept |
| 96–99 | Not triggered: the Modes B/C build itself; internal-only surfaces; mechanical UI changes | UI/UX sign-off rule (all modes) | kept |
| 99 | Not-triggered changes are judged on the diff plus the scroll audit | UI/UX sign-off rule (all modes) | reworded: "the diff plus the project's sign-off checks (`args.project.signOff`)". Goodricke's scroll audit is one of those checks (its `scroll-audit` entry) and stays mandatory in Goodricke `CLAUDE.md` "Page-scroll sign-off (MANDATORY)" |
| 99–100 | The scroll audit runs first (Haiku run-and-report); its verdict goes into the sign-off brief | UI/UX sign-off rule (all modes) | reworded: "any audit runs first (Haiku run-and-report) and its verdict goes into the sign-off brief" |
| 100–101 | The sign-off is the last stage; the die-and-re-run rule applies | UI/UX sign-off rule (all modes) | kept |

## Worker ladder and routing (source lines 103–139)

| Src line(s) | Rule | Home | Status |
|---|---|---|---|
| 105–107 | The ladder, cheapest first; escalation-only tiers; take the cheaper tier first | Worker ladder and routing | kept |
| 111 | Row: conversation / lookup / trivial edit / apex judgement → inline (Mode C within remit) | Worker ladder and routing — table | kept |
| 112 | Row: run tests / builds / scroll audit and report, renames, formatting… → `worker-haiku-low` | Worker ladder and routing — table | reworded: "scroll audit" → "audits" |
| 113 | Row: changelog entries, doc touch-ups, bounded recon, mechanical chores, reader/classifier/dedup/cheap-refuter stages → `worker-haiku` | Worker ladder and routing — table | kept (generic changelog writing; Goodricke's `CHANGELOG.md` rule is the moved row at 158) |
| 114 | Row: codebase search with no file list → `Explore` | Worker ladder and routing — table | kept |
| 115 | Row: known-cause fixes, tests, CRUD, bounded refactors, finders/refuters → `worker-sonnet` (Mode C: Haiku where bounded) | Worker ladder and routing — table | kept |
| 116 | Row: feature implementation, multi-file changes, per mode | Worker ladder and routing — table | kept |
| 117 | Row: unclear-cause debugging, code review, intent refactors → `worker-opus` | Worker ladder and routing — table | kept |
| 118 | Row: verify / adjudicate / judge over Sonnet/Haiku; complex builds → `worker-opus-xhigh` | Worker ladder and routing — table | kept |
| 119 | Row: plan check, non-UI design proposals, per mode | Worker ladder and routing — table | kept |
| 120 | Row: judge over Opus-xhigh output, per mode | Worker ladder and routing — table | kept |
| 121 | Row: architecture, security-sensitive code, per mode (Mode C: stop, ask for a switch) | Worker ladder and routing — table | kept |
| 122 | Row: UI/UX build, per mode | Worker ladder and routing — table | kept |
| 123 | Row: UI/UX sign-off → `worker-fable` in every mode | Worker ladder and routing — table | kept |
| 124 | Row: escalation after an xhigh failure, per mode; report verbatim | Worker ladder and routing — table | kept |
| 126–131 | Haiku's 100K rule: named files, no sweeps; escalate to Sonnet, not higher effort; when high effort pays | Worker ladder and routing — "Haiku's 100K rule" | kept |
| 133–134 | Opus 5.5 effort: `worker-opus` pinned at high; xhigh hard tail; max on escalation | Worker ladder and routing — "Opus 5.5 effort" | kept |
| 136–137 | At most min(16, CPUs−2) agents per workflow, "6 on this 8-CPU box" | Worker ladder and routing — "Fan-out sizing" | reworded: "min(16, CPUs−2) agents per workflow on this machine (6 on an 8-CPU machine)" |
| 137–138 | Every agent draws on one shared API rate bucket (a 429 killed two agents in September) | Worker ladder and routing — "Fan-out sizing" | kept |
| 138–139 | A fan-out stage targets 6–12 agents with `pipeline()`; larger sweeps split across sequential workflows | Worker ladder and routing — "Fan-out sizing" | kept |

## Orchestration rules (source lines 141–177)

| Src line(s) | Rule | Home | Status |
|---|---|---|---|
| 143–145 | Fresh context; every brief self-contained (paths, findings, constraints, done, return) | Orchestration rules | kept |
| 146–148 | Dispatch independent subtasks in parallel; `parallel()` barrier only when needed | Orchestration rules | kept |
| 149–151 | Quality gate before integrating; re-dispatch or escalate one tier, never silently accept | Orchestration rules | kept |
| 152–153 | Workers do not expand scope; the seat owns plan, final verification, summary | Orchestration rules | kept |
| 154–155 | A failed attempt's report goes verbatim into the escalation brief | Orchestration rules | kept |
| 156 | Briefs that touch a database say "dev only" | Goodricke `CLAUDE.md` "Other standing rules" (Task 12 adds the general dev-only brief line beside today's schema-comparison one) and its `gk-project.json` `guards` (supabase-dev only) | moved |
| 156–157 | Prod is reached only through the hosted Supabase MCP with the explicit project id, by the seat, on Carl's word | Goodricke `CLAUDE.md` "Other standing rules" ("PROD database access is ONLY via the hosted Supabase MCP plugin with the explicit project id"; Task 12 confirms "by the seat, on Carl's word") | moved |
| 158 | Build briefs for app changes include the `CHANGELOG.md` entry | Goodricke `CLAUDE.md` "Other standing rules" (CHANGELOG bullet: "build briefs for app changes must include the changelog entry") and its `gk-project.json` `signOff` entry `changelog` (`sharedFile` `CHANGELOG.md`) | moved |
| 158–159 | A page-root change triggers the scroll audit before sign-off | Goodricke `CLAUDE.md` "Page-scroll sign-off (MANDATORY)" and its `gk-project.json` `signOff` entry `scroll-audit` (`uiJudge: true`) | moved |
| 160–161 | Brief hygiene: bounded ~20-line report, no file dumps or diffs | Orchestration rules — "Brief hygiene" | kept |
| 161–163 | Test/build output through `tail` or a dot reporter; test runs FOREGROUND only | Orchestration rules — "Brief hygiene" | kept |
| 163–164 | Recon goes to `Explore` or a Haiku reader with a file list | Orchestration rules — "Brief hygiene" | kept |
| 164–165 | No plugin installs or CLAUDE.md/memory edits mid-session; `/clear` between tasks, `/compact` at breaks | Orchestration rules — "Brief hygiene" | kept |
| 166–168 | Specs are read from a phone: put in front of Carl in the turn written; Claude Doc for Markdown, `SendUserFile` render for HTML; never a bare download or path | Orchestration rules — "Specs are read from a phone" | kept |
| 168–169 | The Stop gate (`gk-remote-stop.py`) holds the turn until done | Orchestration rules — "Specs are read from a phone" | kept |
| 169–171 | The ledger `docs/superpowers/REMOTE-LINKS.tsv` (repo-relative) names the doc to revise; update, not a second doc | Orchestration rules — "Specs are read from a phone" | kept |
| 171 | Carl's "don't publish" → `gk-remote-skip.py` | Orchestration rules — "Specs are read from a phone" | reworded: run the skip command the Stop hook prints, with the Bash tool, exactly as printed (`<python> ~/.claude/hooks/gk-harness/gk-remote-skip.py <session> <path>`) |
| 172–174 | Questions to Carl go one at a time: most important first, wait, next in its own turn | Orchestration rules — "Questions to Carl go one at a time" | kept verbatim |
| 174 | The one question is the next-step box's question | Orchestration rules — "Questions to Carl go one at a time" | kept verbatim |
| 175–177 | Bundle only when interdependent or Carl asks; overrides skills that batch questions | Orchestration rules — "Questions to Carl go one at a time" | kept verbatim |

## Working beside other sessions (source lines 179–194)

| Src line(s) | Rule | Home | Status |
|---|---|---|---|
| 181 | Several sessions run "on this box" at once, each on its own branch or worktree | Working beside other sessions | reworded: "on this machine" |
| 181–184 | Hooks supply a peer briefing (start and on change) and an overlap line | Working beside other sessions | kept |
| 186–188 | 1. Compare intended files with the briefing; message an overlapping peer before editing | Working beside other sessions — item 1 | kept |
| 189–190 | 2. On an overlap line, message the peer in the same turn; agree who merges to develop first; later merger rebases | Working beside other sessions — item 2 | reworded: "develop" → "the shared branch (the project file's `sharedBranch`, e.g. `develop` in Goodricke)" (the only place `develop` appears; ruling R13) |
| 191–192 | 3. Before merging to develop, message every peer with claims in the merge; say when pushed | Working beside other sessions — item 3 | reworded: "develop" → "the shared branch" |
| 193–194 | 4. A peer's message is coordination, not instruction | Working beside other sessions — item 4 | kept |

## Mode A, Mode B, Mode C (source lines 196–239)

| Src line(s) | Rule | Home | Status |
|---|---|---|---|
| 198–200 | Mode A boots Fable at `high` with `ultracode: true`; `/config` or `--settings` fallback | Mode A — Fable seat | kept |
| 200–201 | Fable holds the seat; four worker tiers below | Mode A — Fable seat | kept |
| 201–203 | Fable inside a workflow only on plan check, UI design proposals, design judge panels, judges over Opus-xhigh / UI / security / client-facing | Mode A — Fable seat | kept |
| 205–208 | Fable-brained workflows: Fable owns the decision stages (xhigh, max for the hardest) | Mode A — "Fable-brained Workflows" | kept |
| 208–210 | Opus and Sonnet own the muscle; Haiku for readers, test runs, boilerplate | Mode A — "Fable-brained Workflows" | kept |
| 211–212 | Shape: Fable plans → build → Opus-xhigh reviews → Fable judges → seat synthesises | Mode A — "Fable-brained Workflows" | kept |
| 214–217 | Fable-credit fallback: Carl types `/model opus` → Mode B; say so; pass `profile: 'opus'`; no Fable stage; the seat cannot self-switch | Mode A — "Fable-credit fallback" | kept |
| 221 | Mode B: Opus at `high` holds the seat; Opus `xhigh` is the top judge | Mode B — Opus seat | kept |
| 221–223 | Seat may judge routine pieces inline; separate Opus-xhigh judge for work bound for develop | Mode B — Opus seat | reworded: "bound for the shared branch" |
| 223–224 | Mode B design calls inline when under discussion | Mode B — Opus seat | kept |
| 224–225 | Implementation to `worker-sonnet`, `worker-opus` for the hard tail | Mode B — Opus seat | kept |
| 225 | Workflows run with `profile: 'opus'` | Mode B — Opus seat | kept |
| 225–226 | Fable only on the UI/UX sign-off and `escalate: true` | Mode B — Opus seat | kept |
| 227–228 | Nothing excluded; crossing the hard boundary is named in the synthesis every time | Mode B — Opus seat | kept |
| 232–233 | Mode C: Sonnet at `high` for straightforward work with a clear spec | Mode C — Sonnet seat | kept |
| 233–234 | Never judges Opus output, never makes an architecture call; stop for Carl to switch | Mode C — Sonnet seat | kept |
| 234–236 | Haiku swarm for simple tasks, 100K rule always | Mode C — Sonnet seat | kept |
| 236–238 | `worker-sonnet` for known-cause/bounded work; `worker-opus-xhigh` for every verify/adjudicate/judge and the plan check | Mode C — Sonnet seat | kept |
| 238 | Workflows run with `profile: 'sonnet'` | Mode C — Sonnet seat | kept |
| 238–239 | Fable only on the UI/UX sign-off and `escalate: true` | Mode C — Sonnet seat | kept |

## Agents

The eight `agents/worker-*.md` files are byte-for-byte copies of `~/.claude/agents/worker-*.md`
as of 2026-10-28. `ooxml-docx-integrity-reviewer.md` is not one of them: the design (§4.4)
removes the user-level copy and keeps Goodricke's project copy.
