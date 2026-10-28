from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "hooks"))

import gk_model  # noqa: E402

HOOK = os.path.join(ROOT, "hooks", "gk-seat.py")
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}


def git(cwd, *args):
    env = dict(os.environ, **GIT_ENV)
    subprocess.run(["git", "-C", str(cwd)] + list(args), check=True, capture_output=True,
                   stdin=subprocess.DEVNULL, env=env)


class SeatHookBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.cdir = self.tmp / "claude"
        self.cdir.mkdir()

    def run_hook(self, payload, raw=None, extra_env=None, timeout=30):
        env = dict(os.environ, GK_CLAUDE_DIR=str(self.cdir))
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


class UpdateCheckTests(SeatHookBase):
    def make_repos(self):
        work, bare, root = self.tmp / "work", self.tmp / "origin.git", self.tmp / "root"
        work.mkdir()
        git(work, "init", "-q", "-b", "main")
        (work / "a").write_text("1")
        git(work, "add", "a")
        git(work, "commit", "-q", "-m", "one")
        subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True,
                       capture_output=True)
        subprocess.run(["git", "clone", "-q", str(bare), str(root)], check=True,
                       capture_output=True)
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
        shutil.rmtree(self.bare)
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
        shutil.rmtree(self.bare)  # recheck fails -> available false
        out = self.start(source="startup", model="claude-opus-5-5")
        self.assertNotIn("update available", self.card_of(out))
        self.assertFalse(self.stamp()["available"])
        self.assertNotEqual(self.stamp()["checked"], "2020-01-01T00:00:00Z")

    def test_up_to_date_gives_no_line(self):
        self.make_repos()
        subprocess.run(["git", "-C", str(self.root), "pull", "-q"], check=True,
                       capture_output=True, env=dict(os.environ, **GIT_ENV))
        out = self.start(source="startup", model="claude-opus-5-5")
        self.assertNotIn("update available", self.card_of(out))
        self.assertFalse(self.stamp()["available"])

    def test_only_on_startup(self):
        self.make_repos()
        out = self.start(source="resume", model="claude-opus-5-5")
        self.assertNotIn("update available", self.card_of(out))
        self.assertFalse((self.cdir / "state" / "gk-harness-update.json").exists())

    def test_no_config_skips_silently(self):
        out = self.start(source="startup", model="claude-opus-5-5")
        self.assertNotIn("update available", self.card_of(out))
        self.assertFalse((self.cdir / "state" / "gk-harness-update.json").exists())

    def test_update_check_offline(self):
        self.make_repos()
        shutil.rmtree(self.bare)
        t0 = time.monotonic()
        out = self.start(source="startup", model="claude-opus-5-5")
        self.assertLessEqual(time.monotonic() - t0, 4)
        self.assertNotIn("update available", self.card_of(out))
        self.assertFalse(self.stamp()["available"])


if __name__ == "__main__":
    unittest.main()
