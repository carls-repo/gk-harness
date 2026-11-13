"""Generates agents/*.md from routes.json (the single source of truth).

Three kinds of agent file: one per tier (worker-<tier>), one per retired name (the target
tier's model, effort and body, under a "Retired name: use worker-<tier>." description), and one
per extra agent in "agents" (Explore), which may carry a tools line."""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import routes as rt

FINISHING_HEADING = "# Finishing work"
FINISHING_EFFORTS = ("low", "medium")
# A YAML plain scalar may not hold ": " or " #", nor start with an indicator character.
_PLAIN_UNSAFE = re.compile(r": | #|^[-?:,\[\]{}#&*!|>'\"%@`]|:$")


def _scalar(text: str) -> str:
    """text as a YAML scalar: plain when that is safe, else a JSON (YAML double-quoted) string."""
    return json.dumps(text, ensure_ascii=False) if _PLAIN_UNSAFE.search(text) else text


def _render(name: str, description: str, model: str, effort: str, body: str, tools=None) -> str:
    head = ("---\nname: %s\ndescription: %s\nmodel: %s\neffort: %s\n"
            % (name, _scalar(description), model, effort))
    if tools:
        head += "tools: %s\n" % tools
    return head + "---\n" + body


def tier_body(routes: dict, tier_name: str) -> str:
    """The tier's body, with the finishing-work text appended for low and medium effort."""
    t = rt.tier(routes, tier_name)
    body = t["body"]
    finishing = routes.get("finishingWork")
    if finishing and t["effort"] in FINISHING_EFFORTS:
        body = "%s\n\n%s\n\n%s\n" % (body.rstrip("\n"), FINISHING_HEADING, finishing)
    return body


def render_agent(routes: dict, tier_name: str) -> str:
    t = rt.tier(routes, tier_name)
    return _render(t["agent"], t["description"], t["model"], t["effort"], tier_body(routes, tier_name))


def render_retired(routes: dict, name: str) -> str:
    target = routes["retired"][name]
    t = rt.tier(routes, target)
    description = "Retired name: use %s. %s" % (t["agent"], t["description"])
    return _render(name, description, t["model"], t["effort"], tier_body(routes, target))


def render_extra(routes: dict, name: str) -> str:
    a = routes["agents"][name]
    t = rt.tier(routes, a["tier"])
    return _render(name, a["description"], t["model"], t["effort"], a["body"], a.get("tools"))


def outputs(root: Path) -> dict:
    """Repo-relative path -> the text that file should hold."""
    routes = rt.load(Path(root) / "routes.json")
    out = {"agents/%s.md" % rt.tier(routes, name)["agent"]: render_agent(routes, name)
           for name in routes["tiers"]}
    for name in routes.get("retired") or {}:
        out["agents/%s.md" % name] = render_retired(routes, name)
    for name in routes.get("agents") or {}:
        out["agents/%s.md" % name] = render_extra(routes, name)
    return out


def _current(path: Path):
    try:
        with open(str(path), encoding="utf-8", newline="") as f:
            return f.read()
    except OSError:
        return None


def stale(root: Path) -> list:
    root = Path(root)
    return [rel for rel, text in outputs(root).items() if _current(root / rel) != text]


def write(root: Path) -> list:
    root = Path(root)
    written = []
    for rel, text in outputs(root).items():
        if _current(root / rel) != text:
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(str(target), "w", encoding="utf-8", newline="") as f:
                f.write(text)
            written.append(rel)
    return written
