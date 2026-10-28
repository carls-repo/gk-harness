"""Publish gate: gk-remote-pending, gk-remote-clear, gk-remote-stop, gk-remote-skip.

Ports every case of the old ~/.claude/hooks/gk-remote.test.sh, plus the docs-tool matcher
(is_docs_publish), the ledger created on the first publish in any repo, and the skip command
printed with the running interpreter. The hooks run as subprocesses with GK_CLAUDE_DIR set to a
temp folder and temp git repos, never the real ~/.claude or a real repo.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS = os.path.join(ROOT, "hooks")

PENDING = "gk-remote-pending.py"
CLEAR = "gk-remote-clear.py"
STOP = "gk-remote-stop.py"
SKIP = "gk-remote-skip.py"

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_NOSYSTEM": "1",  # GIT_CONFIG_GLOBAL is added by setUpModule
}
_GLOBAL_DIR = None

URL1 = "https://claude.ai/code/artifact/aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb"
URL2 = "https://claude.ai/artifact/cccccccc-1111-2222-3333-dddddddddddd"
DOC_ID = "aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb"
HEADER = "path\turl\tpublished"

# Claude Docs tool names as the surfaces give them.
CLI_DOCS = "mcp__claude_ai_Claude_Docs__"
DESK_DOCS = "mcp__1a59c906-04da-521d-bda7-7f71b9f9e01c__"
# A doc's birth: a batch call whose container creates the project.
NEW_DOC = {"container": {"kind": "project", "create": {"name": "x"}}, "batch": []}


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
    r = subprocess.run(["git", "-C", str(cwd)] + list(args), check=True, capture_output=True,
                       stdin=subprocess.DEVNULL, env=env, encoding="utf-8")
    return r.stdout


def load_clear_module():
    spec = importlib.util.spec_from_file_location("gk_remote_clear", os.path.join(HOOKS, CLEAR))
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, HOOKS)
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(HOOKS)
    return mod


class RemoteBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(os.path.realpath(self._tmp.name))
        self.g = self.tmp / "claude"
        self.R = self.tmp / "repo"
        self.N = self.tmp / "plain"
        git(self.tmp, "init", "-q", "-b", "main", str(self.R))
        for d in ("docs/superpowers", "docs/reports", "docs/plans", "src"):
            (self.R / d).mkdir(parents=True)
        (self.N / "docs" / "superpowers").mkdir(parents=True)
        (self.R / "README.md").write_text("x\n")
        git(self.R, "add", "-A")
        git(self.R, "-c", "core.hooksPath=" + os.devnull, "commit", "-q", "-m", "init")
        self.sid = "S1"
        self.cwd = self.R

    def make_worktree(self):
        self.W = self.tmp / "wt"
        git(self.R, "worktree", "add", "-q", str(self.W), "-b", "wt-branch")
        (self.W / "docs" / "superpowers").mkdir(parents=True, exist_ok=True)
        return self.W

    # ---- running the hooks
    def run_hook(self, hook, payload=None, raw=None, args=(), cwd=None):
        env = dict(os.environ, GK_CLAUDE_DIR=str(self.g), **GIT_ENV)
        if raw is None:
            raw = json.dumps(payload) if payload is not None else ""
        p = subprocess.run([sys.executable, os.path.join(HOOKS, hook)] + list(args),
                           input=raw if isinstance(raw, bytes) else raw.encode("utf-8"),
                           capture_output=True, env=env,
                           cwd=str(cwd) if cwd else None, timeout=60)
        self.last_err = p.stderr.decode("utf-8", "replace")
        return p.returncode, p.stdout.decode("utf-8")

    def ok(self, hook, payload=None, raw=None):
        rc, out = self.run_hook(hook, payload, raw)
        self.assertEqual(rc, 0, self.last_err)
        return out

    def post(self, tool, ti, tr=None, cwd=None):
        return {"session_id": self.sid, "cwd": str(cwd or self.cwd), "hook_event_name": "PostToolUse",
                "tool_name": tool, "tool_input": ti, "tool_response": {} if tr is None else tr}

    def edit(self, path, tool="Edit", cwd=None, key="file_path", write=True):
        if write:
            p = Path(path) if os.path.isabs(str(path)) else Path(cwd or self.cwd, path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x")
        return self.ok(PENDING, self.post(tool, {key: str(path)}, cwd=cwd))

    def clear(self, tool, ti, tr=None, cwd=None):
        return self.ok(CLEAR, self.post(tool, ti, tr, cwd))

    def stop(self, active):
        return self.ok(STOP, {"session_id": self.sid, "cwd": str(self.R), "hook_event_name": "Stop",
                              "stop_hook_active": active})

    def skip(self, *args):
        rc, out = self.run_hook(SKIP, raw="", args=args)
        return rc, out + self.last_err

    # ---- reading state
    def pfile(self, sid=None):
        return self.g / "state" / "gk-remote-pending" / ("%s.json" % (sid or self.sid))

    def pend(self, sid=None):
        return json.loads(self.pfile(sid).read_text(encoding="utf-8"))

    def paths(self, sid=None):
        return sorted(e["path"] for e in self.pend(sid)["pending"])

    def entry(self, suffix, sid=None):
        return [e for e in self.pend(sid)["pending"] if e["path"].endswith(suffix)][0]

    def ledger_path(self, root=None):
        return Path(root or self.R, "docs", "superpowers", "REMOTE-LINKS.tsv")

    def ledger(self, root=None):
        return self.ledger_path(root).read_text(encoding="utf-8")

    def ledger_url(self, rel, root=None):
        rows = [l.split("\t") for l in self.ledger(root).splitlines() if l.startswith(rel + "\t")]
        return rows[-1][1]

    @staticmethod
    def ctx(out):
        return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out.strip() else ""

    @staticmethod
    def reason(out):
        return json.loads(out)["reason"] if out.strip() else ""


class PendingTests(RemoteBase):
    """Hook 4: a written spec is noted as pending and the seat is told how to publish it."""

    def test_spec_matched(self):
        out = self.edit(self.R / "docs/superpowers/a-spec.md")
        msg = self.ctx(out)
        self.assertIn("Spec written (gk-remote): docs/superpowers/a-spec.md.", msg)
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        self.assertIn("No reading copy yet.", msg)
        self.assertIn("gk-remote-skip.py S1 docs/superpowers/a-spec.md", msg)
        self.assertIn("a Claude Doc", msg)
        st = self.pend()
        self.assertEqual(len(st["pending"]), 1)
        self.assertEqual(st["session_id"], "S1")
        e = st["pending"][0]
        self.assertEqual((e["root"], e["kind"]), (str(self.R), "md"))
        self.assertIs(e["yielded"], False)

    def test_ignored_paths(self):
        self.edit(self.R / "docs/superpowers/a-spec.md")
        for f in ("docs/superpowers/_scratch.md", "docs/superpowers/README.md", "src/x.md",
                  "docs/superpowers/notes.txt", "docs/other/x.md", "docs/reports/README.md"):
            with self.subTest(f=f):
                self.assertEqual(self.edit(self.R / f), "")
        self.assertEqual(len(self.pend()["pending"]), 1)

    def test_non_git_path_ignored(self):
        self.assertEqual(self.edit(self.N / "docs/superpowers/x.md", cwd=self.N), "")
        self.assertFalse(self.pfile().exists())

    def test_nested_plans_dir_and_two_pending_note(self):
        self.edit(self.R / "docs/superpowers/a-spec.md")
        msg = self.ctx(self.edit(self.R / "docs/plans/sub/dir/p.md"))
        self.assertIn("docs/plans/sub/dir/p.md", msg)
        self.assertIn("2 specs pending", msg)

    def test_dedup_and_yielded_reset(self):
        self.edit(self.R / "docs/superpowers/a-spec.md")
        self.edit(self.R / "docs/plans/sub/dir/p.md")
        st = self.pend()
        for e in st["pending"]:
            e["yielded"] = True
            e["first_at"] = e["last_at"] = "2000-01-01T00:00:00Z"
        self.pfile().write_text(json.dumps(st))
        self.edit(self.R / "docs/superpowers/a-spec.md")
        self.assertEqual(len(self.pend()["pending"]), 2)
        a = self.entry("a-spec.md")
        self.assertIs(a["yielded"], False)
        self.assertIs(self.entry("p.md")["yielded"], True)
        self.assertEqual(a["first_at"], "2000-01-01T00:00:00Z")
        self.assertGreater(a["last_at"], a["first_at"])

    def test_html_channel_and_worktree_root(self):
        W = self.make_worktree()
        self.sid = "S2"
        out = self.edit(W / "docs/reports/r.html", tool="Write", cwd=W)
        self.assertIn('SendUserFile with display "render"', self.ctx(out))
        self.assertEqual(self.pend()["pending"][0]["root"], str(W))
        self.assertEqual(self.pend()["pending"][0]["kind"], "html")
        # notebook_path, relative to cwd, resolves to the same entry
        self.edit("docs/reports/r.html", tool="NotebookEdit", cwd=W, key="notebook_path", write=False)
        self.assertEqual(len(self.pend()["pending"]), 1)

    def test_ledger_names_last_link(self):
        led = self.ledger_path()
        led.write_text("path\turl\tpublished\n"
                       "docs/superpowers/led.md\t%s\t2026-10-01\n"
                       "docs/superpowers/other.md\t%s\t2026-10-02\n"
                       "docs/superpowers/led.md\t%s\t2026-10-05\n" % (URL1, URL1, URL2))
        msg = self.ctx(self.edit(self.R / "docs/superpowers/led.md"))
        self.assertIn("A reading copy already exists at %s (published 2026-10-05): revise that doc, "
                      "do not create another." % URL2, msg)
        self.assertNotIn(URL1, msg)
        with open(led, "a") as f:
            f.write("docs/superpowers/led.md\tsent:SendUserFile\t2026-10-06\n")
        msg = self.ctx(self.edit(self.R / "docs/superpowers/led.md"))
        self.assertIn("exists at %s (published 2026-10-05)" % URL2, msg)
        with open(led, "a") as f:
            f.write("docs/superpowers/sentonly.md\tsent:SendUserFile\t2026-10-06\n")
        msg = self.ctx(self.edit(self.R / "docs/superpowers/sentonly.md"))
        self.assertIn("No reading copy yet.", msg)

    def test_served_session_id_ignored(self):
        self.sid = "served:abc"
        self.assertEqual(self.edit(self.R / "docs/superpowers/a-spec.md"), "")
        self.assertFalse((self.g / "state" / "gk-remote-pending").exists())


class ClearTests(RemoteBase):
    """Hook 5: a publish clears pending specs and records the link in the repo's ledger."""

    def setUp(self):
        super().setUp()
        self.sid = "S4"
        for f in ("docs/superpowers/one.md", "docs/superpowers/two.md", "docs/reports/three.html"):
            self.edit(self.R / f)

    def test_send_user_file(self):
        out = self.clear("SendUserFile", {"files": [str(self.R / "docs/superpowers/one.md")],
                                          "display": "render"})
        self.assertEqual(out, "")
        self.assertEqual(self.paths(), ["docs/reports/three.html", "docs/superpowers/two.md"])
        self.assertEqual(self.ledger().splitlines()[0], HEADER)
        self.assertIn("docs/superpowers/one.md\tsent:SendUserFile\t20", self.ledger())
        self.clear("SendUserFile", {"files": ["docs/reports/three.html"]})
        self.assertEqual(self.paths(), ["docs/superpowers/two.md"])
        self.clear("SendUserFile", {"files": ["/nowhere/x.md"]})
        self.assertEqual(len(self.pend()["pending"]), 1)

    def test_non_publishing_calls_clear_nothing(self):
        self.clear("Artifact", {"action": "read", "url": "x"}, {"content": URL1})
        self.clear("Artifact", {"action": "publish", "asset": True, "file_path": "a.png"}, {"url": URL1})
        self.clear(CLI_DOCS + "read", {}, {"x": URL1})
        self.assertEqual(len(self.pend()["pending"]), 3)
        self.assertFalse(self.ledger_path().exists())

    def test_docs_name_without_doc_shape_clears_nothing(self):
        self.clear(CLI_DOCS + "batch", {"batch": []}, {"u": URL1})
        self.clear(CLI_DOCS + "update", {"payload": "x"}, {"u": URL1})
        self.clear("mcp__903ad2ed-f15f-4ae3-b958-706c5e1b78d9__create_draft",
                   {"container": {"kind": "project", "id": DOC_ID}}, {"u": URL1})
        self.clear("mcp__supabase-dev__create_branch", {"container": {}}, {"u": URL1})
        self.assertEqual(len(self.pend()["pending"]), 3)
        self.assertFalse(self.ledger_path().exists())

    def test_docs_batch_clears_all(self):
        self.edit(self.R / "docs/superpowers/four.md")
        out = self.clear(CLI_DOCS + "batch", NEW_DOC,
                         {"content": [{"type": "text", "text": "created doc %s and also %s" % (URL1, URL2)}]})
        self.assertEqual(out, "")
        self.assertEqual(self.pend()["pending"], [])
        led = self.ledger()
        for rel in ("one.md", "two.md", "four.md"):
            self.assertIn("docs/superpowers/%s\t%s\t20" % (rel, URL1), led)
        self.assertIn("docs/reports/three.html\t%s\t20" % URL1, led)
        self.assertNotIn(URL2, led)
        self.assertEqual(led.count(HEADER + "\n"), 1)
        # empty pending: the ledger is untouched
        self.clear(CLI_DOCS + "update", {"ref": {"object": "node", "id": "n1"}, "payload": "x"},
                   {"x": URL1})
        self.assertEqual(self.ledger(), led)
        # Artifact with no action publishes
        self.edit(self.R / "docs/superpowers/five.md")
        self.clear("Artifact", {"file_path": "/x.html"}, {"url": URL2})
        self.assertEqual(self.pend()["pending"], [])
        self.assertTrue(self.ledger().splitlines()[-1].startswith("docs/superpowers/five.md\t%s\t20" % URL2))
        # no link in the response and no container id -> doc:unknown-url
        self.edit(self.R / "docs/superpowers/six.md")
        self.clear(CLI_DOCS + "create", NEW_DOC, {"ok": True})
        self.assertTrue(self.ledger().splitlines()[-1].startswith("docs/superpowers/six.md\tdoc:unknown-url\t20"))

    def test_worktree_ledger_in_worktree_toplevel(self):
        W = self.make_worktree()
        self.sid = "S5"
        self.edit(W / "docs/superpowers/w.md", tool="Write", cwd=W)
        self.clear(CLI_DOCS + "batch", NEW_DOC, {"u": URL1}, cwd=W)
        self.assertIn("docs/superpowers/w.md\t%s" % URL1, self.ledger(W))
        self.assertNotIn("w.md", self.ledger_path().read_text() if self.ledger_path().exists() else "")

    def test_ledger_without_trailing_newline_gets_one(self):
        # bytes, not write_text: text mode would turn "\n" into "\r\n" on Windows and
        # plant a CR before the hook runs, so the no-CR check below could not tell a
        # regression from a correct hook.
        self.ledger_path().write_bytes(
            (HEADER + "\ndocs/superpowers/old.md\t%s\t2026-10-01" % URL1).encode("utf-8"))
        self.clear(CLI_DOCS + "batch", NEW_DOC, {"u": URL2})
        lines = self.ledger().splitlines()
        self.assertEqual(lines[1], "docs/superpowers/old.md\t%s\t2026-10-01" % URL1)
        self.assertTrue(lines[2].startswith("docs/") and URL2 in lines[2])
        self.assertNotIn(b"\r", self.ledger_path().read_bytes())


