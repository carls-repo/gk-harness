from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "hooks"))

import gk_model  # noqa: E402

HOOK = os.path.join(ROOT, "hooks", "gk-seat.py")
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_NOSYSTEM": "1",  # GIT_CONFIG_GLOBAL is added by setUpModule
}
_GLOBAL_DIR = None


def setUpModule():
    # Keep the developer's global and system git config (gpgsign, insteadOf, templates) out
    # of both the tests' git calls and the hook's.
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


def load_seat_module():
    spec = importlib.util.spec_from_file_location("gk_seat_under_test", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class SeatHookBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.cdir = self.tmp / "claude"
        self.cdir.mkdir()

    def run_hook(self, payload, raw=None, extra_env=None, timeout=30):
        env = dict(os.environ, GK_CLAUDE_DIR=str(self.cdir), GK_NO_LAUNCH_ARGS="1",
                   **GIT_ENV)
        env.pop("GK_PROFILE", None)
        env.update(extra_env or {})
        data = raw if raw is not None else json.dumps(payload).encode("utf-8")
        return subprocess.run([sys.executable, HOOK], input=data, capture_output=True,
                              env=env, timeout=timeout)

    def out(self, proc):
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.decode("utf-8")) if proc.stdout.strip() else None

    def state(self, sid="s1"):
        p = self.cdir / "state" / "gk-seat" / ("%s.json" % sid)
        return json.loads(p.read_text()) if p.exists() else None

    def start(self, **kw):
        d = {"hook_event_name": "SessionStart", "session_id": "s1", "source": "clear"}
        d.update(kw)
        return self.out(self.run_hook(d))

    def card_of(self, out):
        return out["hookSpecificOutput"]["additionalContext"]


