"""gkh/ledger_ctl.py: the ledger's switch, the receiver's start and stop, and the status lines.
Every test points GK_CLAUDE_DIR at a temporary folder and GK_LEDGER_PORT at a free loopback
port, so nothing here reads the real ~/.claude or touches port 4318, and every receiver or
stand-in a test starts is stopped by its cleanup."""
from __future__ import annotations

import datetime
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from gkh import ledger_ctl, settings_merge as sm  # noqa: E402

HOOKS = Path(ROOT) / "hooks"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def load_hook(name: str):
    """hooks/<name>.py loaded by path, with hooks/ on sys.path for its own imports."""
    sys.path.insert(0, str(HOOKS))
    try:
        spec = importlib.util.spec_from_file_location("_gkctl_" + name, HOOKS / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(HOOKS))
    return module


def utc_iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


class LedgerCtlBase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.cdir = Path(tmp) / ".claude"
        self.cdir.mkdir()
        self.port = free_port()
        env = mock.patch.dict(os.environ, {"GK_CLAUDE_DIR": str(self.cdir),
                                           "GK_LEDGER_PORT": str(self.port)})
        env.start()
        self.addCleanup(env.stop)
        self.ldir = self.cdir / "state" / "gk-ledger"

    def write(self, path: Path, text: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def switch(self, since: str) -> None:
        self.write(self.cdir / "state" / "gk-ledger-on.json", json.dumps({"since": since}))

    def settings(self, env: dict) -> None:
        self.write(self.cdir / "settings.json", json.dumps({"env": env}))

    def sleeper(self, *argv: str) -> subprocess.Popen:
        """A child that sleeps, named on its command line as argv says; killed at cleanup."""
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"] + list(argv))
        self.addCleanup(child.wait)
        self.addCleanup(lambda: child.poll() is None and child.kill())
        return child


class SharedNamesTests(LedgerCtlBase):
    def test_gkh_names_equal_the_hooks_names(self):
        common = load_hook("gk_common")
        ledger = load_hook("gk_ledger")
        self.assertEqual(ledger_ctl.switch_file(), Path(os.path.abspath(common.ledger_on_file())))
        self.assertEqual(ledger_ctl.ledger_dir(), Path(os.path.abspath(common.ledger_dir())))
        self.assertEqual(ledger_ctl.PORT, common.LEDGER_PORT)
        self.assertEqual(ledger_ctl.HOST, common.LEDGER_HOST)
        self.assertEqual(ledger_ctl.port(), common.ledger_port())
        for bad in ("", "x", "0", "70000"):
            with mock.patch.dict(os.environ, {"GK_LEDGER_PORT": bad}):
                self.assertEqual(ledger_ctl.port(), common.ledger_port(), bad)
                self.assertEqual(ledger_ctl.port(), 4318)
        self.assertEqual(ledger_ctl.PID_NAME, ledger.PID_NAME)
        self.assertEqual(ledger_ctl.HEALTH_PATH, ledger.HEALTH_PATH)
        self.assertEqual(sm.LEDGER_ENV[("env", "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT")],
                         "http://%s:%d%s" % (common.LEDGER_HOST, common.LEDGER_PORT,
                                             ledger.LOGS_PATH))
        # The same switch, read both ways.
        self.assertFalse(ledger_ctl.switch_on())
        self.assertFalse(common.ledger_on())
        self.switch("2026-11-16T10:00:00Z")
        self.assertTrue(ledger_ctl.switch_on())
        self.assertTrue(common.ledger_on())
        with mock.patch.object(ledger_ctl, "_windows", lambda: True):
            self.assertFalse(ledger_ctl.switch_on())

    def test_health_matches_the_hooks_probe(self):
        common = load_hook("gk_common")
        self.assertEqual(ledger_ctl.health(), "free")
        self.assertEqual(common.probe_health(self.port), "free")
        with socket.socket() as s:  # listens, never answers
            s.bind(("127.0.0.1", self.port))
            s.listen(1)
            self.assertEqual(ledger_ctl._probe(self.port, 0.2), "foreign")
            self.assertEqual(common.probe_health(self.port, 0.2), "foreign")


class LifecycleTests(LedgerCtlBase):
    def test_stop_via_pidfile(self):
        if os.name == "nt":
            self.skipTest("POSIX signals")
        child = self.sleeper("gk_ledger.py", "serve")
        self.write(self.ldir / "receiver.pid", "%d\n" % child.pid)
        self.assertEqual(ledger_ctl.pid(), child.pid)
        self.assertTrue(ledger_ctl.stop(timeout=5))
        # stop() reaps its own child (here, the test's), so only "gone" can be checked.
        self.assertFalse(ledger_ctl.peers.pid_alive(child.pid))
        self.assertIsNotNone(child.poll())
        self.assertIsNone(ledger_ctl.pid())
        self.assertTrue(ledger_ctl.stop())  # nothing left to stop

    def test_stop_never_signals_a_pid_that_is_not_the_receiver(self):
        if os.name == "nt":
            self.skipTest("POSIX signals")
        child = self.sleeper("something-else")
        self.write(self.ldir / "receiver.pid", "%d\n" % child.pid)
        self.assertIsNone(ledger_ctl.pid())
        self.assertTrue(ledger_ctl.stop(timeout=0.2))
        self.assertIsNone(child.poll(), "a reused pid must not be signalled")
        for junk in ("", "abc", "-4", "0"):
            self.write(self.ldir / "receiver.pid", junk)
            self.assertIsNone(ledger_ctl.pid(), junk)

    def test_start_spawns_the_installed_copy_detached(self):
        script = self.write(self.cdir / "hooks" / "gk-harness" / "gk_ledger.py", "")
        self.write(self.cdir / "gk-harness.json", json.dumps({"python": "/opt/py/bin/python3"}))
        spawned = []
        with mock.patch.object(ledger_ctl.subprocess, "Popen",
                               lambda argv, **kw: spawned.append((argv, kw))), \
                mock.patch.object(ledger_ctl, "health", lambda: "free"):
            self.assertFalse(ledger_ctl.start(wait=0.1))  # spawned, but never answered
        self.assertEqual(len(spawned), 1)
        argv, kw = spawned[0]
        self.assertEqual(argv, ["/opt/py/bin/python3", str(script), "serve", "--port",
                                str(self.port)])
        self.assertTrue(kw["start_new_session"])
        self.assertTrue(kw["close_fds"])
        for stream in ("stdin", "stdout", "stderr"):
            self.assertIs(kw[stream], subprocess.DEVNULL, stream)
        self.assertEqual(kw["cwd"], str(self.ldir))
        self.assertTrue(self.ldir.is_dir())
        # Without gk-harness.json, the Python running gkh.
        os.remove(str(self.cdir / "gk-harness.json"))
        spawned.clear()
        with mock.patch.object(ledger_ctl.subprocess, "Popen",
                               lambda argv, **kw: spawned.append((argv, kw))), \
                mock.patch.object(ledger_ctl, "health", lambda: "free"):
            ledger_ctl.start(wait=0)
        self.assertEqual(spawned[0][0][0], sys.executable)

    def test_start_spawns_nothing_when_the_port_is_taken_or_nothing_is_installed(self):
        spawned = []
        popen = mock.patch.object(ledger_ctl.subprocess, "Popen",
                                  lambda argv, **kw: spawned.append(argv))
        with popen:
            self.assertFalse(ledger_ctl.start(wait=0))  # no installed gk_ledger.py
            self.write(self.cdir / "hooks" / "gk-harness" / "gk_ledger.py", "")
            for state, result in (("ours", True), ("foreign", False)):
                with mock.patch.object(ledger_ctl, "health", lambda s=state: s):
                    self.assertIs(ledger_ctl.start(wait=0), result, state)
            with mock.patch.object(ledger_ctl, "_windows", lambda: True), \
                    mock.patch.object(ledger_ctl, "health", lambda: "free"):
                self.assertFalse(ledger_ctl.start(wait=0))
        self.assertEqual(spawned, [])

    def test_start_and_stop_a_real_receiver(self):
        if os.name == "nt":
            self.skipTest("the ledger does not run on Windows")
        target = self.cdir / "hooks" / "gk-harness"
        target.mkdir(parents=True)
        for name in ("gk_ledger.py", "gk_common.py"):
            shutil.copy(str(HOOKS / name), str(target / name))
        shutil.copy(os.path.join(ROOT, "routes.json"), str(target / "routes.json"))
        self.addCleanup(ledger_ctl.stop)
        self.assertTrue(ledger_ctl.start())
        self.assertEqual(ledger_ctl.health(), "ours")
        receiver = ledger_ctl.pid()
        self.assertIsNotNone(receiver)
        if sys.platform.startswith("linux"):
            self.assertEqual(os.readlink("/proc/%d/cwd" % receiver), str(self.ldir))
        self.assertTrue(ledger_ctl.start())  # up already: nothing more is spawned
        self.assertEqual(ledger_ctl.pid(), receiver)
        self.assertTrue(ledger_ctl.restart())
        self.assertNotEqual(ledger_ctl.pid(), receiver)
        self.assertTrue(ledger_ctl.stop())
        self.assertEqual(ledger_ctl.health(), "free")
        self.assertIsNone(ledger_ctl.pid())


class StatusLineTests(LedgerCtlBase):
    def test_status_lines_counts_today(self):
        self.switch("2026-11-16T10:00:00Z")
        lines = ledger_ctl.status_lines()
        self.assertIn("ledger: on since 2026-11-16T10:00:00Z", lines)
        self.assertIn("receiver: not running (port %d)" % self.port, lines)
        self.assertIn("today: no lines yet", lines)
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        rows = [{"ts": "%sT08:00:00.000Z" % today, "event": "api_request"},
                {"ts": "%sT09:30:15.250Z" % today, "event": "api_request"},
                {"ts": "%sT09:31:00.000Z" % today, "event": "api_error"}]
        self.write(self.ldir / ("%s.jsonl" % today),
                   "".join(json.dumps(r) + "\n" for r in rows))
        self.write(self.ldir / "2020-01-01.jsonl", json.dumps(rows[0]) + "\n")
        lines = ledger_ctl.status_lines()
        self.assertIn("today: 3 lines, the last at %sT09:31:00.000Z" % today, lines)
        self.assertIn("today: 3 lines, the last at %sT09:31:00.000Z" % today,
                      ledger_ctl.status_lines(short=True))
        self.assertIn("folder: %s" % self.ldir.as_posix(), lines)
        # The five keys, in the long form only.
        self.assertIn("settings.json keys:", lines)
        for path in sm.LEDGER_ENV:
            self.assertIn("  %s: absent" % path[-1], lines)
        self.assertNotIn("settings.json keys:", ledger_ctl.status_lines(short=True))
        self.settings({"OTEL_LOGS_EXPORTER": "otlp"})
        self.assertIn("  OTEL_LOGS_EXPORTER: set, not owned", ledger_ctl.status_lines())

    def test_status_when_off_is_short(self):
        self.assertEqual(ledger_ctl.status_lines(short=True), [])
        lines = ledger_ctl.status_lines()
        self.assertEqual(lines[0], "ledger: off")
        self.assertIn("receiver: not running (port %d)" % self.port, lines)

    def test_status_warns_content_variable(self):
        self.settings({"OTEL_LOG_USER_PROMPTS": "1", "OTEL_LOG_TOOL_DETAILS": "1",
                       "OTEL_LOGS_EXPORTER": "otlp", "OTHER": "x"})
        for short in (False, True):
            warnings = [ln for ln in ledger_ctl.status_lines(short=short)
                        if ln.startswith("warning:")]
            self.assertEqual(len(warnings), 2, warnings)
            self.assertIn("OTEL_LOG_USER_PROMPTS is set in settings.json env", warnings[0])
            self.assertIn("OTEL_LOG_TOOL_DETAILS is set in settings.json env", warnings[1])
        self.settings({"OTEL_LOGS_EXPORTER": "otlp"})
        self.assertFalse([ln for ln in ledger_ctl.status_lines() if ln.startswith("warning:")])

    def test_status_says_restart_sessions(self):
        if os.name == "nt":
            self.skipTest("the ledger does not run on Windows")
        live = self.sleeper()
        now = time.time()
        self.switch(utc_iso(now))

        def session(name: str, pid: int, started: float) -> None:
            self.write(self.cdir / "sessions" / ("%s.json" % name),
                       json.dumps({"pid": pid, "sessionId": name, "startedAt": int(started * 1000),
                                   "status": "idle"}))

        # Only sessions started after the switch: nothing to restart.
        session("new", live.pid, now + 60)
        self.assertNotIn(ledger_ctl.RESTART_LINE, ledger_ctl.status_lines())
        # A dead session from before counts for nothing.
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        session("gone", dead.pid, now - 3600)
        self.assertNotIn(ledger_ctl.RESTART_LINE, ledger_ctl.status_lines())
        # A live one from before the switch must restart to record.
        session("old", live.pid, now - 3600)
        self.assertIn(ledger_ctl.RESTART_LINE, ledger_ctl.status_lines())
        self.assertIn(ledger_ctl.RESTART_LINE, ledger_ctl.status_lines(short=True))
        self.assertEqual(ledger_ctl.RESTART_LINE, "restart sessions to start recording")
        # With the ledger off there is nothing to record.
        os.remove(str(self.cdir / "state" / "gk-ledger-on.json"))
        self.assertNotIn(ledger_ctl.RESTART_LINE, ledger_ctl.status_lines())


if __name__ == "__main__":
    unittest.main()
