# Route by task, Plan 1 (Stages 0 to 2): Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:**
- Replace the three-seat harness with one routing table, `routes.json`. It drives the agents, the policy's routing table, every workflow stage and the seat card.
- Ship it live on Carl's go, with a tagged rollback.

**Architecture:**
- **`gkh/routes.py`** loads and validates `routes.json`.
- **`gkh/gen.py`** generates three things into the repo: the agent files, a routes block (data plus a small helper) in each workflow script, and the policy's routing table. A check mode fails CI when any of them is stale.
- **The hooks** read the installed `routes.json` at run time. This is the seat card and the Workflow hook, which injects `seat`, `dials` and `changedPaths`.
- **The installer** takes ownership of four settings keys, refuses to update while a peer session is busy, and gains three commands: `gen`, `report` and `fable`.

**Tech stack:**
- The installer and hooks are standard-library Python 3.9+.
- The workflow scripts are plain JavaScript, run by the Workflow tool.
- Python tests use `unittest`. Node 20 tests use `node --test` with `tests/workflows/harness.mjs`.

**Published copy of this plan:** https://claude.ai/artifact/9gFwTwAuokBYdwTKeTGHLa (revise that doc, not a new one).

**Spec:** `docs/2026-11-12-task-routing-design.md` (approved 12 November 2026; Claude Doc
https://claude.ai/artifact/4HppTtscZrjZgGg9X1LZq2). Plans 2 (Stage 3, measurement) and 3 (Stage 5, legal lab) are written after this plan ships.

## Global Constraints

- **Where the work happens.** Work happens only in the worktree `~/apps/gk-harness-routing` on branch `feat/task-routing`. Never edit, check out or install from `~/.gk-harness`. That checkout is live: the shell aliases are sourced from it.
- **The live `~/.claude`** is touched only by Task 1, and by Task 20 on Carl's go. Tests run against a temporary home (`InstallBase` in `tests/test_install.py`, and `GK_CLAUDE_DIR` for hooks).
- **Python** is standard library only, version 3.9+. Every module starts `from __future__ import annotations`. No `match`, no walrus in comprehensions that 3.9 rejects.
- **Workflow scripts** use no Node API, clock or randomness (the existing static scanner test enforces this). They name models by alias only: `haiku`, `sonnet`, `opus` or `fable`.
- **`policy.md`** stays under 40,000 characters, contains none of the banned strings in `tests/test_policy.py`, and says `develop` exactly once.
- **Text.** All text is British English and plain words. Report text follows the spec's "low-context reader" rule.
- **Commits** are made within this approved plan, one or more per task. Each message ends with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Push `feat/task-routing` to `origin` at the end of each stage.
- **Tests run in the foreground only.** Pipe long output through `tail -20`.
- **Tier names.** A tier is `<alias>-<effort>`, for example `opus-high`. An agent is `worker-<tier>`.
- **Capability order.** Model rank first (haiku 1, sonnet 2, opus 3, fable 4), then effort (low 1, medium 2, high 3, xhigh 4, max 5).

## Review Focus

1. **`gkh report` double-counting.** Claude Code writes several transcript lines for one API message (same `message.id`). Without de-duplication the report would multiply cost. Task 17 owns a test with a three-line, one-id fixture: the expected cost is that message's last line, counted once.
2. **A Fable stage that dies.** A workflow must never return a partial result as if complete. Tasks 9 to 12 each test a dead Fable stage: a read-only stage re-runs once on its `noFable` tier and is listed in `routing.offTier`; an implementer piece comes back `unfinished`.
3. **Settings overwriting Carl's own choice.** A later install must leave alone a key Carl changed by hand. Task 15 tests: install, then hand-set `model` to `fable`, then install again. `model` stays `fable` and `status` names it.
4. **The busy-peer check counting its own session.** An update run from inside a Claude session must not refuse because of that session. Task 16 tests that a busy session whose pid is an ancestor of the process is ignored.
5. **The hook building a git command from caller text.** A `target` such as `main..x; rm -rf ~` must never reach git. Task 14 tests that a target failing gk-review's own pattern gets no `changedPaths`, and that the Python and JavaScript patterns are the same string.

---

## Stage 0: drift fixes and proofs

### Task 0: Workspace

**Files:** none in the repo.

- [ ] **Step 1: Create the worktree.**

```bash
git -C ~/.gk-harness fetch origin
git -C ~/.gk-harness worktree add ~/apps/gk-harness-routing -b feat/task-routing origin/main
git -C ~/apps/gk-harness-routing branch --unset-upstream
```

- [ ] **Step 1b: Move the spec and plan onto the branch.**
  - Copy `docs/2026-11-12-task-routing-design.md` and this plan from `~/.gk-harness/docs/` into the worktree's `docs/`.
  - Commit them as `docs: task-routing spec and Plan 1`.
  - Delete the untracked originals in `~/.gk-harness/docs/`. Left behind, they would block Task 6's fast-forward.
  - From here on, the worktree copy is the one to edit.
- [ ] **Step 2: Check the baseline.**
  Run: `cd ~/apps/gk-harness-routing && python3 -m unittest discover -s tests 2>&1 | tail -3 && node --test tests/workflows/ 2>&1 | tail -4`
  Expected: `Ran 416 tests … OK (skipped=5)` and `# pass 68`.
- [ ] **Step 3: Start the ledger.** Create `.superpowers/sdd/<plan workspace>/progress.md`, with the first line naming this plan file.

### Task 1: Restore the Fable seat's effort (by hand, live settings)

**Files:** `~/.claude/settings.json` (live; the only live edit before Task 20).

- [ ] **Step 1: Back up the settings file.** Copy `~/.claude/settings.json` to the session scratchpad.
- [ ] **Step 2: Add the Fable 5.2 entry.** Add `"claude-fable-5-2": {"effortLevel": "high"}` to `modelSettings`, in the same shape as the existing `claude-fable-5-1` entry. Use a short Python `json` load and dump that keeps the file's 2-space indent. Change nothing else.
- [ ] **Step 3: Verify.** Run `python3 -m json.tool ~/.claude/settings.json >/dev/null && echo ok`, then diff against the backup. Expected: `ok`, and a diff of exactly the one added entry.
- [ ] **Step 4: Ledger.** Record the change in the ledger: "Task 1: settings fable-5-2 effort high (by hand; Task 15 adopts the key)".

### Task 2: Fable 5.2 in the agent text and the policy

**Files:**
- Modify: `agents/worker-fable.md`, `agents/worker-fable-max.md`, `policy.md` (lineup line and the two "Fable 5.1" mentions)
- Test: `tests/test_policy.py`

- [ ] **Step 1: Write the failing test.** Add `test_no_stale_fable_version`: no file in `agents/`, and not `policy.md`, contains `Fable 5.1` or `fable-5-1`.
- [ ] **Step 2: Run it.** Run: `python3 -m unittest tests.test_policy -v 2>&1 | tail -5`. Expected: FAIL naming the files.
- [ ] **Step 3: Fix the text.**
  - Replace "Fable 5.1" with "Fable 5.2".
  - The `policy.md` lineup line becomes `**Fable 5.2** (\`claude-fable-5-2\`, $8/$40, 1M context, thinking always on)`.
- [ ] **Step 4: Run all tests.** Run: `python3 -m unittest discover -s tests 2>&1 | tail -3`. Expected: OK.
- [ ] **Step 5: Commit.** `git commit -am "fix(agents): Fable 5.2 in agent descriptions and the policy lineup"`

### Task 3: Stage 0 proofs

**Files:** Create `docs/proofs/2026-11-stage0.md`.

Each proof records the command or check, what was seen, and the conclusion. Proof 7 is already done: on 12 November `~/.claude/sessions/<sid>.json` showed `"status": "busy"` for a working session and `"idle"` otherwise. Copy that into the file.

The live checks use a scratch directory under the session scratchpad, and project-level `.claude/` files inside it. They never touch `~/.claude/agents` or `~/.claude/settings.json`. `claude -p` runs use subscription usage only; keep each prompt to one short instruction.

- [ ] **Proof 1: does an `Explore` agent override the built-in one?**
  - In the scratch dir, write `.claude/agents/Explore.md` with frontmatter `name: Explore`, `model: haiku`, `effort: medium`, and the read-only tools `Read, Grep, Glob, Bash`.
  - Run `claude -p "Use the Explore agent to list the files here."`.
  - Read the newest subagent transcript under `~/.claude/projects/<scratch>/…/subagents/` and record its `message.model`.
- [ ] **Proof 2: does the subagent default apply under a Fable seat?**
  - Run `CLAUDE_CODE_SUBAGENT_MODEL=opus claude -p --model fable "Start a general-purpose subagent with no model to print the date."` and record the subagent's model.
  - Repeat with a project agent whose frontmatter says `model: haiku`. Its own model must win.
- [ ] **Proof 3: what happens when a model is unavailable?**
  - Run the same Fable-seat call as proof 2 with `--settings '{"availableModels":["opus","sonnet","haiku"]}'`. Record whether the call fails or quietly runs on another model.
  - Repeat with `CLAUDE_CODE_NO_MODEL_FALLBACK=1`.
  - Record that a real usage limit cannot be forced here.
- [ ] **Proof 4: per-agent tool limits.** From the executing session, run a one-agent Workflow script. Pass `agent(prompt, {model:'haiku', effort:'low', disallowedTools:['Write','Edit']})` and ask it to write a file in the scratchpad. Record refused or allowed. Repeat with the Bash limit option if the runtime has one.
- [ ] **Proof 5: how does effort resolve, and in what shape?**
  - Record how `modelSettings.<id>.effortLevel` ranks against the top-level `effortLevel`.
  - Record which key shape Claude Code actually reads (`effortLevel` under the model id, as on the box; or `effort`, as in the test fixture `tests/fixtures/settings/box-hooks.json`).
  - Use the `effort` field that SessionStart and PostModelSwitch hooks receive. Add a temporary project hook in the scratch dir that dumps its input.
- [ ] **Proof 6: does the desktop app have a per-session ultracode switch?** Use `mcp__ccd_settings__get_settings`. Record yes or no.
- [ ] **Commit:** `git add docs/proofs && git commit -m "docs(proofs): Stage 0 proofs for task routing"`.
- [ ] **Rulings.** For each proof that contradicts the spec, write a `Ruling:` line in the ledger with the consequence:
  - **Proof 1 negative:** ship no `Explore.md`; class `search` routes to `worker-haiku-medium`.
  - **Proof 4 negative:** the legal tool limits move to Plan 3 as a hook-based guard.

---

## Stage 1: one source of truth, no behaviour change

### Task 4: `routes.json` v1 and `gkh/routes.py`

**Files:**
- Create: `routes.json`, `gkh/routes.py`, `tests/test_routes.py`

**Interfaces:**
- Produces:
  - `gkh.routes.load(path: Path) -> dict` raises `RoutesError` when the file is invalid.
  - `problems(routes: dict) -> list[str]`
  - `tier(routes: dict, name: str) -> dict` returns `{"model", "effort", "agent", "description", "body"}`.
  - `capability(routes: dict, name: str) -> tuple[int, int]`
  - `judge_violations(routes: dict) -> list[str]` (returns `[]` while v1 has no `judges`)
  - `RoutesError(Exception)`

**v1 content (today's facts only):**
- `version: 1`.
- `models`: four aliases, each with `id`, `rank`, `display` (for example "Opus 5.5"), `price` and `priceDate`.
  - ids: `claude-haiku-5-5`, `claude-sonnet-5-5`, `claude-opus-5-5`, `claude-fable-5-2`.
  - Copy prices from the claude-api skill's pricing reference and record its path.
  - Haiku also gets `priceOver100k`.
- `efforts: ["low","medium","high","xhigh","max"]`.
- `tiers`: one per existing agent. Each tier has its `agent` name, its `model` and `effort` from the agent's frontmatter, and its `description` and `body` copied verbatim from the agent file. The eight are `haiku-low`, `haiku-high`, `sonnet-medium`, `opus-high`, `opus-xhigh`, `opus-max`, `fable-xhigh` and `fable-max`. Their agents are `worker-haiku-low`, `worker-haiku`, `worker-sonnet`, `worker-opus`, `worker-opus-xhigh`, `worker-opus-max`, `worker-fable` and `worker-fable-max`.

- [ ] **Step 1: Write the failing tests** in `tests/test_routes.py`:
  - `test_repo_routes_valid`: `problems(load(ROOT/"routes.json")) == []`.
  - `test_capability_order`: with a small fixture dict, `capability(r,"fable-medium") > capability(r,"opus-xhigh") > capability(r,"opus-high") > capability(r,"opus-medium")`.
  - `test_unknown_model_is_a_problem`: a tier with `model: "gpt"` gives a problem naming the tier.
  - `test_duplicate_agent_names_are_a_problem`.
  - `test_invalid_json_raises`: `RoutesError`.
- [ ] **Step 2: Run them.** Run: `python3 -m unittest tests.test_routes -v 2>&1 | tail -8`. Expected: FAIL (`ModuleNotFoundError: gkh.routes`).
- [ ] **Step 3: Implement** `gkh/routes.py` and `routes.json`. `problems` checks three things:
  - every tier's model and effort are known;
  - agent names are unique and match `^worker-[a-z0-9-]+$` (or exactly `Explore`);
  - prices are positive numbers.
- [ ] **Step 4: Run all tests.** Expected: PASS.
- [ ] **Step 5: Commit.** `feat(routes): routes.json v1 holds today's tiers, with a loader and validator`

### Task 5: `gkh gen` generates the agents; the installer reads routes

**Files:**
- Create: `gkh/gen.py`, `tests/test_gen.py`
- Modify: `gkh/cli.py` (`COMMANDS`, `USAGE`, `main`, `WORKERS` → derived, `_required`, `DEPLOYED`), `tests/test_policy.py` (`EIGHT` → derived), `tests/test_install.py` (`CommandTests`, routes deployment), `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `gkh.routes.load`, `tier`.
- Produces:
  - `gkh.gen.render_agent(routes: dict, tier_name: str) -> str`
  - `outputs(root: Path) -> dict[str, str]` (repo-relative path → text)
  - `stale(root: Path) -> list[str]`
  - `write(root: Path) -> list[str]` (the paths written)
  - The command `install.py gen [--check]`: `--check` exits 1 and lists the stale paths.

**Details:**
- `render_agent` writes `---\nname: <agent>\ndescription: <description>\nmodel: <model>\neffort: <effort>\n---\n<body>`, matching today's files byte for byte. Read one first to confirm the field order and the blank line.
- `cli.WORKERS` becomes the sorted agent names read from `routes.json`.
- `DEPLOYED` gains `(".", "routes.json", ("hooks", "gk-harness"))`, so the installed copy sits beside the hooks that read it. *(This differs from spec 4.1, which says `~/.claude/gk-harness/`. The new place is chosen so hooks find the file next to themselves.)*

- [ ] **Step 1: Write the failing tests.**
  - `tests/test_gen.py::test_generated_agents_equal_committed`: `stale(ROOT) == []`.
  - `test_stale_lists_a_hand_edit`: copy the repo to a temp dir, append a line to one agent, and expect `stale(tmp) == ["agents/worker-opus.md"]`.
  - `test_gen_check_exit_code`: `cli.main(["gen","--check"])` returns 0 on the clean repo.
  - `tests/test_install.py::RoundTripTests::test_routes_deployed`: after install, `<cdir>/hooks/gk-harness/routes.json` bytes equal the repo file. After uninstall, it is gone.
  - In `tests/test_policy.py`, `EIGHT` becomes `{t["agent"] + ".md" for t in load(ROOT/"routes.json")["tiers"].values()}`.
- [ ] **Step 2: Run them.** Expected: FAIL (`gkh.gen` is missing, and `gen` is an unknown command).
- [ ] **Step 3: Implement** `gkh/gen.py` and the `cli` changes. Add `gen` to `COMMANDS`, `"--check"` to `OPTIONS["gen"]`, and the gen line to `USAGE`.
- [ ] **Step 4: CI.** In `.github/workflows/ci.yml`, job `py`, add a step that runs only on `ubuntu-latest`: `python install.py gen --check`.
- [ ] **Step 5: Run all tests and the check.** Run both suites, plus `python3 install.py gen --check; echo $?`. Expected: all pass, and `0`.
- [ ] **Step 6: Commit.** `feat(gen): agents generated from routes.json; installer takes its agent list from routes`

### Task 6: Ship Stage 1 (Carl's go)

Behaviour does not change. This task proves the generator and installer path live before the switch.

- [ ] **Step 1: Push and report.** Push `feat/task-routing`. Publish a short Stage 0+1 report to Carl as a Claude Doc: the proofs, the rulings, and the test lines.
- [ ] **Step 2: On Carl's go, merge and push.** Fast-forward `main` to the branch in `~/.gk-harness` with `git -C ~/.gk-harness merge --ff-only feat/task-routing`, then push `main`.
- [ ] **Step 3: Update and verify.** Run `~/.gk-harness/install.sh update`, then `install.sh status`. Expected:
  - `status` reports no drift;
  - `~/.claude/agents/worker-*.md` are unchanged apart from Task 2's description text;
  - `~/.claude/hooks/gk-harness/routes.json` is present.

---

## Stage 2: the new design, switched live on Carl's go

### Task 7: `routes.json` v2 and the new agent set

**Files:**
- Modify: `routes.json`, `gkh/routes.py` (validation for `classes`, `stages`, `judges`, `seat`, `evidence`)
- Test: `tests/test_routes.py`, `tests/test_policy.py`
- Generated: `agents/*.md`

**Values (from spec 4.4, verbatim):**

**Tiers and agents.** Each tier has a new agent, `worker-<tier>`:
- `haiku-low`
- `haiku-medium`
- `sonnet-medium`
- `sonnet-high`
- `opus-medium`
- `opus-high`
- `opus-xhigh` (the `noFable` fallback only)
- `fable-medium`
- `fable-high`

`Explore` (`haiku-medium`, read-only tools) is added only if proof 1 is positive.

**Retired names**, kept for one version, held in a new `retired` map in `routes.json` (old agent name → tier), and mapped to their nearest new tier. The generator renders each one with the target tier's model and effort, and a description that starts "Retired name: use worker-<tier>.". The agent-set test in `tests/test_policy.py` derives its expected set as: tier agents ∪ retired names ∪ `Explore`, where `Explore` is shipped.

| Old name | New tier |
| --- | --- |
| `worker-haiku` | `haiku-medium` |
| `worker-sonnet` | `sonnet-medium` |
| `worker-opus` | `opus-high` |
| `worker-opus-max` | `opus-xhigh` |
| `worker-fable` | `fable-high` |
| `worker-fable-max` | `fable-high` |

**New tier bodies:**
- Reuse today's body text for the matching role.
- Low and medium tiers get the "Finish the whole task" text, copied verbatim from the claude-api skill's `shared/model-migration.md`, Fable 5.2 section, "Finish the whole task" bullet, under a `# Finishing work` heading.
- No body contains "This codebase is yours".

**`classes`.** Each class gets a `tier`, a `who` and a `check`. The `who` values are:

| Class | Tier |
| --- | --- |
| `chat`, `lookup` | `seat` |
| `search` | `haiku-medium` |
| `run-report` | `haiku-low` |
| `mechanical` | `sonnet-medium` |
| `known-cause` | `sonnet-medium` |
| `feature` | `opus-medium`, `next: opus-high` |
| `ui-build` | `opus-high` |
| `hard` | `fable-medium`, `next: fable-high` |
| `plan-check`, `design-panel`, `judge-opus` | `fable-medium` |
| `judge-sensitive` | `fable-high` |
| `ui-signoff` | `fable-high` |
| `legal-doc` | `seat`, `floor: opus-medium` |

**`ladder`:** `haiku-medium`, `sonnet-medium`, `sonnet-high`, `opus-medium`, `opus-high`, `fable-medium`, `fable-high`.

**`stages`.** Keys are `<workflow>.<role>`. The `onFail` column is given for Fable stages only; other stages keep today's handling of a dead agent.

| Stage | Tier | Conditions | `noFable` | `onFail` | apex |
| --- | --- | --- | --- | --- | --- |
| `gk-review.scope` | `haiku-medium` | — | — | — | |
| `gk-review.find` | `sonnet-medium` | — | — | — | |
| `gk-review.refute` | `sonnet-medium` | — | — | — | |
| `gk-review.judge` | `opus-high` | `sensitive`: `fable-high`; `authorFloor: true` | `opus-xhigh` | `rerun` | yes |
| `gk-build.plan-check` | `fable-medium` | — | `opus-xhigh` | `rerun` | yes |
| `gk-build.implement` | `byClass` | `spec`: `sonnet-medium`; `judgement`: `opus-medium`; `hard`: `fable-medium`; `security`: `fable-medium` | `hard` and `security`: `opus-high` | `unfinished` | |
| `gk-build.check` | `haiku-low` | — | — | — | |
| `gk-build.review` | `opus-high` | `atLeast: implementer` | `opus-xhigh` | `rerun` | |
| `gk-build.judge` | `fable-medium` | `ui`: `fable-high` | `opus-xhigh` | `rerun` | yes |
| `gk-diagnose.hypothesise`, `gk-diagnose.test` | `opus-high` | — | — | — | |
| `gk-diagnose.adjudicate` | `fable-medium` | — | `opus-xhigh` | `rerun` | yes |
| `gk-research.sweep` | `haiku-medium` | — | — | — | |
| `gk-research.merge`, `gk-research.deep-read` | `opus-high` | — | — | — | |
| `gk-research.critic` | `opus-high` | — | — | — | yes |
| `gk-design.propose` | `opus-high` | `ui`: `fable-medium` | `opus-high` | `rerun` | |
| `gk-design.judge` | `fable-high` | — | `opus-xhigh` | `rerun` | yes |
| `gk-design.sign-off` | `fable-high` | — | `none` (Task 12) | — | |

**`judges`** (each pair is judge stage over judged stage):
- `gk-review.judge` over `gk-review.find` and `gk-review.refute`;
- `gk-build.review` over `gk-build.implement` (via `atLeast`);
- `gk-build.judge` over `gk-build.review`;
- `gk-diagnose.adjudicate` over `gk-diagnose.test`;
- `gk-research.critic` over `gk-research.merge` and `gk-research.deep-read`;
- `gk-design.judge` over `gk-design.propose`.

**`seat`:**
- `default: "opus-medium"`.
- `effort: {opus: "medium", fable: "medium", sonnet: "medium"}`.
- `launchers: {gk: "opus-medium", "gk-fable": "fable-medium", "gk-ultra": "opus-medium+ultracode"}`.
- `subagentDefault: "opus"`.

**`evidence`:** every class and stage gets `"guide"`.

- [ ] **Step 1: Write the failing tests** in `tests/test_routes.py`:
  - `test_judges_hold`: `judge_violations(load(...)) == []`.
  - `test_judge_violation_detected`: a fixture with the `gk-build.judge` tier set to `sonnet-medium` yields one violation.
  - `test_every_fable_stage_has_nofable_and_onfail`.
  - `test_ladder_strictly_increases`.
  - `test_every_route_has_evidence`.
  - `test_class_tiers_exist`.
  - `test_v2_values` pins three spot values: `gk-build.plan-check` = `fable-medium`, `gk-review.judge` sensitive = `fable-high`, and `seat.default` = `opus-medium`.
- [ ] **Step 2: Write the failing tests** in `tests/test_policy.py`:
  - `test_agent_text_matches_tier`: each generated description names its model's display name and its effort word, and no other model's display name. Retired names are allowed to name their target.
  - `test_retired_names_point_to_live_tiers`.
- [ ] **Step 3: Run them.** Expected: FAIL.
- [ ] **Step 4: Implement.** Extend `problems` and add `judge_violations`, which handles `atLeast` by checking the floor `opus-high` against every `byClass` tier. Update `routes.json`. Run `python3 install.py gen`.
- [ ] **Step 5: Run both suites.** Expected: Python passes. The Node routing-equivalence tests (old per-profile routing) are still untouched at this point. They pass because the workflow scripts do not read `routes.json` yet.
- [ ] **Step 6: Commit.** `feat(routes): routes.json v2: tiers, classes, stages, judges, seat; tier-named agents`

### Task 8: The workflow routes block and its helper

**Files:**
- Create: `gkh/templates/routes-helper.js`, `tests/workflows/routes.test.mjs`
- Modify:
  - `gkh/gen.py`: add `render_routes_block`, and rewrite `meta.phases[].model`.
  - `routes.json`: add `phases`, mapping `<workflow>` → `{<phase title>: <stage key>}` for every phase that runs an agent.
  - `workflows/gk-*.js`: the generated insertion only, in this task.

**Interfaces:**
- Produces:
  - `gkh.gen.render_routes_block(routes: dict, workflow: str) -> str`. The block runs from `// <gk-routes v2> generated by gkh gen from routes.json v<N>; do not edit` to `// </gk-routes>`.
  - The block is inserted directly after each script's `// ===== gk-project block: END =====` line. Never between `meta` and the marker.
- Inside the block:
  - `const ROUTES = {version, tiers: {...the tiers this workflow uses}, stages: {...this workflow's stages}}`.
  - The helper code, identical in every script, copied from the template:
    - `routeOf(stage, ctx) -> {tier, model, effort, off}`, where `ctx = {cls, ui, sensitive, author, implementer}`;
    - `runStage(stage, ctx, prompt, opts) -> Promise<any>`;
    - `routingResult() -> {routes, dials, offTier}`;
    - `const DIALS` (validated from `ARGS.dials`; unknown entries are logged and dropped).

**`routeOf` semantics, in order:**
1. Start from the stage's base tier, or its `byClass[ctx.cls || 'judgement']`.
2. Apply a matching condition (`ui`, `sensitive`).
3. Apply `atLeast` against `ctx.implementer`, with the floor `opus-high`.
4. Apply `authorFloor`: if `ctx.author` is above `opus-high`, the tier becomes the higher of the two.
5. If `DIALS` holds `fable` and the stage is `apex`, the tier becomes `fable-high`.
6. If `DIALS` holds `nofable` and the tier's model is `fable`, the tier becomes the stage's `noFable` (the `byClass` entry where there is one), with `off: 'nofable'`. `nofable` wins over `fable`.

**`runStage`:**
1. Call `agent(prompt, {...opts, model, effort})`.
2. If the result is `null`, the tier's model is `fable` and `onFail` is `rerun`, call once more on the `noFable` tier and record `{stage, label, tier, reason: 'onFail'}`.
3. If `onFail` is `unfinished`, return `null` (the caller marks the piece unfinished).
4. Push every `off` route onto `offTier`.

- [ ] **Step 1: Write the failing tests** in `tests/workflows/routes.test.mjs`:
  - `routes block present and current`: every `workflows/gk-*.js` except `gk-selftest.js` has exactly one block, after the project block's END line.
  - `routeOf follows routes.json`: load the helper source from the template, evaluate it with a fixture `ROUTES` and `ARGS`, and assert the six semantics above, one case each.
  - `the marker stays directly after meta, within 16 KB`: the existing static test still passes. Re-run it here.
- [ ] **Step 2: Run them.** Run: `node --test tests/workflows/routes.test.mjs 2>&1 | tail -8`. Expected: FAIL.
- [ ] **Step 3: Implement** the template and `render_routes_block`, and extend `outputs`, so `gen` writes the blocks and `--check` covers them. The `meta.phases[].model` labels are generated from the stage that phase runs, keyed by phase title in `routes.json` `phases`.
- [ ] **Step 4: Run `gen` and both suites.** Expected: the new tests pass. Existing tests still pass, because no script calls the helper yet.
- [ ] **Step 5: Commit.** `feat(workflows): generated routes block and routing helper in every gk workflow`

### Task 9: gk-research and gk-diagnose use the table

**Files:**
- Modify: `workflows/gk-research.js`, `workflows/gk-diagnose.js`, `tests/workflows/design-research-diagnose.test.mjs`, `tests/workflows/routes.test.mjs`

**Changes:**
- Every `agent()` call becomes `runStage('<workflow>.<role>', {}, prompt, opts)`. No model or effort literal remains outside the routes block.
- `profile` and `escalate` are accepted but ignored. When either is present, log `profile is retired; routes come from routes.json v<N>`. Remove the `Profile:` log.
- The marker becomes `// gk-harness:args-v2`.
- `whenToUse`, the thrown "requires args" text and the phase details drop `profile`/`escalate`/Mode wording and add `dials?: string[]`.
- gk-research's allow-list gains `dials`, `seat` and `changedPaths`. It keeps `profile` and `escalate` for one version.
- Each result gains `routing: routingResult()`.

- [ ] **Step 1: Write the failing tests.**
  - `routes.test.mjs` gains a table-driven `stage tiers equal routes.json`. For each of the two workflows, under the dials `[]`, `['nofable']` and `['fable']`, every call's `[model, effort]` equals what the test computes from `routes.json`, which it reads with `fs`.
  - Add `dead adjudicator re-runs on opus xhigh`: the responder returns `null` for `adjudicate` on `fable`. Expect a second `adjudicate` call on `opus`/`xhigh`, and `result.routing.offTier` to contain `{stage:'gk-diagnose.adjudicate', reason:'onFail'}`.
  - Add `retired args are accepted`: `{profile:'opus', escalate:true}` runs and logs the retired line.
- [ ] **Step 2: Remove the old tests.** Delete the routing-equivalence tests for these two workflows from `design-research-diagnose.test.mjs`. Keep its other tests.
- [ ] **Step 3: Run them.** Expected: the new tests FAIL.
- [ ] **Step 4: Implement.** Expected: both suites pass.
- [ ] **Step 5: Commit.** `feat(workflows): gk-research and gk-diagnose take every stage from routes.json`

### Task 10: gk-review uses the table, with a judge floor from code

**Files:**
- Modify: `workflows/gk-review.js`, the project block in all five `workflows/gk-*.js` (adding `uiPaths`, identically), `tests/workflows/review-build.test.mjs`, `tests/workflows/routes.test.mjs`

**Changes:**
- **Project block.** It parses an optional `uiPaths: string[]` of regular expressions, validated like `signOff[].paths`.
- **Sensitivity.** `sensitive` is true when any of these holds:
  - `ARGS.ui`, `ARGS.security` or `ARGS.clientFacing`;
  - a `changedPaths` entry that is not a test file (by the existing test-file rule) matches `project.uiPaths` or any `signOff[].paths`;
  - the scope agent raises it (`touchesUi`, `touchesSecurity`, `touchesClientWording`).

  The scope pass can only raise sensitivity, never clear it.
- **Author.** The judge's `ctx.author` is `ARGS.author` (a tier name) or else `ARGS.seat`.
- **Logs.** The judge log becomes `Judge: <model> <effort> over N survivor(s)<route>`. Update the `judgeRouteLogs` helper to match.
- The Task 9 changes apply here too: marker v2, retired args accepted, the routing result, and the meta and `whenToUse` text.

- [ ] **Step 1: Write the failing tests** (table-driven). Each case is a set of inputs with dials `[]` and `['nofable']`, and an expected judge tier:

| Case | Inputs | Judge tier |
| --- | --- | --- |
| a | none | `opus-high` |
| b | `ui` | `fable-high` |
| c | `security` | `fable-high` |
| d | `changedPaths: ['frontend/src/app/x.tsx']`, project `uiPaths: ['^frontend/src/app/']` | `fable-high` |
| e | same as d, but the path is `frontend/src/app/x.test.tsx` | `opus-high` |
| f | scope detects `touchesUi` | `fable-high` |
| g | `seat: 'fable-medium'` | `fable-medium` |
| h | `author: 'fable-high'` | `fable-high` |
| i | case b with dials `['nofable']` | `opus-xhigh` |
| j | dead `fable-high` judge | a re-run on `opus-xhigh`, listed in `offTier` |

  Also add `uiPaths validated`: a non-string entry throws the project-block error.
- [ ] **Step 2: Remove the old tests.** Delete the gk-review routing-equivalence tests (old per-profile matrix). Keep the sentence, schema, guard, target, focus and finder tests. The golden copies stay as text fixtures for the sentence tests.
- [ ] **Step 3: Run, then implement.** Expected: the new tests fail first, then both suites pass.
- [ ] **Step 4: Commit.** `feat(gk-review): judge from routes.json; sensitivity floor from changed paths and project rules`

### Task 11: gk-build uses the table, with piece classes and an exit-code check

**Files:** `workflows/gk-build.js`, `tests/workflows/review-build.test.mjs`, `tests/workflows/routes.test.mjs`

**Changes:**
- **Piece class.** Pieces take `class: 'spec'|'judgement'|'hard'|'security'`, defaulting to `judgement`.
  - `hard: true` is still accepted for one version, maps to `class: 'hard'`, and logs `pieces[i].hard is retired; use class`.
  - Giving both `hard` and `class` throws.
  - The plan-check prompt asks the checker to confirm each piece's class. Its schema gains `classes: [{name, class, agree: boolean, reason}]`. A disagreement is returned in the result, not applied.
- **Tiers.**
  - Implementers take `routeOf('gk-build.implement', {cls})`.
  - Review takes `{implementer: <that piece's tier>}`.
  - The judge takes `{ui: FABLE_JUDGE}`. `FABLE_JUDGE` is computed as today.
- **Failures.** A dead implementer on a Fable class returns the piece as `unfinished`, with no re-run. Failed pieces come back in `result.failed` for the seat to re-run on the next rung. There is no retry inside the workflow.
- **Check stage.** The schema becomes `{ran: string, exitCode: integer, tail: string}`, and `passed` is `exitCode === 0`. The prompt asks for the exit code and the last 40 lines verbatim. The result gains `seatMustRerun: <the check command>`.
- The Task 9 changes apply here too.

- [ ] **Step 1: Write the failing tests.**
  - Table-driven implementer tiers: one case per class, under `[]` and `['nofable']`.
  - `review tier at least implementer`: a `hard` piece's review runs on `fable-medium`, and on `opus-xhigh` under `nofable`.
  - `check passes only on exit code 0`: a responder returning `{exitCode: 1, …}` with prose claiming success still marks the check failed.
  - `dead fable implementer is unfinished`.
  - `hard is retired but accepted`.
  - `hard and class together throw`.
- [ ] **Step 2: Remove the old tests.** Delete gk-build's routing-equivalence tests and the old `implTiers` grid. Keep the rest.
- [ ] **Step 3: Run, then implement.** Expected: the new tests fail, then both suites pass.
- [ ] **Step 4: Commit.** `feat(gk-build): piece classes, routes.json tiers, check decided by exit code`

### Task 12: gk-design uses the table

**Files:** `workflows/gk-design.js`, `tests/workflows/design-research-diagnose.test.mjs`, `tests/workflows/routes.test.mjs`

**Changes:**
- Proposals: `routeOf('gk-design.propose', {ui: UI})`. Judges: `gk-design.judge`.
- **The sign-off stage is not called when the judges ran on a Fable tier.** Their judgement is the Fable sign-off.
- **When Fable is off:**
  - The judges run on `opus-xhigh`.
  - The result's `signOff` becomes `{pending: 'Fable off', note: 'UI sign-off still owed by Fable'}`. No sign-off agent runs.
  - *(Ruling: an Opus sign-off would only repeat the judges.)*
- The Task 9 changes apply here too.

- [ ] **Step 1: Write the failing tests.**
  - Table-driven proposals and judges under `[]`, `['nofable']` and `['fable']`, with `ui` true and false.
  - `no sign-off agent when judges ran on fable`.
  - `sign-off pending when fable is off`.
- [ ] **Step 2: Remove the old routing-equivalence tests.**
- [ ] **Step 3: Run, then implement.** Expected: both suites pass.
- [ ] **Step 4: Commit.** `feat(gk-design): routes.json tiers; UI sign-off from the Fable judges, pending when Fable is off`

### Task 13: No tier literals outside the block

**Files:** `tests/workflows/routes.test.mjs`

- [ ] **Step 1: Write the test** `no tier literals outside the routes block`. In every gk script, after removing the routes block and the comments, there are none of `model: '`, `effort: '`, `'xhigh'` or `'max'`. `meta.phases[].model` is allowed: the generator owns it, and `gen --check` covers it.
- [ ] **Step 2: Run it.** Expected: PASS. If it fails, fix the script, not the test.
- [ ] **Step 3: Commit.** `test(workflows): tiers live only in the generated routes block`

### Task 14: Hooks: seat card, injected seat, dials and changed paths

**Files:**
- Modify: `hooks/gk_model.py`, `hooks/gk-seat.py`, `hooks/gk-profile.py`, `hooks/gk_common.py`
- Test: `tests/test_model.py`, `tests/test_seat_hook.py`, `tests/test_profile_hook.py`, `tests/test_common.py`

**Interfaces:**
- **`gk_common`:**
  - `routes_file() -> Path` (beside the hook files)
  - `load_routes() -> dict|None`
  - `fable_off_file() -> Path` (`claude_dir()/"state"/"gk-fable-off.json"`)
  - `fable_off() -> bool`
- **`gk_model`:**
  - Removes `MODE`, `RANK`, `profile_for_model` and `resolve_profile`.
  - Adds `alias_for_model(model) -> str|None`, which maps by substring to `haiku`/`sonnet`/`opus`/`fable`.
  - Adds `seat_tier(model, effort) -> str|None`. The effort defaults to the routes seat effort for that alias.
  - `save_seat(sid, model, effort, source) -> bool`. `load_seat(sid)` requires a known alias.
  - Adds `resolve_seat(data) -> str|None`, which returns a tier string.
  - `seat_card(model, effort, routes, notes) -> str`:
    - text: `gk-harness seat: <model> at <effort> · routes v<N>`;
    - one sentence per note. A note is added when the effort differs from `routes.seat.effort[alias]`, when the saved default `model` in settings is not `opus`, and when `fable_off()` is true ("Fable is off: run gkh fable on to restore it.");
    - when routes cannot be read: `routes unavailable; tell Carl`.
- **`gk-profile.py`** (the filename is kept; ruling: renaming would leave an orphan, because the installer only adds files):
  - `MARKERS = (b"gk-harness:args-v1", b"gk-harness:args-v2")`.
  - For v2 scripts it injects:
    - `seat` (if absent);
    - `dials`: the caller's list, plus `'nofable'` when `fable_off()`;
    - `project` (as today, but `project.legal` is always taken from the file, also when the caller passed `project`);
    - `scratchpad` (gk-diagnose);
    - `changedPaths` for gk-review.
  - It no longer injects `profile`, and drops the `HIGHER` warning.
  - New `changed_paths(top: Path, target: str) -> list[str]|None`. It returns `None` unless `target` matches `TARGET_RE`. Copy that pattern from `gk-review.js`, and add a test asserting the two pattern strings are equal.
    - For `working-tree`: `git diff --name-only HEAD` plus `git ls-files --others --exclude-standard`.
    - For a range: `git diff --name-only <range>`, the same range form gk-review's finders use. Read its prompt text.
    - Use `gk_common._git`. Cap the result at 2,000 paths.

- [ ] **Step 1: Rewrite the tests.**
  - Card text for:
    - each alias;
    - a non-default effort;
    - Fable off;
    - routes missing;
    - a saved default model of `fable`.
  - Seat state without a profile.
  - Hook injection of `seat`, `dials` (with and without the off file), `project.legal` overwritten, and `changedPaths` in a temp git repo with one tracked change and one untracked file.
  - Hostile targets, which produce no `changedPaths` and run no git: `main..x; rm -rf ~`, `$(id)`, and `--output=/tmp/x`.
  - Pattern equality.
  - A v1 script is still handled, with no `seat`, `dials` or `changedPaths` added. That keeps old copies working.
- [ ] **Step 2: Run them.** Run: `python3 -m unittest tests.test_model tests.test_seat_hook tests.test_profile_hook tests.test_common 2>&1 | tail -5`. Expected: FAIL.
- [ ] **Step 3: Implement.** Expected: PASS. Then run the whole Python suite.
- [ ] **Step 4: Commit.** `feat(hooks): seat card from routes.json; Workflow hook injects seat, dials and changed paths`

### Task 15: The installer owns four settings keys

**Files:** `gkh/settings_merge.py`, `gkh/cli.py` (`_settings_step`, `status`, `_status_drift`), `tests/test_gkh_settings_merge.py`, `tests/test_install.py`

**Interfaces:**
- `settings_merge.owned_desired(routes: dict) -> dict[tuple, Any]` returns:
  - `("model",)` → `"opus"`;
  - `("modelSettings", <id>, "effortLevel")` → the seat effort, for opus, fable and sonnet. Use proof 5's key shape if it differs.
  - `("env", "CLAUDE_CODE_SUBAGENT_MODEL")` → `"opus"`;
  - `("ultracode",)` → `False`.
- `merge(settings, python, claude_dir, desired: dict|None = None, last: dict|None = None) -> (new, undo)`. For each desired path:
  - if the installer has never owned it (no `last` entry), write it and record the prior value (first adoption);
  - otherwise write only if the current value equals `last[path]`.
- `undo["owned"] = {"<dotted path>": {"prior": {"present": bool, "value": …}, "wrote": value}}`.
- `unmerge` restores the prior value only where the current value still equals `wrote`.
- `hand_changed(settings, last) -> list[str]`: the dotted paths where the current value differs from what the installer last wrote.
- `cli._settings_step` reads `last` from the newest settings action in the journal (`manifest.actions_newest_first`) and `desired` from the repo's `routes.json`. Its note text becomes `settings.json: hooks registered; model, efforts, subagent default and ultracode owned`.
- `status` lists `hand_changed` paths. It warns if `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` or `CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL` is set in `settings.env` or in the process environment.

- [ ] **Step 1: Write the failing tests.**
  - **Fresh install:** all four keys are written, and `undo.owned` holds their priors.
  - **Box install:** the `BOX_OTHER_KEYS` model `fable` becomes `opus`, with the prior recorded. Update `KEPT_KEYS` to drop `model` and `modelSettings`, and assert the new behaviour.
  - **Hand change survives:** install; set `model` to `"fable"`; install again. `model` is still `"fable"`, and `status` output names `model`.
  - **Uninstall:**
    - it restores every prior where the value is unchanged;
    - it leaves a hand-changed key alone;
    - `ultracode`'s prior `False` or `True` comes back.
  - **No-op install:** a second identical install writes nothing.
  - **Status warning:** with `env.CLAUDE_CODE_SUBAGENT_MODEL_FORCE` present, `status` warns.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** Expected: PASS. Then run the whole Python suite.
- [ ] **Step 4: Commit.** `feat(install): own model, effort, subagent default and ultracode; never override a hand change`

### Task 16: Busy-peer refusal, version 2.0.0, launchers, `gkh` and `gkh fable`

**Files:**
- Create: `gkh/peers.py`, `tests/test_gkh_peers.py`
- Modify: `gkh/cli.py`, `gkh/__init__.py` (`VERSION = "2.0.0"`), `gkh/shell.py` (rc block exports `GK_HARNESS_ROOT`), `shell/aliases.sh`, `shell/aliases.ps1`, `tests/test_install.py`, `tests/test_entry.py`, `tests/test_gkh_shell.py`, `tests/test_gkh_claude_md.py`

**Interfaces:**
- `gkh.peers.ancestor_pids() -> set[int]`: Linux uses `/proc/<pid>/stat`; macOS uses `ps -o ppid=`; on Windows it returns an empty set.
- `busy_peers(claude_dir: Path, own: set[int]) -> list[str]`. It reads `<claude_dir>/sessions/*.json` and returns the `name` of each session where all three hold:
  - `status == "busy"`;
  - the `pid` is alive (copy the small `pid_alive` from `hooks/gk_common.py`);
  - the `pid` is not in `own`.
- **`install` and `update`:**
  - both refuse, with exit code 3, when `busy_peers` is not empty. The message: `<n> session(s) busy: <names>. Wait until they are idle, or pass --ignore-busy.`
  - `update` checks before `git pull`.
  - add `--ignore-busy` to `OPTIONS` for both.
- **`fable off|on|status`:** `off` writes `<cdir>/state/gk-fable-off.json` `{"since": <utc iso>}`, `on` removes it, and `status` prints which.
- **`shell/aliases.sh`:**

```sh
gk() { claude --model opus --effort medium "$@"; }
gk-fable() { claude --model fable --effort medium "$@"; }
gk-ultra() { claude --model opus --effort medium --settings '{"ultracode":true}' "$@"; }
gkh() { python3 "$GK_HARNESS_ROOT/install.py" "$@"; }
```

  Write the same in `aliases.ps1` with PowerShell functions. Drop `gk-opus` and `gk-sonnet`; legacy rc lines are already handled by `legacy.RC_ALIAS_RE`.

- [ ] **Step 1: Write the failing tests.**
  - `busy_peers` with a fixture sessions dir:
    - a live busy session from a spawned `sleep 30` child, included;
    - an idle session, excluded;
    - a dead pid, excluded;
    - our own ancestor, excluded.
  - `update` refuses before pulling: it exits 3 and the seed repo's HEAD is unchanged.
  - `--ignore-busy` proceeds.
  - `fable off` then `fable on` creates and removes the file.
  - The alias file defines `gk`, `gk-fable`, `gk-ultra` and `gkh`, and has no `gk-opus`.
  - The rc block exports `GK_HARNESS_ROOT`.
  - The version tests now say `2.0.0`.
- [ ] **Step 2: Run, then implement, then run the whole suite.** Expected: PASS.
- [ ] **Step 3: Commit.** `feat(install): refuse while peers are busy; gk, gk-fable, gk-ultra, gkh; gkh fable on|off; v2.0.0`

### Task 17: `gkh report`

**Files:** Create `gkh/report.py`, `tests/test_gkh_report.py`, `tests/fixtures/transcripts/report-*.jsonl`. Modify `gkh/cli.py`.

**Interfaces:**
- `iter_calls(claude_dir: Path, since: str|None = None, session: str|None = None) -> Iterator[dict]`.
  - It reads `projects/*/*.jsonl`, their subagent transcripts, and workflow journals.
  - Each call is `{session, agent, workflow, stage, model, input, output, cache_write, cache_read, message_id}`.
  - Lines are de-duplicated by `message.id`, keeping the last line's usage.
- `price(routes: dict, call: dict) -> float`. The model id maps to an alias by substring. Haiku uses `priceOver100k` when `input + cache_read + cache_write > 100_000`.
- `summarise(calls, routes) -> dict` groups by session, day, workflow, stage, agent and model, and gives Fable's share. Per-class cost comes from `Route: <class>` lines in assistant text and is marked best effort.
- `render(summary, as_json: bool) -> str`. The header says: "On a subscription, cost is a proxy for your share of the usage limits."
- The command: `install.py report [--since YYYY-MM-DD] [--session ID] [--config-dir DIR] [--json]`. `OPTIONS` parsing gains option values.

- [ ] **Step 1: Write the failing tests.**
  - The de-duplication fixture: three lines, one id, cost counted once from the last line.
  - Haiku over-100K pricing.
  - A subagent transcript attributed to its agent name.
  - A workflow journal attributed to its workflow and stage label.
  - `--since` filters.
  - `--json` parses.
  - Route-line class tagging.
- [ ] **Step 2: Run, then implement.** Expected: PASS.
- [ ] **Step 3: Verify read-only against the live box.** Run `python3 install.py report --since 2026-11-05 | head -30`. This reads transcripts only. Paste the head into the ledger.
- [ ] **Step 4: Commit.** `feat(report): gkh report prices transcripts from routes.json`

### Task 18: The new policy

**Files:** `policy.md`, `docs/policy-coverage.md`, `README.md`, `gkh/gen.py` (`render_policy_table`), `tests/test_policy.py`

**Changes:**
- `policy.md` is rewritten to the spec 7 outline, about 120 lines. The class table sits between `<!-- gk-routes:table -->` and `<!-- /gk-routes:table -->`, generated by `render_policy_table(routes)`. Its columns are class, examples, who, tier and evidence.
- It includes:
  - the two approval lists and the default sentence;
  - the dials, which count only as a whole word at the start or end of Carl's prompt, and are passed to workflows in `args.dials`;
  - the Fable switch;
  - the route line;
  - the review size rule (about 150 changed lines). The "Finish the whole task" text sits in the seat section for a Fable seat at medium, and the low-context reader text sits in the reports section.
- `docs/policy-coverage.md` is rewritten: each rule of the old `policy.md` (by heading or line) maps to its new home, or to "dropped, because …".
- `README.md` covers launchers, `gkh` commands, `gkh fable off|on`, dials, the settings the installer owns, and rollback (Task 20 Step 6).

- [ ] **Step 1: Write the failing tests.**
  - `test_policy_is_short`: at most 140 lines.
  - `test_policy_has_both_approval_lists_and_default`: the headings "Needs Carl's word" and "No approval needed", and the default sentence.
  - `test_no_retired_concepts`: none of `Mode A`, `Mode B`, `Mode C`, `profile:`, `escalate`, `think less` or `This codebase is yours`.
  - `test_table_is_generated`: `gen --check` is clean.
  - The existing tests stay: size, banned strings, `develop` once.
- [ ] **Step 2: Run, then implement, then run both suites and `gen --check`.**
- [ ] **Step 3: Commit.** `feat(policy): short routing policy with a generated class table; coverage map of the old rules`

### Task 19: Stage 2 report and branch review

- [ ] **Step 1: Run every check.** Run both suites and `gen --check`. Push `feat/task-routing`.
- [ ] **Step 2: Report to Carl.** Publish a Stage 2 report as a Claude Doc. It covers what changed, the test lines, the rulings, the new policy text in full (Carl reads it before the switch), and the switch steps in Task 20.

### Task 20: The switch (Carl's go)

- [ ] **Step 1: Wait.** Wait for Carl's go. Name any peer sessions that are busy.
- [ ] **Step 2: Tag the rollback point.** Run `git -C ~/.gk-harness tag pre-routing main && git -C ~/.gk-harness push origin pre-routing`.
- [ ] **Step 3: Merge and push.** Run `git -C ~/.gk-harness merge --ff-only feat/task-routing && git -C ~/.gk-harness push origin main`.
- [ ] **Step 4: Update.** Run `~/.gk-harness/install.sh update`. Expected: it refuses if a peer is busy (wait, or Carl says `--ignore-busy`), then installs. Then run `install.sh status`. Expected: no drift, and `model`, the efforts, the subagent default and `ultracode` owned.
- [ ] **Step 5: Live checks** (record each in the ledger):
  - open a new terminal session with `gk`, and a desktop session. The card shows `opus at medium · routes v2`.
  - run `gkh fable off`, then run Workflow `gk-selftest` with `args: {dials: []}`. The echoed args hold `dials: ['nofable']` and a `seat`. Then run `gkh fable on`.
  - run `gkh report --since <today>`.
- [ ] **Step 6: Write the rollback into the README and the ledger:**
  1. `~/.gk-harness/install.sh uninstall`
  2. `git -C ~/.gk-harness switch --detach pre-routing`
  3. `~/.gk-harness/install.sh install`

  To return: `git -C ~/.gk-harness switch main`, then `install.sh install`.
- [ ] **Step 7: Rewrite the memory notes** in a fresh session after the switch, never mid-session. These are the three notes that contradict the new policy: `token-efficiency-posture.md`, `three-seat-policy.md` and `fable-credit-fallback.md`. Each is rewritten to the new facts: an Opus 5.5 medium default seat, routes from `routes.json`, `gkh fable off`, and no profile. Update the `MEMORY.md` lines.
- [ ] **Step 8: Close out.** Carl confirms he has read the new policy. Publish the switch report.
- [ ] **Step 9: Run Tasks 14 and 15 of the October plan** (laptop, cloud) against v2.0.0, as that plan's own tasks.
