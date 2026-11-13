"""The seat: model aliases, effort resolution, transcript tail reading, per-session seat
state and the seat card.

Standard library only, Python 3.9+. Everything is defensive: a failure comes back as None
or False, never as an exception.

The seat is a tier name from routes.json, `<alias>-<effort>` (for example `opus-medium`).
Hooks receive no effort field (Stage 0 proof 5), so the effort resolves in this order: the
launch command's --effort (or the stored seat's, when it came from there); the user
settings' `modelSettings.<model id>.effortLevel`, then `modelSettings.<alias>.effortLevel`;
then routes.json's seat effort for that alias.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import gk_common

_ALIASES = ("fable", "opus", "sonnet", "haiku")
TAIL_BYTES = 262144
_PROC_ROOT = "/proc"
_EFFORTS = ("low", "medium", "high", "xhigh", "max")
_FLAG_RE = re.compile(r"(?:^|\s)--(model|effort)(?:=|\s+)([^\s=-][^\s]*)")
_UNSET = object()
FABLE_OFF_NOTE = "Fable is off: run gkh fable on to restore it."
UNREPORTED_NOTE = ("Read the model named in your system prompt and pass seat (its tier, "
                   "e.g. opus-medium) on gk Workflow calls.")


def alias_for_model(model: Any) -> str | None:
    """haiku|sonnet|opus|fable when the model string names one, else None."""
    if not isinstance(model, str):
        return None
    m = model.lower()
    for a in _ALIASES:
        if a in m:
            return a
    return None


def _effort(value: Any) -> str | None:
    return value if isinstance(value, str) and value in _EFFORTS else None


def model_from_transcript(path: Any, max_bytes: int = TAIL_BYTES,
                          accept: Any = None) -> str | None:
    """The model of the last main-thread assistant entry, or model attachment, in the tail,
    among those `accept(model)` is truthy for (by default, a model with a known alias)."""
    if not isinstance(path, (str, bytes, os.PathLike)):
        return None  # an int (or bool) would be taken as a file descriptor and closed
    try:
        if not stat.S_ISREG(os.stat(path).st_mode):
            return None  # a FIFO would block in open(); a character device never ends
        with open(path, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            start = max(0, size - max_bytes)
            f.seek(max(0, start - 1))
            raw = f.read(max_bytes + (1 if start > 0 else 0))
    except Exception:
        return None
    if start > 0:
        # raw begins one byte before the window: if that byte ends a line the first line is
        # whole; otherwise the first line is cut and is dropped.
        if raw[:1] == b"\n":
            raw = raw[1:]
        else:
            nl = raw.find(b"\n")
            raw = raw[nl + 1:] if nl >= 0 else b""
    lines = raw.split(b"\n")
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            e = json.loads(line.decode("utf-8"))
        except Exception:
            continue
        if not isinstance(e, dict) or e.get("isSidechain") is True:
            continue
        model = None
        if e.get("type") == "assistant":
            msg = e.get("message")
            model = msg.get("model") if isinstance(msg, dict) else None
        elif e.get("type") == "attachment":
            att = e.get("attachment")
            if isinstance(att, dict) and att.get("type") == "model":
                ident = att.get("identity")
                model = ident.get("modelId") if isinstance(ident, dict) else None
        if isinstance(model, str) and (accept or alias_for_model)(model):
            return model
    return None


def parse_launch_flags(argv: list[str]) -> tuple[str | None, str | None]:
    """(model, effort) from a Claude Code argv; each is None unless it is usable."""
    values: dict = {}
    for i, arg in enumerate(argv):
        if not isinstance(arg, str):
            continue
        for flag in ("model", "effort"):
            if arg == "--" + flag:
                if i + 1 < len(argv) and isinstance(argv[i + 1], str):
                    values[flag] = argv[i + 1]
            elif arg.startswith("--" + flag + "="):
                values[flag] = arg.split("=", 1)[1]
    model = values.get("model")
    if alias_for_model(model) is None:
        model = None
    effort = values.get("effort")
    effort = effort.lower() if isinstance(effort, str) else None
    return model, (effort if effort in _EFFORTS else None)


def _is_claude_argv(argv: list[str]) -> bool:
    """Whether argv is a Claude Code process: argv[0] names claude, or a node process whose
    script (argv[1]) does, as in an npm install running cli.js."""
    if not argv:
        return False
    if "claude" in argv[0].lower():
        return True
    base = re.split(r"[\\/]", argv[0])[-1].lower()
    return base.startswith("node") and len(argv) > 1 and "claude" in argv[1].lower()


def _parse_ps_line(line: str) -> tuple[int, list[str]] | None:
    """(ppid, argv) from one `ps -o ppid=,args=` line (macOS), or None. The argv is the program
    (before the first ' -', split in two when it is a node process) followed by one
    --flag=value item per --model/--effort found in the line."""
    parts = line.strip().split(None, 1)
    if len(parts) < 2 or not parts[0].isdigit():
        return None
    args = parts[1]
    program = args.split(" -", 1)[0].strip()
    argv = [program]
    first = program.split(None, 1)
    if len(first) == 2 and re.split(r"[\\/]", first[0])[-1].lower().startswith("node"):
        argv = first
    for m in _FLAG_RE.finditer(args):
        argv.append("--%s=%s" % (m.group(1), m.group(2)))
    return int(parts[0]), argv


def _proc_info(pid: int) -> tuple[int, list[str]] | None:
    """(ppid, argv) of a process on Linux or macOS, or None. Linux reads /proc and spawns
    nothing; macOS runs one ps. On macOS argv is the program followed by --flag=value items."""
    try:
        if sys.platform.startswith("linux"):
            with open(os.path.join(_PROC_ROOT, str(pid), "stat"), "rb") as f:
                stat_text = f.read().decode("utf-8", "replace")
            ppid = int(stat_text[stat_text.rindex(")") + 1:].split()[1])
            with open(os.path.join(_PROC_ROOT, str(pid), "cmdline"), "rb") as f:
                raw = f.read().decode("utf-8", "replace")
            return ppid, [a for a in raw.split("\0") if a != ""]
        if sys.platform == "darwin":
            r = subprocess.run(["ps", "-ww", "-o", "ppid=,args=", "-p", str(pid)],
                               stdin=subprocess.DEVNULL, capture_output=True, timeout=1)
            if r.returncode != 0:
                return None
            return _parse_ps_line(r.stdout.decode("utf-8", "replace"))
    except Exception:
        return None
    return None


def launch_args(max_depth: int = 8) -> tuple[str | None, str | None]:
    """(model, effort) from the command line of the nearest Claude Code ancestor process.
    GK_NO_LAUNCH_ARGS=1 turns the lookup off (tests that run hooks as subprocesses)."""
    try:
        if os.environ.get("GK_NO_LAUNCH_ARGS") == "1":
            return None, None
        if not (sys.platform.startswith("linux") or sys.platform == "darwin"):
            return None, None
        pid = os.getppid()
        seen: set = set()
        for _ in range(max_depth):
            # A pid seen twice covers a self-loop (ppid == pid) and longer cycles alike.
            if pid <= 1 or pid in seen:
                return None, None
            seen.add(pid)
            info = _proc_info(pid)
            if info is None:
                return None, None
            ppid, argv = info
            if _is_claude_argv(argv):
                return parse_launch_flags(argv)
            pid = ppid
    except Exception:
        pass
    return None, None


def _routes_or(routes: Any) -> dict | None:
    return gk_common.load_routes() if routes is _UNSET else routes


def routes_effort(alias: Any, routes: Any) -> str | None:
    """routes.json's seat effort for an alias, or None (haiku has none)."""
    seat = routes.get("seat") if isinstance(routes, dict) else None
    efforts = seat.get("effort") if isinstance(seat, dict) else None
    return _effort(efforts.get(alias)) if isinstance(efforts, dict) else None


