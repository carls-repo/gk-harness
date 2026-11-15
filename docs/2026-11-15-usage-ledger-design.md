# gk-harness: per-call usage ledger

Draft for Carl, 15 November 2026. Branch `feat/usage-ledger`.

Updated later on 15 November with Stage 4, a local Grafana dashboard. Carl ruled out Grafana Cloud
and phone access.

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
  - the ids that tie the call to its session, agent and workflow run;
  - the project (repository), folder and branch the session was working in (section 4.8).
- **G3.** `gkh report` reads the ledger, so background calls and same-day totals show up.
  Transcripts still fill any gap.
- **G4.** Nothing leaves the machine.
- **G5.** A local Grafana dashboard of token use and cost (Stage 4). It runs on the box only, and
  Carl views it from the Mac through an SSH port forward.

**Non-goals**

- Grafana Cloud, or any dashboard reachable from off the box, the phone included (Carl, 15
  November).
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
 "project": "Goodricke", "root": "/home/carls/apps/mike-dev", "branch": "develop",
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

### 4.7 The Grafana dashboard (Stage 4)

Grafana is a live view. `gkh report` stays the exact record, with stage labels and the
transcript backfill.

**Metrics from the receiver**

- The receiver also serves `GET /metrics` on `127.0.0.1:4318`, in Prometheus text format.
- Every counter carries the labels `model`, `effort`, `agent_name`, `workflow` (the name, not the
  run id), `source` and `project`. No session, request or agent ids go into labels, so the label sets stay
  small. The counters:
  - `gk_ledger_requests_total`, plus the label `event` (`api_request` or `api_error`);
  - `gk_ledger_tokens_total`, plus the label `kind` (`input`, `output`, `cache_read`,
    `cache_write_5m` or `cache_write_1h`);
  - `gk_ledger_cost_usd_total`, from Claude Code's `cost_usd`, which equals the `routes.json`
    price for the current models;
  - `gk_ledger_large_prompts_total`: calls whose prompt (input plus cache read plus cache write)
    is over 100,000 tokens.
- The counters start at zero each time the receiver starts, and Prometheus treats that as a
  counter reset. Calls between the last scrape and a restart are missing from the charts only.
  The ledger and `gkh report` stay exact.

**Prometheus and Grafana in Docker**

- `deploy/grafana/` holds:
  - `compose.yml`;
  - `prometheus.yml`;
  - Grafana's provisioning: the data source and the dashboard JSON.
- Both containers use host networking, so Prometheus can reach the receiver on `127.0.0.1`.
  - Prometheus listens on `127.0.0.1:9090`.
  - Grafana listens on `127.0.0.1:3300`. Ports 3000 and 3001 are Goodricke's. Ports 9090 and 3300
    were free on 15 November.
- Prometheus scrapes every 15 seconds and keeps data for 400 days.
- Data sits in Docker named volumes, not on `/srv/gkdata`, so the stack comes back after a reboot
  without the LUKS unlock. Both containers are `restart: unless-stopped`.
- Image versions are pinned exactly at build time. Pulling them is a download, so it happens on
  Carl's go.

**Running it**

- Carl's user is not in the `docker` group (checked 15 November), and joining it would amount to
  root. So the stack follows the mikeref pattern:
  - the compose files are installed root-owned in `/etc/gk-grafana/`;
  - an exact-command sudoers rule, `/etc/sudoers.d/gk-grafana`, allows only
    `docker compose -f /etc/gk-grafana/compose.yml` with `up -d`, `down` or `ps`;
  - `gkh grafana up|down|status` runs those commands.
- Setting this up needs Carl at the box.

**Access**

- Anonymous viewers can see the dashboard.
- The admin password is generated at setup into `/etc/gk-grafana/grafana.env` (root, mode 0600),
  and is never printed into a session.
- From the Mac: `ssh -L 3300:127.0.0.1:3300 <box>`, then open `http://localhost:3300`.

**The dashboard, "gk-harness usage"**

