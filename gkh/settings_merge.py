"""settings.json: the hook registrations the installer owns, the settings keys it owns, a strict
loader, and a merge whose `undo` record lets uninstall reverse it surgically.

The installer owns a hook entry exactly when its command contains hooks/gk-harness/ (with "\\"
read as "/"). Besides those it changes only the fixed legacy entries in gkh.legacy and the owned
keys, which owned_desired() reads from routes.json: the default `model`, each seat model's
`modelSettings.<model id>.effortLevel`, `env.CLAUDE_CODE_SUBAGENT_MODEL` and `ultracode`. An
owned key is written when the installer first adopts it, and after that only while it still
holds what the installer last wrote: a value changed by hand is never overwritten. Every other
key and entry is left as it was. All functions here are pure: they take a settings dict and
return a new one, never changing their input.

An owned key's path is a tuple of object keys; the journal holds it dotted ("model",
"modelSettings.claude-opus-5-5.effortLevel"). owned_desired() refuses a key holding ".", so the
dotted form always parses back to the same tuple.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any

from . import legacy
from .paths import OWNED_MARKER, quotable
from .routes import RoutesError, tier as routes_tier

HOOK_TIMEOUT = 10

# (event, matcher or None, scripts in order). Each row becomes one hook group.
REGISTRATIONS = (
    ("SessionStart", None, ("gk-seat.py", "gk-peers.py")),
    ("PostModelSwitch", None, ("gk-seat.py",)),
    ("UserPromptSubmit", None, ("gk-peers.py",)),
    ("PreToolUse", "Workflow", ("gk-profile.py",)),
    ("PostToolUse", "Edit|Write|MultiEdit|NotebookEdit", ("gk-claim.py", "gk-remote-pending.py")),
    ("PostToolUse", "mcp__.*__(batch|update|create)|Artifact|SendUserFile",
     ("gk-remote-clear.py",)),
    ("Stop", None, ("gk-remote-stop.py",)),
    ("SessionEnd", None, ("gk-claims-end.py",)),
)
REGISTERED_EVENTS = tuple(dict.fromkeys(ev for ev, _, _ in REGISTRATIONS))

_MISSING = object()


class SettingsError(ValueError):
    """settings.json cannot be read, is not strict JSON, or has a shape the merge cannot use.
    The message names the file."""


def hook_command(python: Any, claude_dir: Any, script: str) -> str:
    """'"<python>" "<claude_dir>/hooks/gk-harness/<script>"', both paths posix and quoted.
    Raises ValueError (paths.quotable) when either path holds '"', '$' or '`'."""
    return '"%s" "%s/%s%s"' % (quotable(python), quotable(claude_dir), OWNED_MARKER, script)


def is_owned(command: Any) -> bool:
    return isinstance(command, str) and OWNED_MARKER in command.replace("\\", "/")


def desired_groups(python: Any, claude_dir: Any) -> dict:
    """{event: [group, ...]} for REGISTRATIONS, in table order."""
    out: dict = {}
    for event, matcher, scripts in REGISTRATIONS:
        group: dict = {} if matcher is None else {"matcher": matcher}
        group["hooks"] = [{"type": "command", "command": hook_command(python, claude_dir, s),
                           "timeout": HOOK_TIMEOUT} for s in scripts]
        out.setdefault(event, []).append(group)
    return out


# --- strict loading -------------------------------------------------------------------------

def _no_duplicate_keys(pairs: list) -> dict:
    obj: dict = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate key %s" % json.dumps(key))
        obj[key] = value
    return obj


def _no_constants(name: str) -> Any:
    raise ValueError("%s is not allowed" % name)


def _finite_float(text: str) -> float:
    """A JSON number with a fraction or exponent, refused when it overflows to infinity (such as
    1e400), which dumps would write back as Infinity, which is not JSON."""
    value = float(text)
    if math.isinf(value) or math.isnan(value):
        raise ValueError("the number %s is out of range" % text)
    return value


def check_shape(settings: Any, label: str = "settings.json") -> None:
    """Raise SettingsError unless settings is an object whose `hooks`, if present, is an object
    and whose registered events, if present, are lists. Other events are not ours to judge."""
    if not isinstance(settings, dict):
        raise SettingsError("%s does not hold a JSON object" % label)
    if "hooks" not in settings:
        return
    hooks = settings["hooks"]
    if not isinstance(hooks, dict):
        raise SettingsError('%s: "hooks" is not an object' % label)
    for event in REGISTERED_EVENTS:
        if event in hooks and not isinstance(hooks[event], list):
            raise SettingsError('%s: "hooks.%s" is not a list' % (label, event))


def load_strict(path: Any) -> dict | None:
    """The parsed settings, or None when the file is absent.

    Strict: no comments, no trailing commas, nothing truncated or trailing, no NaN/Infinity and
    no number that overflows to infinity (a rewrite would write Infinity), no duplicate keys (a
    rewrite would silently drop one). A UTF-8 byte-order mark is allowed.
    Raises SettingsError, naming the file and where it fails.
    """
    label = str(path)
    try:
        data = Path(path).read_bytes()
    except FileNotFoundError:
        return None
    except OSError as e:
        raise SettingsError("%s cannot be read: %s" % (label, e.strerror or e)) from None
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise SettingsError("%s is not UTF-8 text" % label) from None
    try:
        settings = json.loads(text, object_pairs_hook=_no_duplicate_keys,
                              parse_constant=_no_constants, parse_float=_finite_float)
    except json.JSONDecodeError as e:
        raise SettingsError("%s is not strict JSON: %s (line %d, column %d)"
                            % (label, e.msg, e.lineno, e.colno)) from None
    except (ValueError, RecursionError) as e:
        raise SettingsError("%s is not strict JSON: %s" % (label, e)) from None
    check_shape(settings, label)
    return settings


def dumps(settings: Any) -> str:
    """Claude Code's own layout: two-space indent, non-ASCII kept, a final newline."""
    return json.dumps(settings, indent=2, ensure_ascii=False) + "\n"


