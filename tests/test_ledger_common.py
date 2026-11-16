from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "hooks"))

import gk_common  # noqa: E402

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_NOSYSTEM": "1",
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


class LedgerCommonTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = os.path.realpath(self._tmp.name)
        self.claude = os.path.join(self.tmp, "claude")
        os.makedirs(self.claude)
        env = mock.patch.dict(os.environ, dict(GIT_ENV, GK_CLAUDE_DIR=self.claude))
        env.start()
        self.addCleanup(env.stop)

    def repo(self, name):
        p = os.path.join(self.tmp, name)
        os.makedirs(p)
        git(p, "init", "-q", "-b", "main")
        git(p, "commit", "-q", "--allow-empty", "-m", "init")
        return p

    def test_project_from_project_file(self):
        r = self.repo("beta")
        os.makedirs(os.path.join(r, ".claude"))
        with open(os.path.join(r, ".claude", "gk-project.json"), "w") as f:
            json.dump({"version": 1, "name": "Goodricke"}, f)
        ident = gk_common.project_identity(r)
        self.assertEqual(ident["project"], "Goodricke")
        self.assertEqual(ident["root"], r)
        self.assertEqual(ident["branch"], "main")

    def test_worktree_takes_main_repo_name(self):
        r = self.repo("alpha")
        wt = os.path.join(self.tmp, "wt-somewhere")
        git(r, "worktree", "add", "-q", "-b", "feat/x", wt)
        ident = gk_common.project_identity(wt)
        self.assertEqual(ident["project"], "alpha")
        self.assertEqual(ident["branch"], "feat/x")
        self.assertEqual(ident["root"], os.path.realpath(wt))

    def test_leading_dot_dropped(self):
        r = self.repo(".gk-harness")
        self.assertEqual(gk_common.project_identity(r)["project"], "gk-harness")

    def test_no_repo(self):
        d = os.path.join(self.tmp, "plain")
        os.makedirs(d)
        ident = gk_common.project_identity(d)
        self.assertEqual(ident["project"], gk_common.NO_REPO)
        self.assertEqual(ident["root"], d)
        self.assertEqual(ident["cwd"], d)
        self.assertIsNone(ident["branch"])

    def test_detached_head_branch_none(self):
        r = self.repo("det")
        git(r, "checkout", "-q", "--detach")
        self.assertIsNone(gk_common.project_identity(r)["branch"])

    def test_session_map_written_and_unchanged_skip(self):
        r = self.repo("gamma")
        self.assertTrue(gk_common.write_session_map("s1", r))
        p = gk_common.session_map_path("s1")
        self.assertEqual(p, gk_common.ledger_dir() / "sessions" / "s1.json")
        data = json.loads(p.read_text())
        self.assertEqual(data["session"], "s1")
        self.assertEqual(data["project"], "gamma")
        self.assertEqual(data["branch"], "main")
        self.assertIn("updated", data)
        before = os.stat(p).st_mtime_ns
        gk_common.write_session_map("s1", r)
        self.assertEqual(os.stat(p).st_mtime_ns, before)

    def test_session_map_follows_branch_switch(self):
        r = self.repo("delta")
        gk_common.write_session_map("s2", r)
        git(r, "checkout", "-q", "-b", "other")
        gk_common.write_session_map("s2", r)
        data = json.loads(gk_common.session_map_path("s2").read_text())
        self.assertEqual(data["branch"], "other")

    @unittest.skipIf(os.name == "nt", "POSIX modes")
    def test_session_map_folders_and_file_are_private(self):
        r = self.repo("eps")
        old = os.umask(0o022)
        try:
            self.assertTrue(gk_common.write_session_map("s3", r))
        finally:
            os.umask(old)
        ldir = gk_common.ledger_dir()
        self.assertEqual(stat.S_IMODE(os.stat(ldir).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(ldir / "sessions").st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(gk_common.session_map_path("s3")).st_mode), 0o600)
        # A rewrite (the branch moved) keeps the file private.
        git(r, "checkout", "-q", "-b", "moved")
        self.assertTrue(gk_common.write_session_map("s3", r))
        self.assertEqual(stat.S_IMODE(os.stat(gk_common.session_map_path("s3")).st_mode), 0o600)

    def test_session_map_unsafe_sid(self):
        self.assertFalse(gk_common.write_session_map("../evil", self.tmp))

    def test_ledger_on_false_on_windows(self):
        f = gk_common.ledger_on_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{}")
        self.assertTrue(gk_common.ledger_on())
        with mock.patch.object(gk_common.os, "name", "nt"):
            self.assertFalse(gk_common.ledger_on())

    def test_ledger_port_env_override_and_bad_value(self):
        self.assertEqual(gk_common.LEDGER_PORT, 4318)
        with mock.patch.dict(os.environ, {"GK_LEDGER_PORT": "5555"}):
            self.assertEqual(gk_common.ledger_port(), 5555)
        for bad in ("abc", "70000", "0", ""):
            with mock.patch.dict(os.environ, {"GK_LEDGER_PORT": bad}):
                self.assertEqual(gk_common.ledger_port(), 4318)


if __name__ == "__main__":
    unittest.main()