class SeatHookTests(SeatHookBase):
    def test_start_with_model(self):
        out = self.start(model="claude-opus-5-5", effort="xhigh")
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertEqual(self.card_of(out), gk_model.seat_card("claude-opus-5-5", "opus", "xhigh"))
        st = self.state()
        self.assertEqual((st["profile"], st["model"], st["effort"], st["source"]),
                         ("opus", "claude-opus-5-5", "xhigh", "clear"))

    def test_start_effort_object_form(self):
        out = self.start(model="claude-opus-5-5", effort={"level": "xhigh"})
        self.assertEqual(self.card_of(out), gk_model.seat_card("claude-opus-5-5", "opus", "xhigh"))
        self.assertEqual(self.state()["effort"], "xhigh")

    def test_start_without_model_keeps_state(self):
        self.start(model="claude-sonnet-5-5", effort="high")
        out = self.start(source="clear", effort={"level": "high"})
        self.assertEqual(self.card_of(out), gk_model.seat_card("claude-sonnet-5-5", "sonnet", "high"))
        self.assertEqual(self.state()["profile"], "sonnet")

    def test_start_without_model_reads_transcript(self):
        tp = self.tmp / "t.jsonl"
        tp.write_text(json.dumps({"type": "assistant",
                                  "message": {"model": "claude-fable-5-1"}}) + "\n")
        out = self.start(transcript_path=str(tp), effort="high")
        self.assertEqual(self.card_of(out), gk_model.seat_card("claude-fable-5-1", "fable", "high"))
        self.assertEqual(self.state()["profile"], "fable")

    def test_start_nothing_known(self):
        out = self.start()
        self.assertEqual(self.card_of(out), gk_model.seat_card(None, None, None))
        self.assertIn("not reported", self.card_of(out))
        self.assertIsNone(self.state())

    def test_start_model_default_is_treated_as_missing(self):
        out = self.start(model="default", effort="high")
        self.assertIn("not reported", self.card_of(out))
        self.assertIsNone(self.state())

    def test_switch_updates_state(self):
        self.start(model="claude-opus-5-5", effort="xhigh")
        out = self.out(self.run_hook({
            "hook_event_name": "PostModelSwitch", "session_id": "s1",
            "to_model": "claude-sonnet-5-5", "from_model": "claude-opus-5-5",
            "effort": {"level": "high"}}))
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PostModelSwitch")
        self.assertEqual(self.card_of(out), gk_model.seat_card("claude-sonnet-5-5", "sonnet", "high"))
        st = self.state()
        self.assertEqual((st["profile"], st["effort"]), ("sonnet", "high"))

    def test_switch_without_effort_uses_stored(self):
        self.start(model="claude-opus-5-5", effort="xhigh")
        out = self.out(self.run_hook({"hook_event_name": "PostModelSwitch", "session_id": "s1",
                                      "to_model": "claude-fable-5-1"}))
        self.assertEqual(self.card_of(out), gk_model.seat_card("claude-fable-5-1", "fable", "xhigh"))

    def test_switch_falls_back_to_requested_model(self):
        self.start(model="claude-opus-5-5", effort="xhigh")
        out = self.out(self.run_hook({"hook_event_name": "PostModelSwitch", "session_id": "s1",
                                      "to_model": "default", "requested_model": "sonnet",
                                      "effort": {"level": "high"}}))
        self.assertEqual(self.card_of(out), gk_model.seat_card("sonnet", "sonnet", "high"))
        self.assertEqual(self.state()["profile"], "sonnet")

    def test_switch_unmapped_clears_state(self):
        for to_model, requested in (("default", "<synthetic>"), (None, None),
                                    ("gw-model-7", "default")):
            self.start(model="claude-opus-5-5", effort="xhigh")
            self.assertEqual(self.state()["profile"], "opus")
            d = {"hook_event_name": "PostModelSwitch", "session_id": "s1",
                 "effort": {"level": "high"}}
            if to_model is not None:
                d["to_model"] = to_model
            if requested is not None:
                d["requested_model"] = requested
            out = self.out(self.run_hook(d))
            self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PostModelSwitch")
            self.assertEqual(self.card_of(out), gk_model.seat_card(None, None, None))
            self.assertIsNone(self.state(), (to_model, requested))
            self.assertFalse((self.cdir / "state" / "gk-seat" / "s1.json").exists())

    def test_switch_unmapped_without_state(self):
        out = self.out(self.run_hook({"hook_event_name": "PostModelSwitch", "session_id": "s1",
                                      "to_model": "default"}))
        self.assertEqual(self.card_of(out), gk_model.seat_card(None, None, None))
        self.assertIsNone(self.state())

    def test_input_model_beats_stored_model(self):
        self.start(model="claude-sonnet-5-5", effort="high")
        out = self.start(model="claude-opus-5-5", effort="high")
        self.assertEqual(self.card_of(out), gk_model.seat_card("claude-opus-5-5", "opus", "high"))
        self.assertEqual(self.state()["profile"], "opus")

    def test_stored_model_beats_transcript(self):
        self.start(model="claude-sonnet-5-5", effort="high")
        tp = self.tmp / "t.jsonl"
        tp.write_text(json.dumps({"type": "assistant",
                                  "message": {"model": "claude-fable-5-1"}}) + "\n")
        out = self.start(transcript_path=str(tp), effort="high")
        self.assertEqual(self.card_of(out), gk_model.seat_card("claude-sonnet-5-5", "sonnet", "high"))
        self.assertEqual(self.state()["profile"], "sonnet")

    def test_other_events_noop(self):
        for ev in ("PreToolUse", "Stop", "UserPromptSubmit", None):
            d = {"session_id": "s1", "model": "claude-opus-5-5"}
            if ev:
                d["hook_event_name"] = ev
            proc = self.run_hook(d)
            self.assertEqual((proc.returncode, proc.stdout), (0, b""))
        self.assertIsNone(self.state())

    def test_garbage_input(self):
        cases = [b"", b"[]", b'{"session_id": "../x", "hook_event_name": "SessionStart"}',
                 bytes(range(256)), b"\xff\xfe{"]
        for raw in cases:
            proc = self.run_hook(None, raw=raw)
            self.assertEqual((proc.returncode, proc.stdout), (0, b""), raw)

    def test_output_under_cap(self):
        out = self.start(model="claude-opus-5-5" + "x" * 5000, effort="y" * 5000)
        self.assertLess(len(json.dumps(out)), 10000)