# --- merge and undo -------------------------------------------------------------------------

def _sweep(groups: list, event: str, event_index: int, records: list | None) -> tuple:
    """Drop owned entries (and, when records is a list, legacy entries, recording each) from
    groups. Returns (kept_groups, anchor, removed_any): anchor is where the first group emptied
    by the sweep stood among the kept groups, or None. Groups that are not {..., "hooks": [...]}
    are kept as they are. Each record's kept_index is where the group that still holds the
    rest of its entries stands among the kept groups, or None when the sweep emptied it."""
    kept: list = []
    anchor = None
    removed_any = False
    for gi, group in enumerate(groups):
        entries = group.get("hooks") if isinstance(group, dict) else None
        if not isinstance(entries, list):
            kept.append(group)
            continue
        keep_entries, made, dropped = [], [], False
        for xi, entry in enumerate(entries):
            command = entry.get("command") if isinstance(entry, dict) else None
            if is_owned(command):
                dropped = True
            elif records is not None and legacy.is_legacy_command(command):
                dropped = True
                made.append({"event": event, "event_index": event_index, "group_index": gi,
                             "group": {k: ([] if k == "hooks" else copy.deepcopy(v))
                                       for k, v in group.items()},
                             "entry_index": xi, "entry": copy.deepcopy(entry)})
            else:
                keep_entries.append(entry)
        if not dropped:
            kept.append(group)
            continue
        removed_any = True
        kept_index = len(kept) if keep_entries else None
        for r in made:
            r["group_removed"] = not keep_entries
            r["kept_index"] = kept_index
            records.append(r)
        if keep_entries:
            survivor = dict(group)
            survivor["hooks"] = keep_entries
            kept.append(survivor)
        elif anchor is None:
            anchor = len(kept)
    return kept, anchor, removed_any


# --- owned keys -----------------------------------------------------------------------------

def owned_desired(routes: dict) -> dict:
    """{path tuple: value} for the keys the installer owns, read from routes.json:
      ("model",)                                   the seat default tier's model alias;
      ("modelSettings", <model id>, "effortLevel") seat.effort[alias], for each alias there,
                                                   keyed by models[alias].id (Stage 0 proof 5:
                                                   Claude Code reads effortLevel, not effort);
      ("env", "CLAUDE_CODE_SUBAGENT_MODEL")        seat.subagentDefault;
      ("ultracode",)                               False.
    Raises RoutesError when routes.json lacks any of them or names a model id holding "."."""
    try:
        seat = routes["seat"]
        models = routes["models"]
        out: dict = {("model",): routes_tier(routes, seat["default"])["model"]}
        for alias, effort in seat["effort"].items():
            out[("modelSettings", models[alias]["id"], "effortLevel")] = effort
        out[("env", "CLAUDE_CODE_SUBAGENT_MODEL")] = seat["subagentDefault"]
    except (KeyError, TypeError, AttributeError) as e:
        raise RoutesError("routes.json has no usable seat settings (%s)" % e) from None
    for path, value in out.items():
        if not all(isinstance(k, str) and k and "." not in k for k in path) \
                or not isinstance(value, str) or not value:
            raise RoutesError("routes.json gives an unusable settings key %r = %r"
                              % (path, value))
    out[("ultracode",)] = False
    return out


