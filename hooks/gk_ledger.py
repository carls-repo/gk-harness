"""The usage ledger's receiver: OTLP log parsing, the allowlisted ledger line, day files and
the Prometheus counters (spec docs/2026-11-15-usage-ledger-design.md, sections 4.2, 4.3, 4.7).

Standard library only, Python 3.9+. The pure functions come first, then the loopback HTTP
server that calls them (`make_server`, `serve`, `main`). Attribute names and value types
follow the Stage 0 proofs (docs/proofs/2026-11-ledger-stage0.md).
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import errno
import json
import math
import os
import re
import signal
import socket
import stat
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional

import gk_common

LEDGER_VERSION = 1
EVENTS = ("claude_code.api_request", "claude_code.api_error")
SERVICE = "claude-code"
LARGE_PROMPT = 100_000
NAME_FILE_MAX = 32 * 1024 * 1024  # run records and meta files bigger than this are skipped
FIELD_MAX_BYTES = 200  # each string field of a line, in UTF-8 bytes
LINE_MAX_BYTES = 4096  # appended lines must stay below this so O_APPEND writes never interleave

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


_INT_LIMIT = 2 ** 63  # OTLP integers are int64; anything wider is not a real count


def _int(x: Any) -> Optional[int]:
    n: Optional[int] = None
    if isinstance(x, bool):
        return None
    if isinstance(x, int):
        n = x
    elif isinstance(x, float):
        n = int(x) if math.isfinite(x) and x == int(x) else None
    elif isinstance(x, str) and len(x) <= 32:
        try:
            n = int(x.strip())
        except ValueError:
            return None
    return n if n is not None and -_INT_LIMIT <= n < _INT_LIMIT else None


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
    except RecursionError:
        raise ValueError("body is not JSON: nested too deeply") from None
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
        return ts.strip()
    if isinstance(time_ns, int) and time_ns > 0:
        try:
            return _iso(datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
                        + datetime.timedelta(microseconds=time_ns // 1000))
        except (ValueError, OverflowError):
            pass
    return _iso(datetime.datetime.now(datetime.timezone.utc))


def _str(x: Any) -> Optional[str]:
    """x cut so its JSON-encoded form (UTF-8, quotes excluded) is at most FIELD_MAX_BYTES,
    at a character boundary; None when x is not a str. Measuring the encoded form bounds the
    line even for escaped control characters, which take up to six bytes each."""
    if not isinstance(x, str):
        return None
    x = x.encode("utf-8", "replace").decode("utf-8")  # lone surrogates would fail the write
    if len(x) * 6 <= FIELD_MAX_BYTES:
        return x
    used = 0
    for i, c in enumerate(x):
        used += len(json.dumps(c, ensure_ascii=False).encode("utf-8", "replace")) - 2
        if used > FIELD_MAX_BYTES:
            return x[:i]
    return x


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
            line["workflow"] = _str(_safe_call(names.workflow, session,
                                               line["workflow_run"])) or line["workflow"]
        if line["agent"] and line["agent_name"] in (None, _CUSTOM):
            line["agent_name"] = _str(_safe_call(names.agent, session, line["agent"])) \
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
    0600). Makes the folder. False on any failure, a short write included, and for a line of
    LINE_MAX_BYTES or more, which is refused unwritten."""
    return _write_line(ledger_dir, line) is None


LINE_REFUSED = "line_refused"
WRITE_FAILED = "write_failed"


