from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "hooks", "gk-profile.py")
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_NOSYSTEM": "1",  # GIT_CONFIG_GLOBAL is added by setUpModule
}
_GLOBAL_DIR = None

HIGHER = ("gk-harness: this Workflow call asks for profile '%s' above the seat ('%s') without "
          "escalate: true. The policy allows that only on Carl's word.")


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


class ProfileHookTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.cdir = self.tmp / "claude"
        self.cdir.mkdir()
        self.norepo = self.tmp / "norepo"
        self.norepo.mkdir()
        self.stdouts = []

    # -- helpers -------------------------------------------------------------------------

    def run_hook(self, payload=None, raw=None, extra_env=None):
        env = dict(os.environ, GK_CLAUDE_DIR=str(self.cdir),
                   GIT_CEILING_DIRECTORIES=str(self.tmp), **GIT_ENV)
        env.pop("GK_PROFILE", None)
        env.update(extra_env or {})
        data = raw if raw is not None else json.dumps(payload).encode("utf-8")
        proc = subprocess.run([sys.executable, HOOK], input=data, capture_output=True,
                              env=env, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.stdouts.append(proc.stdout.decode("utf-8"))
        return proc

    def call(self, tool_input, cwd=None, extra_env=None, **kw):
        d = {"hook_event_name": "PreToolUse", "tool_name": "Workflow", "session_id": "s1",
             "cwd": str(cwd if cwd is not None else self.norepo), "tool_input": tool_input}
        d.update(kw)
        proc = self.run_hook(d, extra_env=extra_env)
        self.assertEqual(proc.stderr, b"")
        text = proc.stdout.decode("utf-8")
        if text == "":
            return None
        return json.loads(text)  # a stray newline or other text fails here

    def args_of(self, out):
        return out["hookSpecificOutput"]["updatedInput"]["args"]

    def seat(self, profile, sid="s1"):
        d = self.cdir / "state" / "gk-seat"
        d.mkdir(parents=True, exist_ok=True)
        model = {"fable": "claude-fable-5-1", "opus": "claude-opus-5-5",
                 "sonnet": "claude-sonnet-5-5"}[profile]
        (d / ("%s.json" % sid)).write_text(json.dumps(
            {"model": model, "profile": profile, "effort": "high", "source": "startup",
             "updated": "2026-10-28T00:00:00Z"}))

    def git(self, cwd, *args):
        env = dict(os.environ, **GIT_ENV)
        subprocess.run(["git", "-C", str(cwd)] + list(args), check=True, capture_output=True,
                       stdin=subprocess.DEVNULL, env=env)

    def repo(self, project_text=None):
        r = self.tmp / "repo"
        r.mkdir()
        self.git(r, "init", "-q")
        if project_text is not None:
            (r / ".claude").mkdir()
            (r / ".claude" / "gk-project.json").write_text(project_text, encoding="utf-8")
        return r

    def root_of(self, path):
        return Path(os.path.realpath(str(path))).as_posix()

    def assert_no_permission_decision(self):
        for s in self.stdouts:
            self.assertNotIn("permissionDecision", s)

    # -- profile -------------------------------------------------------------------------

    def test_injects_profile_from_state(self):
        self.seat("opus")
        out = self.call({"name": "gk-review", "args": {"target": "working-tree"}})
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PreToolUse")
        self.assertEqual(self.args_of(out), {"target": "working-tree", "profile": "opus"})
        self.assertEqual(out["hookSpecificOutput"]["updatedInput"]["name"], "gk-review")
        self.assertNotIn("additionalContext", out["hookSpecificOutput"])
        self.assertNotIn("systemMessage", out)

    def test_injects_profile_from_transcript(self):
        tp = self.tmp / "t.jsonl"
        tp.write_text(json.dumps({"type": "assistant",
                                  "message": {"model": "claude-sonnet-5-5"}}) + "\n")
        out = self.call({"name": "gk-build"}, transcript_path=str(tp))
        self.assertEqual(self.args_of(out), {"profile": "sonnet"})

    def test_injects_profile_from_env(self):
        out = self.call({"name": "gk-build", "args": None}, extra_env={"GK_PROFILE": "fable"})
        self.assertEqual(self.args_of(out), {"profile": "fable"})

    def test_no_source_passthrough(self):
        self.assertIsNone(self.call({"name": "gk-build", "args": {"x": 1}}))
        self.assertIsNone(self.call({"name": "gk-build"}))

    def test_explicit_kept(self):
        self.seat("opus")
        self.assertIsNone(self.call({"name": "gk-build", "args": {"profile": "sonnet"}}))
        self.assertIsNone(self.call({"name": "gk-build", "args": {"profile": "opus"}}))

    def test_explicit_higher_warns_not_changed(self):
        self.seat("opus")
        out = self.call({"name": "gk-build", "args": {"profile": "fable", "escalate": "yes"}})
        msg = HIGHER % ("fable", "opus")
        self.assertEqual(out, {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                      "additionalContext": msg},
                               "systemMessage": msg})

    def test_explicit_higher_with_escalate_silent(self):
        self.seat("sonnet")
        self.assertIsNone(self.call({"name": "gk-build",
                                     "args": {"profile": "fable", "escalate": True}}))

    def test_explicit_higher_no_seat_silent(self):
        self.assertIsNone(self.call({"name": "gk-build", "args": {"profile": "fable"}}))

    def test_empty_profile_is_replaced(self):
        self.seat("opus")
        out = self.call({"name": "gk-build", "args": {"profile": ""}})
        self.assertEqual(self.args_of(out), {"profile": "opus"})

    # -- which calls ---------------------------------------------------------------------

    def test_non_gk_untouched(self):
        self.seat("opus")
        self.assertIsNone(self.call({"name": "my-flow", "args": {}}))
        self.assertIsNone(self.call({"script": "export default 1", "args": {}}))
        self.assertIsNone(self.call({"scriptPath": "/x/flows/build.js"}))
        self.assertIsNone(self.call({"name": "gk-Review"}))
        self.assertIsNone(self.call({"name": "gk-build"}, tool_name="Agent"))
        # an inline script is never a gk call, whatever its name
        self.assertIsNone(self.call({"name": "gk-review", "script": "export default 1",
                                     "args": {"x": 1}}))
        self.assertIsNone(self.call({"name": "gk-diagnose", "script": "x"},
                                    scratchpad_dir="/s"))

    def test_scriptpath_gk_detected(self):
        self.seat("opus")
        out = self.call({"scriptPath": "/home/u/.claude/workflows/gk-review.js"})
        self.assertEqual(self.args_of(out), {"profile": "opus"})
        out = self.call({"scriptPath": "C:\\Users\\u\\.claude\\workflows\\gk-build.js"})
        self.assertEqual(self.args_of(out), {"profile": "opus"})

    # -- args shapes ---------------------------------------------------------------------

    def test_args_string_parsed(self):
        self.seat("opus")
        out = self.call({"name": "gk-review", "args": '{"target": "develop..HEAD"}'})
        self.assertEqual(self.args_of(out), {"target": "develop..HEAD", "profile": "opus"})

    def test_args_array_untouched(self):
        self.seat("opus")
        repo = self.repo('{"version": 1}')
        for args in (["a"], 3, True, "null", "not json", "[1, 2]"):
            self.assertIsNone(self.call({"name": "gk-review", "args": args}, cwd=repo))

    def test_scriptpath_diagnose_gets_scratchpad(self):
        out = self.call({"scriptPath": "/x/gk-diagnose.js", "args": {"symptom": "s"}},
                        scratchpad_dir="/sp")
        self.assertEqual(self.args_of(out), {"symptom": "s", "scratchpad": "/sp"})

    def test_unusable_cwd_still_injects_profile(self):
        self.seat("opus")
        afile = self.tmp / "afile"
        afile.write_text("x")
        for cwd in (9, str(self.tmp / "missing"), str(afile)):
            d = {"hook_event_name": "PreToolUse", "tool_name": "Workflow", "session_id": "s1",
                 "cwd": cwd, "tool_input": {"name": "gk-build"}}
            proc = self.run_hook(d)
            self.assertEqual(proc.stderr, b"")
            out = json.loads(proc.stdout.decode("utf-8"))
            self.assertEqual(self.args_of(out), {"profile": "opus"}, cwd)

    # -- project -------------------------------------------------------------------------

    def test_project_injected_with_root(self):
        repo = self.repo('{"version": 1, "name": "Goodricke"}')
        sub = repo / "a" / "b"
        sub.mkdir(parents=True)
        out = self.call({"name": "gk-review", "args": {"target": "x"}}, cwd=sub)
        args = self.args_of(out)
        self.assertEqual(args["project"]["name"], "Goodricke")
        self.assertEqual(args["project"],
                         {"version": 1, "name": "Goodricke", "root": self.root_of(repo)})
        self.assertNotIn("profile", args)
        self.assertEqual(args["target"], "x")

    def test_project_absent_gives_root_only(self):
        repo = self.repo()
        self.seat("fable")
        out = self.call({"name": "gk-build"}, cwd=repo)
        self.assertEqual(self.args_of(out),
                         {"profile": "fable", "project": {"root": self.root_of(repo)}})
        self.assertNotIn("additionalContext", out["hookSpecificOutput"])

    def test_project_invalid_warns(self):
        repo = self.repo('{"version": 2}')
        out = self.call({"name": "gk-build"}, cwd=repo)
        root = self.root_of(repo)
        self.assertEqual(self.args_of(out), {"project": {"root": root}})
        ctx = out["hookSpecificOutput"]["additionalContext"]
        prefix = "gk-harness: .claude/gk-project.json at %s is unreadable (" % root
        self.assertTrue(ctx.startswith(prefix), ctx)
        self.assertTrue(ctx.endswith("); the workflow runs without the project rules."), ctx)
        self.assertIn("unsupported version 2", ctx)
        self.assertEqual(out["systemMessage"], ctx)

    def test_project_explicit_kept(self):
        repo = self.repo('{"version": 1, "name": "Goodricke"}')
        self.assertIsNone(self.call({"name": "gk-build", "args": {"project": None}}, cwd=repo))
        self.assertIsNone(self.call({"name": "gk-build", "args": {"project": {"x": 1}}},
                                    cwd=repo))

    def test_warnings_combine(self):
        self.seat("sonnet")
        repo = self.repo("{nope")
        out = self.call({"name": "gk-build", "args": {"profile": "opus"}}, cwd=repo)
        self.assertEqual(self.args_of(out),
                         {"profile": "opus", "project": {"root": self.root_of(repo)}})
        lines = out["hookSpecificOutput"]["additionalContext"].split("\n")
        self.assertEqual(lines[0], HIGHER % ("opus", "sonnet"))
        self.assertIn("not valid JSON", lines[1])
        self.assertEqual(len(lines), 2)

    # -- scratchpad ----------------------------------------------------------------------

    def test_scratchpad_for_diagnose_only(self):
        sp = str(self.tmp / "scratch")
        out = self.call({"name": "gk-diagnose", "args": {"symptom": "s"}}, scratchpad_dir=sp)
        self.assertEqual(self.args_of(out), {"symptom": "s", "scratchpad": sp})
        self.assertIsNone(self.call({"name": "gk-review"}, scratchpad_dir=sp))
        self.assertIsNone(self.call({"name": "gk-diagnose", "args": {"scratchpad": "/mine"}},
                                    scratchpad_dir=sp))
        self.assertIsNone(self.call({"name": "gk-diagnose"}, scratchpad_dir=42))
        self.assertIsNone(self.call({"name": "gk-diagnose"}, scratchpad_dir=""))
        self.assertIsNone(self.call({"name": "gk-diagnose"}))

    # -- robustness ----------------------------------------------------------------------

    def test_garbage_input_exit_zero(self):
        for raw in (b"", b"not json", b"[1]", b"\xff\xfe", b"null",
                    json.dumps({"tool_name": "Workflow", "tool_input": "x"}).encode(),
                    json.dumps({"tool_name": "Workflow", "tool_input": {"name": 5,
                                                                         "scriptPath": 7},
                                "cwd": 9, "session_id": {}}).encode()):
            proc = self.run_hook(raw=raw, extra_env={"GK_PROFILE": "opus"})
            self.assertEqual(proc.stdout, b"", raw)
            self.assertEqual(proc.stderr, b"", raw)

    def test_resume_call_keeps_other_fields(self):
        self.seat("opus")
        ti = {"scriptPath": "/w/gk-build.js", "resumeFromRunId": "run-123",
              "args": {"plan": "p"}, "somethingNew": [1, {"a": 2}]}
        out = self.call(ti)
        upd = out["hookSpecificOutput"]["updatedInput"]
        self.assertEqual(upd, dict(ti, args={"plan": "p", "profile": "opus"}))

    def test_never_permission_decision(self):
        # Runs every other case in this class, then checks every stdout they produced.
        names = sorted(n for n in dir(self) if n.startswith("test_")
                       and n != "test_never_permission_decision")
        for n in names:
            with self.subTest(case=n):
                self._fresh()
                getattr(self, n)()
        self.assertGreater(len(self.stdouts), 20)
        self.assert_no_permission_decision()

    def _fresh(self):
        # A clean temp tree per replayed case; stdouts accumulate across them.
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.tmp = Path(t.name)
        self.cdir = self.tmp / "claude"
        self.cdir.mkdir()
        self.norepo = self.tmp / "norepo"
        self.norepo.mkdir()


if __name__ == "__main__":
    unittest.main()
