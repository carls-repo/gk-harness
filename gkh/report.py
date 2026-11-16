"""gkh report: prices the Claude Code transcripts on this machine from routes.json.

Claude Code keeps them under <claude dir>/projects/<project slug>/:

- <session>.jsonl, the main session (the seat);
- <session>/subagents/agent-<id>.jsonl, a subagent, with agent-<id>.meta.json naming its
  agentType;
- <session>/subagents/workflows/wf_<run>/agent-<id>.jsonl, a workflow agent; the run's
  journal.jsonl names each agent's label and phase in its "started" events, and
  <session>/workflows/wf_<run>.json names the workflow (workflowName).

Each assistant line carries message.id, message.model and message.usage. Content blocks of one
message are streamed as several lines with the same id: each id counts once, with the last line's
usage. A message that turns up in two files (a forked session copies its history) counts once,
where it was first found.

Privacy: the report prints only numbers, ids, model names, workflow names, stage labels and
dates. Message content is never printed; the seat's text is read in memory only, to find its
"Route: <class>" lines, and only a word that is a class in routes.json is kept; any other is
counted as UNRECOGNISED. Files are read line by line, never whole.

The usage ledger (spec 4.4, 4.8): <claude dir>/state/gk-ledger/<YYYY-MM-DD>.jsonl holds one line
per model call that Claude Code exported, written by hooks/gk_ledger.py. merge_sources matches
ledger calls with transcript calls by request id: a call found in both counts once, with the
ledger's tokens and Claude Code's own cost; a transcript call with no ledger line fills the gap;
a ledger call with no transcript is grouped under its agent name or "background (<source>)".
api_error lines are not model calls: they are counted apart (errors) and never priced.
"""
from __future__ import annotations

import bisect
import glob
import importlib.util
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

HEADER = "On a subscription, cost is a proxy for your share of the usage limits."
TITLE = "gk-harness report: Claude Code transcripts priced from routes.json"
NO_ROUTE = "(no route line)"
# A Route: line whose word is not a class in routes.json: the word may be prose, so it is
# never printed.
UNRECOGNISED = "(unrecognised route line)"
OLDER_IDS = "Older model ids are priced at their alias's current price."
NO_WORKFLOW = "(not in a workflow)"
NO_STAGE = "(no stage)"
SEAT = "seat"
SUBAGENT = "subagent"  # a subagent whose meta file is missing or names no agentType
WORKFLOW_AGENT = "workflow-subagent"
FABLE = "fable"
HAIKU = "haiku"
OVER_100K = 100_000
# Claude Code's own placeholder messages (an interrupted turn, an API error): no model call.
NOT_A_CALL = ("<synthetic>",)
TOP = 10
GROUPS = ("byModel", "byDay", "byWorkflow", "byWorkflowRun", "byStage", "byAgent", "bySession",
          "byProject", "byFolder")
TITLES = {"byModel": "By model", "byDay": "By day", "byWorkflow": "By workflow",
          "byWorkflowRun": "By workflow run", "byStage": "By workflow stage",
          "byAgent": "By agent", "bySession": "By session", "byProject": "By project",
          "byFolder": "By folder"}
NO_PROJECT = "(unknown project)"
NO_FOLDER = "(unknown folder)"
SOURCES = ("ledger", "transcripts", "both")
# The ledger folder under the claude dir (hooks/gk_common.py ledger_dir, gkh/ledger_ctl.py).
LEDGER_PARTS = ("state", "gk-ledger")
BACKGROUND = "background (%s)"
COST_TOLERANCE = 0.01  # Claude Code's cost_usd may differ from the routes.json price by this
TOKEN_KEYS = ("input", "output", "cache_write", "cache_read", "cache_write_1h", "cache_write_5m")
# Ledger line key -> call key; every other ledger key keeps its name.
LEDGER_RENAMES = {"request": "request_id", "agent": "agent_id", "cost_usd": "cc_cost"}
CLASS_NOTE = ("best effort: each call is put under the latest 'Route: <class>' line the seat "
              "wrote in its session before it")
