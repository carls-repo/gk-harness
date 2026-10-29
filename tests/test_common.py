from __future__ import annotations

import io
import json
import ntpath
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))

import gk_common  # noqa: E402

# A child that takes the lock, says so on stdout, holds it, writes a marker
# while still holding it, then releases it by leaving the with-block.
LOCK_CHILD = r"""
import sys, time
sys.path.insert(0, sys.argv[1])
import gk_common
with gk_common.locked(sys.argv[2]):
    sys.stdout.write("locked\n")
    sys.stdout.flush()
    time.sleep(float(sys.argv[3]))
    with open(sys.argv[4], "w") as f:
        f.write("released")
"""


# Today's hooks (~/.claude/hooks/gk-remote-clear.py dir_lock): a blocking flock on the
# folder's own descriptor. The child takes it, says so, holds it, then lets go.
OLD_LOCK_HOLDER = r"""
import fcntl, os, sys, time
fd = os.open(sys.argv[1], os.O_RDONLY)
fcntl.flock(fd, fcntl.LOCK_EX)
sys.stdout.write("locked\n")
sys.stdout.flush()
time.sleep(float(sys.argv[2]))
os.close(fd)
"""

# Today's blocking flock, cut short by a 1 s alarm: prints GOT or TIMEOUT.
OLD_LOCK_TRY = r"""
import fcntl, os, signal, sys

class Alarm(Exception):
    pass

def on_alarm(signum, frame):
    raise Alarm()

signal.signal(signal.SIGALRM, on_alarm)
fd = os.open(sys.argv[1], os.O_RDONLY)
signal.alarm(1)
try:
    fcntl.flock(fd, fcntl.LOCK_EX)
    print("GOT")
except Alarm:
    print("TIMEOUT")
finally:
    signal.alarm(0)
    os.close(fd)
"""

# Temp folders carry a space, as a Windows home such as C:\Users\Carl Selby does.
TMP_PREFIX = "gk common "


def stdin_bytes(data):
    return io.TextIOWrapper(io.BytesIO(data), encoding="utf-8")


def same_path(a, b):
    """Path equality that survives Windows 8.3 short names and case differences."""
    return os.path.normcase(os.path.realpath(os.fspath(a))) == os.path.normcase(
        os.path.realpath(os.fspath(b)))


