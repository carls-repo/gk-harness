Date: 16 November 2026
Claude Code: 2.1.325 (`claude --version`)

# Stage 0 proofs for the usage ledger (P1 to P5, P7)

Live checks of what Claude Code's OpenTelemetry log export sends, run before the ledger relies on
it (spec `docs/2026-11-15-usage-ledger-design.md`, sections 3 and 5). P6 (a desktop session)
needs the live settings, so it runs in Task 8. P8 (Docker) runs before Stage 4.

**Setup.**
- Scratch directory: `<session scratchpad>/ledger-proofs/`. It is not a git repository. It held a
  capture receiver (`receiver.py`, Python `http.server` on `127.0.0.1:4319`) that wrote each
  request's method, path, headers and body to a numbered file and answered `200 {}`. The
  receiver and the captures were never committed. The receiver was stopped at the end.
- Every `claude -p` run started from that directory, through a wrapper. The wrapper ran
  `env -i` with only `HOME`, `PATH`, `USER`, `LANG` and `TERM=dumb`, so the desktop host's session
  variables were dropped. It then set, on the command line only:
  `CLAUDE_CODE_ENABLE_TELEMETRY=1 OTEL_LOGS_EXPORTER=otlp OTEL_EXPORTER_OTLP_PROTOCOL=http/json
  OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=http://127.0.0.1:4319/v1/logs OTEL_METRICS_EXPORTER=none`,
  and ran `timeout 300 claude --model haiku …`. No `OTEL_LOG_*` variable was set.
  `~/.claude/settings.json` was not edited, and it holds no `OTEL_*` key.
- Billing: the runs used the claude.ai subscription login. The `cost_usd` values are notional
  list prices, not charges.
- Transcripts: `~/.claude/projects/<scratch slug>/<session id>.jsonl`, with subagents under
  `<session id>/subagents/`. Comparisons printed only request ids, agent ids, run ids and field
  names, never message text.

## P1: payload shape

**Check.** One short run, `claude --model haiku -p "Reply with the word ok."` (session
`f78e262b…`). Read the captured body's structure and each attribute's key and value type.

**Seen.**
- One `POST /v1/logs`, `Content-Type: application/json`,
  `User-Agent: OTel-OTLP-Exporter-JavaScript/0.208.0`. No authorisation header.
- The body is standard OTLP JSON: `resourceLogs[].resource.attributes`,
  `resourceLogs[].scopeLogs[].scope`, `scopeLogs[].logRecords[]`.
- **Resource attributes:** `service.name` = `"claude-code"`, `service.version` = `"2.1.325"`,
  `host.arch`, `os.type`, `os.version` (all `stringValue`).
- **Scope:** `{"name": "com.anthropic.claude_code.events", "version": "2.1.325"}`.
- **Each log record** has the keys `timeUnixNano`, `observedTimeUnixNano` (both JSON strings),
  `body`, `attributes` and `droppedAttributesCount`. There is no `severityText` or
  `severityNumber`.
- **The event name sits in two places.** The record body is
  `{"stringValue": "claude_code.api_request"}`, with the prefix. The attribute `event.name` is
  `"api_request"`, without the prefix.
- **Integer attributes are JSON numbers**, for example `{"intValue": 251}`, not the strings that
  the OTLP JSON mapping allows for 64-bit integers. `cost_usd` is a `doubleValue`.
- One body carried 51 records: `managed_settings_resolved`, `hook_registered` (20),
  `plugin_loaded` (13), `mcp_server_connection` (8), `hook_execution_start` and
  `hook_execution_complete` (3 each), `user_prompt`, `api_request` and `assistant_response`.
  Other runs also sent `tool_decision`, `tool_result`, `skill_activated` and
  `subagent_completed`.

**`claude_code.api_request` attributes, in the order sent:**

