from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "hooks", "gk-profile.py")
sys.path.insert(0, os.path.join(ROOT, "hooks"))

import gk_common  # noqa: E402
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
        env = dict(os.environ, GK_CLAUDE_DIR=str(self.cdir), GK_NO_LAUNCH_ARGS="1",
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

    def seat(self, alias, sid="s1", effort="high"):
        d = self.cdir / "state" / "gk-seat"
        d.mkdir(parents=True, exist_ok=True)
        model = {"fable": "claude-fable-5-2", "opus": "claude-opus-5-5",
                 "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-5-5"}[alias]
        (d / ("%s.json" % sid)).write_text(json.dumps(
            {"model": model, "effort": effort, "source": "startup", "effortFrom": "launch",
             "updated": "2026-10-28T00:00:00Z"}))

    def v1_user_script(self, name="gk-build"):
        """An old user-level copy that still carries the args-v1 marker."""
        wf = self.cdir / "workflows"
        wf.mkdir(parents=True, exist_ok=True)
        (wf / (name + ".js")).write_text("export const meta = {name: '%s'}\n"
                                         "// gk-harness:args-v1\n" % name, encoding="utf-8")

    def fable_off(self):
        (self.cdir / "state").mkdir(exist_ok=True)
        (self.cdir / "state" / "gk-fable-off.json").write_text('{"since": "x"}')

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

    # -- seat (args-v2 scripts) -----------------------------------------------------------

    def test_injects_seat_from_state(self):
        self.seat("opus")
        out = self.call({"name": "gk-review", "args": {"target": "working-tree"}})
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PreToolUse")
        self.assertEqual(self.args_of(out), {"target": "working-tree", "seat": "opus-high"})
        self.assertEqual(out["hookSpecificOutput"]["updatedInput"]["name"], "gk-review")
        self.assertNotIn("additionalContext", out["hookSpecificOutput"])
        self.assertNotIn("systemMessage", out)

    def test_injects_seat_from_transcript_and_routes(self):
        tp = self.tmp / "t.jsonl"
        tp.write_text(json.dumps({"type": "assistant",
                                  "message": {"model": "claude-sonnet-5-5"}}) + "\n")
        out = self.call({"name": "gk-build"}, transcript_path=str(tp))
        self.assertEqual(self.args_of(out), {"seat": "sonnet-medium"})
        (self.cdir / "settings.json").write_text(json.dumps(
            {"modelSettings": {"claude-sonnet-5-5": {"effortLevel": "xhigh"}}}))
        out = self.call({"name": "gk-build"}, transcript_path=str(tp), effort="low")
        self.assertEqual(self.args_of(out), {"seat": "sonnet-xhigh"})

    def test_seat_is_a_plain_tier_name(self):
        self.seat("opus", effort="medium")
        out = self.call({"name": "gk-build"})
        self.assertEqual(self.args_of(out), {"seat": "opus-medium"})
        self.seat("fable", effort=None)
        out = self.call({"name": "gk-build"})
        self.assertEqual(self.args_of(out), {"seat": "fable-medium"})

    def test_no_seat_when_unresolved(self):
        self.assertIsNone(self.call({"name": "gk-build", "args": {"x": 1}},
                                    extra_env={"GK_PROFILE": "opus"}))
        self.assertIsNone(self.call({"name": "gk-build"}))
        self.seat("haiku", effort=None)  # haiku has no seat effort in routes.json
        self.assertIsNone(self.call({"name": "gk-build"}))

    def test_explicit_seat_kept(self):
        self.seat("opus")
        self.assertIsNone(self.call({"name": "gk-build", "args": {"seat": "fable-high"}}))
        out = self.call({"name": "gk-build", "args": {"seat": ""}})
        self.assertEqual(self.args_of(out), {"seat": "opus-high"})

    def test_v2_script_gets_no_profile(self):
        # profile is retired for args-v2 scripts: never injected, never compared
        self.seat("sonnet")
        out = self.call({"name": "gk-build"}, extra_env={"GK_PROFILE": "fable"})
        self.assertEqual(self.args_of(out), {"seat": "sonnet-high"})
        out = self.call({"name": "gk-build", "args": {"profile": "fable"}})
        self.assertEqual(out, {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "updatedInput": {"name": "gk-build", "args": {"profile": "fable",
                                                          "seat": "sonnet-high"}}}})
        for js in sorted((Path(ROOT) / "workflows").glob("gk-*.js")):
            if b"gk-harness:args-v2" not in js.read_bytes():
                continue
            out = self.call({"scriptPath": str(js)})
            self.assertNotIn("profile", self.args_of(out), js.name)

    # -- dials -------------------------------------------------------------------------------

    def test_dials_from_the_fable_switch(self):
        self.assertIsNone(self.call({"name": "gk-build", "args": {"dials": ["fable"]}}))
        self.assertIsNone(self.call({"name": "gk-build"}))
        self.fable_off()
        out = self.call({"name": "gk-build"})
        self.assertEqual(self.args_of(out), {"dials": ["nofable"]})
        out = self.call({"name": "gk-review", "args": {"dials": ["fable", "deep"]}})
        self.assertEqual(self.args_of(out), {"dials": ["fable", "deep", "nofable"]})
        self.assertIsNone(self.call({"name": "gk-build", "args": {"dials": ["nofable"]}}))
        # a malformed dials value is left for the script to reject
        self.assertIsNone(self.call({"name": "gk-build", "args": {"dials": "fable"}}))

    # -- args-v1 scripts keep today's behaviour ------------------------------------------

    def test_v1_injects_profile_from_state(self):
        self.v1_user_script("gk-review")
        self.seat("opus")
        self.fable_off()
        out = self.call({"name": "gk-review", "args": {"target": "working-tree"}})
        self.assertEqual(self.args_of(out), {"target": "working-tree", "profile": "opus"})

    def test_v1_injects_profile_from_transcript(self):
        self.v1_user_script()
        tp = self.tmp / "t.jsonl"
        tp.write_text(json.dumps({"type": "assistant",
                                  "message": {"model": "claude-sonnet-5-5"}}) + "\n")
        out = self.call({"name": "gk-build"}, transcript_path=str(tp))
        self.assertEqual(self.args_of(out), {"profile": "sonnet"})

    def test_v1_injects_profile_from_env(self):
        self.v1_user_script()
        out = self.call({"name": "gk-build", "args": None}, extra_env={"GK_PROFILE": "fable"})
        self.assertEqual(self.args_of(out), {"profile": "fable"})

    def test_v1_haiku_seat_gives_sonnet(self):
        # as the retired profile_for_model did: haiku, and any other claude model, is sonnet
        self.v1_user_script()
        self.seat("haiku")
        out = self.call({"name": "gk-build"})
        self.assertEqual(self.args_of(out), {"profile": "sonnet"})
        (self.cdir / "state" / "gk-seat" / "s1.json").unlink()
        tp = self.tmp / "t.jsonl"
        tp.write_text(json.dumps({"type": "assistant",
                                  "message": {"model": "claude-haiku-5-5"}}) + "\n")
        out = self.call({"name": "gk-build"}, transcript_path=str(tp))
        self.assertEqual(self.args_of(out), {"profile": "sonnet"})

    def test_v1_unknown_claude_model_gives_sonnet(self):
        self.v1_user_script()
        tp = self.tmp / "t.jsonl"
        tp.write_text(json.dumps({"type": "assistant",
                                  "message": {"model": "claude-mystery-9"}}) + "\n")
        out = self.call({"name": "gk-build"}, transcript_path=str(tp))
        self.assertEqual(self.args_of(out), {"profile": "sonnet"})
        # a seat record from the profile era is read by its profile, as before
        d = self.cdir / "state" / "gk-seat"
        d.mkdir(parents=True)
        (d / "s1.json").write_text(json.dumps({"model": "claude-mystery-9",
                                               "profile": "sonnet", "effort": "high"}))
        out = self.call({"name": "gk-build"})
        self.assertEqual(self.args_of(out), {"profile": "sonnet"})
        # a model that is not a claude model gives nothing, as before
        tp.write_text(json.dumps({"type": "assistant", "message": {"model": "gpt-9"}}) + "\n")
        (d / "s1.json").unlink()
        self.assertIsNone(self.call({"name": "gk-build"}, transcript_path=str(tp)))

    def test_v1_haiku_seat_warns_on_a_higher_profile(self):
        self.v1_user_script()
        self.seat("haiku")
        out = self.call({"name": "gk-build", "args": {"profile": "opus"}})
        msg = HIGHER % ("opus", "sonnet")
        self.assertEqual(out, {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                      "additionalContext": msg},
                               "systemMessage": msg})

    def test_v1_no_source_passthrough(self):
        self.v1_user_script()
        self.assertIsNone(self.call({"name": "gk-build", "args": {"x": 1}}))
        self.assertIsNone(self.call({"name": "gk-build"}))

    def test_v1_explicit_kept(self):
        self.v1_user_script()
        self.seat("opus")
        self.assertIsNone(self.call({"name": "gk-build", "args": {"profile": "sonnet"}}))
        self.assertIsNone(self.call({"name": "gk-build", "args": {"profile": "opus"}}))

    def test_v1_explicit_higher_warns_not_changed(self):
        self.v1_user_script()
        self.seat("opus")
        out = self.call({"name": "gk-build", "args": {"profile": "fable", "escalate": "yes"}})
        msg = HIGHER % ("fable", "opus")
        self.assertEqual(out, {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                      "additionalContext": msg},
                               "systemMessage": msg})

    def test_v1_explicit_higher_with_escalate_silent(self):
        self.v1_user_script()
        self.seat("sonnet")
        self.assertIsNone(self.call({"name": "gk-build",
                                     "args": {"profile": "fable", "escalate": True}}))

    def test_v1_explicit_higher_no_seat_silent(self):
        self.v1_user_script()
        self.assertIsNone(self.call({"name": "gk-build", "args": {"profile": "fable"}}))

    def test_v1_empty_profile_is_replaced(self):
        self.v1_user_script()
        self.seat("opus")
        out = self.call({"name": "gk-build", "args": {"profile": ""}})
        self.assertEqual(self.args_of(out), {"profile": "opus"})

    def test_v1_gets_no_seat_dials_changed_paths_or_legal_rule(self):
        # An old copy may reject keys it does not know, so a v1 script gets only what it got.
        repo = self.repo('{"version": 1, "name": "P", "legal": true}')
        self.commit_file(repo, "a.txt", "1")
        (repo / "a.txt").write_text("2")
        wf = repo / ".claude" / "workflows"
        wf.mkdir()
        (wf / "gk-review.js").write_text("// gk-harness:args-v1\n", encoding="utf-8")
        self.seat("opus")
        self.fable_off()
        out = self.call({"name": "gk-review", "args": {"target": "working-tree"}}, cwd=repo)
        self.assertEqual(self.args_of(out), {
            "target": "working-tree", "profile": "opus",
            "project": {"version": 1, "name": "P", "legal": True, "root": self.root_of(repo)}})
        out = self.call({"name": "gk-review", "args": {"target": "working-tree",
                                                       "project": {"legal": False}}}, cwd=repo)
        self.assertEqual(self.args_of(out), {"target": "working-tree", "profile": "opus",
                                             "project": {"legal": False}})

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
        self.assertEqual(self.args_of(out), {"seat": "opus-high"})
        out = self.call({"scriptPath": "C:\\Users\\u\\.claude\\workflows\\gk-build.js"})
        self.assertEqual(self.args_of(out), {"seat": "opus-high"})

    def test_saved_run_scriptpath_detected(self):
        # The runtime saves each run's script as <name>-wf_<id>.js; a re-run or resume by
        # that scriptPath is the same gk workflow.
        self.seat("opus")
        repo = self.repo('{"version": 1, "name": "P"}')
        project = {"version": 1, "name": "P", "root": self.root_of(repo)}
        out = self.call({"scriptPath":
                         "/x/projects/p/s/workflows/scripts/gk-review-wf_33244bcf-5bd.js"},
                        cwd=repo)
        self.assertEqual(self.args_of(out), {"seat": "opus-high", "project": project})
        out = self.call({"scriptPath":
                         "/x/projects/p/s/workflows/scripts/gk-diagnose-wf_1a2b3c4d-5e6.js",
                         "args": {"symptom": "s"}}, cwd=repo, scratchpad_dir="/sp")
        self.assertEqual(self.args_of(out), {"symptom": "s", "seat": "opus-high",
                                             "project": project, "scratchpad": "/sp"})
        out = self.call({"scriptPath": "/x/workflows/gk-review.js"}, cwd=repo)
        self.assertEqual(self.args_of(out), {"seat": "opus-high", "project": project})
        for path in ("/x/scripts/notgk-review-wf_1-2.js", "/x/scripts/gk-review-wf_.js"):
            self.assertIsNone(self.call({"scriptPath": path}), path)

    # -- the script that runs: only a marked one is injected (ruling R59) ----------------

    def project_copy_note(self, name):
        return ("gk-harness: this repo's own .claude/workflows/%s.js runs (it predates "
                "gk-harness), so nothing was injected; pass profile yourself only if that copy "
                "accepts it, and merge the shared branch to pick up the user-level workflows."
                % name)

    def note_only(self, name):
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                       "additionalContext": self.project_copy_note(name)}}

    def golden_bytes(self, name):
        """An old (pre-harness) copy: the Goodricke golden, which has no marker."""
        data = (Path(ROOT) / "tests" / "workflows" / "golden" / (name + ".js")).read_bytes()
        self.assertNotIn(b"gk-harness:args-v1", data)
        return data

    def harness_bytes(self, name):
        return (Path(ROOT) / "workflows" / (name + ".js")).read_bytes()

    def test_repo_copy_without_marker_gets_nothing(self):
        self.seat("opus")
        repo = self.repo('{"version": 1, "name": "P"}')
        wf = repo / ".claude" / "workflows"
        wf.mkdir()
        (wf / "gk-research.js").write_bytes(self.golden_bytes("gk-research"))
        sub = repo / "a"
        sub.mkdir()
        for cwd in (repo, sub):
            out = self.call({"name": "gk-research", "args": {"question": "q"}}, cwd=cwd)
            self.assertEqual(out, self.note_only("gk-research"))
        self.assertNotIn("updatedInput", self.stdouts[-1])
        self.assertNotIn("systemMessage", self.stdouts[-1])
        project = {"version": 1, "name": "P", "root": self.root_of(repo)}
        # a workflow the repo has no copy of is injected as before
        out = self.call({"name": "gk-design"}, cwd=repo)
        self.assertEqual(self.args_of(out), {"seat": "opus-high", "project": project})
        # the same repo copy once it carries the marker (a merge of the harness's copy)
        (wf / "gk-research.js").write_bytes(self.harness_bytes("gk-research"))
        for cwd in (repo, sub):
            out = self.call({"name": "gk-research", "args": {"question": "q"}}, cwd=cwd)
            self.assertEqual(self.args_of(out), {"question": "q", "seat": "opus-high",
                                                 "project": project})

    def test_repo_copy_wins_over_a_marked_user_copy(self):
        self.seat("opus")
        user = self.cdir / "workflows"
        user.mkdir()
        (user / "gk-research.js").write_bytes(self.harness_bytes("gk-research"))
        repo = self.repo()
        wf = repo / ".claude" / "workflows"
        wf.mkdir(parents=True)
        (wf / "gk-research.js").write_bytes(self.golden_bytes("gk-research"))
        out = self.call({"name": "gk-research", "args": {"question": "q"}}, cwd=repo)
        self.assertEqual(out, self.note_only("gk-research"))

    def test_cwd_copy_outside_a_repo_without_marker_gets_nothing(self):
        self.seat("opus")
        wf = self.norepo / ".claude" / "workflows"
        wf.mkdir(parents=True)
        (wf / "gk-review.js").write_text("export default 1\n", encoding="utf-8")
        out = self.call({"name": "gk-review", "args": {"target": "x"}})
        self.assertEqual(out, self.note_only("gk-review"))
        out = self.call({"name": "gk-build"})
        self.assertEqual(self.args_of(out), {"seat": "opus-high"})
        # the marker anywhere in the first 16 KB is enough; past them it is not seen
        (wf / "gk-review.js").write_text("export default 1\n// gk-harness:args-v1\n",
                                         encoding="utf-8")
        out = self.call({"name": "gk-review", "args": {"target": "x"}})
        self.assertEqual(self.args_of(out), {"target": "x", "profile": "opus"})
        (wf / "gk-review.js").write_text("//" + "x" * 16384 + "\n// gk-harness:args-v1\n",
                                         encoding="utf-8")
        out = self.call({"name": "gk-review", "args": {"target": "x"}})
        self.assertEqual(out, self.note_only("gk-review"))
        # the v2 marker in the first 16 KB makes it a v2 script, whatever follows
        (wf / "gk-review.js").write_text("// gk-harness:args-v2\n// gk-harness:args-v1\n",
                                         encoding="utf-8")
        out = self.call({"name": "gk-review", "args": {"target": "x"}})
        self.assertEqual(self.args_of(out), {"target": "x", "seat": "opus-high"})
        (wf / "gk-review.js").write_text("// gk-harness:args-v1\n//" + "x" * 16384
                                         + "\n// gk-harness:args-v2\n", encoding="utf-8")
        out = self.call({"name": "gk-review", "args": {"target": "x"}})
        self.assertEqual(self.args_of(out), {"target": "x", "profile": "opus"})

    def test_seat_in_the_home_folder_runs_the_marked_user_copy(self):
        # Install puts the harness workflows in <home>/.claude/workflows. A seat started in the
        # home folder (or in a home that is itself a git repo) runs those, and gets its profile.
        home = self.tmp / "home"
        self.cdir = home / ".claude"
        user = self.cdir / "workflows"
        user.mkdir(parents=True)
        for name in ("gk-review", "gk-build"):
            (user / (name + ".js")).write_bytes(self.harness_bytes(name))
        self.seat("opus")
        sub = home / "notes"
        sub.mkdir()
        for cwd in (home, sub):
            out = self.call({"name": "gk-review", "args": {"target": "x"}}, cwd=cwd)
            self.assertEqual(self.args_of(out), {"target": "x", "seat": "opus-high"}, cwd)
        self.git(home, "init", "-q")
        for cwd in (home, sub):
            # target x names no revision, so git fails and no changedPaths is added
            out = self.call({"name": "gk-review", "args": {"target": "x"}}, cwd=cwd)
            self.assertEqual(self.args_of(out), {"target": "x", "seat": "opus-high",
                                                 "project": {"root": self.root_of(home)}}, cwd)
        out = self.call({"name": "gk-build"}, cwd=home)
        self.assertEqual(self.args_of(out), {"seat": "opus-high",
                                             "project": {"root": self.root_of(home)}})
        # an old, unmarked user-level copy is left alone, wherever the seat starts
        (user / "gk-review.js").write_bytes(self.golden_bytes("gk-review"))
        for cwd in (home, self.norepo):
            out = self.call({"name": "gk-review", "args": {"target": "x"}}, cwd=cwd)
            self.assertEqual(out, self.note_only("gk-review"), cwd)

    def test_no_script_found_injects_as_before(self):
        # Nothing at the repo, cwd or user level: the runtime's own lookup decides, and the
        # call is injected as it was before the marker rule.
        self.seat("opus")
        repo = self.repo()
        (repo / ".claude" / "workflows" / "gk-review.js").mkdir(parents=True)  # not a file
        out = self.call({"name": "gk-review"}, cwd=repo)
        self.assertEqual(self.args_of(out),
                         {"seat": "opus-high", "project": {"root": self.root_of(repo)}})

    def test_saved_run_script_of_an_old_copy_gets_nothing(self):
        self.seat("opus")
        scripts = self.tmp / "projects" / "p" / "s" / "workflows" / "scripts"
        scripts.mkdir(parents=True)
        old = scripts / "gk-review-wf_33244bcf-5bd.js"
        old.write_bytes(self.golden_bytes("gk-review"))
        out = self.call({"scriptPath": str(old), "args": {"target": "x"}})
        self.assertEqual(out, self.note_only("gk-review"))
        # a relative scriptPath is read against cwd
        rel = os.path.join("workflows", "scripts", old.name)
        out = self.call({"scriptPath": rel, "args": {"target": "x"}},
                        cwd=self.tmp / "projects" / "p" / "s")
        self.assertEqual(out, self.note_only("gk-review"))

    def test_marked_scriptpath_is_injected(self):
        self.seat("opus")
        scripts = self.tmp / "scripts"
        scripts.mkdir()
        saved = scripts / "gk-review-wf_33244bcf-5bd.js"
        saved.write_bytes(self.harness_bytes("gk-review"))
        out = self.call({"scriptPath": str(saved), "args": {"target": "x"}})
        self.assertEqual(self.args_of(out), {"target": "x", "seat": "opus-high"})
        # every harness workflow, run by its own path, is an args-v2 script and gets the seat
        for js in sorted((Path(ROOT) / "workflows").glob("gk-*.js")):
            self.assertIn(b"gk-harness:args-v2", js.read_bytes()[:16384], js.name)
            out = self.call({"scriptPath": str(js)})
            self.assertEqual(self.args_of(out), {"seat": "opus-high"}, js.name)

    def test_selftest_gets_seat_and_dials(self):
        self.seat("opus", effort="medium")
        user = self.cdir / "workflows"
        user.mkdir()
        (user / "gk-selftest.js").write_bytes(self.harness_bytes("gk-selftest"))
        out = self.call({"name": "gk-selftest", "args": {"dials": []}})
        self.assertEqual(self.args_of(out), {"dials": [], "seat": "opus-medium"})
        self.fable_off()
        out = self.call({"name": "gk-selftest", "args": {"dials": []}})
        self.assertEqual(self.args_of(out), {"dials": ["nofable"], "seat": "opus-medium"})
        self.assertNotIn("profile", self.args_of(out))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs mkfifo")
    def test_fifo_script_is_not_read(self):
        self.seat("opus")
        wf = self.norepo / ".claude" / "workflows"
        wf.mkdir(parents=True)
        os.mkfifo(str(wf / "gk-review.js"))
        out = self.call({"name": "gk-review"})  # a hang would hit the 30 s timeout
        self.assertEqual(self.args_of(out), {"seat": "opus-high"})

    # -- args shapes ---------------------------------------------------------------------

    def test_args_string_parsed(self):
        self.seat("opus")
        out = self.call({"name": "gk-review", "args": '{"target": "develop..HEAD"}'})
        self.assertEqual(self.args_of(out), {"target": "develop..HEAD", "seat": "opus-high"})

    def test_args_array_untouched(self):
        self.seat("opus")
        repo = self.repo('{"version": 1}')
        for args in (["a"], 3, True, "null", "not json", "[1, 2]"):
            self.assertIsNone(self.call({"name": "gk-review", "args": args}, cwd=repo))

    def test_scriptpath_diagnose_gets_scratchpad(self):
        out = self.call({"scriptPath": "/x/gk-diagnose.js", "args": {"symptom": "s"}},
                        scratchpad_dir="/sp")
        self.assertEqual(self.args_of(out), {"symptom": "s", "scratchpad": "/sp"})

    def test_unusable_cwd_still_injects_seat(self):
        self.seat("opus")
        afile = self.tmp / "afile"
        afile.write_text("x")
        for cwd in (9, str(self.tmp / "missing"), str(afile)):
            d = {"hook_event_name": "PreToolUse", "tool_name": "Workflow", "session_id": "s1",
                 "cwd": cwd, "tool_input": {"name": "gk-build"}}
            proc = self.run_hook(d)
            self.assertEqual(proc.stderr, b"")
            out = json.loads(proc.stdout.decode("utf-8"))
            self.assertEqual(self.args_of(out), {"seat": "opus-high"}, cwd)

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
        self.assertNotIn("seat", args)
        self.assertEqual(args["target"], "x")

    def test_project_absent_gives_root_only(self):
        repo = self.repo()
        self.seat("fable")
        out = self.call({"name": "gk-build"}, cwd=repo)
        self.assertEqual(self.args_of(out),
                         {"seat": "fable-high", "project": {"root": self.root_of(repo)}})
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
        self.v1_user_script()
        self.seat("sonnet")
        repo = self.repo("{nope")
        out = self.call({"name": "gk-build", "args": {"profile": "opus"}}, cwd=repo)
        self.assertEqual(self.args_of(out),
                         {"profile": "opus", "project": {"root": self.root_of(repo)}})
        lines = out["hookSpecificOutput"]["additionalContext"].split("\n")
        self.assertEqual(lines[0], HIGHER % ("opus", "sonnet"))
        self.assertIn("not valid JSON", lines[1])
        self.assertEqual(len(lines), 2)

    def test_project_legal_comes_from_the_file(self):
        repo = self.repo('{"version": 1, "name": "L", "legal": true}')
        root = self.root_of(repo)
        out = self.call({"name": "gk-build"}, cwd=repo)
        self.assertEqual(self.args_of(out)["project"],
                         {"version": 1, "name": "L", "legal": True, "root": root})
        # the caller's project is kept, but its legal is always the file's
        out = self.call({"name": "gk-build", "args": {"project": {"x": 1, "legal": False}}},
                        cwd=repo)
        self.assertEqual(self.args_of(out), {"project": {"x": 1, "legal": True}})
        out = self.call({"name": "gk-build", "args": {"project": {"x": 1}}}, cwd=repo)
        self.assertEqual(self.args_of(out), {"project": {"x": 1, "legal": True}})
        out = self.call({"name": "gk-build", "args": {"project": {"legal": 1}}}, cwd=repo)
        self.assertEqual(self.args_of(out), {"project": {"legal": True}})
        self.assertIsNone(self.call({"name": "gk-build", "args": {"project": {"legal": True}}},
                                    cwd=repo))
        self.assertIsNone(self.call({"name": "gk-build", "args": {"project": None}}, cwd=repo))

    def test_project_legal_removed_when_the_file_has_none(self):
        repo = self.repo('{"version": 1, "name": "P"}')
        for cwd in (repo, self.norepo):
            out = self.call({"name": "gk-build", "args": {"project": {"x": 1, "legal": True}}},
                            cwd=cwd)
            self.assertEqual(self.args_of(out), {"project": {"x": 1}}, cwd)
        bad = self.tmp / "bad"
        bad.mkdir()
        self.git(bad, "init", "-q")
        (bad / ".claude").mkdir()
        (bad / ".claude" / "gk-project.json").write_text('{"version": 1, "legal": true',
                                                          encoding="utf-8")
        out = self.call({"name": "gk-build", "args": {"project": {"legal": True}}}, cwd=bad)
        self.assertEqual(self.args_of(out), {"project": {}})

    # -- changedPaths (gk-review) ------------------------------------------------------

    def commit_file(self, repo, rel, text):
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        self.git(repo, "add", rel)
        self.git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", rel)

    def test_changed_paths_working_tree(self):
        repo = self.repo()
        self.commit_file(repo, ".gitignore", "*.log\n")
        self.commit_file(repo, "src/a.py", "1")
        self.commit_file(repo, "same.txt", "1")
        (repo / "src" / "a.py").write_text("2")
        (repo / "docs").mkdir()
        (repo / "docs" / "new file.md").write_text("n")
        (repo / "x.log").write_text("ignored")
        sub = repo / "src"
        out = self.call({"name": "gk-review", "args": {"target": "working-tree"}}, cwd=sub)
        self.assertEqual(self.args_of(out)["changedPaths"], ["src/a.py", "docs/new file.md"])
        # a caller's list adds paths after git's but never hides one; non-strings are dropped
        out = self.call({"name": "gk-review", "args": {"target": "working-tree",
                                                       "changedPaths": ["z", "src/a.py", 5]}},
                        cwd=repo)
        self.assertEqual(self.args_of(out)["changedPaths"], ["src/a.py", "docs/new file.md", "z"])
        out = self.call({"name": "gk-review", "args": {"target": "working-tree",
                                                       "changedPaths": None}}, cwd=repo)
        self.assertEqual(self.args_of(out)["changedPaths"], ["src/a.py", "docs/new file.md"])
        # when git fails the caller's value is kept as given (the script judges it)
        out = self.call({"name": "gk-review", "args": {"target": "nosuch..HEAD",
                                                       "changedPaths": ["z", 5]}}, cwd=repo)
        self.assertEqual(self.args_of(out)["changedPaths"], ["z", 5])
        out = self.call({"name": "gk-build", "args": {"target": "working-tree"}}, cwd=repo)
        self.assertNotIn("changedPaths", self.args_of(out))
        # outside a repository there is nothing to diff
        self.assertIsNone(self.call({"name": "gk-review", "args": {"target": "working-tree"}}))

    def test_changed_paths_range(self):
        repo = self.repo()
        self.git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
        self.commit_file(repo, "base.txt", "1")
        self.git(repo, "checkout", "-q", "-b", "feat")
        self.commit_file(repo, "feat.txt", "1")
        self.git(repo, "checkout", "-q", "main")
        self.commit_file(repo, "main-only.txt", "1")
        (repo / "dirty.txt").write_text("not in any range")

        def paths(target):
            out = self.call({"name": "gk-review", "args": {"target": target}}, cwd=repo)
            return self.args_of(out).get("changedPaths")
        self.assertEqual(paths("main...feat"), ["feat.txt"])  # from the merge base, as git diff
        # tip to tip: main-only.txt is gone on feat; same content, but no rename is folded in
        self.assertEqual(sorted(paths("main..feat")), ["feat.txt", "main-only.txt"])
        self.assertEqual(paths("main..main"), [])
        self.assertIsNone(paths("nosuch..feat"))

    def test_empty_caller_list_cannot_hide_a_ui_path(self):
        # The judge's path floor reads changedPaths: an empty or partial caller list must not
        # switch it off.
        repo = self.repo()
        self.git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
        self.commit_file(repo, "README.md", "1")
        self.git(repo, "checkout", "-q", "-b", "feat")
        self.commit_file(repo, "frontend/src/app/x.tsx", "ui")
        self.commit_file(repo, "lib/y.py", "y")
        for given in ([], ["lib/y.py"]):
            out = self.call({"name": "gk-review", "args": {"target": "main..feat",
                                                           "changedPaths": given}}, cwd=repo)
            got = self.args_of(out)["changedPaths"]
            self.assertIn("frontend/src/app/x.tsx", got, given)
            self.assertEqual(got, ["frontend/src/app/x.tsx", "lib/y.py"], given)
        # a caller's list that already equals git's changes nothing
        out = self.call({"name": "gk-review", "args": {
            "target": "main..feat", "changedPaths": ["frontend/src/app/x.tsx", "lib/y.py"],
            "project": {}}}, cwd=repo)
        self.assertIsNone(out)

    def test_hostile_target_gets_no_changed_paths(self):
        repo = self.repo()
        self.commit_file(repo, "a.txt", "1")
        for target in ("main..x; rm -rf ~", "$(id)", "--output=/tmp/x", "main..-x",
                       "main..x\n", "a b", ""):
            out = self.call({"name": "gk-review", "args": {"target": target}}, cwd=repo)
            self.assertNotIn("changedPaths", self.args_of(out), target)

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
        self.assertEqual(upd, dict(ti, args={"plan": "p", "seat": "opus-high"}))

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


