"""routes.json: the single source of truth for which model and effort each agent and stage uses.

Version 2 holds:
  "efforts": [...], "models": {alias: {id, rank, display, price, priceDate}},
  "finishingWork": text appended to every low- and medium-effort tier body (gen.py),
  "tiers": {name: {model, effort, agent, description, body}},
  "retired": {old agent name: tier}, rendered with the tier's model, effort and body,
  "agents": {name: {tier, tools, description, body}}, extra agents such as Explore,
  "ladder": [tier, ...], the escalation order, strictly rising,
  "classes": {name: {tier ("seat" or a tier), who, check, next?, floor?}},
  "stages": {"<workflow>.<role>": {tier ("byClass" with "byClass": {class: tier}),
             when?: {condition: tier}, atLeast?: "implementer", authorFloor?, apex?,
             noFable?: tier | "none" | {class: tier}, onFail?: rerun | unfinished | hold}},
  "judges": {judge stage: [judged stage, ...]},
  "seat": {default, effort: {alias: effort}, launchers: {name: tier[+flag]}, subagentDefault},
  "evidence": {class, stage or "seat": record id or "guide"}.

Capability is ordered by model rank first, then by effort position in "efforts".
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

AGENT_NAME = re.compile(r"^worker-[a-z0-9-]+$")
EXTRA_AGENT_NAMES = ("Explore",)
REQUIRED = ("version", "efforts", "models", "tiers")
PRICE_KEYS = ("input", "output", "cacheWrite", "cacheRead")
WHO = ("seat", "worker", "workflow")
ON_FAIL = ("rerun", "unfinished", "hold")
SEAT_TIER = "seat"
BY_CLASS = "byClass"
NO_FABLE_NONE = "none"


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


def _has(mapping: dict, key: Any) -> bool:
    """key in mapping, for a key of any JSON type (lists and objects are never keys)."""
    return isinstance(key, str) and key in mapping


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _section(routes: dict, key: str, out: list) -> dict:
    """routes[key] when it is an object; {} (with a problem when present but wrong) otherwise."""
    value = routes.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        out.append("%s: must be an object" % key)
        return {}
    return value


def _is_fable(routes: dict, tier_name: Any) -> bool:
    tiers = routes.get("tiers")
    t = tiers.get(tier_name) if isinstance(tiers, dict) and isinstance(tier_name, str) else None
    return isinstance(t, dict) and t.get("model") == "fable"


def problems(routes: dict) -> list:
    """Every way routes breaks the shape above, as readable strings. Never raises."""
    out = []
    efforts = routes.get("efforts")
    if not isinstance(efforts, list) or not all(isinstance(e, str) for e in efforts):
        out.append("efforts: must be a list of effort names")
        efforts = []
    models = _section(routes, "models", out)
    tiers = _section(routes, "tiers", out)
    out += _model_problems(models)
    out += _tier_problems(tiers, models, efforts)
    tier_agents = {t.get("agent") for t in tiers.values()
                   if isinstance(t, dict) and isinstance(t.get("agent"), str)}
    extras = _section(routes, "agents", out)
    out += _agent_problems(extras, tiers, tier_agents)
    out += _retired_problems(_section(routes, "retired", out), tiers, tier_agents | set(extras))
    out += _ladder_problems(routes, tiers)
    classes = _section(routes, "classes", out)
    out += _class_problems(classes, tiers)
    stages = _section(routes, "stages", out)
    judges = _section(routes, "judges", out)
    out += _stage_problems(routes, stages, tiers, judges)
    out += _judges_problems(judges, stages)
    seat = _section(routes, "seat", out)
    out += _seat_problems(seat, tiers, models, efforts)
    out += _evidence_problems(routes, classes, stages, seat)
    return out


def _model_problems(models: dict) -> list:
    out = []
    for alias, m in models.items():
        if not isinstance(m, dict):
            out.append("model %s: must be an object" % alias)
            continue
        rank = m.get("rank")
        if not isinstance(rank, int) or isinstance(rank, bool):
            out.append("model %s: rank must be an integer" % alias)
        for field in ("price", "priceOver100k"):
            if field not in m:
                continue
            price = m[field]
            if not isinstance(price, dict):
                out.append("model %s: %s must be an object" % (alias, field))
                continue
            for key in PRICE_KEYS:
                if not _positive(price.get(key)):
                    out.append("model %s: %s.%s must be a positive number" % (alias, field, key))
    return out


def _tier_problems(tiers: dict, models: dict, efforts: list) -> list:
    out = []
    seen = {}
    for name, t in tiers.items():
        if not isinstance(t, dict):
            out.append("tier %s: must be an object" % name)
            continue
        if not _has(models, t.get("model")):
            out.append("tier %s: unknown model %r" % (name, t.get("model")))
        if not isinstance(t.get("effort"), str) or t.get("effort") not in efforts:
            out.append("tier %s: unknown effort %r" % (name, t.get("effort")))
        for field in ("description", "body"):
            if not _text(t.get(field)):
                out.append("tier %s: %s must be a non-empty string" % (name, field))
        agent = t.get("agent")
        if not isinstance(agent, str) or not (AGENT_NAME.match(agent) or agent in EXTRA_AGENT_NAMES):
            out.append("tier %s: agent name %r must match ^worker-[a-z0-9-]+$ or be Explore" % (name, agent))
        elif agent in seen:
            out.append("tier %s: agent name %s duplicates tier %s" % (name, agent, seen[agent]))
        else:
            seen[agent] = name
    return out


def _agent_problems(extras: dict, tiers: dict, tier_agents: set) -> list:
    out = []
    for name, a in extras.items():
        if not isinstance(a, dict):
            out.append("agent %s: must be an object" % name)
            continue
        if not (AGENT_NAME.match(name) or name in EXTRA_AGENT_NAMES):
            out.append("agent %s: name must match ^worker-[a-z0-9-]+$ or be Explore" % name)
        if name in tier_agents:
            out.append("agent %s: name duplicates a tier agent" % name)
        if not _has(tiers, a.get("tier")):
            out.append("agent %s: unknown tier %r" % (name, a.get("tier")))
        if "tools" in a and not _text(a.get("tools")):
            out.append("agent %s: tools must be a non-empty string" % name)
        for field in ("description", "body"):
            if not _text(a.get(field)):
                out.append("agent %s: %s must be a non-empty string" % (name, field))
    return out


def _retired_problems(retired: dict, tiers: dict, live: set) -> list:
    out = []
    for name, target in retired.items():
        if not AGENT_NAME.match(name):
            out.append("retired %s: name must match ^worker-[a-z0-9-]+$" % name)
        if name in live:
            out.append("retired %s: name is still a live agent" % name)
        if not _has(tiers, target):
            out.append("retired %s: unknown tier %r" % (name, target))
    return out


def _ladder_problems(routes: dict, tiers: dict) -> list:
    ladder = routes.get("ladder")
    if ladder is None:
        return []
    if not isinstance(ladder, list) or not ladder:
        return ["ladder: must be a non-empty list of tiers"]
    unknown = [t for t in ladder if not _has(tiers, t)]
    if unknown:
        return ["ladder: unknown tiers %s" % ", ".join(map(repr, unknown))]
    try:
        caps = [capability(routes, t) for t in ladder]
    except RoutesError as e:
        return ["ladder: %s" % e]
    if any(a >= b for a, b in zip(caps, caps[1:])):
        return ["ladder: capability must rise strictly at every rung"]
    return []


def _class_problems(classes: dict, tiers: dict) -> list:
    out = []
    for name, c in classes.items():
        if not isinstance(c, dict):
            out.append("class %s: must be an object" % name)
            continue
        if c.get("tier") != SEAT_TIER and not _has(tiers, c.get("tier")):
            out.append("class %s: unknown tier %r" % (name, c.get("tier")))
        for key in ("next", "floor"):
            if key in c and not _has(tiers, c[key]):
                out.append("class %s: unknown %s tier %r" % (name, key, c[key]))
        if c.get("who") not in WHO:
            out.append("class %s: who must be one of %s" % (name, ", ".join(WHO)))
        if not _text(c.get("check")):
            out.append("class %s: check must be a non-empty string" % name)
    return out


def _stage_problems(routes: dict, stages: dict, tiers: dict, judges: dict) -> list:
    out = []
    for key, s in stages.items():
        where = "stage %s" % key
        if not isinstance(s, dict):
            out.append("%s: must be an object" % where)
            continue
        if "." not in key:
            out.append("%s: key must be <workflow>.<role>" % where)
        by_class = s.get(BY_CLASS)
        if s.get("tier") == BY_CLASS:
            if not isinstance(by_class, dict) or not by_class:
                out.append("%s: tier byClass needs a non-empty byClass object" % where)
                by_class = {}
            for cls, t in by_class.items():
                if not _has(tiers, t):
                    out.append("%s: class %s has unknown tier %r" % (where, cls, t))
        elif not _has(tiers, s.get("tier")):
            out.append("%s: unknown tier %r" % (where, s.get("tier")))
        when = s.get("when", {})
        if not isinstance(when, dict):
            out.append("%s: when must be an object" % where)
        else:
            for cond, t in when.items():
                if not _has(tiers, t):
                    out.append("%s: condition %s has unknown tier %r" % (where, cond, t))
        if "atLeast" in s:
            if s["atLeast"] != "implementer":
                out.append("%s: atLeast must be \"implementer\"" % where)
            elif not judges.get(key):
                out.append("%s: atLeast needs a judges entry naming the stage it covers" % where)
        for flag in ("apex", "authorFloor"):
            if flag in s and not isinstance(s[flag], bool):
                out.append("%s: %s must be true or false" % (where, flag))
        if "onFail" in s and s["onFail"] not in ON_FAIL:
            out.append("%s: onFail must be one of %s" % (where, ", ".join(ON_FAIL)))
        out += _no_fable_problems(routes, key, s, tiers, where)
    return out


def _no_fable_problems(routes: dict, key: str, s: dict, tiers: dict, where: str) -> list:
    out = []
    nf = s.get("noFable")
    if isinstance(nf, dict):
        if s.get("tier") != BY_CLASS:
            out.append("%s: a per-class noFable needs tier byClass" % where)
        for cls, t in nf.items():
            if not _has(tiers, t) or _is_fable(routes, t):
                out.append("%s: noFable for class %s must be a non-Fable tier, not %r" % (where, cls, t))
    elif nf is not None and nf != NO_FABLE_NONE and (not _has(tiers, nf) or _is_fable(routes, nf)):
        out.append("%s: noFable must be a non-Fable tier or \"none\", not %r" % (where, nf))
    if not reaches_fable(routes, key):
        return out
    if nf is None:
        out.append("%s: can run on Fable, so it needs noFable" % where)
    elif isinstance(nf, dict):
        by_class = s.get(BY_CLASS) if isinstance(s.get(BY_CLASS), dict) else {}
        need = {c for c, t in by_class.items() if _is_fable(routes, t)}
        missing = sorted(need - set(nf))
        if missing:
            out.append("%s: noFable has no tier for Fable classes %s" % (where, ", ".join(missing)))
    if s.get("onFail") not in ON_FAIL:
        out.append("%s: can run on Fable, so it needs onFail (%s)" % (where, ", ".join(ON_FAIL)))
    return out


def _judges_problems(judges: dict, stages: dict) -> list:
    out = []
    for judge, judged in judges.items():
        if judge not in stages:
            out.append("judges: unknown judge stage %s" % judge)
        if not isinstance(judged, list) or not judged:
            out.append("judges %s: must list the stages it judges" % judge)
            continue
        for s in judged:
            if not _has(stages, s):
                out.append("judges %s: unknown judged stage %r" % (judge, s))
    return out


def _seat_problems(seat: dict, tiers: dict, models: dict, efforts: list) -> list:
    if not seat:
        return []
    out = []
    if not _has(tiers, seat.get("default")):
        out.append("seat: unknown default tier %r" % seat.get("default"))
    effort = seat.get("effort")
    if not isinstance(effort, dict):
        out.append("seat: effort must be an object")
    else:
        for alias, e in effort.items():
            if alias not in models or not isinstance(e, str) or e not in efforts:
                out.append("seat: effort %s=%r names an unknown model or effort" % (alias, e))
    launchers = seat.get("launchers")
    if not isinstance(launchers, dict):
        out.append("seat: launchers must be an object")
    else:
        for name, value in launchers.items():
            if not isinstance(value, str) or value.split("+", 1)[0] not in tiers:
                out.append("seat: launcher %s has unknown tier %r" % (name, value))
    if not _has(models, seat.get("subagentDefault")):
        out.append("seat: unknown subagentDefault %r" % seat.get("subagentDefault"))
    return out


def _evidence_problems(routes: dict, classes: dict, stages: dict, seat: dict) -> list:
    routes_needing = list(classes) + list(stages) + (["seat"] if seat else [])
    if not routes_needing:
        return []
    evidence = routes.get("evidence")
    if not isinstance(evidence, dict):
        return ["evidence: must be an object naming a record or \"guide\" for every route"]
    missing = [k for k in routes_needing if not _text(evidence.get(k))]
    if missing:
        return ["evidence: no record or \"guide\" for %s" % ", ".join(missing)]
    return []


def tier(routes: dict, name: str) -> dict:
    tiers = routes.get("tiers")
    t = tiers.get(name) if isinstance(tiers, dict) and isinstance(name, str) else None
    if not isinstance(t, dict):
        raise RoutesError("unknown tier %r" % name)
    return {k: t.get(k) for k in ("model", "effort", "agent", "description", "body")}


def capability(routes: dict, name: str) -> tuple:
    t = tier(routes, name)
    try:
        rank = routes["models"][t["model"]]["rank"]
        efforts = routes["efforts"]
        if not isinstance(rank, int) or isinstance(rank, bool) or not isinstance(efforts, list):
            raise TypeError
        return (rank, efforts.index(t["effort"]) + 1)
    except (KeyError, ValueError, TypeError):
        raise RoutesError("tier %r has an unknown model or effort" % name)


def _higher(routes: dict, a: str, b: str) -> str:
    return a if capability(routes, a) >= capability(routes, b) else b


def stage_variants(routes: dict, key: str, fable: bool = True, _seen: tuple = ()) -> dict:
    """{condition: tier} for every tier the stage can run on. Condition "" is the base tier,
    "class:<c>" a byClass entry, any other name a `when` condition.

    `atLeast: "implementer"` lifts the base (the floor) to each tier of the stages this stage
    judges (its `judges` entry), condition by condition. With fable=False, a Fable tier is
    replaced by the stage's noFable (its per-class entry for a byClass stage); a condition whose
    noFable is "none" or missing is dropped, because the stage does not run there."""
    stages = routes.get("stages")
    s = stages.get(key) if isinstance(stages, dict) and isinstance(key, str) else None
    if not isinstance(s, dict):
        raise RoutesError("unknown stage %r" % key)
    out = {}
    if s.get("tier") == BY_CLASS:
        by_class = s.get(BY_CLASS)
        for cls, t in (by_class.items() if isinstance(by_class, dict) else ()):
            out["class:" + cls] = t
    else:
        base = s.get("tier")
        out[""] = base
        when = s.get("when")
        if isinstance(when, dict):
            out.update(when)
        if s.get("atLeast"):
            judges = routes.get("judges") if isinstance(routes.get("judges"), dict) else {}
            judged_list = judges.get(key)
            for judged in judged_list if isinstance(judged_list, list) else []:
                if judged in _seen or judged == key:
                    continue
                for cond, t in stage_variants(routes, judged, True, _seen + (key,)).items():
                    out[cond] = _higher(routes, out.get(cond, base), t)
    if fable:
        return out
    nf = s.get("noFable")
    mapped = {}
    for cond, t in out.items():
        if not _is_fable(routes, t):
            mapped[cond] = t
            continue
        sub = nf.get(cond[len("class:"):]) if isinstance(nf, dict) and cond.startswith("class:") else nf
        if isinstance(sub, str) and sub != NO_FABLE_NONE:
            mapped[cond] = sub
    return mapped


def reaches_fable(routes: dict, key: str) -> bool:
    """True when some condition, class or atLeast lift puts the stage on a Fable tier."""
    try:
        return any(_is_fable(routes, t) for t in stage_variants(routes, key).values())
    except RoutesError:
        return False


def judge_violations(routes: dict) -> list:
    """One string per (judge, judged) pair in "judges" where the judge can run below the work.

    Both stages are expanded with stage_variants, with Fable on and with Fable off. Each judged
    condition is compared with the judge's tier for the same condition, or the judge's base tier
    when the judge has none; so a judge's conditional tier counts only against the same condition
    on the judged stage. An atLeast judge (gk-build.review) carries the judged stage's class
    conditions, its floor lifted to each class tier, so the floor is checked against every class.
    A judge that does not run where the judged stage runs is a violation too."""
    judges = routes.get("judges")
    if not isinstance(judges, dict):
        return []
    out = []
    for judge, judged_list in judges.items():
        for judged in judged_list if isinstance(judged_list, list) else []:
            fails = []
            try:
                for fable in (True, False):
                    mode = "" if fable else " (Fable off)"
                    jv = stage_variants(routes, judge, fable)
                    for cond, st in sorted(stage_variants(routes, judged, fable).items()):
                        jt = jv.get(cond, jv.get(""))
                        label = cond or "base"
                        if jt is None:
                            fails.append("[%s%s] the judge does not run, the work runs on %s"
                                         % (label, mode, st))
                        elif capability(routes, jt) < capability(routes, st):
                            fails.append("[%s%s] %s is below %s" % (label, mode, jt, st))
            except RoutesError as e:
                fails.append(str(e))
            if fails:
                out.append("%s does not cover %s: %s" % (judge, judged, "; ".join(fails)))
    return out


def agent_names(routes: dict) -> list:
    """Sorted names of every agent file the generator writes: each tier's agent, each key of
    the optional "retired" map and each key of the optional "agents" map (Explore)."""
    names = {t.get("agent") for t in (routes.get("tiers") or {}).values() if isinstance(t, dict)}
    names |= set((routes.get("retired") or {}).keys())
    names |= set((routes.get("agents") or {}).keys())
    return sorted(n for n in names if n)
