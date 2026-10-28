"""SessionStart / PostModelSwitch hook: record the seat and print the seat card.

Standard library only, Python 3.9+. Writes ~/.claude/state/gk-seat/<sid>.json, and on a
fresh start (at most once a day) checks whether the gk-harness checkout's origin is ahead.
"""
from __future__ import annotations

import calendar
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import gk_common
import gk_model

UPDATE_INTERVAL = 24 * 3600
GIT_TIMEOUT = 3
_TS = "%Y-%m-%dT%H:%M:%SZ"


def stamp_path() -> Path:
    return gk_common.claude_dir() / "state" / "gk-harness-update.json"


def _git(root: str, args: list[str]) -> subprocess.CompletedProcess | None:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="", SSH_ASKPASS="",
               SSH_ASKPASS_REQUIRE="never", GCM_INTERACTIVE="never")
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o ConnectTimeout=3")
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(k, None)
    extra: dict = {}
    if os.name == "nt":
        extra["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    else:
        extra["start_new_session"] = True  # no controlling tty for prompts
    try:
        return subprocess.run(["git", "-C", root] + args, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              encoding="utf-8", errors="replace",
                              timeout=GIT_TIMEOUT, env=env, **extra)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def origin_is_ahead(root: str) -> bool:
    """Whether origin's HEAD is a commit this checkout lacks or has not merged. Never fetches."""
    r = _git(root, ["ls-remote", "origin", "HEAD"])
    if r is None or r.returncode != 0:
        return False
    fields = r.stdout.split()
    head = fields[0] if fields else ""
    if not head or any(c not in "0123456789abcdefABCDEF" for c in head):
        return False
    r = _git(root, ["cat-file", "-e", head + "^{commit}"])
    if r is None:
        return False
    if r.returncode != 0:
        return True
    r = _git(root, ["merge-base", "--is-ancestor", head, "HEAD"])
    return r is not None and r.returncode == 1


def update_line(root: str) -> str:
    if os.name == "nt":
        return ('gk-harness update available: run powershell -NoProfile -ExecutionPolicy '
                'Bypass -File "%s/install.ps1" update' % root)
    return "gk-harness update available: run %s/install.sh update" % root


def update_notice() -> str | None:
    """The update line when a (cached or fresh) check says origin is ahead."""
    cfg = gk_common.read_json(gk_common.claude_dir() / "gk-harness.json")
    root = cfg.get("root") if isinstance(cfg, dict) else None
    if not isinstance(root, str) or not root or not os.path.isdir(root):
        return None
    stamp = gk_common.read_json(stamp_path())
    if isinstance(stamp, dict) and isinstance(stamp.get("checked"), str):
        try:
            age = time.time() - calendar.timegm(time.strptime(stamp["checked"], _TS))
        except (ValueError, OverflowError):
            age = None
        if age is not None and 0 <= age < UPDATE_INTERVAL:
            return update_line(root) if stamp.get("available") is True else None
    # Count the attempt before the network call, so a hook killed mid-check is not retried
    # at every startup.
    checked = time.strftime(_TS, time.gmtime())
    gk_common.write_json(stamp_path(), {"checked": checked, "available": False})
    available = origin_is_ahead(root)
    if available:
        gk_common.write_json(stamp_path(), {"checked": checked, "available": True})
    return update_line(root) if available else None


def _reply(event: str, card: str) -> None:
    gk_common.emit({"hookSpecificOutput": {"hookEventName": event, "additionalContext": card}})


def handle(data: Any) -> None:
    if not isinstance(data, dict):
        return
    event = data.get("hook_event_name")
    sid = data.get("session_id")
    if event not in ("SessionStart", "PostModelSwitch") or gk_common.safe_sid(sid) is None:
        return
    seat = gk_model.load_seat(sid) or {}
    effort = gk_model.effort_of(data)
    source = data.get("source")
    if event == "PostModelSwitch":
        model = data.get("to_model")
        if gk_model.profile_for_model(model) is None:
            return
        effort = effort or seat.get("effort")
    else:
        model = data.get("model")
        if gk_model.profile_for_model(model) is None:
            model = seat.get("model")
        if gk_model.profile_for_model(model) is None:
            model = gk_model.model_from_transcript(data.get("transcript_path"))
        effort = effort or seat.get("effort")
    profile = gk_model.profile_for_model(model)
    if profile is not None:
        gk_model.save_seat(sid, model, profile, effort, source if isinstance(source, str) else event)
    card = gk_model.seat_card(model, profile, effort)
    if event == "SessionStart" and source == "startup":
        line = update_notice()
        if line:
            card += "\n" + line
    _reply(event, card)


if __name__ == "__main__":
    gk_common.run_hook("gk-seat", handle)