class LaunchFallbackTests(SeatHookBase):
    """In-process: launch_args is patched, so the hook module is run through handle()."""

    def run_handle(self, payload, launch):
        import contextlib
        import io
        mod = load_seat_module()
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"GK_CLAUDE_DIR": str(self.cdir)}), \
                mock.patch.object(gk_model, "launch_args", launch), \
                mock.patch.object(mod, "update_notice", return_value=None), \
                contextlib.redirect_stdout(buf):
            mod.handle(dict({"hook_event_name": "SessionStart", "session_id": "s1"}, **payload))
        return json.loads(buf.getvalue())["hookSpecificOutput"]["additionalContext"]

    def test_startup_takes_model_and_effort_from_launch(self):
        card = self.run_handle({"source": "startup"}, lambda: ("claude-opus-5-5", "xhigh"))
        self.assertEqual(card, gk_model.seat_card("claude-opus-5-5", "opus", "xhigh"))
        st = self.state()
        self.assertEqual((st["model"], st["profile"], st["effort"]),
                         ("claude-opus-5-5", "opus", "xhigh"))

    def _boom(self):
        def boom():
            raise AssertionError("launch_args called")
        return boom

    def test_clear_and_compact_never_read_launch(self):
        for source in ("clear", "compact"):
            card = self.run_handle({"source": source}, self._boom())
            self.assertEqual(card, gk_model.seat_card(None, None, None), source)
            self.assertIsNone(self.state(), source)

    def test_resume_reads_launch(self):
        card = self.run_handle({"source": "resume"}, lambda: ("claude-opus-5-5", "xhigh"))
        self.assertEqual(card, gk_model.seat_card("claude-opus-5-5", "opus", "xhigh"))

    def test_input_model_beats_launch_model(self):
        card = self.run_handle({"source": "startup", "model": "claude-sonnet-5-5"},
                               lambda: ("claude-opus-5-5", "high"))
        self.assertEqual(card, gk_model.seat_card("claude-sonnet-5-5", "sonnet", "high"))

    def test_stored_model_beats_launch_model(self):
        with mock.patch.dict(os.environ, {"GK_CLAUDE_DIR": str(self.cdir)}):
            gk_model.save_seat("s1", "claude-sonnet-5-5", "sonnet", "high", "startup")
        card = self.run_handle({"source": "startup"}, lambda: ("claude-opus-5-5", None))
        self.assertEqual(card, gk_model.seat_card("claude-sonnet-5-5", "sonnet", "high"))

    def _transcript(self, model):
        path = self.cdir / "t.jsonl"
        path.write_text(json.dumps({"type": "assistant", "message": {"model": model}}) + "\n")
        return str(path)

    def test_launch_model_beats_transcript_model(self):
        card = self.run_handle({"source": "startup",
                                "transcript_path": self._transcript("claude-sonnet-5-5")},
                               lambda: ("claude-opus-5-5", "high"))
        self.assertEqual(card, gk_model.seat_card("claude-opus-5-5", "opus", "high"))

    def test_empty_launch_falls_back_to_transcript(self):
        card = self.run_handle({"source": "startup",
                                "transcript_path": self._transcript("claude-sonnet-5-5")},
                               lambda: (None, None))
        self.assertEqual(card, gk_model.seat_card("claude-sonnet-5-5", "sonnet", None))

    def test_full_input_never_reads_launch(self):
        card = self.run_handle({"source": "startup", "model": "claude-opus-5-5",
                                "effort": "high"}, self._boom())
        self.assertEqual(card, gk_model.seat_card("claude-opus-5-5", "opus", "high"))

    def test_startup_effort_only_from_launch(self):
        card = self.run_handle({"source": "startup", "model": "claude-sonnet-5-5"},
                               lambda: (None, "high"))
        self.assertEqual(card, gk_model.seat_card("claude-sonnet-5-5", "sonnet", "high"))
        self.assertIn("at high", card)

    def test_launch_called_at_most_once(self):
        calls = []

        def once():
            calls.append(1)
            return (None, None)
        self.run_handle({"source": "startup"}, once)
        self.assertEqual(len(calls), 1)


