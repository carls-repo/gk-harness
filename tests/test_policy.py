from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gkh.routes import agent_names, load  # noqa: E402

POLICY = ROOT / "policy.md"
AGENTS = ROOT / "agents"

ROUTES = load(ROOT / "routes.json")
AGENT_FILES = {n + ".md" for n in agent_names(ROUTES)}
DISPLAY = {alias: m["display"] for alias, m in ROUTES["models"].items()}
FINISHING_START = "Please avoid stopping while you still owe work the user asked for."
FINISHING_END = "This does not override the need for confirmation on risky or destructive actions."
# Box paths and Goodricke-only rules that must not reach a policy installed on every machine.
# CHANGELOG is the file name (generic "changelog entries" work stays in the routing table).
BANNED_EXACT = ("/home/carls", "mike-dev", ":8080", "CHANGELOG")
BANNED_ANY_CASE = ("supabase", "scroll audit")
# The one place the policy may name Goodricke's shared branch: the sentence that defines the
# shared branch as the project file's `sharedBranch`, with `develop` as its example.
DEVELOP = re.compile(r"\bdevelop\b")
# Sentence ends: ., ! or ? then whitespace, except after "e.g." and "i.e.".
SENTENCE_END = re.compile(r"(?<!e\.g\.)(?<!i\.e\.)(?<=[.!?])\s+")


def sentences(text: str) -> list:
    out = []
    for para in re.split(r"\n\s*\n", text):
        out.extend(SENTENCE_END.split(" ".join(para.split())))
    return out


def frontmatter(text: str) -> dict:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return {}
    out = {}
    for line in lines[1:]:
        if line == "---":
            return out
        key, sep, value = line.partition(":")
        if sep:
            value = value.strip()
            # gen writes a scalar that YAML could misread as a JSON (YAML double-quoted) string.
            out[key.strip()] = json.loads(value) if value.startswith('"') else value
    return {}


class PolicyTest(unittest.TestCase):
    def test_no_stale_fable_version(self):
        stale = [f.name for f in [POLICY, *sorted(AGENTS.glob("*.md"))]
                 if "Fable 5.1" in f.read_text() or "fable-5-1" in f.read_text()]
        self.assertEqual(stale, [], "stale Fable 5.1 text in: %s" % stale)

    def test_policy_fits_and_has_no_box_paths(self):
        text = POLICY.read_text(encoding="utf-8")
        self.assertLess(len(text), 40000)
        for word in BANNED_EXACT:
            self.assertNotIn(word, text)
        lowered = text.lower()
        for word in BANNED_ANY_CASE:
            self.assertNotIn(word, lowered)
        self.assertEqual(len(DEVELOP.findall(text)), 1, "develop must appear exactly once")
        found = [s for s in sentences(text) if DEVELOP.search(s)]
        self.assertEqual(len(found), 1)
        self.assertIn("`sharedBranch`", found[0],
                      "develop appears outside the sentence that names sharedBranch")
        self.assertIn("shared branch", found[0].lower())

    def test_agent_set_is_tiers_retired_and_extras(self):
        expected = ({t["agent"] for t in ROUTES["tiers"].values()} | set(ROUTES["retired"])
                    | set(ROUTES.get("agents", {})))
        self.assertIn("Explore", expected)
        self.assertEqual(AGENT_FILES, {n + ".md" for n in expected})
        names = {p.name for p in AGENTS.iterdir()}
        self.assertEqual(names, AGENT_FILES)
        for name in sorted(AGENT_FILES):
            with self.subTest(agent=name):
                meta = frontmatter((AGENTS / name).read_text(encoding="utf-8"))
                self.assertEqual(meta.get("name"), name[:-3])
                self.assertIn(meta.get("model"), {"haiku", "sonnet", "opus", "fable"})

    def _target(self, name: str) -> str:
        """The tier an agent file runs on."""
        for tier_name, t in ROUTES["tiers"].items():
            if t["agent"] == name:
                return tier_name
        if name in ROUTES["retired"]:
            return ROUTES["retired"][name]
        return ROUTES["agents"][name]["tier"]

    def test_agent_text_matches_tier(self):
        for name in sorted(AGENT_FILES):
            with self.subTest(agent=name):
                text = (AGENTS / name).read_text(encoding="utf-8")
                meta = frontmatter(text)
                t = ROUTES["tiers"][self._target(name[:-3])]
                self.assertEqual(meta["model"], t["model"])
                self.assertEqual(meta["effort"], t["effort"])
                desc = meta["description"]
                self.assertIn(DISPLAY[t["model"]], desc)
                self.assertIn("%s effort" % t["effort"], desc)
                for alias, display in DISPLAY.items():
                    if alias != t["model"]:
                        self.assertNotIn(display, desc)
                self.assertNotIn("This codebase is yours", text)
                body = text.split("\n---\n", 1)[1]
                if name[:-3] in ROUTES.get("agents", {}):
                    continue
                if t["effort"] in ("low", "medium"):
                    self.assertIn("\n# Finishing work\n", body)
                    tail = body[body.index("# Finishing work"):]
                    self.assertIn(FINISHING_START, tail)
                    self.assertIn(FINISHING_END, tail)
                else:
                    self.assertNotIn("Finishing work", body)

    def test_explore_is_read_only_haiku_medium(self):
        text = (AGENTS / "Explore.md").read_text(encoding="utf-8")
        lines = text.split("\n---\n", 1)[0].splitlines()[1:]
        self.assertEqual([ln.split(":", 1)[0] for ln in lines],
                         ["name", "description", "model", "effort", "tools"])
        meta = frontmatter(text)
        self.assertEqual((meta["model"], meta["effort"]), ("haiku", "medium"))
        self.assertEqual(meta["tools"], "Read, Grep, Glob, Bash")
        self.assertIn("read-only", meta["description"].lower())

    def test_retired_names_point_to_live_tiers(self):
        retired = ROUTES["retired"]
        self.assertEqual(retired, {
            "worker-haiku": "haiku-medium", "worker-sonnet": "sonnet-medium",
            "worker-opus": "opus-high", "worker-opus-max": "opus-xhigh",
            "worker-fable": "fable-high", "worker-fable-max": "fable-high"})
        live = {t["agent"] for t in ROUTES["tiers"].values()}
        for old, tier_name in sorted(retired.items()):
            with self.subTest(retired=old):
                self.assertIn(tier_name, ROUTES["tiers"])
                self.assertNotIn(old, live)
                t = ROUTES["tiers"][tier_name]
                text = (AGENTS / (old + ".md")).read_text(encoding="utf-8")
                meta = frontmatter(text)
                self.assertTrue(meta["description"].startswith(
                    "Retired name: use worker-%s. " % tier_name), meta["description"])
                self.assertEqual((meta["model"], meta["effort"]), (t["model"], t["effort"]))
                live_text = (AGENTS / (t["agent"] + ".md")).read_text(encoding="utf-8")
                self.assertEqual(text.split("\n---\n", 1)[1], live_text.split("\n---\n", 1)[1])

if __name__ == "__main__":
    unittest.main()
