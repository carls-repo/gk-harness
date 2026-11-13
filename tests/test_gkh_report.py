from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from gkh import cli, report  # noqa: E402
from gkh.routes import load as load_routes  # noqa: E402

FIX = Path(ROOT) / "tests" / "fixtures" / "transcripts"
S1 = "11111111-1111-4111-8111-111111111111"
S2 = "22222222-2222-4222-8222-222222222222"
# Prices per million tokens, fixed here so the arithmetic below does not move with routes.json.
ROUTES = {"models": {
    "haiku": {"id": "claude-haiku-5-5",
              "price": {"input": 0.1, "output": 0.5, "cacheWrite": 0.125, "cacheWrite1h": 0.2,
                        "cacheRead": 0.01},
              "priceOver100k": {"input": 0.5, "output": 2.5, "cacheWrite": 0.625,
                                "cacheWrite1h": 1.0, "cacheRead": 0.05}},
    "sonnet": {"id": "claude-sonnet-5-5",
               "price": {"input": 2, "output": 10, "cacheWrite": 2.5, "cacheWrite1h": 4,
                         "cacheRead": 0.1}},
    "opus": {"id": "claude-opus-5-5",
             "price": {"input": 4, "output": 20, "cacheWrite": 5, "cacheWrite1h": 8,
                       "cacheRead": 0.2}},
    "fable": {"id": "claude-fable-5-2",
              "price": {"input": 8, "output": 40, "cacheWrite": 10, "cacheWrite1h": 16,
                        "cacheRead": 0.2}},
}, "classes": {"known-cause": {}, "feature": {}, "chat": {}}}
# msg_sub1 writes 2,000 cache tokens: 500 for an hour (at 4) and 1,500 for 5 minutes (at 2.5).
COST = {"msg_route1": 0.00024, "msg_dup": 0.0204, "msg_haiku_big": 0.0305,
        "msg_haiku_small": 0.006, "msg_route2": 0.012, "msg_unknown": 0.0, "msg_sub1": 0.01805,
        "msg_wf1": 0.0048, "msg_other": 0.002}
TOTAL = sum(COST.values())


def make_claude_dir(tmp: Path) -> Path:
    """The fixtures laid out as Claude Code keeps them under <claude dir>/projects."""
    proj = tmp / "claude" / "projects" / "-home-c-repo"
    sub = proj / S1 / "subagents"
    run = sub / "workflows" / "wf_test-123"
    run.mkdir(parents=True)
    (proj / S1 / "workflows").mkdir()
    for src, dst in (("report-main.jsonl", proj / (S1 + ".jsonl")),
                     ("report-other.jsonl", proj / (S2 + ".jsonl")),
                     ("report-agent.jsonl", sub / "agent-aaa1.jsonl"),
                     ("report-agent.meta.json", sub / "agent-aaa1.meta.json"),
                     ("report-wf-agent.jsonl", run / "agent-bbb2.jsonl"),
                     ("report-journal.jsonl", run / "journal.jsonl"),
                     ("report-workflow.json", proj / S1 / "workflows" / "wf_test-123.json")):
        shutil.copyfile(str(FIX / src), str(dst))
    return tmp / "claude"


def run_cli(argv: list) -> tuple:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class ReportTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cdir = make_claude_dir(self.tmp)

    def calls(self, **kw) -> dict:
        out = {}
        for c in report.iter_calls(self.cdir, **kw):
            self.assertNotIn(c["message_id"], out, "a message counted twice")
            out[c["message_id"]] = c
        return out