class UpdateCheckTests(SeatHookBase):
    def make_repos(self):
        work, bare, root = self.tmp / "work", self.tmp / "origin.git", self.tmp / "root"
        work.mkdir()
        git(work, "init", "-q", "-b", "main")
        (work / "a").write_text("1")
        git(work, "add", "a")
        git(work, "commit", "-q", "-m", "one")
        git(self.tmp, "clone", "-q", "--bare", str(work), str(bare))
        git(self.tmp, "clone", "-q", str(bare), str(root))
        (work / "a").write_text("2")
        git(work, "commit", "-q", "-am", "two")
        git(work, "push", "-q", str(bare), "main")
        self.bare, self.root = bare, root
        (self.cdir / "gk-harness.json").write_text(json.dumps(
            {"version": "0.1.0", "root": str(root), "python": sys.executable,
             "installed_at": "2026-10-28T00:00:00Z"}))

    def expected_line(self):
        r = str(self.root)
        if os.name == "nt":
            return ('gk-harness update available: run powershell -NoProfile -ExecutionPolicy '
                    'Bypass -File "%s/install.ps1" update' % r)
        return "gk-harness update available: run %s/install.sh update" % r

    def stamp(self):
        return json.loads((self.cdir / "state" / "gk-harness-update.json").read_text())

    def test_update_check_once_a_day(self):
        self.make_repos()
        out = self.start(source="startup", model="claude-opus-5-5", effort="high")
        card = self.card_of(out)
        self.assertTrue(card.startswith(gk_model.seat_card("claude-opus-5-5", "opus", "high")))
        self.assertIn(self.expected_line(), card)
        self.assertTrue(self.stamp()["available"])
        # Origin is now unreachable: a fresh check would find nothing, so the line can only
        # come from the stamp.
        self.bare.rename(self.tmp / "gone.git")
        out = self.start(source="startup", model="claude-opus-5-5", effort="high")
        self.assertIn(self.expected_line(), self.card_of(out))
        self.assertTrue(self.stamp()["available"])

    @unittest.skipIf(os.name == "nt", "POSIX only: PATH shim")
    def test_second_start_runs_no_git(self):
        self.make_repos()
        self.start(source="startup", model="claude-opus-5-5", effort="high")
        shim = self.tmp / "shim"
        shim.mkdir()
        log = self.tmp / "git.log"
        (shim / "git").write_text('#!/bin/sh\necho "$@" >> "%s"\nexit 1\n' % log)
        (shim / "git").chmod(0o755)
        proc = self.run_hook({"hook_event_name": "SessionStart", "session_id": "s1",
                              "source": "startup", "model": "claude-opus-5-5"},
                             extra_env={"PATH": "%s%s%s" % (shim, os.pathsep, os.environ["PATH"])})
        self.assertIn(self.expected_line(), self.card_of(self.out(proc)))
        self.assertFalse(log.exists())

    def test_stale_stamp_rechecks(self):
        self.make_repos()
        self.start(source="startup", model="claude-opus-5-5")
        st = self.cdir / "state" / "gk-harness-update.json"
        st.write_text(json.dumps({"checked": "2020-01-01T00:00:00Z", "available": True}))
        self.bare.rename(self.tmp / "gone.git")  # recheck fails -> available false
        out = self.start(source="startup", model="claude-opus-5-5")
        self.assertNotIn("update available", self.card_of(out))
        self.assertFalse(self.stamp()["available"])
        self.assertNotEqual(self.stamp()["checked"], "2020-01-01T00:00:00Z")

    def test_up_to_date_gives_no_line(self):
        self.make_repos()
        git(self.root, "pull", "-q")
        out = self.start(source="startup", model="claude-opus-5-5")
        self.assertNotIn("update available", self.card_of(out))
        self.assertFalse(self.stamp()["available"])

    def test_only_on_startup(self):
        self.make_repos()
        out = self.start(source="resume", model="claude-opus-5-5")
        self.assertNotIn("update available", self.card_of(out))
        self.assertFalse((self.cdir / "state" / "gk-harness-update.json").exists())

    def test_switch_never_runs_update_check(self):
        self.make_repos()
        for source in ("startup", None):
            d = {"hook_event_name": "PostModelSwitch", "session_id": "s1",
                 "to_model": "claude-opus-5-5", "effort": {"level": "high"}}
            if source:
                d["source"] = source
            out = self.out(self.run_hook(d))
            self.assertEqual(self.card_of(out), gk_model.seat_card("claude-opus-5-5", "opus", "high"))
            self.assertNotIn("update available", self.card_of(out))
        self.assertFalse((self.cdir / "state" / "gk-harness-update.json").exists())

    def test_no_config_skips_silently(self):
        out = self.start(source="startup", model="claude-opus-5-5")
        self.assertNotIn("update available", self.card_of(out))
        self.assertFalse((self.cdir / "state" / "gk-harness-update.json").exists())

    def test_update_check_offline(self):
        self.make_repos()
        self.bare.rename(self.tmp / "gone.git")
        out = self.start(source="startup", model="claude-opus-5-5")
        self.assertNotIn("update available", self.card_of(out))
        self.assertFalse(self.stamp()["available"])

    def test_update_check_hanging_remote(self):
        self.make_repos()
        git(self.root, "remote", "set-url", "origin", "ssh://example.invalid/x.git")
        ssh = self.tmp / "slow_ssh.py"
        ssh.write_text("import sys, time\nsys.stderr.write('Enter passphrase: ')\n"
                       "sys.stderr.flush()\ntime.sleep(20)\n")
        cmd = '"%s" "%s"' % (Path(sys.executable).as_posix(), ssh.as_posix())
        t0 = time.monotonic()
        proc = self.run_hook({"hook_event_name": "SessionStart", "session_id": "s1",
                              "source": "startup", "model": "claude-opus-5-5"},
                             extra_env={"GIT_SSH_COMMAND": cmd}, timeout=15)
        self.assertLessEqual(time.monotonic() - t0, 4.5)
        self.assertNotIn("update available", self.card_of(self.out(proc)))
        self.assertFalse(self.stamp()["available"])


