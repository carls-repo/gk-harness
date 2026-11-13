"""One source of truth for tier facts: the launchers in shell/ and the workflow classes in
routes.json must name the same tiers as routes.json's seat launchers and stages."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTES = json.loads((ROOT / "routes.json").read_text(encoding="utf-8"))
SHELL = ROOT / "shell"

# One launcher per line: `name() { claude <flags> "$@"; }` (bash) or
# `function name { claude <flags> @args }` (PowerShell).
SH_LINE = re.compile(r'^([\w-]+)\(\) \{ claude (.*) "\$@"; \}$', re.M)
PS_LINE = re.compile(r"^function ([\w-]+) \{ claude (.*) @args \}$", re.M)
FLAG = re.compile(r"--(model|effort|settings) (\"[^\"]*\"|'[^']*'|\S+)")

# Workflow classes whose examples name no stage: the stage (and the `when` condition, if any)
# whose tier the class is.
CLASS_STAGES = {
    "plan-check": [("gk-build.plan-check", None)],
    "design-panel": [("gk-design.judge", None)],
    "judge-sensitive": [("gk-review.judge", "sensitive"), ("gk-build.judge", "ui")],
}


def _settings_ultracode(value: str) -> bool:
    """Whether a --settings value turns ultracode on: inline JSON, or the harness's settings
    file named through GK_HARNESS_ROOT."""
    raw = value.strip("'\"")
    if raw.startswith("{"):
        data = json.loads(raw)
    else:
        rel = re.sub(r"^\$env:GK_HARNESS_ROOT/", "", raw)
        data = json.loads((ROOT / rel).read_text(encoding="utf-8"))
    return data.get("ultracode") is True


def launchers(text: str, line: re.Pattern) -> dict:
    """{name: tier[+ultracode]} for every launcher that runs claude."""
    out = {}
    for name, flags in line.findall(text):
        found = dict(FLAG.findall(flags))
        tier = "%s-%s" % (found["model"], found["effort"])
        if "settings" in found and _settings_ultracode(found["settings"]):
            tier += "+ultracode"
        out[name] = tier
    return out


class LauncherTests(unittest.TestCase):
    def test_shell_launchers_match_routes(self):
        want = ROUTES["seat"]["launchers"]
        for path, line in ((SHELL / "aliases.sh", SH_LINE), (SHELL / "aliases.ps1", PS_LINE)):
            with self.subTest(path.name):
                got = launchers(path.read_text(encoding="utf-8"), line)
                self.assertEqual(got, want)
                for name, tier in got.items():
                    self.assertIn(tier.split("+")[0], ROUTES["tiers"], name)

    def test_the_parser_sees_a_drift(self):
        text = (SHELL / "aliases.sh").read_text(encoding="utf-8").replace(
            "gk-fable() { claude --model fable --effort medium", "gk-fable() { claude --model fable --effort high")
        self.assertEqual(launchers(text, SH_LINE)["gk-fable"], "fable-high")
        text = text.replace("""--settings '{"ultracode":true}'""", """--settings '{"ultracode":false}'""")
        self.assertEqual(launchers(text, SH_LINE)["gk-ultra"], "opus-medium")


class WorkflowClassTests(unittest.TestCase):
    def stages_of(self, name: str, cls: dict) -> list:
        named = re.findall(r"`(gk-[\w-]+\.[\w-]+)`", cls.get("examples", ""))
        return [(s, None) for s in named] or CLASS_STAGES.get(name, [])

    def test_each_workflow_class_has_its_stages_tier(self):
        workflow = {k: c for k, c in ROUTES["classes"].items() if c.get("who") == "workflow"}
        self.assertEqual(set(workflow),
                         {"plan-check", "design-panel", "judge-opus", "judge-sensitive", "ui-signoff"})
        for name, cls in workflow.items():
            stages = self.stages_of(name, cls)
            self.assertTrue(stages, "%s names no stage" % name)
            for stage, cond in stages:
                with self.subTest(cls=name, stage=stage):
                    s = ROUTES["stages"][stage]
                    tier = s["when"][cond] if cond else s["tier"]
                    self.assertEqual(tier, cls["tier"])


if __name__ == "__main__":
    unittest.main()
