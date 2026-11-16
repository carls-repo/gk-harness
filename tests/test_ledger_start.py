"""Task 4: the receiver's start logic (gk_common) and its wiring into gk-seat.py (SessionStart)
and gk-peers.py (UserPromptSubmit). No test leaves a receiver running: start_receiver is
monkeypatched, and the one real spawn is terminated in cleanup."""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "hooks"))

import gk_common  # noqa: E402
import gk_ledger  # noqa: E402

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_NOSYSTEM": "1",  # GIT_CONFIG_GLOBAL is added by setUpModule
}
_GLOBAL_DIR = None


def setUpModule():
    global _GLOBAL_DIR
    _GLOBAL_DIR = tempfile.TemporaryDirectory()
    empty = os.path.join(_GLOBAL_DIR.name, "gitconfig")
    open(empty, "w").close()
    GIT_ENV["GIT_CONFIG_GLOBAL"] = empty


def tearDownModule():
    GIT_ENV.pop("GIT_CONFIG_GLOBAL", None)
    if _GLOBAL_DIR is not None:
        _GLOBAL_DIR.cleanup()


def git(cwd, *args):
    env = dict(os.environ, **GIT_ENV)
    subprocess.run(["git", "-C", str(cwd)] + list(args), check=True, capture_output=True,
                   stdin=subprocess.DEVNULL, env=env)


