"""The usage ledger's switch and receiver, as gkh sees them: `gkh ledger on|off|status`, install
(which restarts the receiver while the switch is on) and uninstall (which stops it).

gkh never imports the hooks package, so this module re-states the few names it shares with
hooks/gk_common.py and hooks/gk_ledger.py: the switch file, the ledger folder, the port and the
health probe, and the receiver's pidfile. tests/test_gkh_ledger_ctl.py holds them equal.

The receiver it starts is the installed copy, <claude_dir>/hooks/gk-harness/gk_ledger.py, run by
the Python that gk-harness.json names: the one the hooks run with.
"""
from __future__ import annotations

import datetime
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import paths, peers

HOST = "127.0.0.1"
PORT = 4318
PORT_VAR = "GK_LEDGER_PORT"
HEALTH_PATH = "/gk-ledger/health"
HEALTH_KEY = "gk-ledger"
PID_NAME = "receiver.pid"
SCRIPT = "gk_ledger.py"
START_WAIT = 3.0  # seconds start() waits for the receiver to answer its health probe
CONTENT_PREFIX = "OTEL_LOG_"  # Claude Code's content variables, never set by the installer
RESTART_LINE = "restart sessions to start recording"


def _windows() -> bool:
    return sys.platform == "win32"


def switch_file() -> Path:
    """The switch the hooks read (hooks/gk_common.py ledger_on_file)."""
    return paths.claude_dir() / "state" / "gk-ledger-on.json"


def ledger_dir() -> Path:
    """The day files, the receiver's pidfile and log, and the session maps
    (hooks/gk_common.py ledger_dir)."""
    return paths.claude_dir() / "state" / "gk-ledger"


def port() -> int:
    """$GK_LEDGER_PORT when it is an integer from 1 to 65535 (tests only), else PORT
    (hooks/gk_common.py ledger_port)."""
    try:
        n = int(os.environ.get(PORT_VAR, ""))
    except ValueError:
        return PORT
    return n if 1 <= n <= 65535 else PORT


def switch_on() -> bool:
    """Whether the switch file exists. Always False on Windows, where the ledger cannot run."""
    if _windows():
        return False
    return switch_file().exists()


def since() -> str | None:
    """The switch file's "since" (UTC, yyyy-mm-ddTHH:MM:SSZ), or None when it has none."""
    try:
        data = json.loads(switch_file().read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return None
    value = data.get("since") if isinstance(data, dict) else None
    return value if isinstance(value, str) and value else None


def _probe(port_: int, timeout: float = 0.5) -> str:
    """hooks/gk_common.py probe_health: "ours" when GET /gk-ledger/health answers JSON holding
    the "gk-ledger" key, "free" when the connect is refused, "foreign" otherwise."""
    import http.client

    try:
        conn = http.client.HTTPConnection(HOST, port_, timeout=timeout)
        try:
            conn.request("GET", HEALTH_PATH)
            body = conn.getresponse().read(4096)
        finally:
            conn.close()
    except ConnectionRefusedError:
        return "free"
    except Exception:
        return "foreign"
    try:
        data = json.loads(body)
    except Exception:
        return "foreign"
    return "ours" if isinstance(data, dict) and HEALTH_KEY in data else "foreign"


def health() -> str:
    """"ours", "free" or "foreign" for the ledger port."""
    return _probe(port())


def _command_line(pid: int) -> str | None:
    """The process's command line, or None when it cannot be read."""
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/%d/cmdline" % pid, "rb") as f:
                return f.read().replace(b"\0", b" ").decode("utf-8", "replace")
        out = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True,
                             stdin=subprocess.DEVNULL, timeout=peers.PS_TIMEOUT)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return out.stdout.decode("utf-8", "replace") if out.returncode == 0 else None


