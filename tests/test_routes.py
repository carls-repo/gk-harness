from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from gkh import routes as rt  # noqa: E402


def fixture() -> dict:
    price = {"input": 1, "output": 2, "cacheWrite": 1, "cacheRead": 1}
    return {
        "version": 1,
        "efforts": ["low", "medium", "high", "xhigh", "max"],
        "models": {
            "opus": {"id": "o", "rank": 3, "display": "Opus", "price": dict(price), "priceDate": "d"},
            "fable": {"id": "f", "rank": 4, "display": "Fable", "price": dict(price), "priceDate": "d"},
        },
        "tiers": {
            "opus-medium": {"model": "opus", "effort": "medium", "agent": "worker-opus-medium",
                            "description": "d", "body": "b"},
            "opus-high": {"model": "opus", "effort": "high", "agent": "worker-opus", "description": "d", "body": "b"},
            "opus-xhigh": {"model": "opus", "effort": "xhigh", "agent": "worker-opus-xhigh",
                           "description": "d", "body": "b"},
            "fable-medium": {"model": "fable", "effort": "medium", "agent": "worker-fable-medium",
                             "description": "d", "body": "b"},
        },
    }


class RoutesTests(unittest.TestCase):
    def test_agent_names_sorted_with_retired(self):
        r = {"tiers": {"a": {"agent": "worker-z"}, "b": {"agent": "worker-a"}},
             "retired": {"worker-m": {}}}
        self.assertEqual(rt.agent_names(r), ["worker-a", "worker-m", "worker-z"])
        self.assertEqual(rt.agent_names({"tiers": {"a": {"agent": "worker-z"}}}), ["worker-z"])

    def test_repo_routes_valid(self):
        r = rt.load(Path(ROOT) / "routes.json")
        self.assertEqual(rt.problems(r), [])
        self.assertEqual(len(r["tiers"]), 8)
        self.assertEqual(rt.judge_violations(r), [])

    def test_capability_order(self):
        r = fixture()
        c = rt.capability
        self.assertGreater(c(r, "fable-medium"), c(r, "opus-xhigh"))
        self.assertGreater(c(r, "opus-xhigh"), c(r, "opus-high"))
        self.assertGreater(c(r, "opus-high"), c(r, "opus-medium"))
        self.assertEqual(c(r, "opus-high"), (3, 3))

    def test_tier_returns_fields_and_rejects_unknown(self):
        r = fixture()
        self.assertEqual(rt.tier(r, "opus-high")["agent"], "worker-opus")
        with self.assertRaises(rt.RoutesError):
            rt.tier(r, "nope")

    def test_fixture_is_valid(self):
        self.assertEqual(rt.problems(fixture()), [])

    def test_unknown_model_is_a_problem(self):
        r = fixture()
        r["tiers"]["opus-high"]["model"] = "gpt"
        ps = rt.problems(r)
        self.assertTrue(any("opus-high" in p for p in ps))

    def test_unknown_effort_is_a_problem(self):
        r = fixture()
        r["tiers"]["opus-high"]["effort"] = "ludicrous"
        self.assertTrue(any("opus-high" in p for p in rt.problems(r)))

    def test_duplicate_agent_names_are_a_problem(self):
        r = fixture()
        r["tiers"]["opus-xhigh"]["agent"] = "worker-opus"
        self.assertTrue(any("worker-opus" in p for p in rt.problems(r)))

    def test_bad_agent_name_is_a_problem(self):
        r = fixture()
        r["tiers"]["opus-high"]["agent"] = "Bad Name"
        self.assertTrue(rt.problems(r))
        r["tiers"]["opus-high"]["agent"] = "Explore"
        self.assertEqual(rt.problems(r), [])

    def test_non_positive_price_is_a_problem(self):
        r = fixture()
        r["models"]["opus"]["price"]["input"] = 0
        self.assertTrue(any("opus" in p for p in rt.problems(r)))

    def test_invalid_json_raises(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "routes.json"
            p.write_text("{not json", encoding="utf-8")
            with self.assertRaises(rt.RoutesError):
                rt.load(p)
            p.write_text(json.dumps({"version": 1}), encoding="utf-8")
            with self.assertRaises(rt.RoutesError):
                rt.load(p)
            with self.assertRaises(rt.RoutesError):
                rt.load(Path(d) / "missing.json")


if __name__ == "__main__":
    unittest.main()