def dotted(path: tuple) -> str:
    return ".".join(path)


def _path(key: Any) -> tuple | None:
    """The path tuple of a dotted key read from the journal, or None when it is not one."""
    if not isinstance(key, str):
        return None
    path = tuple(key.split("."))
    return path if all(path) else None


def _get(settings: Any, path: tuple) -> Any:
    """The value at path, or _MISSING when it, or an object on the way to it, is absent."""
    node = settings
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return _MISSING
        node = node[key]
    return node


def _state(settings: dict, path: tuple) -> dict:
    value = _get(settings, path)
    if value is _MISSING:
        return {"present": False}
    return {"present": True, "value": copy.deepcopy(value)}


def _same(value: Any, other: Any) -> bool:
    """Equal as JSON: true is not 1 and 1 is not 1.0, as they would be to Python's ==."""
    if value is _MISSING or other is _MISSING:
        return value is other
    return json.dumps(value, sort_keys=True) == json.dumps(other, sort_keys=True)


def _valid_prior(prior: Any) -> bool:
    return isinstance(prior, dict) and (prior.get("present") is False or (
        prior.get("present") is True and "value" in prior))


def _put(settings: dict, path: tuple, value: Any, created: list) -> None:
    """Set path to value, making each missing object on the way (recorded, dotted, in created).
    Raises SettingsError when something on the way is not an object."""
    node = settings
    for i, key in enumerate(path[:-1]):
        if key not in node:
            node[key] = {}
            created.append(dotted(path[:i + 1]))
        elif not isinstance(node[key], dict):
            raise SettingsError('settings.json: "%s" is not an object' % dotted(path[:i + 1]))
        node = node[key]
    node[path[-1]] = copy.deepcopy(value)


def _own(new: dict, desired: dict, last: dict, priors: dict, undo: dict) -> None:
    """Write the desired keys into new, recording each in undo["owned"]: first adoption (no
    `last` entry) writes and records the value found as the prior; after that a key is written
    only while it still holds last[key], carrying its first prior forward, and a key changed by
    hand is left as it is, its record carried forward unchanged."""
    for path, value in desired.items():
        key = dotted(path)
        found = _state(new, path)
        prior = found
        if key in last:
            if _valid_prior(priors.get(key)):
                prior = priors[key]
            if not (found["present"] and _same(found["value"], last[key])):
                undo["owned"][key] = {"prior": copy.deepcopy(prior),
                                      "wrote": copy.deepcopy(last[key])}
                continue
        _put(new, path, value, undo["created_objects"])
        undo["owned"][key] = {"prior": copy.deepcopy(prior), "wrote": copy.deepcopy(value)}


def hand_changed(settings: Any, last: Any) -> list:
    """The dotted keys of last ({dotted key: the value the installer last wrote}) whose value in
    settings is now different, or absent, in last's order."""
    out = []
    for key, wrote in (last.items() if isinstance(last, dict) else ()):
        path = _path(key)
        if path is not None and not _same(_get(settings, path), wrote):
            out.append(key)
    return out


def owned_paths(undo: Any) -> list:
    """The dotted keys undo["owned"] records, in its order."""
    owned = undo.get("owned") if isinstance(undo, dict) else None
    return [k for k in owned if _path(k) is not None] if isinstance(owned, dict) else []


