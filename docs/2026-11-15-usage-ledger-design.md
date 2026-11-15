# gk-harness: per-call usage ledger

Draft for Carl, 15 November 2026. Branch `feat/usage-ledger`.

## 1. Why

On 15 November Carl asked for automatic token and cost tracking on every model call. Today
`gkh report` prices the transcripts only when someone runs it. That leaves two gaps:

- **Nothing is recorded as a call happens.** A running total needs a report run.
- **Claude Code's background calls are missed.** Calls for session titles, summaries,
  classifiers and prompt suggestions are never written to a transcript, so the report never
  sees them. In one long session, Claude Code's own cost record holds about 1.1M Haiku input
  tokens that no transcript shows. At Haiku prices that is under a dollar, so the gap is real
  but small.

## 2. Goals, non-goals and anti-goals

**Goals**

- **G1.** One ledger line per model API request, written within seconds of the call. This
  covers every Claude Code process this user runs on the box:
  - desktop Code-tab sessions and terminal sessions;
  - `claude -p` runs;
  - subagents and workflow agents.
- **G2.** Each line carries:
  - the model and effort;
  - the token counts: input, output, cache read, and cache write (5-minute and 1-hour);
  - the cost in dollars and the duration;
  - the ids that tie the call to its session, agent and workflow run.
- **G3.** `gkh report` reads the ledger, so background calls and same-day totals show up.
  Transcripts still fill any gap.
- **G4.** Nothing leaves the machine.

**Non-goals**

- Dashboards, Prometheus or Grafana.
- Budgets or alerts.
- A status line: the desktop app does not show a custom one.
- The laptop and the cloud. They follow with October Tasks 14 and 15, once the box works.
- Windows. In this version, `gkh ledger on` refuses there.
- **The Goodricke app's own model calls.** The backend starts the CLI with
  `--setting-sources ""` and `--no-session-persistence` (`backend/src/lib/docreviewLlm.ts`). So
  user settings are never read, telemetry stays off, and no transcript is written. The app already
  records each call's tokens and `cost_usd` in its audit log (`backend/src/lib/audit.ts`).

**Anti-goals.** Any of these is a failure, even if the ledger works:

- Prompt text, a response, a tool input or file content written to the ledger.
- A session slowed, blocked or broken because the receiver is down.
- A listener that can be reached from off the machine.

## 3. What Claude Code provides

Checked on 15 November 2026 against CLI 2.1.325, the version on the box. The sources are
listed at the end of this section.

**Switching it on**

- Telemetry is off until `CLAUDE_CODE_ENABLE_TELEMETRY=1` is set and an exporter is chosen.
- The event exporter is set with `OTEL_LOGS_EXPORTER` (`otlp` here).
- The wire protocol is set with `OTEL_EXPORTER_OTLP_PROTOCOL` (`http/json` here).
- The address is set with `OTEL_EXPORTER_OTLP_ENDPOINT`, or with a per-signal endpoint variable.
- Events are sent in batches, every `OTEL_LOGS_EXPORT_INTERVAL` (5,000 ms by default).

**The per-call event**

`claude_code.api_request` is emitted once per API request. Its attributes:

- `model`, `effort`, `speed` and `query_source`;
- `input_tokens`, `output_tokens` and `cache_read_tokens`;
- `cache_creation_tokens`, with a 5-minute and 1-hour split since 2.1.300;
- `cost_usd`, `cost_usd_micros` and `duration_ms`;
- `request_id`, `session.id`, `prompt.id`, `event.timestamp` and `event.sequence`;
- `agent.name`;
- inside subagents, `agent_id` and `parent_agent_id` (2.1.310 and later);
- for workflow agents, `workflow.run_id` and `workflow.name`.

A failed request emits `claude_code.api_error` instead, with its `status_code` and `attempt`.

**What stays private by default**

- Prompt text, responses, tool details and raw request bodies are off by default. Each sits
  behind its own `OTEL_LOG_*` variable.
- Project settings cannot switch telemetry on or redirect it. User settings can: the `env`
  block of `~/.claude/settings.json`.
- Desktop Code-tab sessions run over SSH and read the box's `~/.claude/settings.json`.
- Events go only to the configured endpoint. Nothing from this telemetry goes to Anthropic.

**Price and matching**

- `cost_usd` is Claude Code's own list-price sum. For the current models it equals the
  `routes.json` prices, including Haiku's rate over 100K tokens.