class GitPlumbingTests(unittest.TestCase):
    """In-process tests of the update check's git environment and deadline."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        self.mod = load_seat_module()

    def env_for(self, environ):
        base = {k: v for k, v in os.environ.items()
                if k not in ("GIT_SSH", "GIT_SSH_COMMAND")}
        base.update(GIT_ENV)
        base.update(environ)
        with mock.patch.dict(os.environ, base, clear=True):
            return self.mod.git_env(str(self.repo), self.mod._now() + 4)

    def test_default_ssh_when_user_has_none(self):
        env = self.env_for({})
        self.assertEqual(env["GIT_SSH_COMMAND"], self.mod.DEFAULT_SSH)
        self.assertEqual(env["GIT_NO_LAZY_FETCH"], "1")

    def test_user_git_ssh_is_kept(self):
        env = self.env_for({"GIT_SSH": "plink.exe"})
        self.assertNotIn("GIT_SSH_COMMAND", env)
        self.assertEqual(env["GIT_SSH"], "plink.exe")

    def test_user_git_ssh_command_is_kept(self):
        env = self.env_for({"GIT_SSH_COMMAND": "my-ssh -v"})
        self.assertEqual(env["GIT_SSH_COMMAND"], "my-ssh -v")

    def test_core_ssh_command_is_kept(self):
        git(self.repo, "config", "core.sshCommand", "my-ssh -i key")
        env = self.env_for({})
        self.assertNotIn("GIT_SSH_COMMAND", env)

    def test_one_deadline_for_the_whole_check(self):
        clock = [100.0]
        calls = []

        def fake_run(argv, **kw):
            calls.append((argv[3], kw["timeout"]))
            clock[0] += 1.5
            if argv[3] == "ls-remote":
                kw["stdout"].write(b"0123abcd\tHEAD\n")
            return subprocess.CompletedProcess(argv, 1 if argv[3] == "config" else 0)

        with mock.patch.object(self.mod, "_now", lambda: clock[0]), \
                mock.patch.object(self.mod.subprocess, "run", fake_run), \
                mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GIT_SSH", None)
            os.environ.pop("GIT_SSH_COMMAND", None)
            self.assertFalse(self.mod.origin_is_ahead(str(self.repo)))
        # config at t=0 (3 s), ls-remote at t=1.5 (2.5 s left), cat-file at t=3 (1 s left);
        # at t=4.5 the budget is spent, so merge-base never runs.
        self.assertEqual(calls, [("config", 3), ("ls-remote", 2.5), ("cat-file", 1.0)])

    def test_spent_deadline_runs_no_git(self):
        with mock.patch.object(self.mod.subprocess, "run",
                               side_effect=AssertionError("git ran")):
            self.assertIsNone(self.mod._run_git(str(self.repo), ["status"], {},
                                                self.mod._now() - 1))


if __name__ == "__main__":
    unittest.main()
