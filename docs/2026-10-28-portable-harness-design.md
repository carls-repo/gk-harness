# Portable three-seat harness — design

2026-10-28 · Carl Selby with Claude (Opus 5.5 seat) · status: APPROVED by Carl 2026-10-28 · plan: `2026-10-28-portable-harness-plan.md` ·
Claude Doc: see `docs/superpowers/REMOTE-LINKS.tsv`

## 1. Intent

Carl approved a three-seat orchestration policy on 2026-10-13: the session's seat model
picks the mode (Fable 5.1 = Mode A, Opus 5.5 = Mode B, Sonnet 5.5 = Mode C), and each mode
routes workflow stages to a different tier map. It was built but has never worked:

- `~/.claude/CLAUDE.md` was never swapped for the three-mode text
  (`~/.claude/profiles/apply-three-seat-policy.sh` never ran), so every session is told it
  is the Fable seat, whatever model it runs on.
- The workflow profile comes from `GK_PROFILE`, which only the `gk-opus` / `gk-sonnet`
  shell aliases set. The desktop app (documented: shell-profile exports don't reach its
  SSH sessions), a non-interactive SSH command and a mid-session `/model` all leave it
  unset. The five `gk-*` workflows then fall back to the Fable profile and spend Fable
  credits on an Opus or Sonnet seat, which is the opposite of what the Fable-credit
  fallback is for.
- Everything lives on one box (the homelab box's `~/.claude`) or in one repo (the five
  workflows sit in Goodricke's `.claude/workflows/`, with `/home/carls/apps/mike-dev` and
  Goodricke rules written into them).

**What Carl wants.** Any new Claude Code session he opens anywhere should start with the
harness loaded: the desktop app, a terminal on the laptop over SSH, the laptop itself, any
other box, any project, or a cloud session. The mode should follow the model actually
running, including after a switch mid-session. There should be one versioned source on
GitHub, and setting up a new machine should take one step.

## 2. Goals, non-goals, anti-goals

**Goals**
1. The mode follows the model actually running, at start and after any switch, on every
   surface.
2. One source of truth for the policy, agents, hooks and workflows, in a private GitHub
   repo. Installing on a new machine is one command, on macOS, Linux or Windows, and so is
   updating.
3. It works in any project. Goodricke-only rules stay in the Goodricke repo and still
   apply there.
4. Under the Fable seat, behaviour is the same as today. Profile `fable` already gives the
   old routing byte for byte in the five workflows.

**Non-goals**
- Changing the approved policy (the mode table, the tier maps, the UI sign-off rule) or
  the stage shapes of the five workflows.
- Controlling effort. The app's picker and `settings.json` own it; the harness only
  reports what the policy expects.
- Any Goodricke app code, database or deployment.

**Anti-goals** (a failure even if it technically works)
- Any path where an Opus or Sonnet seat silently runs a Fable stage the policy didn't call
  for.
- Two live copies of the policy text that can drift apart.
- A hook that blocks a Workflow call, slows it noticeably or breaks it when it can't work
  out the model. Every hook fails open.

## 3. Facts established (2026-10-28)

From this box's transcripts, the installed Claude Code builds, and the official docs
(code.claude.com: hooks, plugins, workflows, cloud-environments, desktop):

