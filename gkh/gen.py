"""Generates agents/*.md from routes.json (the single source of truth)."""
from __future__ import annotations

from pathlib import Path

from . import routes as rt


def render_agent(routes: dict, tier_name: str) -> str:
    t = rt.tier(routes, tier_name)
    return ("---\nname: %s\ndescription: %s\nmodel: %s\neffort: %s\n---\n%s"
            % (t["agent"], t["description"], t["model"], t["effort"], t["body"]))


def outputs(root: Path) -> dict:
    """Repo-relative path -> the text that file should hold."""
    routes = rt.load(Path(root) / "routes.json")
    return {"agents/%s.md" % rt.tier(routes, name)["agent"]: render_agent(routes, name)
            for name in routes["tiers"]}


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
