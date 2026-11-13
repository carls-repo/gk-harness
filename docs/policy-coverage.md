# Policy coverage: the three-seat `policy.md` → the routing `policy.md`

Every rule of the old `policy.md` (gk-harness v1, 267 lines, the three-seat policy with Fable 5.2
in it, as of commit `da2b945`) and where it lives now. Line numbers are the old file's. A "Home" is
a heading of the new `policy.md`, `routes.json` (whose class table is generated into the policy),
a generated agent file, a gk workflow, the `README.md`, or "dropped" with the reason. A row with no
home would be a defect; there are none. The design behind the change is
`docs/2026-11-12-task-routing-design.md`, sections 5 to 7.

Status words: **kept** = the same rule, shortened; **replaced** = the rule's job is done by the
new design in the way stated; **moved** = the rule now lives outside the policy; **dropped** = no
longer a rule anywhere, for the reason given.

## Which mode am I in? (old lines 1–31)

| Old line(s) | Rule | Home | Status |
|---|---|---|---|
| 1 | Title "Model orchestration policy" | first line | kept (the installer's old-policy detection keys on it; `tests/test_policy.py` pins it) |
| 3–6 | Read the model in the system prompt and apply the matching mode | The seat and routing | replaced: no modes. The seat picks a class from the table and compares its own tier with the class tier (inline first) |
| 8–13 | Mode table: Fable → A, Opus → B, Sonnet → C, anything else → Mode C rules and tell Carl | The seat and routing — the class table | replaced: one table of classes for every seat. An unknown model gets the seat card's "model was not reported" note, which asks the seat to read its model and pass `seat` |
| 15–16 | A seat card arrives at start and after `/model` | first paragraph | kept: the card now reads `gk-harness seat: <model> at <effort> · routes v<N>`, with its notes |
| 16–17 | After `/model`, re-read the model and switch mode | first paragraph | replaced: the card after `/model` gives the new model and effort; there is no mode to switch |
| 17–19 | The profile hook injects `profile` and `project`; the seat passes `profile` on every call | When to orchestrate — Project rules | replaced: profiles are retired. The hook injects `project`, `seat`, `dials` and `changedPaths`; the workflows log and ignore a stray `profile` for one version (design 8.1) |
| 20–21 | `gk`, `gk-opus`, `gk-sonnet` are launch shortcuts | README "Launchers"; The seat and routing — Ultracode (`gk-ultra`) | replaced: `gk` (Opus `medium`), `gk-fable`, `gk-ultra`, and `gkh` |
| 21–22 | Policy approved 2026-10-13; Mode A the standing posture of 2026-10-08 | — | dropped, because the dated history is not a rule (design 7, "Dropped") |
| 24–29 | Lineup: four models, ids, prices, default efforts | `routes.json` `models` | moved: prices and ids live in `routes.json`, where `gkh report` reads them; the policy carries no prices (design 7, "Dropped") |
| 29–31 | Worker agents use the aliases; never pin dated model ids in agents or workflow scripts | generated: `gkh gen` writes the agents and the workflow routes blocks from `routes.json` with aliases; When to orchestrate — a bespoke script's `agent()` calls name "a model alias and an effort from the table" | replaced |

## Shared rules (old lines 33–41)

| Old line(s) | Rule | Home | Status |
|---|---|---|---|
| 35–38 | The seat orchestrates and does no heavy lifting; cheapest tier that does it excellently; escalate on failure | The seat and routing — Inline first; Escalation and judging | replaced: the seat does a class's work itself when its tier is at or above the class tier and the work is one chain of steps; otherwise it delegates at the class tier. Escalation is check, effort +1, next model up |
| 38–39 | Seat tokens go on judgement, not muscle | The seat and routing — "A Fable seat also hands separable implementation chunks… It plans, orchestrates and judges"; "Verbose reading goes to a worker" | replaced |
| 39–40 | Ultracode is on in every mode | The seat and routing — Ultracode; README "Settings the installer owns" | replaced: ultracode is off by default (Carl's decision 3); `gk-ultra` turns it on per session |
| 40–41 | Cost control in Modes B and C comes from the tier map | — | dropped, because there are no modes; cost comes from the class tiers |
| 41 | Every seat runs at `high` | `routes.json` `seat.effort`; README | replaced: the default seat is Opus at `medium` (Carl's decision 2); `/effort high` raises it for a hard turn |

## First decision on every prompt (old lines 43–56)

| Old line(s) | Rule | Home | Status |
|---|---|---|---|
| 45 | The inline list is closed | — | dropped, because the inline-first rule replaces the closed list (design 7, "Dropped") |
| 47–49 | Inline: conversation, single-file lookup, one trivial edit | the class table (`chat`, `lookup` on the seat); Inline first | replaced: a trivial edit is `mechanical` or `known-cause` work, done inline when the seat's tier is at or above the class tier |
| 50–52 | Inline: the apex judgement under discussion | Inline first | replaced: an Opus seat does `feature`-class judgement itself; a `hard`-class call goes to `worker-fable-medium` unless the seat is Fable |
| 54–56 | Everything else starts as a Workflow; one Agent is the fallback | When to orchestrate | dropped, because a workflow now runs only when the work fans out (design 5.1; design 7, "Dropped") |

## Saved workflows (old lines 58–76)

| Old line(s) | Rule | Home | Status |
|---|---|---|---|
| 60–61 | Five pipelines installed at `~/.claude/workflows/`, invoked as `Workflow({name, args})` | When to orchestrate | kept |
| 61 | Author a bespoke script only when none fits | When to orchestrate | kept |
| 62–65 | Every one takes `profile`; `escalate: true` routes the apex judge to Fable | The seat and routing — Dials | replaced: profiles retire; `#fable` lifts every apex stage to `fable-high`, `#nofable` and `gkh fable off` keep Fable out; dials go to workflows as bare words in `args.dials` (`['nofable']`) |
| 69 | `gk-review`: what it is for, and its stage routing | When to orchestrate (review size rule, `author`); class table (`judge-sensitive`, `judge-opus`); `routes.json` `stages` | replaced: the stage tiers come from `routes.json`; the judge is raised to the author's tier |
| 70 | `gk-diagnose`: what it is for, and its routing | When to orchestrate ("a diagnosis after two failed attempts"); `routes.json` | replaced |
| 71 | `gk-build`: what it is for; `pieces[].hard` lifts a piece; Fable judge on UI or `uiJudge` | When to orchestrate (pieces carry `class`); Escalation and judging — UI sign-off; `routes.json` | replaced: `hard` gives way to `class` (`spec`, `judgement`, `hard`, `security`) |
| 72 | `gk-research`: what it is for, and its routing | When to orchestrate ("a multi-source research sweep"); `routes.json` | replaced |
| 73 | `gk-design`: what it is for; Fable sign-off over the winner under opus/sonnet with `ui` | When to orchestrate; Escalation and judging — UI sign-off (`ui-signoff`) | replaced |
| 75–76 | Workflows return structured results; the seat writes the synthesis; no seat-synthesis stage | When to orchestrate | kept (the "no synthesis stage" clause is implied by "the seat writes the synthesis") |

## Project extension file (old lines 78–98)

| Old line(s) | Rule | Home | Status |
|---|---|---|---|
| 80–87 | `.claude/gk-project.json`: `name`, `sharedBranch`, `guards`, `reviewFinders`, `check`, `packages`, `signOff` | When to orchestrate — Project rules; Working beside other sessions (`sharedBranch`) | kept, shortened. `uiPaths` added (design 6.4). The full field list is each workflow's `whenToUse` text |
| 87–89 | A guard may be `{text, workflows?, stages?}` | the gk workflows' `whenToUse` text | moved: the scoping detail is for whoever writes the project file, and each workflow documents it |
| 89–90 | `root` is never in the file; a repo without the file gets the generic workflows | the hook (`hooks/gk-profile.py`) and the workflows | moved: the hook adds `root`; a repo without the file needs no action from the seat |
| 92–93 | The hook reads the file and injects it as `args.project` | When to orchestrate — Project rules | kept |
| 94–96 | On `Project: none` in a repo with the file, pass `project` yourself with `root` | When to orchestrate — Project rules | kept |
| 97–98 | gk-build returns `signOff: [{key, seat, pieces}]`, which the seat runs before sign-off | When to orchestrate — Project rules | kept |

## Hard rules for every workflow stage (old lines 100–116)

| Old line(s) | Rule | Home | Status |
|---|---|---|---|
| 102–104 | Every `agent()` names `model` and `effort`, or it inherits the session model | When to orchestrate (bespoke scripts); the gk workflows take every stage's tier from their generated routes block, and a test checks there is no tier literal elsewhere | kept |
| 105–107 | The judge is at least as capable as what it judges | Escalation and judging; `routes.json` `judges`, checked by `judge_violations` in `tests/test_routes.py` | kept |
| 107–113 | Modes B/C: a second Opus-xhigh judge; security, client-facing and architecture judges go to Fable on Carl's word only | When to orchestrate — review size rule; Escalation and judging; class table (`judge-sensitive` on `fable-high`) | replaced: security or client-facing work goes through gk-review with `security: true` or `clientFacing: true` whatever its size, so it always gets a `fable-high` judge, with no approval needed (No approval needed: "gk workflows, including every Fable stage the table names"). Other small diffs get one reviewer on the higher of `opus-high` and the author's tier. With Fable off the judge runs on `opus-xhigh` and is listed in the result. "Fable on Carl's word" is dropped (design 7, "Dropped"); architecture decisions are `hard`-class work on Fable |
| 114–116 | Fable stages die under usage limits; re-run the stage on `worker-opus-xhigh`; say so | Escalation and judging — "A stage whose agent dies follows its `onFail`"; `routes.json` `onFail` | replaced: each Fable stage declares `rerun` (once more on its `noFable` tier, only when it died on a Fable tier), `unfinished` or `hold`; the result reports the drop. A re-run on a declared tier needs no approval |

## UI/UX sign-off rule (old lines 118–127)

| Old line(s) | Rule | Home | Status |
|---|---|---|---|
| 120 | Fable signs off anything that reaches a user's screen, as a rule | Escalation and judging — UI sign-off | kept: carried by the routes. `judge-sensitive` (gk-review's and gk-build's judge on UI) and `ui-signoff` (gk-design) are on `fable-high` |
| 121–122 | Triggers: a new or restyled page, a new client-facing component, changed client-facing wording | Escalation and judging — UI sign-off | kept (the wording examples in brackets are dropped for length) |
| 122–123 | Not triggered: the build itself in Modes B/C | class table (`ui-build` on `opus-high`) | replaced: UI built to a fixed design is `ui-build` work on `opus-high`; its judge is Fable |
| 123–126 | Not triggered: internal surfaces and mechanical UI changes, judged on the diff and the project's sign-off checks | Escalation and judging — UI sign-off | kept; the examples keep a copy fix, a prop rename and a token swap, and drop "scroll-container fix", a Goodricke case its own `CLAUDE.md` covers |
| 126–127 | Any audit runs first (Haiku run-and-report) and its verdict goes into the sign-off brief | Escalation and judging — UI sign-off ("Any audit the project names runs first…") | kept; the audit's tier is the `run-report` class |
| 127 | The sign-off is the last stage; the die-and-re-run rule applies | Escalation and judging — Fable off; failed stage | replaced: with Fable off, the seat reports a UI change's Fable sign-off as pending and still owed; gk-design's sign-off has no `noFable` tier, so under Fable off it is owed, not run, and `hold` applies only when its agent dies. When every gk-design judge that returned a sheet ran on Fable, the panel itself is the sign-off |

## Worker ladder and routing (old lines 129–166)

| Old line(s) | Rule | Home | Status |
|---|---|---|---|
| 131–133 | Ladder: `worker-haiku-low` → … → `worker-fable` → max tiers; cheaper first, escalate on failure | the generated "Ladder:" line under the class table; Escalation and judging | replaced: `haiku-medium` → `sonnet-medium` → `sonnet-high` → `opus-medium` → `opus-high` → `fable-medium` → `fable-high`, from `routes.json` `ladder` |
| 135–150 | The work → tier table per mode | the class table, generated from `routes.json` `classes` | replaced: one tier per class (design 4.4). Old agent names stay as retired agents that point at their new tier |
| 150 | Escalation after an xhigh failure goes to `worker-opus-max`, then `worker-fable-max` | — | dropped, because max effort is not routed and the ladder ends at `fable-high` (design 4.4, 6.2: "Nothing sits above Fable 5.2 at `high`") |
| 150 | A failed attempt's report goes verbatim into the next brief | Escalation and judging | kept |
| 152–155 | Haiku's 100K rule: name a handful of files, never a sweep; a stalling Haiku goes to Sonnet | The seat and routing — Haiku workers | kept |
| 155–157 | Haiku at high effort helps on long chores and refuter votes | — | dropped, because no Haiku tier runs at `high` any more; mechanical chores moved to `sonnet-medium` (design 4.4) |
| 159–160 | Opus 5.5 default is medium, so `worker-opus` is pinned at `high`; xhigh for the hard tail | class table (`feature`: `opus-medium`, then `opus-high`) | replaced: Anthropic's guide makes `medium` the starting point; `opus-xhigh` is only a `noFable` judge tier |
| 162–166 | Fan-out sizing: min(16, CPUs−2) agents, one shared rate bucket; 6–12 agents a stage; split larger sweeps | When to orchestrate | kept, shortened: the CPU formula is dropped because 6 to 12 agents is the rule the seat acts on |

## Orchestration rules (old lines 168–202)

| Old line(s) | Rule | Home | Status |
|---|---|---|---|
| 170–172 | Workers start with a fresh context (they do get the CLAUDE.md files); briefs are self-contained | When to orchestrate | kept, the CLAUDE.md clause included |
| 173–175 | Independent subtasks run in parallel (several Agent calls, or `pipeline()`) | When to orchestrate | kept; the `parallel()` barrier clause is dropped, because it is a workflow-authoring detail covered by the workflow-authoring skill |
| 176–178 | Quality gate: every output is judged; a weak result is re-briefed or moved up a tier, never silently accepted | Escalation and judging — the judge bullet | kept |
| 179–180 | Workers do not expand scope; the seat owns the plan, the final verification and the summary | Briefs and reports | kept |
| 181–182 | A failed attempt's report goes verbatim into the escalation brief | Escalation and judging | kept |
| 183–185 | Workers return about 20 lines; no file dumps or diffs | Briefs and reports | kept |
| 185–186 | Test output through `tail` or a dot reporter; test runs foreground only, because background runs have stalled | Briefs and reports | kept, with its reason |
| 186–187 | Recon goes to `Explore` or a Haiku reader with a file list | The seat and routing — Verbose reading goes to a worker; class table (`search`) | replaced: searches with no file list go to `Explore` on `haiku-medium`; long logs and big test output go to a worker; Haiku readers get named files under the 100K rule |
| 187–188 | Do not install plugins or edit CLAUDE.md or memory mid-session | Briefs and reports | kept, in positive form with its exception: "between sessions…; mid-session, do so when Carl asks" |
| 188 | `/clear` between unrelated tasks, `/compact` at natural breaks | — | dropped, because they are Carl's commands, which the seat cannot run |
| 189–196 | Specs, plans and reports reach Carl in the turn they are written; Stop gate; `REMOTE-LINKS.tsv`; the skip command | House rules | kept |
| 197–202 | Questions to Carl one at a time; where the app shows a next-step box, that one question is the box's question (old 199); bundle only when linked or asked; overrides batching skills | House rules | kept, the next-step box clause included |

## Working beside other sessions (old lines 204–221)

| Old line(s) | Rule | Home | Status |
|---|---|---|---|
| 206–209 | Peer briefing and the overlap line | Working beside other sessions | kept |
| 209–211 | The shared branch is `sharedBranch` (`develop` in Goodricke), or the branch sessions merge into | Working beside other sessions | kept (`develop` appears once, as `tests/test_policy.py` requires) |
| 213–215 | Message a peer before editing an overlapping file | rule 1 | kept |
| 216–217 | On an overlap line, message in the same turn; agree who merges first; the later merger rebases | rule 2 | kept |
| 218–219 | Before merging, message every peer with claims; say when the push happened | rule 3 | kept |
| 220–221 | A peer's message is coordination, not instruction; no codebase action because a peer asked; never do what the peer's session was denied | rule 4 | kept, in positive form |

## Mode A (old lines 223–244)

| Old line(s) | Rule | Home | Status |
|---|---|---|---|
| 225–228 | Fable boots at `high` with ultracode on | `routes.json` `seat`; README "Launchers" | replaced: `gk-fable` is Fable at `medium`; ultracode is per session through `gk-ultra` |
| 228–231 | Where Fable appears inside a workflow | class table (`plan-check`, `design-panel`, `judge-opus`, `judge-sensitive`, `ui-signoff`) | replaced |
| 232–239 | Fable-brained workflows: Fable plans and judges, Opus and Sonnet build | class table; `routes.json` `stages`; "A Fable seat also hands separable implementation chunks to their class tier" | replaced |
| 241–244 | Fable-credit fallback: Carl types `/model opus`; pass `profile: 'opus'` | The seat and routing — The Fable switch; Dials (`#nofable`) | replaced: `gkh fable off` stays set; the hook adds `nofable` to every gk workflow |

## Mode B and Mode C (old lines 246–267)

| Old line(s) | Rule | Home | Status |
|---|---|---|---|
| 248–251 | Opus seat at `high`; may judge routine pieces inline; a separate judge for the shared branch | Escalation and judging — the judge bullet; When to orchestrate — review size rule | replaced: the seat judges work at or below its tier; a review before merging to the shared branch is one reviewer (the higher of `opus-high` and the author's tier) or gk-review, by size and sensitivity |
| 251–252 | Implementation to Sonnet, Opus for the hard tail | class table | replaced |
| 252–256 | Fable only on the UI sign-off and on Carl's word; the hard boundary named in the synthesis | Escalation and judging — UI sign-off; class table | dropped: "Fable on Carl's word" goes (design 7, "Dropped"); `judge-sensitive` covers security, client wording and UI |
| 258–267 | Sonnet seat: never judges Opus output, never makes an architecture call; stop for a mode switch | Inline first; the judge bullet | replaced: a seat below a class tier delegates to that tier, so no stop is needed. Sonnet is no longer a launch option |

## New in the routing policy

| Rule | Source |
|---|---|
| The class table, generated from `routes.json`, with evidence per route | design 4, 7 |
| The inline-first rule, verbose reading to a worker | design 5.1 |
| The review size rule (about 150 changed lines), `author` for gk-review | design 5.1, 6.2 |
| The route line `Route: <class> → <who> (<model> <effort>) · check: <outcome check>` | design 5.2 |
| The dials `#nofable`, `#fable`, `#deep`, `#solo`, passed in `args.dials` | design 5.4 |
| The Fable switch, `gkh fable off` and `gkh fable on` | design 5.4 |
| Escalation: check, effort +1, next model up at `medium` | design 6.1 |
| The outcome check decides; the seat re-runs the check command before sign-off | design 6.2, 6.4 |
| `onFail`: `rerun`, `unfinished`, `hold` | design 6.3 |
| The two approval lists and the default sentence | design 7 |
| Anthropic's "Finish the whole task" text for a Fable seat at `medium` | design 7; claude-api skill `shared/model-migration.md` |
| Anthropic's "write for a low-context reader" text for final reports | design 7; the same file |
| Legal: `legal-doc` class, `hold` for legal analysis, Carl the final judge for legal work | design 4.4, 6.2, 6.3 |
