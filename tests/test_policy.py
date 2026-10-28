from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "policy.md"
AGENTS = ROOT / "agents"

EIGHT = {
    "worker-haiku-low.md", "worker-haiku.md", "worker-sonnet.md", "worker-opus.md",
    "worker-opus-xhigh.md", "worker-opus-max.md", "worker-fable.md", "worker-fable-max.md",
}
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
            out[key.strip()] = value.strip()
    return {}


class PolicyTest(unittest.TestCase):
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

    def test_agents_are_the_eight(self):
        names = {p.name for p in AGENTS.iterdir()}
        self.assertEqual(names, EIGHT)
        for name in sorted(EIGHT):
            with self.subTest(agent=name):
                meta = frontmatter((AGENTS / name).read_text(encoding="utf-8"))
                self.assertEqual(meta.get("name"), name[:-3])
                self.assertIn(meta.get("model"), {"haiku", "sonnet", "opus", "fable"})


if __name__ == "__main__":
    unittest.main()