def user_settings() -> dict | None:
    data = gk_common.read_json(gk_common.claude_dir() / "settings.json")
    return data if isinstance(data, dict) else None


def settings_effort(model: Any, routes: Any = _UNSET) -> str | None:
    """`modelSettings.<model id>.effortLevel`, then `modelSettings.<alias>.effortLevel`, from
    the user settings.json. The id is the model string as reported, then routes.json's id for
    the alias. Only `effortLevel` counts: it is the key Claude Code reads (Stage 0 proof 5)."""
    alias = alias_for_model(model)
    settings = user_settings()
    per_model = settings.get("modelSettings") if settings else None
    if alias is None or not isinstance(per_model, dict):
        return None
    routes = _routes_or(routes)
    models = routes.get("models") if isinstance(routes, dict) else None
    entry = models.get(alias) if isinstance(models, dict) else None
    keys = [model] if model != alias else []
    if isinstance(entry, dict) and isinstance(entry.get("id"), str):
        keys.append(entry["id"])
    keys.append(alias)
    for key in keys:
        cfg = per_model.get(key)
        effort = _effort(cfg.get("effortLevel")) if isinstance(cfg, dict) else None
        if effort:
            return effort
    return None


def resolve_effort(model: Any, launch: Any = None,
                   routes: Any = _UNSET) -> tuple[str | None, str | None]:
    """(effort, where it came from: "launch" | "settings" | "routes"), or (None, None)."""
    if alias_for_model(model) is None:
        return None, None
    if _effort(launch):
        return launch, "launch"
    routes = _routes_or(routes)
    effort = settings_effort(model, routes)
    if effort:
        return effort, "settings"
    effort = routes_effort(alias_for_model(model), routes)
    if effort:
        return effort, "routes"
    return None, None


