from __future__ import annotations

import copy
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


def repo() -> dict:
    return copy.deepcopy(rt.load(Path(ROOT) / "routes.json"))


class RoutesTests(unittest.TestCase):
    def test_agent_names_sorted_with_retired(self):
        r = {"tiers": {"a": {"agent": "worker-z"}, "b": {"agent": "worker-a"}},
             "retired": {"worker-m": {}}}
        self.assertEqual(rt.agent_names(r), ["worker-a", "worker-m", "worker-z"])
        self.assertEqual(rt.agent_names({"tiers": {"a": {"agent": "worker-z"}}}), ["worker-z"])
        r["agents"] = {"Explore": {}}
        self.assertEqual(rt.agent_names(r), ["Explore", "worker-a", "worker-m", "worker-z"])

    def test_repo_routes_valid(self):
        r = rt.load(Path(ROOT) / "routes.json")
        self.assertEqual(rt.problems(r), [])
        self.assertEqual(r["version"], 2)
        self.assertEqual(len(r["tiers"]), 9)

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

    def test_one_hour_cache_write_price_is_optional_but_positive(self):
        r = fixture()
        self.assertEqual(rt.problems(r), [])
        r["models"]["opus"]["price"]["cacheWrite1h"] = 2
        self.assertEqual(rt.problems(r), [])
        for bad in (0, -1, "2", None, True):
            with self.subTest(bad=bad):
                r["models"]["opus"]["price"]["cacheWrite1h"] = bad
                self.assertProblem(r, "model opus: price.cacheWrite1h")
        r = fixture()
        r["models"]["opus"]["priceOver100k"] = {"input": 1, "output": 1, "cacheWrite": 1,
                                                "cacheRead": 1, "cacheWrite1h": 0}
        self.assertProblem(r, "model opus: priceOver100k.cacheWrite1h")

    def test_repo_prices_one_hour_cache_writes_for_every_model(self):
        models = repo()["models"]
        for alias, m in models.items():
            for field in ("price", "priceOver100k"):
                if field in m:
                    self.assertGreater(m[field].get("cacheWrite1h", 0), m[field]["cacheWrite"],
                                       "%s %s" % (alias, field))

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

    # --- hardening: wrong shapes are reported, never raised --------------------------------

    def assertProblem(self, r, needle):
        ps = rt.problems(r)
        self.assertTrue(any(needle in p for p in ps), "no problem naming %r in %r" % (needle, ps))

    def test_non_object_model_is_a_problem(self):
        r = fixture()
        r["models"]["opus"] = "opus"
        self.assertProblem(r, "model opus")

    def test_non_object_tier_is_a_problem(self):
        r = fixture()
        r["tiers"]["opus-high"] = 3
        self.assertProblem(r, "tier opus-high")

    def test_non_object_price_is_a_problem(self):
        r = fixture()
        r["models"]["opus"]["price"] = [1, 2]
        self.assertProblem(r, "model opus")

    def test_non_object_class_is_a_problem(self):
        r = repo()
        r["classes"]["chat"] = "seat"
        self.assertProblem(r, "class chat")

    def test_non_object_stage_is_a_problem(self):
        r = repo()
        r["stages"]["gk-review.scope"] = ["haiku-medium"]
        self.assertProblem(r, "stage gk-review.scope")

    def test_non_object_tiers_or_stages_is_a_problem(self):
        for key in ("tiers", "stages", "models", "classes"):
            with self.subTest(key=key):
                r = repo()
                r[key] = ["not", "an", "object"]
                self.assertProblem(r, key)

    def test_missing_or_empty_description_or_body_is_a_problem(self):
        for field in ("description", "body"):
            for value in (None, "", "   "):
                with self.subTest(field=field, value=value):
                    r = repo()
                    if value is None:
                        del r["tiers"]["opus-high"][field]
                    else:
                        r["tiers"]["opus-high"][field] = value
                    self.assertProblem(r, "tier opus-high: %s" % field)
                    r = repo()
                    if value is None:
                        del r["agents"]["Explore"][field]
                    else:
                        r["agents"]["Explore"][field] = value
                    self.assertProblem(r, "agent Explore: %s" % field)

    def test_problems_and_judge_violations_never_raise(self):
        """Every node of the repo file, swapped for each wrong JSON type, gives strings, not a raise."""
        base = repo()
        paths = []

        def walk(node, path):
            paths.append(path)
            items = node.items() if isinstance(node, dict) else enumerate(node) if isinstance(node, list) else ()
            for k, v in items:
                walk(v, path + (k,))

        walk(base, ())
        for path in paths[1:]:
            for bad in ([1], 3, None, {}, {"x": [1]}, "nope"):
                r = copy.deepcopy(base)
                node = r
                for k in path[:-1]:
                    node = node[k]
                node[path[-1]] = bad
                try:
                    self.assertIsInstance(rt.problems(r), list)
                    self.assertIsInstance(rt.judge_violations(r), list)
                except Exception as e:  # pragma: no cover - the failure message is the point
                    self.fail("%r = %r raised %s: %s" % (path, bad, type(e).__name__, e))

    # --- v2 sections ------------------------------------------------------------------------

    def test_judges_hold(self):
        self.assertEqual(rt.judge_violations(rt.load(Path(ROOT) / "routes.json")), [])

    def test_judge_violation_detected(self):
        r = repo()
        r["stages"]["gk-build.judge"]["tier"] = "sonnet-medium"
        vs = rt.judge_violations(r)
        self.assertEqual(len(vs), 1, vs)
        self.assertIn("gk-build.judge", vs[0])
        self.assertIn("gk-build.review", vs[0])

    def test_judge_violation_with_fable_off(self):
        r = repo()
        r["stages"]["gk-design.judge"]["noFable"] = "sonnet-high"
        vs = rt.judge_violations(r)
        self.assertEqual(len(vs), 1, vs)
        self.assertIn("Fable off", vs[0])

    def test_judge_conditional_tier_counts_only_on_its_condition(self):
        r = repo()
        # gk-design.judge: base below the proposals' UI tier, but a ui-only lift covers it.
        r["stages"]["gk-design.judge"]["tier"] = "opus-high"
        self.assertEqual(len(rt.judge_violations(r)), 1)
        r["stages"]["gk-design.judge"]["when"] = {"ui": "fable-medium"}
        r["stages"]["gk-design.judge"]["noFable"] = "opus-xhigh"
        self.assertEqual(rt.judge_violations(r), [])

    def test_at_least_floor_checked_against_every_class(self):
        r = repo()
        self.assertEqual(rt.stage_variants(r, "gk-build.review"),
                         {"": "opus-high", "class:spec": "opus-high", "class:judgement": "opus-high",
                          "class:hard": "fable-medium", "class:security": "fable-medium"})
        self.assertEqual(rt.stage_variants(r, "gk-build.review", fable=False)["class:hard"],
                         "opus-xhigh")
        r["stages"]["gk-build.review"]["tier"] = "nope"
        self.assertTrue(rt.judge_violations(r))

    def test_every_fable_stage_has_nofable_and_onfail(self):
        r = repo()
        tiers = set(r["tiers"])
        reach = [k for k in r["stages"] if rt.reaches_fable(r, k)]
        self.assertIn("gk-build.implement", reach)
        self.assertIn("gk-build.review", reach)
        self.assertIn("gk-design.propose", reach)
        self.assertIn("gk-design.sign-off", reach)
        self.assertIn("gk-research.critic", reach)  # apex: the fable dial lifts it
        for key in reach:
            with self.subTest(stage=key):
                s = r["stages"][key]
                nf = s.get("noFable")
                self.assertIsNotNone(nf)
                if isinstance(nf, dict):
                    fable_classes = {c for c, t in s["byClass"].items()
                                     if r["tiers"][t]["model"] == "fable"}
                    self.assertEqual(set(nf), fable_classes)
                    self.assertTrue(set(nf.values()) <= tiers)
                else:
                    self.assertTrue(nf == "none" or nf in tiers, nf)
                    if nf != "none":
                        self.assertNotEqual(r["tiers"][nf]["model"], "fable")
                self.assertIn(s.get("onFail"), {"rerun", "unfinished", "hold"})
        self.assertEqual(r["stages"]["gk-design.sign-off"]["noFable"], "none")
        self.assertEqual(r["stages"]["gk-design.sign-off"]["onFail"], "hold")
        self.assertEqual(r["stages"]["gk-build.implement"]["noFable"],
                         {"hard": "opus-high", "security": "opus-high"})

    def test_fable_stage_without_nofable_is_a_problem(self):
        r = repo()
        del r["stages"]["gk-diagnose.adjudicate"]["noFable"]
        self.assertProblem(r, "stage gk-diagnose.adjudicate")
        r = repo()
        r["stages"]["gk-diagnose.adjudicate"]["onFail"] = "retry"
        self.assertProblem(r, "stage gk-diagnose.adjudicate")

    def test_apex_stage_without_nofable_is_a_problem(self):
        """An apex stage reaches Fable under the fable dial, so it needs noFable and onFail."""
        r = repo()
        r["stages"]["gk-research.sweep"]["apex"] = True
        self.assertTrue(rt.reaches_fable(r, "gk-research.sweep"))
        self.assertProblem(r, "stage gk-research.sweep: can run on Fable, so it needs noFable")
        self.assertProblem(r, "stage gk-research.sweep: can run on Fable, so it needs onFail")
        r = repo()
        del r["stages"]["gk-research.critic"]["noFable"]
        self.assertProblem(r, "stage gk-research.critic")
        r = repo()
        r["stages"]["gk-research.critic"]["apex"] = False
        del r["stages"]["gk-research.critic"]["noFable"]
        del r["stages"]["gk-research.critic"]["onFail"]
        self.assertEqual(rt.problems(r), [])

    def test_ladder_strictly_increases(self):
        r = repo()
        caps = [rt.capability(r, t) for t in r["ladder"]]
        self.assertEqual(caps, sorted(set(caps)))
        self.assertEqual(r["ladder"], ["haiku-medium", "sonnet-medium", "sonnet-high", "opus-medium",
                                       "opus-high", "fable-medium", "fable-high"])
        r["ladder"] = ["opus-high", "opus-medium"]
        self.assertProblem(r, "ladder")

    def test_every_route_has_evidence(self):
        r = repo()
        ev = r["evidence"]
        for key in list(r["classes"]) + list(r["stages"]) + ["seat"]:
            with self.subTest(route=key):
                self.assertIsInstance(ev.get(key), str)
                self.assertTrue(ev[key])
        del ev["gk-review.find"]
        self.assertProblem(r, "evidence")

    def test_class_tiers_exist(self):
        r = repo()
        tiers = set(r["tiers"])
        for name, c in r["classes"].items():
            with self.subTest(cls=name):
                self.assertTrue(c["tier"] == "seat" or c["tier"] in tiers)
                for k in ("next", "floor"):
                    if k in c:
                        self.assertIn(c[k], tiers)
                self.assertIn(c["who"], {"seat", "worker", "workflow"})
                self.assertTrue(c["check"])
        r["classes"]["feature"]["tier"] = "opus-ultra"
        self.assertProblem(r, "class feature")

    def test_class_examples_required(self):
        r = repo()
        for name, c in r["classes"].items():
            with self.subTest(cls=name):
                self.assertIsInstance(c.get("examples"), str)
                self.assertTrue(c["examples"].strip())
        for bad in ("", "  ", None, ["a list"]):
            with self.subTest(examples=bad):
                r = repo()
                if bad is None:
                    del r["classes"]["search"]["examples"]
                else:
                    r["classes"]["search"]["examples"] = bad
                self.assertProblem(r, "class search: examples")

    def test_v2_values(self):
        r = repo()
        self.assertEqual(r["stages"]["gk-build.plan-check"]["tier"], "fable-medium")
        self.assertEqual(r["stages"]["gk-review.judge"]["when"]["sensitive"], "fable-high")
        self.assertEqual(r["seat"]["default"], "opus-medium")

    def test_phases_validated(self):
        r = repo()
        self.assertEqual(r["phases"]["gk-review"]["Verify"], "gk-review.refute")
        self.assertEqual(rt.problems(r), [])
        r["phases"]["gk-review"]["Scope"] = "gk-review.nope"
        self.assertProblem(r, "phases gk-review")
        r = repo()
        r["phases"]["gk-review"]["Scope"] = "gk-build.check"
        self.assertProblem(r, "phases gk-review")
        r = repo()
        r["phases"]["gk-review"] = ["Scope"]
        self.assertProblem(r, "phases gk-review")

    def test_retired_and_extra_agents_validated(self):
        r = repo()
        r["retired"]["worker-old"] = "opus-ultra"
        self.assertProblem(r, "retired worker-old")
        r = repo()
        r["retired"]["worker-opus-high"] = "opus-high"
        self.assertProblem(r, "worker-opus-high")
        r = repo()
        r["agents"]["Explore"]["tier"] = "nope"
        self.assertProblem(r, "agent Explore")


if __name__ == "__main__":
    unittest.main()
