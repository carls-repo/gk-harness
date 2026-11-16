"""The installer's commands: install [--retire-legacy] [--ignore-busy], uninstall, update
[--retire-legacy] [--ignore-busy], status, gen [--check], fable off|on|status, ledger
on|off|status [--ignore-busy] (the usage ledger, see ledger_ctl.py), and report [--since
YYYY-MM-DD] [--session ID] [--config-dir DIR] [--json] (read-only: it prices the transcripts,
see report.py).

install reads everything and decides every write before it makes the first one, so a problem
found then (the preflight) stops it with nothing changed (exit 2). The writes then run in a fixed
order, and each is recorded in the action journal (backups/gk-harness/manifest.json) with the
state it replaced, so uninstall can replay them in reverse. An install that finds nothing to
change writes nothing, not even the journal.

The journal is saved before each write it records, and uninstall saves it after each action it
reverses, so a run stopped at any point (a kill, a closed terminal, power loss) leaves a journal
that names every change still in place. An action whose write never happened, or whose reversal
already happened, finds its file absent or equal to its backup, and is passed over.

install and update refuse (exit 3) while another Claude session on this machine is busy, so an
update never swaps hooks under a working session; update checks before it pulls.
"""
from __future__ import annotations

import functools
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from . import VERSION, claude_md, gen, ledger_ctl, legacy, manifest, paths, peers, report, shell
from .routes import RoutesError, agent_names, load as load_routes
from . import settings_merge as sm

COMMANDS = ("install", "uninstall", "update", "status", "gen", "fable", "ledger", "report")
RETIRE = "--retire-legacy"
CHECK = "--check"
IGNORE_BUSY = "--ignore-busy"
FABLE_WORDS = ("off", "on", "status")
LEDGER_WORDS = ("on", "off", "status")
JSON = "--json"
SINCE = "--since"
SESSION = "--session"
CONFIG_DIR = "--config-dir"
# Flags (and fable's and ledger's words), each at most once.
OPTIONS = {"install": (RETIRE, IGNORE_BUSY), "update": (RETIRE, IGNORE_BUSY), "gen": (CHECK,),
           "fable": FABLE_WORDS, "ledger": LEDGER_WORDS + (IGNORE_BUSY,), "report": (JSON,)}
# Options that take a value, as "--name value" or "--name=value", each at most once.
VALUE_OPTIONS = {"report": (SINCE, SESSION, CONFIG_DIR)}
USAGE = ("usage: install [install [%s] [%s] | uninstall | update [%s] [%s] | status | gen [%s] "
         "| fable off|on|status | ledger on|off|status [%s] "
         "| report [%s YYYY-MM-DD] [%s ID] [%s DIR] [%s]]\n"
         % (RETIRE, IGNORE_BUSY, RETIRE, IGNORE_BUSY, CHECK, IGNORE_BUSY, SINCE, SESSION,
            CONFIG_DIR, JSON))
BUSY_EXIT = 3
# Sources install will not run without. It deploys every hooks/*.py, agents/worker-*.md,
# agents/Explore.md and workflows/gk-*.js present; there may be no workflows yet.
HOOK_SUPPORT = ("gk_common.py", "gk_model.py", "gk_ledger.py", "gk-remote-skip.py")
OTHER_SOURCES = ("policy.md", "shell/aliases.sh", "shell/aliases.ps1")
DEPLOYED = (("hooks", "*.py", ("hooks", "gk-harness")),
            (".", "routes.json", ("hooks", "gk-harness")),
            ("agents", "worker-*.md", ("agents",)),
            ("agents", "Explore.md", ("agents",)),
            ("workflows", "gk-*.js", ("workflows",)))
EXECUTION_POLICY_NOTICE = (
    "gk-harness: the gk, gk-fable, gk-ultra and gkh functions load from your PowerShell profile "
    "only when PowerShell may run profile scripts. If a new PowerShell window does not know them, "
    "run: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned")
_GIT_REPO_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")
# Either one overrides every agent's own model, so the routing in routes.json would not hold.
FORCING_VARS = ("CLAUDE_CODE_SUBAGENT_MODEL_FORCE", "CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL")
SETTINGS_NOTE = ("settings.json: hooks registered; model, efforts, subagent default and ultracode "
                 "owned")


class Stop(Exception):
    """A problem found before the first write: the command exits 2 and changes nothing."""


def _stopped(problem: Any) -> int:
    sys.stderr.write("gk-harness: %s; nothing was changed.\n" % problem)
    return 2


def _busy_refusal() -> int | None:
    """BUSY_EXIT, with the refusal on stderr, when another Claude session on this machine is
    busy (its own ancestors never count); None when none is."""
    names = peers.busy_peers(paths.claude_dir(), peers.ancestor_pids())
    if not names:
        return None
    sys.stderr.write("%d session(s) busy: %s. Wait until they are idle, or pass %s.\n"
                     % (len(names), ", ".join(names), IGNORE_BUSY))
    return BUSY_EXIT


def _warn(text: str) -> None:
    sys.stderr.write("gk-harness: %s\n" % text)


def _plural(n: int, word: str, plural: str | None = None) -> str:
    return "%d %s" % (n, word if n == 1 else (plural or word + "s"))


def _windows() -> bool:
    return sys.platform == "win32"


def _command_line(root: Any, command: str) -> str:
    if _windows():
        return ('powershell -NoProfile -ExecutionPolicy Bypass -File "%s/install.ps1" %s'
                % (paths.posix(root), command))
    return "%s/install.sh %s" % (paths.posix(root), command)


def _read(path: Path) -> bytes | None:
    """The file's bytes, or None when it is absent. Raises Stop when it cannot be read."""
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as e:
        raise Stop("%s cannot be read: %s" % (paths.posix(path), e.strerror or e)) from None


def _sha(path: Path) -> str | None:
    """The file's sha256; None when absent; "" when it cannot be read (so it never matches)."""
    try:
        return manifest.sha256_file(path)
    except OSError:
        return ""


def _empty(text: str) -> bool:
    """Nothing but whitespace, after any byte-order mark."""
    return text.lstrip(claude_md.BOM).strip() == ""


