"""Portable primitives shared by every gk hook.

Standard library only, Python 3.9+, on Linux, macOS and Windows. Each helper is defensive:
the hooks it serves must never block or break a session, so failures come back as None or
False rather than as exceptions, except where the interface says otherwise (`locked` raises
LockTimeout, which `run_hook` catches).
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterator, NoReturn

# The path module rel_posix works with; a test swaps in ntpath to check Windows separators.
_path = os.path

_SID_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,127}")

WRITE_RETRIES = 5
WRITE_RETRY_DELAY = 0.05
LOCK_TIMEOUT = 2.0
LOCK_POLL = 0.025
GIT_TIMEOUT = 5
PROJECT_FILE = (".claude", "gk-project.json")
PROJECT_MAX = 262144  # bytes: a project file is a few KB; more is refused, not read
SHARED_BRANCH_MAX = 200
# The folders under <claude_dir>/state that hold one <session_id>.json per session. A session's
# file in each is removed when it ends (gk-claims-end) or is found dead (gk-peers).
SESSION_STATE_DIRS = ("gk-claims", "gk-peers", "gk-remote-pending", "gk-seat")

_ERROR_ACCESS_DENIED = 5
_DWORD_MAX = 0xFFFFFFFF
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259


class LockTimeout(Exception):
    """`locked()` could not take the folder lock within LOCK_TIMEOUT seconds."""


def claude_dir() -> Path:
    """$GK_CLAUDE_DIR when set and non-empty, else ~/.claude."""
    env = os.environ.get("GK_CLAUDE_DIR")
    return Path(env) if env else Path.home() / ".claude"


def safe_sid(v: Any) -> str | None:
    """v when it is a session id that is safe to use as a file name, else None."""
    return v if isinstance(v, str) and _SID_RE.fullmatch(v) else None


def read_json(path: str | os.PathLike) -> Any:
    """The parsed file, or None when it is missing, unreadable or not valid JSON."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def write_json(path: str | os.PathLike, obj: Any) -> bool:
    """Write obj as JSON atomically: a temp file in the same folder, then os.replace.

    os.replace is retried on PermissionError (Windows refuses to replace a file another
    process has open). Returns False, with the temp file removed, when anything fails.
    """
    path = os.fspath(path)
    tmp = None
    try:
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        tmp = "%s.%d.%s.tmp" % (path, os.getpid(), os.urandom(4).hex())
        with open(tmp, "x", encoding="utf-8") as f:
            json.dump(obj, f)
        for attempt in range(WRITE_RETRIES + 1):
            try:
                os.replace(tmp, path)
                return True
            except PermissionError:
                if attempt == WRITE_RETRIES:
                    raise
                time.sleep(WRITE_RETRY_DELAY)
    except Exception:
        if tmp is not None:
            _remove_quietly(tmp)
        return False
    return False  # not reached: the loop either returns or raises


def _remove_quietly(path: str) -> None:
    """Remove path, retrying like write_json does (on Windows an antivirus scanner or the
    indexer can hold a new file for a moment); give up quietly after the last retry."""
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


@contextlib.contextmanager
def locked(dir_path: str | os.PathLike) -> Iterator[None]:
    """Hold an exclusive lock on a folder; raise LockTimeout after LOCK_TIMEOUT seconds.

    POSIX: flock on the folder's own descriptor, as the hooks before gk-harness did, so old
    and new hooks exclude each other. Windows: msvcrt.locking on <dir>/.gk-lock.
    """
    d = os.fspath(dir_path)
    os.makedirs(d, exist_ok=True)
    if os.name == "nt":
        acquire, release, fd = _lock_windows(d)
    else:
        acquire, release, fd = _lock_posix(d)
    try:
        deadline = time.monotonic() + LOCK_TIMEOUT
        while not acquire():
            if time.monotonic() >= deadline:
                raise LockTimeout("could not lock %s within %.1f s" % (d, LOCK_TIMEOUT))
            time.sleep(LOCK_POLL)
        try:
            yield
        finally:
            release()
    finally:
        os.close(fd)


def _lock_posix(d: str) -> tuple[Callable[[], bool], Callable[[], None], int]:
    import fcntl

    fd = os.open(d, os.O_RDONLY)

    def acquire() -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False

    def release() -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)

    return acquire, release, fd


