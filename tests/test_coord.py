"""Coordination hooks: gk-peers, gk-claim, gk-claims-end.

Ports every case of the old ~/.claude/hooks/gk-coord.test.sh. The hooks run as
subprocesses with GK_CLAUDE_DIR set to a temp folder, never the real ~/.claude.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS = os.path.join(ROOT, "hooks")
sys.path.insert(0, HOOKS)

import gk_common  # noqa: E402

PEERS = "gk-peers.py"
CLAIM = "gk-claim.py"
END = "gk-claims-end.py"

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_NOSYSTEM": "1",  # GIT_CONFIG_GLOBAL is added by setUpModule
}
_GLOBAL_DIR = None

LIVE = os.getpid()
DEAD = None  # a finished child's pid, set by setUpModule
# The finished child's Popen, kept until tearDownModule: on Windows its open process handle
# stops the pid from being handed to a later process (which would make DEAD read as alive).
_DEAD_CHILD = None

POLICY = ("Policy: message a peer (SendMessage) before editing a file it has claimed, when a "
          "hook warns of an overlap, and before merging to the shared branch.")
POLICY_DEVELOP = ("Policy: message a peer (SendMessage) before editing a file it has claimed, "
                  "when a hook warns of an overlap, and before merging to the shared branch "
                  "(develop).")
PROJECT = {"version": 1, "sharedBranch": "develop"}


def setUpModule():
    global _GLOBAL_DIR, DEAD, _DEAD_CHILD
    _GLOBAL_DIR = tempfile.TemporaryDirectory()
    empty = os.path.join(_GLOBAL_DIR.name, "gitconfig")
    open(empty, "w").close()
    GIT_ENV["GIT_CONFIG_GLOBAL"] = empty
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    _DEAD_CHILD = child
    DEAD = child.pid
    if gk_common.pid_alive(DEAD):
        raise unittest.SkipTest("the finished child's pid %d was reused" % DEAD)


def tearDownModule():
    global _DEAD_CHILD
    _DEAD_CHILD = None
    GIT_ENV.pop("GIT_CONFIG_GLOBAL", None)
    if _GLOBAL_DIR is not None:
        _GLOBAL_DIR.cleanup()


def git(cwd, *args):
    env = dict(os.environ, **GIT_ENV)
    r = subprocess.run(["git", "-C", str(cwd)] + list(args), check=True, capture_output=True,
                       stdin=subprocess.DEVNULL, env=env, encoding="utf-8")
    return r.stdout


def make_repo(path):
    git(os.path.dirname(str(path)), "init", "-q", "-b", "main", str(path))
    Path(path, "a.txt").write_text("a\n")
    git(path, "add", "a.txt")
    git(path, "commit", "-q", "-m", "init")


def common_dir(repo):
    # Built exactly as gk_common.git_identity builds it (realpath, no normcase), so peer
    # claims written by the fixture carry the same spelling the hook stores on every OS.
    return os.path.realpath(os.path.join(str(repo), git(repo, "rev-parse", "--git-common-dir").strip()))


def write_project(repo, obj):
    d = Path(repo, ".claude")
    d.mkdir(exist_ok=True)
    (d / "gk-project.json").write_text(json.dumps(obj))


class CoordBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(os.path.realpath(self._tmp.name))
        self.g = self.tmp / "g"
        self.g.mkdir()

    # ---- running a hook
    def run_hook(self, hook, payload=None, raw=None, gdir=None):
        env = dict(os.environ, GK_CLAUDE_DIR=str(gdir if gdir is not None else self.g),
                   GK_NO_LAUNCH_ARGS="1", **GIT_ENV)
        if raw is None:
            raw = json.dumps(payload)
        data = raw.encode("utf-8") if isinstance(raw, str) else raw
        p = subprocess.run([sys.executable, os.path.join(HOOKS, hook)], input=data,
                           capture_output=True, env=env, timeout=60)
        self.last_err = p.stderr.decode("utf-8", "replace")
        return p.returncode, p.stdout.decode("utf-8")

    def ok(self, hook, payload=None, raw=None):
        rc, out = self.run_hook(hook, payload, raw)
        self.assertEqual(rc, 0)
        return out

    @staticmethod
    def ctx(out):
        return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out.strip() else ""

    @staticmethod
    def evn(out):
        return json.loads(out)["hookSpecificOutput"]["hookEventName"] if out.strip() else ""

    # ---- fixtures
    def reg(self, sid, name, status, pid, started=1000, fname=None):
        d = self.g / "sessions"
        d.mkdir(exist_ok=True)
        (d / ("%s.json" % (fname or sid))).write_text(json.dumps(
            {"pid": pid, "sessionId": sid, "cwd": "/x", "name": name, "status": status,
             "startedAt": started, "updatedAt": 1}))

    def claims(self, sid, common, branch, at, *paths):
        d = self.g / "state" / "gk-claims"
        d.mkdir(parents=True, exist_ok=True)
        (d / ("%s.json" % sid)).write_text(json.dumps(
            {"session_id": sid, "updated_at": at,
             "claims": [{"common": common, "root": "/r", "branch": branch, "path": p, "at": at}
                        for p in paths]}))

    def state(self, sub, sid):
        return self.g / "state" / sub / ("%s.json" % sid)

    def touch_state(self, sub, sid):
        p = self.state(sub, sid)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}")

    @staticmethod
    def start(sid, source="startup", cwd="/x"):
        return {"session_id": sid, "cwd": cwd, "hook_event_name": "SessionStart", "source": source}

    @staticmethod
    def ups(sid):
        return {"session_id": sid, "cwd": "/x", "hook_event_name": "UserPromptSubmit", "prompt": "hi"}

    @staticmethod
    def post(sid, cwd, tool, key, path):
        return {"session_id": sid, "cwd": str(cwd), "hook_event_name": "PostToolUse",
                "tool_name": tool, "tool_input": {key: str(path)}}


# =================== hook 1: gk-peers ===================

class PeersTests(CoordBase):
    def setUp(self):
        super().setUp()
        self.reg("own-1", "OWN SEAT", "busy", LIVE)
        self.reg("peer-live", "PEER ONE", "idle", LIVE, 2000)
        self.reg("peer-dead", "GHOST", "busy", DEAD, 3000)
        self.claims("peer-live", "/c", "feat/peer", "2026-10-14T07:00:00Z", "a.txt", "b.txt")
        self.claims("own-1", "/c", "main", "2026-10-14T07:00:00Z", "ownfile.txt")
        for sub in ("gk-claims", "gk-peers", "gk-remote-pending", "gk-seat"):
            self.touch_state(sub, "peer-dead")
        self.touch_state("gk-remote-pending", "own-1")
        self.touch_state("gk-seat", "own-1")

    def test_start_briefing(self):
        out = self.ok(PEERS, self.start("own-1"))
        self.assertEqual(self.evn(out), "SessionStart")
        c = self.ctx(out)
        self.assertIn("PEER ONE", c)
        self.assertIn("1 other session on this machine", c)
        self.assertNotIn("on this box", c)
        self.assertIn("feat/peer", c)
        self.assertIn("2 files: a.txt, b.txt", c)
        self.assertIn("Policy: message a peer (SendMessage)", c)
        self.assertNotIn("OWN SEAT", c)
        self.assertNotIn("ownfile.txt", c)
        self.assertNotIn("GHOST", c)
        self.assertNotIn("not listed", c)

    def test_start_prunes_dead_state_only(self):
        self.ok(PEERS, self.start("own-1"))
        for sub in ("gk-claims", "gk-peers", "gk-remote-pending", "gk-seat"):
            self.assertFalse(self.state(sub, "peer-dead").exists(), sub)
        self.assertTrue(self.state("gk-claims", "peer-live").exists())
        self.assertTrue(self.state("gk-remote-pending", "own-1").exists())
        self.assertTrue(self.state("gk-claims", "own-1").exists())
        self.assertTrue(self.state("gk-seat", "own-1").exists())
        self.assertTrue(self.state("gk-peers", "own-1").exists(), "snapshot stored")
        self.assertTrue((self.g / "sessions" / "peer-dead.json").exists(), "registry untouched")

    def test_ups_sequence(self):
        self.ok(PEERS, self.start("own-1"))
        self.assertEqual(self.ok(PEERS, self.ups("own-1")), "", "unchanged is silent")

        self.reg("peer-live", "PEER ONE", "busy", LIVE, 2000)
        self.claims("peer-live", "/c", "feat/peer", "2026-10-14T07:05:00Z", "a.txt", "b.txt", "c.txt")
        self.assertEqual(self.ok(PEERS, self.ups("own-1")), "", "status + claim count change is silent")

        self.reg("peer-new", "NEWBIE", "idle", LIVE, 4000)
        out = self.ok(PEERS, self.ups("own-1"))
        c = self.ctx(out)
        self.assertEqual(self.evn(out), "UserPromptSubmit")
        self.assertIn("Peer sessions changed:", c)
        self.assertIn("NEWBIE", c)
        self.assertNotIn("PEER ONE", c, "lists only changes")
        self.assertNotIn("Policy:", c)
        self.assertEqual(self.ok(PEERS, self.ups("own-1")), "", "silent after the change was reported")

        self.claims("peer-live", "/c", "feat/other", "2026-10-14T07:06:00Z", "a.txt")
        c = self.ctx(self.ok(PEERS, self.ups("own-1")))
        self.assertIn("PEER ONE", c)
        self.assertIn("feat/other", c)
        self.assertIn("Branch moved", c)
        self.assertNotIn("NEWBIE", c)

        self.reg("peer-live", "RENAMED ONE", "busy", LIVE, 2000)
        self.assertIn("RENAMED ONE", self.ctx(self.ok(PEERS, self.ups("own-1"))))

        (self.g / "sessions" / "peer-new.json").unlink()
        self.assertIn("Gone: NEWBIE", self.ctx(self.ok(PEERS, self.ups("own-1"))))

        self.reg("peer-new", "NEWBIE", "idle", LIVE, 4000)
        c = self.ctx(self.ok(PEERS, self.start("own-1", "resume")))
        self.assertIn("2 other sessions on this machine", c, "start re-emits on resume")

    def test_start_caps_files_at_15(self):
        self.claims("peer-live", "/c", "feat/other", "x", *["f%02d.txt" % i for i in range(17)])
        c = self.ctx(self.ok(PEERS, self.start("own-1", "compact")))
        self.assertIn("f14.txt, +2 more", c)
        self.assertNotIn("f15.txt", c)


class PeersNoPeerTests(CoordBase):
    def test_no_peers_then_late_peers(self):
        self.reg("own-1", "OWN SEAT", "busy", LIVE)
        self.assertEqual(self.ctx(self.ok(PEERS, self.start("own-1"))),
                         "Live peer sessions (gk-peers): none.")
        self.assertEqual(self.ok(PEERS, self.ups("own-1")), "", "ups with no peers is silent")
        self.reg("peer-live", "LATE PEER", "idle", LIVE, 2000)
        self.assertIn("Peer sessions changed:", self.ctx(self.ok(PEERS, self.ups("own-1"))))
        out = self.ok(PEERS, {"session_id": "nosnap", "hook_event_name": "UserPromptSubmit"})
        self.assertIn("LATE PEER", self.ctx(out), "no prior snapshot: peers are new")
        self.reg("peer-live2", "LATE PEER 2", "idle", LIVE, 2100)
        self.claims("peer-live2", "/c", "main", "2026-10-14T07:00:00Z", "z.txt")
        c = self.ctx(self.ok(PEERS, self.start("own-1")))
        self.assertIn("LATE PEER", c)
        self.assertIn("no edits yet", c)

    def test_duplicate_session_id_live_entry_wins(self):
        # a resumed session: a stale <pid>.json lingers with the same sessionId
        self.reg("own-1", "OWN SEAT", "busy", LIVE)
        self.reg("peer-x", "STALE NAME", "busy", DEAD, 100, fname="999995")
        self.reg("peer-x", "CURRENT NAME", "idle", LIVE, 200, fname=str(LIVE))
        c = self.ctx(self.ok(PEERS, self.start("own-1")))
        self.assertIn("CURRENT NAME", c)
        self.assertIn("idle", c)
        self.assertNotIn("STALE NAME", c)
        self.assertNotIn("busy", c)
        self.assertIn("1 other session on this machine", c)

    def test_served_session_id_gets_no_snapshot(self):
        self.reg("peer-live", "PEER ONE", "idle", LIVE, 2000)
        c = self.ctx(self.ok(PEERS, self.start("served:abc")))
        self.assertIn("PEER ONE", c)
        self.assertFalse((self.g / "state" / "gk-peers").exists())


class PeersCapTests(CoordBase):
    def setUp(self):
        super().setUp()
        self.reg("own-1", "OWN SEAT", "busy", LIVE)

    def test_briefing_capped_with_policy_last(self):
        deep = "deeply/nested/folder/" * 5
        for i in range(40):
            sid = "peer-%02d" % i
            self.reg(sid, "PEER %02d" % i, "busy", LIVE, 2000 + i)
            self.claims(sid, "/c", "feat/branch-%02d" % i, "2026-10-14T07:00:00Z",
                        *["%sfile-%02d-%02d.txt" % (deep, i, j) for j in range(20)])
        c = self.ctx(self.ok(PEERS, self.start("own-1")))
        self.assertLessEqual(len(c), 8000)
        lines = c.splitlines()
        self.assertIn("40 other sessions on this machine.", lines[0])
        self.assertEqual(lines[-1], POLICY)
        shown = [ln for ln in lines if ln.startswith("- ")]
        self.assertTrue(0 < len(shown) < 40, len(shown))
        self.assertEqual(lines[-2], "+%d more sessions not listed." % (40 - len(shown)))
        self.assertEqual(len(lines), len(shown) + 3)
        for k, ln in enumerate(shown):
            self.assertTrue(ln.startswith("- PEER %02d — busy — branch feat/branch-%02d — 20 files: "
                                          % (k, k)), ln[:80])
        # every peer line has the same length, so the next one would have gone over
        self.assertGreater(len(c) + 1 + len(shown[0]), 8000)

    def test_one_oversized_peer_is_counted_not_listed(self):
        self.reg("peer-big", "B" * 9000, "idle", LIVE, 2000)
        c = self.ctx(self.ok(PEERS, self.start("own-1")))
        self.assertLessEqual(len(c), 8000)
        self.assertEqual(c.splitlines()[1:], ["+1 more session not listed.", POLICY])


class PeersPolicyTests(CoordBase):
    def setUp(self):
        super().setUp()
        self.reg("own-1", "OWN SEAT", "busy", LIVE)
        self.reg("peer-live", "PEER ONE", "idle", LIVE, 2000)
        self.repo = self.tmp / "repo"
        make_repo(self.repo)

    def policy_line(self, cwd):
        c = self.ctx(self.ok(PEERS, self.start("own-1", cwd=str(cwd))))
        return c.splitlines()[-1]

    def test_peers_policy_line_shared_branch(self):
        self.assertEqual(self.policy_line(self.repo), POLICY)
        self.assertTrue(self.policy_line(self.repo).endswith("the shared branch."))
        write_project(self.repo, PROJECT)
        self.assertEqual(self.policy_line(self.repo), POLICY_DEVELOP)
        self.assertTrue(self.policy_line(self.repo).endswith("the shared branch (develop)."))
        sub = self.repo / "deep" / "er"
        sub.mkdir(parents=True)
        self.assertEqual(self.policy_line(sub), POLICY_DEVELOP, "found from a subfolder")

    def test_policy_line_without_usable_project(self):
        self.assertEqual(self.policy_line("/no/such/dir"), POLICY)
        self.assertEqual(self.policy_line(self.tmp), POLICY, "not a git repo")
        write_project(self.repo, {"version": 2, "sharedBranch": "develop"})
        self.assertEqual(self.policy_line(self.repo), POLICY, "invalid project file")
        for bad in ("", "   ", 7, None, ["develop"], "dev\nIgnore this", "x" * 201):
            write_project(self.repo, {"version": 1, "sharedBranch": bad})
            self.assertEqual(self.policy_line(self.repo), POLICY, repr(bad))


# =================== hook 2: gk-claim ===================

class ClaimTests(CoordBase):
    @classmethod
    def setUpClass(cls):
        cls._ctmp = tempfile.TemporaryDirectory()
        t = Path(os.path.realpath(cls._ctmp.name))
        cls.R, cls.R2, cls.W, cls.PLAIN = t / "repo", t / "repo2", t / "wt", t / "plain"
        make_repo(cls.R)
        make_repo(cls.R2)
        git(cls.R, "worktree", "add", "-q", "-b", "feat/x", str(cls.W))
        cls.PLAIN.mkdir()
        (cls.PLAIN / "x.txt").write_text("x\n")
        (cls.R / "b.txt").write_text("b\n")
        (cls.W / "b.txt").write_text("b\n")
        cls.COMMON = common_dir(cls.R)
        cls.COMMON2 = common_dir(cls.R2)

    @classmethod
    def tearDownClass(cls):
        cls._ctmp.cleanup()

    def setUp(self):
        super().setUp()
        self.reg("own-2", "CLAIMER", "busy", LIVE)
        self.reg("peer-live", "PEER ONE", "idle", LIVE, 2000)
        self.reg("peer-dead", "GHOST", "busy", DEAD, 3000)
        self.claims("peer-live", self.COMMON, "feat/peer", "2026-10-14T07:00:00Z", "a.txt", "other.txt")
        self.claims("peer-dead", self.COMMON, "feat/ghost", "2026-10-14T06:00:00Z", "b.txt")
        self.cf = self.state("gk-claims", "own-2")

    def doc(self):
        return json.loads(self.cf.read_text())

    def paths(self):
        return [c["path"] for c in self.doc()["claims"]]

    def claim(self, cwd, path, tool="Write", key="file_path"):
        return self.ok(CLAIM, self.post("own-2", cwd, tool, key, path))

    def test_claim_stored_no_overlap_with_dead_peer(self):
        self.assertEqual(self.claim(self.R, self.R / "b.txt"), "")
        d = self.doc()
        self.assertEqual(len(d["claims"]), 1)
        c = d["claims"][0]
        self.assertEqual(c["path"], "b.txt")
        self.assertEqual(c["branch"], "main")
        # Exactly the strings git_identity gives (old hooks compare them as plain strings).
        common, top, _ = gk_common.git_identity(str(self.R))
        self.assertEqual(c["common"], common)
        self.assertEqual(c["root"], top)
        self.assertEqual(d["session_id"], "own-2")

    def test_claim_dedup_same_file_and_across_worktrees(self):
        self.claim(self.R, self.R / "b.txt")
        self.claim(self.R, self.R / "b.txt", tool="Edit")
        self.assertEqual(len(self.doc()["claims"]), 1)
        self.claim(self.W, self.W / "b.txt", tool="Edit")
        d = self.doc()
        self.assertEqual(len(d["claims"]), 1)
        self.assertEqual(d["claims"][0]["branch"], "feat/x")
        self.assertEqual(d["claims"][0]["root"], gk_common.git_identity(str(self.W))[1])
        self.assertEqual(d["claims"][0]["common"], gk_common.git_identity(str(self.R))[0])

    def test_overlap_across_worktrees_warns(self):
        self.claim(self.R, self.R / "b.txt")
        out = self.claim(self.W, self.W / "a.txt", tool="Edit")
        self.assertEqual(self.evn(out), "PostToolUse")
        c = self.ctx(out)
        self.assertIn('Overlap (gk-claim): a.txt was edited by session "PEER ONE" on branch '
                      'feat/peer at 2026-10-14T07:00:00Z.', c)
        self.assertIn("Message that session (SendMessage) in this turn", c)
        self.assertTrue(c.endswith("agree who merges to the shared branch first."), c)
        self.assertNotIn("develop", c)
        self.assertEqual(len(c.splitlines()), 1)
        self.assertEqual(len(self.doc()["claims"]), 2, "overlapping claim still recorded")

    def test_overlap_names_project_shared_branch(self):
        write_project(self.R, PROJECT)
        try:
            c = self.ctx(self.claim(self.R, self.R / "a.txt", tool="Edit"))
        finally:
            (self.R / ".claude" / "gk-project.json").unlink()
        self.assertTrue(c.endswith("agree who merges to the shared branch (develop) first."), c)

    def test_write_failure_reported_and_overlap_still_warned(self):
        # The own claims file's path is a folder, so storing the claim fails, while the
        # live peer's claims beside it stay readable.
        self.cf.mkdir(parents=True)
        out = self.claim(self.R, self.R / "a.txt", tool="Edit")
        self.assertIn("gk-claim: cannot store claims", self.last_err)
        self.assertIn('Overlap (gk-claim): a.txt was edited by session "PEER ONE" on branch '
                      'feat/peer at 2026-10-14T07:00:00Z.', self.ctx(out))
        self.assertTrue(self.cf.is_dir())
        self.assertEqual(list(self.cf.parent.glob("*.tmp")), [], "no temp file left behind")

    def test_same_relpath_in_different_repo_does_not_warn(self):
        self.claim(self.R, self.R / "b.txt")
        self.assertEqual(self.claim(self.R2, self.R2 / "a.txt"), "")
        d = self.doc()
        self.assertEqual(len(d["claims"]), 2)
        self.assertEqual(d["claims"][1]["common"], gk_common.git_identity(str(self.R2))[0])

    def test_non_git_paths_silent_and_unclaimed(self):
        self.assertEqual(self.claim(self.PLAIN, self.PLAIN / "x.txt"), "")
        self.assertEqual(self.claim(self.PLAIN, self.PLAIN / "not-yet-created" / "new.txt"), "")
        self.assertFalse(self.cf.exists())

    def test_skipped_segments_ignored(self):
        for rel in ("node_modules/pkg/index.js", ".next/c.js", "backend/node_modules/y/z.js"):
            p = self.R / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x\n")
            self.claim(self.R, p)
        self.claim(self.R, self.R / ".git" / "config")
        self.assertFalse(self.cf.exists())

    def test_file_under_claude_dir_ignored(self):
        inner = self.g / "inner"
        inner.mkdir()
        git(self.g, "init", "-q", str(inner))
        (inner / "f.txt").write_text("x\n")
        self.claim(inner, inner / "f.txt")
        self.assertFalse(self.cf.exists())

    def test_relative_path_resolved_against_cwd(self):
        self.assertIn("Overlap (gk-claim): a.txt", self.ctx(self.claim(self.R, "a.txt", tool="Edit")))

    def test_notebook_and_nested_paths(self):
        (self.R / "nb.ipynb").write_text("{}")
        self.claim(self.R, self.R / "nb.ipynb", tool="NotebookEdit", key="notebook_path")
        (self.R / "newdir").mkdir(exist_ok=True)
        (self.R / "newdir" / "n.txt").write_text("n\n")
        self.claim(self.R, self.R / "newdir" / "n.txt")
        self.assertEqual(self.paths().count("nb.ipynb"), 1)
        self.assertEqual(self.paths().count("newdir/n.txt"), 1)

    def test_multiple_hits_one_line_each(self):
        self.reg("peer-live2", "PEER TWO", "busy", LIVE, 2500)
        self.claims("peer-live2", self.COMMON, "feat/two", "2026-10-14T07:10:00Z", "a.txt")
        c = self.ctx(self.claim(self.W, self.W / "a.txt", tool="Edit"))
        self.assertEqual(len(c.splitlines()), 2)
        self.assertIn("PEER ONE", c)
        self.assertIn("PEER TWO", c)
        self.assertNotIn("CLAIMER", c, "own claims never self-warn")

    def test_repo_without_commits_claimed(self):
        r3 = self.tmp / "repo3"
        git(self.tmp, "init", "-q", "-b", "main", str(r3))
        (r3 / "u.txt").write_text("u\n")
        self.claim(r3, r3 / "u.txt")
        self.assertEqual(self.paths().count("u.txt"), 1)
        self.assertEqual(self.doc()["claims"][0]["branch"], "(unborn)")

    def test_served_session_id_not_claimed(self):
        self.assertEqual(self.ok(CLAIM, self.post("served:abc", self.R, "Edit", "file_path",
                                                  self.R / "a.txt")), "")
        self.assertEqual(list((self.g / "state" / "gk-claims").glob("served*")), [])


# =================== hook 3: gk-claims-end ===================

class ClaimsEndTests(CoordBase):
    SUBS = ("gk-claims", "gk-peers", "gk-remote-pending", "gk-seat")

    def test_end_deletes_own_state_only(self):
        for sub in self.SUBS:
            self.touch_state(sub, "own-2")
            self.touch_state(sub, "peer-live")
        out = self.ok(END, {"session_id": "own-2", "hook_event_name": "SessionEnd", "reason": "other"})
        self.assertEqual(out, "")
        for sub in self.SUBS:
            self.assertFalse(self.state(sub, "own-2").exists(), sub)
            self.assertTrue(self.state(sub, "peer-live").exists(), sub)
        self.assertEqual(self.ok(END, {"session_id": "own-2", "hook_event_name": "SessionEnd"}), "")

    def test_unsafe_session_id(self):
        self.touch_state("gk-claims", "x")
        self.assertEqual(self.ok(END, {"session_id": "../../etc/x"}), "")
        self.assertEqual(self.ok(END, {"session_id": "../gk-claims/x"}), "")
        self.assertTrue(self.state("gk-claims", "x").exists())


# =================== every hook: garbage ===================

GARBAGE = [
    "", "garbage {{", "[1,2]", "null", '"str"', "{}", '{"session_id":5,"tool_input":"x"}',
    '{"session_id":"s","hook_event_name":"PostToolUse","tool_input":{"file_path":123}}',
    '{"session_id":"s","hook_event_name":"SessionStart","tool_input":null,"source":7,"cwd":9}x',
]


class GarbageTests(CoordBase):
    def test_all_hooks_exit_0_and_stay_silent_on_garbage(self):
        self.reg("own-3", "G", "busy", LIVE)
        for hook in (PEERS, CLAIM, END):
            for raw in GARBAGE:
                with self.subTest(hook=hook, raw=raw):
                    self.assertEqual(self.run_hook(hook, raw=raw), (0, ""))
        for hook in (PEERS, CLAIM, END):
            with self.subTest(hook=hook, raw="invalid utf-8"):
                self.assertEqual(self.run_hook(hook, raw=b"\xff\xfe{"), (0, ""))

    def test_garbage_with_bad_claude_dir(self):
        rc, _ = self.run_hook(CLAIM, raw="garbage", gdir=self.tmp / "nonexistent" / "x")
        self.assertEqual(rc, 0)

    def test_unusable_claude_dir(self):
        blocker = self.tmp / "file"
        blocker.write_text("x")
        rc, _ = self.run_hook(PEERS, {"session_id": "s", "hook_event_name": "SessionStart"},
                              gdir=blocker / "nope")
        self.assertEqual(rc, 0)
        rc, _ = self.run_hook(CLAIM, self.post("s", self.tmp, "Write", "file_path", blocker),
                              gdir=blocker / "nope")
        self.assertEqual(rc, 0)
        rc, _ = self.run_hook(END, {"session_id": "s"}, gdir=blocker / "nope")
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