class FixRoundTests(RemoteBase):
    """The old suite's fix round: yielded entries and docs with no link in the response."""

    def test_yielded_entries_still_count_and_clear(self):
        self.sid = "S10"
        self.edit(self.R / "docs/superpowers/y1.md")
        self.stop(True)
        self.assertEqual([e["yielded"] for e in self.pend()["pending"]], [True])
        msg = self.ctx(self.edit(self.R / "docs/superpowers/y2.md"))
        self.assertIn("2 specs pending", msg)
        self.clear(CLI_DOCS + "batch", NEW_DOC, {"u": URL1})
        self.assertIn("docs/superpowers/y1.md\t%s" % URL1, self.ledger())
        self.assertIn("docs/superpowers/y2.md\t%s" % URL1, self.ledger())

    def test_doc_lifecycle_without_response_links(self):
        self.sid = "S11"
        life = "docs/superpowers/life.md"
        code_url = "https://claude.ai/code/artifact/" + DOC_ID
        self.edit(self.R / life)
        self.clear(CLI_DOCS + "create", {"container": {"kind": "project", "create": {"name": "x"}}},
                   {"u": URL1})
        msg = self.ctx(self.edit(self.R / life))
        self.assertIn("A reading copy already exists at %s" % URL1, msg)
        self.clear(CLI_DOCS + "update", {"container": {"kind": "doc", "id": DOC_ID}, "payload": "x"},
                   {"ok": True})
        self.assertEqual(self.ledger_url(life), code_url)
        msg = self.ctx(self.edit(self.R / life))
        self.assertIn("exists at " + code_url, msg)
        self.assertNotIn("No reading copy yet.", msg)
        self.clear(CLI_DOCS + "update", {"ref": {"object": "node", "id": "n1"}, "payload": "x"},
                   {"ok": True})
        self.assertEqual(self.ledger_url(life), "doc:unknown-url")
        msg = self.ctx(self.edit(self.R / life))
        self.assertIn("exists at " + code_url, msg)
        with open(self.ledger_path(), "a") as f:
            f.write("docs/superpowers/lost.md\tdoc:unknown-url\t2026-10-09\n")
        msg = self.ctx(self.edit(self.R / "docs/superpowers/lost.md"))
        self.assertIn("published on 2026-10-09 but its link was not captured", msg)
        self.assertIn("do not create another", msg)
        self.assertNotIn("No reading copy yet.", msg)