| Key | Type | Notes |
|---|---|---|
| `user.id` | stringValue | 64 hex characters (an installation hash) |
| `session.id` | stringValue | the main session's id |
| `organization.id` | stringValue | UUID |
| `user.email` | stringValue | the account email |
| `user.account_uuid` | stringValue | UUID |
| `user.account_id` | stringValue | `user_` and 24 characters |
| `anthropic.provider` | stringValue | `firstParty` |
| `anthropic.custom_base_url` | boolValue | `false` |
| `anthropic.plan` | stringValue | `max` |
| `terminal.type` | stringValue | `dumb` here (the wrapper's `TERM`) |
| `event.name` | stringValue | `api_request` |
| `event.timestamp` | stringValue | ISO 8601, 24 characters |
| `event.sequence` | intValue | |
| `prompt.id` | stringValue | UUID |
| `workflow.run_id` | stringValue | workflow agents only, e.g. `wf_f8803e0f-b36` |
| `workflow.name` | stringValue | workflow agents only; `custom` for an inline script (see P3) |
| `model` | stringValue | `claude-haiku-5-5` |
| `input_tokens` | intValue | |
| `output_tokens` | intValue | |
| `cache_read_tokens` | intValue | |
| `cache_creation_tokens` | intValue | the sum of the two below |
| `cache_creation_5m_tokens` | intValue | |
| `cache_creation_1h_tokens` | intValue | |
| `cost_usd` | doubleValue | e.g. `0.005843` |
| `cost_usd_micros` | intValue | e.g. `5843` |
| `duration_ms` | intValue | |
| `ttft_ms` | intValue | not named in the spec |
| `request_id` | stringValue | `req_…`, 28 characters |
| `client_request_id` | stringValue | UUID; not named in the spec |
| `speed` | stringValue | `normal` |
| `query_source` | stringValue | see the list below |
| `effort` | stringValue | `medium`, `low` |
| `agent.name` | stringValue | subagents and workflow agents only |
| `skill.name` | stringValue | only when a skill was active (e.g. `workflow-authoring`) |
| `agent_id` | stringValue | subagents and workflow agents only, 17 characters |
| `parent_agent_id` | stringValue | nested subagents only (see P3) |

**`claude_code.api_error` attributes.** One error was provoked with an unknown model id
(`--model claude-haiku-0-0-nonexistent`, a 404, exit code 1). The record had the same
identity attributes as above (`user.id` to `prompt.id`), then `model` (stringValue), `error`
(stringValue, the API's error message: here `model: <the model id>`), `status_code` (intValue,
`404`), `duration_ms` (intValue), `attempt` (intValue, `1`), `request_id` (stringValue),
`speed`, `query_source` (`sdk`) and `effort` (all stringValue). It carries no token or cost
fields.

**Content.** The `user_prompt` record carries `prompt` and `prompt_text`, and the
`assistant_response` record carries `response`. All three held exactly `<REDACTED>` in every
capture. `prompt_length` (stringValue) and `response_length` (intValue) hold real lengths. No
`api_request` or `api_error` record carries prompt or response text.

**`query_source` values seen:** `sdk` (the main session of a `-p` run),
`agent:custom` (a user-defined agent; here the user-level `Explore` override),
`agent:builtin:general-purpose` and `agent:builtin:workflow-subagent`. An interactive or desktop
session was not run, so its main-session value is not known yet (P6 will show it).

**Conclusion.** Positive. The allowlist can key on the record body
(`claude_code.api_request`, `claude_code.api_error`) or on `event.name`; both are present.
Every field the ledger needs is in the table, with the types shown.

**Ruling:** the parser reads integer attributes as JSON numbers and also accepts the string form
the OTLP JSON mapping allows, because 2.1.325 sends numbers.

**Ruling:** the spec's attribute list (section 3) is completed by this table: `ttft_ms`,
`client_request_id`, `cache_creation_5m_tokens`, `cache_creation_1h_tokens`, `skill.name`,
`user.account_id`, `user.id` and `anthropic.plan` exist and are not in the spec. Identity
attributes (`user.email`, `user.account_uuid`, `user.account_id`, `organization.id`, `user.id`)
are not ledger fields and are dropped.

## P2: request ids

**Check.** Compare the `request_id` of each `api_request` record with the `requestId` of the
assistant lines in the run's transcripts, main and subagent files together.

**Seen.**
- P1 run: 1 transcript call, 1 event, the same id (`req_011Ch6J9o6xNMvZrvXHWTQnq`).
- Across every run below with a listener (P1, P3, the nested-agent run, the five P4 runs and the
  three P5 runs with a listener), 23 transcript calls and 23 events matched one to one. No event
  lacked a transcript call, and no call lacked an event.
- `tool_decision` and `tool_result` records also carry the `request_id` of the call that made
  the tool call.

**Conclusion.** Positive. `request_id` equals the transcript's `requestId`, so a ledger line
can be matched to its transcript call.

## P3: agent ids, workflow run ids and the session id

**Check.** One run with `--allowedTools "Agent,Workflow"` (session `5dbefd3d…`). The one-line
prompt asked for one Explore subagent that lists the folder, then one inline Workflow whose only
step is an `agent()` call with `{model:'haiku', effort:'low'}`. A second run with
`--allowedTools "Agent"` (session `4528f97b…`, see below) asked for a general-purpose subagent that
starts its own general-purpose subagent, to see `parent_agent_id`.

The Workflow tool **was available** in `-p` mode. Its run was `wf_f8803e0f-b36`.

**Seen: run 1 (8 calls, 8 events).**

| Caller | Transcript file | `agent_id` | `workflow.run_id` | `workflow.name` | `agent.name` | `query_source` | `effort` |
|---|---|---|---|---|---|---|---|
| main session (5 calls) | `<session>.jsonl` | absent | absent | absent | absent | `sdk` | `medium` |
| Explore subagent (2 calls) | `subagents/agent-a16b52a2f13903ba1.jsonl` | `a16b52a2f13903ba1` | absent | absent | `custom` | `agent:custom` | `medium` |
| workflow agent (1 call) | `subagents/workflows/wf_f8803e0f-b36/agent-aa7e1a96e001d2b70.jsonl` | `aa7e1a96e001d2b70` | `wf_f8803e0f-b36` | `custom` | `workflow-subagent` | `agent:builtin:workflow-subagent` | `low` |

- Every record carried `session.id` = `5dbefd3d-e540-4c6e-92c1-765bcfee770b`, the main session's
  id, including the subagent's and the workflow agent's.
- The transcript lines in each agent file carry the same `agentId` and the main `sessionId`.
- The run's record `<session>/workflows/wf_f8803e0f-b36.json` has `runId: "wf_f8803e0f-b36"`
  and `workflowName: "haiku-ok-ping"` (the name the model gave the inline script). The script is
  saved as `<session>/workflows/scripts/haiku-ok-ping-wf_f8803e0f-b36.js`.
- The Explore subagent's `meta.json` says `"agentType":"Explore","isBuiltIn":false`: the
  user-level `Explore` agent replaces the built-in one, so telemetry calls it `custom`.
- Neither agent record carried `parent_agent_id`.

**Seen: run 2, nested agents (5 calls, 5 events).**

| Caller | `agent_id` | `parent_agent_id` | `agent.name` | `query_source` |
|---|---|---|---|---|
| main session (2 calls) | absent | absent | absent | `sdk` |
| first subagent, depth 1 (2 calls) | `a5f230de00fda48ae` | absent | `general-purpose` | `agent:builtin:general-purpose` |
| its subagent, depth 2 (1 call) | `a4bc4bf3d9968c839` | `a5f230de00fda48ae` | `general-purpose` | `agent:builtin:general-purpose` |

The depth-2 agent's `meta.json` has `"parentAgentId":"a5f230de00fda48ae","spawnDepth":2`, and its
transcript sits flat beside its parent's, in `subagents/agent-a4bc4bf3d9968c839.jsonl`. (These
general-purpose subagents ran on `claude-opus-5-5`, from the user settings' subagent model. That
does not affect the ids.)

