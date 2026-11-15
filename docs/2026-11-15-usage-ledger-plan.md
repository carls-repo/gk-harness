# Per-call usage ledger: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal.** Record every Claude Code model call on the box as one ledger line, as it happens.
`gkh report` breaks the lines down by project and merges them with the transcripts. A local
Grafana dashboard reads them live.

**Architecture.** Claude Code's built-in telemetry posts one `claude_code.api_request` event per
call to a loopback receiver, `hooks/gk_ledger.py`. The receiver:

- keeps only allowlisted fields;
- stamps each line with the session's project, from a session map the hooks keep;
- appends one line per call to a daily file;
- serves Prometheus counters.

The installer owns five `env` keys only while `gkh ledger on` is set. `gkh report` matches
ledger lines to transcript calls by request id. Prometheus and Grafana run in Docker on loopback
(Stage 4).

**Tech stack.** Python 3.9+ standard library only (`http.server`, `json`, `socket`,
`subprocess`), plus `unittest`. Stage 4 adds Docker Compose, Prometheus and Grafana.

**Spec:** `docs/2026-11-15-usage-ledger-design.md`, the ledger spec, approved by Carl on 15
November 2026. Read it first; this plan argues from it.

## Global constraints

- Python 3.9 or later, standard library only, in every module and test.
- **Loopback only.** Nothing listens on any address but `127.0.0.1`, and nothing is sent off the
  machine: no Grafana Cloud, and Grafana's update, news and analytics calls are off.
- **No content.** No prompt text, response, tool input or file content is ever written. The
  installer never sets an `OTEL_LOG_*` variable.
- **Hooks never break a session.** Every new hook path swallows its own errors (inside
  `gk_common.run_hook`). The prompt-time probe takes at most 100 ms and never waits for a spawn.
- **The ledger is off until `gkh ledger on`.** `install` and `update` never switch it on. On
  Windows, `gkh ledger on` refuses.
- **The fixed values** (from spec 4.1 and 4.2):
  - port 4318;
  - endpoint `http://127.0.0.1:4318/v1/logs`;
  - a 4 MiB body cap;
  - a 30-minute idle exit;
  - a 90-day session-map retention;
  - a 100,000-token large-prompt threshold;
  - Prometheus on `127.0.0.1:9090` and Grafana on `127.0.0.1:3300`.
- **Live checkout.** Never edit, check out or install from `~/.gk-harness` (the live harness)
  before Task 8. Never run `install.py` with no arguments: that runs an install.
- **Test runs** are foreground only, piped through `tail`:
  `python3 -m unittest discover -s tests 2>&1 | tail -5`.
- **Commits** end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Docs** are in British English, with no exclamation marks.

## Review focus

The five inputs most likely to bite that no task's own tests would otherwise exercise. Each now
has a test in the task named.

1. **The UTC day boundary.** An event at 23:59:59.999Z and one at 00:00:00.000Z land in two day
   files, and `gkh report --since` takes the right one (Tasks 2 and 6).
2. **A forked session.** Its transcript copies the parent's history, so one request id appears
   in two transcripts and once in the ledger. It counts once (Task 6).
3. **Torn files.** After a crash, a session-map file may be half written, or the last ledger line
   cut short. The receiver treats the map as missing. The report skips the cut line and counts
   it under `"bad"` (Tasks 2 and 6).
4. **Port 4318 held by another program**, such as a real OpenTelemetry collector installed later.
   Session start shows the foreign-port notice once. The prompt probe sees an open port and
   spawns nothing, so there is no retry storm (Task 4).
5. **Sessions open before `gkh ledger on`.** They have no telemetry variables, so they send
   nothing until restarted. Their hooks still keep the receiver up and the map current, without
   error, and `gkh ledger status` says sessions must restart (Tasks 4 and 5).

---

### Task 0: Stage 0 proofs (P1 to P5, P7)

These prove what the spec's section 5 leaves open. P6 (a desktop session) needs the live
settings, so it runs in Task 8. Nothing here touches `~/.claude/settings.json`: every run gets
its variables on the command line.

**Files:**
- Create: `docs/proofs/2026-11-ledger-stage0.md`
- Create: `tests/fixtures/otlp/logs-api-request.json` (a main-session call)
- Create: `tests/fixtures/otlp/logs-subagent.json` (a subagent call and a workflow-agent call)
- Scratch only, never committed: a capture receiver in the session scratchpad that writes each
  request's method, path, headers and body to a numbered file.

**Interfaces:**
- **Produces, for Task 2:**
  - the exact attribute keys and value types of `api_request` and `api_error` records;
  - where the event name sits (an `event.name` attribute or the record body);
  - the resource attribute that names the service, and its value;
  - the list of `query_source` values seen;
  - the two fixtures above.
- **Every task after this one reads the proof file.**

- [ ] **Step 1: Write the capture receiver** in the scratchpad (`http.server`, port 4319). It
  records every request and answers `200 {}`.