def _write_problem(path: Path) -> str | None:
    """Why a write to path would fail, found before any write; None when it would not.

    The writes go as manifest.write_bytes_atomic makes them: through a symlink to its real path
    (the link stays a link), as a temp file beside the real file renamed over it. So the real
    file's folder, or the nearest one that exists when the write must create it, must be
    writable (a link into a read-only store such as /nix/store is not); on Windows a read-only
    file also refuses the rename."""
    name, target = paths.posix(path), str(path)
    if os.path.islink(target):
        target = os.path.realpath(target)
        name = "%s (a link to %s)" % (name, paths.posix(target))
    folder = os.path.dirname(target)
    while folder and not os.path.isdir(folder):
        parent = os.path.dirname(folder)
        if parent == folder:
            break
        folder = parent
    if folder and not os.access(folder, os.W_OK | os.X_OK):
        return "%s cannot be written: the folder %s is not writable" % (name, paths.posix(folder))
    if os.name == "nt" and os.path.isfile(target) and not os.access(target, os.W_OK):
        return "%s cannot be written: it is read-only" % name
    return None


# --- sources --------------------------------------------------------------------------------

def workers(root: Path) -> list:
    """The worker agent names, read from the checkout's routes.json (Stop when it is unusable)."""
    try:
        return agent_names(load_routes(root / "routes.json"))
    except RoutesError as e:
        raise Stop("the gk-harness checkout's routes.json is unusable (%s)" % e) from None


def _required(root: Path) -> list:
    scripts = sorted({s for _, _, names in sm.REGISTRATIONS for s in names} | set(HOOK_SUPPORT))
    return (["hooks/" + s for s in scripts] + ["agents/%s.md" % w for w in workers(root)]
            + list(OTHER_SOURCES) + ["routes.json"])


def _check_sources(root: Path) -> None:
    missing = [rel for rel in _required(root) if not root.joinpath(*rel.split("/")).is_file()]
    if missing:
        raise Stop("the gk-harness checkout %s is missing %s"
                   % (paths.posix(root), ", ".join(missing)))


def _group_label(group: str) -> str:
    """routes.json sits in the repo root but lands beside the hooks, so it counts as hooks."""
    return "hooks" if group == "." else group


def _deployed(root: Path, cdir: Path) -> list:
    """[(group, source, target)] for every file install copies, in copy order."""
    out = []
    for group, pattern, target in DEPLOYED:
        folder = root / group
        if not folder.is_dir():
            continue
        for src in sorted(folder.glob(pattern)):
            if src.is_file():
                out.append((_group_label(group), src, cdir.joinpath(*target) / src.name))
    return out


# --- install: the plan ----------------------------------------------------------------------

class _Step:
    """One planned write: data replaces path, which is backed up first when it exists."""

    def __init__(self, path: Path, data: bytes, existed: bool, backup_rel: str,
                 record: Callable[[dict, str], dict], note: str, group: str | None = None):
        self.path = path
        self.data = data
        self.existed = existed
        self.backup_rel = backup_rel  # where its backup goes, inside the backup folder
        self.record = record  # (prior, sha256) -> the journal action
        self.note = note
        self.group = group  # "hooks" (routes.json too), "agents" or "workflows" (copied files), "rc", or None


def _rel(path: Path, cdir: Path) -> str:
    return path.relative_to(cdir).as_posix()


def _preflight() -> tuple:
    if sys.version_info < (3, 9):
        raise Stop("Python 3.9 or newer is required (this is %d.%d)"
                   % tuple(sys.version_info[:2]))
    python = paths.python_exe()
    if python is None:
        raise Stop("cannot tell which Python is running the installer")
    root, cdir = paths.harness_root(), paths.claude_dir()
    for p in (python, cdir, root):
        try:
            paths.quotable(p)
        except ValueError as e:
            raise Stop(e) from None
    return root, cdir, python


def _copy_steps(root: Path, cdir: Path) -> list:
    steps = []
    for group, src, dst in _deployed(root, cdir):
        data = _read(src)
        if data is None:
            raise Stop("%s is missing" % paths.posix(src))
        current = _read(dst)
        if current != data:
            steps.append(_Step(dst, data, current is not None, _rel(dst, cdir),
                               functools.partial(manifest.file_action, dst), "", group))
    return steps


def _policy_step(root: Path, cdir: Path) -> _Step | None:
    try:
        policy = (_read(root / "policy.md") or b"").decode("utf-8")
    except UnicodeDecodeError:
        raise Stop("%s/policy.md is not UTF-8 text" % paths.posix(root)) from None
    md = cdir / "CLAUDE.md"
    try:
        block = claude_md.render_block(policy, VERSION)
        existing = manifest.read_text(md)
        text, mode = claude_md.apply(existing, block)
    except (claude_md.BlockError, manifest.TextEncodingError) as e:
        raise Stop(e) from None
    except OSError as e:
        raise Stop("%s cannot be read: %s" % (paths.posix(md), e.strerror or e)) from None
    if mode == "unchanged":
        return None
    note = {"created": "CLAUDE.md: created with the policy block",
            "appended": "CLAUDE.md: policy block added after this machine's notes",
            "updated": "CLAUDE.md: policy block updated",
            "replaced_old_policy": "CLAUDE.md: the old policy replaced by the policy block"}[mode]
    return _Step(md, manifest.encode_text(text), existing is not None, "CLAUDE.md",
                 functools.partial(manifest.claude_md_action, md, mode=mode), note)