**Conclusion.** Positive for `agent_id`, `workflow.run_id` and `session.id`: each equals the
transcript's `agent-<id>` file name, the `wf_` folder name and the main session's id. Workflow
agents were proven from a real Workflow tool run, not inferred.

**Ruling:** `parent_agent_id` is present only on a subagent started by another subagent. A
subagent or workflow agent started by the main session has no `parent_agent_id`; its parent is
the session. The spec's "inside subagents, `agent_id` and `parent_agent_id`" should read that
way.

**Ruling:** `workflow.name` is `custom` for an inline Workflow script, not the script's name.
The ledger takes a workflow's name from `<session>/workflows/<run id>.json` (`workflowName`)
when it needs it. A saved workflow run by name was not tested; it may carry its real name.

**Ruling:** `agent.name` is the built-in agent's name, or `custom` for any user or project
agent (including an override of a built-in name). The ledger takes the agent type from
`subagents/agent-<id>.meta.json` (`agentType`) when it needs it.

## P4: nothing lost at exit

**Check.** Five one-line `-p` runs. Compare the `api_request` records received with the
transcript calls, run by run.

**Seen.**

| Run | Prompt (one line) | Wall time | POSTs | Transcript calls | Events | Missing |
|---|---|---|---|---|---|---|
| 1 | reply with a word | 8.2 s | 1 | 1 | 1 | none |
| 2 | name a colour | 8.3 s | 1 | 1 | 1 | none |
| 3 | 2 plus 3 | 9.0 s | 1 | 1 | 1 | none |
| 4 | run `date` with Bash (`--allowedTools "Bash(date)"`) | 10.3 s | 2 | 2 | 2 | none |
| 5 | reply with a word | 8.0 s | 1 | 1 | 1 | none |

