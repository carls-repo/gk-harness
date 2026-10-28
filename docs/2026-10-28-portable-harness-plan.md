# Portable three-seat harness — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `carls-repo/gk-harness`, an installer repo that puts the three-seat policy,
the worker agents, the five `gk-*` workflows and the coordination hooks into any machine's
`~/.claude`. The mode follows the model actually running, on macOS, Linux and Windows.

**Architecture:** The repo is cloned to `~/.gk-harness`, and a Python installer copies its
files into the standard user-level locations. It merges the hook registrations into
`settings.json` and writes the policy between markers in `~/.claude/CLAUDE.md`. Hooks record
the seat's model in a per-session state file and inject `profile` and `project` into `gk-*`
Workflow calls. Goodricke's own rules move to `.claude/gk-project.json` in the Goodricke
repo.

**Tech Stack:** Python 3.9+ standard library (installer and hooks); Node 20 `node:test`, used
only to test the workflow scripts; GitHub Actions (ubuntu, macos, windows).

**Spec:** `docs/superpowers/harness/2026-10-28-portable-harness-design.md` (Goodricke
repo; approved by Carl on 2026-10-28; Claude Doc https://claude.ai/artifact/3p5fuGo1AkZrtxPJbkdQaS).
Task 1 copies it into the new repo's `docs/`.

## Global Constraints

- Installer and hooks: Python **3.9+**, standard library only. No `jq`, no `node`, no GNU-only
  flags and no bash-4 features, at runtime.
- Every hook exits 0 on every input, never blocks a tool call, and fails open. No PreToolUse
  hook ever sets `permissionDecision`.
- Repo `carls-repo/gk-harness` (private), cloned to `~/.gk-harness` (`%USERPROFILE%\.gk-harness`
  on Windows). Development happens in that clone.
- Names are unchanged: `gk-review`, `gk-diagnose`, `gk-build`, `gk-research`, `gk-design`,
  plus `worker-haiku-low`, `worker-haiku`, `worker-sonnet`, `worker-opus`, `worker-opus-xhigh`,
  `worker-opus-max`, `worker-fable`, `worker-fable-max`.
- Policy markers: `<!-- gk-harness:begin v{VERSION} -->` … `<!-- gk-harness:end -->`.
- The installer owns a `settings.json` hook entry only when its command contains
  `hooks/gk-harness/` (after replacing `\` with `/`). It sets `ultracode: true`. It never
  touches `model`, `effortLevel`, `modelSettings`, `permissions`, `autoMode` or any entry it
  doesn't own, except the fixed legacy list in Task 8.
- Backups go to `~/.claude/backups/gk-harness/<UTC yyyymmddTHHMMSSZ>/`, with the manifest at
  `~/.claude/backups/gk-harness/manifest.json`.
- Seat state lives at `~/.claude/state/gk-seat/<session_id>.json`. The mapping is
  `claude-fable-*` → `fable`, `claude-opus-*` → `opus`, `claude-sonnet-*` → `sonnet`, and any
  other Claude model → `sonnet` (Mode C rules).
- SessionStart output stays under 10,000 characters. The seat card stays under 600.
- Profile `fable` plus the Goodricke project file gives the same per-stage `(model, effort)`
  routing as today's Goodricke workflows (Goal 4). Task 10 and Task 11 tests prove it.
- The update check runs at most once per 24 h and never pulls.
- Generic text says "the shared branch". `develop` appears only in the Goodricke project file.
- **Goodricke rules that bind any step touching the Goodricke repo:**
  - database work is dev only (supabase-dev MCP);
  - prod only via the hosted Supabase MCP, by the seat, on Carl's word;
  - never push to `upstream`; never push `main`;
  - never `git checkout` in the shared `mike-dev` checkout;
  - never run `npm run dev`.
- Questions to Carl go one at a time. API-token spend needs Carl's word first; the live
  matrix runs on subscription sessions. Fable cells run only on Carl's word, because the
  Fable-credit fallback is in force.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Test runs are
  foreground only, piped through `tail`.

## Review Focus

1. **Session liveness on macOS and Windows.** Today's `alive()` checks `/proc/<pid>`, which
   macOS doesn't have, so every live peer looks dead and its state is pruned. On Windows,
   `os.kill(pid, 0)` *terminates* the process. Expected: a live peer is reported live on every
   OS, and checking never harms it. Test: Task 2, `test_pid_alive_*`.
2. **Model strings in odd forms**: `opus[1m]`, `claude-opus-5-5[1m]`, Bedrock ids such as
   `us.anthropic.claude-opus-5-5-v1:0`, `default`, the `<synthetic>` model on transcript error
   entries, and subagent (sidechain) transcript entries. Expected: each family maps
   correctly. `default` and `<synthetic>` count as "not reported", and sidechain entries never
   decide the seat. Test: Task 3, `test_profile_for_model_table` and
   `test_transcript_skips_synthetic_and_sidechain`.
3. **Hook commands under a home path with spaces or backslashes** (`C:\Users\Carl Selby`).
   Expected: every registered command runs from the platform shell and from bash. Test:
   Task 8, `test_registered_commands_execute_with_space_in_home`.
4. **A hand-edited or invalid `settings.json`** (comments, a trailing comma, a truncated
   file). Expected: the installer stops before writing anything and names the problem. Test:
   Task 8, `test_invalid_settings_aborts_without_writes`.
5. **A locked file on Windows** (another session or antivirus holds the state file while
   `os.replace` runs). Expected: a brief retry, then the hook gives up quietly, exits 0 and
   leaves no `.tmp` files. Test: Task 2, `test_write_json_gives_up_cleanly`.

---

## File structure (gk-harness repo)

```
install.sh                 # macOS/Linux entry: finds python3 ≥3.9, runs install.py
install.ps1                # Windows entry: finds py -3 / python ≥3.9, runs install.py
install.py                 # puts the repo root on sys.path, calls gkh.cli.main()
gkh/__init__.py            # VERSION
gkh/paths.py               # claude_dir, harness root, shell rc choice per OS
gkh/manifest.py            # action journal for install/uninstall
gkh/settings_merge.py      # hook registrations, merge/strip, legacy entries
gkh/claude_md.py           # policy marker block
gkh/shell.py               # rc block for zsh/bash/PowerShell
gkh/legacy.py              # the fixed legacy list (Task 8)
gkh/cli.py                 # install | uninstall | update | status
hooks/gk_common.py         # portable primitives shared by every hook
hooks/gk_model.py          # model→profile, transcript tail, seat state, seat card
hooks/gk-seat.py           # SessionStart + PostModelSwitch
hooks/gk-profile.py        # PreToolUse on Workflow
hooks/gk-peers.py  gk-claim.py  gk-claims-end.py           # ported coordination
hooks/gk-remote-pending.py  gk-remote-clear.py  gk-remote-stop.py  gk-remote-skip.py
agents/worker-*.md         # the eight workers
workflows/gk-{review,build,design,research,diagnose,selftest}.js
policy.md                  # the three-mode policy, project-neutral
shell/aliases.sh  shell/aliases.ps1
tests/test_*.py            # unittest
tests/workflows/harness.mjs  tests/workflows/*.test.mjs
tests/workflows/golden/    # today's five Goodricke workflows, verbatim
tests/workflows/fixtures/goodricke.gk-project.json
docs/                      # design, plan, policy-coverage.md
.github/workflows/ci.yml   .gitattributes  .gitignore  README.md
```

**Execution note.** `gk-build` can't run these tasks, because it is pinned to
`/home/carls/apps/mike-dev` until Task 10 lands. Use Agent calls with the worker named on each
task (the Mode B map: `worker-sonnet` by default, `worker-opus` for the hard tail). A
reviewer runs after every task.

---

### Task 1: Repo skeleton, entry points, CI

Worker: `worker-sonnet`.

**Files:**
- Create: `install.sh`, `install.ps1`, `install.py`, `gkh/__init__.py`, `gkh/cli.py` (stub),
  `tests/__init__.py`, `tests/test_entry.py`, `.github/workflows/ci.yml`, `.gitattributes`,
  `.gitignore`, `README.md`, `docs/2026-10-28-portable-harness-design.md`,
  `docs/2026-10-28-portable-harness-plan.md`

**Interfaces:**
- Produces: `gkh.VERSION = "1.0.0"`; `gkh.cli.main(argv: list[str]) -> int`, with
  subcommands `install` (the default), `uninstall`, `update` and `status`. Task 8 fills them
  in; here each returns 0 after printing `not implemented`.

- [ ] **Step 1 (Carl, one action):** create an empty private repository
  `carls-repo/gk-harness` on github.com. `gh` isn't installed on the box; the box pushes over
  SSH (`git@github.com:`).
- [ ] **Step 2:** `git init ~/.gk-harness`, `git remote add origin git@github.com:carls-repo/gk-harness.git`,
  `git switch -c main`. Copy the design and this plan into `docs/`.
- [ ] **Step 3: Write the failing test** `tests/test_entry.py`:
  - `test_version_format`: `re.fullmatch(r"\d+\.\d+\.\d+", gkh.VERSION)`.
  - `test_install_py_runs_status`: `subprocess.run([sys.executable, "install.py", "status"])`
    has returncode 0.
  - `test_install_sh_rejects_old_python` (POSIX only): `PYTHON=/bin/false ./install.sh status`
    exits non-zero, and its stderr contains `Python 3.9 or newer is required`.
- [ ] **Step 4:** Run `python3 -m unittest discover -s tests 2>&1 | tail -5`. Expected: FAIL.
- [ ] **Step 5: Implement the entry points.**
  - `install.sh` honours `$PYTHON`, else uses `python3`. It checks
    `sys.version_info >= (3, 9)` and then `exec`s `"$PY" "$ROOT/install.py" "$@"`. It is POSIX
    `sh`, with no bashisms. On macOS (`uname` is `Darwin`), when no `python3` is found, the
  message adds `Run xcode-select --install, then run this again.`
  - `install.ps1` tries `py -3`, then `python`, with the same check and the same message. Its
    message adds `Install it from https://www.python.org/downloads/`.
  - `install.py` puts its own directory first on `sys.path` and exits with
    `gkh.cli.main(sys.argv[1:])`.
- [ ] **Step 6: Set up CI and repo metadata.**
  - `ci.yml` jobs:
    - `py`: matrix `os: [ubuntu-latest, macos-latest, windows-latest]`, Python `3.12`, plus
      ubuntu with `3.9`. Runs `python -m unittest discover -s tests -v`.
    - `wf`: ubuntu, Node 20. Runs `node --test tests/workflows/`, which is skipped while the
      folder is empty.
  - `.gitattributes`: `* text=auto eol=lf`, and `*.ps1 text eol=crlf`.
  - `README.md`: one install line per OS, then what the installer changes (the Task 8 table)
    and how to uninstall.
- [ ] **Step 7:** Run the tests. Expected: PASS.
- [ ] **Step 8: Commit** with the message `chore: skeleton, entry points, CI`.

### Task 2: Portable primitives (`hooks/gk_common.py`)

Worker: `worker-opus`. Today's hooks are already defensive. This task moves their shared
helpers here and makes the two Linux-only ones portable.

**Files:**
- Create: `hooks/gk_common.py`, `tests/test_common.py`
- Source to port: `~/.claude/hooks/gk-claim.py` (`git_identity`, `alive`),
  `gk-remote-clear.py` (`dir_lock`), `gk-peers.py` (`read_json`, `write_json`, `safe_sid`)

**Interfaces (produces; every hook imports these):**
- `claude_dir() -> pathlib.Path`: `$GK_CLAUDE_DIR`, else `Path.home() / ".claude"`.
- `safe_sid(v) -> str | None`: matches `^[0-9A-Za-z][0-9A-Za-z._-]{0,127}$`.
- `read_json(path) -> object | None`: returns None when the file is missing or invalid.
- `write_json(path, obj) -> bool`: writes a temp file in the same folder, then `os.replace`.
  On `PermissionError` it retries 5 times, 50 ms apart. On failure it removes the temp file
  and returns False.
- `locked(dir_path)`, a context manager:
  - on POSIX, `fcntl.flock` on the folder's own descriptor. This is today's behaviour, so old
    and new hooks exclude each other during the changeover.
  - on Windows, `msvcrt.locking` on `<dir>/.gk-lock`.
  - it waits up to 2 s, then raises `LockTimeout`.
- `pid_alive(pid) -> bool`:
  - on POSIX, `os.kill(pid, 0)`. `ProcessLookupError` means dead; `PermissionError` means
    alive.
  - on Windows, via `ctypes`: `OpenProcess(0x1000, False, pid)` then `GetExitCodeProcess`;
    `== 259` means alive. **Never `os.kill` on Windows.**
  - a non-int, a bool or a value ≤ 0 means dead.
- `git_identity(start) -> (common_dir, toplevel, branch) | None`: today's semantics (the
  unborn-branch fallback, 5 s timeout, `GIT_OPTIONAL_LOCKS=0`).
- `git_toplevel(start) -> pathlib.Path | None`.
- `rel_posix(path, root) -> str | None`: a `/`-separated relative path, or None when the path
  is outside `root`.
- `load_project(toplevel) -> (dict | None, str | None)`: reads `.claude/gk-project.json`.
  - returns `(data, None)` when the file is valid (a dict with `version == 1`);
  - returns `(None, None)` when the file is absent;
  - returns `(None, "<reason>")` when it is unreadable or invalid.
- `read_input() -> dict | None`, `emit(obj) -> None` (JSON to stdout),
  `run_hook(name, fn) -> NoReturn`:
  - `run_hook` calls `fn(read_input())`;
  - it writes `"<name>: <repr(error)>"` to stderr on any exception, `LockTimeout` included;
  - it always ends with `sys.exit(0)`.

- [ ] **Step 1: Write the failing tests:**
  - `test_pid_alive_self`: `pid_alive(os.getpid())` is True.
  - `test_pid_alive_dead`: start `sys.executable -c pass`, wait for it, then
    `pid_alive(p.pid)` is False.
  - `test_pid_alive_does_not_harm`: start a `sleep 5` child, call `pid_alive` on it 3 times,
    then `p.poll()` is still None. On Windows this catches the `TerminateProcess` trap.
  - `test_pid_alive_rejects` covers `None`, `True`, `0`, `-1` and `"12"`.
  - `test_write_json_roundtrip`.
  - `test_write_json_retries_then_succeeds`: patch `os.replace` to raise `PermissionError`
    twice; it returns True and the file holds the object.
  - `test_write_json_gives_up_cleanly`: `os.replace` always raises; it returns False, and the
    folder holds no `*.tmp`.
  - `test_locked_excludes`: a second `locked()` on the same folder from a child process waits
    until the first lock is released.
  - `test_locked_timeout`: the child holds the lock for 3 s; the parent gets `LockTimeout` in
    ≤ 2.5 s.
  - `test_rel_posix` covers inside, outside and a Windows-style separator.
  - `test_load_project_states` covers absent, valid, `version: 2`, a JSON array and broken
    JSON.
  - `test_run_hook_always_exits_zero`: `fn` raises; `SystemExit.code == 0`.
- [ ] **Step 2:** Run the tests. Expected: FAIL (import error).
- [ ] **Step 3:** Implement `hooks/gk_common.py` to the interfaces above.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5: Commit** with the message `feat(hooks): portable primitives shared by every hook`.

### Task 3: Model, transcript and seat state (`hooks/gk_model.py`)

Worker: `worker-sonnet`.

**Files:** Create `hooks/gk_model.py`, `tests/test_model.py`, `tests/fixtures/transcripts/*.jsonl`

**Interfaces:**
- Consumes: `claude_dir`, `read_json`, `write_json` (Task 2).
- Produces:
  - `profile_for_model(model) -> str | None`
  - `MODE = {"fable": "A", "opus": "B", "sonnet": "C"}`, `RANK = {"sonnet": 1, "opus": 2, "fable": 3}`
  - `in_lineup(model) -> bool`: the string names fable, opus or sonnet.
  - `model_from_transcript(path, max_bytes=262144) -> str | None`
  - `effort_of(data: dict) -> str | None`: a string `effort` is returned as is; a dict
    returns its `level` when that is a string; anything else returns None.
  - `seat_path(sid) -> Path`, `load_seat(sid) -> dict | None`
  - `save_seat(sid, model, profile, effort, source) -> bool`. The record is
    `{model, profile, effort, source, updated}`, with `updated` as an ISO UTC time.
  - `seat_card(model, profile, effort) -> str`
  - `resolve_profile(data: dict) -> str | None`: seat state, then the transcript tail, then
    `$GK_PROFILE` when it is one of `fable|opus|sonnet`, then None.

- [ ] **Step 1: Write the failing tests.**
  - `test_profile_for_model_table`, with exact pairs:

    | Input | Profile |
    |---|---|
    | `claude-fable-5-1` | `fable` |
    | `claude-opus-5-5` | `opus` |
    | `claude-sonnet-5-5` | `sonnet` |
    | `claude-opus-5-5[1m]` | `opus` |
    | `opus` | `opus` |
    | `fable` | `fable` |
    | `us.anthropic.claude-opus-5-5-v1:0` | `opus` |
    | `claude-haiku-5-5` | `sonnet` |
    | `claude-next-7` | `sonnet` |
    | `default` | `None` |
    | `""` | `None` |
    | `None` | `None` |
    | `<synthetic>` | `None` |
    | `42` | `None` |

    Rule: lowercase the input; `fable`, `opus` or `sonnet` as a substring gives that
    profile; else a substring `claude` or `haiku` gives `sonnet`; else None.
  - `test_transcript_last_assistant_model`: the fixture switches from opus to sonnet
    mid-file, so the result is `claude-sonnet-5-5`.
  - `test_transcript_skips_synthetic_and_sidechain`: the last entries are a `<synthetic>`
    assistant entry and an `isSidechain: true` sonnet entry, before them an opus entry, so
    the result is `claude-opus-5-5`.
  - `test_transcript_tail_only`: a 3 MB fixture whose only model entry is in the first 1 KB
    gives None, and the read takes under 200 ms.
  - `test_transcript_missing_or_garbage` gives None.
  - `test_seat_card_exact`:
    - `seat_card("claude-opus-5-5", "opus", "xhigh")` equals
      `gk-harness seat: claude-opus-5-5 at xhigh → Mode B (profile 'opus'). Pass profile: 'opus' on every Workflow call. The policy expects effort high.`
    - with effort `high`, the last sentence is absent;
    - with effort `None`, it reads `at unknown effort`;
    - `seat_card("claude-next-7", "sonnet", "high")` appends
      ` claude-next-7 is not in the lineup, so Mode C rules apply; say so to Carl in your first reply.`;
    - `seat_card(None, None, None)` equals
      `gk-harness seat: the model was not reported. Read the model named in your system prompt, apply that mode, and pass profile explicitly on every Workflow call.`;
    - every card is under 600 characters.
  - `test_resolve_profile_order`: state beats transcript, transcript beats env, and with
    nothing set the result is None.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - The transcript reader seeks to `max(0, size - max_bytes)`, drops the first partial line
    and walks the lines in reverse.
  - It accepts an entry with `type == "assistant"`, `isSidechain` not True, and a string
    `message.model` for which `profile_for_model` is not None.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5: Commit** with the message `feat(hooks): model-to-profile mapping, transcript tail, seat card`.

### Task 4: Seat hook (`hooks/gk-seat.py`)

Worker: `worker-sonnet`.

**Files:** Create `hooks/gk-seat.py`, `tests/test_seat_hook.py`

**Interfaces:**
- Consumes: Tasks 2 and 3. It reads `~/.claude/gk-harness.json`
  (`{version, root, python, installed_at}`, written by Task 8) for the update check.
- Produces:
  - the state file;
  - SessionStart and PostModelSwitch stdout of the form
    `{"hookSpecificOutput": {"hookEventName": <event>, "additionalContext": <card>}}`;
  - the update stamp `~/.claude/state/gk-harness-update.json` (`{checked: ISO, available: bool}`).

- [ ] **Step 1: Write the failing tests.** Each runs the hook as a subprocess with
  `GK_CLAUDE_DIR` set to a temp folder.
  - `test_start_with_model`: the input has `model: claude-opus-5-5` and `effort: xhigh`. The
    state holds `profile: opus`, and the card is exactly the Task 3 opus/xhigh card.
  - `test_start_without_model_keeps_state` (`source: clear`, existing state sonnet) gives
    sonnet.
  - `test_start_without_model_reads_transcript` gives the transcript's profile.
  - `test_start_nothing_known` prints the "not reported" card and writes no state.
  - `test_start_model_default_is_treated_as_missing`.
  - `test_switch_updates_state`: PostModelSwitch with `to_model: claude-sonnet-5-5` gives
    state sonnet and a card with `hookEventName: PostModelSwitch`.
  - `test_other_events_noop`.
  - `test_garbage_input`: empty input, `[]`, `{"session_id": "../x"}` and binary noise each
    exit 0 with no stdout.
  - `test_output_under_cap`: under 10,000 characters.
  - `test_update_check_once_a_day`: point `root` at a temp git repo whose `origin` (a local
    bare repo) is one commit ahead. The first start prints
    `gk-harness update available: run <root>/install.sh update`; on Windows the command is
    `install.ps1 update`. A second start within 24 h runs no `git` (patch with a recorder) and
    prints the line from the stamp.
  - `test_update_check_offline`: `origin` is unreachable. The stamp's `available` is false,
    and the call finishes in ≤ 4 s.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - SessionStart: input `model` (when it maps), else the existing state, else the transcript.
    Save when the profile is known. The card, plus the update line when there is one, goes
    out as `additionalContext`.
  - The update check runs only when `source == "startup"` and the stamp is older than 24 h:
    1. `git -C <root> ls-remote origin HEAD`, with a 3 s timeout;
    2. if `git cat-file -e <hash>` fails, or `git merge-base --is-ancestor <hash> HEAD`
       fails, an update is available.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5: Commit** with the message `feat(hooks): seat state and seat card at start and on model switch`.

### Task 5: Profile hook (`hooks/gk-profile.py`)

Worker: `worker-opus`. It replaces `~/.claude/hooks/gk-profile-guard.sh` (a bash and node
version of the same idea). Port that script's `args`-shape handling.

**Files:** Create `hooks/gk-profile.py`, `tests/test_profile_hook.py`

**Interfaces:**
- Consumes: `resolve_profile`, `RANK`, `load_seat` (Task 3); `git_toplevel`, `load_project`
  (Task 2).
- Produces:
  - only when something changes:
    `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": {...tool_input, "args": <new>}}}`;
  - a warning is added as `hookSpecificOutput.additionalContext`, and also as top-level
    `systemMessage`;
  - no `permissionDecision`, ever.
- Injected `args.project` shape:
  `dict(<gk-project.json contents or {}>, root=<git toplevel as posix>)`.

Rules:
1. Act only on `gk-*` calls: `tool_input.name` matches `^gk-[a-z0-9-]+$`, or the basename of
   `tool_input.scriptPath` matches `^gk-[a-z0-9-]+\.js$`. Pass any other Workflow call
   through untouched. (My call: injecting `profile` into a bespoke script's `args` could
   break a script that reads `args` some other way.)
2. `args` handling:
   - missing or null becomes `{}`;
   - a dict is copied;
   - a JSON-object string is parsed;
   - anything else (an array, a number, an unparseable string) passes through untouched.
3. Profile:
   - an explicit non-empty `args.profile` is kept;
   - if it ranks above the seat's resolved profile and `args.escalate` is not `true`, warn
     with:
     `gk-harness: this Workflow call asks for profile '{p}' above the seat ('{seat}') without escalate: true. The policy allows that only on Carl's word.`
   - otherwise inject `resolve_profile(data)` when it is not None.
4. Project:
   - if `"project"` is not in `args` and the session `cwd` has a git toplevel, inject the
     shape above;
   - if `load_project` returns a reason, still inject `{root}` and warn with
     `gk-harness: .claude/gk-project.json at {root} is unreadable ({reason}); the workflow runs without the project rules.`
5. Scratchpad: for `name == "gk-diagnose"` with no `args.scratchpad`, inject the hook input's
   `scratchpad_dir` when it is a string.

- [ ] **Step 1: Write the failing tests:**
  - `test_injects_profile_from_state`;
  - `test_injects_profile_from_transcript`;
  - `test_injects_profile_from_env`;
  - `test_no_source_passthrough`: with no project either, the output is empty;
  - `test_explicit_kept`;
  - `test_explicit_higher_warns_not_changed`;
  - `test_explicit_higher_with_escalate_silent`;
  - `test_non_gk_untouched`;
  - `test_scriptpath_gk_detected`;
  - `test_args_string_parsed`;
  - `test_args_array_untouched`;
  - `test_project_injected_with_root`: a temp git repo whose `.claude/gk-project.json` is
    `{"version": 1, "name": "Goodricke"}` gives `args.project.name == "Goodricke"` and the
    `root` equal to the repo;
  - `test_project_absent_gives_root_only`;
  - `test_project_invalid_warns`;
  - `test_project_explicit_kept`;
  - `test_scratchpad_for_diagnose_only`;
  - `test_never_permission_decision`: across all the cases above, the string
    `permissionDecision` never appears in stdout;
  - `test_garbage_input_exit_zero`;
  - `test_resume_call_keeps_other_fields`: `resumeFromRunId` and `scriptPath` are kept
    verbatim in `updatedInput`.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement to the rules.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5: Commit** with the message `feat(hooks): Workflow profile/project injection keyed on the seat`.

### Task 6: Coordination and publish-gate hooks, ported

Worker: `worker-opus`.

**Files:**
- Create, ported from `~/.claude/hooks/`: `hooks/gk-peers.py`, `gk-claim.py`,
  `gk-claims-end.py`, `gk-remote-pending.py`, `gk-remote-clear.py`, `gk-remote-stop.py`,
  `gk-remote-skip.py`
- Create: `tests/test_coord.py`, which ports every case in `~/.claude/hooks/gk-coord.test.sh`
- Create: `tests/test_remote.py`, which ports every case in `~/.claude/hooks/gk-remote.test.sh`

**Interfaces:**
- Consumes: Task 2 (`locked`, `pid_alive`, `rel_posix`, `write_json`, `load_project`,
  `run_hook`).
- Produces: the same state formats as today, under `state/gk-claims`, `state/gk-peers` and
  `state/gk-remote-pending`, so old and new hooks interoperate during the changeover.
- New: `gk-remote-clear.is_docs_publish(tool_name: str, tool_input: dict) -> bool`.

Changes from today (everything else is ported as is):
- `alive` → `pid_alive`; `dir_lock` → `locked`. Repo-relative paths go through `rel_posix`,
  so `SPEC_RE` and the claim paths match on Windows.
- `gk-peers`:
  - "on this box" becomes "on this machine";
  - `POLICY` becomes
    `Policy: message a peer (SendMessage) before editing a file it has claimed, when a hook warns of an overlap, and before merging to the shared branch{ (X)}.`,
    where ` (X)` is the session cwd's project `sharedBranch` when present;
  - the prune list adds `gk-seat`.
- `gk-claims-end` also deletes `state/gk-seat/<sid>.json`.
- `gk-remote-stop`'s skip command uses `shlex.quote(Path(sys.executable).as_posix())` and the
  sibling `gk-remote-skip.py` path (as posix), not `python3 ~/.claude/hooks/…`.
- `gk-remote-clear` replaces its exact-name check with `is_docs_publish`. It is true when the
  name matches `^mcp__.+__(batch|update|create)$` and either `tool_input.container` is a dict,
  or `tool_input.ref` is a dict and `"payload"` is in `tool_input`. Artifact and SendUserFile
  handling is unchanged.
- The publish gate runs in every git repo (design §7.2). The ledger
  `docs/superpowers/REMOTE-LINKS.tsv` is created on the first publish. §7.2 supersedes §4.4's
  "do nothing where the ledger is absent" for the gate.

- [ ] **Step 1: Write the failing tests.**
  - Port both bash suites case for case. For the live-session fixture use `os.getpid()`; for
    the dead one, a finished child's pid.
  - Add `test_is_docs_publish`. True for:
    - `mcp__claude_ai_Claude_Docs__batch` with a container;
    - `mcp__1a59c906-04da-521d-bda7-7f71b9f9e01c__batch` with a container;
    - the same server's `__update` with ref and payload;
    - `__create` with a container;
    - `mcp__plugin_docs_Claude_Docs__update` with ref and payload.

    False for:
    - `mcp__supabase-dev__create_branch`;
    - `mcp__x__update` with `{}`;
    - `mcp__903ad2ed-f15f-4ae3-b958-706c5e1b78d9__create_draft`.
  - `test_clear_on_desktop_docs_name`: an end-to-end pending → clear with the
    `mcp__1a59c906-…__batch` name appends a ledger row and empties the pending list.
  - `test_ledger_created_on_first_publish` in a repo with no `docs/superpowers/`.
  - `test_peers_policy_line_shared_branch`: without a project, the line ends
    `the shared branch.`; with a project file `{"version": 1, "sharedBranch": "develop"}` it
    ends `the shared branch (develop).`
  - `test_stop_skip_command_uses_running_python`.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Port the seven hooks onto `gk_common`.
- [ ] **Step 4:** Run the tests. Expected: PASS. The bash suites are no longer needed.
- [ ] **Step 5: Commit** with the message
  `feat(hooks): coordination and publish gate, portable; docs-tool matcher fixed`.

### Task 7: Agents and the project-neutral policy

Worker: `worker-opus` drafts; the seat reviews the coverage table line by line.

**Files:**
- Create: `agents/worker-*.md`, the eight files copied verbatim from `~/.claude/agents/`
- Create: `policy.md`, from `~/.claude/profiles/CLAUDE.md.next` (17,977 chars, the version with
  the 2026-10-28 one-question rule)
- Create: `docs/policy-coverage.md`, `tests/test_policy.py`

Edits to `CLAUDE.md.next` (line numbers as of 2026-10-28):
- **Lines 15–19:** "Mode is chosen at launch…". Replace with: the mode follows the running
  model; a seat card arrives at session start and after `/model`; the profile hook injects
  `profile` and `project` into `gk-*` calls as a safety net, but **the seat passes `profile`
  explicitly on every Workflow call**; `gk`, `gk-opus` and `gk-sonnet` are launch shortcuts
  only.
- **Lines 156–159** (dev-only database briefs, prod via the hosted Supabase MCP, CHANGELOG in
  build briefs, the scroll audit on a page root): remove. Task 12 carries them in Goodricke.
- **Line 99:** "judged on the diff plus the scroll audit…" becomes "judged on the diff plus
  the project's sign-off checks (`args.project.signOff`); any audit runs first (Haiku
  run-and-report) and its verdict goes into the sign-off brief".
- **Line 112:** "scroll audit" becomes "audits".
- **Line 137:** "6 on this 8-CPU box" becomes "min(16, CPUs−2) on this machine (6 on an 8-CPU
  machine)".
- **Lines 181, 190, 191 and 223:** "this box" becomes "this machine"; "develop" becomes "the
  shared branch (the project file's `sharedBranch`, e.g. `develop` in Goodricke)".
- **New short section "Project extension file"** after "Saved workflows":
  - `.claude/gk-project.json` carries `guards`, `reviewFinders` and `signOff` (the Task 10
    schema);
  - the hook injects it as `args.project`;
  - if a workflow's log says `Project: none` in a repo that has the file, read the file and
    pass `project` yourself;
  - gk-build's `signOff` result lists what the seat must run before sign-off.
- **Keep** "Specs are read from a phone", with the skip command written as
  `<python> ~/.claude/hooks/gk-harness/gk-remote-skip.py`. **Keep** "Questions to Carl go one
  at a time".

- [ ] **Step 1: Write the failing tests** `tests/test_policy.py`. These pin outcomes, not rule
  text:
  - `test_policy_fits_and_has_no_box_paths`:
    - `len(policy.md) < 40000`;
    - none of `/home/carls`, `mike-dev`, `:8080`, `supabase`, `CHANGELOG`, `scroll audit`
      appears in it;
    - the word `develop` appears only inside the `sharedBranch` example.
  - `test_agents_are_the_eight`: `agents/` holds exactly the eight names, and each file's
    frontmatter `model` is an alias (`haiku|sonnet|opus|fable`), never a dated id.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Write `policy.md` with the edits above, and copy the agents.
- [ ] **Step 4:** Write `docs/policy-coverage.md`: one row per rule or bullet in
  `CLAUDE.md.next`, giving where it lives now (a `policy.md` heading, Goodricke `CLAUDE.md`,
  or dropped with the reason). A row with no home is a defect.
- [ ] **Step 5:** Run the tests. Expected: PASS. The seat reads the coverage table and
  confirms that every row has a home.
- [ ] **Step 6: Commit** with the message `feat: project-neutral three-mode policy and the eight workers`.

### Task 8: Installer (`gkh/`)

Worker: `worker-opus`.

**Files:**
- Create: `gkh/paths.py`, `manifest.py`, `settings_merge.py`, `claude_md.py`, `shell.py`,
  `legacy.py`
- Modify: `gkh/cli.py`, `README.md`
- Create: `shell/aliases.sh`, `shell/aliases.ps1`, `tests/test_install.py`

**Interfaces:**
- Consumes: the repo's `hooks/`, `agents/`, `workflows/`, `policy.md` and `shell/`.
- Produces:
  - `~/.claude/gk-harness.json` = `{version, root, python, installed_at}`, as posix paths
    (read by Tasks 4 and 6);
  - `cli.main` with:
    - `install [--retire-legacy]`;
    - `uninstall`;
    - `update`: `git -C <root> pull --ff-only`, then `install`;
    - `status`: prints version, root, python, owned entries, and drift (owned files whose
      hash differs from the repo).

Deployment (the design's §4.1 table, made exact):

| Target | Source | Rule |
|---|---|---|
| `~/.claude/hooks/gk-harness/*.py` | `hooks/*.py` | copied |
| `~/.claude/agents/worker-*.md` | `agents/` | copied; an existing file of the same name is backed up first |
| `~/.claude/workflows/gk-*.js` | `workflows/` | copied, with the same backup rule |
| `~/.claude/CLAUDE.md` | `policy.md` | the marker block. Its first line inside is `<!-- generated by gk-harness: edit policy.md in the gk-harness repo, then run install -->`. If the file has no block and its first line is `# Model orchestration policy`, the whole file is the old policy: back it up and replace it with the block alone (otherwise there would be two live policies). If not, keep the content and append the block |
| `~/.claude/settings.json` | registrations below | merged; `ultracode: true` |
| shell rc | `shell/aliases.*` | a block between `# >>> gk-harness >>>` and `# <<< gk-harness <<<` that sources `<root>/shell/aliases.sh`, or `. "<root>/shell/aliases.ps1"` on Windows |

The rc file:
- macOS: `~/.zshrc`;
- Linux: `~/.zshrc` if `$SHELL` ends in `zsh`, else `~/.bashrc`;
- Windows: the `$PROFILE` path reported by `pwsh`, else `powershell`
  (`-NoProfile -Command $PROFILE`, 5 s timeout). If neither answers, print a notice and skip.

The aliases:
- `aliases.sh`: `gk='claude --model fable'`, `gk-opus='claude --model opus'`,
  `gk-sonnet='claude --model sonnet'`;
- `aliases.ps1`: functions of the same names that pass `@args` through.

Registrations. Each command is `"<python>" "<claude_dir>/hooks/gk-harness/<script>"`, both
paths as posix and both quoted, with `timeout: 10`:

| Event | Matcher | Scripts (in order) |
|---|---|---|
| SessionStart | — | `gk-seat.py`, `gk-peers.py` |
| PostModelSwitch | — | `gk-seat.py` |
| UserPromptSubmit | — | `gk-peers.py` |
| PreToolUse | `Workflow` | `gk-profile.py` |
| PostToolUse | `Edit\|Write\|MultiEdit\|NotebookEdit` | `gk-claim.py`, `gk-remote-pending.py` |
| PostToolUse | `mcp__.*__(batch\|update\|create)\|Artifact\|SendUserFile` | `gk-remote-clear.py` |
| Stop | — | `gk-remote-stop.py` |
| SessionEnd | — | `gk-claims-end.py` |

Legacy (`gkh/legacy.py`, a fixed list; each item acts only if it exists):
- **Settings entries** whose command names any of `gk-profile-guard.sh`, `hooks/gk-claim.py`,
  `hooks/gk-claims-end.py`, `hooks/gk-peers.py` or `hooks/gk-remote-{clear,pending,stop}.py`
  outside `hooks/gk-harness/`. They are removed on `install`, and each removal is recorded.
- **rc lines** matching `^alias gk(-opus|-sonnet)?=` are removed on `install`, and recorded.
- **Files**, moved only by `install --retire-legacy`, into `<backup>/legacy/` under the same
  relative path:
  - `~/.claude/hooks/gk-profile-guard.sh` and `gk-profile-guard.test.sh`;
  - `~/.claude/hooks/gk-{claim,claims-end,peers,remote-clear,remote-pending,remote-skip,remote-stop}.py`;
  - `~/.claude/hooks/gk-{coord,remote}.test.sh`;
  - `~/.claude/profiles/`.

  The files are kept in place until then, because sessions that started before the install
  may still call them.

Manifest. An action journal, appended on each install:
- action types: `file`, `settings`, `claude_md`, `shell_rc`, `legacy_move`;
- each records the prior state (absent, a backup path, or the removed JSON entries and the
  prior `ultracode` value) and the `sha256` of what was written.

`uninstall` replays the actions in reverse:
- if the file's current hash equals the recorded one, it restores the prior state;
- otherwise it reverses surgically:
  - in settings: drop the owned entries, re-add the removed legacy entries, restore
    `ultracode`;
  - in `CLAUDE.md` and the rc file: strip the block;
  - for a copied file that has changed since: leave it and warn
    `changed since install, left in place: <path>`.

A second `install` with nothing changed writes nothing and creates no backup folder.

Preflight, before the first write:
- Python ≥ 3.9;
- every source file present;
- `settings.json` parses as strict JSON, or is absent;
- no path contains `"`.

On failure: exit 2 with `gk-harness: <problem>; nothing was changed.`

- [ ] **Step 1: Write the failing tests** `tests/test_install.py`. All run against a temp
  `HOME` and `GK_CLAUDE_DIR`.
  - `test_round_trip_fresh`: install, install again, uninstall. After the second install the
    tree is byte-identical to the first and there is no new backup folder. After uninstall the
    tree equals the starting tree, apart from `backups/gk-harness/`.
  - `test_round_trip_legacy_box`: a fixture shaped like today's box:
    - old-policy `CLAUDE.md`;
    - `settings.json` with today's hooks section verbatim, plus `autoMode`, `permissions` and
      `model`;
    - the old hook files, `profiles/`, and a `.bashrc` with the three aliases.

    After install:
    - the `CLAUDE.md` block is the only policy;
    - the legacy entries are gone;
    - `autoMode`, `permissions`, `model`, `effortLevel` and `modelSettings` are byte-equal;
    - the `supabase-row-cap.sh` and `pii-in-logs.sh` entries are untouched (Task 13 removes
      them by hand);
    - the old files are still present.

    `--retire-legacy` moves them. Uninstall restores the fixture exactly.
  - `test_machine_notes_preserved`: a `CLAUDE.md` with local notes keeps them outside the
    block, through install and uninstall.
  - `test_invalid_settings_aborts_without_writes`: `settings.json` holding `{"a": 1,}`, or
    `// c\n{}`, or truncated JSON. Exit code 2, the stderr names `settings.json`, and every
    file's mtime and hash are unchanged.
  - `test_registered_commands_execute_with_space_in_home`:
    - `HOME` is `<tmp>/Carl Selby`. Install, then run each registered command string with
      `subprocess.run(cmd, shell=True, input=<fixture>)`;
    - where `shutil.which("bash")` exists, also run `bash -c cmd`;
    - each exits 0, and the SessionStart one prints the seat card.
  - `test_user_edit_survives_uninstall`: edit `settings.json` after install by adding a
    permission. After uninstall the permission is still there, and no owned entry is left.
  - `test_status_reports_drift`.
  - `test_rc_choice_per_os`, with `sys.platform` and `$SHELL` patched.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement to the tables and rules above. `README.md` gets the deployment
  table and the uninstall line.
- [ ] **Step 4:** Run the tests. Expected: PASS on Linux locally; push to a branch and read
  the CI result for macOS and Windows.
- [ ] **Step 5: Commit** with the message `feat(installer): install, uninstall, update, status with an action journal`.

### Task 9: Workflow test harness, golden copies, `gk-selftest`

Worker: `worker-sonnet`.

**Files:**
- Create: `tests/workflows/harness.mjs`, `tests/workflows/harness.test.mjs`
- Create: `tests/workflows/golden/gk-{review,build,design,research,diagnose}.js`, copied
  verbatim from Goodricke `develop` @ `0ba966af0`
  (`git -C /home/carls/apps/mike-dev show 0ba966af0:.claude/workflows/<f>`; read-only, no
  checkout), plus a `README.md` naming the commit
- Create: `tests/workflows/fixtures/goodricke.gk-project.json` (filled in by Task 10)
- Create: `workflows/gk-selftest.js`

**Interfaces:**
- Produces:
  `runWorkflow(scriptPath: string, args: any, responders?: Array<{match: RegExp, reply: (prompt, opts) => any}>) -> Promise<{result, calls: Array<{label, phase, model, effort, prompt}>, logs: string[]}>`
  - it strips `export ` from the `meta` line and runs the body as an async function with
    stubbed `agent`, `parallel`, `pipeline`, `phase`, `log`, `args`, `budget` and `workflow`;
  - `agent()` records the call and returns the first responder whose `match` tests
    `opts.label`; otherwise `minimalFromSchema(opts.schema)` (the required properties only:
    strings `"x"`, numbers `1`, booleans `false`, arrays `[]`), or `""` with no schema;
  - `parallel` and `pipeline` are faithful: thunks are awaited, a stage throw drops the item
    to `null`.
- `gk-selftest.js` makes no `agent()` calls and returns
  `{profile: args?.profile ?? null, project: args?.project ? {name: args.project.name ?? null, root: args.project.root ?? null, guards: (args.project.guards || []).length} : null, scratchpad: args?.scratchpad ?? null}`.
  Its meta is `{name: 'gk-selftest', description: 'Echo the profile and project the harness injected; runs no agents'}`.

- [ ] **Step 1: Write the failing tests:**
  - `harness: golden gk-review runs under the fable profile and records every agent call with model and effort`;
  - `harness: a stage throw drops the item`;
  - `selftest echoes`.
- [ ] **Step 2:** Run `node --test tests/workflows/ 2>&1 | tail -5`. Expected: FAIL.
- [ ] **Step 3:** Implement the harness and `gk-selftest.js`.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5: Commit** with the message `test(workflows): stub harness, golden Goodricke copies, gk-selftest`.

### Task 10: Neutralise `gk-review` and `gk-build`

Worker: `worker-opus-xhigh`. These two carry project hooks beyond guards.

**Files:**
- Create: `workflows/gk-review.js`, `workflows/gk-build.js`, starting from the golden copies
- Modify: `tests/workflows/fixtures/goodricke.gk-project.json`
- Create: `tests/workflows/review-build.test.mjs`

**`gk-project.json` schema v1** (Tasks 5, 6 and 11 rely on it):
```json
{
  "version": 1,
  "name": "Goodricke",
  "sharedBranch": "develop",
  "guards": ["<one rule line added to every stage brief>"],
  "reviewFinders": [{"key": "security-and-invariants", "replaces": "security", "brief": "<finder brief>"}],
  "signOff": [
    {"key": "changelog", "sharedFile": "CHANGELOG.md", "implementer": "<rule>", "reviewer": "<what reviewers check>", "seat": "<what the seat does>"},
    {"key": "scroll-audit", "trigger": "<condition the implementer reports>", "uiJudge": true, "implementer": "<rule>", "reviewer": "<check>", "seat": "<command and rule>"}
  ]
}
```
`root` is never in the file; the hook adds it. Every workflow validates `args.project`:
- absent, or an object;
- `guards` and `reviewFinders`, when present, are arrays;
- anything else throws `gk-<name>: args.project is malformed (<field>)`.

Each workflow logs `Project: <name> (<n> guards, <n> finders, <n> sign-off checks)`, or
`Project: none (generic rules)`.

Changes:
- `REPO` becomes `ROOT = args.project?.root`. With a root, prompts say "the git repository at
  ${ROOT}". Without one, they say "the git repository containing your working directory (run
  `git rev-parse --show-toplevel` first)".
- `normFile` strips the `ROOT + "/"` prefix when there is a root.
- Every Goodricke sentence in the prompts moves verbatim into `guards` (all stages),
  `reviewFinders` or `signOff`. The `guards` lines are appended to every stage brief under
  `Project rules:`.
- `gk-review` core finders, in this order:
  - `correctness`;
  - `security`, with a generic brief: injection, authorisation on every mutating path,
    secrets in code or logs, unsafe deserialisation, missing input validation at trust
    boundaries;
  - `regressions-and-tests`;
  - `efficiency-and-simplification`.

  A project finder with `replaces: K` takes K's place (same position, its own key); other
  project finders are appended before the `focus` finders. Goodricke's `security-and-invariants`
  replaces `security`, which gives today's four finders in today's order.
- `gk-build`:
  - implementers may touch each `signOff[].sharedFile` in addition to their owned files
    (today: `CHANGELOG.md`);
  - each signOff's `implementer` text goes into the implement brief, and its `reviewer` text
    into the review brief;
  - implementer schema: `changelogLine` → `sharedFileLines: [{file, line}]`, and
    `pageRootChanged` → `triggered: string[]` (the signOff keys whose `trigger` the piece
    met);
  - a triggered entry with `uiJudge: true` routes the judge to fable, as page roots do today;
  - judge schema: `scrollAuditRequired` → `signOffRequired: [{key, pieces}]`;
  - the return value replaces `scrollAuditRequired` with
    `signOff: [{key, seat, pieces}]`. Entries without `trigger` are always listed, with every
    piece; triggered entries are listed with the pieces that met them. If the judge dies, the
    fallback lists every triggered entry;
  - the usage strings and `meta.whenToUse` are generic, with "Goodricke" removed.

- [ ] **Step 1: Write the failing tests** `tests/workflows/review-build.test.mjs`:
  - `routing equivalence, gk-review`. For each profile `fable|opus|sonnet` × flags
    `{}`, `{ui:true}`, `{security:true}`, `{escalate:true}`, `{clientFacing:true}`, and with
    responders that make Find return one finding per finder:
    - golden script with `{target:'develop..HEAD', ...flags, profile}`;
    - new script with the same args plus
      `project = {...goodrickeFixture, root:'/home/carls/apps/mike-dev'}`.

    The two give deep-equal `calls.map(c => [c.label, c.phase, c.model, c.effort])`.
  - `routing equivalence, gk-build`: the same grid, with `{ui:true}` replaced by one piece
    reporting the scroll-audit trigger (golden: `pageRootChanged: true`).
  - `goodricke sentences preserved`:
    - for every golden prompt, take the sentences matching
      `/mike-dev|supabase|prod|:8080|CHANGELOG|scratch scripts|Goodricke|Principal|canAct|ethical wall|page root|de-id|checkout/i`;
    - each sentence, whitespace-normalised, appears in the matching new prompt;
    - exceptions are listed in the test file, one reason each, and reviewed.
  - `generic run is clean`: run the new scripts with `project = {root:'/tmp/repo'}` and target
    `main..HEAD`. No prompt contains `Goodricke`, `mike-dev`, `CHANGELOG`, `supabase`,
    `:8080` or `\bdevelop\b`.
  - `build returns signOff`: one piece triggers scroll-audit and one doesn't. The result's
    `signOff` has `changelog` with both pieces and `scroll-audit` with one.
  - `project malformed throws`.
- [ ] **Step 2:** Run `node --test tests/workflows/ 2>&1 | tail -8`. Expected: FAIL.
- [ ] **Step 3:** Write the two workflows and fill in the fixture. The fixture text comes from
  the golden prompts, verbatim.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5: Commit** with the message `feat(workflows): gk-review and gk-build are project-neutral; Goodricke rules via gk-project.json`.

### Task 11: Neutralise `gk-design`, `gk-research`, `gk-diagnose`

Worker: `worker-opus`. This task needs only guards and the root, plus the scratchpad for
`gk-diagnose`.

**Files:**
- Create: `workflows/gk-design.js`, `workflows/gk-research.js`, `workflows/gk-diagnose.js`,
  starting from the golden copies
- Create: `tests/workflows/design-research-diagnose.test.mjs`
- Modify: the fixture, only by appending guard lines this task's golden prompts need

**Interfaces:**
- Consumes: the Task 10 schema and project validation (copy the validator; workflow scripts
  can't import).

Changes:
- `REPO` and `PROD_WORKTREE` become `ROOT` plus guards.
- `gk-diagnose`:
  - `DEFAULT_SCRATCHPAD` is deleted;
  - the scratchpad is `args.scratchpad`. Without one, the prompts say "make a temporary folder
    outside the repository with `mktemp -d` and delete it when done";
  - the no-paid-API rule moves to guards.
- `gk-design`'s UI sign-off method drops "including the page-root scroll rule"; that rule is
  a guard.
- Descriptions are generic.

- [ ] **Step 1: Write the failing tests:**
  - routing equivalence for each of the three, over profiles × `{ui:true|false}` ×
    `{escalate:true|false}`, with the same method as Task 10;
  - Goodricke sentences preserved;
  - `generic run is clean`;
  - `diagnose uses injected scratchpad`: given `args.scratchpad = '/tmp/s'`, every prompt that
    mentions a scratchpad names `/tmp/s`;
  - `diagnose without scratchpad says mktemp`.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Write the three workflows.
- [ ] **Step 4:** Run the tests, both the Python and Node suites. Expected: PASS.
- [ ] **Step 5: Commit** with the message `feat(workflows): gk-design, gk-research, gk-diagnose are project-neutral`.

### Task 12: Goodricke side (branch only, not merged yet)

Worker: `worker-sonnet`, in a Goodricke worktree.

**Files** (Goodricke repo, branch `chore/gk-harness-split` off `develop`):
- Create: `.claude/gk-project.json`, byte-equal to the gk-harness fixture
- Modify: `CLAUDE.md`
- Delete: `.claude/workflows/gk-{review,build,design,research,diagnose}.js`

- [ ] **Step 1: Create the worktree.**
  - `git worktree add ../mike-gk-split -b chore/gk-harness-split develop`, then
    `git -C ../mike-gk-split branch --unset-upstream`. This follows the
    "worktree upstream=develop" gotcha.
  - No checkout in `mike-dev` itself.
- [ ] **Step 2:** Copy the fixture to `.claude/gk-project.json`. Check:
  `cmp ~/.gk-harness/tests/workflows/fixtures/goodricke.gk-project.json .claude/gk-project.json`
  exits 0.
- [ ] **Step 3: Update `CLAUDE.md`.**
  - Confirm each rule that left the global policy is present: dev-only database briefs; prod
    only via the hosted Supabase MCP with the explicit project id, by the seat, on Carl's
    word; the CHANGELOG entry in build briefs; the scroll audit on a page-root change.
  - Today's file already has the last three. Add the general dev-only brief line.
  - Add a two-line "Harness" note: workflows come from gk-harness at user level, and this
    repo's rules for them live in `.claude/gk-project.json`.
- [ ] **Step 4:** Delete the five project workflows. They would otherwise outrank the
  user-level ones (design F6).
- [ ] **Step 5: Commit** on the branch with the message
  `chore(harness): Goodricke rules move to .claude/gk-project.json; workflows come from gk-harness`.
  - No CHANGELOG entry: tooling, not app behaviour.
  - Do **not** merge or push. The merge happens in Task 13, after the box install is proved.

### Task 13: Box rollout and live proofs (Carl's go)

The seat, with Carl. The box is a working machine with six other sessions, so start this
only when Carl says go, at a moment when no peer is mid-task.

- [ ] **Step 1:** Push `gk-harness` `main` to `origin`, then confirm CI is green on all three
  operating systems.
- [ ] **Step 2: Install.** Run `~/.gk-harness/install.sh`, then `install.sh status`. Expected:
  - version 1.0.0, no drift;
  - the `CLAUDE.md` block in place;
  - the old policy in backups;
  - `autoMode` unchanged (`diff` against the backup's `autoMode` key).
- [ ] **Step 3: Remove the F12 duplicates by hand.**
  - Drop the `supabase-row-cap.sh` and `pii-in-logs.sh` entries from
    `~/.claude/settings.json`; Goodricke's `.claude/settings.json` keeps them.
  - Move `~/.claude/agents/ooxml-docx-integrity-reviewer.md` into the install's backup
    folder.
- [ ] **Step 4: Live proof of `updatedInput` (F4).** In a new desktop-app session on the box,
  call `Workflow({name: "gk-selftest"})` with no args. Expected: the result shows
  `profile: "opus"` and a `project.root`.
  - If `profile` is null, the hook's `updatedInput` was ignored. **Stop and report to Carl.**
    Don't add `permissionDecision: "allow"`: that would auto-approve every Workflow call.
- [ ] **Step 5: PostModelSwitch.** In that session, `/model sonnet`. Expected:
  - the state file says sonnet;
  - `gk-selftest` returns `sonnet`.
  - Record whether the seat card arrived as context, which answers the design's open
    question.
- [ ] **Step 6: Live matrix, box rows.** For each cell, open a new session, ask "which mode
  are you in?", then run `gk-selftest`:

  | Surface | Opus | Sonnet | Switch | Fable |
  |---|---|---|---|---|
  | Desktop app → box | ✓ | ✓ | ✓ | on Carl's word |
  | Laptop terminal → SSH → box | ✓ | ✓ | ✓ | on Carl's word |

  Also confirm that no session shows a settings validation error at startup.
- [ ] **Step 7: Coordination.** One desktop session and one SSH terminal session, both in a
  scratch git repo:
  - each lists the other in its peer briefing;
  - both edit `a.txt`;
  - the second gets the overlap line and messages the first in that turn, before editing
    further.
- [ ] **Step 8: Project separation.**
  - A session in a non-Goodricke repo (the scratch repo): `gk-selftest` returns
    `project.name: null`, and asked "what are this repo's sign-off rules?" it names none of
    Goodricke's.
  - In `mike-dev`, on Task 12's branch worktree: `gk-selftest` returns `Goodricke` with its
    guard count, and asked the same question the session names the CHANGELOG entry, the
    scroll audit and the dev-only Supabase rule.
- [ ] **Step 9: Merge Task 12.**
  - Message the peers that hold claims on the touched files (policy rule 3).
  - Trial-merge first (the "dev merge watcher crash" gotcha).
  - Then merge `chore/gk-harness-split` into `develop` from the `mike-dev` checkout, and push
    `develop` (`git fetch` first).
- [ ] **Step 10:** Once no pre-install session is alive, run `install.sh --retire-legacy`.
  Remove the three `gk` aliases from `~/.bashrc` if `install` didn't. Install leaves
  `~/.claude/hooks/supabase-row-cap.sh` and `pii-in-logs.sh` in place; their entries are
  already gone.
- [ ] **Step 11: Memory, at the end of the session.**
  - Update `three-seat-policy.md` to point to the gk-harness repo, and note that the mode
    follows the model.
  - Index it in `MEMORY.md`.

### Task 14: Laptop rollout (macOS, Carl at the laptop)

- [ ] **Step 1:** Run `git clone git@github.com:carls-repo/gk-harness.git ~/.gk-harness`. The
  laptop's SSH key must be on GitHub, or use HTTPS. Then run `~/.gk-harness/install.sh`.
  Expected: it finds Python 3, or it stops with the Xcode Command Line Tools message.
- [ ] **Step 2:** Run the live-matrix row "Laptop, local": Opus, Fable on Carl's word, and a
  switch.
- [ ] **Step 3:** Open two local sessions and check that each lists the other. This is the
  macOS liveness proof (Review Focus 1).

The Windows row of the live matrix waits until Carl uses a Windows machine (design §6). Until
then, the windows-latest CI job is the proof.

### Task 15: Cloud spike, Route A (Carl configures the environment)

- [ ] **Step 1 (Carl):** create a fine-grained GitHub token with read-only access to
  `gk-harness`, and store it as the cloud environment secret `GK_HARNESS_TOKEN`. Never paste
  it into a session. Set the environment's setup script to:
  ```bash
  git clone --depth 1 "https://x-access-token:${GK_HARNESS_TOKEN}@github.com/carls-repo/gk-harness.git" ~/.gk-harness && ~/.gk-harness/install.sh
  ```
- [ ] **Step 2:** In a cloud session on a test repo, ask "which mode are you in?", run
  `gk-selftest`, and run `ls ~/.claude/workflows ~/.claude/agents`. Record:
  - (a) whether the policy loaded, shown by the mode answer quoting the seat card or the
    policy;
  - (b) whether `gk-selftest` resolved by name;
  - (c) whether `profile` was injected;
  - (d) whether `worker-opus` resolves as an agent type.
- [ ] **Step 3: Decision.**
  - All four pass: Route A is done. Add the setup script to each cloud environment Carl uses.
  - Any fail: write the findings up as a Claude Doc and bring Route B (design §4.3) back to
    Carl, one question at a time.