def seat_tier(model: Any, effort: Any = None, routes: Any = _UNSET) -> str | None:
    """The seat's tier name, `<alias>-<effort>`; the effort defaults to routes.json's seat
    effort for the alias. None when the model names no alias or no effort is known."""
    alias = alias_for_model(model)
    if alias is None:
        return None
    effort = _effort(effort) or routes_effort(alias, _routes_or(routes))
    return "%s-%s" % (alias, effort) if effort else None


def seat_path(sid: Any) -> Path:
    return gk_common.claude_dir() / "state" / "gk-seat" / ("%s.json" % sid)


def load_seat(sid: Any) -> dict | None:
    if gk_common.safe_sid(sid) is None:
        return None
    rec = gk_common.read_json(seat_path(sid))
    if isinstance(rec, dict) and alias_for_model(rec.get("model")) is not None:
        return rec
    return None


def save_seat(sid: Any, model: Any, effort: Any, source: Any, effort_from: Any = None) -> bool:
    """Record the seat; `effortFrom` says where the effort came from ("launch" carries over
    a /model switch, as the --effort flag does)."""
    if gk_common.safe_sid(sid) is None:
        return False
    rec = {
        "model": model, "effort": effort, "effortFrom": effort_from, "source": source,
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    return gk_common.write_json(seat_path(sid), rec)


def resolve_seat(data: Any) -> str | None:
    """The seat's tier for a Workflow call: the stored seat, else the transcript's model (or
    the launch command's) with the effort resolved as above. None when unknown."""
    if not isinstance(data, dict):
        return None
    routes = gk_common.load_routes()
    rec = load_seat(data.get("session_id"))
    if rec is not None:
        model = rec["model"]
        effort = _effort(rec.get("effort")) or resolve_effort(model, None, routes)[0]
        return seat_tier(model, effort, routes)
    model = model_from_transcript(data.get("transcript_path"))
    launch_model, launch_effort = launch_args()
    if model is None:
        model = launch_model
    return seat_tier(model, resolve_effort(model, launch_effort, routes)[0], routes)


def _clip(value: Any, limit: int = 80) -> str:
    text = value if isinstance(value, str) else str(value)
    text = "".join(c if c.isprintable() else "?" for c in text[: limit + 1])
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def seat_notes(model: Any, effort: Any, routes: Any) -> list[str]:
    """The card's notes, one sentence each: an effort off routes.json's seat effort (or a
    model routes.json has no seat effort for), a saved default model that is not Opus, and
    the Fable switch."""
    notes = []
    alias = alias_for_model(model)
    if alias is not None and isinstance(routes, dict):
        want = routes_effort(alias, routes)
        if want is None:
            notes.append("%s is not a seat model in routes.json; say so to Carl in your first "
                         "reply." % alias)
        elif effort != want:
            notes.append("routes.json puts the %s seat at %s." % (alias, want))
    settings = user_settings()
    default = settings.get("model") if settings else None
    if isinstance(default, str) and alias_for_model(default) != "opus":
        notes.append("The saved default model is %s, not opus (a /model choice is saved as "
                     "the default)." % _clip(default, 40))
    if gk_common.fable_off():
        notes.append(FABLE_OFF_NOTE)
    return notes


def seat_card(model: Any, effort: Any, routes: Any, notes: Any) -> str:
    """The seat notice injected into the session: `gk-harness seat: <model> at <effort> ·
    routes v<N>`, then one sentence per note."""
    version = routes.get("version") if isinstance(routes, dict) else None
    tail = ("routes v%d" % version if type(version) is int
            else "routes unavailable; tell Carl")
    sentences = [n for n in (notes if isinstance(notes, list) else []) if isinstance(n, str) and n]
    if alias_for_model(model) is None:
        head = "gk-harness seat: the model was not reported \u00b7 %s" % tail
        sentences.insert(0, UNREPORTED_NOTE)
    else:
        head = "gk-harness seat: %s at %s \u00b7 %s" % (
            _clip(model), _clip(effort) if effort else "unknown effort", tail)
    return head + (". " + " ".join(sentences) if sentences else "")