- It differs from `routes.json` in five cases: older model ids, fast mode, US-only inference,
  web-search requests and a `modelPricing` override.
- Assistant lines in the transcripts carry a `requestId`, so a ledger line can be matched to its
  transcript call.

**What doesn't help**

- No hook fires per model request. `SubagentStop` fires for workflow agents too, but it carries
  no token usage.
- The status line holds session totals, but the desktop app does not show it.
- `/usage` (alias `/cost`) shows session totals only.

**Sources:**

- code.claude.com/docs/en/ pages: `monitoring-usage`, `costs`, `hooks`, `statusline` and `desktop`;
- the installed 2.1.325 bundle;
- this box's transcripts.

The research run was gk-research `wf_90077886-fec`, on 15 November.

## 4. Design

The data flows in four steps:

1. Every Claude Code process sends its `api_request` events to `127.0.0.1:4318`, over OTLP
   HTTP with JSON bodies.
2. The gk-ledger receiver keeps the allowed fields of each event.
3. It appends one line per call to `~/.claude/state/gk-ledger/<UTC day>.jsonl`.
4. `gkh report` reads those lines and matches them to the transcripts by request id.

### 4.1 Settings

The ledger is off until `gkh ledger on`, and `install` and `update` never switch it on. The
switch is the file `state/gk-ledger-on.json`.

While the ledger is on, the installer owns five `env` keys in `~/.claude/settings.json`. They
follow the same rules as the four keys it owns now:

- the value found first is recorded, and `gkh ledger off` or uninstall puts it back;
- a value changed by hand is left alone, and `status` lists it.

`gkh ledger off` takes the keys out of ownership as well as putting their values back. So a key
that is off never shows as a hand change, and a later `update` does not write it again.

| Key | Value |
|---|---|
| `CLAUDE_CODE_ENABLE_TELEMETRY` | `1` |
| `OTEL_LOGS_EXPORTER` | `otlp` |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `http/json` |
| `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` | `http://127.0.0.1:4318/v1/logs` |
| `OTEL_METRICS_EXPORTER` | `none` |

The logs endpoint is set on its own, so nothing else is exported anywhere. The installer never
sets an `OTEL_LOG_*` content variable. If one is set by hand, `gkh ledger status` warns, and the
receiver drops the content anyway.

### 4.2 The receiver

The receiver is `hooks/gk_ledger.py`. It uses the standard library only, and is installed with
the hooks into `~/.claude/hooks/gk-harness/`. That way uninstall removes it, and the checkout
can sit on any branch.

**What it accepts**

- It listens on `127.0.0.1:4318` only.
- It accepts `POST /v1/logs` with a JSON body of up to 4 MB, and replies `200 {}`.
- `GET /gk-ledger/health` replies with `{"gk-ledger": "<version>"}`. Every other path gets a 404.

**What it writes**

- For each `claude_code.api_request` or `claude_code.api_error` record, it writes one line of
  allowlisted fields (section 4.3). Every other record and attribute is dropped.
- It keeps only records whose resource names Claude Code as the service. Programs that a
  session starts inherit the `env` block, so any OpenTelemetry-instrumented tool would also send
  to this port. Proof P1 confirms the attribute.
- It appends to the day file named by the event's UTC timestamp, one line per `write`, opened
  with `O_APPEND`. Lines are under 4 KB, so they never interleave.

**How it starts**

- Nothing starts while the ledger is off.
- On every prompt, the UserPromptSubmit hook (`gk-peers.py`) tries a TCP connect to the port,
  with a 100 ms limit. If the connect is refused, it starts the receiver as below. This brings
  the receiver back within one prompt after a crash, even in a session that runs for days.
- The SessionStart hook (`gk-seat.py`) asks `/gk-ledger/health`, then acts on the answer:
  - **Healthy:** nothing to do.
  - **Port free:** it starts `gk_ledger.py serve` as a detached process, with its own session,
    stdio to `/dev/null` and a log at `state/gk-ledger/receiver.log`. It then waits up to one
    second for the port.
  - **Port held by another program:** it starts nothing, and the card says so (section 4.6).
- The pidfile is `state/gk-ledger/receiver.pid`.

**How it stops**

- It exits after 30 minutes with no request and no live session. A session counts as live when
  its `sessions/*.json` names a pid that is still running.
- While the ledger is on, `install` and `update` restart a running receiver at once, so new code
  takes over without waiting for a session.
- `gkh ledger off` and `uninstall` stop it through its pidfile.
- Calls made while it was down still count in the report, from the transcripts.