def _write_line(ledger_dir: Path, line: dict) -> Optional[tuple]:
    """append_line's work: None when written, else (error kind, detail). The detail names the
    error only, never the line's content."""
    try:
        data = (json.dumps(line, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    except (TypeError, ValueError) as e:
        return (LINE_REFUSED, "not encodable: %s" % type(e).__name__)
    if len(data) >= LINE_MAX_BYTES:
        return (LINE_REFUSED, "line of %d bytes, limit %d" % (len(data), LINE_MAX_BYTES))
    try:
        path = day_file(ledger_dir, line.get("ts"))
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            n = os.write(fd, data)
        finally:
            os.close(fd)
    except OSError as e:
        return (WRITE_FAILED, "%s: %s" % (type(e).__name__, e))
    except (TypeError, ValueError) as e:
        return (WRITE_FAILED, type(e).__name__)
    if n != len(data):
        return (WRITE_FAILED, "short write of %d of %d bytes" % (n, len(data)))
    return None


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
    the session's transcript folder under projects_dir. Answers from run records and meta
    files are cached; None is not, because the file may appear later."""

    def __init__(self, projects_dir: Optional[Path] = None) -> None:
        self.projects_dir = Path(projects_dir) if projects_dir is not None \
            else gk_common.claude_dir() / "projects"
        self._cache: dict = {}
        self._lock = threading.Lock()

    def workflow(self, session: Any, run_id: Any) -> Optional[str]:
        """`workflowName` from projects_dir/*/<session>/workflows/<run_id>.json. Claude Code
        writes that run record only when the run ends, so during the run the name comes from
        the script file it writes before the first agent,
        projects_dir/*/<session>/workflows/scripts/<name>-<run_id>.js. The run record wins
        when both exist. A name read from a script file is not cached, so the run record is
        preferred once it appears."""
        found = self._find("workflow", session, run_id,
                           "*/%s/workflows/%s.json", "workflowName")
        return found if found is not None else self._script_name(session, run_id)

    def _script_name(self, session: Any, run_id: Any) -> Optional[str]:
        """<name> from the first projects_dir/*/<session>/workflows/scripts/<name>-<run_id>.js.
        The name may itself hold hyphens: it is everything before "-<run_id>.js"."""
        s, r = gk_common.safe_sid(session), gk_common.safe_sid(run_id)
        if s is None or r is None:
            return None
        suffix = "-%s.js" % r
        try:
            paths = sorted(self.projects_dir.glob("*/%s/workflows/scripts/*%s" % (s, suffix)))
        except (OSError, ValueError):
            return None
        for path in paths:
            name = path.name[:-len(suffix)]
            if name and path.name.endswith(suffix):
                return name
        return None

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
    carry session, request, prompt, agent or workflow-run ids.

    Prometheus's increase() ignores a series' first sample, so a new series (a new label set,
    or every set after the receiver restarts) would lose its first value. So render() shows a
    series as 0 the first time it includes it, and its running total from the next render on.
    """

    def __init__(self) -> None:
        self._values: dict = {}
        self._shown: set = set()  # series some render() has already shown, as 0 the first time
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
            items = []
            for key, value in sorted(self._values.items()):
                if key not in self._shown:
                    self._shown.add(key)
                    value = 0
                items.append((key, value))
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


# ---------------------------------------------------------------------------------------------
# The HTTP server (spec 4.2 and 6)

MAX_BODY = 4 * 1024 * 1024
IDLE_SECONDS = 1800
MAP_RETENTION_DAYS = 90
LOG_NAME = "receiver.log"
PID_NAME = "receiver.pid"
READ_TIMEOUT = 30  # seconds a client may take to send its headers and body
LOG_DETAIL_MAX = 300

LOGS_PATH = "/v1/logs"
HEALTH_PATH = "/gk-ledger/health"
METRICS_PATH = "/metrics"
METRICS_TYPE = "text/plain; version=0.0.4"


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ErrorLog:
    """Counts errors by kind for receiver.log. The first instance of each kind is written at
    once; running counts are written when a kind's count reaches 10, 100, 1000 ... and by
    flush(). Details name the error, never a request body. Writing the log never raises."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.counts: dict = {}
        self._lock = threading.Lock()

    def record(self, kind: str, detail: str) -> None:
        with self._lock:
            n = self.counts.get(kind, 0) + 1
            self.counts[kind] = n
            counts = dict(self.counts)
        if n == 1:
            self._write("first %s: %s" % (kind, str(detail)[:LOG_DETAIL_MAX]))
        elif n >= 10 and str(n).rstrip("0") == "1":
            self._write(self._counts_text(counts))

    def note(self, text: str) -> None:
        self._write(text)

    def flush(self) -> None:
        with self._lock:
            counts = dict(self.counts)
        if counts:
            self._write(self._counts_text(counts))

    @staticmethod
    def _counts_text(counts: dict) -> str:
        return "counts " + " ".join("%s=%d" % kv for kv in sorted(counts.items()))

    def _write(self, text: str) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
            with open(fd, "a", encoding="utf-8", errors="replace") as f:
                f.write("%s %s\n" % (_now_iso(), text.replace("\n", " ")))
        except (OSError, ValueError):
            pass


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "gk-ledger/%d" % LEDGER_VERSION
    sys_version = ""
    timeout = READ_TIMEOUT
    server: "LedgerServer"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass  # no per-request log lines

    def _reply(self, status: int, body: bytes = b"{}", ctype: str = "application/json",
               close: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _refuse(self, status: int, kind: str, detail: str) -> None:
        """An error reply. The body may be unread, so the connection closes after it."""
        self.server.errors.record(kind, detail)
        self._reply(status, close=True)

    def _path(self) -> str:
        return urllib.parse.urlsplit(self.path).path

    def do_GET(self) -> None:
        self.server.touch()
        path = self._path()
        if path == HEALTH_PATH:
            self._reply(200, json.dumps({"gk-ledger": LEDGER_VERSION}).encode())
        elif path == METRICS_PATH:
            self._reply(200, self.server.metrics.render().encode("utf-8"), METRICS_TYPE)
        else:
            self._reply(404, close=True)

    def do_POST(self) -> None:
        self.server.touch()
        if self._path() != LOGS_PATH:
            self._reply(404, close=True)
            return
        ctype = self.headers.get("Content-Type") or ""
        if "json" not in ctype.lower():
            self._refuse(415, "wrong_type", "Content-Type %r" % ctype[:80])
            return
        raw = self.headers.get("Content-Length")
        if raw is None:
            self._refuse(411, "no_length", "no Content-Length")
            return
        try:
            length = int(raw.strip())
            if length < 0 or not raw.strip().isdigit():
                raise ValueError
        except ValueError:
            self._refuse(400, "bad_length", "Content-Length %r" % raw[:40])
            return
        if length > MAX_BODY:
            self._refuse(413, "too_large", "Content-Length %d over %d" % (length, MAX_BODY))
            return
        try:
            body = self.rfile.read(length)
        except OSError as e:
            self.server.errors.record("read_failed", type(e).__name__)
            self.close_connection = True
            return
        if len(body) != length:
            self._refuse(400, "short_body", "%d of %d bytes" % (len(body), length))
            return
        try:
            records = parse_logs(body)
        except ValueError as e:
            self._refuse(400, "parse", str(e))
            return
        self.server.ingest(records)
        self._reply(200)

    def _not_found(self) -> None:
        self.server.touch()
        self._reply(404, close=True)

    do_HEAD = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _not_found


class LedgerServer(ThreadingHTTPServer):
    """The receiver: writes Claude Code's api_request and api_error records to the ledger."""

    daemon_threads = True
    allow_reuse_port = False  # Python 3.13's HTTPServer turns it on; a second bind must fail
    request_queue_size = 128  # the default backlog of 5 resets bursts from many sessions

    def __init__(self, ledger_dir: Path, address: tuple) -> None:
        self.ledger_dir = Path(ledger_dir)
        self.lookup = SessionLookup(self.ledger_dir / "sessions")
        self.names = NameLookup(gk_common.claude_dir() / "projects")
        self.metrics = Metrics()
        self.errors = ErrorLog(self.ledger_dir / LOG_NAME)
        self.last_request = time.monotonic()
        super().__init__(address, _Handler)

    def touch(self) -> None:
        self.last_request = time.monotonic()

    def handle_error(self, request: Any, client_address: Any) -> None:
        """socketserver prints tracebacks to stderr; count them in receiver.log instead. A
        client that hangs up or stalls is normal (the prompt hook's port probe does) and is
        not counted."""
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError, socket.timeout)):
            return
        self.errors.record("handler", "%s: %s" % (type(exc).__name__, exc))

    def ingest(self, records: list) -> None:
        for res, attrs, event_name, time_ns in records:
            line = _safe_call(to_line, res, attrs, event_name, time_ns, self.lookup, self.names)
            if line is None:
                continue
            failure = _write_line(self.ledger_dir, line)
            if failure is None:
                self.metrics.add(line)
            else:
                self.errors.record(*failure)


def make_server(ledger_dir: Path, port: int, host: str = gk_common.LEDGER_HOST
                ) -> ThreadingHTTPServer:
    """A bound, not yet serving, receiver on host:port. Makes ledger_dir and its sessions
    folder (mode 0700) when they are missing."""
    server = LedgerServer(ledger_dir, (host, port))
    try:
        gk_common.make_private_dirs(ledger_dir, Path(ledger_dir) / "sessions")
    except OSError as e:
        server.errors.record("ledger_dir", "%s: %s" % (type(e).__name__, e))
    return server


def prune_session_maps(sessions_dir: Path, days: int = MAP_RETENTION_DAYS) -> int:
    """Remove session maps not modified for `days` days. Returns how many were removed."""
    cutoff = time.time() - days * 86400
    removed = 0
    try:
        paths = list(Path(sessions_dir).glob("*.json"))
    except OSError:
        return 0
    for p in paths:
        try:
            st = p.lstat()
            if stat.S_ISREG(st.st_mode) and st.st_mtime < cutoff:
                p.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def _write_pidfile(path: Path) -> bool:
    tmp = path.with_name("%s.%d.tmp" % (path.name, os.getpid()))
    try:
        fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with open(fd, "w") as f:
            f.write("%d\n" % os.getpid())
        os.replace(tmp, path)
        return True
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()
        return False


def _remove_own_pidfile(path: Path) -> None:
    """Remove the pidfile only while it still names this process."""
    try:
        if path.read_text().strip() == str(os.getpid()):
            path.unlink()
    except OSError:
        pass


def serve(ledger_dir: Path, port: int, idle_seconds: int = IDLE_SECONDS,
          live: Callable[[], bool] = gk_common.live_session) -> int:
    """Run the receiver until it has had no request for idle_seconds while live() is False,
    or until SIGTERM. Returns 0, also when the port is already in use (then it writes
    nothing); 1 when it cannot bind for another reason."""
    ledger_dir = Path(ledger_dir)
    try:
        server = make_server(ledger_dir, port)
    except (OSError, OverflowError) as e:
        if getattr(e, "errno", None) == errno.EADDRINUSE:
            return 0
        ErrorLog(ledger_dir / LOG_NAME).note("bind failed on port %s: %s" % (port, e))
        return 1
    pidfile = ledger_dir / PID_NAME
    stop = threading.Event()
    old_handler = None
    if threading.current_thread() is threading.main_thread():
        try:
            old_handler = signal.signal(signal.SIGTERM, lambda signum, frame: stop.set())
        except (ValueError, OSError):
            old_handler = None
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.25},
                              name="gk-ledger-server", daemon=True)
    try:
        prune_session_maps(ledger_dir / "sessions")
        if not _write_pidfile(pidfile):
            server.errors.note("could not write %s" % PID_NAME)
        server.touch()
        thread.start()
        tick = max(0.05, min(5.0, idle_seconds / 4))
        while not stop.wait(tick):
            if time.monotonic() - server.last_request < idle_seconds:
                continue
            try:
                busy = bool(live())
            except Exception:
                busy = False
            if not busy:
                break
    finally:
        if thread.is_alive():
            server.shutdown()
            thread.join(10)
        server.server_close()
        _remove_own_pidfile(pidfile)
        server.errors.flush()
        if old_handler is not None:
            with contextlib.suppress(ValueError, OSError):
                signal.signal(signal.SIGTERM, old_handler)
    return 0


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(prog="gk_ledger.py",
                                     description="The gk-harness usage ledger's receiver.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_serve = sub.add_parser("serve", help="run the receiver on the loopback address")
    p_serve.add_argument("--port", type=int, default=None)
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2
    if args.command == "serve":
        port = args.port if args.port is not None else gk_common.ledger_port()
        return serve(gk_common.ledger_dir(), port)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