- [ ] **Step 2: P1, P2 and P7.** With the capture receiver running, start one short run:
  ```bash
  CLAUDE_CODE_ENABLE_TELEMETRY=1 OTEL_LOGS_EXPORTER=otlp OTEL_EXPORTER_OTLP_PROTOCOL=http/json \
  OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=http://127.0.0.1:4319/v1/logs OTEL_METRICS_EXPORTER=none \
  claude --model haiku -p "Reply with the word ok."
  ```
  - **Record:** every path that was hit (P7: only `/v1/logs`), and the record shape (P1).
  - **Check:** `request_id` against the transcript line's `requestId` (P2).
- [ ] **Step 3: P3.** Run once with `--allowedTools "Agent,Workflow"`. The prompt runs one
  Explore subagent and one inline Workflow with a single `agent()` call at
  `{model:'haiku', effort:'low'}`.
  - **Record:** `agent_id`, `parent_agent_id`, `workflow.run_id` and `session.id` on those records.
  - **Check:** they match the transcript's `agent-<id>` file names, the `wf_` folder and the main
    session's id.
- [ ] **Step 4: P4.** Run five one-line `-p` runs. Compare the records received with the
  transcript calls, run by run. Expected: none missing at exit.
- [ ] **Step 5: P5.** Point the endpoint at a closed port (4320) and time three runs, then three
  with the capture receiver listening. Record the wall times and whether stderr shows anything.
  Expected: no error, and no slowdown above 1 second.
- [ ] **Step 6: Make the fixtures.** Copy one real body per fixture, then replace these values
  with fixed fakes:
  - `user.email` becomes `user@example.invalid`;
  - `user.account_uuid` and `organization.id` become fixed fake UUIDs.

  Keep the structure and every other attribute exactly. Then confirm that no fixture holds prompt
  or response text.
- [ ] **Step 7: Write up the proofs.** Write `docs/proofs/2026-11-ledger-stage0.md`: for each
  proof, the check, what was seen and the conclusion, in the style of
  `docs/proofs/2026-11-stage0.md`. If a finding contradicts the spec, record a **Ruling:** line
  (follow what was seen) for the seat to confirm.
- [ ] **Step 8: Commit** the proof file and fixtures:
  `docs(ledger): Stage 0 proofs P1-P5, P7 and OTLP fixtures`.

### Task 1: Ledger helpers and project identity in `gk_common`

**Files:**
- Modify: `hooks/gk_common.py`
- Test: `tests/test_ledger_common.py` (new)

**Interfaces:**
- **Consumes:**
  - `git_identity(start) -> (common_dir, toplevel, branch) | None` (a detached HEAD gives
    `"HEAD"`);
  - `load_project(toplevel) -> (data | None, reason | None)`;
  - `write_json(path, obj) -> bool`, which is atomic;
  - `claude_dir()`.
- **Produces** (all in `hooks/gk_common.py`):
  - `LEDGER_HOST = "127.0.0.1"` and `LEDGER_PORT = 4318`;
  - `ledger_port() -> int`: `$GK_LEDGER_PORT` when it holds an integer from 1 to 65535 (tests
    only), else `LEDGER_PORT`;
  - `ledger_dir() -> Path`, which is `claude_dir()/state/gk-ledger`;
  - `ledger_on_file() -> Path`, which is `claude_dir()/state/gk-ledger-on.json`;
  - `ledger_on() -> bool`: False when `os.name == "nt"`, else whether the switch file exists;
  - `NO_REPO = "(no repo)"`;
  - `project_identity(cwd) -> dict`, giving `{"cwd", "root", "project", "branch"}`:
    - `root` is the git top level, or `cwd` outside a repository;
    - `project` follows spec 4.8: the `gk-project.json` `name` if it is a non-empty string; else
      the folder holding the common dir when the common dir's own name is `.git`, else the common
      dir's own name, with one leading `.` dropped; else `NO_REPO`;
    - `branch` is `None` for `"HEAD"` or outside a repository;
  - `session_map_path(sid) -> Path`, which is `ledger_dir()/sessions/<sid>.json`;
  - `write_session_map(sid, cwd) -> bool`: writes
    `{"session", "cwd", "root", "project", "branch", "updated": <UTC ISO>}`. It skips the write
    when the stored map already holds the same `cwd`, `root`, `project` and `branch`, and returns
    False for an unsafe `sid`.
- **Not added** to `SESSION_STATE_DIRS`: the map must outlive its session.

- [ ] **Step 1: Write the failing tests.** They make temporary repos with `git init`, a worktree
  made with `git worktree add`, and a `.claude/gk-project.json`:
  - `test_project_from_project_file`: `name: "Goodricke"` gives `project == "Goodricke"`.
  - `test_worktree_takes_main_repo_name`: a worktree of repo `alpha` gives `"alpha"`.
  - `test_leading_dot_dropped`: a repo folder `.gk-harness` gives `"gk-harness"`.
  - `test_no_repo`: a plain temp folder gives `NO_REPO`, with `root == cwd` and `branch is None`.
  - `test_detached_head_branch_none`.
  - `test_session_map_written_and_unchanged_skip`: the second call with the same identity leaves
    the mtime unchanged.
  - `test_session_map_follows_branch_switch`: after a branch switch the map holds the new branch.
  - `test_ledger_on_false_on_windows`: patch `os.name` to `"nt"`, with the switch file present.
  - `test_ledger_port_env_override_and_bad_value`: `"abc"` and `"70000"` fall back to 4318.