class IterCallsTests(ReportTestCase):
    def test_streamed_lines_of_one_message_count_once_from_the_last_line(self):
        calls = self.calls()
        dup = calls["msg_dup"]
        self.assertEqual((dup["input"], dup["output"], dup["cache_write"], dup["cache_read"]),
                         (100, 1000, 0, 0))
        self.assertAlmostEqual(report.price(ROUTES, dup), COST["msg_dup"])
        summary = report.summarise(list(calls.values()), ROUTES)
        self.assertEqual(summary["totals"]["calls"], len(COST))
        self.assertAlmostEqual(summary["totals"]["cost"], TOTAL)

    def test_a_message_seen_in_two_files_counts_once(self):
        # A forked session copies earlier messages into its own file.
        proj = self.cdir / "projects" / "-home-c-repo"
        shutil.copyfile(str(proj / (S1 + ".jsonl")),
                        str(proj / "33333333-3333-4333-8333-333333333333.jsonl"))
        self.assertEqual(set(self.calls()), set(COST))

    def test_fields(self):
        c = self.calls()["msg_route1"]
        self.assertEqual({k: c[k] for k in ("session", "agent", "workflow", "stage", "model",
                                            "input", "output", "cache_write", "cache_read")},
                         {"session": S1, "agent": "seat", "workflow": None, "stage": None,
                          "model": "claude-opus-5-5", "input": 10, "output": 10,
                          "cache_write": 0, "cache_read": 0})
        self.assertEqual(c["day"], "2026-11-04")

    def test_synthetic_lines_and_non_assistant_lines_are_not_calls(self):
        self.assertEqual(set(self.calls()), set(COST))

    def test_subagent_is_attributed_to_its_agent_name(self):
        c = self.calls()["msg_sub1"]
        self.assertEqual((c["session"], c["agent"], c["workflow"], c["stage"]),
                         (S1, "worker-sonnet", None, None))

    def test_workflow_agent_is_attributed_to_its_workflow_and_stage(self):
        c = self.calls()["msg_wf1"]
        self.assertEqual((c["session"], c["workflow"], c["workflow_run"], c["stage"], c["label"]),
                         (S1, "gk-review", "wf_test-123", "Judge", "judge"))
        self.assertEqual(c["agent"], "workflow-subagent")

    def test_workflow_without_its_run_record_is_named_by_its_run_id(self):
        (self.cdir / "projects" / "-home-c-repo" / S1 / "workflows" / "wf_test-123.json").unlink()
        self.assertEqual(self.calls()["msg_wf1"]["workflow"], "wf_test-123")

    def test_since_keeps_calls_on_and_after_the_day(self):
        self.assertEqual(set(self.calls(since="2026-11-05")),
                         set(COST) - {"msg_route1", "msg_dup"})
        self.assertEqual(set(self.calls(since="2026-11-07")), set())

    def test_since_skips_files_last_written_before_it(self):
        old = time.mktime((2026, 10, 1, 0, 0, 0, 0, 0, -1))
        main = self.cdir / "projects" / "-home-c-repo" / (S2 + ".jsonl")
        os.utime(str(main), (old, old))
        opened = []
        real = report._open_lines

        def spy(path):
            opened.append(Path(path).name)
            return real(path)

        report._open_lines = spy
        self.addCleanup(setattr, report, "_open_lines", real)
        self.assertNotIn("msg_other", self.calls(since="2026-11-05"))
        self.assertNotIn(S2 + ".jsonl", opened)

    def test_session_filter(self):
        self.assertEqual(set(self.calls(session=S2)), {"msg_other"})
        self.assertEqual(set(self.calls(session=S1)), set(COST) - {"msg_other"})
        self.assertEqual(set(self.calls(session="nope")), set())

    def test_route_lines_tag_the_session_calls_that_follow(self):
        calls = self.calls()
        self.assertEqual({m: c["route_class"] for m, c in calls.items()}, {
            "msg_route1": "known-cause", "msg_dup": "known-cause",
            "msg_haiku_big": "known-cause", "msg_haiku_small": "known-cause",
            "msg_sub1": "known-cause", "msg_route2": "feature", "msg_unknown": "feature",
            "msg_wf1": "feature", "msg_other": "Hartwell"})

    def test_route_words_outside_the_classes_are_unrecognised(self):
        calls = self.calls(classes=set(ROUTES["classes"]))
        self.assertEqual(calls["msg_other"]["route_class"], report.UNRECOGNISED)
        self.assertEqual(calls["msg_sub1"]["route_class"], "known-cause")

    def test_the_one_hour_cache_split_is_carried(self):
        calls = self.calls()
        sub = calls["msg_sub1"]
        self.assertEqual((sub["cache_write"], sub["cache_write_1h"], sub["cache_write_5m"]),
                         (2000, 500, 1500))
        self.assertNotIn("cache_write_1h", calls["msg_dup"])