_ROUTE = re.compile(r"^[\s>*_`]*Route:[\s*_`]*([A-Za-z][\w-]*)", re.M)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
# A run record holds the workflow's script, args and result; one this large is not read.
_RUN_RECORD_LIMIT = 32 * 1024 * 1024


def valid_date(text: str) -> bool:
    """True for a real calendar date written YYYY-MM-DD."""
    if not isinstance(text, str) or not _DATE.match(text):
        return False
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _open_lines(path: Path) -> Iterator[str]:
    """The file's lines, one at a time; nothing when it cannot be read."""
    try:
        with open(str(path), encoding="utf-8", errors="replace") as fh:
            for line in fh:
                yield line
    except OSError:
        return


def _cutoff(since: str | None) -> float | None:
    """A file last written before this (a day before since, for clocks and time zones) holds
    no line on or after since."""
    if since is None:
        return None
    day = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return (day - timedelta(days=1)).timestamp()


def _fresh(path: Path, cutoff: float | None) -> bool:
    if cutoff is None:
        return True
    try:
        return os.stat(str(path)).st_mtime >= cutoff
    except OSError:
        return False


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _texts(content: Any) -> Iterator[str]:
    if isinstance(content, str):
        yield content
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" \
                    and isinstance(block.get("text"), str):
                yield block["text"]


def _cache_split(usage: dict) -> dict:
    """{cache_write_1h, cache_write_5m} when the usage line splits its cache writes by
    lifetime; {} when it does not."""
    split = usage.get("cache_creation")
    if not isinstance(split, dict) or not ("ephemeral_1h_input_tokens" in split
                                           or "ephemeral_5m_input_tokens" in split):
        return {}
    return {"cache_write_1h": _int(split.get("ephemeral_1h_input_tokens")),
            "cache_write_5m": _int(split.get("ephemeral_5m_input_tokens"))}


def _read_calls(path: Path, routes_out: list | None) -> dict:
    """{message id: call} for the file's assistant lines, in first-seen order with the last
    line's usage. With routes_out, appends (timestamp, class) for each Route: line found."""
    calls: dict = {}
    for line in _open_lines(path):
        if '"assistant"' not in line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict) or obj.get("type") != "assistant":
            continue
        msg = obj.get("message")
        if not isinstance(msg, dict):
            continue
        ts = obj.get("timestamp") if isinstance(obj.get("timestamp"), str) else ""
        if routes_out is not None:
            for text in _texts(msg.get("content")):
                if "Route:" in text:
                    routes_out.extend((ts, cls) for cls in _ROUTE.findall(text))
        mid, model, usage = msg.get("id"), msg.get("model"), msg.get("usage")
        if not isinstance(mid, str) or not isinstance(model, str) or not isinstance(usage, dict) \
                or model in NOT_A_CALL:
            continue
        calls[mid] = {"message_id": mid, "model": model, "timestamp": ts, "day": ts[:10] or None,
                      "request_id": _text(obj.get("requestId")), "cwd": _text(obj.get("cwd")),
                      "git_branch": _text(obj.get("gitBranch")),
                      "input": _int(usage.get("input_tokens")),
                      "output": _int(usage.get("output_tokens")),
                      "cache_write": _int(usage.get("cache_creation_input_tokens")),
                      "cache_read": _int(usage.get("cache_read_input_tokens")),
                      **_cache_split(usage)}
    return calls