- [ ] **Step 2: Run the tests.** Expected: they fail with AttributeError.
  `python3 -m unittest tests.test_ledger_common 2>&1 | tail -5`
- [ ] **Step 3: Implement** the functions listed under Produces. Use one `git_identity` call per
  `project_identity`.
- [ ] **Step 4: Run the tests** again. Expected: PASS. Then run the full suite. Expected: OK.
- [ ] **Step 5: Commit:** `feat(ledger): session map and project identity helpers`.

### Task 2: The receiver's pure functions

**Files:**
- Create: `hooks/gk_ledger.py` (the functions only, in this task)
- Modify: `gkh/cli.py`: add `"gk_ledger.py"` to `HOOK_SUPPORT`, so `_check_sources` requires it
  (`hooks/*.py` is already deployed).
- Test: `tests/test_ledger.py` (new)

**Interfaces:**
- **Consumes:**
  - Task 0's fixtures and proof file (the attribute names and the service attribute);
  - Task 1's `session_map_path` and `ledger_dir`. `gk_ledger.py` imports `gk_common` the way the
    other hooks do.
- **Produces** (in `hooks/gk_ledger.py`):
  - `LEDGER_VERSION = 1`;
  - `EVENTS = ("claude_code.api_request", "claude_code.api_error")`;
  - `SERVICE`: the value Task 0 saw, expected `"claude-code"`;
  - `LARGE_PROMPT = 100_000`;
  - `LINE_KEYS`, the spec 4.3 key order:
    ```
    ts, event, session, prompt, request, model, effort, speed, source, agent, parent,
    agent_name, workflow_run, workflow, project, root, branch, input, output, cache_read,
    cache_write, cache_write_5m, cache_write_1h, cost_usd, duration_ms, status, attempt
    ```
  - `any_value(v: dict) -> Any`: decodes an OTLP AnyValue (`stringValue`; `intValue` sent as a
    string; `doubleValue`; `boolValue`; anything else gives `None`);
  - `parse_logs(body: bytes) -> list`: gives `[(resource_attrs: dict, record_attrs: dict,
    event_name: str | None, time_unix_nano: int | None), ...]`, and raises ValueError on
    non-JSON or a wrong top-level shape;
  - `to_line(resource, attrs, event_name, time_ns, lookup) -> dict | None`:
    - None when the event is not in `EVENTS`, or the service is not `SERVICE`;
    - otherwise only the `LINE_KEYS`, in order;
    - `ts` comes from `event.timestamp`, else `time_ns`, else now;
    - counts are ints and `cost_usd` is a float;
    - `project`, `root` and `branch` come from `lookup(session)`, else None;
  - `day_file(ledger_dir: Path, ts: str) -> Path`: `<ledger_dir>/<YYYY-MM-DD>.jsonl` from the
    UTC date in `ts`, falling back to today's UTC date when `ts` is unusable;
  - `append_line(ledger_dir: Path, line: dict) -> bool`: one `os.write` of compact JSON plus `\n`
    to a file opened with `O_APPEND|O_CREAT|O_WRONLY`, mode 0600; it makes the folder;
  - `class SessionLookup(sessions_dir: Path)`, callable as `(sid) -> dict | None`: an mtime
    cache, where an unreadable or partial file gives None;
  - `class Metrics` with `add(line: dict) -> None` and `render() -> str`:
    - Prometheus text 0.0.4;
    - counters `gk_ledger_requests_total{event}`, `gk_ledger_tokens_total{kind}`,
      `gk_ledger_cost_usd_total` and `gk_ledger_large_prompts_total`;
    - every counter carries `model`, `effort`, `agent_name`, `workflow`, `source` and `project`,
      with None written as `""`;
    - label values are escaped for `\`, `"` and newline;
    - the large-prompt counter adds when `input + cache_read + cache_write > LARGE_PROMPT`.

- [ ] **Step 1: Write the failing tests:**
  - `test_fixture_line_exact`: a golden dict built from the fixture and a fake lookup.
  - `test_subagent_fixture_carries_agent_and_workflow`.
  - `test_other_event_dropped`, and `test_other_service_dropped`.
  - `test_unknown_and_content_attributes_dropped`: inject `prompt`, `user.email` and a
    `body.content` attribute into a copy; none appears in the line.
  - `test_int_value_as_string`.
  - `test_missing_or_torn_session_map_gives_nulls` (review focus 3).
  - `test_day_boundary` (review focus 1): `23:59:59.999Z` and `00:00:00.000Z` give two files.
  - `test_append_line_is_one_line_and_mode_0600`.
  - `test_metrics_render_parses`: a small regex parser over `render()`, checking the names, label
    sets and values after two lines.
  - `test_metrics_label_escaping`.
  - `test_large_prompt_threshold`: 100,000 is not counted; 100,001 is.
  - `test_metrics_labels_carry_no_ids`: no session, request or agent id appears in `render()`.