def _config_step(root: Path, cdir: Path, python: str) -> _Step | None:
    path = paths.harness_config_path()
    want = {"version": VERSION, "root": paths.posix(root), "python": paths.posix(python)}
    raw = _read(path)
    try:
        have = json.loads(raw.decode("utf-8")) if raw is not None else None
    except (ValueError, RecursionError):
        have = None
    if isinstance(have, dict) and all(have.get(k) == v for k, v in want.items()):
        return None
    want["installed_at"] = paths.utc_iso()
    data = (json.dumps(want, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    return _Step(path, data, raw is not None, _rel(path, cdir),
                 functools.partial(manifest.file_action, path), "gk-harness.json: written")


def _owned_desired(root: Path, ledger: bool = False) -> dict:
    """The settings keys install owns, from the checkout's routes.json (Stop when unusable),
    with the ledger's telemetry keys when ledger is True."""
    try:
        return sm.owned_desired(load_routes(root / "routes.json"), ledger=ledger)
    except RoutesError as e:
        raise Stop("the gk-harness checkout's routes.json is unusable (%s)" % e) from None


def _write_happened(a: dict) -> bool:
    """Whether a settings action's write was made: an install stopped between journalling it
    and writing leaves its file absent or equal to its backup (the test uninstall uses to pass
    such an action over), and then what it records was never written."""
    path = a.get("path")
    current = _sha(Path(path)) if isinstance(path, str) else None
    prior = a.get("prior") if isinstance(a.get("prior"), dict) else {}
    return current is not None and current != _backup_sha(prior)


def _owned_last(journal: dict) -> tuple:
    """(last, priors) for settings_merge.merge: for each owned key, the value install last wrote
    and the prior recorded with it, from the newest settings action that records the key and
    whose write was made. A key whose newest record is under undo["released"] (the ledger's
    keys, once it is off) is no longer owned, so it is in neither.

    Versions before the owned keys forced ultracode to true and recorded, in undo["ultracode"],
    the value before that. With no owned record of ultracode, the oldest such record holds the
    value from before any install touched it: install is then taken to have written true, with
    that prior, so that uninstall puts back the original value, not the true."""
    last: dict = {}
    priors: dict = {}
    seen: set = set()  # the keys whose newest record has been read
    old_ultracode = None
    for a in manifest.actions_newest_first(journal):
        undo = a.get("undo") if isinstance(a, dict) and a.get("type") == "settings" else None
        if not isinstance(undo, dict) or not _write_happened(a):
            continue
        owned = undo.get("owned")
        for key, record in (owned.items() if isinstance(owned, dict) else ()):
            if key not in seen and isinstance(record, dict) and "wrote" in record:
                seen.add(key)
                last[key] = record["wrote"]
                priors[key] = record.get("prior")
        released = undo.get("released")
        seen.update(k for k in (released if isinstance(released, dict) else ())
                    if isinstance(k, str))
        if isinstance(undo.get("ultracode"), dict):
            old_ultracode = undo["ultracode"]
    if "ultracode" not in last and old_ultracode is not None:
        last["ultracode"] = True
        priors["ultracode"] = old_ultracode
    return last, priors


def _release(last: dict, priors: dict, ledger: bool) -> dict:
    """The ledger keys merge must release: while the switch is off, each one still owned, with
    its record ({"prior", "wrote"})."""
    if ledger:
        return {}
    return {key: {"prior": priors.get(key), "wrote": last[key]}
            for key in map(sm.dotted, sm.LEDGER_ENV) if key in last}


def _owned_args(root: Path, journal: dict) -> tuple:
    """merge's (desired, last, priors, release) for this install: the ledger's keys are desired
    while its switch is on and released once it is off. Stop when routes.json is unusable."""
    ledger = ledger_ctl.switch_on()
    last, priors = _owned_last(journal)
    return _owned_desired(root, ledger), last, priors, _release(last, priors, ledger)


def _settings_step(root: Path, cdir: Path, python: str, journal: dict) -> _Step | None:
    path = cdir / "settings.json"
    desired, last, priors, release = _owned_args(root, journal)
    try:
        current = sm.load_strict(path)
        new, undo = sm.merge({} if current is None else current, python, cdir, desired, last,
                             priors, release)
    except ValueError as e:
        raise Stop(e) from None
    text = sm.dumps(new)
    if current is not None and text == sm.dumps(current):
        return None
    note = SETTINGS_NOTE
    ledger_keys = set(map(sm.dotted, sm.LEDGER_ENV))
    if ledger_keys & set(undo["owned"]):
        note += "; the ledger's telemetry keys owned"
    elif undo.get("released"):
        note += "; the ledger's telemetry keys handed back"
    n = len(undo["removed"])
    if n:
        note += ", %d old gk hook %s removed" % (n, "entry" if n == 1 else "entries")
    return _Step(path, text.encode("utf-8"), current is not None, "settings.json",
                 functools.partial(manifest.settings_action, path, undo=undo), note)


def _rc_step(root: Path, notices: list) -> _Step | None:
    windows = _windows()
    rc = paths.rc_file()
    if rc is None:
        notices.append("gk-harness: no PowerShell profile found (neither pwsh nor powershell "
                       "reported $PROFILE), so the gk shortcuts were not set up")
        return None
    try:
        got = manifest.read_text_encoded(paths.posix(rc))  # posix, as the messages name it
        text, encoding = got if got is not None else (None, "utf-8")
        removed: list = []
        kept = text
        if text is not None:
            kept, removed = shell.strip_legacy_aliases(text)
        # A UTF-16 or UTF-32 profile carries its own mark through its encoding.
        new, mode = shell.apply(kept, shell.render_block(root, windows), label=paths.posix(rc),
                                powershell=windows and encoding == "utf-8")
    except (manifest.TextEncodingError, claude_md.BlockError) as e:
        problem = str(e)
    except OSError as e:
        problem = "%s cannot be read: %s" % (paths.posix(rc), e.strerror or e)
    else:
        if new == text:
            return None
        problem = _write_problem(rc)
        if problem is None:
            note = "%s: gk shortcut block %s" % (paths.posix(rc), {
                "unchanged": "already present", "updated": "updated"}.get(mode, "added"))
            if removed:
                note += ", %s removed" % _plural(len(removed), "old gk alias", "old gk aliases")
            # An existing profile that was empty gets the mark a created one gets; uninstall
            # returns it to empty.
            mark_added = (text is not None and not claude_md.bom_of(text)
                          and bool(claude_md.bom_of(new)))
            return _Step(rc, manifest.encode_text(new, encoding), text is not None,
                         "rc/" + rc.name,
                         functools.partial(manifest.shell_rc_action, rc, removed_aliases=removed,
                                           mark_added=mark_added),
                         note, "rc")
    notices.append("gk-harness: %s; the gk shortcuts were not set up" % problem)
    return None


def _plan(root: Path, cdir: Path, python: str, retire: bool, journal: dict) -> dict:
    _check_sources(root)
    notices: list = []
    steps = _copy_steps(root, cdir)
    # The hooks are in place before settings.json names them, and the old entries leave
    # settings.json before --retire-legacy moves their files.
    for step in (_policy_step(root, cdir), _config_step(root, cdir, python),
                 _settings_step(root, cdir, python, journal), _rc_step(root, notices)):
        if step is not None:
            steps.append(step)
    backup = paths.new_backup_dir()
    moves = legacy.retire_plan(cdir, backup) if retire else []
    # A target that cannot be written would stop every install at the same step, so it stops
    # this one before the first write (the rc file was judged in _rc_step: it is skipped).
    if steps or moves:
        for path in [s.path for s in steps if s.group != "rc"] + [paths.manifest_path()]:
            problem = _write_problem(path)
            if problem is not None:
                raise Stop(problem)
    return {"steps": steps, "moves": moves, "backup": backup, "notices": notices}


# --- install: the writes --------------------------------------------------------------------

def _missing_folders(path: Path) -> list:
    """The folders a write to path will create, outermost first, found as the writer finds them
    (manifest.write_bytes_atomic), so that they can be journalled before the write."""
    target = str(path)
    if os.path.islink(target):
        target = os.path.realpath(target)
    missing = []
    cur = os.path.dirname(target)
    while cur and not os.path.isdir(cur):
        missing.append(cur)
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return [Path(d) for d in reversed(missing)]


def _journal_add(journal: dict, record: dict, actions: list) -> None:
    """Add actions to this install's record (the record joins the journal with its first action)
    and save the journal: before the write they describe, so that a kill never leaves a write
    the journal does not name."""
    if not record["actions"]:
        journal["installs"].append(record)
    record["actions"].extend(actions)
    manifest.save(paths.manifest_path(), journal)


def _journal_drop(journal: dict, record: dict, n: int) -> None:
    """Take back the last n actions, whose write failed and changed nothing, and save."""
    del record["actions"][-n:]
    if not record["actions"]:
        journal["installs"].pop()
    manifest.save(paths.manifest_path(), journal)


def _apply(plan: dict, journal: dict, record: dict, done: list) -> None:
    """Make the planned writes, each journalled first. Each existing file is backed up before
    its action is journalled, so a journalled backup always exists."""
    backup = plan["backup"]
    for step in plan["steps"]:
        prior = manifest.prior_absent()
        if step.existed:
            saved = backup.joinpath(*step.backup_rel.split("/"))
            manifest.copy_file(step.path, saved, private=True)
            record["backup"] = paths.posix(backup)
            prior = manifest.prior_backup(saved)
        folders = _missing_folders(step.path)
        actions = [manifest.folder_action(d) for d in folders]
        actions.append(step.record(prior, manifest.sha256_bytes(step.data)))
        _journal_add(journal, record, actions)
        created: list = []
        try:
            manifest.write_bytes_atomic(step.path, step.data, created=created)
        except OSError:
            _journal_drop(journal, record, len(actions))
            raise
        if [paths.posix(d) for d in created] != [paths.posix(d) for d in folders]:
            # Another program made or removed one of them meanwhile: journal what the write made.
            record["actions"][-len(actions):] = ([manifest.folder_action(d) for d in created]
                                                 + actions[-1:])
            manifest.save(paths.manifest_path(), journal)
        done.append(step)
    for move in plan["moves"]:
        record["backup"] = paths.posix(backup)
        _journal_add(journal, record, [manifest.legacy_move_action(move["src"], move["dst"])])
        try:
            move["dst"].parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(move["src"]), str(move["dst"]))
        except OSError:
            if not os.path.lexists(str(move["dst"])):
                _journal_drop(journal, record, 1)
            raise
        done.append(move)


def _report(done: list, record: dict, cdir: Path, root: Path) -> None:
    print("gk-harness %s installed into %s" % (VERSION, paths.posix(cdir)))
    for group, label in (("hooks", "hooks/gk-harness"), ("agents", "agents"),
                         ("workflows", "workflows")):
        copied = [s for s in done if isinstance(s, _Step) and s.group == group]
        if copied:
            saved = sum(1 for s in copied if s.existed)
            print("  %s: %s copied%s" % (label, _plural(len(copied), "file"),
                                        " (%d backed up first)" % saved if saved else ""))
    for step in done:
        if isinstance(step, _Step) and step.group in (None, "rc"):
            print("  " + step.note)
    moves = [m for m in done if isinstance(m, dict)]
    if moves:
        retired = _plural(len(moves), "old file or folder", "old files or folders")
        print("  %s retired into %s/legacy" % (retired, record["backup"]))
    if record["backup"]:
        print("backups: %s" % record["backup"])
    print("undo with: %s" % _command_line(root, "uninstall"))


def install(retire: bool, ignore_busy: bool = False) -> int:
    if not ignore_busy:
        refused = _busy_refusal()
        if refused is not None:
            return refused
    try:
        root, cdir, python = _preflight()
        journal = manifest.load(paths.manifest_path())
        plan = _plan(root, cdir, python, retire, journal)
    except (Stop, manifest.ManifestError) as e:
        return _stopped(e)
    for notice in plan["notices"]:
        print(notice)
    if not plan["steps"] and not plan["moves"]:
        print("gk-harness %s is installed in %s; nothing to change." % (VERSION,
                                                                       paths.posix(cdir)))
        return 0
    record = manifest.new_install(VERSION, None)
    done: list = []
    try:
        _apply(plan, journal, record, done)
    except OSError as e:
        _warn("%s; install stopped part-way. What it changed is in the journal: run install "
              "again to finish, or uninstall to undo it." % e)
        return 1
    _report(done, record, cdir, root)
    if _windows() and any(isinstance(s, _Step) and s.group == "rc" for s in done):
        print(EXECUTION_POLICY_NOTICE)
    if ledger_ctl.switch_on():
        # The receiver runs the code just copied.
        if ledger_ctl.restart():
            print("ledger: receiver restarted on port %d" % ledger_ctl.port())
        else:
            _warn("ledger: the receiver did not start on port %d (gkh ledger status)"
                  % ledger_ctl.port())
    return 0


# --- uninstall ------------------------------------------------------------------------------

def _unlink(path: Path) -> None:
    try:
        os.unlink(str(path))
    except FileNotFoundError:
        pass


def _remove(path: Path) -> None:
    """Remove a target file. A symlink was written through, so its real file goes and the link
    stays, as it was before the install made that file."""
    target = str(path)
    if os.path.islink(target):
        target = os.path.realpath(target)
    _unlink(Path(target))


class _Keep:
    """Copies of the files uninstall rewrites or removes after they were edited since install,
    taken just before, into backups/gk-harness/<stamp>-uninstall/ under the path install backs
    them up under (the rc file under rc/). The folder is chosen at the first copy; a file
    copied once in this run is not copied again, so the copy is the file as uninstall found
    it."""

    def __init__(self) -> None:
        self.folder = None  # a Path once chosen

    def save(self, path: Path, kind: str) -> None:
        if self.folder is None:
            base = paths.backup_root() / (paths.utc_stamp() + "-uninstall")
            folder, n = base, 1
            while os.path.lexists(str(folder)):
                n += 1
                folder = base.with_name("%s-%d" % (base.name, n))
            self.folder = folder
        rel = "rc/" + path.name
        if kind != "shell_rc":
            try:
                rel = _rel(path, paths.claude_dir())
            except ValueError:
                rel = path.name
        saved = self.folder.joinpath(*rel.split("/"))
        if not os.path.lexists(str(saved)):
            manifest.copy_file(path, saved, private=True)


def _restore(path: Path, prior: Any, private: bool) -> None:
    """Put back the state an install recorded as replaced: no file, or its backup."""
    state = prior.get("state") if isinstance(prior, dict) else None
    if state == "absent":
        _remove(path)
        return
    saved = prior.get("backup") if state == "backup" else None
    if not isinstance(saved, str):
        _warn("the journal does not say what %s replaced; left in place" % paths.posix(path))
    elif not Path(saved).is_file():
        _warn("the backup %s is missing; left in place: %s" % (saved, paths.posix(path)))
    else:
        manifest.copy_file(Path(saved), path, private=private)


def _backup_sha(prior: dict) -> str | None:
    """The sha256 of the backup prior names; None when it names none or it is absent."""
    saved = prior.get("backup") if prior.get("state") == "backup" else None
    return _sha(Path(saved)) if isinstance(saved, str) else None


def _undo_file(a: dict, private: bool, surgical: Callable[[Path, dict, _Keep], None],
               keep: _Keep) -> None:
    """The shared reverse of a recorded write: the prior state when the file is exactly as the
    install left it (or gone, when there is a backup to bring back), nothing when it already
    equals its backup (put back by hand, by an uninstall cut short, or never overwritten by an
    install cut short), else surgical(), which copies the file into keep before it rewrites or
    removes it."""
    path = Path(a["path"])
    prior = a.get("prior") if isinstance(a.get("prior"), dict) else {}
    current = _sha(path)
    if current == a.get("sha256"):
        _restore(path, prior, private)
    elif current is None:
        if prior.get("state") == "backup":
            _restore(path, prior, private)
    elif current and current == _backup_sha(prior):
        return
    else:
        surgical(path, a, keep)


def _changed_copy(path: Path, a: dict, keep: _Keep) -> None:
    _warn("changed since install, left in place: %s" % paths.posix(path))


def _group_keys(group: Any) -> Any:
    return {k: v for k, v in group.items() if k != "hooks"} if isinstance(group, dict) else None


def _not_back_yet(settings: dict, removed: list) -> list:
    """The recorded legacy entries that settings does not hold already (under the same event,
    in a group with the same matcher), each counted once, so that reversing the merge again
    after an uninstall cut short, or after an entry was put back by hand, adds none twice."""
    present = []
    hooks = settings.get("hooks")
    for event, groups in hooks.items() if isinstance(hooks, dict) else ():
        for group in groups if isinstance(groups, list) else ():
            entries = group.get("hooks") if isinstance(group, dict) else None
            for entry in entries if isinstance(entries, list) else ():
                present.append((event, _group_keys(group), entry))
    out = []
    for r in removed:
        key = (r.get("event"), _group_keys(r.get("group")), r.get("entry")) \
            if isinstance(r, dict) else None
        if key in present:
            present.remove(key)
        else:
            out.append(r)
    return out


def _not_superseded(undo: dict, done: set) -> dict:
    """undo without its records of the owned keys in done. A newer record of a key carries the
    first prior forward, and its reversal has already put that prior back or left a hand change
    in place, so an older record of the key must not act on it (by restoring its own prior over
    a hand change that happens to equal what it once wrote). An old "ultracode" record goes too
    once a newer one owns ultracode, since that newer one carries its prior."""
    owned = undo.get("owned")
    if isinstance(owned, dict):
        undo = dict(undo, owned={k: v for k, v in owned.items() if k not in done})
    if "ultracode" in done and "ultracode" in undo:
        undo = {k: v for k, v in undo.items() if k != "ultracode"}
    return undo


def _surgical_settings(path: Path, a: dict, keep: _Keep, done: set) -> None:
    """Reverse a settings action on a file changed since, then add the owned keys it records to
    done (the keys reversed so far in this uninstall, newest record first)."""
    try:
        current = sm.load_strict(paths.posix(path))  # posix, so messages name it as the journal
    except sm.SettingsError as e:
        _warn("%s; its gk-harness hook entries were left in it" % e)
        return
    if current is None:
        return
    undo = a.get("undo")
    if isinstance(undo, dict):
        if isinstance(undo.get("removed"), list):
            undo = dict(undo, removed=_not_back_yet(current, undo["removed"]))
        undo = _not_superseded(undo, done)
    new = sm.unmerge(current, undo)
    if sm.dumps(new) != sm.dumps(current):
        keep.save(path, a["type"])
        manifest.write_text_atomic(path, sm.dumps(new))
    done.update(sm.owned_paths(a.get("undo")))
    released = a["undo"].get("released") if isinstance(a.get("undo"), dict) else None
    # A key released here was given up: an older record of it must not act on it either.
    done.update(k for k in (released if isinstance(released, dict) else ())
                if isinstance(k, str))


def _surgical_claude_md(path: Path, a: dict, keep: _Keep) -> None:
    try:
        text = manifest.read_text(paths.posix(path))
        if text is None:
            return
        stripped = claude_md.strip(text)
    except (manifest.TextEncodingError, claude_md.BlockError) as e:
        _warn("%s; the policy block was left in it" % e)
        return
    except OSError as e:
        _warn("%s cannot be read (%s); left in place" % (paths.posix(path), e.strerror or e))
        return
    prior = a.get("prior") if isinstance(a.get("prior"), dict) else {}
    if a.get("mode") == "replaced_old_policy" and prior.get("state") == "backup":
        if _empty(stripped):
            keep.save(path, a["type"])
            _restore(path, prior, False)
            return
        print("gk-harness: %s changed since install, so only the policy block was removed; the "
              "old policy it replaced is in %s" % (paths.posix(path), prior.get("backup")))
    elif prior.get("state") == "absent" and _empty(stripped):
        keep.save(path, a["type"])
        _remove(path)
        return
    if stripped != text:
        keep.save(path, a["type"])
        manifest.write_text_atomic(path, stripped)


def _surgical_rc(path: Path, a: dict, keep: _Keep) -> None:
    try:
        got = manifest.read_text_encoded(paths.posix(path))
        if got is None:
            return
        text, encoding = got
        stripped = shell.strip(text, label=paths.posix(path))
    except (manifest.TextEncodingError, claude_md.BlockError) as e:
        _warn("%s; the gk-harness block was left in it" % e)
        return
    except OSError as e:
        _warn("%s cannot be read (%s); left in place" % (paths.posix(path), e.strerror or e))
        return
    prior = a.get("prior") if isinstance(a.get("prior"), dict) else {}
    if prior.get("state") == "absent" and _empty(stripped):
        keep.save(path, a["type"])
        _remove(path)
    elif a.get("mark_added") is True and _empty(stripped):
        # An empty profile that install gave a mark: empty again, zero bytes.
        keep.save(path, a["type"])
        manifest.write_bytes_atomic(path, b"")
    elif stripped != text:
        keep.save(path, a["type"])
        manifest.write_text_atomic(path, stripped, encoding)
    lines = [r.get("text") for r in a.get("removed_aliases") or [] if isinstance(r, dict)]
    if lines:
        print("gk-harness: %s changed since install, so the old gk aliases it held were not put "
              "back; they were:\n%s" % (paths.posix(path),
                                        "\n".join("  %s" % s for s in lines)))


def _clear_pycache(folder: Path) -> None:
    """Remove the __pycache__ Python leaves beside the hooks, when it holds only .pyc files."""
    cache = folder / "__pycache__"
    if cache.is_symlink() or not cache.is_dir():
        return
    names = os.listdir(str(cache))
    if all(n.endswith(".pyc") for n in names):
        for n in names:
            _unlink(cache / n)
        try:
            os.rmdir(str(cache))
        except OSError:
            pass


def _undo_folder(a: dict) -> None:
    folder = Path(a["path"])
    if folder.name == "gk-harness" and folder.parent.name == "hooks":
        _clear_pycache(folder)
    try:
        os.rmdir(str(folder))
    except OSError:
        pass  # absent, or holds something that is not ours


def _undo_move(a: dict) -> None:
    src, dst = Path(a["src"]), Path(a["dst"])
    if not os.path.lexists(str(dst)):
        if not os.path.lexists(str(src)):
            _warn("the retired copy %s is missing, so %s was not put back" % (a["dst"], a["src"]))
        # else it is back already (an uninstall cut short), or was never moved (an install cut
        # short), so there is nothing to do
    elif os.path.lexists(str(src)):
        _warn("%s exists again, so the retired copy stays in %s" % (a["src"], a["dst"]))
    else:
        src.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(dst), str(src))