def _lock_windows(d: str) -> tuple[Callable[[], bool], Callable[[], None], int]:
    import errno
    import msvcrt

    busy = {errno.EACCES, getattr(errno, "EDEADLOCK", errno.EDEADLK)}
    fd = os.open(os.path.join(d, ".gk-lock"), os.O_RDWR | os.O_CREAT)

    def acquire() -> bool:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError as e:
            if e.errno in busy:
                return False
            raise

    def release() -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

    return acquire, release, fd


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
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
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
            return ctypes.get_last_error() == _ERROR_ACCESS_DENIED
        try:
            code = wintypes.DWORD()
            if not exit_code(h, ctypes.byref(code)):
                return False
            return code.value == _STILL_ACTIVE
        finally:
            close(h)
    except Exception:
        return False


def _git(start: str | os.PathLike, args: list[str]) -> subprocess.CompletedProcess | None:
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(k, None)
    try:
        return subprocess.run(
            ["git", "-C", os.fspath(start)] + args,
            stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8",
            errors="surrogateescape", timeout=GIT_TIMEOUT, env=env,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def git_identity(start: str | os.PathLike) -> tuple[str, str, str] | None:
    """(common_dir, toplevel, branch) as real paths and a branch name, or None outside a work tree.

    One git call; on an unborn branch (HEAD does not resolve yet) a second call without
    HEAD, and the branch is "(unborn)". A detached HEAD gives "HEAD".
    """
    args = ["rev-parse", "--git-common-dir", "--show-toplevel", "--abbrev-ref", "HEAD"]
    r = _git(start, args)
    if r is None:
        return None
    lines = r.stdout.splitlines()
    if r.returncode == 0 and len(lines) >= 3:
        common, top, branch = lines[0], lines[1], lines[2]
    else:
        r = _git(start, args[:-2])
        if r is None:
            return None
        lines = r.stdout.splitlines()
        if r.returncode != 0 or len(lines) < 2:
            return None
        common, top, branch = lines[0], lines[1], "(unborn)"
    try:
        common = os.path.realpath(os.path.join(os.fspath(start), common))
        return common, os.path.realpath(top), branch
    except (OSError, ValueError):
        return None


def git_toplevel(start: str | os.PathLike) -> Path | None:
    """The work tree's top-level folder (real path), or None outside a work tree."""
    r = _git(start, ["rev-parse", "--show-toplevel"])
    if r is None or r.returncode != 0:
        return None
    lines = r.stdout.splitlines()
    if not lines or not lines[0]:
        return None
    try:
        return Path(os.path.realpath(lines[0]))
    except (OSError, ValueError):
        return None


def rel_posix(path: str | os.PathLike, root: str | os.PathLike) -> str | None:
    """path relative to root with "/" separators, or None when path is not inside root.

    root itself is not a path inside root, so it gives None too.
    """
    try:
        rel = _path.relpath(os.fspath(path), os.fspath(root))
    except ValueError:  # Windows: different drives
        return None
    if rel == _path.curdir or rel == _path.pardir or rel.startswith(_path.pardir + _path.sep):
        return None
    return rel.replace(_path.sep, "/")


def load_project(toplevel: str | os.PathLike) -> tuple[dict | None, str | None]:
    """Read <toplevel>/.claude/gk-project.json.

    (data, None) when valid (a JSON object with version 1); (None, None) when absent;
    (None, reason) when unreadable or invalid. Only a regular file of at most 256 KB is
    read, so a link to a device or a FIFO committed at that path can neither hang the hook
    nor fill its memory.
    """
    path = Path(toplevel).joinpath(*PROJECT_FILE)
    try:
        st = os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None, None
    except OSError as e:
        return None, "%s is unreadable: %s" % (path, e)
    if not stat.S_ISREG(st.st_mode):
        return None, "%s is not a regular file" % path
    if st.st_size > PROJECT_MAX:
        return None, "%s is larger than 256 KB" % path
    try:
        with open(path, "rb") as f:
            raw = f.read(PROJECT_MAX + 1)
    except (FileNotFoundError, NotADirectoryError):
        return None, None
    except OSError as e:
        return None, "%s is unreadable: %s" % (path, e)
    if len(raw) > PROJECT_MAX:  # it grew after the stat
        return None, "%s is larger than 256 KB" % path
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, RecursionError) as e:
        # RecursionError: nesting deeper than the interpreter's recursion limit.
        return None, "%s is not valid JSON: %s" % (path, e)
    if not isinstance(data, dict):
        return None, "%s is not a JSON object" % path
    version = data.get("version")
    if type(version) is not int or version != 1:
        return None, "%s has unsupported version %r (expected 1)" % (path, version)
    return data, None