- [ ] **Step 2: Run the tests.** Expected: they fail (no module).
- [ ] **Step 3: Implement** the functions listed under Produces.
- [ ] **Step 4: Run the tests.** Expected: PASS, and the full suite OK.
- [ ] **Step 5: Commit:** `feat(ledger): OTLP parsing, allowlist, ledger lines and counters`.

### Task 3: The receiver's server

**Files:**
- Modify: `hooks/gk_ledger.py`
- Modify: `hooks/gk_common.py`: add `live_session() -> bool`, which is True when any
  `claude_dir()/sessions/*.json` names a live pid (reuse `pid_alive`).
- Test: `tests/test_ledger_server.py` (new)

**Interfaces:**
- **Consumes:** Task 2's functions, plus `gk_common.ledger_dir`, `ledger_port` and `pid_alive`.
- **Produces:**
  - `MAX_BODY = 4 * 1024 * 1024`, `IDLE_SECONDS = 1800` and `MAP_RETENTION_DAYS = 90`;
  - `make_server(ledger_dir: Path, port: int, host: str = "127.0.0.1") -> ThreadingHTTPServer`.
    The handler:
    - `POST /v1/logs` takes a `Content-Type` holding `json` and a `Content-Length` of
      `MAX_BODY` or less. It writes each `to_line` result with `append_line` and adds it to
      `Metrics`, then replies `200`, `application/json`, `{}`.
    - It replies 400 on a parse error, 411 with no length, 413 over the cap, and 415 on another
      type.
    - `GET /gk-ledger/health` replies `{"gk-ledger": LEDGER_VERSION}`.
    - `GET /metrics` replies with `Metrics.render()` as `text/plain; version=0.0.4`.
    - Anything else gets a 404.
    - It writes no per-request log lines.
  - `serve(ledger_dir: Path, port: int, idle_seconds: int = IDLE_SECONDS,
    live: Callable[[], bool] = gk_common.live_session) -> int`:
    - it prunes session maps older than `MAP_RETENTION_DAYS`;
    - it binds the port. On EADDRINUSE it returns 0 and writes nothing;
    - it writes `ledger_dir/receiver.pid`;
    - it serves until a watcher thread sees no request for `idle_seconds` while `live()` is
      False, or until SIGTERM;
    - it removes the pidfile and returns 0;
    - errors go to `ledger_dir/receiver.log`, which holds counts and the first instance of each
      error, never request bodies.
  - `main(argv) -> int` for `gk_ledger.py serve [--port N]`, with `--port` defaulting to
    `ledger_port()`. The module ends with `if __name__ == "__main__": sys.exit(main(sys.argv[1:]))`.

- [ ] **Step 1: Write the failing tests.** Each runs `make_server` on port 0 in a thread, with
  temporary dirs:
  - `test_post_fixture_writes_line`.
  - `test_health`.
  - `test_metrics_after_post`.
  - `test_404_other_path`.
  - `test_400_not_json`, `test_411_no_length`, `test_413_over_cap` and `test_415_wrong_type`.
  - `test_binds_loopback_only`: `server.server_address[0] == "127.0.0.1"`.
  - `test_twenty_concurrent_posts_keep_lines_whole`: every line parses, and there are 20 of
    them.
  - `test_second_serve_on_bound_port_returns_0`.
  - `test_idle_exit_removes_pidfile`, with `idle_seconds=1` and `live=lambda: False`.
  - `test_live_session_keeps_it_running`, with `live=lambda: True` for 2 seconds.
  - `test_prunes_old_session_maps`: one map aged 91 days by `os.utime`, and one fresh.
  - `test_disk_full_logged_once_keeps_serving`: patch `os.write` to raise
    `OSError(errno.ENOSPC)`. Two posts each reply 200, `receiver.log` names the error once, and
    a health check still answers.
- [ ] **Step 2: Run the tests.** Expected: they fail.
- [ ] **Step 3: Implement** the server as listed under Produces.
- [ ] **Step 4: Run the tests.** Expected: PASS, and the full suite OK.
- [ ] **Step 5: Commit:** `feat(ledger): loopback receiver with health, metrics and idle exit`.

### Task 4: Start logic and hook wiring

**Files:**
- Modify: `hooks/gk_common.py`
- Modify: `hooks/gk-seat.py`, for SessionStart only
- Modify: `hooks/gk-peers.py`, for UserPromptSubmit only
- Test: `tests/test_ledger_start.py` (new). It loads both hooks with `importlib`, as
  `tests/test_seat_hook.py` does, and monkeypatches `gk_common.start_receiver`.

**Interfaces:**
- **Consumes:** Task 1's `ledger_on`, `ledger_port` and `write_session_map`; Task 3's
  `gk_ledger.py serve`.