def _meta(path: Path) -> dict:
    try:
        with open(str(path), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _journal(path: Path) -> dict:
    """{agent id: (label, phase)} from the run journal's "started" events."""
    out: dict = {}
    for line in _open_lines(path):
        if '"started"' not in line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "started" \
                and isinstance(obj.get("agentId"), str):
            label, phase = obj.get("label"), obj.get("phase")
            out[obj["agentId"]] = (label if isinstance(label, str) else None,
                                   phase if isinstance(phase, str) else None)
    return out


def _workflow_name(record: Path, run: str) -> str:
    """The run record's workflowName; the run id when the record is missing or unreadable."""
    try:
        if record.stat().st_size <= _RUN_RECORD_LIMIT:
            name = _meta(record).get("workflowName")
            if isinstance(name, str) and name:
                return name
    except OSError:
        pass
    return run


def _sessions(project: Path) -> list:
    names = set()
    try:
        for entry in project.iterdir():
            if entry.suffix == ".jsonl" and entry.is_file():
                names.add(entry.stem)
            elif entry.is_dir() and (entry / "subagents").is_dir():
                names.add(entry.name)
    except OSError:
        return []
    return sorted(names)


def _agent_files(folder: Path) -> list:
    try:
        return sorted(p for p in folder.glob("agent-*.jsonl") if p.is_file())
    except OSError:
        return []


def _session_files(project: Path, sid: str, cutoff: float | None) -> Iterator[tuple]:
    """(path, attribution) for each fresh transcript of the session: the main file first."""
    main = project / (sid + ".jsonl")
    if main.is_file() and _fresh(main, cutoff):
        yield main, {"agent": SEAT, "workflow": None, "workflow_run": None, "stage": None,
                     "label": None}
    sub = project / sid / "subagents"
    for path in _agent_files(sub):
        if _fresh(path, cutoff):
            agent_type = _meta(path.with_name(path.stem + ".meta.json")).get("agentType")
            yield path, {"agent": agent_type if isinstance(agent_type, str) and agent_type
                         else SUBAGENT, "workflow": None, "workflow_run": None, "stage": None,
                         "label": None}
    runs = sub / "workflows"
    try:
        run_dirs = sorted(p for p in runs.iterdir() if p.is_dir()) if runs.is_dir() else []
    except OSError:
        run_dirs = []
    for run_dir in run_dirs:
        files = [p for p in _agent_files(run_dir) if _fresh(p, cutoff)]
        if not files:
            continue
        journal = _journal(run_dir / "journal.jsonl")
        name = _workflow_name(project / sid / "workflows" / (run_dir.name + ".json"), run_dir.name)
        for path in files:
            agent_id = path.stem[len("agent-"):]
            meta = _meta(path.with_name(path.stem + ".meta.json"))
            label, phase = journal.get(agent_id, (None, None))
            if label is None and isinstance(meta.get("description"), str):
                label = meta["description"]
            if phase is None and isinstance(meta.get("workflowPhase"), str):
                phase = meta["workflowPhase"]
            agent_type = meta.get("agentType")
            yield path, {"agent": agent_type if isinstance(agent_type, str) and agent_type
                         else WORKFLOW_AGENT, "workflow": name, "workflow_run": run_dir.name,
                         "stage": phase or label, "label": label}


def iter_calls(claude_dir: Path, since: str | None = None, session: str | None = None,
               classes: Iterable[str] | None = None) -> Iterator[dict]:
    """Every model call in the transcripts under claude_dir/projects, once per message id.

    Each call: {session, agent, workflow, workflow_run, stage, label, model, input, output,
    cache_write, cache_read, message_id, timestamp, day, route_class}. since (YYYY-MM-DD) keeps
    calls on or after that day (UTC); session keeps the sessions whose id is it or starts with
    it. route_class is the latest Route: line the seat wrote in the session at or before the
    call, or None; with classes, a word not among them becomes UNRECOGNISED. cache_write_1h and
    cache_write_5m are there when the usage line splits its cache writes."""
    projects = Path(claude_dir) / "projects"
    if not projects.is_dir():
        return
    cutoff = _cutoff(since)
    known = None if classes is None else set(classes)
    seen: set = set()
    for project in sorted(p for p in projects.iterdir() if p.is_dir()):
        for sid in _sessions(project):
            if session is not None and not sid.startswith(session):
                continue
            events: list = []
            for path, who in _session_files(project, sid, cutoff):
                calls = _read_calls(path, events if who["agent"] == SEAT else None)
                if who["agent"] == SEAT:
                    if known is not None:
                        events = [(ts, cls if cls in known else UNRECOGNISED)
                                  for ts, cls in events]
                    events.sort()
                stamps = [e[0] for e in events]
                for mid, call in calls.items():
                    if mid in seen:
                        continue
                    if since is not None and (call["day"] is None or call["day"] < since):
                        continue
                    seen.add(mid)
                    i = bisect.bisect_right(stamps, call["timestamp"]) - 1
                    call.update(who, session=sid, route_class=events[i][1] if i >= 0 else None)
                    yield call


def ledger_folder(claude_dir: Path) -> Path:
    """The ledger's day files, receiver files and session maps under this claude dir."""
    return Path(claude_dir).joinpath(*LEDGER_PARTS)


class LedgerReader:
    """iter_ledger's iterator. Besides the calls, it keeps a tally once read: calls (api_request
    lines yielded, each request id once), errors (api_error lines, skipped), bad (lines that are
    not a JSON object, such as a torn last line) and first_day (the earliest day file read)."""

    def __init__(self, claude_dir: Path, since: str | None, session: str | None) -> None:
        self.folder = ledger_folder(claude_dir)
        self.since, self.session = since, session
        self.calls = self.errors = self.bad = 0
        self.first_day: str | None = None
        self._it = self._read()

    def __iter__(self) -> "LedgerReader":
        return self

    def __next__(self) -> dict:
        return next(self._it)

    def days(self) -> list:
        """(day, path) for each day file on or after since, oldest first."""
        try:
            files = [p for p in self.folder.glob("*.jsonl") if p.is_file()]
        except OSError:
            return []
        out = sorted((p.stem, p) for p in files if valid_date(p.stem))
        return [(d, p) for d, p in out if self.since is None or d >= self.since]

    def _read(self) -> Iterator[dict]:
        seen: set = set()
        for day, path in self.days():
            if self.first_day is None:
                self.first_day = day
            for raw in _open_lines(path):
                if not raw.strip():
                    continue
                try:
                    line = json.loads(raw)
                except ValueError:
                    line = None
                if not isinstance(line, dict):
                    self.bad += 1
                    continue
                if self.session is not None and not str(line.get("session") or "") \
                        .startswith(self.session):
                    continue
                if line.get("event") == "api_error":
                    self.errors += 1
                    continue
                if line.get("event") != "api_request":
                    continue
                call = _ledger_call(line, day)
                if self.since is not None and (call["day"] or day) < self.since:
                    continue
                rid = call["request_id"]
                if rid is not None:
                    if rid in seen:
                        continue
                    seen.add(rid)
                self.calls += 1
                yield call


def _ledger_call(line: dict, day: str) -> dict:
    call = {LEDGER_RENAMES.get(k, k): v for k, v in line.items()}
    for k in TOKEN_KEYS:
        call[k] = _int(line.get(k))
    for k in ("cache_write_1h", "cache_write_5m"):
        if line.get(k) is None:
            del call[k]
    cost = line.get("cost_usd")
    call["cc_cost"] = float(cost) if isinstance(cost, (int, float)) \
        and not isinstance(cost, bool) else None
    ts = _text(line.get("ts")) or ""
    call.update(timestamp=ts, day=ts[:10] if valid_date(ts[:10]) else day, message_id=None,
                request_id=_text(line.get("request")), agent_id=_text(line.get("agent")))
    for k in ("model", "session", "source", "agent_name", "workflow", "workflow_run", "project",
              "root", "branch"):
        call[k] = _text(line.get(k))
    call.pop("event", None)
    call.pop("ts", None)
    return call


def iter_ledger(claude_dir: Path, since: str | None = None,
                session: str | None = None) -> LedgerReader:
    """The api_request calls in the ledger day files, oldest day first, each request id once.

    Each call has iter_calls's token keys plus request_id, source, agent_id, agent_name,
    workflow, workflow_run, project, root, branch and cc_cost (Claude Code's cost_usd), and the
    line's other keys under their own names. since (YYYY-MM-DD) skips the day files before it;
    session keeps the sessions whose id is it or starts with it. The reader's bad, errors,
    calls and first_day hold the tally once it has been read."""
    return LedgerReader(Path(claude_dir), since, session)


_IDENTITY: list = []


def project_identity(cwd: str) -> dict:
    """hooks/gk_common.py project_identity, loaded once, so the naming rules have one copy."""
    if not _IDENTITY:
        path = harness_hooks() / "gk_common.py"
        spec = importlib.util.spec_from_file_location("gk_harness_report_gk_common", str(path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        _IDENTITY.append(module.project_identity)
    return _IDENTITY[0](cwd)


def harness_hooks() -> Path:
    from . import paths
    return paths.harness_root() / "hooks"


class _Places:
    """A session's project from its session map, else a folder's project, each read once."""

    def __init__(self, claude_dir: Path | None) -> None:
        self.maps = ledger_folder(claude_dir) / "sessions" if claude_dir is not None else None
        self._sessions: dict = {}
        self._folders: dict = {}

    def session(self, sid: Any) -> dict | None:
        if self.maps is None or not isinstance(sid, str) or not sid:
            return None
        if sid not in self._sessions:
            data = _meta(self.maps / (sid + ".json")) if "/" not in sid and "\\" not in sid \
                and not sid.startswith(".") else {}
            self._sessions[sid] = {k: _text(data.get(k)) for k in ("project", "root", "branch")} \
                if _text(data.get("project")) else None
        return self._sessions[sid]

    def folder(self, cwd: str | None, branch: str | None) -> dict:
        if cwd is None:
            return {"project": None, "root": None, "branch": branch}
        if cwd not in self._folders:
            ident = None
            if os.path.isdir(cwd):
                try:
                    ident = project_identity(cwd)
                except Exception:  # the hooks' code failed to load or run: name the folder
                    ident = None
            self._folders[cwd] = {"project": ident["project"], "root": ident["root"],
                                  "branch": ident["branch"]} if ident else None
        found = self._folders[cwd]
        if found is not None:
            return dict(found)
        return {"project": os.path.basename(cwd.rstrip("/\\")) or cwd, "root": cwd,
                "branch": branch}

    def of(self, call: dict) -> dict:
        return self.session(call.get("session")) \
            or self.folder(call.get("cwd"), call.get("git_branch"))


class _Stages:
    """(stage, label) for a workflow agent the transcripts did not show, from its run's
    journal.jsonl, as iter_calls finds it."""

    def __init__(self, claude_dir: Path | None) -> None:
        self.projects = Path(claude_dir) / "projects" if claude_dir is not None else None
        self._runs: dict = {}

    def __call__(self, session: Any, run: Any, agent_id: Any) -> tuple:
        if self.projects is None or not all(isinstance(x, str) and x and "/" not in x
                                            and "\\" not in x and not x.startswith(".")
                                            for x in (session, run)):
            return None, None
        if (session, run) not in self._runs:
            journal: dict = {}
            try:
                pattern = "*/%s/subagents/workflows/%s/journal.jsonl" % (glob.escape(session),
                                                                          glob.escape(run))
                for path in sorted(self.projects.glob(pattern)):
                    journal.update(_journal(path))
            except (OSError, ValueError):
                pass
            self._runs[(session, run)] = journal
        label, phase = self._runs[(session, run)].get(agent_id, (None, None))
        return phase or label, label


class MergedCalls:
    """merge_sources's iterator; filled is the number of transcript calls with no ledger line,
    once read."""

    def __init__(self, it_fn: Any) -> None:
        self.filled = self.matched = 0
        self._it = it_fn(self)

    def __iter__(self) -> "MergedCalls":
        return self

    def __next__(self) -> dict:
        return next(self._it)


def merge_sources(transcript: Iterable[dict] | None, ledger: Iterable[dict] | None,
                  source: str = "both", claude_dir: Path | None = None) -> MergedCalls:
    """The calls to price, each request id once (spec 4.4).

    both: a transcript call whose request id is in the ledger takes the ledger's tokens,
    cc_cost, project, root and branch, and keeps its own attribution and route class. A
    transcript call with no match keeps its own figures; its project comes from the session map,
    else project_identity(cwd) when that folder exists, else the folder's name. A ledger call
    with no match is put under its agent_name, else "background (<source>)". "ledger" or
    "transcripts" yields that side only. claude_dir finds the session maps and run journals."""
    if source not in SOURCES:
        raise ValueError("source must be one of %s" % ", ".join(SOURCES))
    places, stages = _Places(claude_dir), _Stages(claude_dir)

    def run(state: MergedCalls) -> Iterator[dict]:
        index: dict = {}
        loose: list = []
        if source != "transcripts":
            for call in ledger or ():
                rid = call.get("request_id")
                if rid is None:
                    loose.append(call)
                elif rid not in index:
                    index[rid] = call
        used: set = set()
        if source != "ledger":
            for call in transcript or ():
                rid = call.get("request_id")
                if rid is not None:
                    if rid in used:
                        continue
                    used.add(rid)
                match = index.get(rid) if rid is not None else None
                if match is None:
                    state.filled += 1
                    call.update(cc_cost=None, **places.of(call))
                    yield call
                    continue
                state.matched += 1
                for k in TOKEN_KEYS:
                    if k in match:
                        call[k] = match[k]
                    elif k in ("cache_write_1h", "cache_write_5m"):
                        call.pop(k, None)
                call["cc_cost"] = match.get("cc_cost")
                if match.get("project") is not None:
                    call.update(project=match["project"], root=match.get("root"),
                                branch=match.get("branch"))
                else:
                    call.update(places.of(call))
                yield call
        for call in list(index.values()) + loose:
            if call.get("request_id") in used:
                continue
            name = call.get("agent_name")
            call["agent"] = name or BACKGROUND % (call.get("source") or "?")
            call["stage"], call["label"] = stages(call.get("session"), call.get("workflow_run"),
                                                  call.get("agent_id")) \
                if call.get("workflow_run") else (None, None)
            call.setdefault("route_class", None)
            yield call

    return MergedCalls(run)


def ledger_summary(reader: LedgerReader | None, merged: MergedCalls, source: str) -> dict:
    """summary["ledger"]: {calls, since, filled, bad, errors, source}, read after the calls."""
    if reader is None:
        return {"calls": 0, "since": None, "filled": merged.filled, "bad": 0, "errors": 0,
                "source": source}
    return {"calls": reader.calls, "since": reader.first_day, "filled": merged.filled,
            "bad": reader.bad, "errors": reader.errors, "source": source}


def alias(routes: dict, model: str | None) -> str | None:
    """The routes.json model alias whose name the model id contains, or None."""
    text = (model or "").lower()
    for name in (routes.get("models") or {}):
        if name.lower() in text:
            return name
    return None


def _prompt(call: dict) -> int:
    return call.get("input", 0) + call.get("cache_read", 0) + call.get("cache_write", 0)


def price(routes: dict, call: dict) -> float:
    """The call's cost in dollars at the routes.json prices per million tokens; 0.0 for a model
    routes.json does not know. A prompt over 100K tokens takes priceOver100k where the model
    has one (Haiku)."""
    name = alias(routes, call.get("model"))
    if name is None:
        return 0.0
    entry = routes["models"][name]
    rates = entry.get("price") or {}
    if _prompt(call) > OVER_100K and isinstance(entry.get("priceOver100k"), dict):
        rates = entry["priceOver100k"]
    # The 1-hour part of the cache writes, when the line says, at cacheWrite1h; the rest at
    # cacheWrite (the 5-minute rate).
    write, write_1h = call.get("cache_write", 0), min(call.get("cache_write_1h", 0),
                                                       call.get("cache_write", 0))
    return (call.get("input", 0) * rates.get("input", 0)
            + call.get("output", 0) * rates.get("output", 0)
            + (write - write_1h) * rates.get("cacheWrite", 0)
            + write_1h * rates.get("cacheWrite1h", rates.get("cacheWrite", 0))
            + call.get("cache_read", 0) * rates.get("cacheRead", 0)) / 1e6


def _add(table: dict, key: str, call: dict, cost: float) -> None:
    row = table.get(key)
    if row is None:
        row = table[key] = {"key": key, "calls": 0, "cost": 0.0, "share": 0.0, "input": 0,
                            "output": 0, "cache_write": 0, "cache_write_1h": 0, "cache_read": 0}
    row["calls"] += 1
    row["cost"] += cost
    for k in ("input", "output", "cache_write", "cache_write_1h", "cache_read"):
        row[k] += call.get(k, 0)


def _rows(table: dict, total: float, by_key: bool = False) -> list:
    rows = list(table.values())
    for row in rows:
        row["share"] = row["cost"] / total if total else 0.0
    if by_key:
        return sorted(rows, key=lambda r: r["key"])
    return sorted(rows, key=lambda r: (-r["cost"], -r["calls"], r["key"]))


def summarise(calls: Iterable[dict], routes: dict) -> dict:
    """Totals, Fable's share, Haiku calls over 100K, the unknown models, and the cost grouped
    by model, day, workflow, workflow run, stage, agent and session; per route class as best
    effort: a route word that is not a class in routes.json counts as UNRECOGNISED."""
    known = set(routes.get("classes") or {})
    models = routes.get("models") or {}
    older = False
    tables: dict = {g: {} for g in GROUPS}
    classes: dict = {}
    unknown: dict = {}
    totals: dict = {}
    fable = over = 0.0
    fable_calls = over_calls = 0
    cc_calls, cc_total, cc_priced = 0, 0.0, 0.0
    for call in calls:
        cost = price(routes, call)
        cc = call.get("cc_cost")
        if isinstance(cc, (int, float)) and not isinstance(cc, bool):
            cc_calls += 1
            cc_total += cc
            cc_priced += cost
        name = alias(routes, call.get("model"))
        wf, run = call.get("workflow"), call.get("workflow_run")
        keys = {"byModel": call.get("model") or "?",
                "byDay": call.get("day") or "(no date)",
                "byWorkflow": wf or NO_WORKFLOW,
                "byWorkflowRun": "%s %s" % (wf, run) if wf and run and wf != run
                else (run or NO_WORKFLOW),
                "byStage": "%s / %s" % (wf, call.get("stage") or NO_STAGE) if wf else NO_WORKFLOW,
                "byAgent": call.get("agent") or "?",
                "bySession": call.get("session") or "?",
                "byProject": call.get("project") or NO_PROJECT,
                "byFolder": call.get("root") or NO_FOLDER}
        for group, key in keys.items():
            _add(tables[group], key, call, cost)
        route = call.get("route_class")
        _add(classes, NO_ROUTE if not route else route if route in known else UNRECOGNISED,
             call, cost)
        if name is not None and call.get("model") != (models.get(name) or {}).get("id"):
            older = True
        _add(totals, "total", call, cost)
        if name is None:
            _add(unknown, call.get("model") or "?", call, 0.0)
        elif name == FABLE:
            fable += cost
            fable_calls += 1
        elif name == HAIKU and _prompt(call) > OVER_100K:
            over += cost
            over_calls += 1
    total = totals["total"]["cost"] if totals else 0.0
    head = totals.get("total") or {"calls": 0, "cost": 0.0, "input": 0, "output": 0,
                                   "cache_write": 0, "cache_write_1h": 0, "cache_read": 0}
    summary = {"since": None, "session": None,
               "totals": {k: head[k] for k in ("calls", "cost", "input", "output",
                                               "cache_write", "cache_write_1h", "cache_read")},
               "fable": {"calls": fable_calls, "cost": fable,
                         "share": fable / total if total else 0.0},
               "haikuOver100k": {"calls": over_calls, "cost": over}}
    for group in GROUPS:
        summary[group] = _rows(tables[group], total, by_key=group == "byDay")
    summary["byClass"] = {"bestEffort": True, "note": CLASS_NOTE,
                          "rows": _rows(classes, total)}
    summary["unknownModels"] = _rows(unknown, total)
    summary["notes"] = [OLDER_IDS] if older else []
    if cc_calls and abs(cc_total - cc_priced) > COST_TOLERANCE * cc_priced:
        gap = "%.1f%% %s" % (100 * abs(cc_total - cc_priced) / cc_priced,
                             "above" if cc_total > cc_priced else "below") if cc_priced \
            else "against"
        summary["notes"].append("Claude Code's own cost for the %d ledger calls is %s, %s the "
                                "routes.json price of %s." % (cc_calls, _money(cc_total), gap,
                                                              _money(cc_priced)))
    return summary


def _money(value: float) -> str:
    return "$%.2f" % value if value >= 0.01 or value == 0 else "$%.4f" % value


def _tokens(n: int) -> str:
    for size, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= size:
            return "%.1f%s" % (n / size, unit)
    return str(n)


def _table(title: str, rows: list, note: str | None = None, latest: bool = False) -> list:
    """Up to TOP rows: the first ones, or with latest the last ones (still in their order)."""
    out = ["", title + (" (%s)" % note if note else "")]
    if not rows:
        return out + ["  none"]
    shown = rows[-TOP:] if latest else rows[:TOP]
    width = min(max(len(r["key"]) for r in shown), 48)
    out.append("  %-*s %7s %11s %6s %8s %8s %8s %8s" % (width, "", "calls", "cost", "share",
                                                       "in", "out", "c-write", "c-read"))
    for r in shown:
        key = r["key"] if len(r["key"]) <= width else r["key"][:width - 3] + "..."
        out.append("  %-*s %7d %11s %5.1f%% %8s %8s %8s %8s" % (
            width, key, r["calls"], _money(r["cost"]), 100 * r["share"], _tokens(r["input"]),
            _tokens(r["output"]), _tokens(r["cache_write"]), _tokens(r["cache_read"])))
    if len(rows) > TOP:
        out.append("  ... and %d %s (--json lists them all)"
                   % (len(rows) - TOP, "earlier" if latest else "more"))
    return out


def render(summary: dict, as_json: bool) -> str:
    """The summary as text, or as JSON (with the header under "header")."""
    if as_json:
        return json.dumps(dict(summary, header=HEADER), indent=2) + "\n"
    t, f, h = summary["totals"], summary["fable"], summary["haikuOver100k"]
    scope = "since %s" % summary["since"] if summary.get("since") else "all dates"
    scope += ", session %s" % summary["session"] if summary.get("session") else ", all sessions"
    lines = [TITLE, HEADER, "Scope: %s" % scope,
             "Total: %s over %d calls; Fable %s (%.1f%%, %d calls); Haiku over 100K: %d calls, %s"
             % (_money(t["cost"]), t["calls"], _money(f["cost"]), 100 * f["share"], f["calls"],
                h["calls"], _money(h["cost"])),
             "Tokens: in %s, out %s, cache write %s (1-hour %s), cache read %s"
             % (_tokens(t["input"]), _tokens(t["output"]), _tokens(t["cache_write"]),
                _tokens(t["cache_write_1h"]), _tokens(t["cache_read"]))]
    led = summary.get("ledger")
    if isinstance(led, dict):
        if led.get("source") == "transcripts":
            lines.append("Ledger: not read (--source transcripts)")
        elif led.get("since") is None:
            lines.append("Ledger: no ledger files")
        else:
            lines.append("Ledger: %d calls since %s; transcripts filled %d"
                         % (led["calls"], led["since"], led["filled"]))
    for group in GROUPS:
        lines += _table(TITLES[group], summary[group], latest=group == "byDay")
        if group == "byModel":
            lines += summary.get("notes") or []
    lines += _table("By route class", summary["byClass"]["rows"], summary["byClass"]["note"])
    if summary["unknownModels"]:
        lines += _table("Unknown models (counted at no cost)", summary["unknownModels"])
    return "\n".join(lines) + "\n"
