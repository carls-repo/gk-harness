from __future__ import annotations

import os
import shutil
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


if __name__ == "__main__":
    unittest.main()