- **Produces** (in `hooks/gk_common.py`):
  - `probe_health(port: int, timeout: float = 0.5) -> str`:
    - `"ours"` when GET `/gk-ledger/health` answers with the `gk-ledger` key;
    - `"free"` when the connect is refused;
    - `"foreign"` otherwise.
  - `port_open(port: int, timeout: float = 0.1) -> bool`: a TCP connect only.
  - `start_receiver(port: int, wait: float = 1.0) -> bool`:
    - spawns `[sys.executable, <this folder>/gk_ledger.py, "serve", "--port", str(port)]`
      with `start_new_session=True`, `close_fds=True` and stdio set to DEVNULL;
    - polls `port_open` until `wait` runs out;
    - `wait=0` returns at once.
  - `LEDGER_DOWN` (the spec 4.6 text):
    `"Ledger: receiver not running, calls are not being recorded (gkh ledger status)"`.
  - `LEDGER_FOREIGN`:
    `"Ledger: port %d is held by another program, calls are not being recorded (gkh ledger status)"`.
- **In `gk-seat.py`**, on SessionStart, while `ledger_on()`:
  1. `write_session_map(sid, data.get("cwd"))`.
  2. `state = probe_health(port)`; when it is `"free"`, run `start_receiver(port)` and probe
     again.
  3. For `"free"`, add `LEDGER_DOWN` to the card. For `"foreign"`, add `LEDGER_FOREIGN % port`.
  4. Wrap all of it in try/except, so a failure leaves the card unchanged.
- **In `gk-peers.py`**, on UserPromptSubmit, while `ledger_on()`:
  1. `write_session_map(own, data.get("cwd"))`.
  2. If `not port_open(port)`, run `start_receiver(port, wait=0)`.
  3. Add nothing to the output, and wrap it in try/except.

- [ ] **Step 1: Write the failing tests:**
  - `test_off_does_nothing`: no map is written and nothing is spawned.
  - `test_session_start_free_spawns`.
  - `test_session_start_ours_no_spawn`, with a real `make_server` on an ephemeral port set
    through `GK_LEDGER_PORT`.
  - `test_session_start_foreign_notice_once`, with a bare socket listener (review focus 4).
  - `test_session_start_spawn_fails_down_notice`.
  - `test_prompt_probe_refused_spawns_without_wait`.
  - `test_prompt_probe_open_port_no_spawn`: covers both the foreign listener and ours.
  - `test_prompt_refreshes_map_after_branch_switch`.
  - `test_prompt_hook_errors_never_raise`: make `write_session_map` raise.
  - `test_probe_under_100ms_on_refused_port`.
- [ ] **Step 2: Run the tests.** Expected: they fail.
- [ ] **Step 3: Implement** the start logic and both hook changes.
- [ ] **Step 4: Run the tests.** Expected: PASS, and the full suite OK. That includes
  `test_seat_hook` and `test_coord`, unchanged while the ledger is off.
- [ ] **Step 5: Commit:** `feat(ledger): hooks keep the session map and the receiver up`.

### Task 5: Installer ownership, `gkh ledger` and lifecycle

**Files:**
- Modify: `gkh/settings_merge.py`
- Modify: `gkh/cli.py`
- Create: `gkh/ledger_ctl.py`
- Modify: `README.md` (a Ledger section)
- Test: extend `tests/test_gkh_settings_merge.py` and `tests/test_install.py`; create
  `tests/test_gkh_ledger_ctl.py`.

**Interfaces:**
- **Consumes:**
  - `owned_desired(routes)`, `merge(...)`, `_unown`, `_owned_last(journal)` and
    `_settings_step(...)`;
  - `install(retire, ignore_busy)`, `uninstall()` and `status()`;
  - `_busy_refusal()`, `_windows()` and `paths.claude_dir()`.
- **Produces, in `gkh/settings_merge.py`:**
  - `LEDGER_ENV`, holding these five path tuples and values:

    | Key | Value |
    |---|---|
    | `("env", "CLAUDE_CODE_ENABLE_TELEMETRY")` | `"1"` |
    | `("env", "OTEL_LOGS_EXPORTER")` | `"otlp"` |
    | `("env", "OTEL_EXPORTER_OTLP_PROTOCOL")` | `"http/json"` |
    | `("env", "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT")` | `"http://127.0.0.1:4318/v1/logs"` |
    | `("env", "OTEL_METRICS_EXPORTER")` | `"none"` |

  - `owned_desired(routes, ledger: bool = False) -> dict` adds `LEDGER_ENV` when `ledger` is
    True.
  - `merge(..., release: dict | None = None)`:
    - `release` is `{dotted key: {"prior", "wrote"}}`;
    - for each key it puts back `prior` when the key still holds `wrote` (removing the key when
      the prior is absent), and otherwise leaves the value;
    - in every case it records the key in `undo["released"][key]`, so released keys are no
      longer owned;
    - `unmerge` ignores `"released"`.
