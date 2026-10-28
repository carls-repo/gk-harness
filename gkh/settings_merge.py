"""settings.json: the hook registrations the installer owns, a strict loader, and a merge whose
`undo` record lets uninstall reverse it surgically.

The installer owns a hook entry exactly when its command contains hooks/gk-harness/ (with "\\"
read as "/"). Besides those it changes only `ultracode` (set to true) and the fixed legacy
entries in gkh.legacy; every other key and entry is left as it was. All functions here are pure:
they take a settings dict and return a new one, never changing their input.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any

from . import legacy
from .paths import OWNED_MARKER, quotable

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


def _ultracode_state(settings: dict) -> dict:
    if "ultracode" in settings:
        return {"present": True, "value": copy.deepcopy(settings["ultracode"])}
    return {"present": False}


def merge(settings: dict, python: Any, claude_dir: Any) -> tuple:
    """(new_settings, undo).

    new_settings has every legacy entry removed, any previously owned entries replaced by the
    REGISTRATIONS groups (in place of the first group the sweep emptied, else at the end of the
    event's list; events not yet present are added at the end of `hooks`) and `ultracode: true`.
    Running merge on its own output changes nothing.

    undo is JSON-ready, for the journal and unmerge():
      {"removed":   [{"event", "event_index", "group_index", "group", "group_removed",
                      "kept_index", "entry_index", "entry"}, ...]  # each legacy entry taken
                                                               # out, in order
       "ultracode": {"present": False} or {"present": True, "value": <prior value>},
       "events":    [event names in `hooks` before the merge], or None when `hooks` was absent,
       "created_events": [the event lists this merge added to `hooks`, in order]}
    """
    check_shape(settings)
    new = copy.deepcopy(settings)
    undo = {"removed": [], "ultracode": _ultracode_state(new),
            "events": list(new["hooks"]) if "hooks" in new else None, "created_events": []}
    hooks = new.setdefault("hooks", {})
    desired = desired_groups(python, claude_dir)
    for event_index, event in enumerate(list(hooks)):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept, anchor, removed_any = _sweep(groups, event, event_index, undo["removed"])
        if event in desired:
            at = len(kept) if anchor is None else anchor
            kept[at:at] = copy.deepcopy(desired[event])
        if removed_any and not kept:
            del hooks[event]
        else:
            hooks[event] = kept
    for event, groups in desired.items():
        if event not in hooks:
            hooks[event] = copy.deepcopy(groups)
            undo["created_events"].append(event)
    new["ultracode"] = True
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
    put the recorded legacy entries back, restore `ultracode`, and remove an event list only
    when the merge created it (undo["created_events"]) and it is now empty, and the `hooks` key
    only when the merge created it and it is now empty. Every other key, an empty event list
    the user added since included, is left as it is. Malformed parts of undo are ignored; with
    no usable created_events, only the event lists that held nothing but owned entries go."""
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

    prior = undo.get("ultracode")
    if isinstance(prior, dict) and prior.get("present") is True and "value" in prior:
        new["ultracode"] = copy.deepcopy(prior["value"])
    elif isinstance(prior, dict) and prior.get("present") is False:
        new.pop("ultracode", None)
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