class PriceTests(unittest.TestCase):
    def call(self, model, i=0, o=0, cw=0, cr=0):
        return {"model": model, "input": i, "output": o, "cache_write": cw, "cache_read": cr}

    def test_formula(self):
        self.assertAlmostEqual(report.price(ROUTES, self.call("claude-sonnet-5-5", 1000, 1000,
                                                              2000, 3000)), 0.0173)

    def test_one_hour_cache_writes_take_their_own_price(self):
        call = dict(self.call("claude-opus-5-5", 0, 0, 3000, 0), cache_write_1h=1000,
                    cache_write_5m=2000)
        self.assertAlmostEqual(report.price(ROUTES, call), (1000 * 8 + 2000 * 5) / 1e6)
        # Without the split, every cache write is at the 5-minute price, as before.
        self.assertAlmostEqual(report.price(ROUTES, self.call("claude-opus-5-5", 0, 0, 3000, 0)),
                               3000 * 5 / 1e6)
        # Haiku over 100K: the 1-hour part at priceOver100k's cacheWrite1h.
        big = dict(self.call("claude-haiku-5-5", 1, 0, 200000, 0), cache_write_1h=100000,
                   cache_write_5m=100000)
        self.assertAlmostEqual(report.price(ROUTES, big),
                               (1 * 0.5 + 100000 * 1.0 + 100000 * 0.625) / 1e6)

    def test_haiku_over_100k_takes_the_higher_price(self):
        self.assertAlmostEqual(report.price(ROUTES, self.call("claude-haiku-5-5", 50000, 1000,
                                                              0, 60000)), 0.0305)
        self.assertAlmostEqual(report.price(ROUTES, self.call("claude-haiku-5-5", 50000, 1000,
                                                              0, 50000)), 0.006)
        self.assertAlmostEqual(report.price(ROUTES, self.call("claude-haiku-5-5", 1, 0,
                                                              100000, 0)),
                               (0.5 + 100000 * 0.625) / 1e6)

    def test_alias_by_substring_and_unknown_is_free(self):
        self.assertAlmostEqual(report.price(ROUTES, self.call("claude-fable-5-1", 1000000)), 8)
        self.assertAlmostEqual(report.price(ROUTES, self.call("claude-opus-5-20261001", 0, 1e6)),
                               20)
        self.assertEqual(report.alias(ROUTES, "Claude-Opus-5-5"), "opus")
        self.assertEqual(report.price(ROUTES, self.call("gpt-9-turbo", 1000000)), 0.0)
        self.assertIsNone(report.alias(ROUTES, "gpt-9-turbo"))