- The P3 runs (8 and 5 calls, 4 and 3 POSTs) and the P5 runs with a listener (1 each) also lost
  nothing.
- A run shorter than about 5 seconds sends one POST, at exit. A longer run sends a batch about
  every 5 seconds and a last one at exit.
- These walls include a 3-second wait: stdin was not redirected in this loop, and `-p` printed
  `Warning: no stdin data received in 3s, proceeding without it.` on stderr. That is the only
  stderr output. The P5 runs used `< /dev/null` and had none.

**Conclusion.** Positive. A short `-p` run delivers its last events before it exits. Nothing
was lost in the final window in 10 runs.

## P5: receiver down

**Check.** Three runs with the endpoint at a closed port (`http://127.0.0.1:4320/v1/logs`,
nothing listening), then three with the capture receiver listening. Each run:
`claude --model haiku -p "Reply with the word ok." --output-format json < /dev/null`.

**Seen.**

| Endpoint | Run 1 | Run 2 | Run 3 | Mean | Exit | stderr | POSTs received |
|---|---|---|---|---|---|---|---|
| closed port | 5.47 s | 5.43 s | 5.54 s | 5.48 s | 0, `success` | empty | none |
| listening | 5.33 s | 5.63 s | 5.43 s | 5.46 s | 0, `success` | empty | 1 each |

**Conclusion.** Positive. With no listener, a `-p` run shows no error and no slowdown (the
difference is 0.02 s, within noise). The events of a run with no listener are dropped: nothing
was seen to be retried, and Claude Code keeps no local queue that a later run sends. The
transcripts remain the backstop for such a gap.

## P7: endpoint used as given

**Check.** Record every method and path that reached the receiver across all runs.

**Seen.** 18 requests in all, every one `POST /v1/logs`. No `/v1/metrics` or `/v1/traces`
request arrived. `OTEL_METRICS_EXPORTER=none` was set and no traces exporter was set. Nothing in
the runs' output showed any other export target.

**Conclusion.** Positive. `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` is used exactly as given, path
included, and only log events are sent.

## Fixtures

Two real captured bodies, saved as `tests/fixtures/otlp/`:

- `logs-api-request.json`: one body from P4 run 4 (its second POST). It holds one main-session
  `api_request` (`query_source: sdk`), an `assistant_response`, a Stop hook pair and four
  `mcp_server_connection` records.
- `logs-subagent.json`: the third POST of P3 run 1 (session `5dbefd3d…`). It holds the workflow
  agent's `api_request` (`agent_id`, `workflow.run_id`, `workflow.name`, `agent.name`,
  `skill.name`), two main-session `api_request` records and other events. That body held no
  Explore-subagent call (those arrived in the run's previous POST), so the Explore subagent's
  first `api_request` record from that previous POST, same session, was added at the front of
  the same `logRecords` list. Nothing else was changed.

In both, these values were replaced with fixed fakes; every other key and value is as captured:

- `user.email` → `user@example.invalid`
- `user.account_uuid` → `00000000-0000-4000-8000-0000000000a1`
- `organization.id` → `00000000-0000-4000-8000-0000000000b2`
- `user.account_id` → `user_000000000000000000000000` (also an account identifier)
- `user.id` → 63 zeros and a `1` (an installation hash)

A grep for the five real values found none in either file. The only `prompt`, `prompt_text`
and `response` values in the fixtures are `<REDACTED>`.

## Not proven here

- P6 (a desktop session), in Task 8.
- The main-session `query_source` of an interactive or desktop session.
- `workflow.name` for a saved workflow run by name.
- An `api_error` with `attempt` above 1 (a retried 5xx or 429); only a 404 was seen.
