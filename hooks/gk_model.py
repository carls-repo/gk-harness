"""Model-to-profile mapping, transcript tail reading and per-session seat state.

Standard library only, Python 3.9+. Everything is defensive: a failure comes back as None
or False, never as an exception.
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

MODE = {"fable": "A", "opus": "B", "sonnet": "C"}
RANK = {"sonnet": 1, "opus": 2, "fable": 3}
_LINEUP = ("fable", "opus", "sonnet")
TAIL_BYTES = 262144
_PROC_ROOT = "/proc"
_EFFORTS = ("low", "medium", "high", "xhigh", "max")
_FLAG_RE = re.compile(r"(?:^|\s)--(model|effort)(?:=|\s+)([^\s=-][^\s]*)")


def profile_for_model(model: Any) -> str | None:
    """fable|opus|sonnet when the model string names one; sonnet for other claude/haiku models."""
    if not isinstance(model, str):
        return None
    m = model.lower()
    for p in _LINEUP:
        if p in m:
            return p
    if "claude" in m or "haiku" in m:
        return "sonnet"
    return None


def in_lineup(model: Any) -> bool:
    """Whether the model string names fable, opus or sonnet."""
    return isinstance(model, str) and any(p in model.lower() for p in _LINEUP)


def model_from_transcript(path: Any, max_bytes: int = TAIL_BYTES) -> str | None:
    """The model of the last main-thread assistant entry, or model attachment, in the tail."""
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
        if isinstance(model, str) and profile_for_model(model) is not None:
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
    if profile_for_model(model) is None:
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


def effort_of(data: Any) -> str | None:
    """The effort level from a hook input dict: a string, or a dict's string `level`."""
    e = data.get("effort") if isinstance(data, dict) else None
    if isinstance(e, str):
        return e
    if isinstance(e, dict) and isinstance(e.get("level"), str):
        return e["level"]
    return None


def seat_path(sid: Any) -> Path:
    return gk_common.claude_dir() / "state" / "gk-seat" / ("%s.json" % sid)


def load_seat(sid: Any) -> dict | None:
    if gk_common.safe_sid(sid) is None:
        return None
    rec = gk_common.read_json(seat_path(sid))
    if isinstance(rec, dict) and rec.get("profile") in _LINEUP:
        return rec
    return None


def save_seat(sid: Any, model: Any, profile: Any, effort: Any, source: Any) -> bool:
    if gk_common.safe_sid(sid) is None:
        return False
    rec = {
        "model": model, "profile": profile, "effort": effort, "source": source,
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    return gk_common.write_json(seat_path(sid), rec)


def _clip(value: Any, limit: int = 80) -> str:
    text = value if isinstance(value, str) else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def seat_card(model: Any, profile: Any, effort: Any) -> str:
    """The one-paragraph seat notice injected into the session."""
    if not isinstance(profile, str) or profile not in MODE:
        return ("gk-harness seat: the model was not reported. Read the model named in your "
                "system prompt, apply that mode, and pass profile explicitly on every "
                "Workflow call.")
    reported = profile_for_model(model) is not None
    name = _clip(model) if reported else "an unreported model"
    card = ("gk-harness seat: %s at %s \u2192 Mode %s (profile '%s'). "
            "Pass profile: '%s' on every Workflow call."
            % (name, _clip(effort) if effort else "unknown effort", MODE[profile], profile,
               profile))
    if effort != "high":
        card += " The policy expects effort high."
    if reported and not in_lineup(model):
        card += (" %s is not in the lineup, so Mode %s rules apply; say so to Carl in your "
                 "first reply." % (name, MODE[profile]))
    return card


def resolve_profile(data: Any) -> str | None:
    """Seat state, then the transcript tail, then $GK_PROFILE, else None."""
    if isinstance(data, dict):
        seat = load_seat(data.get("session_id"))
        if seat:
            return seat["profile"]
        p = profile_for_model(model_from_transcript(data.get("transcript_path")))
        if p:
            return p
    env = os.environ.get("GK_PROFILE")
    return env if env in _LINEUP else None