def _unown(new: dict, undo: dict) -> None:
    """Put back each owned key's prior where the key still holds what the merge wrote (a prior
    of {"present": False} removes it), then remove each object the merge created that is now
    empty, innermost first."""
    owned = undo.get("owned")
    for key, record in (owned.items() if isinstance(owned, dict) else ()):
        path = _path(key)
        if path is None or not isinstance(record, dict) or "wrote" not in record \
                or not _valid_prior(record.get("prior")):
            continue
        if not _same(_get(new, path), record["wrote"]):
            continue
        parent = _get(new, path[:-1]) if len(path) > 1 else new
        if record["prior"]["present"]:
            parent[path[-1]] = copy.deepcopy(record["prior"]["value"])
        else:
            del parent[path[-1]]
    created = undo.get("created_objects")
    for key in reversed(created if isinstance(created, list) else []):
        path = _path(key)
        if path is None:
            continue
        parent = _get(new, path[:-1]) if len(path) > 1 else new
        if isinstance(parent, dict) and parent.get(path[-1]) == {} \
                and isinstance(parent[path[-1]], dict):
            del parent[path[-1]]


def merge(settings: dict, python: Any, claude_dir: Any, desired: dict | None = None,
          last: dict | None = None, priors: dict | None = None) -> tuple:
    """(new_settings, undo).

    new_settings has every legacy entry removed, any previously owned entries replaced by the
    REGISTRATIONS groups (in place of the first group the sweep emptied, else at the end of the
    event's list; events not yet present are added at the end of `hooks`), and the owned keys
    of desired ({path tuple: value}, from owned_desired) written as _own() says, using last
    ({dotted key: the value the installer last wrote}) and priors ({dotted key: the prior
    recorded then}), both from the newest journal record of each key. A key in desired that a
    new object must hold is added at the end of its parent; one on its way that is not an
    object raises SettingsError. Running merge on its own output changes nothing.

    undo is JSON-ready, for the journal and unmerge():
      {"removed":   [{"event", "event_index", "group_index", "group", "group_removed",
                      "kept_index", "entry_index", "entry"}, ...]  # each legacy entry taken
                                                               # out, in order
       "events":    [event names in `hooks` before the merge], or None when `hooks` was absent,
       "created_events": [the event lists this merge added to `hooks`, in order],
       "owned":     {dotted key: {"prior": {"present": False} or {"present": True,
                                            "value": <value before the installer owned it>},
                                  "wrote": <the value the installer wrote, or last wrote>}},
       "created_objects": [dotted keys of the objects this merge made for owned keys, in order]}
    Versions before the owned keys recorded "ultracode" ({"present", "value"?}) instead: the
    value before they set it to true. unmerge() still reads it.
    """
    check_shape(settings)
    new = copy.deepcopy(settings)
    undo = {"removed": [], "events": list(new["hooks"]) if "hooks" in new else None,
            "created_events": [], "owned": {}, "created_objects": []}
    hooks = new.setdefault("hooks", {})
    groups_wanted = desired_groups(python, claude_dir)
    for event_index, event in enumerate(list(hooks)):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept, anchor, removed_any = _sweep(groups, event, event_index, undo["removed"])
        if event in groups_wanted:
            at = len(kept) if anchor is None else anchor
            kept[at:at] = copy.deepcopy(groups_wanted[event])
        if removed_any and not kept:
            del hooks[event]
        else:
            hooks[event] = kept
    for event, groups in groups_wanted.items():
        if event not in hooks:
            hooks[event] = copy.deepcopy(groups)
            undo["created_events"].append(event)
    _own(new, desired or {}, last or {}, priors or {}, undo)
    return new, undo


def strip_owned(settings: dict) -> dict:
    """settings without any owned entry. Groups, event lists and `hooks` that this emptied are
    removed; nothing else changes (ultracode included)."""
    new = copy.deepcopy(settings)
    hooks = new.get("hooks")
    if not isinstance(hooks, dict):
        return new
    had_events = bool(hooks)
    for event in list(hooks):
        if isinstance(hooks[event], list):
            kept, _, removed_any = _sweep(hooks[event], event, 0, None)
            if removed_any and not kept:
                del hooks[event]
            else:
                hooks[event] = kept
    if had_events and not hooks:
        del new["hooks"]
    return new


def _valid_record(r: Any) -> bool:
    if not isinstance(r, dict):
        return False
    ints = [r.get(k) for k in ("event_index", "group_index", "entry_index")]
    return (isinstance(r.get("event"), str) and isinstance(r.get("group"), dict)
            and isinstance(r.get("entry"), dict) and isinstance(r.get("group_removed"), bool)
            and all(isinstance(i, int) and not isinstance(i, bool) and i >= 0 for i in ints))


def _insert_key(d: dict, key: str, value: Any, index: int) -> None:
    items = list(d.items())
    items.insert(min(index, len(items)), (key, value))
    d.clear()
    d.update(items)


