from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from gkh import paths  # noqa: E402

PS_PROFILE = r"C:\Users\Carl Selby\Documents\PowerShell\Microsoft.PowerShell_profile.ps1"


class FakeRunner:
    """Stands in for the PowerShell probe: answers per executable name, records each call."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, argv, timeout):
        self.calls.append((list(argv), timeout))
        return self.answers.get(argv[0])


class ClaudeDirTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "Carl Selby"
        self.home.mkdir()

    def env(self, **extra):
        env = {"HOME": str(self.home), "USERPROFILE": str(self.home)}
        env.update(extra)
        return mock.patch.dict(os.environ, env, clear=False)

    def test_default_is_home_dot_claude(self):
        with self.env(), mock.patch.dict(os.environ):
            os.environ.pop("GK_CLAUDE_DIR", None)
            self.assertEqual(paths.claude_dir(), self.home / ".claude")

    def test_empty_override_is_ignored(self):
        with self.env(GK_CLAUDE_DIR=""):
            self.assertEqual(paths.claude_dir(), self.home / ".claude")

    def test_override_wins(self):
        target = self.tmp / "elsewhere" / ".claude"
        with self.env(GK_CLAUDE_DIR=str(target)):
            self.assertEqual(paths.claude_dir(), target)

    def test_relative_override_is_made_absolute(self):
        # Hook commands carry this path, so it must not depend on the cwd a hook runs in.
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(str(self.tmp))
        here = os.getcwd()  # as the OS reports it (macOS gives /private/var for /var)
        with self.env(GK_CLAUDE_DIR="rel-claude"):
            got = paths.claude_dir()
        self.assertTrue(got.is_absolute())
        self.assertEqual(os.path.normcase(str(got)), os.path.normcase(os.path.join(here, "rel-claude")))

    def test_environ_argument_is_used(self):
        target = self.tmp / "given"
        self.assertEqual(paths.claude_dir({"GK_CLAUDE_DIR": str(target)}), target)

    def test_derived_locations(self):
        with self.env(GK_CLAUDE_DIR=str(self.tmp / "c")):
            c = self.tmp / "c"
            self.assertEqual(paths.hooks_target(), c / "hooks" / "gk-harness")
            self.assertEqual(paths.backup_root(), c / "backups" / "gk-harness")
            self.assertEqual(paths.manifest_path(), c / "backups" / "gk-harness" / "manifest.json")
            self.assertEqual(paths.harness_config_path(), c / "gk-harness.json")

    def test_harness_root_is_this_repo(self):
        root = paths.harness_root()
        self.assertEqual(os.path.normcase(str(root)), os.path.normcase(str(Path(ROOT).resolve())))
        self.assertTrue((root / "gkh" / "paths.py").is_file())
        self.assertTrue((root / "hooks").is_dir())


class PosixTests(unittest.TestCase):
    def test_windows_path_gets_forward_slashes(self):
        p = PureWindowsPath(r"C:\Users\Carl Selby\.claude")
        self.assertEqual(paths.posix(p), "C:/Users/Carl Selby/.claude")

    def test_native_path_and_str(self):
        p = Path(tempfile.gettempdir()) / "a b" / "c"
        self.assertEqual(paths.posix(p), p.as_posix())
        self.assertEqual(paths.posix(str(p)), p.as_posix())


class ClockTests(unittest.TestCase):
    def test_stamp_and_iso_formats(self):
        t = 1793181296  # 2026-10-28T09:54:56Z
        self.assertEqual(paths.utc_stamp(t), "20261028T095456Z")
        self.assertEqual(paths.utc_iso(t), "2026-10-28T09:54:56Z")
        self.assertRegex(paths.utc_stamp(), r"^\d{8}T\d{6}Z$")
        self.assertRegex(paths.utc_iso(), r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_new_backup_dir_is_unused_and_not_created(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        t = 1793181296
        with mock.patch.dict(os.environ, {"GK_CLAUDE_DIR": str(tmp)}):
            first = paths.new_backup_dir(t)
            self.assertEqual(first, tmp / "backups" / "gk-harness" / "20261028T095456Z")
            self.assertFalse(first.exists())
            first.mkdir(parents=True)
            second = paths.new_backup_dir(t)
            self.assertEqual(second.name, "20261028T095456Z-2")
            second.mkdir()
            self.assertEqual(paths.new_backup_dir(t).name, "20261028T095456Z-3")


class PythonExeTests(unittest.TestCase):
    def test_is_the_running_interpreter_absolute(self):
        exe = paths.python_exe()
        self.assertTrue(os.path.isabs(exe))
        self.assertEqual(os.path.normcase(exe), os.path.normcase(os.path.abspath(sys.executable)))

    def test_empty_executable_gives_none(self):
        with mock.patch.object(sys, "executable", ""):
            self.assertIsNone(paths.python_exe())


class RcFileTests(unittest.TestCase):
    home = Path("/home/carl")

    def test_macos_is_zshrc_whatever_the_shell(self):
        for shell in ("/bin/zsh", "/bin/bash", ""):
            got = paths.rc_file("darwin", {"SHELL": shell}, FakeRunner({}), self.home)
            self.assertEqual(got, self.home / ".zshrc")

    def test_linux_zsh_login_shell(self):
        for shell in ("/bin/zsh", "/usr/local/bin/zsh"):
            got = paths.rc_file("linux", {"SHELL": shell}, FakeRunner({}), self.home)
            self.assertEqual(got, self.home / ".zshrc")

    def test_linux_otherwise_bashrc(self):
        for env in ({"SHELL": "/bin/bash"}, {"SHELL": "/usr/bin/fish"}, {"SHELL": "/bin/zsh5"}, {}):
            got = paths.rc_file("linux", env, FakeRunner({}), self.home)
            self.assertEqual(got, self.home / ".bashrc", env)

    def test_windows_asks_pwsh_first(self):
        runner = FakeRunner({"pwsh": PS_PROFILE + "\r\n", "powershell": r"C:\other.ps1"})
        got = paths.rc_file("win32", {}, runner, self.home)
        self.assertEqual(got, Path(PS_PROFILE))
        self.assertEqual(runner.calls, [(["pwsh", "-NoProfile", "-Command", "$PROFILE"], 5)])

    def test_windows_falls_back_to_powershell(self):
        runner = FakeRunner({"pwsh": None, "powershell": PS_PROFILE + "\n"})
        got = paths.rc_file("win32", {}, runner, self.home)
        self.assertEqual(got, Path(PS_PROFILE))
        self.assertEqual([c[0][0] for c in runner.calls], ["pwsh", "powershell"])
        self.assertTrue(all(c[1] == 5 for c in runner.calls))

    def test_windows_unusable_answers_are_skipped(self):
        for bad in ("", "   \n", "relative\\profile.ps1", "C:\\a.ps1\nC:\\b.ps1"):
            runner = FakeRunner({"pwsh": bad, "powershell": None})
            self.assertIsNone(paths.rc_file("win32", {}, runner, self.home), repr(bad))

    def test_windows_neither_answers(self):
        self.assertIsNone(paths.rc_file("win32", {}, FakeRunner({}), self.home))

    def test_default_runner_survives_a_missing_executable(self):
        self.assertIsNone(paths.run_capture(["gk-harness-no-such-exe-0f3a"], 5))

    def test_default_runner_returns_stdout(self):
        out = paths.run_capture([sys.executable, "-c", "print('C:/x.ps1')"], 5)
        self.assertEqual(out.strip(), "C:/x.ps1")

    def test_default_runner_nonzero_exit_is_none(self):
        self.assertIsNone(paths.run_capture([sys.executable, "-c", "print('x'); raise SystemExit(3)"], 5))

    def test_default_runner_times_out(self):
        self.assertIsNone(paths.run_capture([sys.executable, "-c", "import time; time.sleep(5)"], 0.5))


if __name__ == "__main__":
    unittest.main()