class PidAliveTests(unittest.TestCase):
    def test_pid_alive_self(self):
        # Checked in a child, not in the test runner: if pid_alive ever used os.kill on
        # Windows, the in-process call would terminate the runner with exit code 0 and
        # CI would go green with the failures unreported.
        code = "import os, sys; sys.path.insert(0, sys.argv[1]); import gk_common; print(gk_common.pid_alive(os.getpid()))"
        out = subprocess.run(
            [sys.executable, "-c", code, str(HOOKS)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=30,
        )
        self.assertEqual((out.returncode, out.stdout.strip()), (0, "True"), out.stderr)

    @unittest.skipUnless(os.name == "nt", "Windows-only: os.kill there terminates the process")
    def test_pid_alive_windows_never_calls_os_kill(self):
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
        try:
            with mock.patch("os.kill") as k:
                self.assertTrue(gk_common.pid_alive(os.getpid()))
                self.assertTrue(gk_common.pid_alive(p.pid))
            k.assert_not_called()
            self.assertIsNone(p.poll())
        finally:
            p.kill()
            p.wait()

    def test_pid_alive_dead(self):
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        self.assertFalse(gk_common.pid_alive(p.pid))

    def test_pid_alive_does_not_harm(self):
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
        try:
            for _ in range(3):
                self.assertTrue(gk_common.pid_alive(p.pid))
            self.assertIsNone(p.poll())
        finally:
            p.kill()
            p.wait()

    def test_pid_alive_rejects(self):
        for v in (None, True, False, 0, -1, "12", 12.0, 2**32 + 4):
            self.assertFalse(gk_common.pid_alive(v), repr(v))

    def test_pid_alive_rejects_pid_beyond_dword_before_asking_the_os(self):
        # A Windows DWORD would truncate 2**32 + 4 to 4 (System, always alive), so the
        # value must be refused before either OS call. Both are stubbed to say "alive".
        with mock.patch.object(gk_common, "_pid_alive_windows", return_value=True) as win, \
                mock.patch("os.kill") as kill:
            self.assertFalse(gk_common.pid_alive(2**32 + 4))
        win.assert_not_called()
        kill.assert_not_called()


class SidAndDirTests(unittest.TestCase):
    def test_safe_sid(self):
        self.assertEqual(gk_common.safe_sid("abc-123_x.y"), "abc-123_x.y")
        for v in (None, 5, "", "-abc", "../x", "a/b", "a\n", "a" * 129):
            self.assertIsNone(gk_common.safe_sid(v), repr(v))
        self.assertEqual(gk_common.safe_sid("a" * 128), "a" * 128)

    def test_claude_dir(self):
        with mock.patch.dict(os.environ, {"GK_CLAUDE_DIR": "/x/y"}):
            self.assertEqual(gk_common.claude_dir(), Path("/x/y"))
        with mock.patch.dict(os.environ, {"GK_CLAUDE_DIR": ""}):
            self.assertEqual(gk_common.claude_dir(), Path.home() / ".claude")


class JsonFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix=TMP_PREFIX)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_read_json_missing_and_invalid(self):
        self.assertIsNone(gk_common.read_json(os.path.join(self.tmp, "nope.json")))
        bad = os.path.join(self.tmp, "bad.json")
        with open(bad, "w") as f:
            f.write("{not json")
        self.assertIsNone(gk_common.read_json(bad))

    def test_write_json_roundtrip(self):
        path = os.path.join(self.tmp, "sub", "a.json")
        obj = {"a": [1, 2, {"b": "c\u00e9"}], "n": None}
        self.assertTrue(gk_common.write_json(path, obj))
        self.assertEqual(gk_common.read_json(path), obj)
        self.assertEqual(list(Path(self.tmp, "sub").glob("*.tmp")), [])

    def test_write_json_retries_then_succeeds(self):
        path = os.path.join(self.tmp, "a.json")
        real = os.replace
        calls = []

        def flaky(src, dst):
            calls.append(src)
            if len(calls) <= 2:
                raise PermissionError("locked by another process")
            return real(src, dst)

        with mock.patch("os.replace", side_effect=flaky):
            self.assertTrue(gk_common.write_json(path, {"x": 1}))
        self.assertEqual(len(calls), 3)
        self.assertEqual(gk_common.read_json(path), {"x": 1})
        self.assertEqual(list(Path(self.tmp).glob("*.tmp")), [])

    def test_write_json_gives_up_cleanly(self):
        path = os.path.join(self.tmp, "a.json")
        with mock.patch("os.replace", side_effect=PermissionError("locked")) as rep:
            self.assertFalse(gk_common.write_json(path, {"x": 1}))
        self.assertEqual(rep.call_count, 6)  # the first try and 5 retries
        self.assertFalse(os.path.exists(path))
        self.assertEqual(list(Path(self.tmp).glob("*.tmp")), [])

    def test_write_json_retries_temp_removal(self):
        # Windows: antivirus or the indexer can hold the new temp file for a moment, so
        # the first removals fail; the temp file must still be gone at the end.
        path = os.path.join(self.tmp, "a.json")
        real = os.unlink
        calls = []

        def flaky(p):
            calls.append(p)
            if len(calls) <= 2:
                raise PermissionError("held by a scanner")
            return real(p)

        with mock.patch("os.replace", side_effect=PermissionError("locked")), \
                mock.patch("os.unlink", side_effect=flaky):
            self.assertFalse(gk_common.write_json(path, {"x": 1}))
        self.assertEqual(len(calls), 3)
        self.assertEqual(list(Path(self.tmp).glob("*.tmp")), [])

    def test_write_json_unserialisable_is_false(self):
        path = os.path.join(self.tmp, "a.json")
        self.assertFalse(gk_common.write_json(path, {"x": object()}))
        self.assertFalse(os.path.exists(path))
        self.assertEqual(list(Path(self.tmp).glob("*.tmp")), [])


class LockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix=TMP_PREFIX)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.lockdir = os.path.join(self.tmp, "state")
        os.makedirs(self.lockdir)
        self.marker = os.path.join(self.tmp, "released")

    def start_holder(self, hold):
        p = subprocess.Popen(
            [sys.executable, "-c", LOCK_CHILD, str(HOOKS), self.lockdir, str(hold), self.marker],
            stdout=subprocess.PIPE, text=True,
        )
        self.addCleanup(p.wait)
        self.addCleanup(p.kill)
        self.addCleanup(p.stdout.close)
        self.assertEqual(p.stdout.readline().strip(), "locked")
        return p

    def test_locked_excludes(self):
        p = self.start_holder(1.0)
        t0 = time.monotonic()
        with gk_common.locked(self.lockdir):
            waited = time.monotonic() - t0
            # The child writes the marker while it still holds the lock, so seeing
            # it here proves we only got in after the child let go.
            self.assertTrue(os.path.exists(self.marker))
        self.assertGreater(waited, 0.5)
        self.assertEqual(p.wait(timeout=10), 0)

    def test_locked_timeout(self):
        self.start_holder(3.0)
        t0 = time.monotonic()
        with self.assertRaises(gk_common.LockTimeout):
            with gk_common.locked(self.lockdir):
                self.fail("lock acquired while the child held it")
        elapsed = time.monotonic() - t0
        self.assertLessEqual(elapsed, 2.5)
        self.assertGreaterEqual(elapsed, 1.5)
        self.assertFalse(os.path.exists(self.marker))

    def test_locked_releases_on_error(self):
        with self.assertRaises(ValueError):
            with gk_common.locked(self.lockdir):
                raise ValueError("boom")
        t0 = time.monotonic()
        with gk_common.locked(self.lockdir):
            pass
        self.assertLess(time.monotonic() - t0, 1.0)

    @unittest.skipIf(os.name == "nt", "POSIX-only: today's hooks lock with fcntl.flock")
    def test_locked_excludes_todays_lock(self):
        # Old and new hooks run side by side during the changeover (Task 6).
        p = subprocess.Popen([sys.executable, "-c", OLD_LOCK_HOLDER, self.lockdir, "3"],
                             stdout=subprocess.PIPE, text=True)
        self.addCleanup(p.wait)
        self.addCleanup(p.kill)
        self.addCleanup(p.stdout.close)
        self.assertEqual(p.stdout.readline().strip(), "locked")
        with self.assertRaises(gk_common.LockTimeout):
            with gk_common.locked(self.lockdir):
                self.fail("new locked() acquired while today's lock was held")

    @unittest.skipIf(os.name == "nt", "POSIX-only: today's hooks lock with fcntl.flock")
    def test_todays_lock_excluded_by_locked(self):
        def old_try():
            return subprocess.run([sys.executable, "-c", OLD_LOCK_TRY, self.lockdir],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, timeout=30)

        with gk_common.locked(self.lockdir):
            r = old_try()
            self.assertEqual((r.returncode, r.stdout.strip()), (0, "TIMEOUT"), r.stderr)
        # Control: once released, the same old-style lock is granted.
        r = old_try()
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "GOT"), r.stderr)


