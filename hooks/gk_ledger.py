"""The usage ledger's receiver: OTLP log parsing, the allowlisted ledger line, day files and
the Prometheus counters (spec docs/2026-11-15-usage-ledger-design.md, sections 4.2, 4.3, 4.7).

Standard library only, Python 3.9+. This module holds the pure functions; the HTTP server
that calls them comes in a later task. Attribute names and value types follow the Stage 0
proofs (docs/proofs/2026-11-ledger-stage0.md).
"""
from __future__ import annotations

import datetime
import json
import math
import os
import re
import threading
from pathlib import Path
from typing import Any, Callable, Optional

import gk_common

LEDGER_VERSION = 1
EVENTS = ("claude_code.api_request", "claude_code.api_error")
SERVICE = "claude-code"
LARGE_PROMPT = 100_000
NAME_FILE_MAX = 32 * 1024 * 1024  # run records and meta files bigger than this are skipped

LINE_KEYS = (
    "ts", "event", "session", "prompt", "request", "model", "effort", "speed", "source",
    "agent", "parent", "agent_name", "workflow_run", "workflow", "project", "root", "branch",
    "input", "output", "cache_read", "cache_write", "cache_write_5m", "cache_write_1h",
    "cost_usd", "duration_ms", "status", "attempt",
)

_PREFIX = "claude_code."
_SHORT_EVENTS = tuple(e[len(_PREFIX):] for e in EVENTS)
_CUSTOM = "custom"

# Ledger key -> OTLP attribute, by type. Anything not named here is dropped.
_STRINGS = (
    ("session", "session.id"), ("prompt", "prompt.id"), ("request", "request_id"),
    ("model", "model"), ("effort", "effort"), ("speed", "speed"), ("source", "query_source"),
    ("agent", "agent_id"), ("parent", "parent_agent_id"), ("agent_name", "agent.name"),
    ("workflow_run", "workflow.run_id"), ("workflow", "workflow.name"),
)
_INTS = (
    ("input", "input_tokens"), ("output", "output_tokens"), ("cache_read", "cache_read_tokens"),
    ("cache_write", "cache_creation_tokens"), ("cache_write_5m", "cache_creation_5m_tokens"),
    ("cache_write_1h", "cache_creation_1h_tokens"), ("duration_ms", "duration_ms"),
    ("status", "status_code"), ("attempt", "attempt"),
)
_IDENTITY = ("project", "root", "branch")

_TS_RE = re.compile(
    r"(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.(\d{1,9}))?(Z|[+-]\d\d:?\d\d)$")


def any_value(v: Any) -> Any:
    """Decode an OTLP AnyValue. intValue may be a JSON number or a string; arrays, maps,
    bytes and anything malformed give None."""
    if not isinstance(v, dict):
        return None
    if "stringValue" in v:
        s = v["stringValue"]
        return s if isinstance(s, str) else None
    if "intValue" in v:
        return _int(v["intValue"])
    if "doubleValue" in v:
        return _float(v["doubleValue"])
    if "boolValue" in v:
        b = v["boolValue"]
        return b if isinstance(b, bool) else None
    return None


def _int(x: Any) -> Optional[int]:
    if isinstance(x, bool):
        return None
    if isinstance(x, int):
        return x
    if isinstance(x, float):
        return int(x) if math.isfinite(x) and x == int(x) else None
    if isinstance(x, str):
        try:
            return int(x.strip())
        except ValueError:
            return None
    return None


def _float(x: Any) -> Optional[float]:
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float, str)):
        try:
            f = float(x)
        except ValueError:
            return None
        return f if math.isfinite(f) else None
    return None


def _attrs(items: Any) -> dict:
    out: dict = {}
    if not isinstance(items, list):
        return out
    for a in items:
        if isinstance(a, dict) and isinstance(a.get("key"), str):
            out[a["key"]] = any_value(a.get("value"))
    return out


def parse_logs(body: bytes) -> list:
    """[(resource_attrs, record_attrs, event_name, time_unix_nano), ...] from an OTLP JSON
    logs body. event_name is the record body's string (e.g. "claude_code.api_request"), else
    None. Raises ValueError on non-JSON or a body that is not {"resourceLogs": [...]};
    malformed items inside are skipped."""
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError) as e:
        raise ValueError("body is not JSON: %s" % e) from None
    if not isinstance(data, dict) or not isinstance(data.get("resourceLogs", []), list):
        raise ValueError("body is not an OTLP logs object")
    out = []
    for rl in data.get("resourceLogs", []):
        if not isinstance(rl, dict):
            continue
        res = rl.get("resource")
        resource = _attrs(res.get("attributes")) if isinstance(res, dict) else {}
        scopes = rl.get("scopeLogs")
        for sl in scopes if isinstance(scopes, list) else ():
            if not isinstance(sl, dict) or not isinstance(sl.get("logRecords"), list):
                continue
            for rec in sl["logRecords"]:
                if not isinstance(rec, dict):
                    continue
                name = any_value(rec.get("body"))
                out.append((resource, _attrs(rec.get("attributes")),
                            name if isinstance(name, str) else None,
                            _int(rec.get("timeUnixNano"))))
    return out