class StopTests(RemoteBase):
    """Hook 6: the Stop gate holds the turn once while specs are unpublished."""

    def test_stop_silent_without_state(self):
        self.sid = "S6"
        self.assertEqual(self.stop(False), "")
        self.assertFalse(self.pfile().exists())

    def test_stop_blocks_then_yields(self):
        self.sid = "S6"
        self.edit(self.R / "docs/superpowers/stop-spec.md")
        self.edit(self.R / "docs/reports/stop.html")
        out = self.stop(False)
        self.assertEqual(json.loads(out)["decision"], "block")
        r = self.reason(out)
        self.assertIn("docs/superpowers/stop-spec.md", r)
        self.assertIn('docs/reports/stop.html: SendUserFile with display "render"', r)
        self.assertIn("gk-remote-skip.py S6 docs/superpowers/stop-spec.md", r)
        self.assertIn("Publish now, then end the turn again. If you genuinely cannot (connector down), "
                      "end the turn once more and say so.", r)
        self.assertEqual(self.stop(True), "")
        self.assertTrue(all(e["yielded"] for e in self.pend()["pending"]))
        self.assertEqual(self.stop(False), "")
        self.edit(self.R / "docs/superpowers/stop-spec.md")
        out = self.stop(False)
        self.assertIn('"decision": "block"', out)
        self.assertNotIn("stop.html", out)
        self.stop(True)
        self.assertEqual(self.stop(False), "")

    def test_stop_skip_command_uses_running_python(self):
        self.sid = "S6"
        rel = "docs/superpowers/it's a spec.md"
        self.edit(self.R / rel)
        cmd = "%s %s S6 %s" % (shlex.quote(Path(sys.executable).as_posix()),
                               shlex.quote(Path(HOOKS, SKIP).as_posix()), shlex.quote(rel))
        self.assertIn("If Carl says not to publish: " + cmd, self.reason(self.stop(False)))
        self.assertIn(cmd, self.ctx(self.edit(self.R / rel)))
        self.assertNotIn("~/.claude", self.reason(self.stop(False)))
        if os.name == "nt":
            return  # the printed command is for a POSIX shell (the Bash tool)
        env = dict(os.environ, GK_CLAUDE_DIR=str(self.g))
        p = subprocess.run(["sh", "-c", cmd], capture_output=True, env=env, cwd=str(self.R),
                           encoding="utf-8", timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("Removed pending spec: " + rel, p.stdout)
        self.assertEqual(self.pend()["pending"], [])


class SkipTests(RemoteBase):
    def test_skip(self):
        self.sid = "S7"
        for n in "abc":
            self.edit(self.R / ("docs/superpowers/skip-%s.md" % n))
        rc, out = self.skip("S7", "docs/superpowers/skip-a.md")
        self.assertEqual(rc, 0)
        self.assertIn("Removed pending spec: docs/superpowers/skip-a.md", out)
        self.assertEqual(self.paths(), ["docs/superpowers/skip-b.md", "docs/superpowers/skip-c.md"])
        rc, out = self.skip("S7", str(self.R / "docs/superpowers/skip-b.md"))
        self.assertEqual((rc, self.paths()), (0, ["docs/superpowers/skip-c.md"]))
        rc, out = self.skip("S7", "docs/superpowers/nope.md")
        self.assertEqual(rc, 0)
        self.assertIn("No pending entry matches", out)
        rc, out = self.skip("NOSUCH", "docs/x.md")
        self.assertEqual(rc, 0)
        self.assertIn("No pending specs for session NOSUCH.", out)
        rc, out = self.skip()
        self.assertEqual(rc, 0)
        self.assertIn("usage:", out)
        rc, out = self.skip("../x", "docs/x.md")
        self.assertEqual(rc, 0)
        self.assertIn("invalid session id", out)
        self.assertNotIn("skip-a.md", self.stop(False))


class GarbageTests(RemoteBase):
    def test_garbage_stdin_exits_0_silent(self):
        self.sid = "S1"
        self.edit(self.R / "docs/superpowers/a-spec.md")
        inputs = ["garbage {{", "", "[]", "null", "123", '{"session_id":5}',
                  '{"session_id":"../x","tool_input":{"file_path":"/a"}}',
                  '{"session_id":"S9","tool_input":"str"}', b"\xff\xfe{"]
        for hook in (PENDING, CLEAR, STOP):
            for g in inputs:
                with self.subTest(hook=hook, g=g):
                    rc, out = self.run_hook(hook, raw=g)
                    self.assertEqual((rc, out), (0, ""))
        names = [n for n in os.listdir(self.g / "state" / "gk-remote-pending") if not n.startswith(".")]
        self.assertEqual(names, ["S1.json"])


class DocsMatcherTests(RemoteBase):
    def test_is_docs_publish(self):
        mod = load_clear_module()
        container = {"container": {"kind": "project", "id": DOC_ID}, "batch": []}
        ref = {"ref": {"object": "node", "id": "n1"}, "payload": "x"}
        true_cases = [
            ("mcp__claude_ai_Claude_Docs__batch", container),
            (DESK_DOCS + "batch", container),
            (DESK_DOCS + "update", ref),
            (DESK_DOCS + "create", {"container": {"kind": "project", "create": {"name": "x"}}}),
            ("mcp__plugin_docs_Claude_Docs__update", ref),
        ]
        false_cases = [
            ("mcp__supabase-dev__create_branch", container),
            ("mcp__x__update", {}),
            ("mcp__903ad2ed-f15f-4ae3-b958-706c5e1b78d9__create_draft", container),
            ("mcp__x__update", {"ref": {"id": "n1"}}),          # ref but no payload
            ("mcp__x__update", {"ref": "n1", "payload": "x"}),  # ref not an object
            ("mcp__x__batch", {"container": "c1"}),             # container not an object
            ("mcp____batch", container),                        # no server name
            ("Artifact", container),
            ("mcp__x__batch\n", container),
            ("mcp__x__batch", None),
            (None, container),
        ]
        for name, ti in true_cases:
            with self.subTest(name=name):
                self.assertIs(mod.is_docs_publish(name, ti), True)
        for name, ti in false_cases:
            with self.subTest(name=name, ti=ti):
                self.assertIs(mod.is_docs_publish(name, ti), False)

    def test_clear_on_desktop_docs_name(self):
        self.edit(self.R / "docs/superpowers/desk.md")
        out = self.clear(DESK_DOCS + "batch", {"container": {"kind": "project", "id": DOC_ID},
                                               "batch": [{"op": "x"}]}, {"ok": True})
        self.assertEqual(out, "")
        self.assertEqual(self.pend()["pending"], [])
        self.assertEqual(self.ledger_url("docs/superpowers/desk.md"),
                         "https://claude.ai/code/artifact/" + DOC_ID)

    def test_ledger_created_on_first_publish(self):
        bare = self.tmp / "bare"
        git(self.tmp, "init", "-q", "-b", "main", str(bare))
        self.cwd = bare
        self.edit(bare / "docs/reports/r.md")
        self.assertFalse((bare / "docs" / "superpowers").exists())
        self.clear(DESK_DOCS + "update", {"ref": {"object": "node", "id": "n1"}, "payload": "x",
                                          "container": {"kind": "project", "id": DOC_ID}}, {"u": URL1})
        self.assertEqual(self.pend()["pending"], [])
        lines = self.ledger(bare).splitlines()
        self.assertEqual(lines[0], HEADER)
        self.assertTrue(lines[1].startswith("docs/reports/r.md\t%s\t20" % URL1))
        self.assertNotIn(b"\r", self.ledger_path(bare).read_bytes())


if __name__ == "__main__":
    unittest.main()