class RelPosixTests(unittest.TestCase):
    def test_rel_posix(self):
        root = os.path.abspath(os.path.join(os.sep, "Users", "Carl Selby", "repo"))
        inside = os.path.join(root, "my src", "a.py")
        self.assertEqual(gk_common.rel_posix(inside, root), "my src/a.py")
        self.assertEqual(gk_common.rel_posix(Path(inside), Path(root)), "my src/a.py")
        outside = os.path.abspath(os.path.join(os.sep, "Users", "Carl Selby", "elsewhere", "a.py"))
        self.assertIsNone(gk_common.rel_posix(outside, root))
        self.assertIsNone(gk_common.rel_posix(os.path.join(root + "x", "a.py"), root))
        self.assertIsNone(gk_common.rel_posix(root, root))

    def test_rel_posix_windows_separator(self):
        with mock.patch.object(gk_common, "_path", ntpath):
            root = r"C:\Users\Carl Selby\repo"
            self.assertEqual(gk_common.rel_posix(root + r"\my src\a.py", root), "my src/a.py")
            self.assertEqual(gk_common.rel_posix(r"C:\users\carl selby\Repo\src\a.py", r"c:\Users\Carl Selby\repo"),
                             "src/a.py")
            self.assertIsNone(gk_common.rel_posix(r"C:\Users\Carl Selby\other\a.py", root))
            self.assertIsNone(gk_common.rel_posix(r"D:\Users\Carl Selby\repo\a.py", root))


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.top = tempfile.mkdtemp(prefix=TMP_PREFIX)
        self.addCleanup(shutil.rmtree, self.top, True)

    def put(self, text):
        d = os.path.join(self.top, ".claude")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "gk-project.json"), "w", encoding="utf-8") as f:
            f.write(text)

    def test_load_project_states(self):
        self.assertEqual(gk_common.load_project(self.top), (None, None))

        self.put(json.dumps({"version": 1, "guards": ["dev only"]}))
        self.assertEqual(gk_common.load_project(Path(self.top)),
                         ({"version": 1, "guards": ["dev only"]}, None))

        for bad in (json.dumps({"version": 2}), json.dumps({"version": True}),
                    json.dumps({"guards": []}), "[1, 2]", "{broken",
                    "[" * 100000 + "]" * 100000):  # nesting past the recursion limit
            self.put(bad)
            data, reason = gk_common.load_project(self.top)
            self.assertIsNone(data, bad[:40])
            self.assertIsInstance(reason, str, bad[:40])
            self.assertTrue(reason, bad[:40])

    def project_path(self):
        d = os.path.join(self.top, ".claude")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "gk-project.json")

    def assert_refused_promptly(self, ending):
        t = time.monotonic()
        data, reason = gk_common.load_project(self.top)
        self.assertLess(time.monotonic() - t, 1.0)
        self.assertIsNone(data)
        self.assertIsInstance(reason, str)
        self.assertTrue(reason.endswith(ending), reason)

    @unittest.skipUnless(os.path.exists("/dev/zero"), "needs /dev/zero")
    def test_load_project_link_to_dev_zero_refused(self):
        try:
            os.symlink("/dev/zero", self.project_path())
        except (OSError, NotImplementedError) as e:
            self.skipTest("cannot make a symlink here: %s" % e)
        self.assert_refused_promptly("is not a regular file")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX only")
    def test_load_project_fifo_refused_without_blocking(self):
        os.mkfifo(self.project_path())
        self.assert_refused_promptly("is not a regular file")

    def test_load_project_folder_refused(self):
        os.makedirs(self.project_path())
        self.assert_refused_promptly("is not a regular file")

    def test_load_project_over_256_kb_refused(self):
        body = json.dumps({"version": 1, "pad": "x" * (300 * 1024)})
        self.put(body)
        self.assert_refused_promptly("is larger than 256 KB")
        # exactly at the cap is still read
        self.put(json.dumps({"version": 1, "pad": ""}))
        pad = gk_common.PROJECT_MAX - len(json.dumps({"version": 1, "pad": ""}).encode())
        self.put(json.dumps({"version": 1, "pad": "x" * pad}))
        self.assertEqual(os.path.getsize(self.project_path()), gk_common.PROJECT_MAX)
        data, reason = gk_common.load_project(self.top)
        self.assertIsNone(reason)
        self.assertEqual(data["version"], 1)