def pid() -> int | None:
    """The pid in the receiver's pidfile, when that process is alive and is the receiver: its
    command line holds "gk_ledger.py serve". A pid reused by another program since is not, and
    neither is one whose command line cannot be read (on Windows never, where the ledger does
    not run), so stop() never signals a process it cannot name."""
    try:
        n = int((ledger_dir() / PID_NAME).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    if n <= 0 or n == os.getpid() or not peers.pid_alive(n):
        return None
    line = _command_line(n)
    return n if line is not None and "%s serve" % SCRIPT in line else None


def _python() -> str:
    """The Python gk-harness.json names (the hooks' own), else the one running gkh."""
    try:
        cfg = json.loads(paths.harness_config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        cfg = None
    python = cfg.get("python") if isinstance(cfg, dict) else None
    return python if isinstance(python, str) and python else sys.executable


def start(wait: float = START_WAIT) -> bool:
    """Start the installed receiver, fully detached, unless it is up already. True when the
    port then answers as ours. Never spawns while another program holds the port, or on
    Windows."""
    if _windows():
        return False
    state = health()
    if state != "free":
        return state == "ours"
    script = paths.hooks_target() / SCRIPT
    if not script.is_file():
        return False
    folder = ledger_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Its own session, no inherited descriptors, stdio to nothing, and the ledger folder as
        # its working directory, so the long-lived receiver never pins a worktree.
        subprocess.Popen([_python(), str(script), "serve", "--port", str(port())],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True,
                         cwd=str(folder))
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    deadline = time.monotonic() + wait
    while True:
        if health() == "ours":
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def _reap(n: int) -> None:
    """Collect n's exit status when it is this process's own child, so it does not linger as a
    zombie that still counts as alive."""
    try:
        os.waitpid(n, os.WNOHANG)
    except (ChildProcessError, OSError):
        pass


def stop(timeout: float = 3.0) -> bool:
    """SIGTERM the receiver the pidfile names, then wait up to timeout seconds for it to exit.
    True when no receiver of ours is left running (also when none was)."""
    n = pid()
    if n is None:
        return True
    try:
        os.kill(n, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    deadline = time.monotonic() + timeout
    while True:
        _reap(n)
        if not peers.pid_alive(n):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def restart() -> bool:
    """stop(), then start(): the receiver runs the code install has just copied. True only
    when a receiver now answers and it is a new process: none of ours answered before, or the
    pid now running differs from the one before. False, with nothing started, when the old
    receiver did not stop; False too when the old one still answers (stop() could not name
    it, so start() found the port already ours)."""
    was_up = health() == "ours"
    before = pid()
    if not stop():
        return False
    if not start():
        return False
    if not was_up:
        return True
    after = pid()
    return before is not None and after is not None and after != before


# --- status ---------------------------------------------------------------------------------

def _today_file() -> Path:
    """Today's day file (hooks/gk_ledger.py day_file): the UTC date."""
    day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    return ledger_dir() / ("%s.jsonl" % day)


def _today() -> str:
    """"today: N lines, the last at <ts>" for today's day file."""
    path = _today_file()
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return "today: no lines yet"
    except OSError as e:
        return "today: %s cannot be read: %s" % (paths.posix(path), e.strerror or e)
    lines = [ln for ln in data.split(b"\n") if ln.strip()]
    if not lines:
        return "today: no lines yet"
    try:
        ts = json.loads(lines[-1].decode("utf-8")).get("ts")
    except (ValueError, AttributeError):
        ts = None
    if not isinstance(ts, str) or not ts:
        ts = paths.utc_iso(path.stat().st_mtime)
    return "today: %d %s, the last at %s" % (len(lines), "line" if len(lines) == 1 else "lines",
                                            ts)


def _receiver() -> str:
    state, p = health(), port()
    if state == "ours":
        n = pid()
        return "receiver: up on port %d%s" % (p, " (pid %d)" % n if n else "")
    if state == "foreign":
        return ("receiver: port %d is held by another program, so calls are not being recorded"
                % p)
    return "receiver: not running (port %d)" % p


def _settings() -> Any:
    try:
        return json.loads((paths.claude_dir() / "settings.json").read_bytes().decode("utf-8-sig"))
    except (OSError, ValueError, RecursionError):
        return None


def _key_lines(settings: Any) -> list:
    """One line per ledger key: owned, hand-changed, set (not owned) or absent."""
    from . import cli, manifest  # cli imports this module; import it only when needed
    from . import settings_merge as sm

    try:
        last, _ = cli._owned_last(manifest.load(paths.manifest_path()))
    except manifest.ManifestError:
        last = {}
    out = []
    for path in sm.LEDGER_ENV:
        key = sm.dotted(path)
        value = sm._get(settings, path)
        if key in last:
            state = "owned" if sm._same(value, last[key]) else "hand-changed"
        else:
            state = "absent" if value is sm._MISSING else "set, not owned"
        out.append("  %s: %s" % (path[-1], state))
    return out


def _sessions_before(when: str) -> bool:
    """Whether a live Claude session started before when (UTC, yyyy-mm-ddTHH:MM:SSZ)."""
    try:
        cut = datetime.datetime.strptime(when, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc).timestamp() * 1000
    except ValueError:
        return False
    try:
        files = sorted((paths.claude_dir() / "sessions").glob("*.json"))
    except OSError:
        return False
    for path in files:
        try:
            data = json.loads(path.read_bytes().decode("utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        started = data.get("startedAt")
        if isinstance(started, (int, float)) and not isinstance(started, bool) \
                and started < cut and peers.pid_alive(data.get("pid")):
            return True
    return False


def _content_warnings(settings: Any) -> list:
    env = settings.get("env") if isinstance(settings, dict) else None
    return ["warning: %s is set in settings.json env; Claude Code then sends content, which "
            "the receiver drops" % name
            for name in (env if isinstance(env, dict) else ()) if name.startswith(CONTENT_PREFIX)]


def status_lines(short: bool = False) -> list:
    """What `gkh ledger status` prints; short is the part `gkh status` adds under its ledger
    line. Both: the receiver's state and port, today's line count and the time of the last
    line, the restart line when a live session started before the switch went on, and a
    warning for each OTEL_LOG_* content variable in settings.json env. The long form also
    names the switch and the ledger folder, and the state of each of the five keys."""
    on = switch_on()
    settings = _settings()
    out = []
    if not short:
        if on:
            out.append("ledger: on since %s" % (since() or "an unknown time"))
        else:
            out.append("ledger: off" + (" (not available on Windows)" if _windows() else ""))
        out.append("folder: %s" % paths.posix(ledger_dir()))
    if on or not short:
        out.append(_receiver())
        out.append(_today())
    elif pid() is not None:
        out.append("receiver: still running though the ledger is off (pid %d)" % pid())
    if not short:
        out.append("settings.json keys:")
        out.extend(_key_lines(settings))
    when = since() if on else None
    if when and _sessions_before(when):
        out.append(RESTART_LINE)
    out.extend(_content_warnings(settings))
    return out