def _undo(a: Any, keep: _Keep, done: set) -> None:
    kind = a.get("type") if isinstance(a, dict) else None
    keys = {"legacy_move": ("src", "dst"), "folder": ("path",)}.get(kind, ("path", "sha256"))
    if kind not in manifest.ACTION_TYPES or not all(isinstance(a.get(k), str) for k in keys):
        _warn("skipped a journal entry it cannot read: %s" % json.dumps(a)[:200])
    elif kind == "file":
        _undo_file(a, False, _changed_copy, keep)
    elif kind == "settings":
        _undo_file(a, True, functools.partial(_surgical_settings, done=done), keep)
    elif kind == "claude_md":
        _undo_file(a, False, _surgical_claude_md, keep)
    elif kind == "shell_rc":
        _undo_file(a, True, _surgical_rc, keep)
    elif kind == "folder":
        _undo_folder(a)
    else:
        _undo_move(a)


def _ledger_down() -> None:
    """Remove the ledger switch and stop the receiver, warning about what could not be done.
    The switch goes first, so that a hook running meanwhile does not start the receiver again.
    The ledger's day files stay."""
    try:
        _unlink(ledger_ctl.switch_file())
    except OSError as e:
        _warn("%s: %s" % (paths.posix(ledger_ctl.switch_file()), e.strerror or e))
    if not ledger_ctl.stop():
        _warn("ledger: the receiver did not stop; it exits by itself once idle")