@unittest.skipIf(shutil.which("git") is None, "git not on PATH")
class GitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix=TMP_PREFIX))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.repo, "sub"))
        env = mock.patch.dict(os.environ, {"GIT_CEILING_DIRECTORIES": self.tmp})
        env.start()
        self.addCleanup(env.stop)

    def git(self, *args):
        subprocess.run(["git", "-C", self.repo, "-c", "user.name=t", "-c", "user.email=t@t",
                        "-c", "commit.gpgsign=false"] + list(args),
                       check=True, capture_output=True)

    def test_git_identity_unborn_then_branch(self):
        self.git("init", "-q")
        self.git("symbolic-ref", "HEAD", "refs/heads/trunk")
        sub = os.path.join(self.repo, "sub")
        self.assertIn(" ", self.repo)
        common, top, branch = gk_common.git_identity(sub)
        self.assertTrue(same_path(common, os.path.join(self.repo, ".git")), common)
        self.assertTrue(same_path(top, self.repo), top)
        self.assertEqual(branch, "(unborn)")
        self.git("commit", "-q", "--allow-empty", "-m", "x")
        common, top, branch = gk_common.git_identity(Path(sub))
        self.assertTrue(same_path(common, os.path.join(self.repo, ".git")), common)
        self.assertTrue(same_path(top, self.repo), top)
        self.assertEqual(branch, "trunk")
        top = gk_common.git_toplevel(sub)
        self.assertIsInstance(top, Path)
        self.assertTrue(same_path(top, self.repo), top)

    def test_git_outside_repo(self):
        self.assertIsNone(gk_common.git_identity(self.repo))
        self.assertIsNone(gk_common.git_toplevel(self.repo))


class HookIoTests(unittest.TestCase):
    def test_read_input(self):
        cases = [
            (b'{"session_id": "s1"}', {"session_id": "s1"}),
            (b"[1, 2]", None),
            (b"", None),
            (b"{broken", None),
            (b'{"a": "\xff"}', None),
        ]
        for raw, want in cases:
            with mock.patch("sys.stdin", stdin_bytes(raw)):
                self.assertEqual(gk_common.read_input(), want, raw)

    def test_emit(self):
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            gk_common.emit({"a": "\u00e9"})
        self.assertEqual(json.loads(out.getvalue()), {"a": "\u00e9"})

    def test_run_hook_always_exits_zero(self):
        seen = []

        def boom(data):
            seen.append(data)
            raise RuntimeError("kaput")

        err = io.StringIO()
        with mock.patch("sys.stdin", stdin_bytes(b'{"k": 1}')), mock.patch("sys.stderr", err):
            with self.assertRaises(SystemExit) as cm:
                gk_common.run_hook("gk-test", boom)
        self.assertEqual(cm.exception.code, 0)
        self.assertEqual(seen, [{"k": 1}])
        self.assertEqual(err.getvalue(), "gk-test: RuntimeError('kaput')\n")

    def test_run_hook_swallows_lock_timeout_and_exit(self):
        def timeout(data):
            raise gk_common.LockTimeout("busy")

        def bail(data):
            sys.exit(3)

        for fn, wrote in ((timeout, True), (bail, False)):
            err = io.StringIO()
            with mock.patch("sys.stdin", stdin_bytes(b"")), mock.patch("sys.stderr", err):
                with self.assertRaises(SystemExit) as cm:
                    gk_common.run_hook("gk-test", fn)
            self.assertEqual(cm.exception.code, 0)
            self.assertEqual(err.getvalue().startswith("gk-test: LockTimeout("), wrote)

    def test_run_hook_clean_run_exits_zero(self):
        with mock.patch("sys.stdin", stdin_bytes(b"{}")):
            with self.assertRaises(SystemExit) as cm:
                gk_common.run_hook("gk-test", lambda data: None)
        self.assertEqual(cm.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