It is not a systemd unit. The box has no user linger, and the same code then works on the
laptop.

### 4.3 The ledger line

One JSON object per line, keys in this order. Absent values are `null`.

```json
{"ts": "2026-11-15T10:42:07.512Z", "event": "api_request", "session": "<session.id>",
 "prompt": "<prompt.id>", "request": "<request_id>", "model": "claude-opus-5-5",
 "effort": "medium", "speed": "normal", "source": "<query_source>",
 "agent": "<agent_id>", "parent": "<parent_agent_id>", "agent_name": "<agent.name>",
 "workflow_run": "<workflow.run_id>", "workflow": "<workflow.name>",
 "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
 "cache_write_5m": 0, "cache_write_1h": 0, "cost_usd": 0.0, "duration_ms": 0,
 "status": null, "attempt": null}
```

- `status` and `attempt` are filled only on `api_error` lines.
- A line is about 450 bytes. At 400 to 2,000 calls a day, that is under 1 MB a day.
- Files are kept, and nothing rotates them in this version.
- The attribute names and value types are taken from Proof P1 before the allowlist is written.

### 4.4 `gkh report`

- **Sources.** It reads the ledger lines in the `--since` window. Transcripts are still read for
  stage labels and route lines.
- **Merging.** Ledger lines and transcript calls are matched by request id:
  - a call found in both counts once, with the ledger's figures;
  - a transcript call with no ledger line counts from the transcript, because the receiver was
    down or the call predates the ledger;
  - a ledger line with no transcript match is grouped under the agent column as
    `background (<query_source>)`.
- **Stage labels.** A workflow run id and agent id lead to the run's `journal.jsonl`, as today.
  Proof P3 confirms the ids match.
- **Price.** It prices from `routes.json`, as today. If Claude Code's `cost_usd` total differs
  from that price by more than 1%, it adds a line saying so.
- **Gaps.** A new header line reads `Ledger: <n> calls since <day>; transcripts filled <m>`, so
  gaps show.
- **Checking.** `--source ledger|transcripts|both` picks the source; the default is `both`.

### 4.5 `gkh ledger on|off|status`

- **`on`** writes `state/gk-ledger-on.json`, then writes the five keys, recording what was there
  before. It starts the receiver. On Windows it refuses.
- **`off`** puts the recorded values back, takes the keys out of ownership, stops the receiver and
  removes the switch file.
- Either change reaches running sessions only when they restart: Claude Code reads the `env` block
  at session start.
- **`status`** prints:
  - whether the receiver is up;
  - the port;
  - today's line count and the time of the last line;
  - the state of the five keys;
  - a warning for any `OTEL_LOG_*` content variable.
- `gkh status` gains one ledger line.

### 4.6 The seat card

While the ledger is on and no healthy receiver answers after SessionStart, the card adds:
`Ledger: receiver not running, calls are not being recorded (gkh ledger status)`. Otherwise
the card is unchanged.

## 5. Proofs to run before building

These run in short `claude -p` runs on the subscription, with no API spend. A throwaway receiver
in the scratchpad records what arrives.

- **P1. Payload shape.** Capture one real OTLP JSON logs body. Record where the event name sits
  (in the body, or as an `event.name` attribute), and each attribute's key and value type. The
  allowlist is written from this capture.
- **P2. Request ids.** The event's `request_id` equals the transcript line's `requestId` for the
  same call.
- **P3. Agent ids.** For a subagent and for a workflow agent, `agent_id` equals the transcript's
  `agent-<id>`, and `workflow.run_id` equals the `wf_` folder name.
- **P4. Exit.** A short `claude -p` delivers its last events before it exits, with nothing lost
  in the final five-second window.
- **P5. Receiver down.** With no listener, a `-p` run shows no error and no slowdown. Compare
  wall times with and without a listener, and record whether Claude Code retries or drops.
- **P6. Desktop.** A desktop Code-tab session started after the settings change sends events.
- **P7. Endpoint.** `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` is used exactly as given, path included,
  and no metrics or traces go anywhere.

If P4 or P5 fails, the design still holds: the transcripts are the backstop, and the report
labels the gap. If P1 shows content in default records, the allowlist already drops it.

## 6. Failure handling and privacy

**Failures**

- **Receiver down or never started.** Sessions carry on (P5), and the report fills from the
  transcripts. The card says the ledger isn't recording.
- **Receiver crashed or stopped mid-session.** The next prompt's probe starts it again. Calls in
  between count from the transcripts.