def uninstall() -> int:
    path = paths.manifest_path()
    try:
        journal = manifest.load(path)
    except manifest.ManifestError as e:
        return _stopped(e)
    # The receiver goes first, while the code it runs is still in place; the ledger's data stay.
    _ledger_down()
    if not journal["installs"]:
        print("gk-harness: nothing to uninstall (no install is recorded in %s)."
              % paths.posix(path))
        return 0
    keep = _Keep()
    done: set = set()  # the owned settings keys reversed so far (see _not_superseded)
    try:
        # The journal is saved after each action is reversed, so a kill leaves at most that one
        # action recorded but done; replayed, it finds its file already as it was.
        while journal["installs"]:
            actions = journal["installs"][-1]["actions"]
            if actions:
                _undo(actions[-1], keep, done)
                actions.pop()
            if not actions:
                journal["installs"].pop()
            manifest.save(path, journal)
    except OSError as e:
        _warn("%s; uninstall stopped part-way. Run uninstall again to finish." % e)
        _report_kept(keep)
        return 1
    # The hooks' own state: the cached update check, and the seat folder once it is empty.
    _drop_update_stamp()
    try:
        os.rmdir(str(paths.claude_dir() / "state" / "gk-seat"))
    except OSError:
        pass
    print("gk-harness uninstalled from %s; the backups stay in %s"
          % (paths.posix(paths.claude_dir()), paths.posix(paths.backup_root())))
    _report_kept(keep)
    return 0