- **Produces, in `gkh/cli.py`:**
  - `_owned_last` reads newest first. A key whose newest record is under `undo["released"]` is
    left out of `last` and `priors`.
  - `_settings_step`:
    - `desired = owned_desired(routes, ledger=ledger_ctl.switch_on())`;
    - `release` is each `LEDGER_ENV` dotted key in `last` while the switch is off, with its
      record.
  - `ledger(word: str, options: list) -> int` for `on`, `off` and `status`:
    - **on:** refuses on Windows (exit 2), then runs the busy check unless `--ignore-busy`. It
      writes the switch file `{"since": UTC ISO}`, then runs `install(False, ignore_busy=True)`,
      which starts the receiver because the switch is now on. It prints the switch-on line plus
      "restart sessions to start recording".
    - **off:** runs the busy check, removes the switch file, `ledger_ctl.stop()`, then
      `install(False, ignore_busy=True)`.
    - **status:** prints `ledger_ctl.status_lines()`.
  - `COMMANDS`, `OPTIONS` and `USAGE` gain `ledger on|off|status [--ignore-busy]`.
  - `install()` calls `ledger_ctl.restart()` after a successful apply while the switch is on.
  - `uninstall()` calls `ledger_ctl.stop()` first and removes the switch file. Ledger data is
    kept.
  - `status()` prints `ledger: on|off` plus the lines from `ledger_ctl.status_lines(short=True)`.
- **Produces, in `gkh/ledger_ctl.py`:**
  - `switch_file() -> Path` and `switch_on() -> bool` (False on Windows);
  - `pid() -> int | None`;
  - `health() -> str`, giving `ours`, `free` or `foreign`;
  - `start() -> bool`, which spawns the **installed** `hooks/gk-harness/gk_ledger.py` with the
    Python in `gk-harness.json`;
  - `stop(timeout: float = 3.0) -> bool`, which sends SIGTERM through the pidfile and waits;
  - `restart() -> bool`;
  - `status_lines(short: bool = False) -> list`:
    - the receiver's state and port;
    - today's line count and the time of the last line;
    - each of the five keys: owned, hand-changed or absent;
    - `restart sessions to start recording` when the switch is newer than the oldest live
      session (review focus 5);
    - a warning for each `OTEL_LOG_*` key found in `settings.json` `env`.

- [ ] **Step 1: Write the failing tests.**
  - **Merge tests:**
    - `test_ledger_keys_desired_only_when_on`;
    - `test_release_restores_prior_and_marks_released`;
    - `test_release_keeps_hand_changed_value`;
    - `test_unmerge_ignores_released`;
    - `test_merge_with_release_is_idempotent`.
  - **Install tests** (the existing `GK_CLAUDE_DIR` harness):
    - `test_install_and_update_leave_ledger_off`;
    - `test_ledger_on_owns_keys_none_hand_changed`;
    - `test_ledger_off_restores_priors_and_install_does_not_rewrite`;
    - `test_uninstall_while_on_restores_priors_and_stops_receiver`;
    - `test_ledger_on_refused_on_windows`;
    - `test_ledger_on_refused_while_peer_busy`.
  - **ledger_ctl tests:**
    - `test_stop_via_pidfile` (a sleeping child as the receiver);
    - `test_status_lines_counts_today`;
    - `test_status_warns_content_variable`;
    - `test_status_says_restart_sessions`.
- [ ] **Step 2: Run the tests.** Expected: they fail.
- [ ] **Step 3: Implement** the changes listed under Produces, and write the README section:
  what it records, `gkh ledger on|off|status`, where the files are, and that sessions must
  restart.
- [ ] **Step 4: Run the tests.** Expected: PASS, the full suite OK, and `python3 -m gkh.gen
  --check` exits 0.
- [ ] **Step 5: Commit:** `feat(ledger): gkh ledger on|off|status; installer owns the
  telemetry keys only while on`.

### Task 6: `gkh report` reads the ledger

**Files:**
- Modify: `gkh/report.py`
- Modify: `gkh/cli.py` (`--source`)
- Test: extend `tests/test_gkh_report.py`, with ledger day files under a temporary claude dir.

**Interfaces:**
- **Consumes:**
  - the ledger lines from Task 2's `LINE_KEYS`;
  - `iter_calls(claude_dir, since, session, classes)`, `summarise(calls, routes)` and
    `render(summary, as_json)`;
  - `project_identity` from Task 1, loaded once from `paths.harness_root()/hooks/gk_common.py`
    with `importlib`, so the naming rules have one copy.
