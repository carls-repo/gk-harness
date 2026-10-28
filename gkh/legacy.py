"""The fixed list of what the installer supersedes on a box that ran the pre-harness hooks.

Each item acts only if it exists. Settings entries and rc alias lines are removed on every
install (and recorded in the journal); the files are moved only by `install --retire-legacy`,
because sessions started before the install may still call them.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .paths import OWNED_MARKER

# A settings.json hook entry is legacy when its command names one of these, outside
# hooks/gk-harness/.
SETTINGS_COMMAND_NAMES = (
    "gk-profile-guard.sh",
    "hooks/gk-claim.py",
    "hooks/gk-claims-end.py",
    "hooks/gk-peers.py",
    "hooks/gk-remote-clear.py",
    "hooks/gk-remote-pending.py",
    "hooks/gk-remote-stop.py",
)

# Shell rc lines removed on install.
RC_ALIAS_RE = re.compile(r"^alias gk(-opus|-sonnet)?=")

# Moved by --retire-legacy into <backup>/legacy/<same relative path>. Relative to the Claude
# config folder, '/'-separated; the last one is a folder.
RETIRE_PATHS = (
    "hooks/gk-profile-guard.sh",
    "hooks/gk-profile-guard.test.sh",
    "hooks/gk-claim.py",
    "hooks/gk-claims-end.py",
    "hooks/gk-peers.py",
    "hooks/gk-remote-clear.py",
    "hooks/gk-remote-pending.py",
    "hooks/gk-remote-skip.py",
    "hooks/gk-remote-stop.py",
    "hooks/gk-coord.test.sh",
    "hooks/gk-remote.test.sh",
    "profiles",
)


def is_legacy_command(command: Any) -> bool:
    """True when command names a legacy hook script and is not under hooks/gk-harness/."""
    if not isinstance(command, str):
        return False
    c = command.replace("\\", "/")
    return OWNED_MARKER not in c and any(name in c for name in SETTINGS_COMMAND_NAMES)


def retire_plan(claude_dir: Path, backup_dir: Path) -> list[dict]:
    """[{rel, src, dst}] for each RETIRE_PATHS item that exists (a symlink counts), in list
    order; dst is <backup_dir>/legacy/<rel>."""
    plan = []
    for rel in RETIRE_PATHS:
        parts = rel.split("/")
        src = claude_dir.joinpath(*parts)
        if src.exists() or src.is_symlink():
            plan.append({"rel": rel, "src": src, "dst": backup_dir.joinpath("legacy", *parts)})
    return plan