def load_profile_module():
    spec = importlib.util.spec_from_file_location("gk_profile_under_test", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ChangedPathsTests(unittest.TestCase):
    """In-process: the target check and the git calls behind changedPaths."""

    def setUp(self):
        self.mod = load_profile_module()
        self.top = Path(tempfile.gettempdir())

    def test_target_pattern_is_gk_reviews(self):
        src = (Path(ROOT) / "workflows" / "gk-review.js").read_text(encoding="utf-8")
        m = re.search(r"!/(\^.*?\$)/\.test\(target\)", src)
        self.assertIsNotNone(m, "gk-review.js no longer tests target with a regex literal")
        self.assertEqual(self.mod.TARGET_PATTERN, m.group(1))
        # and the extra rule beside it
        self.assertIn("target.includes('..-')", src)
        self.assertIn("target !== 'working-tree'", src)

    def test_target_check(self):
        ok = ("working-tree", "main..HEAD", "main...feat/x", "HEAD~2", "v1.2^{commit}",
              "HEAD@{1}..HEAD", "a", "origin/develop..HEAD")
        bad = ("main..x; rm -rf ~", "$(id)", "--output=/tmp/x", "main..-x", "-x", "a b",
               "main..x\n", "x\n", "", "\u00e9", None, 5, ["main"],
               "working-tree\n", ".x", "a:b")
        for t in ok:
            self.assertTrue(self.mod.target_ok(t), t)
        for t in bad:
            self.assertFalse(self.mod.target_ok(t), repr(t))

    def test_hostile_targets_run_no_git(self):
        for t in ("main..x; rm -rf ~", "$(id)", "--output=/tmp/x", "main..-x", None):
            with mock.patch.object(gk_common, "_git") as git, \
                    mock.patch.object(gk_common.subprocess, "run") as run:
                self.assertIsNone(self.mod.changed_paths(self.top, t), t)
            git.assert_not_called()
            run.assert_not_called()

    def fake_git(self, outputs):
        calls = []

        def git(start, args):
            calls.append(args)
            out = outputs.pop(0)
            if out is None:
                return None
            code, text = out
            return subprocess.CompletedProcess(["git"] + args, code, text, "")
        return git, calls

    def test_git_arguments(self):
        git, calls = self.fake_git([(0, "a\0b c\0")])
        with mock.patch.object(gk_common, "_git", git):
            self.assertEqual(self.mod.changed_paths(self.top, "main...feat"), ["a", "b c"])
        git_c = ["-c", "core.fsmonitor=false"]
        diff = git_c + ["diff", "--no-ext-diff", "--no-renames", "--name-only", "-z"]
        self.assertEqual(calls, [diff + ["main...feat", "--"]])
        git, calls = self.fake_git([(0, "a\0b\0"), (0, "b\0n\0")])
        with mock.patch.object(gk_common, "_git", git):
            self.assertEqual(self.mod.changed_paths(self.top, "working-tree"), ["a", "b", "n"])
        self.assertEqual(calls, [diff + ["HEAD", "--"],
                                 git_c + ["ls-files", "--others", "--exclude-standard", "-z"]])

    def test_git_failure_gives_none(self):
        for outputs in ([None], [(128, "")], [(0, "a\0"), (1, "")], [(0, "a\0"), None]):
            git, _ = self.fake_git(list(outputs))
            target = "working-tree" if len(outputs) == 2 else "main..x"
            with mock.patch.object(gk_common, "_git", git):
                self.assertIsNone(self.mod.changed_paths(self.top, target), outputs)

    def test_merge_keeps_git_first_and_caps(self):
        m = self.mod._merge_changed_paths
        args = {"changedPaths": ["c", "a", 7, None, "c"]}
        self.assertTrue(m(args, ["a", "b"]))
        self.assertEqual(args["changedPaths"], ["a", "b", "c"])
        args = {"changedPaths": "not a list"}
        self.assertTrue(m(args, ["a"]))
        self.assertEqual(args["changedPaths"], ["a"])
        args = {"changedPaths": "not a list"}
        self.assertFalse(m(args, None))  # git failed: the script judges the caller's value
        self.assertEqual(args["changedPaths"], "not a list")
        args = {}
        self.assertFalse(m(args, None))
        self.assertNotIn("changedPaths", args)
        self.assertTrue(m(args, []))
        self.assertEqual(args["changedPaths"], [])
        args = {"changedPaths": ["x%d" % i for i in range(1500)]}
        self.assertTrue(m(args, ["g%d" % i for i in range(1000)]))
        self.assertEqual(len(args["changedPaths"]), 2000)
        self.assertEqual(args["changedPaths"][999:1001], ["g999", "x0"])

    def test_capped_at_2000(self):
        many = "".join("f%d\0" % i for i in range(2500))
        git, calls = self.fake_git([(0, many), (0, "u\0")])
        with mock.patch.object(gk_common, "_git", git):
            got = self.mod.changed_paths(self.top, "working-tree")
        self.assertEqual(len(got), 2000)
        self.assertEqual(got[:2], ["f0", "f1"])


if __name__ == "__main__":
    unittest.main()
