"""Model-to-profile mapping, transcript tail reading and per-session seat state.

Standard library only, Python 3.9+. Everything is defensive: a failure comes back as None
or False, never as an exception.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import gk_common

MODE = {"fable": "A", "opus": "B", "sonnet": "C"}
RANK = {"sonnet": 1, "opus": 2, "fable": 3}
_LINEUP = ("fable", "opus", "sonnet")
TAIL_BYTES = 262144


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
    """The model of the last main-thread assistant entry in the transcript's tail."""
    if not isinstance(path, (str, bytes, os.PathLike)):
        return None  # an int (or bool) would be taken as a file descriptor and closed
    try:
        with open(path, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            start = max(0, size - max_bytes)
            f.seek(start)
            raw = f.read()
    except Exception:
        return None
    lines = raw.split(b"\n")
    if start > 0:
        lines = lines[1:]  # the first line is probably cut in half
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            e = json.loads(line.decode("utf-8"))
        except Exception:
            continue
        if not isinstance(e, dict) or e.get("type") != "assistant" or e.get("isSidechain") is True:
            continue
        msg = e.get("message")
        model = msg.get("model") if isinstance(msg, dict) else None
        if isinstance(model, str) and profile_for_model(model) is not None:
            return model
    return None


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


def seat_card(model: Any, profile: Any, effort: Any) -> str:
    """The one-paragraph seat notice injected into the session."""
    if profile not in MODE:
        return ("gk-harness seat: the model was not reported. Read the model named in your "
                "system prompt, apply that mode, and pass profile explicitly on every "
                "Workflow call.")
    card = ("gk-harness seat: %s at %s → Mode %s (profile '%s'). "
            "Pass profile: '%s' on every Workflow call."
            % (model, effort or "unknown effort", MODE[profile], profile, profile))
    if effort != "high":
        card += " The policy expects effort high."
    if not in_lineup(model):
        card += (" %s is not in the lineup, so Mode %s rules apply; say so to Carl in your "
                 "first reply." % (model, MODE[profile]))
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
