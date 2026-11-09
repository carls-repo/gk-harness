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
from unittest import mock

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))

import gk_model  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "transcripts"


class ModelTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = mock.patch.dict(os.environ, {"GK_CLAUDE_DIR": self.tmp})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("GK_PROFILE", None)

    def test_profile_for_model_table(self):
        pairs = [
            ("claude-fable-5-1", "fable"), ("claude-opus-5-5", "opus"),
            ("claude-sonnet-5-5", "sonnet"), ("claude-opus-5-5[1m]", "opus"),
            ("opus", "opus"), ("fable", "fable"),
            ("us.anthropic.claude-opus-5-5-v1:0", "opus"),
            ("Claude-OPUS-5-5", "opus"), ("claude-haiku-5-5", "sonnet"), ("claude-next-7", "sonnet"),
            ("default", None), ("", None), (None, None), ("<synthetic>", None), (42, None),
        ]
        for inp, want in pairs:
            self.assertEqual(gk_model.profile_for_model(inp), want, inp)

    def test_in_lineup(self):
        self.assertTrue(gk_model.in_lineup("claude-opus-5-5"))
        self.assertFalse(gk_model.in_lineup("claude-haiku-5-5"))
        self.assertFalse(gk_model.in_lineup(None))

    def test_effort_of(self):
        self.assertEqual(gk_model.effort_of({"effort": "xhigh"}), "xhigh")
        self.assertEqual(gk_model.effort_of({"effort": {"level": "high"}}), "high")
        self.assertIsNone(gk_model.effort_of({"effort": {"level": 3}}))
        self.assertIsNone(gk_model.effort_of({"effort": 5}))
        self.assertIsNone(gk_model.effort_of({}))

    def test_transcript_last_assistant_model(self):
        self.assertEqual(gk_model.model_from_transcript(FIX / "opus_then_sonnet.jsonl"),
                         "claude-sonnet-5-5")

    def test_transcript_skips_synthetic_and_sidechain(self):
        self.assertEqual(gk_model.model_from_transcript(FIX / "synthetic_sidechain.jsonl"),
                         "claude-opus-5-5")

    def test_transcript_tail_only(self):
        p = Path(self.tmp) / "big.jsonl"
        first = '{"type":"assistant","isSidechain":false,"message":{"model":"claude-opus-5-5"}}\n'
        filler = '{"type":"user","message":{"content":"' + "x" * 900 + '"}}\n'
        with open(p, "w") as f:
            f.write(first)
            for _ in range(3 * 1024 * 1024 // len(filler) + 1):
                f.write(filler)
        self.assertGreater(p.stat().st_size, 3 * 1024 * 1024)
        t = time.monotonic()
        self.assertIsNone(gk_model.model_from_transcript(p))
        self.assertLess(time.monotonic() - t, 0.2)

    def test_transcript_skips_odd_message_shapes(self):
        p = Path(self.tmp) / "odd.jsonl"
        good = '{"type":"assistant","message":{"model":"claude-opus-5-5"}}\n'
        odd = ['{"type":"assistant"}\n', '{"type":"assistant","message":null}\n',
               '{"type":"assistant","message":{"model":42}}\n',
               '{"type":"assistant","message":"claude-fable-5-1"}\n']
        p.write_text(good + "".join(odd), encoding="utf-8")
        self.assertEqual(gk_model.model_from_transcript(p), "claude-opus-5-5")

    def test_transcript_window_inside_multibyte_char(self):
        p = Path(self.tmp) / "mb.jsonl"
        a = '{"type":"assistant","message":{"model":"claude-opus-5-5"}}\n'
        u = '{"type":"user","message":{"content":"' + "\u00e9" * 40 + '"}}\n'
        data = (a + u).encode("utf-8")
        start = len(a.encode()) + len(b'{"type":"user","message":{"content":"') + 1  # mid-char
        self.assertEqual(data[start] & 0xC0, 0x80)
        p.write_bytes(data)
        self.assertEqual(gk_model.model_from_transcript(p, max_bytes=len(data) - start),
                         None)
        data2 = data + a.replace("opus", "fable").encode()
        p.write_bytes(data2)
        self.assertEqual(gk_model.model_from_transcript(p, max_bytes=len(data2) - start),
                         "claude-fable-5-5")

    def test_transcript_cut_first_line_is_dropped(self):
        # the remainder of the cut line is itself valid JSON; it must not be believed
        p = Path(self.tmp) / "cut.jsonl"
        frag = '{"type":"assistant","message":{"model":"claude-fable-5-1"}}'
        data = ("0" + frag + "\n").encode()
        p.write_bytes(data)
        self.assertIsNone(gk_model.model_from_transcript(p, max_bytes=len(frag) + 1))

    def test_transcript_window_on_line_boundary_keeps_first_line(self):
        p = Path(self.tmp) / "edge.jsonl"
        a = '{"type":"assistant","message":{"model":"claude-opus-5-5"}}\n'
        pad = '{"type":"user","message":{"content":"zz"}}\n'
        p.write_bytes((pad + a).encode("utf-8"))  # bytes: no \r\n on Windows
        self.assertEqual(gk_model.model_from_transcript(p, max_bytes=len(a)), "claude-opus-5-5")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX only")
    def test_transcript_fifo_returns_none_promptly(self):
        p = Path(self.tmp) / "fifo"
        os.mkfifo(str(p))
        t = time.monotonic()
        self.assertIsNone(gk_model.model_from_transcript(p))
        self.assertLess(time.monotonic() - t, 1.0)

    def test_transcript_missing_or_garbage(self):
        self.assertIsNone(gk_model.model_from_transcript(Path(self.tmp) / "nope.jsonl"))
        self.assertIsNone(gk_model.model_from_transcript(None))
        g = Path(self.tmp) / "g.jsonl"
        g.write_bytes(b"\xff\xfe not json\n{broken\n[1,2]\n")
        self.assertIsNone(gk_model.model_from_transcript(g))

    def test_non_path_transcript_never_touches_fds(self):
        for bad in (1, 2, True, 0, 3.5, [], {}):
            self.assertIsNone(gk_model.model_from_transcript(bad))
            self.assertIsNone(gk_model.resolve_profile({"session_id": "x1", "transcript_path": bad}))
        for fd in (0, 1, 2):
            os.fstat(fd)

    def test_seat_roundtrip(self):
        self.assertTrue(gk_model.save_seat("s1", "claude-opus-5-5", "opus", "high", "SessionStart"))
        rec = gk_model.load_seat("s1")
        self.assertEqual({k: rec[k] for k in ("model", "profile", "effort", "source")},
                         {"model": "claude-opus-5-5", "profile": "opus", "effort": "high",
                          "source": "SessionStart"})
        self.assertRegex(rec["updated"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(gk_model.seat_path("s1"), Path(self.tmp) / "state" / "gk-seat" / "s1.json")

    def test_seat_bad_sid_and_bad_record(self):
        self.assertFalse(gk_model.save_seat("../x", "m", "opus", None, "s"))
        self.assertIsNone(gk_model.load_seat("../x"))
        self.assertIsNone(gk_model.load_seat("absent"))
        gk_model.save_seat("s2", "m", "haiku", None, "s")
        self.assertIsNone(gk_model.load_seat("s2"))
        gk_model.seat_path("s3").write_text("[1]")
        self.assertIsNone(gk_model.load_seat("s3"))

    def test_seat_card_exact(self):
        c = gk_model.seat_card
        self.assertEqual(
            c("claude-opus-5-5", "opus", "xhigh"),
            "gk-harness seat: claude-opus-5-5 at xhigh \u2192 Mode B (profile 'opus'). "
            "Pass profile: 'opus' on every Workflow call. The policy expects effort high.")
        high = c("claude-opus-5-5", "opus", "high")
        self.assertNotIn("The policy expects", high)
        self.assertTrue(high.endswith("on every Workflow call."))
        none = c("claude-opus-5-5", "opus", None)
        self.assertIn("at unknown effort", none)
        self.assertIn("The policy expects effort high.", none)
        self.assertTrue(c("claude-next-7", "sonnet", "high").endswith(
            " claude-next-7 is not in the lineup, so Mode C rules apply; "
            "say so to Carl in your first reply."))
        self.assertEqual(
            c(None, None, None),
            "gk-harness seat: the model was not reported. Read the model named in your system "
            "prompt, apply that mode, and pass profile explicitly on every Workflow call.")
        for model in (None, "", "default", "<synthetic>", 42):
            card = c(model, "opus", "high")
            self.assertIn("an unreported model at high", card, model)
            self.assertNotIn("None", card)
            self.assertNotIn("not in the lineup", card)
        self.assertIn("The policy expects", c(None, "opus", "low"))
        self.assertEqual(c(None, ["opus"], "high"), c(None, None, None))
        self.assertEqual(c("x", {}, "high"), c(None, None, None))
        long_model = "claude-next-" + "m" * 300
        card = c(long_model, "sonnet", "e" * 300)
        self.assertLess(len(card), 600)
        self.assertIn("\u2026", card)
        self.assertNotIn("m" * 100, card)
        self.assertNotIn("e" * 100, card)
        self.assertLess(len(c(long_model, "sonnet", "high")), 600)
        for card in (c("claude-fable-5-1", "fable", "xhigh"), c("claude-next-7", "sonnet", None),
                     c(None, None, None)):
            self.assertLess(len(card), 600)

    def test_resolve_profile_order(self):
        data = {"session_id": "r1", "transcript_path": str(FIX / "opus_then_sonnet.jsonl")}
        self.assertIsNone(gk_model.resolve_profile({}))
        os.environ["GK_PROFILE"] = "fable"
        self.assertEqual(gk_model.resolve_profile({}), "fable")
        os.environ["GK_PROFILE"] = "bogus"
        self.assertIsNone(gk_model.resolve_profile({}))
        os.environ["GK_PROFILE"] = "fable"
        self.assertEqual(gk_model.resolve_profile(data), "sonnet")
        gk_model.save_seat("r1", "claude-opus-5-5", "opus", None, "s")
        self.assertEqual(gk_model.resolve_profile(data), "opus")


DESKTOP_ARGV = ["/home/carls/.claude/remote/ccd-cli/2.1.317-a5bf08beebc8", "--output-format",
                "stream-json", "--verbose", "--input-format", "stream-json", "--effort=xhigh",
                "--model=claude-opus-5-5"]
BASH_ARGV = ["bash", "-c", "/usr/bin/python3", "/home/carls/.claude/hooks/gk-harness/gk-seat.py"]
PY_ARGV = ["/usr/bin/python3", "/home/carls/.claude/hooks/gk-harness/gk-seat.py"]
TERM_ARGV = ["/home/carls/.local/bin/claude"]


class LaunchFlagsTest(unittest.TestCase):
    def test_parse_launch_flags(self):
        cases = [
            (DESKTOP_ARGV, ("claude-opus-5-5", "xhigh")),
            (["claude", "--model", "sonnet", "--effort", "high"], ("sonnet", "high")),
            (["claude"], (None, None)),
            (["claude", "--model=gpt-4o"], (None, None)),
            (["claude", "--effort=turbo"], (None, None)),
            (["claude", "--model"], (None, None)),
            (["claude", "--effort=XHIGH"], (None, "xhigh")),
        ]
        for argv, want in cases:
            self.assertEqual(gk_model.parse_launch_flags(argv), want, argv)

    def _table(self, rows):
        # rows: {pid: (ppid, argv)}
        return lambda pid: rows.get(pid)

    def _launch(self, rows, ppid=300, **kw):
        with mock.patch.object(gk_model.sys, "platform", "linux"), \
                mock.patch.object(gk_model.os, "getppid", return_value=ppid), \
                mock.patch.object(gk_model, "_proc_info", side_effect=self._table(rows)):
            return gk_model.launch_args(**kw)

    def test_desktop_chain(self):
        rows = {300: (200, PY_ARGV), 200: (100, BASH_ARGV), 100: (1, DESKTOP_ARGV)}
        self.assertEqual(self._launch(rows), ("claude-opus-5-5", "xhigh"))

    def test_stops_at_first_claude_code(self):
        rows = {300: (200, PY_ARGV), 200: (150, BASH_ARGV), 150: (100, TERM_ARGV),
                100: (50, BASH_ARGV), 50: (1, DESKTOP_ARGV)}
        self.assertEqual(self._launch(rows), (None, None))

    def test_no_claude_code(self):
        rows = {300: (200, PY_ARGV), 200: (1, BASH_ARGV)}
        self.assertEqual(self._launch(rows), (None, None))

    def test_depth_limit(self):
        rows = {300: (200, PY_ARGV), 200: (100, BASH_ARGV), 100: (1, DESKTOP_ARGV)}
        self.assertEqual(self._launch(rows, max_depth=2), (None, None))

    def test_reader_fails_partway(self):
        rows = {300: (200, PY_ARGV)}
        self.assertEqual(self._launch(rows), (None, None))

    def _reads(self, rows, ppid=300):
        reads = []

        def reader(pid):
            reads.append(pid)
            return rows.get(pid)
        with mock.patch.object(gk_model.sys, "platform", "linux"), \
                mock.patch.object(gk_model.os, "getppid", return_value=ppid), \
                mock.patch.object(gk_model, "_proc_info", side_effect=reader):
            result = gk_model.launch_args()
        return result, reads

    def test_self_loop_stops_after_one_read(self):
        result, reads = self._reads({300: (300, PY_ARGV)})
        self.assertEqual((result, reads), ((None, None), [300]))

    def test_two_cycle_stops_after_two_reads(self):
        result, reads = self._reads({300: (200, PY_ARGV), 200: (300, BASH_ARGV)})
        self.assertEqual((result, reads), ((None, None), [300, 200]))

    def test_pid_one_and_zero_are_never_read(self):
        rows = {300: (1, PY_ARGV), 1: (0, DESKTOP_ARGV), 0: (0, DESKTOP_ARGV)}
        self.assertEqual(self._reads(rows), ((None, None), [300]))
        self.assertEqual(self._reads(rows, ppid=1), ((None, None), []))
        self.assertEqual(self._reads(rows, ppid=0), ((None, None), []))

    def test_node_cli_is_claude_code(self):
        node = ["/usr/bin/node", "/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js",
                "--model=claude-sonnet-5-5"]
        rows = {300: (200, PY_ARGV), 200: (100, node), 100: (1, DESKTOP_ARGV)}
        self.assertEqual(self._launch(rows), ("claude-sonnet-5-5", None))
        other = ["node", "/srv/app/server.js", "--model=claude-sonnet-5-5"]
        rows = {300: (200, PY_ARGV), 200: (1, other)}
        self.assertEqual(self._launch(rows), (None, None))

    def test_macos_line_parser(self):
        ppid, argv = gk_model._parse_ps_line(
            " 1234 /Applications/Claude.app/Contents/Resources/claude --output-format "
            "stream-json --model=claude-sonnet-5-5")
        self.assertEqual(ppid, 1234)
        self.assertTrue(gk_model._is_claude_argv(argv))
        self.assertEqual(gk_model.parse_launch_flags(argv), ("claude-sonnet-5-5", None))
        ppid, argv = gk_model._parse_ps_line(
            "  99 /bin/bash -c /usr/bin/python3 /Users/c/.claude/hooks/gk-harness/gk-seat.py")
        self.assertEqual(ppid, 99)
        self.assertFalse(gk_model._is_claude_argv(argv))
        ppid, argv = gk_model._parse_ps_line(
            "  7 /usr/local/bin/node /usr/lib/node_modules/@anthropic-ai/claude-code/cli.js "
            "--model claude-opus-5-5 --effort high")
        self.assertTrue(gk_model._is_claude_argv(argv))
        self.assertEqual(gk_model.parse_launch_flags(argv), ("claude-opus-5-5", "high"))
        ppid, argv = gk_model._parse_ps_line(
            "  8 node /srv/app/server.js --model claude-opus-5-5")
        self.assertFalse(gk_model._is_claude_argv(argv))
        self.assertIsNone(gk_model._parse_ps_line("garbage"))

    def test_macos_node_launch_stops_there(self):
        calls = []
        lines = {"300": " 200 /bin/bash -c python3 gk-seat.py",
                 "200": "  100 node /x/claude-code/cli.js --model=claude-sonnet-5-5",
                 "100": "  1 /Applications/Claude.app/claude --model=claude-opus-5-5"}

        def fake_run(argv, **kw):
            calls.append(argv)
            return mock.Mock(returncode=0, stdout=lines[argv[-1]].encode())
        with mock.patch.object(gk_model.sys, "platform", "darwin"), \
                mock.patch.object(gk_model.os, "getppid", return_value=300), \
                mock.patch.object(gk_model.subprocess, "run", fake_run):
            self.assertEqual(gk_model.launch_args(), ("claude-sonnet-5-5", None))
        self.assertEqual(len(calls), 2)

    def test_env_switch_reads_nothing(self):
        with mock.patch.dict(os.environ, {"GK_NO_LAUNCH_ARGS": "1"}), \
                mock.patch.object(gk_model.sys, "platform", "linux"), \
                mock.patch.object(gk_model, "_proc_info",
                                  side_effect=AssertionError("read")) as info:
            self.assertEqual(gk_model.launch_args(), (None, None))
        info.assert_not_called()


class ProcReaderTest(unittest.TestCase):
    def test_linux_reader(self):
        with tempfile.TemporaryDirectory() as d:
            proc = Path(d)
            (proc / "123").mkdir()
            (proc / "123" / "stat").write_bytes(b"123 (a) b) S 45 123 123 0 -1 4194560\n")
            (proc / "123" / "cmdline").write_bytes(b"/usr/bin/claude\0--model=x y\0")
            (proc / "124").mkdir()
            (proc / "124" / "stat").write_bytes(b"124 (a) S 45 1\n")  # no cmdline file
            with mock.patch.object(gk_model, "_PROC_ROOT", d), \
                    mock.patch.object(gk_model.sys, "platform", "linux"):
                self.assertEqual(gk_model._proc_info(123),
                                 (45, ["/usr/bin/claude", "--model=x y"]))
                self.assertIsNone(gk_model._proc_info(124))
                self.assertIsNone(gk_model._proc_info(999))

    def _mac(self, **kw):
        with mock.patch.object(gk_model.sys, "platform", "darwin"), \
                mock.patch.object(gk_model.subprocess, "run", **kw) as run:
            return gk_model._proc_info(42), run

    def test_macos_reader(self):
        out = b" 7 /Applications/Claude.app/claude --model=claude-opus-5-5\n"
        result, run = self._mac(return_value=mock.Mock(returncode=0, stdout=out))
        self.assertEqual(result, (7, ["/Applications/Claude.app/claude",
                                      "--model=claude-opus-5-5"]))
        run.assert_called_once_with(["ps", "-ww", "-o", "ppid=,args=", "-p", "42"],
                                    stdin=subprocess.DEVNULL, capture_output=True, timeout=1)

    def test_macos_reader_failures(self):
        result, _ = self._mac(side_effect=subprocess.TimeoutExpired("ps", 1))
        self.assertIsNone(result)
        result, _ = self._mac(return_value=mock.Mock(returncode=1, stdout=b" 7 /x/claude\n"))
        self.assertIsNone(result)


    def test_windows_spawns_nothing(self):
        with mock.patch.object(gk_model.sys, "platform", "win32"), \
                mock.patch.object(gk_model.subprocess, "run") as run, \
                mock.patch.object(gk_model, "_proc_info") as info:
            self.assertEqual(gk_model.launch_args(), (None, None))
        run.assert_not_called()
        info.assert_not_called()

    def test_real_machine(self):
        r = gk_model.launch_args()
        self.assertEqual(len(r), 2)


ATTACH = {"type": "attachment", "attachment": {"type": "model", "identity": {
    "modelId": "claude-opus-5-5", "marketingName": "Opus 5.5"}, "text": "You are powered by"}}


class TranscriptAttachmentTest(unittest.TestCase):
    def _model(self, entries):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.jsonl"
            p.write_text("".join(json.dumps(e) + "\n" for e in entries))
            return gk_model.model_from_transcript(p)

    def test_attachment_only(self):
        self.assertEqual(self._model([ATTACH]), "claude-opus-5-5")

    def test_later_assistant_wins(self):
        a = {"type": "assistant", "message": {"model": "claude-sonnet-5-5"}}
        self.assertEqual(self._model([ATTACH, a]), "claude-sonnet-5-5")

    def test_sidechain_attachment_ignored(self):
        side = json.loads(json.dumps(ATTACH))
        side["isSidechain"] = True
        side["attachment"]["identity"]["modelId"] = "claude-sonnet-5-5"
        self.assertEqual(self._model([ATTACH, side]), "claude-opus-5-5")

    def test_other_attachment_type_ignored(self):
        other = json.loads(json.dumps(ATTACH))
        other["attachment"]["type"] = "todo"
        self.assertIsNone(self._model([other]))


if __name__ == "__main__":
    unittest.main()
