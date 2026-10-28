"""Where the installer reads and writes: the Claude config folder, the harness repo, the backup
folder and the shell startup file for this operating system."""
from __future__ import annotations

import ntpath
import os
import subprocess
import sys
import time
from pathlib import Path, PurePath
from typing import Callable, Mapping

PS_TIMEOUT = 5
# The installer owns a settings.json hook entry exactly when its command, with "\\" read as
# "/", contains this.
OWNED_MARKER = "hooks/gk-harness/"
_STAMP = "%Y%m%dT%H%M%SZ"
_ISO = "%Y-%m-%dT%H:%M:%SZ"


def claude_dir(environ: Mapping[str, str] | None = None) -> Path:
    """$GK_CLAUDE_DIR when set and non-empty (made absolute), else ~/.claude."""
    env = (os.environ if environ is None else environ).get("GK_CLAUDE_DIR")
    return Path(os.path.abspath(env)) if env else Path.home() / ".claude"


def harness_root() -> Path:
    """The gk-harness repo this code runs from."""
    return Path(__file__).resolve().parents[1]


def hooks_target() -> Path:
    return claude_dir() / "hooks" / "gk-harness"


def backup_root() -> Path:
    return claude_dir() / "backups" / "gk-harness"


def manifest_path() -> Path:
    return backup_root() / "manifest.json"


def harness_config_path() -> Path:
    """<claude_dir>/gk-harness.json, which the hooks read for the repo root."""
    return claude_dir() / "gk-harness.json"


def posix(p: str | os.PathLike) -> str:
    """p with forward slashes. A PureWindowsPath converts on any OS; a str or native path
    converts by the running OS's rules (a backslash is a legal file-name character on POSIX)."""
    return p.as_posix() if isinstance(p, PurePath) else Path(p).as_posix()


def python_exe() -> str | None:
    """The absolute path of the running interpreter (not resolved, so a venv stays a venv),
    or None when Python cannot say."""
    return os.path.abspath(sys.executable) if sys.executable else None


def utc_stamp(t: float | None = None) -> str:
    """yyyymmddTHHMMSSZ, the backup folder name."""
    return time.strftime(_STAMP, time.gmtime(t))


def utc_iso(t: float | None = None) -> str:
    """yyyy-mm-ddTHH:MM:SSZ, for journal and config timestamps."""
    return time.strftime(_ISO, time.gmtime(t))


def new_backup_dir(t: float | None = None) -> Path:
    """An unused backup folder for this install: backups/gk-harness/<stamp>, or <stamp>-N when
    an install in the same second already took that name. Not created here."""
    base = backup_root() / utc_stamp(t)
    candidate, n = base, 1
    while candidate.exists():
        n += 1
        candidate = base.with_name("%s-%d" % (base.name, n))
    return candidate


def run_capture(argv: list[str], timeout: float) -> str | None:
    """stdout of argv when it exits 0 within timeout, else None (never raises)."""
    try:
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=timeout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _ps_profile(runner: Callable[[list[str], float], str | None]) -> Path | None:
    for exe in ("pwsh", "powershell"):
        out = runner([exe, "-NoProfile", "-Command", "$PROFILE"], PS_TIMEOUT)
        lines = (out or "").strip().splitlines()
        if len(lines) == 1 and ntpath.isabs(lines[0].strip()):
            return Path(lines[0].strip())
    return None


def rc_file(platform: str | None = None, environ: Mapping[str, str] | None = None,
            runner: Callable[[list[str], float], str | None] | None = None,
            home: Path | None = None) -> Path | None:
    """The shell startup file that gets the alias block, or None when there is none to use.

    macOS: ~/.zshrc. Linux (and other POSIX): ~/.zshrc when $SHELL ends in zsh, else ~/.bashrc.
    Windows: the $PROFILE path reported by pwsh, else by powershell (5 s each); None when
    neither answers with one absolute path.
    """
    platform = sys.platform if platform is None else platform
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else home
    if platform == "win32":
        return _ps_profile(run_capture if runner is None else runner)
    if platform == "darwin" or environ.get("SHELL", "").endswith("zsh"):
        return home / ".zshrc"
    return home / ".bashrc"
