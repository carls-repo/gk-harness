"""gkh.peers: the busy Claude sessions on this machine that an install or update must not swap
hooks under, and this process's own ancestors, which never count."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from gkh import peers  # noqa: E402


def dead_pid() -> int:
    """The pid of a process that has exited and been reaped."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


@unittest.skipIf(os.name == "nt", "spawns sleep")
class BusyPeersTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.cdir = Path(tmp) / ".claude"
        self.sessions = self.cdir / "sessions"
        self.sessions.mkdir(parents=True)
        self.child = subprocess.Popen(["sleep", "30"])

    def tearDown(self):
        self.child.kill()
        self.child.wait()

    def session(self, stem: str, data) -> None:
        text = data if isinstance(data, str) else json.dumps(data)
        (self.sessions / ("%s.json" % stem)).write_text(text, encoding="utf-8")

    def test_only_a_live_busy_session_outside_our_ancestry_counts(self):
        live = self.child.pid
        self.session("a-live", {"pid": live, "sessionId": "1", "name": "builder",
                                "status": "busy"})
        self.session("b-idle", {"pid": live, "name": "idler", "status": "idle"})
        self.session("c-dead", {"pid": dead_pid(), "name": "ghost", "status": "busy"})
        self.session("d-self", {"pid": os.getpid(), "name": "me", "status": "busy"})
        self.session("e-parent", {"pid": os.getppid(), "name": "my-parent", "status": "busy"})
        own = peers.ancestor_pids()
        self.assertEqual(peers.busy_peers(self.cdir, own), ["builder"])

    def test_a_session_without_a_name_is_listed_by_its_file_stem(self):
        self.session("7c1e-session", {"pid": self.child.pid, "status": "busy"})
        self.session("blank-name", {"pid": self.child.pid, "status": "busy", "name": ""})
        self.assertEqual(peers.busy_peers(self.cdir, set()), ["7c1e-session", "blank-name"])

    def test_unreadable_and_malformed_files_are_skipped(self):
        self.session("a", "{not json")
        self.session("b", "[1, 2]")
        self.session("c", {"pid": str(self.child.pid), "status": "busy", "name": "strpid"})
        self.session("d", {"pid": True, "status": "busy", "name": "boolpid"})
        self.session("e", {"status": "busy", "name": "nopid"})
        (self.sessions / "f.json").write_bytes(b"\xff\xfe\x00garbage")
        (self.sessions / "g.json").mkdir()
        (self.sessions / "notes.txt").write_text(json.dumps(
            {"pid": self.child.pid, "status": "busy", "name": "not-json-suffix"}))
        self.session("z", {"pid": self.child.pid, "status": "busy", "name": "real"})
        self.assertEqual(peers.busy_peers(self.cdir, set()), ["real"])

    def test_no_sessions_folder(self):
        shutil.rmtree(str(self.sessions))
        self.assertEqual(peers.busy_peers(self.cdir, set()), [])

    def test_own_excludes_by_pid(self):
        self.session("x", {"pid": self.child.pid, "status": "busy", "name": "child"})
        self.assertEqual(peers.busy_peers(self.cdir, {self.child.pid}), [])


class PidAliveTests(unittest.TestCase):
    def test_values(self):
        self.assertTrue(peers.pid_alive(os.getpid()))
        for bad in (0, -1, None, "1", True, 2 ** 32 + 4):
            with self.subTest(bad=bad):
                self.assertFalse(peers.pid_alive(bad))
        self.assertFalse(peers.pid_alive(dead_pid()))


class AncestorTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX only")
    def test_holds_self_and_parent(self):
        own = peers.ancestor_pids()
        self.assertIn(os.getpid(), own)
        self.assertIn(os.getppid(), own)

    @unittest.skipIf(os.name == "nt", "POSIX only")
    def test_a_grandchild_sees_this_process(self):
        code = ("import sys; sys.path.insert(0, %r); from gkh import peers; "
                "print(' '.join(map(str, sorted(peers.ancestor_pids()))))" % ROOT)
        out = subprocess.run(["sh", "-c", '"$0" -c "$1"; :', sys.executable, code],
                             capture_output=True, text=True, timeout=30).stdout
        self.assertIn(os.getpid(), [int(p) for p in out.split()])

    def test_windows_is_empty(self):
        with mock.patch.object(sys, "platform", "win32"):
            self.assertEqual(peers.ancestor_pids(), set())

    def test_stat_ppid_after_the_last_closing_bracket(self):
        self.assertEqual(peers.ppid_from_stat("1234 (bash) S 77 1234 1234 0 -1"), 77)
        self.assertEqual(peers.ppid_from_stat("1234 (a) b (c) R 88 1 1 0"), 88)
        self.assertEqual(peers.ppid_from_stat("9 (tmux: server) S 1 9 9"), 1)
        for bad in ("", "1234 bash S 77", "1 (x) S", "1 (x) S notanumber"):
            with self.subTest(bad=bad):
                self.assertIsNone(peers.ppid_from_stat(bad))

    def test_the_walk_stops_on_an_error_and_is_capped(self):
        with mock.patch.object(sys, "platform", "linux"), \
                mock.patch.object(peers, "_parent_linux", side_effect=OSError("gone")):
            self.assertEqual(peers.ancestor_pids(), {os.getpid()})
        # A loop of parents (never 1) stops at the cap rather than running for ever.
        with mock.patch.object(sys, "platform", "linux"), \
                mock.patch.object(peers, "_parent_linux", side_effect=lambda pid: pid + 1):
            own = peers.ancestor_pids()
        self.assertLessEqual(len(own), peers.MAX_DEPTH + 1)
        self.assertIn(os.getpid(), own)

    def test_macos_reads_ps(self):
        calls = []

        def fake(pid):
            calls.append(pid)
            return {os.getpid(): 500, 500: 1}.get(pid)

        with mock.patch.object(sys, "platform", "darwin"), \
                mock.patch.object(peers, "_parent_ps", side_effect=fake):
            self.assertEqual(peers.ancestor_pids(), {os.getpid(), 500, 1})
        self.assertEqual(calls, [os.getpid(), 500])


if __name__ == "__main__":
    unittest.main()