def shared_branch(toplevel: str | os.PathLike | None) -> str | None:
    """The project file's `sharedBranch` at toplevel, or None when there is no usable one.

    Usable means a non-blank, printable string of at most SHARED_BRANCH_MAX characters, so
    a project file can't put a line break or a wall of text into a hook's message.
    """
    if toplevel is None:
        return None
    data, _ = load_project(toplevel)
    branch = data.get("sharedBranch") if data else None
    if not isinstance(branch, str):
        return None
    branch = branch.strip()
    if not branch or len(branch) > SHARED_BRANCH_MAX or not branch.isprintable():
        return None
    return branch


def shared_branch_phrase(toplevel: str | os.PathLike | None) -> str:
    """The phrase "the shared branch", plus " (<sharedBranch>)" when the project names one."""
    branch = shared_branch(toplevel)
    return "the shared branch (%s)" % branch if branch else "the shared branch"


REMOTE_LEDGER = ("docs", "superpowers", "REMOTE-LINKS.tsv")
"""The publish gate's ledger, relative to a repo's top level, as path parts."""


def ledger_path(root: str | os.PathLike) -> str | None:
    """<root>/docs/superpowers/REMOTE-LINKS.tsv, or None when the gate must not use it.

    None when the path, its links resolved, lies outside root (a repo can commit the ledger
    or one of its folders as a symlink), or when something that is not a regular file is
    there (a symlink, a FIFO, a device, a folder). An absent ledger is fine.
    """
    try:
        tsv = os.path.join(os.fspath(root), *REMOTE_LEDGER)
        real_root = os.path.normcase(os.path.realpath(os.fspath(root)))
        real_tsv = os.path.normcase(os.path.realpath(tsv))
        if rel_posix(real_tsv, real_root) is None:
            return None
        if os.path.lexists(tsv) and not stat.S_ISREG(os.lstat(tsv).st_mode):
            return None
    except (OSError, ValueError):
        return None
    return tsv


def canon_path(p: str, base: str) -> str:
    """p made absolute against base, its folder resolved, case-folded where the OS ignores case.

    The publish gate compares paths this way (sent files, skip targets, pending entries).
    """
    p = os.path.normpath(os.path.join(base, p))
    return os.path.normcase(os.path.join(os.path.realpath(os.path.dirname(p)), os.path.basename(p)))


def pending_file(sid: str) -> Path:
    """The publish gate's state file for a session: state/gk-remote-pending/<sid>.json."""
    return claude_dir() / "state" / "gk-remote-pending" / (sid + ".json")


def load_pending(path: str | os.PathLike, sid: str) -> dict:
    """The publish gate's state, {"session_id", "pending": [entries with a str path]}.

    A missing or malformed file gives an empty list; malformed entries are dropped.
    """
    d = read_json(path)
    if isinstance(d, dict) and isinstance(d.get("pending"), list):
        d["pending"] = [e for e in d["pending"] if isinstance(e, dict) and isinstance(e.get("path"), str)]
        d["session_id"] = sid
        return d
    return {"session_id": sid, "pending": []}


def skip_command(sid: str, rel: str) -> str:
    """The shell command that drops one pending spec: this interpreter, the sibling
    gk-remote-skip.py, the session id and the repo-relative path, quoted for a POSIX shell.

    When the interpreter's path is unknown (sys.executable empty or None) the command names
    python3 (python on Windows), so it never starts with "." and sources the script."""
    skip = Path(os.path.abspath(__file__)).with_name("gk-remote-skip.py")
    exe = sys.executable or ("python" if os.name == "nt" else "python3")
    return " ".join(shlex.quote(a) for a in (Path(exe).as_posix(), skip.as_posix(), sid, rel))


def read_input() -> dict | None:
    """The hook's stdin as a JSON object, or None when it is not valid UTF-8 JSON or not an object."""
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def emit(obj: Any) -> None:
    """Write obj to stdout as JSON (ASCII-only, so any console encoding can carry it)."""
    sys.stdout.write(json.dumps(obj))
    sys.stdout.flush()


def run_hook(name: str, fn: Callable[[dict | None], Any]) -> NoReturn:
    """Run fn(read_input()), report any exception to stderr, and always exit 0."""
    try:
        fn(read_input())
    except SystemExit:
        pass
    except Exception as e:
        try:
            sys.stderr.write("%s: %r\n" % (name, e))
        except Exception:
            pass
    sys.exit(0)