- **Port held by another program.** No receiver starts, and the card says so. Export fails
  quietly (P5).
- **Two sessions start at once.** One binds the port. The other finds it busy, gets a healthy
  answer, and does nothing.
- **Bad or oversized body.** The receiver replies 400 and writes nothing; `receiver.log` keeps a
  count.
- **Disk full.** The write fails. The receiver logs it once and keeps serving.

**Privacy**

- Only allowlisted fields are written. The installer never sets an `OTEL_LOG_*` variable, and
  `status` warns if one is set by hand.
- The receiver binds to the loopback address only, with no auth token. This is a single-user
  box: a local process could only add false lines.
- The ledger holds no content, so it needs no de-id handling.
- **Programs started from a session inherit the variables.** The receiver keeps only Claude
  Code's own records, and `OTEL_METRICS_EXPORTER=none` silences those programs' metrics. Neither
  Goodricke package uses OpenTelemetry today.

## 7. Tests, stages and done-when

**Tests**

- **Receiver:**
  - parses the P1 fixture and writes exactly the allowlisted fields;
  - drops other events, and drops content attributes even when present;
  - rejects a non-JSON body, a wrong path and a body over 4 MB;
  - binds to `127.0.0.1` only, and picks the day file by UTC timestamp;
  - keeps lines whole under concurrent posts.
- **Start logic:**
  - a free port leads to a spawn;
  - its own receiver on the port means no action;
  - another program on the port gives the card notice;
  - the prompt probe restarts a dead receiver;
  - nothing starts while the ledger is off;
  - an idle receiver exits;
  - `install` and `update` restart it while the ledger is on.
- **Report:**
  - merging by request id, for a call in both sources, ledger only and transcripts only;
  - background grouping and the header counts;
  - the cost-difference line and `--source`.
- **Installer:**
  - `install` and `update` leave the ledger off;
  - `gkh ledger on` owns the five keys, and `off` puts the prior values back and gives up
    ownership, with nothing listed as a hand change;
  - uninstall while on restores the prior values;
  - a hand change is listed;
  - `on` refuses on Windows;
  - the status line.
- Like the rest of the harness: Python 3.9 or later, standard library only.

**Stages**

- **Stage 0.** Proofs P1 to P7, recorded in `docs/proofs/`.
- **Stage 1.** The receiver, the start logic and the ledger format, with their tests.
- **Stage 2.** The report merge, `gkh ledger`, the installer keys and the card notice, with their
  tests.
- **Stage 3.** Switch on the box, on Carl's go: `gkh ledger on`, then restart sessions. Then run
  the live checks:
  - a desktop session, a terminal session, a `-p` run and a workflow each appear in the ledger
    within 10 seconds;
  - `gkh report --since <today>` shows background calls;
  - a killed receiver comes back on the next prompt.

**Done when**

- The box's ledger for one full day holds every model call that Claude Code made on the box
  that day. The Goodricke app's own calls are excepted (section 2).
- That day's report total is at least the transcript-only total, and the difference is labelled
  background.
- The ledger holds no content: a search for a known prompt phrase finds nothing.

## 8. Calls made, and what is Carl's

**Calls I made**

- **Built-in telemetry, not a turn-end hook.** It works per call, includes the background calls,
  and adds no wait to any turn.
- **A receiver started by the SessionStart hook, not systemd.** The box has no user linger, and
  the same code will run on the laptop.
- **Port 4318 on `127.0.0.1`.** It is the standard OTLP HTTP port, and free on the box today.
- **Logs only.** Metrics and traces stay off.
- **Prices from `routes.json`, as now.** Claude Code's `cost_usd` stays in each line for
  cross-checking.
- **No auth token on the listener.**
- **Ledger files kept indefinitely** (about 1 MB a day).
- **Off until `gkh ledger on`.** Install and update never switch it on, so the laptop and other
  machines stay untouched.
- **Linux and macOS only.** On Windows, `gkh ledger on` refuses for now.
- **A probe on every prompt.** The probe is a TCP connect of up to 100 ms, which brings the
  receiver back within one prompt. Install and update also restart the receiver.
- **Programs started from a session inherit the variables.** This is accepted, because the
  receiver keeps only Claude Code's own records.
- **The Goodricke app's calls are out of scope.** They skip user settings, and the app's audit
  log already holds their tokens and cost.

**Carl's**

- Nothing is left to decide. The builder check raised five questions, and each is settled above.
