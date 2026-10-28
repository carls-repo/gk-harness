"""The action journal at backups/gk-harness/manifest.json, and the file primitives it is
defined over (hashes of what was written, atomic writes, copies).

The journal is {"format": 1, "installs": [install, ...]}, oldest first. An install is
{"at", "version", "backup", "actions": [action, ...]}; each action records what one write
replaced (its prior state) and the sha256 of what was written, so uninstall can tell whether the
file is still exactly as the install left it. Paths are stored posix.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from pathlib import Path
from typing import Any

from .paths import posix, utc_iso

FORMAT = 1
ACTION_TYPES = ("file", "settings", "claude_md", "shell_rc", "legacy_move")
WRITE_RETRIES = 10
WRITE_RETRY_DELAY = 0.1


class ManifestError(ValueError):
    """The journal exists but cannot be read as one; the message names the file."""


# --- the journal ----------------------------------------------------------------------------

def empty() -> dict:
    return {"format": FORMAT, "installs": []}


def load(path: Any) -> dict:
    """The journal, or an empty one when the file is absent. Raises ManifestError otherwise."""
    label = str(path)
    try:
        data = Path(path).read_bytes()
    except FileNotFoundError:
        return empty()
    except OSError as e:
        raise ManifestError("%s cannot be read: %s" % (label, e.strerror or e)) from None
    try:
        journal = json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError) as e:
        raise ManifestError("%s is not valid JSON: %s" % (label, e)) from None
    if not (isinstance(journal, dict) and journal.get("format") == FORMAT
            and isinstance(journal.get("installs"), list)
            and all(isinstance(i, dict) and isinstance(i.get("actions"), list)
                    for i in journal["installs"])):
        raise ManifestError("%s is not a gk-harness journal (format %d)" % (label, FORMAT))
    return journal


def save(path: Any, journal: dict) -> str:
    """Write the journal atomically; returns the sha256 of what was written."""
    return write_json_atomic(path, journal)


def new_install(version: str, backup: Any, at: str | None = None) -> dict:
    """An install record with no actions yet; backup is this install's backup folder or None."""
    return {"at": utc_iso() if at is None else at, "version": version,
            "backup": None if backup is None else posix(backup), "actions": []}


def prior_absent() -> dict:
    return {"state": "absent"}


def prior_backup(backup: Any) -> dict:
    return {"state": "backup", "backup": posix(backup)}


def file_action(path: Any, prior: dict, sha256: str) -> dict:
    """A copied hook, agent or workflow."""
    return {"type": "file", "path": posix(path), "prior": prior, "sha256": sha256}


def settings_action(path: Any, prior: dict, sha256: str, undo: dict) -> dict:
    """settings.json; undo is settings_merge.merge's record (removed legacy entries, prior
    ultracode, prior events), handed back to settings_merge.unmerge."""
    return {"type": "settings", "path": posix(path), "prior": prior, "sha256": sha256,
            "undo": undo}


def claude_md_action(path: Any, prior: dict, sha256: str, mode: str) -> dict:
    """CLAUDE.md; mode is claude_md.apply's."""
    return {"type": "claude_md", "path": posix(path), "prior": prior, "sha256": sha256,
            "mode": mode}


def shell_rc_action(path: Any, prior: dict, sha256: str, removed_aliases: list) -> dict:
    """The shell startup file; removed_aliases is shell.strip_legacy_aliases's record."""
    return {"type": "shell_rc", "path": posix(path), "prior": prior, "sha256": sha256,
            "removed_aliases": removed_aliases}


def legacy_move_action(src: Any, dst: Any) -> dict:
    """A legacy file or folder moved by --retire-legacy."""
    return {"type": "legacy_move", "src": posix(src), "dst": posix(dst)}


def actions_newest_first(journal: dict) -> list:
    """Every action of every install, in the order uninstall replays them."""
    return [a for inst in reversed(journal["installs"]) for a in reversed(inst["actions"])]


# --- file primitives ------------------------------------------------------------------------

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Any) -> str | None:
    """The file's sha256, or None when it is absent."""
    try:
        return sha256_bytes(Path(path).read_bytes())
    except FileNotFoundError:
        return None


def read_text(path: Any) -> str | None:
    """The file as text with its newlines untouched, or None when it is absent. Bytes that are
    not UTF-8 are carried through (surrogateescape), so a write gives them back unchanged."""
    try:
        data = Path(path).read_bytes()
    except FileNotFoundError:
        return None
    return data.decode("utf-8", "surrogateescape")


def _remove_quietly(path: str) -> None:
    for attempt in range(WRITE_RETRIES + 1):
        try:
            os.unlink(path)
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt == WRITE_RETRIES:
                return
            time.sleep(WRITE_RETRY_DELAY)


def write_bytes_atomic(path: Any, data: bytes) -> str:
    """Write data through a temp file in the same folder and os.replace; returns its sha256.

    A symlink is written through (its target is replaced, the link kept). An existing file's
    permission bits are kept. os.replace is retried while Windows reports the file locked.
    Raises on failure, leaving the old file and no temp file behind.
    """
    target = os.fspath(path)
    if os.path.islink(target):
        target = os.path.realpath(target)
    folder = os.path.dirname(target)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = "%s.%d.%s.tmp" % (target, os.getpid(), os.urandom(4).hex())
    try:
        with open(tmp, "xb") as f:
            f.write(data)
        try:
            mode = stat.S_IMODE(os.stat(target).st_mode)
        except FileNotFoundError:
            mode = None
        if mode is not None and os.name != "nt":
            os.chmod(tmp, mode)
        for attempt in range(WRITE_RETRIES + 1):
            try:
                os.replace(tmp, target)
                break
            except PermissionError:
                if attempt == WRITE_RETRIES:
                    raise
                time.sleep(WRITE_RETRY_DELAY)
    except BaseException:
        _remove_quietly(tmp)
        raise
    return sha256_bytes(data)


def write_text_atomic(path: Any, text: str) -> str:
    """write_bytes_atomic of text as UTF-8, newlines untouched (surrogateescape, as read_text)."""
    return write_bytes_atomic(path, text.encode("utf-8", "surrogateescape"))


def write_json_atomic(path: Any, obj: Any) -> str:
    """obj as two-space-indented JSON with a final newline, non-ASCII kept."""
    return write_text_atomic(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def copy_file(src: Any, dst: Any) -> str:
    """Copy src's bytes to dst atomically; returns their sha256."""
    return write_bytes_atomic(dst, Path(src).read_bytes())