def _report_kept(keep: _Keep) -> None:
    if keep.folder is not None and keep.folder.is_dir():
        print("gk-harness: the files you had edited since install were copied, as uninstall "
              "found them, into %s before it changed or removed them" % paths.posix(keep.folder))


# --- update ---------------------------------------------------------------------------------

def _update_stamp() -> Path:
    """The SessionStart hook's cached once-a-day update check."""
    return paths.claude_dir() / "state" / "gk-harness-update.json"


def _drop_update_stamp() -> None:
    """Remove the cached update check (best effort), so the next check starts afresh."""
    try:
        os.remove(str(_update_stamp()))
    except OSError:  # FileNotFoundError included
        pass


def update(options: list) -> int:
    if IGNORE_BUSY not in options:
        refused = _busy_refusal()
        if refused is not None:
            return refused
        # Checked once, here, before the pull: the install below must not refuse after it, and
        # leave the new code pulled but not installed.
        options = list(options) + [IGNORE_BUSY]
    root = paths.harness_root()
    python = paths.python_exe()
    if python is None:
        return _stopped("cannot tell which Python is running the installer")
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    for key in _GIT_REPO_VARS:
        env.pop(key, None)
    sys.stdout.flush()
    try:
        pulled = subprocess.run(["git", "-C", str(root), "pull", "--ff-only"],
                                stdin=subprocess.DEVNULL, env=env)
    except OSError as e:
        return _stopped("git could not be run: %s" % (e.strerror or e))
    if pulled.returncode != 0:
        return _stopped("git pull --ff-only in %s failed (exit %d)"
                        % (paths.posix(root), pulled.returncode))
    # The cached check would go on reporting the update just pulled for up to a day.
    _drop_update_stamp()
    # The pull may have changed the installer itself, so the new code runs the install.
    return subprocess.run([python, str(root / "install.py"), "install"] + options).returncode