- **Produces:**
  - `_read_calls` adds `request_id` (the line's `requestId`), `cwd` and `git_branch` to each
    call.
  - `iter_ledger(claude_dir, since=None, session=None) -> Iterator[dict]`:
    - it yields calls with the same token keys as `iter_calls`, plus `request_id`, `source`,
      `agent_id`, `agent_name`, `workflow`, `workflow_run`, `project`, `root`, `branch` and
      `cc_cost`;
    - it maps the ledger keys onto those names: `request` becomes `request_id`, `agent`
      becomes `agent_id`, `cost_usd` becomes `cc_cost`, and the rest keep their names;
    - it skips day files before `since`;
    - it skips an unparseable line, counting it in a `bad` tally that it exposes.
  - `merge_sources(transcript, ledger, source="both") -> Iterator[dict]`:
    - **both:** a transcript call whose `request_id` is in the ledger takes the ledger's tokens,
      `cc_cost`, `project`, `root` and `branch`, and keeps the transcript's attribution and route
      class.
    - A transcript call with no match keeps its own figures. Its project comes from the session
      map if one exists, else from `project_identity(cwd)` when that folder exists, else
      `{project: basename(cwd), root: cwd, branch: git_branch}`.
    - Unmatched ledger calls get the agent `agent_name` when they have one, else
      `"background (%s)" % source`.
    - Each request id counts once (review focus 2).
    - `source="ledger"` or `"transcripts"` yields that side only.
  - `GROUPS` gains `byProject` and `byFolder`, with the titles "By project" and "By folder".
  - The summary gains `"ledger": {"calls", "since", "filled", "bad"}`. It gains a cost note when
    the matched calls' `cc_cost` total differs from the `routes.json` price by more than 1%.
  - `render` prints the header line `Ledger: <n> calls since <day>; transcripts filled <m>`, or
    `Ledger: no ledger files`.
  - In `cli`, `report` takes `--source ledger|transcripts|both`, defaulting to `both`; any other
    value exits 2.

- [ ] **Step 1: Write the failing tests:**
  - `test_merge_match_takes_ledger_tokens_keeps_attribution`;
  - `test_transcript_only_call_counts_and_fills`;
  - `test_ledger_only_call_is_background`;
  - `test_forked_session_counts_once` (review focus 2);
  - `test_by_project_and_by_folder`;
  - `test_project_fallback_when_folder_gone`;
  - `test_header_line_counts`;
  - `test_cost_note_over_1_percent_only`;
  - `test_source_option_filters_and_rejects_bad_value`;
  - `test_since_respects_ledger_day_files` (review focus 1);
  - `test_torn_last_line_counted_bad` (review focus 3).
- [ ] **Step 2: Run the tests.** Expected: they fail.
- [ ] **Step 3: Implement** the changes listed under Produces. The existing report tests must
  stay green with no ledger files present.
- [ ] **Step 4: Run the tests.** Expected: PASS, and the full suite OK.
- [ ] **Step 5: Commit:** `feat(report): merge the ledger by request id; by project and by
  folder`.

### Task 7: Grafana assets and `gkh grafana`

**Files:**
- Create: `deploy/grafana/compose.yml`
- Create: `deploy/grafana/prometheus.yml`
- Create: `deploy/grafana/provisioning/datasources/prometheus.yml`
- Create: `deploy/grafana/provisioning/dashboards/gk.yml`
- Create: `deploy/grafana/dashboards/gk-harness-usage.json`
- Create: `deploy/grafana/sudoers-gk-grafana`
- Create: `deploy/grafana/README.md`: the Stage 4 setup at the box, step by step.
- Modify: `gkh/cli.py` (`grafana up|down|status`)
- Test: `tests/test_grafana.py` (new)

**Interfaces:**
- **Consumes:** Task 2's `Metrics` (for the metric-name check), and the `gkh` command dispatch.
- **Produces:**
  - **`compose.yml`**, two services, both with `network_mode: host` and
    `restart: unless-stopped`:
    - `prometheus`: image `prom/prometheus:v2.55.1`. It runs with
      `--config.file=/etc/prometheus/prometheus.yml`,
      `--web.listen-address=127.0.0.1:9090` and `--storage.tsdb.retention.time=400d`, with the
      named volume `prometheus-data` at `/prometheus`.
    - `grafana`: image `grafana/grafana-oss:11.3.0`, with the named volume `grafana-data` and
      `env_file: grafana.env` (holding `GF_SECURITY_ADMIN_PASSWORD`). Its environment:
      - `GF_SERVER_HTTP_ADDR=127.0.0.1` and `GF_SERVER_HTTP_PORT=3300`;
      - `GF_AUTH_ANONYMOUS_ENABLED=true` and `GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer`;
      - `GF_USERS_ALLOW_SIGN_UP=false`;
      - `GF_ANALYTICS_REPORTING_ENABLED=false`, `GF_ANALYTICS_CHECK_FOR_UPDATES=false` and
        `GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES=false`;
      - `GF_NEWS_NEWS_FEED_ENABLED=false`.
    - Stage 4 may bump either image tag after checking the current tags with Carl.
  - **`prometheus.yml`:** `scrape_interval: 15s`, and one job, `gk-ledger`, with the target
    `127.0.0.1:4318` and the path `/metrics`.
  - **The data source** has uid `gk-prometheus` and points at `http://127.0.0.1:9090`. The
    dashboard provider loads `/var/lib/grafana/dashboards`.
  - **`gk-harness-usage.json`**, uid `gk-harness-usage`. Every panel uses data source uid
    `gk-prometheus` and is built on `increase()` of the Task 2 counters. The panels:

    | Panel | Built from |
    |---|---|
    | Spend today | `$__range`, defaulting to "today so far" |
    | Spend, last 7 days | a 7-day window |
    | Spend per day by model | daily totals per model |
    | Fable share | `model=~"claude-fable.*"` over the total |
    | Tokens per hour by kind | hourly totals per `kind` |
    | Spend by project, by workflow, by agent, by source | one breakdown each |
    | Errors per hour | `event="api_error"` |
    | Haiku calls over 100K | `gk_ledger_large_prompts_total{model=~"claude-haiku.*"}` |
    | Receiver up | `up{job="gk-ledger"}` |

    "Spend by source" stands in for the spec's background-spend panel: the receiver cannot tell
    a background call from a transcript one. Task 0's list of `query_source` values goes into
    the panel's description.
  - **`sudoers-gk-grafana`:** `carls ALL=(root) NOPASSWD:` followed by exactly three commands,
    `/usr/bin/docker compose -f /etc/gk-grafana/compose.yml` with `up -d`, `down` or `ps`.
  - **In `cli`, `grafana(word) -> int`** for `up`, `down` and `status`:
    - it runs `["sudo", "-n", "/usr/bin/docker", "compose", "-f", "/etc/gk-grafana/compose.yml",
      ...]` with the matching arguments;
    - `status` also asks for `http://127.0.0.1:3300/api/health` and
      `http://127.0.0.1:9090/-/ready`, with a 2-second limit each;
    - it refuses unless `sys.platform` starts with `linux`, with exit 2.

- [ ] **Step 1: Write the failing tests:**
  - `test_dashboard_json_parses_and_uses_datasource_uid`.
  - `test_dashboard_metric_names_exist_in_receiver_output`: render `Metrics` after adding the
    fixture lines, then check that every `gk_ledger_*` name in the dashboard's expressions is
    present.
  - `test_grafana_command_argv_exact` (a fake runner).
  - `test_grafana_refuses_off_linux`.
  - `test_grafana_status_reads_both_health_urls`, with fake local servers on patched ports.
- [ ] **Step 2: Run the tests.** Expected: they fail.
- [ ] **Step 3: Write the assets** and the `grafana` command, and write `deploy/grafana/README.md`:
  1. P8.
  2. `sudo install -d /etc/gk-grafana`, then copy the files root-owned.
  3. Write the admin password into `grafana.env` (mode 0600) with a command that never prints
     it, for example
     `python3 -c 'import secrets;print("GF_SECURITY_ADMIN_PASSWORD="+secrets.token_urlsafe(24))' | sudo tee /etc/gk-grafana/grafana.env >/dev/null`.
  4. `sudo visudo -cf deploy/grafana/sudoers-gk-grafana`, then install it as
     `/etc/sudoers.d/gk-grafana`.
  5. Pull the images.
  6. `gkh grafana up`.
  7. The SSH forward from the Mac.
- [ ] **Step 4: Run the tests.** Expected: PASS, and the full suite OK.
- [ ] **Step 5: Commit:** `feat(grafana): local Prometheus and Grafana assets; gkh grafana`.

### Task 8: Stage 3, the switch on the box (Carl's go)

This runs after the final whole-branch review, and only on Carl's go.

- [ ] **Step 1: Check the peers.** Name any busy peer sessions and wait for them to go idle,
  using the busy-peer check from the routing plan's Task 20.
- [ ] **Step 2: Tag, merge and push.** Run `git -C ~/.gk-harness tag pre-ledger main` and push
  the tag. Then `git -C ~/.gk-harness merge --ff-only feat/usage-ledger` and push `main`.
- [ ] **Step 3: Update.** Run `~/.gk-harness/install.sh update`, then `gkh status`. Expected:
  drift none, `ledger: off`.
- [ ] **Step 4: Switch on.** Run `gkh ledger on`, then `gkh ledger status`. Expected: receiver
  up, and the five keys owned.
- [ ] **Step 5: Live checks.** Carl restarts or opens the sessions. Record each check in
  `docs/proofs/2026-11-ledger-switch.md`:
  - **P6:** a new desktop session's calls appear in today's ledger file within 10 seconds;
  - a terminal session, a `claude -p` run and a gk-selftest-plus-one-agent workflow each appear,
    with a project;
  - `kill $(cat ~/.claude/state/gk-ledger/receiver.pid)`, then one prompt: the receiver is back,
    and the next call is recorded;
  - `gkh report --since <today>` shows the `Ledger:` header, "By project" and background calls;
  - `grep` the day's ledger for a phrase from that day's prompt: no match.
- [ ] **Step 6: Publish the switch record** as a Claude Doc. The rollback is `gkh ledger off`;
  for the code, switch to the `pre-ledger` tag.

### Task 9: Stage 4, Grafana at the box (Carl present)

- [ ] **Step 1: P8,** with Carl. Check that Docker works with host networking, that
  `curl 127.0.0.1:4318/metrics` answers from the host, and that ports 3300 and 9090 are free.
- [ ] **Step 2: Follow `deploy/grafana/README.md`.** Every `sudo` step is Carl's, typed or
  approved by him. The image pull waits for his go.
- [ ] **Step 3: Live checks:**
  - `ss -ltnp` shows 9090 and 3300 on `127.0.0.1` only;
  - a new call shows in Grafana within a minute;
  - one day's spend in Grafana agrees with `gkh report --source ledger` within 1%;
  - `gkh grafana down` stops both containers and keeps their volumes.
- [ ] **Step 4: Record and publish.** Add the results to the switch record, and update its doc.