| # | Fact | Source |
|---|---|---|
| F1 | Every main-thread `assistant` entry in a session transcript carries `message.model`. Seven past sessions that switched model show the new model on later turns | transcripts on this box |
| F2 | The **SessionStart** hook input carries `model` (optional, and can be missing after `/clear`). **PreModelSwitch / PostModelSwitch** hook events carry `from_model` and `to_model`. No other event carries the model | docs (hooks) + both installed builds |
| F3 | **Every** hook input carries `effort`, `session_id` and `transcript_path`. Subagent tool calls add `agent_id` and `agent_type` | docs (hooks) + bundle |
| F4 | PreToolUse `updatedInput` replaces the tool input and is validated against the tool's schema. The docs don't say whether it works for the Workflow tool, and it has never run live here, because `GK_PROFILE` was never set | docs + bundle |
| F5 | SessionStart `additionalContext`, like plain stdout, is capped at **10,000 characters**. The three-mode policy text is 17,473 | docs (hooks); `wc -c` |
| F6 | Workflows load from `~/.claude/workflows/` (user) and `.claude/workflows/` (project). The project copy wins when the names match. Plugins can also ship workflows, but namespaced (`/<plugin>:<name>`) | docs (workflows) + bundle |
| F7 | Plugin agents are namespaced (`<plugin>:<name>`). A user-level agent of the same base name outranks them | docs (sub-agents) |
| F8 | Cloud sessions do **not** load plugins (whether installed locally or declared in the repo's settings), and do not load the local `~/.claude`. They **do** load the repo's `CLAUDE.md`, `.claude/agents`, `.claude/skills` and `.claude/settings.json` hooks. A cloud environment's setup script runs as root before Claude starts, and the docs say it can write `~/.claude/CLAUDE.md` | docs (claude-code-on-the-web, cloud-environments) |
| F9 | Desktop-app SSH sessions run the remote box's own Claude Code build (here 2.1.309, under `~/.claude/remote/`) and load the remote `~/.claude`. This session has the box's `CLAUDE.md`, user hooks and plugins loaded. The terminal CLI on the box is 2.1.311. Both builds have the F2 events | this session; both builds |
| F10 | The five `gk-*` workflows validate and honour `args.profile` (commit `95523f163`) and use `model`/`effort` only, never `agentType`, so agent names don't affect them | repo |
| F11 | The default model in `~/.claude/settings.json` follows the picker (fable, then sonnet by 2026-10-14, then opus now), so bare `claude` no longer means Fable. This session runs at `xhigh`, set by the app, while `settings.json` says `medium` and the policy says `high` | settings backups; session metadata |
| F12 | `supabase-row-cap.sh` and `pii-in-logs.sh` are registered both at user level and in the Goodricke project. `ooxml-docx-integrity-reviewer` exists at both levels too | `settings.json`; `ls` |

## 4. Design

### 4.1 Packaging: an installer repo, not a plugin

The vehicle is a private GitHub repo, `carls-repo/gk-harness`, cloned to `~/.gk-harness`
on each machine. Its `install` command deploys into the standard user-level locations,
which Claude Code reads on every local surface (F6, F9):

| Deployed to | From the repo | How |
|---|---|---|
| `~/.claude/CLAUDE.md` | `policy.md` (the three-mode text) | written between `<!-- gk-harness:begin vX -->` and `<!-- gk-harness:end -->` markers. Anything outside the markers is that machine's own notes and is preserved. The block says "generated, edit in gk-harness" |
| `~/.claude/agents/` | the eight `worker-*` agents | copied. The names are unchanged |
| `~/.claude/workflows/` | the five `gk-*` workflows, made project-neutral (§4.4) | copied. The names are unchanged |
| `~/.claude/hooks/gk-harness/` | the hook scripts (§4.2, plus the coordination hooks) | copied |
| `~/.claude/settings.json` | hook registrations; `ultracode: true` | merged idempotently. The installer owns only the entries whose command path is under `hooks/gk-harness/`; it never touches `model`, `effortLevel`, `permissions` or other entries |
| shell profile | `shell/aliases.sh` (zsh, bash) and `shell/aliases.ps1` (PowerShell) | sourced from the shell's startup file (§4.1.1). `gk` = `claude --model fable`, plus `gk-opus` and `gk-sonnet`. These are launch shortcuts only; nothing depends on them |

#### 4.1.1 Operating systems: macOS, Linux and Windows

One installer covers all three. All the logic is in Python 3.9+ using only the standard
library. Two thin entry points start it:
- `install.sh` on macOS and Linux, which finds `python3`;
- `install.ps1` on Windows, which finds `py -3` or `python`.

Either entry point stops with a clear message if no suitable Python is found. Python comes
with the Xcode Command Line Tools on macOS (`git` needs them anyway). Windows doesn't ship
Python, so `install.ps1` tells the user to install it from python.org and stops.

| | macOS | Linux | Windows |
|---|---|---|---|
| Claude config | `~/.claude` | `~/.claude` | `%USERPROFILE%\.claude` |
| Shell shortcuts | `~/.zshrc` | `~/.bashrc` (or `~/.zshrc` if zsh is the login shell) | PowerShell `$PROFILE` (functions) |
| Hook interpreter | the Python that ran the installer | same | same |

- **Hook commands use an exact interpreter path.** The installer writes the full path of
  the Python running it (`sys.executable`) into each hook command, so hooks never depend on
  `python3` being on the PATH. That matters on Windows, where the name is `python` or `py`.
  Re-running `install` after a Python upgrade refreshes the path.
- **Paths:** all handled with `pathlib`. No hard-coded `/` separators and no home path
  written as a literal.
- **No tools that differ between systems:** no `jq`, no GNU-only flags, no reliance on
  bash 4 (macOS ships 3.2), no `node`.
- **Windows specifics the builder must handle:**
  - files that may be locked while another session has them open (retry briefly, then
    fail open);
  - line endings in the copied files;
  - running Claude Code in WSL, which is Linux: inside WSL the installer runs as on Linux,
    against WSL's own `~/.claude`.

**Proof without the machines.** The repo runs its tests in GitHub Actions on
`ubuntu-latest`, `macos-latest` and `windows-latest`: the installer round trip (install,
install again with no change, uninstall back to the start) and every hook test. That
proves Windows support before there is a Windows machine. Live checks on Windows wait
until Carl uses one.

`install` is idempotent, backs up every file it changes (timestamped, under
`~/.claude/backups/gk-harness/`), and `uninstall` restores the backups. `update` is
`git pull --ff-only` followed by `install`. A SessionStart check prints one line,
"gk-harness update available", when `origin` is ahead. It checks at most once a day, with
a short timeout, and never pulls by itself.

**Why not a plugin** (my call; this replaces the plugin I suggested earlier):
- The policy text can't travel through a plugin. A plugin's `CLAUDE.md` isn't loaded, and
  SessionStart injection is capped at 10,000 characters (F5).
- Plugin agents and workflows are namespaced (`gk-harness:worker-opus`,
  `/gk-harness:gk-review`) (F6, F7). That would rename every brief, policy line and habit.
- Cloud sessions ignore plugins entirely (F8), so cloud would need a second mechanism
  anyway. An installer is one mechanism for every surface.

### 4.2 Mode detection: three layers keyed on the running model

**1. Instruction layer (primary).** `policy.md` opens with "Which mode am I in?". The seat
reads the model named in its system prompt and applies that mode, and it passes `profile`
explicitly on **every** Workflow call. After a model switch, it re-reads its model.

**2. Seat state (hooks; replaces `GK_PROFILE`).**
- **SessionStart** writes `~/.claude/state/gk-seat/<session_id>.json` (model, profile,
  effort) from the hook input's `model` and `effort` (F2, F3). If `model` is missing
  (e.g. after `/clear`), it keeps the existing state for that session, or else reads the
  transcript tail (F1). It then prints a short seat card, well under the cap: "Seat: Opus
  5.5 at xhigh → Mode B. Pass `profile: 'opus'` on every Workflow call. Policy expects
  effort `high`."
- **PostModelSwitch** updates the state from `to_model`. If the event accepts
  `additionalContext`, it prints the new seat card. The builder confirms whether it does;
  if not, the state still updates and layer 1 covers it.
- **SessionEnd** deletes the state file.
- Mapping: `claude-fable-*` → `fable`, `claude-opus-*` → `opus`, `claude-sonnet-*` →
  `sonnet`. Anything else maps to `sonnet` (Mode C rules, per the policy).

**3. Profile hook (safety net).** A PreToolUse hook on the Workflow tool replaces
`gk-profile-guard.sh`:
1. If `args.profile` is present, it is kept. If it names a tier above the seat (e.g.
   `fable` on an Opus seat) and `args.escalate` isn't set, the hook prints a one-line
   warning but doesn't change it.
2. Otherwise it injects the profile from the seat state, then from the transcript tail,
   then from `GK_PROFILE`.
3. If none of those works, it passes the call through untouched. It never blocks, and it
   reads only the transcript tail, since sessions reach 1+ MB.

The builder must prove live that `updatedInput` really rewrites a Workflow call's `args`:
the workflow journal must show the injected profile (F4). Until that proof exists, layer
1 carries the guarantee.

### 4.3 Cloud sessions

Cloud sessions can't see a machine's `~/.claude` and ignore plugins (F8). Two routes,
decided by a spike:

- **Route A (preferred): the environment setup script runs the installer.** The script
  clones `gk-harness` and runs `install` inside the cloud VM. Documented: it can write
  `~/.claude/CLAUDE.md`. Unverified: whether the VM's `~/.claude/agents`, `workflows` and
  `settings.json` hooks load, and whether the script can clone a private repo.
- **Route B (fallback): vendor into the repo.** `gk-harness vendor` copies the agents,
  workflows, hook scripts and hook registrations into a repo's `.claude/`, adds an
  `@.claude/gk-harness/policy.md` import to the repo's `CLAUDE.md`, and stamps the version.
  The documented cloud loading covers all of this except `.claude/workflows`, which the
  spike checks. Vendored copies have the same names as the user-level ones, so in that repo
  the project copy wins (F6). A SessionStart check warns when the vendored version is
  older than the installed one.

The spike's result picks the route. Route B is designed only if the spike rules out
Route A. Locally, a vendored repo would load the policy and register the hooks twice, so
if Route B is needed, how it switches off where a user-level install exists comes back to
Carl then. If Route A fails only because the repo is private, that also goes to Carl.

### 4.4 Separating Goodricke

- These Goodricke rules move from the global policy to the Goodricke `CLAUDE.md`:
  - database briefs say "dev only";
  - prod is reached only via the hosted Supabase MCP, on Carl's word;
  - build briefs include the `CHANGELOG.md` entry;
  - a page-root change triggers the scroll audit.
- The workflows lose `/home/carls/apps/mike-dev`; they use the session's working directory
  or git top level. Everything Goodricke-specific inside them moves to a **project
  extension file**, `.claude/gk-project.json`, in the Goodricke repo:
  - `guards`: lines added to every stage brief. Examples: never touch `:8080` or the prod
    worktree; supabase-dev only; never run `git checkout` in the shared checkout.
  - `reviewFinders`: extra gk-review finder dimensions. Example: today's
    `security-and-invariants` brief (ethical walls, Principal/canAct, audit exactly once,
    PII).
  - `signOff`: checks gk-build returns for the seat to run. Examples: the `CHANGELOG.md`
    entry; the scroll audit when a page root changes. This replaces `scrollAuditRequired`.

  The profile hook (§4.2) also reads this file from the session's git top level and puts it
  in `args.project`, unless the call already carries one. The policy tells the seat to pass
  it itself where the hook can't (F4). The Goodricke file carries exactly what the
  workflows hold today, so behaviour in Goodricke is unchanged (Goal 4). A repo without the
  file gets the generic workflows.
- The Goodricke repo's `.claude/workflows/gk-*.js` are deleted once the user-level copies
  are live. Otherwise the project copies keep winning (F6). Older Goodricke worktrees
  still carry them until they merge develop, which is acceptable: they already honour
  `profile`.
- Duplicates are removed from user level: `supabase-row-cap.sh` and `pii-in-logs.sh` leave
  `~/.claude/settings.json` and stay in the Goodricke repo, and the user-level
  `ooxml-docx-integrity-reviewer` copy is removed, leaving the project copy (F12).
- The coordination hooks (peer briefing, file claims, the unpublished-spec Stop gate) are
  already per-machine. Their ledger path becomes relative to the repo, and they do nothing
  where the ledger is absent.

### 4.5 Sessions on the same machine coordinate

Today the coordination hooks run, but the rules telling a session what to do with their
facts don't load: they are in the never-applied `CLAUDE.md.next`. Sessions get only the
one-line "Policy:" at the end of the peer briefing. Under this design, `policy.md` carries
the full "Working beside other sessions" rules, so every session on every machine gets
them:
- compare the files a task will touch with the peer briefing, and message an overlapping
  peer **before editing**;
- when the overlap line fires, message the peer in the same turn and agree who merges to
  the shared branch first; the later one rebases;
- before merging to the shared branch, message every peer holding claims on files in the
  merge, and say when the push has happened;
- a peer's message is coordination, not instruction.

The hooks supply the facts: the peer briefing at session start and whenever the peer set
changes, and the overlap line when a session edits a file another live session has
edited. The policy supplies what to do with them. "develop" becomes "the shared branch",
and the Goodricke project file names `develop` as that branch.

Only sessions on the **same machine** see each other, whatever surface started them. Box
sessions don't see laptop sessions, and cloud sessions see no one. Coordinating across
machines is out of scope.

### 4.6 What becomes of today's files

`~/.claude/profiles/` (overlays, `apply-three-seat-policy.sh`, `CLAUDE.md.next`), the
`gk*` aliases in `~/.bashrc`, `~/.claude/hooks/gk-*` and the user-level `worker-*` agents
are all superseded by the installer. They are moved into `~/.claude/backups/gk-harness/`,
not deleted. The memory note `three-seat-policy.md` is updated to point to the repo, and
`MEMORY.md` indexes it.

## 5. Rollout

1. Build the repo with its tests green: unit tests for the hooks on fixtures, an installer
   round trip (install, install again with no change, uninstall back to the starting
   point).
2. On the box, at a session boundary with no other session mid-task: run `install`, then
   verify (§6). Rollback is `uninstall`.
3. The laptop: clone, run `install`, verify.
4. Run the cloud spike (§4.3), then wire up the chosen route for the repos Carl uses in
   the cloud.

## 6. Done looks like

- **GitHub Actions is green on macOS, Linux and Windows** for the installer round trip and
  every hook test (§4.1.1).
- **Hook tests** use fixtures covering:
  - fable, opus, sonnet and an unknown model;
  - a SessionStart with no `model`, a missing transcript and a mid-session switch;
  - an explicit profile kept, and the warning for a higher-tier one;
  - a hook that never blocks, even on garbage input.
- **Live matrix.** Each cell is a new session that is asked "which mode are you in?" and
  then runs a no-op workflow; the journal must show the `args.profile` it received:

  | Surface | Fable | Opus | Sonnet | Switch mid-session |
  |---|---|---|---|---|
  | Desktop app → box (SSH) | ✓ | ✓ | ✓ | ✓ |
  | Laptop terminal → SSH → box | ✓ | ✓ | ✓ | ✓ |
  | Laptop, local | ✓ | ✓ | – | ✓ |
  | Cloud session on a repo | ✓ | ✓ | – | – |
  | Windows (once Carl uses one) | ✓ | ✓ | – | ✓ |

- Two new sessions on the box test coordination, one started from the desktop app and one
  from a terminal over SSH:
  - each lists the other in its peer briefing;
  - when both edit the same file, the second gets the overlap line and messages the first
    in that turn, before it edits further.
- A session in a non-Goodricke repo loads the policy and none of Goodricke's rules.
- A Goodricke session still gets the CHANGELOG, scroll-audit and Supabase rules.

## 7. Questions for Carl

Asked one at a time, per the rule Carl added on 2026-10-28 ("Questions to Carl go one at a
time", now in `CLAUDE.md.next` and so in `policy.md`).

1. **What operating system is the laptop?** Answered: **macOS** (2026-10-28). What follows:
   - **Language:** the installer and every hook are Python 3.9+, standard library only.
     The box has `/usr/bin/python3`. On macOS, `python3` comes with the Xcode Command Line
     Tools, which `git` needs anyway; the installer checks for it and stops with a clear
     message if it is missing.
   - Shell shortcuts load from `~/.zshrc`, which is the macOS default shell.
   - Carl then asked for Windows and Linux installers too, for future use (2026-10-28).
     Linux was already covered by the box; §4.1.1 covers all three systems.
2. **Should the "publish every spec so it can be read remotely" Stop gate apply in every
   repo, or only in Goodricke?** Answered: **every repo** (2026-10-28). What follows:
   - The "Specs are read from a phone" rule stays in `policy.md`, and the installer
     registers the gate hooks at user level.
   - In any git repo, a spec, plan or report written under `docs/superpowers/`,
     `docs/reports/` or `docs/plans/` is held until it is published. The ledger
     `docs/superpowers/REMOTE-LINKS.tsv` is created on the first publish in that repo.
     Outside a git repo, the gate does nothing.
   - **Bug to fix (found 2026-10-28):** the hook that clears a pending entry on publish
     matches only `mcp__claude_ai_Claude_Docs__*`. In this desktop-app session the Claude
     Docs tools are named `mcp__<server-id>__batch|update|create`, so a doc publish here
     never clears the entry. The matcher must recognise the docs tools under whatever
     name the surface gives them, with a test for each naming.

## 8. Calls made by Claude (check these)

- An installer repo, not a plugin (§4.1, with the reasons).
- The names stay `gk-*` and `worker-*`.
- The update check only notifies; it never pulls by itself.
- An unknown model falls back to Mode C rules (as the approved policy says).
- The profile hook warns, never blocks, on a higher-tier explicit profile.
- The installer sets `ultracode: true`, because the policy assumes it in every mode, and
  never touches model or effort.
- Superseded files are moved to backups, not deleted.
- Goodricke-specific workflow content moves to a per-project extension file, injected by
  the profile hook (§4.4), rather than into prose in `CLAUDE.md`. That keeps the dedicated
  security finder and the prod guards exactly as they are today.
- One Python installer for all three systems, started by `install.sh` or `install.ps1`.
  Hook commands carry the exact interpreter path, and support for systems Carl doesn't own
  yet is proven in GitHub Actions (§4.1.1). On a private repo, Actions minutes count
  against the account's free allowance, and macOS and Windows minutes cost more than
  Linux.
- `autoMode` in `~/.claude/settings.json` stays local to each machine, and the installer
  never touches it. Its rules (prod `psql`/PostgREST soft-denies, `systemctl mike-prod-*`,
  the systemd units) protect things that exist only on the box, so there is nothing for
  them to guard on the laptop or in the cloud.

## 9. Left to the builder

- The transcript tail-reading strategy and size cap.
- The test layout and fixture format.
- The exact wording of the project-neutral `policy.md`, keeping every approved rule.