- spend today and over the last 7 days;
- spend per day by model;
- Fable's share of spend;
- tokens per hour by kind;
- spend by project, by workflow and by agent;
- background calls' spend;
- errors per hour;
- Haiku calls over 100K tokens;
- whether the receiver is up (Prometheus's `up`).

### 4.8 Projects

Carl asked on 15 November for usage broken down by project. Claude Code's events carry the
session id but not the folder the session works in, so the harness records that itself.

**The session map**

- On every SessionStart and every UserPromptSubmit, the hooks write
  `state/gk-ledger/sessions/<session id>.json`, holding:
  - `cwd`, from the hook input;
  - `root`: the git top level of `cwd`, or `cwd` itself outside a repository;
  - `project`;
  - `branch`, the current git branch.
- Refreshing it on every prompt follows a branch switch or a worktree change mid-session.
- The map is kept after the session ends, and the receiver deletes entries older than 90 days
  when it starts.
- Subagents and workflow agents send their parent session's `session.id` (Proof P3), so they take
  that session's project.

**The project name**, in order:

1. the `name` in `.claude/gk-project.json` at the top level (for example "Goodricke");
2. otherwise the main repository's folder name, so every worktree of one repository shares a
   name, with a leading dot dropped (`.gk-harness` becomes "gk-harness");
3. outside a repository, "(no repo)".

**Where it shows**

- **The ledger.** The receiver looks up each event's `session.id` in the map, caching it and
  re-reading a file when it changes. It writes `project`, `root` and `branch` into the line. A
  session with no map entry gets `null`, shown as "(unknown project)". An example is a `claude -p`
  run that keeps no session record.
- **The report.**
  - It adds "By project" and "By folder" tables.
  - A transcript-only call takes its project from the transcript line's own `cwd` and
    `gitBranch`, resolved the same way. If that folder no longer exists, the project falls back
    to the folder's name.
- **Grafana.** Every counter gains a `project` label. Branch and folder stay out of the labels,
  because branches keep growing in number.

**Privacy.** The ledger now holds folder paths and branch names. They are Carl's own names, never
prompt or file content.

## 5. Proofs to run before building

These run in short `claude -p` runs on the subscription, with no API spend. A throwaway receiver
in the scratchpad records what arrives.

- **P1. Payload shape.** Capture one real OTLP JSON logs body. Record where the event name sits
  (in the body, or as an `event.name` attribute), and each attribute's key and value type. The
  allowlist is written from this capture.
- **P2. Request ids.** The event's `request_id` equals the transcript line's `requestId` for the
  same call.
- **P3. Agent ids.** For a subagent and for a workflow agent, `agent_id` equals the transcript's
  `agent-<id>`, and `workflow.run_id` equals the `wf_` folder name. Their events also carry the
  main session's `session.id`, which the project lookup relies on.
- **P4. Exit.** A short `claude -p` delivers its last events before it exits, with nothing lost
  in the final five-second window.
- **P5. Receiver down.** With no listener, a `-p` run shows no error and no slowdown. Compare
  wall times with and without a listener, and record whether Claude Code retries or drops.
- **P6. Desktop.** A desktop Code-tab session started after the settings change sends events.
- **P7. Endpoint.** `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` is used exactly as given, path included,
  and no metrics or traces go anywhere.

- **P8. Docker (before Stage 4, with Carl at the box).** Both containers work with host
  networking, Prometheus can scrape `127.0.0.1:4318`, and ports 3300 and 9090 are still free.

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
- **Prometheus or Grafana down.** Only the charts stop. The ledger carries on.

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
- **Projects:**
  - the map is written at session start and refreshed on a prompt after a branch switch;
  - the three naming rules: a `gk-project.json` name, a worktree named after its main repository,
    and "(no repo)";
  - the receiver stamps the project from the map;
  - an unknown session gives `null`;
  - an entry over 90 days old is pruned;
  - the report's by-project table, including transcript-only calls.
- **Metrics:**
  - `/metrics` parses as Prometheus text;
  - labels carry no ids;
  - the counters add up line by line, including the large-prompt threshold;
  - label values are escaped;
  - the counters start at zero on restart.
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
- **Stage 4, Grafana, on Carl's go and with Carl at the box.**
  1. Run Proof P8.
  2. Install `/etc/gk-grafana/` and the sudoers rule.
  3. Pull the pinned images.
  4. Run `gkh grafana up`.
  5. Run the live checks:
     - `ss -ltn` shows 9090 and 3300 on `127.0.0.1` only;
     - a new call shows in Grafana within a minute;
     - one day's spend in Grafana agrees with `gkh report --source ledger` within 1%;
     - `gkh grafana down` stops both containers and keeps their data.

**Done when**

- The box's ledger for one full day holds every model call that Claude Code made on the box
  that day. The Goodricke app's own calls are excepted (section 2).
- That day's report total is at least the transcript-only total, and the difference is labelled
  background.
- The ledger holds no content: a search for a known prompt phrase finds nothing.
- Every call from a session started after the switch carries a project, and the day's report
  splits spend by project.
- Stage 4's live checks pass.

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

- **Grafana fed from the receiver's own `/metrics`,** not from Claude Code's metrics through a
  collector. There is one pipeline, so Grafana and `gkh report` count the same calls.
- **Grafana cost from Claude Code's `cost_usd`.** This saves a second copy of the price code in
  the receiver. It equals `routes.json` for the current models.
- **Counters start at zero on restart.** If they were rebuilt from the day's file, Prometheus
  would count the day twice.
- **Host networking, loopback-bound, on ports 9090 and 3300.**
- **The mikeref pattern for Docker:** root-owned files plus an exact-command sudoers rule, rather
  than adding Carl to the `docker` group.
- **Anonymous viewers, and an admin password kept in a root-only file.**
- **400 days of Prometheus data in Docker volumes.**
- **A project is a repository, and its worktrees share its name.** The folder and branch are
  kept in the ledger and the report.
- **A session map written by the hooks.** Claude Code's events carry no folder.
- **`project` as a Grafana label, but not branch or folder.** That keeps the label sets small.

**Carl's**

- **Decided 15 November:** no Grafana Cloud and no phone access. Grafana is local only.
- **Decided 15 November:** usage is broken down by project. A breakdown by Goodricke's own
  features stays out (section 2).
- Nothing else is left to decide. The builder check raised five questions, and each is settled
  above.
