"""The other Claude sessions on this machine that are busy, so install and update can refuse to
swap hooks under a working session.

Claude Code keeps one file per running session in <claude_dir>/sessions/*.json, holding (among
other things) {"pid": <int>, "name": ..., "status": "busy" | "idle"}. A session counts as busy
when its status says so, its process is alive, and it is not this installer's own ancestor (the
session that ran `gkh update`, say, is busy running it)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

MAX_DEPTH = 64  # parents walked before giving up; a real process tree is far shallower
PS_TIMEOUT = 5
_DWORD_MAX = 0xFFFFFFFF
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_ERROR_ACCESS_DENIED = 5


# --- pid_alive: a copy of hooks/gk_common.py's; gkh never imports the hooks package ---------

def pid_alive(pid: Any) -> bool:
    """Whether a process with this pid exists. Never signals or opens it for writing."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if pid > _DWORD_MAX:
        # No OS hands out such a pid, and on Windows the DWORD argument would silently
        # truncate it (2**32 + 4 would open pid 4, System), so it is never alive.
        return False
    if os.name == "nt":
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # it exists but belongs to someone else
    except (OSError, OverflowError):
        return False
    return True


def _pid_alive_windows(pid: int) -> bool:
    # Never os.kill here: on Windows it calls TerminateProcess for any signal but CTRL_*.
    import ctypes
    from ctypes import wintypes

    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        open_process = k32.OpenProcess
        open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        open_process.restype = ctypes.c_void_p
        exit_code = k32.GetExitCodeProcess
        exit_code.argtypes = (ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD))
        exit_code.restype = wintypes.BOOL
        close = k32.CloseHandle
        close.argtypes = (ctypes.c_void_p,)
        close.restype = wintypes.BOOL

        h = open_process(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            # Access denied means the process exists (the mirror of POSIX PermissionError).
            return ctypes.get_last_error() == _ERROR_ACCESS_DENIED  # type: ignore[attr-defined]
        try:
            code = wintypes.DWORD()
            if not exit_code(h, ctypes.byref(code)):
                return False
            return code.value == _STILL_ACTIVE
        finally:
            close(h)
    except Exception:
        return False


# --- this process's ancestors -----------------------------------------------------------------

def ppid_from_stat(text: str) -> int | None:
    """The parent pid in a /proc/<pid>/stat line: the second field after the command name's
    closing bracket (the name may itself hold spaces and brackets), or None."""
    close = text.rfind(")")
    if close < 0:
        return None
    fields = text[close + 1:].split()
    if len(fields) < 2:
        return None
    try:
        return int(fields[1])
    except ValueError:
        return None


def _parent_linux(pid: int) -> int | None:
    with open("/proc/%d/stat" % pid, "rb") as f:
        return ppid_from_stat(f.read().decode("utf-8", "replace"))


def _parent_ps(pid: int) -> int | None:
    out = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True,
                         stdin=subprocess.DEVNULL, timeout=PS_TIMEOUT)
    if out.returncode != 0:
        return None
    try:
        return int(out.stdout.strip())
    except ValueError:
        return None


def ancestor_pids() -> set[int]:
    """os.getpid() and every parent above it up to pid 1: from /proc on Linux, from ps on
    macOS, and the empty set on Windows. The walk stops on any error and after MAX_DEPTH steps."""
    if sys.platform == "win32":
        return set()
    parent = _parent_linux if sys.platform.startswith("linux") else _parent_ps
    pid = os.getpid()
    own = {pid}
    for _ in range(MAX_DEPTH):
        if pid <= 1:
            break
        try:
            ppid = parent(pid)
        except Exception:
            break
        if not isinstance(ppid, int) or ppid <= 0 or ppid in own:
            break
        own.add(ppid)
        pid = ppid
    return own


# --- busy sessions ---------------------------------------------------------------------------

def busy_peers(claude_dir: Path, own: set[int]) -> list[str]:
    """The name (else the file stem) of each session in <claude_dir>/sessions/*.json whose
    status is "busy", whose pid is alive and not in own, in file-name order. A file that cannot
    be read or does not hold a JSON object is passed over."""
    try:
        files = sorted(Path(claude_dir, "sessions").glob("*.json"))
    except OSError:
        return []
    names = []
    for path in files:
        try:
            data = json.loads(path.read_bytes().decode("utf-8"))
        except (OSError, ValueError):  # UnicodeDecodeError and JSONDecodeError included
            continue
        if not isinstance(data, dict) or data.get("status") != "busy":
            continue
        pid = data.get("pid")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid in own or not pid_alive(pid):
            continue
        name = data.get("name")
        names.append(name if isinstance(name, str) and name.strip() else path.stem)
    return names