def _meta(group: dict) -> dict:
    return {k: v for k, v in group.items() if k != "hooks"}


def _survivor(base: list, r: dict, key: tuple, owner: dict) -> Any:
    """The group in base that still holds the rest of r's original group: the one at r's
    kept_index when it still has that group's keys, else the first group with those keys. A
    group already claimed for a different original group is never picked."""
    want = _meta(r["group"])

    def usable(g: Any) -> bool:
        return (isinstance(g, dict) and isinstance(g.get("hooks"), list) and _meta(g) == want
                and owner.get(id(g), key) == key)

    at = r.get("kept_index")
    if isinstance(at, int) and not isinstance(at, bool) and 0 <= at < len(base) \
            and usable(base[at]):
        return base[at]
    return next((g for g in base if usable(g)), None)


def _restore(new: dict, removed: list) -> None:
    """Put each recorded legacy entry back where it was, as far as the file still allows."""
    placed: dict = {}  # (event, original group index) -> the group its entries went back into
    owner: dict = {}  # id(group) -> the (event, original group index) that claimed it
    base: dict = {}  # event -> its groups as the sweep left them, before any re-insertion
    for r in removed:
        if not _valid_record(r):
            continue
        hooks = new.setdefault("hooks", {})
        if not isinstance(hooks, dict):
            return
        event = r["event"]
        if event not in hooks:
            _insert_key(hooks, event, [], r["event_index"])
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        if event not in base:
            base[event] = list(groups)
        key = (event, r["group_index"])
        group = placed.get(key)
        if group is None and not r["group_removed"]:
            group = _survivor(base[event], r, key, owner)
        if group is None:
            group = copy.deepcopy(r["group"])
            group["hooks"] = []
            groups.insert(min(r["group_index"], len(groups)), group)
        placed[key] = group
        owner[id(group)] = key
        group["hooks"].insert(min(r["entry_index"], len(group["hooks"])), copy.deepcopy(r["entry"]))


def _names(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(e, str) for e in value)


def unmerge(settings: dict, undo: Any) -> dict:
    """Reverse merge() surgically, keeping whatever else changed since: drop the owned entries,
    put the recorded legacy entries back, remove an event list only when the merge created it
    (undo["created_events"]) and it is now empty, and the `hooks` key only when the merge
    created it and it is now empty. Put each owned key's prior back only where the key still
    holds what the merge wrote, then remove the objects the merge created for them
    (undo["created_objects"]) that are now empty. An old record's "ultracode" prior comes back
    only while ultracode is still the true those versions wrote. Every other key, an empty
    event list the user added since included, is left as it is. Malformed parts of undo are
    ignored; with no usable created_events, only the event lists that held nothing but owned
    entries go."""
    undo = undo if isinstance(undo, dict) else {}
    new = copy.deepcopy(settings)
    hooks = new.get("hooks")
    emptied = set()
    if isinstance(hooks, dict):
        for event in list(hooks):
            if isinstance(hooks[event], list):
                kept, _, removed_any = _sweep(hooks[event], event, 0, None)
                hooks[event] = kept
                if removed_any and not kept:
                    emptied.add(event)
    removed = undo.get("removed")
    _restore(new, removed if isinstance(removed, list) else [])

    created = undo.get("created_events", _MISSING)
    hooks = new.get("hooks")
    if isinstance(hooks, dict):
        for event in (created if _names(created) else sorted(emptied)):
            if hooks.get(event) == []:
                del hooks[event]
        if undo.get("events", _MISSING) is None and not hooks:
            del new["hooks"]

    _unown(new, undo)
    prior = undo.get("ultracode")
    if _valid_prior(prior) and new.get("ultracode") is True:
        if prior["present"]:
            new["ultracode"] = copy.deepcopy(prior["value"])
        else:
            del new["ultracode"]
    return new


def owned_entries(settings: Any) -> list:
    """[(event, matcher or None, command)] for every owned entry, in file order."""
    out = []
    hooks = settings.get("hooks") if isinstance(settings, dict) else None
    if not isinstance(hooks, dict):
        return out
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            entries = group.get("hooks") if isinstance(group, dict) else None
            for entry in entries if isinstance(entries, list) else []:
                command = entry.get("command") if isinstance(entry, dict) else None
                if is_owned(command):
                    out.append((event, group.get("matcher"), command))
    return out
