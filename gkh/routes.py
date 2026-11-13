"""routes.json: the single source of truth for which model and effort each worker agent uses.

{"version": 1, "efforts": [...], "models": {alias: {id, rank, display, price, priceDate}},
 "tiers": {name: {model, effort, agent, description, body}}}. Capability is ordered by model
rank first, then by effort position in "efforts".
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

AGENT_NAME = re.compile(r"^worker-[a-z0-9-]+$")
REQUIRED = ("version", "efforts", "models", "tiers")
PRICE_KEYS = ("input", "output", "cacheWrite", "cacheRead")


class RoutesError(Exception):
    pass


def load(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RoutesError("cannot read %s: %s" % (path, exc))
    if not isinstance(data, dict):
        raise RoutesError("%s: top level must be an object" % path)
    missing = [k for k in REQUIRED if k not in data]
    if missing:
        raise RoutesError("%s: missing keys: %s" % (path, ", ".join(missing)))
    return data


def _positive(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def problems(routes: dict) -> list:
    out = []
    efforts = routes.get("efforts") or []
    models = routes.get("models") or {}
    tiers = routes.get("tiers") or {}
    for alias, m in models.items():
        for field in ("price", "priceOver100k"):
            if field not in m:
                continue
            for key in PRICE_KEYS:
                if not _positive((m[field] or {}).get(key)):
                    out.append("model %s: %s.%s must be a positive number" % (alias, field, key))
    seen = {}
    for name, t in tiers.items():
        if t.get("model") not in models:
            out.append("tier %s: unknown model %r" % (name, t.get("model")))
        if t.get("effort") not in efforts:
            out.append("tier %s: unknown effort %r" % (name, t.get("effort")))
        agent = t.get("agent")
        if not isinstance(agent, str) or not (AGENT_NAME.match(agent) or agent == "Explore"):
            out.append("tier %s: agent name %r must match ^worker-[a-z0-9-]+$ or be Explore" % (name, agent))
        elif agent in seen:
            out.append("tier %s: agent name %s duplicates tier %s" % (name, agent, seen[agent]))
        else:
            seen[agent] = name
    return out


def tier(routes: dict, name: str) -> dict:
    t = (routes.get("tiers") or {}).get(name)
    if t is None:
        raise RoutesError("unknown tier %r" % name)
    return {k: t.get(k) for k in ("model", "effort", "agent", "description", "body")}


def capability(routes: dict, name: str) -> tuple:
    t = tier(routes, name)
    try:
        rank = routes["models"][t["model"]]["rank"]
        return (rank, routes["efforts"].index(t["effort"]) + 1)
    except (KeyError, ValueError):
        raise RoutesError("tier %r has an unknown model or effort" % name)


def judge_violations(routes: dict) -> list:
    """Checks on the optional "judges" key; v1 has none, so there is nothing to violate."""
    if "judges" not in routes:
        return []
    return []