def load_hook(filename, name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "hooks", filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def refused_port():
    """A loopback port nothing listens on (bound, then released)."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(os.path.realpath(self._tmp.name))
        self.claude = self.tmp / "claude"
        self.claude.mkdir()
        self.port = refused_port()
        self.env = dict(GIT_ENV, GK_CLAUDE_DIR=str(self.claude), GK_NO_LAUNCH_ARGS="1",
                        GK_LEDGER_PORT=str(self.port))
        patcher = mock.patch.dict(os.environ, self.env)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("GK_PROFILE", None)
        self.seat = load_hook("gk-seat.py", "gk_seat_ledger_test")
        self.peers = load_hook("gk-peers.py", "gk_peers_ledger_test")
        self.spawns = []
        self.spawn_effect = None
        patcher = mock.patch.object(gk_common, "start_receiver", side_effect=self._fake_start)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.emitted = []
        patcher = mock.patch.object(gk_common, "emit", side_effect=self.emitted.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _fake_start(self, port, wait=1.0):
        self.spawns.append((port, wait))
        if self.spawn_effect is not None:
            return self.spawn_effect(port)
        return False

    def ledger_on(self):
        p = gk_common.ledger_on_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}")

    def set_port(self, port):
        self.port = port
        os.environ["GK_LEDGER_PORT"] = str(port)

    def our_server(self, port=0):
        server = gk_ledger.make_server(self.claude / "state" / "gk-ledger", port=port)
        thread = threading.Thread(target=server.serve_forever,
                                  kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()

        def stop():
            server.shutdown()
            server.server_close()
            thread.join(5)
        self.addCleanup(stop)
        return server.server_address[1]

    def foreign_listener(self):
        """A bare socket that accepts connections (in the backlog) and never answers."""
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(8)
        self.addCleanup(s.close)
        return s.getsockname()[1]

    def repo(self, name="proj"):
        p = self.tmp / name
        p.mkdir()
        git(p, "init", "-q", "-b", "main")
        git(p, "commit", "-q", "--allow-empty", "-m", "init")
        return p

    def map_of(self, sid):
        p = gk_common.session_map_path(sid)
        return json.loads(p.read_text()) if p.exists() else None

    def session_start(self, sid="s1", cwd=None):
        self.emitted.clear()
        self.seat.handle({"hook_event_name": "SessionStart", "session_id": sid,
                          "source": "clear", "model": "claude-opus-5-5",
                          "cwd": str(cwd or self.tmp)})
        self.assertEqual(len(self.emitted), 1)
        return self.emitted[0]["hookSpecificOutput"]["additionalContext"]

    def prompt(self, sid="s1", cwd=None):
        self.emitted.clear()
        self.peers.handle({"hook_event_name": "UserPromptSubmit", "session_id": sid,
                           "cwd": str(cwd or self.tmp), "prompt": "hi"})
        return list(self.emitted)


class StartHelperTests(_Base):
    def test_probe_free_ours_foreign(self):
        self.assertEqual(gk_common.probe_health(refused_port()), "free")
        self.assertEqual(gk_common.probe_health(self.our_server()), "ours")
        self.assertEqual(gk_common.probe_health(self.foreign_listener(), timeout=0.2), "foreign")

    def test_probe_json_without_key_is_foreign(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        self.addCleanup(s.close)

        def answer():
            conn, _ = s.accept()
            with conn:
                conn.recv(4096)
                body = b'{"status": "ok"}'
                conn.sendall(b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n"
                             b"Content-Length: %d\r\n\r\n%s" % (len(body), body))
        t = threading.Thread(target=answer, daemon=True)
        t.start()
        self.assertEqual(gk_common.probe_health(s.getsockname()[1]), "foreign")
        t.join(5)

    def test_port_open(self):
        self.assertFalse(gk_common.port_open(refused_port()))
        self.assertTrue(gk_common.port_open(self.foreign_listener()))

    def test_probe_under_100ms_on_refused_port(self):
        port = refused_port()
        t0 = time.monotonic()
        self.assertFalse(gk_common.port_open(port))
        self.assertLess(time.monotonic() - t0, 0.1)
        t0 = time.monotonic()
        self.assertEqual(gk_common.probe_health(port), "free")
        self.assertLess(time.monotonic() - t0, 0.1)

    def test_notice_texts(self):
        self.assertEqual(gk_common.LEDGER_DOWN, "Ledger: receiver not running, calls are not "
                         "being recorded (gkh ledger status)")
        self.assertEqual(gk_common.LEDGER_FOREIGN % 4318, "Ledger: port 4318 is held by another "
                         "program, calls are not being recorded (gkh ledger status)")


class RealSpawnTests(unittest.TestCase):
    """start_receiver itself, unpatched: one real receiver, terminated in cleanup."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.claude = Path(tmp.name) / "claude"
        self.claude.mkdir()
        patcher = mock.patch.dict(os.environ, {"GK_CLAUDE_DIR": str(self.claude)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_start_receiver_spawns_detached_and_waits_for_port(self):
        port = refused_port()
        procs, calls = [], []
        real_popen = subprocess.Popen

        def spy(args, **kw):
            calls.append((args, kw))
            p = real_popen(args, **kw)
            procs.append(p)
            return p

        def kill():
            for p in procs:
                if p.poll() is None:
                    p.terminate()
                    try:
                        p.wait(5)
                    except subprocess.TimeoutExpired:
                        p.kill()
                        p.wait(5)
        self.addCleanup(kill)
        with mock.patch.object(gk_common.subprocess, "Popen", side_effect=spy):
            self.assertTrue(gk_common.start_receiver(port, wait=5.0))
        self.assertEqual(len(calls), 1)
        args, kw = calls[0]
        self.assertEqual(args, [sys.executable,
                                os.path.join(ROOT, "hooks", "gk_ledger.py"),
                                "serve", "--port", str(port)])
        self.assertTrue(kw.get("start_new_session"))
        self.assertTrue(kw.get("close_fds"))
        for k in ("stdin", "stdout", "stderr"):
            self.assertEqual(kw.get(k), subprocess.DEVNULL)
        # The receiver runs in the ledger folder, made private, so it never pins a worktree.
        ldir = gk_common.ledger_dir()
        self.assertEqual(kw.get("cwd"), str(ldir))
        self.assertTrue(ldir.is_dir())
        if os.name != "nt":
            self.assertEqual(os.stat(ldir).st_mode & 0o777, 0o700)
        if sys.platform.startswith("linux"):
            self.assertEqual(os.readlink("/proc/%d/cwd" % procs[0].pid), str(ldir))
        self.assertEqual(gk_common.probe_health(port), "ours")

    def test_wait_zero_returns_at_once(self):
        with mock.patch.object(gk_common.subprocess, "Popen") as popen, \
                mock.patch.object(gk_common, "port_open") as po:
            t0 = time.monotonic()
            self.assertTrue(gk_common.start_receiver(refused_port(), wait=0))
            self.assertLess(time.monotonic() - t0, 0.1)
        popen.assert_called_once()
        po.assert_not_called()

    def test_spawn_oserror_returns_false(self):
        with mock.patch.object(gk_common.subprocess, "Popen", side_effect=OSError("nope")):
            self.assertFalse(gk_common.start_receiver(refused_port(), wait=0))


class SessionStartTests(_Base):
    def test_off_does_nothing(self):
        with mock.patch.object(gk_common, "probe_health") as ph, \
                mock.patch.object(gk_common, "port_open") as po:
            card = self.session_start()
            self.prompt()
        self.assertNotIn("Ledger:", card)
        self.assertIsNone(self.map_of("s1"))
        self.assertFalse((self.claude / "state" / "gk-ledger").exists())
        self.assertEqual(self.spawns, [])
        ph.assert_not_called()
        po.assert_not_called()

    def test_card_unchanged_when_ours(self):
        off_card = self.session_start()
        self.ledger_on()
        self.set_port(self.our_server())
        self.assertEqual(self.session_start(), off_card)

    def test_session_start_free_spawns(self):
        self.ledger_on()
        self.spawn_effect = lambda port: bool(self.our_server(port))
        repo = self.repo()
        card = self.session_start(cwd=repo)
        self.assertEqual(self.spawns, [(self.port, 1.0)])
        self.assertNotIn("Ledger:", card)
        m = self.map_of("s1")
        self.assertEqual((m["root"], m["branch"]), (str(repo), "main"))

    def test_session_start_ours_no_spawn(self):
        self.ledger_on()
        self.set_port(self.our_server())
        card = self.session_start()
        self.assertEqual(self.spawns, [])
        self.assertNotIn("Ledger:", card)
        self.assertIsNotNone(self.map_of("s1"))

    def test_session_start_foreign_notice_once(self):
        self.ledger_on()
        self.set_port(self.foreign_listener())
        card = self.session_start()
        self.assertEqual(self.spawns, [])
        self.assertEqual(card.count(gk_common.LEDGER_FOREIGN % self.port), 1)
        self.assertEqual(card.count("Ledger:"), 1)
        self.assertNotIn(gk_common.LEDGER_DOWN, card)

    def test_session_start_spawn_fails_down_notice(self):
        self.ledger_on()
        card = self.session_start()
        self.assertEqual(self.spawns, [(self.port, 1.0)])
        self.assertTrue(card.endswith("\n" + gk_common.LEDGER_DOWN))
        self.assertEqual(card.count("Ledger:"), 1)

    def test_session_start_ledger_errors_leave_card_unchanged(self):
        off_card = self.session_start()
        self.ledger_on()
        with mock.patch.object(gk_common, "write_session_map", side_effect=RuntimeError("x")), \
                mock.patch.object(gk_common, "probe_health", side_effect=RuntimeError("y")), \
                mock.patch("sys.stderr"):
            self.assertEqual(self.session_start(), off_card)

    def test_post_model_switch_does_no_ledger_work(self):
        self.ledger_on()
        self.emitted.clear()
        self.seat.handle({"hook_event_name": "PostModelSwitch", "session_id": "s1",
                          "to_model": "claude-sonnet-5-5", "cwd": str(self.tmp)})
        self.assertEqual(self.spawns, [])
        self.assertIsNone(self.map_of("s1"))
        self.assertNotIn("Ledger:", self.emitted[0]["hookSpecificOutput"]["additionalContext"])


class PromptTests(_Base):
    def test_prompt_probe_refused_spawns_without_wait(self):
        self.ledger_on()
        out = self.prompt()
        self.assertEqual(self.spawns, [(self.port, 0)])
        self.assertEqual(out, [])
        self.assertIsNotNone(self.map_of("s1"))

    def test_prompt_probe_open_port_no_spawn(self):
        self.ledger_on()
        self.set_port(self.foreign_listener())
        self.assertEqual(self.prompt(), [])
        self.set_port(self.our_server())
        self.assertEqual(self.prompt(), [])
        self.assertEqual(self.spawns, [])

    def test_prompt_refreshes_map_after_branch_switch(self):
        self.ledger_on()
        self.set_port(self.our_server())
        repo = self.repo()
        self.prompt(cwd=repo)
        self.assertEqual(self.map_of("s1")["branch"], "main")
        git(repo, "checkout", "-q", "-b", "feat/x")
        self.prompt(cwd=repo)
        self.assertEqual(self.map_of("s1")["branch"], "feat/x")

    def test_prompt_hook_errors_never_raise(self):
        self.ledger_on()
        with mock.patch.object(gk_common, "write_session_map", side_effect=RuntimeError("x")), \
                mock.patch("sys.stderr") as err:
            self.assertEqual(self.prompt(), [])
        self.assertIn("RuntimeError", "".join(c.args[0] for c in err.write.call_args_list))
        # A map failure does not stop the receiver check.
        self.assertEqual(self.spawns, [(self.port, 0)])
        with mock.patch.object(gk_common, "port_open", side_effect=RuntimeError("y")), \
                mock.patch("sys.stderr"):
            self.assertEqual(self.prompt(), [])

    def test_session_start_peers_does_no_ledger_work(self):
        self.ledger_on()
        self.emitted.clear()
        self.peers.handle({"hook_event_name": "SessionStart", "session_id": "s1",
                           "cwd": str(self.tmp)})
        self.assertEqual(self.spawns, [])
        self.assertIsNone(self.map_of("s1"))


if __name__ == "__main__":
    unittest.main()
