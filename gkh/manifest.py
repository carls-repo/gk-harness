"""The action journal at backups/gk-harness/manifest.json, and the file primitives it is
defined over (hashes of what was written, atomic writes, copies).

The journal is {"format": 1, "installs": [install, ...]}, oldest first. An install is
{"at", "version", "backup", "actions": [action, ...]}; each action records what one write
replaced (its prior state) and the sha256 of what was written, so uninstall can tell whether the
file is still exactly as the install left it. Paths are stored posix.
"""
from __future__ import annotations

import codecs
import hashlib
import json
import os
import stat
import time
from pathlib import Path
from typing import Any

from .paths import posix, utc_iso

FORMAT = 1
ACTION_TYPES = ("file", "settings", "claude_md", "shell_rc", "legacy_move", "folder")
WRITE_RETRIES = 10
WRITE_RETRY_DELAY = 0.1
# The encodings a text file the installer edits may be in. A UTF-16 or UTF-32 file is known by
# its byte-order mark (Windows PowerShell 5.1's >, >> and Out-File write UTF-16LE with one);
# anything else is read as UTF-8. The UTF-32 marks come first: FF FE 00 00 starts with FF FE.
TEXT_ENCODINGS = ("utf-8", "utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be")
_MARKED = ((codecs.BOM_UTF32_LE, "utf-32-le"), (codecs.BOM_UTF32_BE, "utf-32-be"),
           (codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be"))


class ManifestError(ValueError):
    """The journal exists but cannot be read as one; the message names the file."""


class TextEncodingError(ValueError):
    """A text file the installer would edit is in an encoding it cannot edit safely, or is
    UTF-16/UTF-32 where only UTF-8 is expected; the message names the file."""


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
    events, the owned keys' priors and written values), handed back to settings_merge.unmerge.
    Records from before the owned keys hold a prior ultracode instead."""
    return {"type": "settings", "path": posix(path), "prior": prior, "sha256": sha256,
            "undo": undo}


def claude_md_action(path: Any, prior: dict, sha256: str, mode: str) -> dict:
    """CLAUDE.md; mode is claude_md.apply's."""
    return {"type": "claude_md", "path": posix(path), "prior": prior, "sha256": sha256,
            "mode": mode}


def shell_rc_action(path: Any, prior: dict, sha256: str, removed_aliases: list,
                    mark_added: bool = False) -> dict:
    """The shell startup file; removed_aliases is shell.strip_legacy_aliases's record.
    mark_added: the write put a UTF-8 byte-order mark at the start of an existing file that had
    none (an empty PowerShell profile), recorded as "mark_added": true, so that uninstall can
    return the file to empty; the key is absent otherwise."""
    action = {"type": "shell_rc", "path": posix(path), "prior": prior, "sha256": sha256,
              "removed_aliases": removed_aliases}
    if mark_added:
        action["mark_added"] = True
    return action


def legacy_move_action(src: Any, dst: Any) -> dict:
    """A legacy file or folder moved by --retire-legacy."""
    return {"type": "legacy_move", "src": posix(src), "dst": posix(dst)}


def folder_action(path: Any) -> dict:
    """A folder a write created (one of its `created` list). Record these, in that list's
    order, before the action of the write that needed them, so that uninstall, replaying newest
    first, reaches each folder after the files in it and removes it only when it is empty."""
    return {"type": "folder", "path": posix(path)}


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


def decode_text(data: bytes, label: str = "the file") -> tuple:
    """(text, encoding), encoding one of TEXT_ENCODINGS.

    A UTF-16 or UTF-32 byte-order mark selects that encoding; the mark is not part of the text
    and encode_text puts it back. Anything else is UTF-8: a UTF-8 mark stays in the text as
    U+FEFF, and bytes that are not UTF-8 are carried through (surrogateescape). Newlines are
    untouched. Raises TextEncodingError for a UTF-16/UTF-32 file cut short, or for unmarked
    bytes holding NUL (UTF-16 without a mark), because an edit would corrupt either.
    """
    for mark, encoding in _MARKED:
        if data.startswith(mark):
            try:
                return data[len(mark):].decode(encoding, "surrogatepass"), encoding
            except UnicodeDecodeError:
                raise TextEncodingError("%s is not valid %s text (cut short?)"
                                        % (label, encoding.upper())) from None
    if b"\x00" in data:
        raise TextEncodingError("%s holds NUL bytes, so it is not UTF-8 text (UTF-16 without a "
                                "byte-order mark?)" % label)
    return data.decode("utf-8", "surrogateescape"), "utf-8"


def encode_text(text: str, encoding: str = "utf-8") -> bytes:
    """text in encoding (one of TEXT_ENCODINGS), with the byte-order mark a UTF-16 or UTF-32
    file is known by; the inverse of decode_text."""
    if encoding == "utf-8":
        return text.encode("utf-8", "surrogateescape")
    for mark, name in _MARKED:
        if name == encoding:
            return mark + text.encode(name, "surrogatepass")
    raise ValueError("unknown text encoding %r" % (encoding,))


def read_text_encoded(path: Any) -> tuple | None:
    """(text, encoding) as decode_text gives them, or None when the file is absent. For a file
    whose encoding must be kept, such as a PowerShell profile: write it back with
    write_text_atomic(path, text, encoding)."""
    try:
        data = Path(path).read_bytes()
    except FileNotFoundError:
        return None
    return decode_text(data, str(path))


def read_text(path: Any) -> str | None:
    """The file as UTF-8 text with its newlines untouched, or None when it is absent. Bytes that
    are not UTF-8 are carried through (surrogateescape), so a write gives them back unchanged.
    Raises TextEncodingError for a UTF-16 or UTF-32 file (which a UTF-8 write would re-encode)
    and as decode_text does."""
    got = read_text_encoded(path)
    if got is None:
        return None
    text, encoding = got
    if encoding != "utf-8":
        raise TextEncodingError("%s is %s text, not UTF-8" % (path, encoding.upper()))
    return text


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


def _remove_folders(made: list) -> None:
    """Remove the folders in made (outermost first), innermost first, each only when empty."""
    for folder in reversed(made):
        try:
            os.rmdir(folder)
        except OSError:
            pass


def _make_folders(folder: str) -> list:
    """Create folder and any missing parents; returns the folders created, outermost first. A
    folder that appears meanwhile is not counted. On failure the folders created so far are
    removed again and the error raised."""
    missing = []
    cur = folder
    while cur and not os.path.isdir(cur):
        missing.append(cur)
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    made: list = []
    try:
        for d in reversed(missing):
            try:
                os.mkdir(d)
            except FileExistsError:
                if not os.path.isdir(d):
                    raise
                continue
            made.append(d)
    except BaseException:
        _remove_folders(made)
        raise
    return made


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def write_bytes_atomic(path: Any, data: bytes, created: list | None = None,
                       private: bool = False) -> str:
    """Write data through a temp file in the same folder and os.replace; returns its sha256.

    A symlink is written through (its target is replaced, the link kept). os.replace is
    retried while Windows reports the file locked. Raises on failure, leaving the old file, no
    temp file and none of the folders this write created (each removed when empty).

    Permissions (POSIX): an existing file keeps its mode, and a new one gets the umask default,
    or 0600 when private is true. The temp file is created 0600 (or narrower, by the umask) and
    set to that mode before any byte is written, so data such as a 0600 settings.json is never
    readable more widely than the file it replaces.

    created: when a list, the folders this write had to create (to hold path) are appended to
    it, outermost first, as Paths, so the caller can journal them with folder_action. Nothing
    is appended when the write fails.
    """
    target = os.fspath(path)
    if os.path.islink(target):
        target = os.path.realpath(target)
    folder = os.path.dirname(target)
    made = _make_folders(folder) if folder else []
    tmp = "%s.%d.%s.tmp" % (target, os.getpid(), os.urandom(4).hex())
    try:
        try:
            mode = stat.S_IMODE(os.stat(target).st_mode)
        except FileNotFoundError:
            mode = 0o600 if private else None
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        fd = os.open(tmp, flags, 0o666 if mode is None else 0o600)
        try:
            if mode is not None and os.name != "nt":
                os.fchmod(fd, mode)
            _write_all(fd, data)
        finally:
            os.close(fd)
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
        _remove_folders(made)
        raise
    if created is not None:
        created.extend(Path(d) for d in made)
    return sha256_bytes(data)


def write_text_atomic(path: Any, text: str, encoding: str = "utf-8", created: list | None = None,
                      private: bool = False) -> str:
    """write_bytes_atomic of encode_text(text, encoding): UTF-8 by default (surrogateescape, as
    read_text), newlines untouched; returns the sha256 of the bytes written."""
    return write_bytes_atomic(path, encode_text(text, encoding), created, private)


def write_json_atomic(path: Any, obj: Any, created: list | None = None,
                      private: bool = False) -> str:
    """obj as two-space-indented JSON with a final newline, non-ASCII kept."""
    return write_text_atomic(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n",
                             created=created, private=private)


def copy_file(src: Any, dst: Any, created: list | None = None, private: bool = False) -> str:
    """Copy src's bytes to dst atomically; returns their sha256. A backup of a file that may
    hold secrets, such as settings.json, is copied with private=True."""
    return write_bytes_atomic(dst, Path(src).read_bytes(), created, private)