# --- status ---------------------------------------------------------------------------------

def _status_drift(root: Path, cdir: Path, settings: Any, python: Any, owned: tuple) -> list:
    drift = []
    for _, src, dst in _deployed(root, cdir):
        current = _sha(dst)
        if current is None:
            drift.append("missing: %s" % paths.posix(dst))
        elif current != _sha(src):
            drift.append("changed: %s" % paths.posix(dst))
    md = cdir / "CLAUDE.md"
    try:
        text = manifest.read_text(md)
        policy = (root / "policy.md").read_text(encoding="utf-8")
        if text is None or not claude_md.has_block(text):
            drift.append("missing: %s (policy block)" % paths.posix(md))
        elif claude_md.apply(text, claude_md.render_block(policy, VERSION))[1] != "unchanged":
            drift.append("changed: %s (policy block)" % paths.posix(md))
    except (OSError, ValueError) as e:
        drift.append("unreadable: %s" % e)
    drift.extend(_hook_entry_drift(cdir, settings, python, owned))
    return drift


def _hook_entry_drift(cdir: Path, settings: Any, python: Any, owned: tuple) -> list:
    """Each hook entry install registers that settings lacks, as "missing: …"; settings is None
    when settings.json is absent or unreadable, so then every entry is missing. With none
    missing, "changed: …" when install would still change the hook entries or the owned keys.
    owned is merge's (desired, last, priors, release); desired is None when routes.json is
    unusable, so then only the hook entries count. A key changed by hand is not drift: install
    leaves it."""
    label = paths.posix(cdir / "settings.json")
    try:
        have = sm.owned_entries(settings)
        missing = []
        for event, matcher, scripts in sm.REGISTRATIONS:
            for script in scripts:
                entry = (event, matcher, sm.hook_command(python, cdir, script))
                if entry in have:
                    have.remove(entry)
                else:
                    missing.append(entry)
        if missing:
            return ["missing: %s (hook entry %s%s: %s)"
                    % (label, event, " [%s]" % matcher if matcher else "", command)
                    for event, matcher, command in missing]
        if isinstance(settings, dict) \
                and sm.dumps(sm.merge(settings, python, cdir, *owned)[0]) != sm.dumps(settings):
            return ["changed: %s (hook entries or owned keys)" % label]
    except (ValueError, TypeError) as e:
        return ["unreadable: %s" % e]
    return []


def _forcing_warnings(settings: Any) -> list:
    """A warning for each of FORCING_VARS set in settings.json's env or in this environment."""
    env = settings.get("env") if isinstance(settings, dict) else None
    out = []
    for name in FORCING_VARS:
        for where, found in (("settings.json env", isinstance(env, dict) and name in env),
                             ("the environment", name in os.environ)):
            if found:
                out.append("warning: %s is set in %s; it overrides every agent's own model"
                           % (name, where))
    return out