class SummariseTests(ReportTestCase):
    def setUp(self):
        super().setUp()
        self.summary = report.summarise(report.iter_calls(self.cdir), ROUTES)

    def rows(self, key):
        return {r["key"]: r for r in self.summary[key]}

    def test_fable_share(self):
        fable = COST["msg_route2"] + COST["msg_wf1"]
        self.assertAlmostEqual(self.summary["fable"]["cost"], fable)
        self.assertAlmostEqual(self.summary["fable"]["share"], fable / TOTAL)

    def test_groups(self):
        self.assertAlmostEqual(self.rows("bySession")[S2]["cost"], COST["msg_other"])
        self.assertAlmostEqual(self.rows("bySession")[S1]["cost"], TOTAL - COST["msg_other"])
        self.assertEqual(list(self.rows("byDay")),
                         ["2026-11-04", "2026-11-05", "2026-11-06"])
        self.assertAlmostEqual(self.rows("byDay")["2026-11-04"]["cost"],
                               COST["msg_route1"] + COST["msg_dup"])
        self.assertAlmostEqual(self.rows("byWorkflow")["gk-review"]["cost"], COST["msg_wf1"])
        self.assertAlmostEqual(self.rows("byWorkflowRun")["gk-review wf_test-123"]["cost"],
                               COST["msg_wf1"])
        self.assertAlmostEqual(self.rows("byStage")["gk-review / Judge"]["cost"], COST["msg_wf1"])
        self.assertAlmostEqual(self.rows("byAgent")["worker-sonnet"]["cost"], COST["msg_sub1"])
        self.assertEqual(self.rows("byModel")["claude-haiku-5-5"]["calls"], 2)
        for key in ("bySession", "byDay", "byWorkflow", "byStage", "byAgent", "byModel"):
            self.assertAlmostEqual(sum(r["cost"] for r in self.summary[key]), TOTAL, msg=key)

    def test_haiku_over_100k_is_counted(self):
        self.assertEqual(self.summary["haikuOver100k"]["calls"], 1)
        self.assertAlmostEqual(self.summary["haikuOver100k"]["cost"], COST["msg_haiku_big"])

    def test_unknown_models_are_listed_not_dropped(self):
        self.assertEqual([(r["key"], r["calls"], r["input"]) for r in self.summary["unknownModels"]],
                         [("gpt-9-turbo", 1, 10)])

    def test_cost_per_class_is_best_effort(self):
        by_class = self.summary["byClass"]
        self.assertTrue(by_class["bestEffort"])
        rows = {r["key"]: r["cost"] for r in by_class["rows"]}
        self.assertAlmostEqual(rows["known-cause"], sum(COST[m] for m in (
            "msg_route1", "msg_dup", "msg_haiku_big", "msg_haiku_small", "msg_sub1")))
        self.assertAlmostEqual(rows["feature"], COST["msg_route2"] + COST["msg_wf1"])
        self.assertAlmostEqual(rows[report.UNRECOGNISED], COST["msg_other"])
        self.assertNotIn(report.NO_ROUTE, rows)

    def test_a_prose_route_word_is_never_printed(self):
        # summarise buckets it even when iter_calls was given no classes.
        for as_json in (False, True):
            self.assertNotIn("Hartwell", report.render(self.summary, as_json=as_json))
        self.assertIn(report.UNRECOGNISED, report.render(self.summary, as_json=False))

    def test_tokens_line_shows_one_hour_cache_writes(self):
        self.assertEqual(self.summary["totals"]["cache_write_1h"], 500)
        line = [l for l in report.render(self.summary, as_json=False).splitlines()
                if l.startswith("Tokens:")][0]
        self.assertIn("cache write 2.0K (1-hour 500)", line)

    def test_no_older_id_note_when_every_id_is_current(self):
        self.assertEqual(self.summary["notes"], [])
        self.assertNotIn(report.OLDER_IDS, report.render(self.summary, as_json=False))

    def test_render_text(self):
        text = report.render(self.summary, as_json=False)
        self.assertIn("On a subscription, cost is a proxy for your share of the usage limits.",
                      text.splitlines()[1])
        self.assertIn("best effort", text)
        self.assertIn("gk-review / Judge", text)
        self.assertIn("gpt-9-turbo", text)
        self.assertNotIn("SECRET", text)

    def test_render_json(self):
        data = json.loads(report.render(self.summary, as_json=True))
        self.assertAlmostEqual(data["totals"]["cost"], TOTAL)
        self.assertEqual(data["header"],
                         "On a subscription, cost is a proxy for your share of the usage limits.")


