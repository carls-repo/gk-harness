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
"Route: <class>" lines. Files are read line by line, never whole.
"""
from __future__ import annotations

import bisect
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

HEADER = "On a subscription, cost is a proxy for your share of the usage limits."
TITLE = "gk-harness report: Claude Code transcripts priced from routes.json"
NO_ROUTE = "(no route line)"
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
GROUPS = ("byModel", "byDay", "byWorkflow", "byWorkflowRun", "byStage", "byAgent", "bySession")
TITLES = {"byModel": "By model", "byDay": "By day", "byWorkflow": "By workflow",
          "byWorkflowRun": "By workflow run", "byStage": "By workflow stage",
          "byAgent": "By agent", "bySession": "By session"}
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


def _texts(content: Any) -> Iterator[str]:
    if isinstance(content, str):
        yield content
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" \
                    and isinstance(block.get("text"), str):
                yield block["text"]


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
                      "input": _int(usage.get("input_tokens")),
                      "output": _int(usage.get("output_tokens")),
                      "cache_write": _int(usage.get("cache_creation_input_tokens")),
                      "cache_read": _int(usage.get("cache_read_input_tokens"))}
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


def iter_calls(claude_dir: Path, since: str | None = None,
               session: str | None = None) -> Iterator[dict]:
    """Every model call in the transcripts under claude_dir/projects, once per message id.

    Each call: {session, agent, workflow, workflow_run, stage, label, model, input, output,
    cache_write, cache_read, message_id, timestamp, day, route_class}. since (YYYY-MM-DD) keeps
    calls on or after that day (UTC); session keeps the sessions whose id is it or starts with
    it. route_class is the latest Route: line the seat wrote in the session at or before the
    call, or None."""
    projects = Path(claude_dir) / "projects"
    if not projects.is_dir():
        return
    cutoff = _cutoff(since)
    seen: set = set()
    for project in sorted(p for p in projects.iterdir() if p.is_dir()):
        for sid in _sessions(project):
            if session is not None and not sid.startswith(session):
                continue
            events: list = []
            for path, who in _session_files(project, sid, cutoff):
                calls = _read_calls(path, events if who["agent"] == SEAT else None)
                if who["agent"] == SEAT:
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
    return (call.get("input", 0) * rates.get("input", 0)
            + call.get("output", 0) * rates.get("output", 0)
            + call.get("cache_write", 0) * rates.get("cacheWrite", 0)
            + call.get("cache_read", 0) * rates.get("cacheRead", 0)) / 1e6


def _add(table: dict, key: str, call: dict, cost: float) -> None:
    row = table.get(key)
    if row is None:
        row = table[key] = {"key": key, "calls": 0, "cost": 0.0, "share": 0.0, "input": 0,
                            "output": 0, "cache_write": 0, "cache_read": 0}
    row["calls"] += 1
    row["cost"] += cost
    for k in ("input", "output", "cache_write", "cache_read"):
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
    effort."""
    tables: dict = {g: {} for g in GROUPS}
    classes: dict = {}
    unknown: dict = {}
    totals: dict = {}
    fable = over = 0.0
    fable_calls = over_calls = 0
    for call in calls:
        cost = price(routes, call)
        name = alias(routes, call.get("model"))
        wf, run = call.get("workflow"), call.get("workflow_run")
        keys = {"byModel": call.get("model") or "?",
                "byDay": call.get("day") or "(no date)",
                "byWorkflow": wf or NO_WORKFLOW,
                "byWorkflowRun": "%s %s" % (wf, run) if wf and run and wf != run
                else (run or NO_WORKFLOW),
                "byStage": "%s / %s" % (wf, call.get("stage") or NO_STAGE) if wf else NO_WORKFLOW,
                "byAgent": call.get("agent") or "?",
                "bySession": call.get("session") or "?"}
        for group, key in keys.items():
            _add(tables[group], key, call, cost)
        _add(classes, call.get("route_class") or NO_ROUTE, call, cost)
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
                                   "cache_write": 0, "cache_read": 0}
    summary = {"since": None, "session": None,
               "totals": {k: head[k] for k in ("calls", "cost", "input", "output",
                                               "cache_write", "cache_read")},
               "fable": {"calls": fable_calls, "cost": fable,
                         "share": fable / total if total else 0.0},
               "haikuOver100k": {"calls": over_calls, "cost": over}}
    for group in GROUPS:
        summary[group] = _rows(tables[group], total, by_key=group == "byDay")
    summary["byClass"] = {"bestEffort": True, "note": CLASS_NOTE,
                          "rows": _rows(classes, total)}
    summary["unknownModels"] = _rows(unknown, total)
    return summary


def _money(value: float) -> str:
    return "$%.2f" % value if value >= 0.01 or value == 0 else "$%.4f" % value


def _tokens(n: int) -> str:
    for size, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= size:
            return "%.1f%s" % (n / size, unit)
    return str(n)


def _table(title: str, rows: list, note: str | None = None) -> list:
    out = ["", title + (" (%s)" % note if note else "")]
    if not rows:
        return out + ["  none"]
    shown = rows[:TOP]
    width = min(max(len(r["key"]) for r in shown), 48)
    out.append("  %-*s %7s %11s %6s %8s %8s %8s %8s" % (width, "", "calls", "cost", "share",
                                                       "in", "out", "c-write", "c-read"))
    for r in shown:
        key = r["key"] if len(r["key"]) <= width else r["key"][:width - 3] + "..."
        out.append("  %-*s %7d %11s %5.1f%% %8s %8s %8s %8s" % (
            width, key, r["calls"], _money(r["cost"]), 100 * r["share"], _tokens(r["input"]),
            _tokens(r["output"]), _tokens(r["cache_write"]), _tokens(r["cache_read"])))
    if len(rows) > TOP:
        out.append("  ... and %d more (--json lists them all)" % (len(rows) - TOP))
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
             "Tokens: in %s, out %s, cache write %s, cache read %s"
             % (_tokens(t["input"]), _tokens(t["output"]), _tokens(t["cache_write"]),
                _tokens(t["cache_read"]))]
    for group in GROUPS:
        lines += _table(TITLES[group], summary[group])
    lines += _table("By route class", summary["byClass"]["rows"], summary["byClass"]["note"])
    if summary["unknownModels"]:
        lines += _table("Unknown models (counted at no cost)", summary["unknownModels"])
    return "\n".join(lines) + "\n"