def status() -> int:
    root, cdir = paths.harness_root(), paths.claude_dir()
    print("gk-harness %s (checkout %s)" % (VERSION, paths.posix(root)))
    print("claude dir: %s" % paths.posix(cdir))
    try:
        cfg = json.loads(paths.harness_config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        cfg = None
    if not isinstance(cfg, dict):
        print("not installed")
        return 0
    python = cfg.get("python") if isinstance(cfg.get("python"), str) else paths.python_exe()
    print("installed: version %s from %s at %s" % (cfg.get("version"), cfg.get("root"),
                                                   cfg.get("installed_at")))
    print("python: %s" % cfg.get("python"))
    drift = []
    try:
        settings = sm.load_strict(cdir / "settings.json")
    except sm.SettingsError as e:
        settings = None
        drift.append("unreadable: %s" % e)
    entries = sm.owned_entries(settings)
    print("owned settings.json entries: %d" % len(entries))
    for event, matcher, command in entries:
        print("  %s%s: %s" % (event, " [%s]" % matcher if matcher else "", command))
    files = _deployed(root, cdir)
    print("owned files: %s" % ", ".join(
        "%d %s" % (sum(1 for g, _, _ in files if g == group), group)
        for group in dict.fromkeys(_group_label(g) for g, _, _ in DEPLOYED)))
    try:
        journal = manifest.load(paths.manifest_path())
    except manifest.ManifestError as e:
        journal = manifest.empty()
        drift.append("unreadable: %s" % e)
    last, priors = _owned_last(journal)
    ledger_on = ledger_ctl.switch_on()
    release = _release(last, priors, ledger_on)
    try:
        desired = _owned_desired(root, ledger_on)
    except Stop:
        desired = None  # routes.json is unusable: status still reports the rest
    # An absent or unreadable settings.json is drift already, not a hand change of every key.
    changed = sm.hand_changed(settings, last) if isinstance(settings, dict) else []
    if changed:
        print("settings.json keys changed by hand (install leaves them): %s"
              % ", ".join(changed))
    for line in _forcing_warnings(settings):
        print(line)
    rcs = [a.get("path") for inst in journal["installs"] for a in inst["actions"]
           if isinstance(a, dict) and a.get("type") == "shell_rc"]
    if rcs:
        print("shell rc: %s" % rcs[-1])
        rc = Path(rcs[-1])
        try:
            got = manifest.read_text_encoded(rc)
            if got is None or not shell.has_block(got[0], label=rcs[-1]):
                drift.append("missing: %s (gk shortcut block)" % rcs[-1])
        except (OSError, ValueError) as e:
            drift.append("unreadable: %s" % e)
    else:
        print("shell rc: not set up by gk-harness")
    retired = legacy.retire_plan(cdir, paths.backup_root())
    if retired:
        print("legacy: %s still in place (install %s moves them to the backups)"
              % (_plural(len(retired), "old file or folder", "old files or folders"), RETIRE))
    print("ledger: %s" % ("on" if ledger_on else "off"))
    for line in ledger_ctl.status_lines(short=True):
        print("  " + line)
    drift.extend(_status_drift(root, cdir, settings, python, (desired, last, priors, release)))
    if drift:
        print("drift:\n%s" % "\n".join("  " + d for d in drift))
        print("run %s to bring it back in line" % _command_line(root, "install"))
    else:
        print("drift: none")
    return 0


# --- fable ----------------------------------------------------------------------------------

def _fable_off_file() -> Path:
    """The Fable switch the hooks read (hooks/gk_common.py fable_off_file)."""
    return paths.claude_dir() / "state" / "gk-fable-off.json"


def _fable_since(path: Path) -> str:
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return "an unknown time"
    since = data.get("since") if isinstance(data, dict) else None
    return since if isinstance(since, str) and since else "an unknown time"


def fable(word: str) -> int:
    """off writes the switch file ({"since": UTC time}), on removes it, status says which."""
    path = _fable_off_file()
    try:
        if word == "off":
            if not path.is_file():
                manifest.write_json_atomic(path, {"since": paths.utc_iso()})
            print("Fable is off since %s" % _fable_since(path))
        elif word == "on":
            try:
                os.remove(str(path))
            except FileNotFoundError:
                pass
            print("Fable is on")
        elif path.exists():
            print("Fable is off since %s" % _fable_since(path))
        else:
            print("Fable is on")
    except OSError as e:
        _warn("%s: %s" % (paths.posix(path), e.strerror or e))
        return 1
    return 0


# --- ledger ---------------------------------------------------------------------------------

def _installed() -> bool:
    return paths.harness_config_path().is_file()


def ledger(word: str, options: list) -> int:
    """on: refuse on Windows, check for busy sessions, write the switch file ({"since": UTC
    time}, kept when it is there already), then install, which owns the five telemetry keys and
    starts the receiver. off: check for busy sessions, remove the switch, stop the receiver,
    then install, which hands the keys back. status: ledger_ctl.status_lines()."""
    if word == "status":
        for line in ledger_ctl.status_lines():
            print(line)
        return 0
    if word == "on" and _windows():
        sys.stderr.write("gk-harness: the usage ledger is not available on Windows; nothing was "
                         "changed.\n")
        return 2
    if IGNORE_BUSY not in options:
        refused = _busy_refusal()
        if refused is not None:
            return refused
    switch = ledger_ctl.switch_file()
    if word == "on":
        made = not switch.is_file()
        try:
            if made:
                manifest.write_json_atomic(switch, {"since": paths.utc_iso()})
        except OSError as e:
            _warn("%s: %s" % (paths.posix(switch), e.strerror or e))
            return 1
        code = install(False, ignore_busy=True)
        if code != 0:
            if code == 2 and made:
                _unlink(switch)  # install changed nothing, so neither does ledger on
            return code
        # install restarted the receiver when it changed something; start it when it did not.
        if not ledger_ctl.start():
            _warn("ledger: the receiver did not start on port %d (gkh ledger status)"
                  % ledger_ctl.port())
        print("ledger: on since %s; %s" % (ledger_ctl.since() or paths.utc_iso(),
                                            ledger_ctl.RESTART_LINE))
        return 0
    _ledger_down()
    if switch.exists():
        return 1  # _ledger_down said why
    if _installed():
        code = install(False, ignore_busy=True)
        if code != 0:
            return code
    print("ledger: off; the recorded lines stay in %s; restart sessions to stop sending "
          "telemetry" % paths.posix(ledger_ctl.ledger_dir()))
    return 0


# --- entry point ----------------------------------------------------------------------------

def _forgiving_output() -> None:
    """A path the console cannot show must not crash the installer after it has written."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="replace")
            except (ValueError, OSError):
                pass


def _gen(check: bool) -> int:
    root = paths.harness_root()
    if check:
        out = gen.stale(root)
        for rel in out:
            print(rel)
        return 1 if out else 0
    for rel in gen.write(root):
        print(rel)
    return 0


def _price_report(values: dict, as_json: bool) -> int:
    since = values.get(SINCE)
    if since is not None and not report.valid_date(since):
        sys.stderr.write("gk-harness: %s wants a date as YYYY-MM-DD, not '%s'.\n" % (SINCE, since))
        return 2
    cdir = Path(os.path.abspath(values[CONFIG_DIR])) if CONFIG_DIR in values \
        else paths.claude_dir()
    try:
        routes = load_routes(paths.harness_root() / "routes.json")
    except RoutesError as e:
        sys.stderr.write("gk-harness: %s\n" % e)
        return 2
    summary = report.summarise(report.iter_calls(cdir, since, values.get(SESSION),
                                                 classes=routes.get("classes") or {}), routes)
    summary.update(since=since, session=values.get(SESSION))
    try:
        sys.stdout.write(report.render(summary, as_json))
        sys.stdout.flush()
    except BrokenPipeError:
        # Piped into head: the reader has what it wanted. Point stdout at nothing so the
        # interpreter's own flush at exit does not complain.
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    return 0


def _parse(command: str, options: list) -> tuple | None:
    """(flags, {option: value}) for the command's options; None when one is not allowed, comes
    twice, or lacks its value."""
    allowed, takes_value = OPTIONS.get(command, ()), VALUE_OPTIONS.get(command, ())
    flags: list = []
    values: dict = {}
    rest = list(options)
    while rest:
        option = rest.pop(0)
        name, eq, value = option.partition("=")
        if name in takes_value:
            if not eq:
                if not rest:
                    return None
                value = rest.pop(0)
            if name in values:
                return None
            values[name] = value
        elif option in allowed and option not in flags:
            flags.append(option)
        else:
            return None
    return flags, values


def main(argv: list[str]) -> int:
    args = list(argv)
    if not args or args[0].startswith("-"):
        args.insert(0, "install")
    command, parsed = args[0], _parse(args[0], args[1:])
    if command not in COMMANDS or parsed is None \
            or (command == "fable" and len(parsed[0]) != 1) \
            or (command == "ledger" and len([o for o in parsed[0] if o in LEDGER_WORDS]) != 1):
        sys.stderr.write(USAGE)
        return 2
    options, values = parsed
    _forgiving_output()
    if command == "report":
        return _price_report(values, JSON in options)
    if command == "install":
        return install(RETIRE in options, IGNORE_BUSY in options)
    if command == "fable":
        return fable(options[0])
    if command == "ledger":
        return ledger(next(o for o in options if o in LEDGER_WORDS), options)
    if command == "gen":
        return _gen(CHECK in options)
    if command == "uninstall":
        return uninstall()
    if command == "update":
        return update(options)
    return status()