class RenderTests(unittest.TestCase):
    def call(self, day, model="claude-opus-5-5"):
        return {"session": "s", "agent": "seat", "workflow": None, "workflow_run": None,
                "stage": None, "label": None, "model": model, "input": 1, "output": 1,
                "cache_write": 0, "cache_read": 0, "message_id": day + model,
                "timestamp": day + "T00:00:00Z", "day": day, "route_class": None}

    def test_by_day_text_shows_the_latest_days_in_date_order(self):
        days = ["2026-10-%02d" % d for d in range(1, 13)]
        summary = report.summarise([self.call(d) for d in days], ROUTES)
        self.assertEqual([r["key"] for r in summary["byDay"]], days)
        text = report.render(summary, as_json=False)
        block = text.split("\nBy day\n", 1)[1].split("\n\n", 1)[0].splitlines()
        shown = [l.split()[0] for l in block[1:] if l.strip().startswith("2026-")]
        self.assertEqual(shown, days[2:])
        self.assertIn("2 earlier", block[-1])

    def test_older_model_ids_get_a_note(self):
        summary = report.summarise([self.call("2026-11-01", "claude-fable-5-1"),
                                    self.call("2026-11-02", "claude-fable-5-2")], ROUTES)
        self.assertEqual(summary["notes"], [report.OLDER_IDS])
        self.assertEqual(report.OLDER_IDS,
                         "Older model ids are priced at their alias's current price.")
        text = report.render(summary, as_json=False)
        block = text.split("\nBy model\n", 1)[1].split("\n\n", 1)[0].splitlines()
        self.assertEqual(block[-1], report.OLDER_IDS)
        self.assertEqual(json.loads(report.render(summary, as_json=True))["notes"],
                         [report.OLDER_IDS])


class CommandTests(ReportTestCase):
    def test_json_parses_and_prices_from_routes_json(self):
        code, out, err = run_cli(["report", "--config-dir", str(self.cdir), "--json"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        live = load_routes(Path(ROOT) / "routes.json")
        expected = sum(report.price(live, c) for c in report.iter_calls(self.cdir))
        self.assertAlmostEqual(data["totals"]["cost"], expected)
        self.assertEqual(data["totals"]["calls"], len(COST))
        self.assertNotIn("SECRET", out)
        self.assertNotIn("Hartwell", out)
        self.assertEqual({r["key"] for r in data["byClass"]["rows"]},
                         {"known-cause", "feature", report.UNRECOGNISED})

    def test_since_and_session_and_equals_form(self):
        code, out, err = run_cli(["report", "--since=2026-11-05", "--config-dir",
                                  str(self.cdir), "--session", S1, "--json"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["totals"]["calls"], len(COST) - 3)
        self.assertEqual((data["since"], data["session"]), ("2026-11-05", S1))

    def test_text_output(self):
        code, out, err = run_cli(["report", "--config-dir", str(self.cdir)])
        self.assertEqual(code, 0, err)
        self.assertIn("On a subscription, cost is a proxy", out)
        self.assertNotIn("SECRET", out)

    def test_gk_claude_dir_is_the_default(self):
        old = os.environ.get("GK_CLAUDE_DIR")
        os.environ["GK_CLAUDE_DIR"] = str(self.cdir)
        self.addCleanup(lambda: os.environ.pop("GK_CLAUDE_DIR", None) if old is None
                        else os.environ.__setitem__("GK_CLAUDE_DIR", old))
        code, out, _ = run_cli(["report", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["totals"]["calls"], len(COST))

    def test_bad_since_exits_2_with_a_plain_message(self):
        for bad in ("yesterday", "2026-13-01", "2026-11-5"):
            with self.subTest(bad=bad):
                code, out, err = run_cli(["report", "--since", bad, "--config-dir",
                                          str(self.cdir)])
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertIn("--since wants a date as YYYY-MM-DD", err)
                self.assertIn(bad, err)

    def test_bad_options_print_usage(self):
        for argv in (["report", "--since"], ["report", "--json", "--json"],
                     ["report", "--frobnicate"], ["report", "extra"],
                     ["report", "--since", "2026-11-05", "--since", "2026-11-06"],
                     ["install", "--since", "2026-11-05"], ["update", "--json"],
                     ["gen", "--check=1"]):
            with self.subTest(argv=argv):
                code, _, err = run_cli(argv)
                self.assertEqual(code, 2)
                self.assertIn("usage:", err)
                self.assertIn("report [--since YYYY-MM-DD]", err)

    def test_missing_claude_dir_is_an_empty_report(self):
        code, out, err = run_cli(["report", "--config-dir", str(self.tmp / "none"), "--json"])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["totals"]["calls"], 0)


if __name__ == "__main__":
    unittest.main()