def _event(event_name: Optional[str], attrs: dict) -> Optional[str]:
    """The short event name ("api_request") when the record is one the ledger keeps."""
    for cand in (event_name, attrs.get("event.name")):
        if not isinstance(cand, str):
            continue
        short = cand[len(_PREFIX):] if cand.startswith(_PREFIX) else cand
        return short if short in _SHORT_EVENTS else None
    return None


def _iso(dt: datetime.datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (dt.microsecond // 1000)


def _parse_ts(ts: Any) -> Optional[datetime.datetime]:
    """The UTC datetime of an ISO 8601 timestamp with a Z or numeric offset, else None."""
    if not isinstance(ts, str):
        return None
    m = _TS_RE.match(ts.strip())
    if not m:
        return None
    y, mo, d, h, mi, s, frac, zone = m.groups()
    try:
        tz = datetime.timezone.utc
        if zone != "Z":
            z = zone.replace(":", "")
            sign = -1 if z[0] == "-" else 1
            tz = datetime.timezone(sign * datetime.timedelta(hours=int(z[1:3]),
                                                             minutes=int(z[3:5])))
        micro = int((frac or "0").ljust(6, "0")[:6])
        dt = datetime.datetime(int(y), int(mo), int(d), int(h), int(mi), int(s), micro, tz)
        return dt.astimezone(datetime.timezone.utc)
    except (ValueError, OverflowError):
        return None


def _ts(attrs: dict, time_ns: Optional[int]) -> str:
    ts = attrs.get("event.timestamp")
    if _parse_ts(ts) is not None:
        return ts
    if isinstance(time_ns, int) and time_ns > 0:
        try:
            return _iso(datetime.datetime.fromtimestamp(time_ns / 1e9, datetime.timezone.utc)
                        .replace(microsecond=(time_ns // 1000) % 1_000_000))
        except (ValueError, OverflowError, OSError):
            pass
    return _iso(datetime.datetime.now(datetime.timezone.utc))


def _str(x: Any) -> Optional[str]:
    return x if isinstance(x, str) else None


def to_line(resource: dict, attrs: dict, event_name: Optional[str], time_ns: Optional[int],
            lookup: Optional[Callable[[str], Any]],
            names: Optional["NameLookup"] = None) -> Optional[dict]:
    """The ledger line (spec 4.3) for one record, or None when the record is not an
    api_request/api_error from Claude Code. Only LINE_KEYS are written, in order."""
    if not isinstance(resource, dict) or resource.get("service.name") != SERVICE:
        return None
    if not isinstance(attrs, dict):
        return None
    event = _event(event_name, attrs)
    if event is None:
        return None
    line: dict = dict.fromkeys(LINE_KEYS)
    line["ts"] = _ts(attrs, time_ns)
    line["event"] = event
    for k, a in _STRINGS:
        line[k] = _str(attrs.get(a))
    for k, a in _INTS:
        line[k] = _int(attrs.get(a))
    line["cost_usd"] = _float(attrs.get("cost_usd"))
    session = line["session"]
    if names is not None and session:
        if line["workflow_run"] and line["workflow"] in (None, _CUSTOM):
            line["workflow"] = _safe_call(names.workflow, session, line["workflow_run"]) \
                or line["workflow"]
        if line["agent"] and line["agent_name"] in (None, _CUSTOM):
            line["agent_name"] = _safe_call(names.agent, session, line["agent"]) \
                or line["agent_name"]
    ident = _safe_call(lookup, session) if lookup is not None and session else None
    if isinstance(ident, dict):
        for k in _IDENTITY:
            line[k] = _str(ident.get(k))
    return line


def _safe_call(fn: Callable, *args: Any) -> Any:
    try:
        return fn(*args)
    except Exception:
        return None


def day_file(ledger_dir: Path, ts: Any) -> Path:
    """<ledger_dir>/<YYYY-MM-DD>.jsonl for the UTC date of ts, else today's UTC date."""
    dt = _parse_ts(ts) or datetime.datetime.now(datetime.timezone.utc)
    return Path(ledger_dir) / ("%s.jsonl" % dt.strftime("%Y-%m-%d"))


def append_line(ledger_dir: Path, line: dict) -> bool:
    """Append one compact JSON line to its day file with a single os.write (O_APPEND, mode
    0600). Makes the folder. False on any failure, a short write included."""
    try:
        data = (json.dumps(line, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
        path = day_file(ledger_dir, line.get("ts"))
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            return os.write(fd, data) == len(data)
        finally:
            os.close(fd)
    except (OSError, TypeError, ValueError):
        return False


class SessionLookup:
    """Callable (sid) -> the session map dict, or None. Cached by (mtime, size); a missing,
    unreadable, partial or non-object file gives None."""

    def __init__(self, sessions_dir: Optional[Path] = None) -> None:
        self.sessions_dir = Path(sessions_dir) if sessions_dir is not None \
            else gk_common.ledger_dir() / "sessions"
        self._cache: dict = {}
        self._lock = threading.Lock()

    def __call__(self, sid: Any) -> Optional[dict]:
        safe = gk_common.safe_sid(sid)
        if safe is None:
            return None
        path = self.sessions_dir / ("%s.json" % safe)
        try:
            st = path.stat()
        except OSError:
            return None
        stamp = (st.st_mtime_ns, st.st_size)
        with self._lock:
            hit = self._cache.get(safe)
        if hit is not None and hit[0] == stamp:
            return hit[1]
        data = gk_common.read_json(path)
        value = data if isinstance(data, dict) else None
        with self._lock:
            self._cache[safe] = (stamp, value)
        return value


class NameLookup:
    """Real workflow and agent names for telemetry's `custom` (Stage 0 proof P3), read from
    the session's transcript folder under projects_dir. Found answers are cached; None is
    not, because the file may appear later."""

    def __init__(self, projects_dir: Optional[Path] = None) -> None:
        self.projects_dir = Path(projects_dir) if projects_dir is not None \
            else gk_common.claude_dir() / "projects"
        self._cache: dict = {}
        self._lock = threading.Lock()

    def workflow(self, session: Any, run_id: Any) -> Optional[str]:
        """`workflowName` from projects_dir/*/<session>/workflows/<run_id>.json."""
        return self._find("workflow", session, run_id,
                          "*/%s/workflows/%s.json", "workflowName")

    def agent(self, session: Any, agent_id: Any) -> Optional[str]:
        """`agentType` from the first projects_dir/*/<session>/subagents/**/agent-<id>.meta.json."""
        return self._find("agent", session, agent_id,
                          "*/%s/subagents/**/agent-%s.meta.json", "agentType")

    def _find(self, kind: str, session: Any, ident: Any, pattern: str, field: str
              ) -> Optional[str]:
        s, i = gk_common.safe_sid(session), gk_common.safe_sid(ident)
        if s is None or i is None:
            return None
        key = (kind, s, i)
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        try:
            paths = sorted(self.projects_dir.glob(pattern % (s, i)))
        except (OSError, ValueError):
            return None
        for path in paths:
            try:
                if path.stat().st_size > NAME_FILE_MAX:
                    continue
            except OSError:
                continue
            data = gk_common.read_json(path)
            value = data.get(field) if isinstance(data, dict) else None
            if isinstance(value, str) and value:
                with self._lock:
                    self._cache[key] = value
                return value
        return None


_LABELS = ("model", "effort", "agent_name", "workflow", "source", "project")
_TOKEN_KINDS = ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h")
_COUNTERS = (
    ("gk_ledger_requests_total", "API calls and API errors recorded since the receiver started."),
    ("gk_ledger_tokens_total", "Tokens by kind since the receiver started."),
    ("gk_ledger_cost_usd_total", "Claude Code's cost_usd since the receiver started."),
    ("gk_ledger_large_prompts_total",
     "Calls whose prompt (input + cache read + cache write) exceeds %d tokens." % LARGE_PROMPT),
)


def _escape(v: Any) -> str:
    s = "" if v is None else str(v)
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _num(x: Any) -> int:
    n = _int(x)
    return n if n is not None else 0


class Metrics:
    """Prometheus text-format (0.0.4) counters over ledger lines (spec 4.7). Labels never
    carry session, request, prompt, agent or workflow-run ids."""

    def __init__(self) -> None:
        self._values: dict = {}
        self._lock = threading.Lock()

    def _inc(self, name: str, labels: tuple, by: float) -> None:
        k = (name, labels)
        self._values[k] = self._values.get(k, 0) + by

    def add(self, line: dict) -> None:
        base = tuple((k, _escape(line.get(k))) for k in _LABELS)
        with self._lock:
            event = line.get("event")
            self._inc("gk_ledger_requests_total", base + (("event", _escape(event)),), 1)
            for kind in _TOKEN_KINDS:
                n = _int(line.get(kind))
                if n is not None:
                    self._inc("gk_ledger_tokens_total", base + (("kind", kind),), n)
            cost = _float(line.get("cost_usd"))
            if cost is not None:
                self._inc("gk_ledger_cost_usd_total", base, cost)
            prompt = (_num(line.get("input")) + _num(line.get("cache_read"))
                      + _num(line.get("cache_write")))
            if prompt > LARGE_PROMPT:
                self._inc("gk_ledger_large_prompts_total", base, 1)

    def render(self) -> str:
        with self._lock:
            items = sorted(self._values.items())
        out = []
        for name, help_text in _COUNTERS:
            out.append("# HELP %s %s" % (name, help_text))
            out.append("# TYPE %s counter" % name)
            for (n, labels), value in items:
                if n != name:
                    continue
                lab = ",".join('%s="%s"' % kv for kv in labels)
                out.append("%s{%s} %s" % (name, lab, _fmt(value)))
        return "\n".join(out) + "\n"


def _fmt(v: float) -> str:
    if isinstance(v, int) or (isinstance(v, float) and v.is_integer()):
        return str(int(v))
    return repr(float(v))
