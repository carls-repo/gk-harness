"""End-to-end tests of install, uninstall, update and status against a temporary home and Claude
config folder. Nothing here touches the real ~/.claude or shell startup files: every test points
HOME, USERPROFILE and GK_CLAUDE_DIR at a temporary folder and stands in for the rc file choice,
except the per-OS rc test, which stands in for the PowerShell answers instead."""
from __future__ import annotations

import codecs
import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from gkh import VERSION, claude_md, cli, legacy, manifest, paths, shell  # noqa: E402
from gkh import settings_merge as sm  # noqa: E402

BOX_HOOKS = Path(ROOT) / "tests" / "fixtures" / "settings" / "box-hooks.json"
BACKUPS = ".claude/backups/gk-harness"
POLICY_BLOCK = claude_md.render_block(
    (Path(ROOT) / "policy.md").read_text(encoding="utf-8"), VERSION)
REPO_HOOKS = sorted(p.name for p in (Path(ROOT) / "hooks").glob("*.py"))
REPO_AGENTS = sorted(p.name for p in (Path(ROOT) / "agents").glob("worker-*.md"))
OLD_POLICY = ("# Model orchestration policy\n\nThe main session plans and judges; workers do the "
              "muscle.\n\n## Routing\n\n- chores go to the cheapest tier\n")
NOTES = "# This machine\n\n- the NAS answers at nas.local\n- run backups on Fridays\n"
BASHRC = ("# ~/.bashrc\nexport PATH=\"$HOME/bin:$PATH\"\n"
          "alias gk='claude --model fable'\nalias gk-opus='claude --model opus'\n"
          "alias gk-sonnet='claude --model sonnet'\nalias ll='ls -l'\n")
# The rest of today's settings.json keys, with invented values.
BOX_OTHER_KEYS = {
    "permissions": {"allow": ["Bash(git status)", "Read(~/notes/**)"],
                    "deny": ["Bash(rm -rf /)"], "defaultMode": "auto"},
    "model": "fable",
    "effortLevel": "high",
    "modelSettings": {"fable": {"effort": "high"}, "opus": {"effort": "xhigh"}},
    "autoMode": {"soft_deny": ["psql against the production database"],
                 "allow": ["npm test", "git log"]},
}
KEPT_KEYS = ("autoMode", "permissions", "model", "effortLevel", "modelSettings")
EXECUTION_POLICY = "Set-ExecutionPolicy -Scope CurrentUser RemoteSigned"
# The hook registrations, spelled out from the brief's table rather than taken from gkh: each
# group's event, matcher (None for none) and scripts, in order.
EXPECTED_GROUPS = (
    ("SessionStart", None, ("gk-seat.py", "gk-peers.py")),
    ("PostModelSwitch", None, ("gk-seat.py",)),
    ("UserPromptSubmit", None, ("gk-peers.py",)),
    ("PreToolUse", "Workflow", ("gk-profile.py",)),
    ("PostToolUse", "Edit|Write|MultiEdit|NotebookEdit", ("gk-claim.py", "gk-remote-pending.py")),
    ("PostToolUse", "mcp__.*__(batch|update|create)|Artifact|SendUserFile",
     ("gk-remote-clear.py",)),
    ("Stop", None, ("gk-remote-stop.py",)),
    ("SessionEnd", None, ("gk-claims-end.py",)),
)
EXPECTED_EVENTS = ["SessionStart", "PostModelSwitch", "UserPromptSubmit", "PreToolUse",
                   "PostToolUse", "Stop", "SessionEnd"]
# The commands in tests/fixtures/settings/box-hooks.json, spelled out: the pre-harness gk hooks
# (gk-peers.py twice: SessionStart and UserPromptSubmit), and the two install leaves alone.
LEGACY_BOX_COMMANDS = ['python3 "$HOME/.claude/hooks/gk-claim.py"',
                       'python3 "$HOME/.claude/hooks/gk-remote-pending.py"',
                       'python3 "$HOME/.claude/hooks/gk-remote-clear.py"',
                       '"$HOME/.claude/hooks/gk-profile-guard.sh"',
                       'python3 "$HOME/.claude/hooks/gk-peers.py"',
                       'python3 "$HOME/.claude/hooks/gk-peers.py"',
                       'python3 "$HOME/.claude/hooks/gk-remote-stop.py"',
                       'python3 "$HOME/.claude/hooks/gk-claims-end.py"']
OTHER_BOX_COMMANDS = ['"$HOME/.claude/hooks/supabase-row-cap.sh"',
                      '"$HOME/.claude/hooks/pii-in-logs.sh"']
# What a legacy command names, from the brief.
LEGACY_NAMES = ("gk-profile-guard.sh", "hooks/gk-claim.py", "hooks/gk-claims-end.py",
                "hooks/gk-peers.py", "hooks/gk-remote-clear.py", "hooks/gk-remote-pending.py",
                "hooks/gk-remote-stop.py")


def runner_python() -> str:
    """The Python running these tests as install records it: absolute, posix, not resolved."""
    return Path(os.path.abspath(sys.executable)).as_posix()


def expected_command(python: str, cdir: Path, script: str) -> str:
    return '"%s" "%s/hooks/gk-harness/%s"' % (python, cdir.as_posix(), script)


def expected_hooks(python: str, cdir: Path) -> dict:
    """settings.json's "hooks" after an install into an empty home."""
    hooks: dict = {}
    for event, matcher, scripts in EXPECTED_GROUPS:
        group: dict = {} if matcher is None else {"matcher": matcher}
        group["hooks"] = [{"type": "command", "command": expected_command(python, cdir, s),
                           "timeout": 10} for s in scripts]
        hooks.setdefault(event, []).append(group)
    return hooks


def expected_entries(python: str, cdir: Path) -> list:
    """[(event, matcher, command, timeout)] for each entry install registers, in order."""
    return [(event, matcher, expected_command(python, cdir, s), 10)
            for event, matcher, scripts in EXPECTED_GROUPS for s in scripts]


def entries_of(settings: dict) -> list:
    """[(event, matcher, command, timeout)] for each hook entry in settings, in file order."""
    return [(event, group.get("matcher"), e.get("command"), e.get("timeout"))
            for event, groups in settings.get("hooks", {}).items() for group in groups
            for e in group.get("hooks", [])]


def insert_in_block(text: str, line: str) -> str:
    """text with line put just after its first line, which must be a gk-harness begin marker
    (a file install created, or whose old policy it replaced, starts with its block), so that
    the edit is inside the block whatever the policy says."""
    first, nl, rest = text.partition("\n")
    if not first.lstrip("\ufeff").startswith(("<!-- gk-harness:begin v", "# >>> gk-harness >>>")):
        raise AssertionError("the first line is not a begin marker: %r" % first)
    return first + nl + line + rest


def find_bash() -> tuple:
    """(the bash Claude Code runs hook commands with, None), or (None, why there is none). On
    Windows that is Git Bash: CLAUDE_CODE_GIT_BASH_PATH, else a bash on PATH that is not the WSL
    launcher (in System32 or WindowsApps), else the bash.exe of the Git for Windows that the git
    on PATH belongs to."""
    if os.name != "nt":
        bash = shutil.which("bash")
        return (bash, None) if bash else (None, "bash is not on PATH")
    named = os.environ.get("CLAUDE_CODE_GIT_BASH_PATH")
    if named and os.path.isfile(named):
        return named, None
    found = shutil.which("bash")
    wsl = bool(found) and any(p in found.lower() for p in ("\\system32\\", "\\windowsapps\\"))
    if found and not wsl:
        return found, None
    git = shutil.which("git")
    if git:
        top = Path(git).resolve().parent.parent  # <Git>\cmd\git.exe, or <Git>\mingw64\bin\git.exe
        for bash in (top / "bin" / "bash.exe", top.parent / "bin" / "bash.exe"):
            if bash.is_file():
                return str(bash), None
    if wsl:
        return None, "only the WSL launcher %s was found, not Git Bash" % found
    return None, "Git Bash was not found (no bash on PATH or beside git, and no " \
                 "CLAUDE_CODE_GIT_BASH_PATH)"


def box_settings() -> dict:
    hooks = json.loads(BOX_HOOKS.read_text(encoding="utf-8"))
    out = {"permissions": BOX_OTHER_KEYS["permissions"], "model": BOX_OTHER_KEYS["model"],
           "hooks": hooks}
    out.update((k, BOX_OTHER_KEYS[k]) for k in ("effortLevel", "modelSettings", "autoMode"))
    return out


def _meta(p: Path, mtimes: bool) -> tuple:
    st = os.lstat(str(p))
    mode = () if os.name == "nt" else (stat.S_IMODE(st.st_mode),)
    return mode + ((st.st_mtime_ns,) if mtimes else ())


def snapshot(top: Path, mtimes: bool = False, skip: tuple = (BACKUPS,)) -> dict:
    """{relative posix path: what it holds} for every folder (path ending "/") and file under top:
    a file's bytes, plus its mode on POSIX, plus its mtime when asked; a folder's mode."""
    out: dict = {}
    for dirpath, dirnames, filenames in os.walk(str(top)):
        base = Path(dirpath)
        keep = []
        for d in sorted(dirnames):
            rel = (base / d).relative_to(top).as_posix()
            if rel in skip:
                continue
            keep.append(d)
            out[rel + "/"] = _meta(base / d, False)
        dirnames[:] = keep
        for f in filenames:
            p = base / f
            out[p.relative_to(top).as_posix()] = (p.read_bytes(),) + _meta(p, mtimes)
    return out


def write(path: Path, text: str, mode: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    if mode is not None and os.name != "nt":
        os.chmod(str(path), mode)
    return path


def commands_of(settings: dict) -> list:
    out = []
    for groups in settings.get("hooks", {}).values():
        for group in groups:
            out.extend(e.get("command") for e in group.get("hooks", []))
    return out


def copy_checkout(dst: Path) -> Path:
    """A copy of this checkout's working tree that install can run from."""
    shutil.copytree(ROOT, str(dst), ignore=shutil.ignore_patterns(
        ".git", ".superpowers", "__pycache__", "tests", "docs"))
    return dst


class Killed(BaseException):
    """Stands in for a kill (SIGKILL, a closed terminal, power loss). No except clause of the
    installer catches it, and once it is raised every later write raises it too, so not even a
    finally clause can save anything after it: what is on disk is what a kill would leave."""


@contextlib.contextmanager
def watch_writes(fail_at: int = 0, error: BaseException | None = None, after: bool = False,
                 only: Path | None = None):
    """Watch manifest.write_bytes_atomic, through which every installer write goes (copies, the
    journal and the targets). With fail_at, its fail_at-th call (counting only the writes to
    only, when given) raises error: before it writes, or with after once it has written. After
    a Killed, every later call raises Killed too. Yields {"calls": [posix path of each call],
    "written": [posix path of each call that completed]}."""
    real = manifest.write_bytes_atomic
    log: dict = {"calls": [], "written": [], "dead": False}
    watched = None if only is None else paths.posix(only)

    def fake(path, data, created=None, private=False):
        if log["dead"]:
            raise Killed()
        p = paths.posix(path)
        log["calls"].append(p)
        n = len(log["calls"]) if watched is None else log["calls"].count(watched)
        hit = bool(fail_at) and n == fail_at and watched in (None, p)
        if hit and not after:
            log["dead"] = isinstance(error, Killed)
            raise error
        sha = real(path, data, created, private)
        log["written"].append(p)
        if hit:
            log["dead"] = isinstance(error, Killed)
            raise error
        return sha

    with mock.patch.object(manifest, "write_bytes_atomic", fake):
        yield log


class InstallBase(unittest.TestCase):
    """A temporary home with an empty .claude in it. The shell startup file is <home>/.bashrc
    unless patch_rc is false."""

    home_name = "home"
    patch_rc = True

    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.tmp = Path(tmp)
        self.home = self.tmp / self.home_name
        self.cdir = self.home / ".claude"
        self.cdir.mkdir(parents=True)
        self.rc = self.home / ".bashrc"
        env = mock.patch.dict(os.environ, {"HOME": str(self.home), "USERPROFILE": str(self.home),
                                           "GK_CLAUDE_DIR": str(self.cdir), "SHELL": "/bin/bash"})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("GK_PROFILE", None)
        if self.patch_rc:
            rc = mock.patch.object(paths, "rc_file", lambda *a, **k: self.rc)
            rc.start()
            self.addCleanup(rc.stop)

    def gk(self, *argv: str) -> tuple:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def ok(self, *argv: str) -> tuple:
        code, out, err = self.gk(*argv)
        self.assertEqual(code, 0, out + err)
        return out, err

    def settings(self) -> dict:
        return json.loads((self.cdir / "settings.json").read_text(encoding="utf-8"))

    def journal(self) -> dict:
        return manifest.load(self.journal_path())

    def journal_path(self) -> Path:
        return self.cdir / "backups" / "gk-harness" / "manifest.json"

    def uninstall_copies(self) -> set:
        """The folders uninstall has copied edited files into."""
        root = self.cdir / "backups" / "gk-harness"
        return {p for p in root.iterdir() if "-uninstall" in p.name} if root.is_dir() else set()

    def uninstall_keeping(self) -> tuple:
        """Run uninstall: (stdout, stderr, the folder it copied edited files into, or None)."""
        before = self.uninstall_copies()
        out, err = self.ok("uninstall")
        new = self.uninstall_copies() - before
        self.assertLessEqual(len(new), 1, new)
        return out, err, (new.pop() if new else None)

    def link(self, path: Path, target: Path) -> Path:
        """path made a symlink to target, or the test skipped where symlinks cannot be made."""
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(str(target), str(path))
        except (OSError, NotImplementedError) as e:
            self.skipTest("cannot make a symlink here: %s" % e)
        return path

    def fresh_home(self) -> None:
        shutil.rmtree(str(self.home))
        self.cdir.mkdir(parents=True)

    def make_box(self) -> dict:
        """A home shaped like today's box: the old policy as CLAUDE.md, settings.json with
        today's hooks section verbatim, the old hook files, profiles/, two existing workers and
        a .bashrc with the three old aliases. Returns the settings it wrote."""
        c = self.cdir
        write(c / "CLAUDE.md", OLD_POLICY)
        before = box_settings()
        write(c / "settings.json", sm.dumps(before), 0o600)
        for rel in legacy.RETIRE_PATHS[:-1]:
            write(c / rel, "#!/bin/sh\n# old %s\n" % rel, 0o755)
        write(c / "profiles" / "CLAUDE.md.next", OLD_POLICY)
        write(c / "profiles" / "apply-three-seat-policy.sh", "#!/bin/sh\necho apply\n", 0o755)
        write(c / "profiles" / "opus-seat.json", '{"model": "opus"}\n')
        write(c / "hooks" / "supabase-row-cap.sh", "#!/bin/sh\nexit 0\n", 0o755)
        write(c / "hooks" / "pii-in-logs.sh", "#!/bin/sh\nexit 0\n", 0o755)
        write(c / "agents" / "worker-opus.md", "---\nname: worker-opus\n---\nold opus worker\n")
        write(c / "agents" / "worker-haiku.md", "---\nname: worker-haiku\n---\nold haiku\n")
        write(c / "agents" / "my-helper.md", "---\nname: my-helper\n---\nmine\n")
        write(self.rc, BASHRC)
        return before

    def assertTreeEqual(self, start: dict, now: dict) -> None:
        """now equals start, apart from the backups folder that holds backups/gk-harness."""
        if ".claude/backups/" not in start:
            now = {k: v for k, v in now.items() if k != ".claude/backups/"}
        self.assertEqual(sorted(now), sorted(start))
        for key in start:
            self.assertEqual(now[key], start[key], key)


class RoundTripTests(InstallBase):
    def test_round_trip_fresh(self):
        start = snapshot(self.home)
        out, err = self.ok("install")
        self.assertIn("hooks/gk-harness: %d files copied" % (len(REPO_HOOKS) + 1), out)
        self.assertEqual(err, "")
        first = snapshot(self.home, mtimes=True, skip=())

        hooks = self.cdir / "hooks" / "gk-harness"
        self.assertEqual(sorted(os.listdir(str(hooks))), sorted(REPO_HOOKS + ["routes.json"]))
        for name in REPO_HOOKS:
            self.assertEqual((hooks / name).read_bytes(),
                             (Path(ROOT) / "hooks" / name).read_bytes(), name)
        self.assertEqual(sorted(os.listdir(str(self.cdir / "agents"))), REPO_AGENTS)
        self.assertEqual((self.cdir / "CLAUDE.md").read_text(encoding="utf-8"), POLICY_BLOCK)
        settings = self.settings()
        self.assertEqual(settings, {"hooks": expected_hooks(runner_python(), self.cdir),
                                    "ultracode": True})
        self.assertEqual(list(settings["hooks"]), EXPECTED_EVENTS)
        cfg = json.loads((self.cdir / "gk-harness.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(cfg), ["installed_at", "python", "root", "version"])
        self.assertEqual((cfg["version"], cfg["root"], cfg["python"]),
                         (VERSION, Path(ROOT).resolve().as_posix(),
                          Path(paths.python_exe()).as_posix()))
        self.assertIn('. "%s/shell/aliases.' % Path(ROOT).resolve().as_posix(),
                      self.rc.read_text(encoding="utf-8"))
        self.assertEqual(os.listdir(str(self.cdir / "backups" / "gk-harness")),
                         ["manifest.json"])

        out, err = self.ok("install")
        self.assertIn("nothing to change", out)
        self.assertEqual(err, "")
        self.assertEqual(snapshot(self.home, mtimes=True, skip=()), first)

        self.ok("uninstall")
        self.assertTreeEqual(start, snapshot(self.home))
        self.assertEqual(self.journal()["installs"], [])

    def test_routes_deployed(self):
        self.ok("install")
        deployed = self.cdir / "hooks" / "gk-harness" / "routes.json"
        self.assertEqual(deployed.read_bytes(), (Path(ROOT) / "routes.json").read_bytes())
        self.ok("uninstall")
        self.assertFalse(deployed.exists())

    def test_round_trip_legacy_box(self):
        c = self.cdir
        before = self.make_box()
        self.assertEqual(sorted(commands_of(before)),
                         sorted(LEGACY_BOX_COMMANDS + OTHER_BOX_COMMANDS))
        start = snapshot(self.home)

        out, _ = self.ok("install")
        self.assertIn(", 3 old gk aliases removed\n", out)
        self.assertEqual((c / "CLAUDE.md").read_text(encoding="utf-8"), POLICY_BLOCK)
        status, _ = self.ok("status")
        self.assertIn("legacy: %d old files or folders still in place" % len(legacy.RETIRE_PATHS),
                      status)
        after = self.settings()
        self.assertEqual([x for x in commands_of(after) if any(n in x for n in LEGACY_NAMES)], [])
        # The box's events come in its own order, so the entries are compared as a multiset.
        self.assertCountEqual([e for e in entries_of(after) if e[2] not in OTHER_BOX_COMMANDS],
                              expected_entries(runner_python(), c))
        for key in KEPT_KEYS:
            self.assertEqual(sm.dumps(after[key]), sm.dumps(before[key]), key)
        self.assertEqual([g for g in after["hooks"]["PostToolUse"]
                          if g.get("matcher") == "Edit|Write|MultiEdit"],
                         [before["hooks"]["PostToolUse"][0]])
        self.assertEqual([h["command"] for h in before["hooks"]["PostToolUse"][0]["hooks"]],
                         ['"$HOME/.claude/hooks/supabase-row-cap.sh"',
                          '"$HOME/.claude/hooks/pii-in-logs.sh"'])
        for rel in legacy.RETIRE_PATHS:
            self.assertTrue((c / rel).exists(), rel)
        rc = self.rc.read_text(encoding="utf-8")
        self.assertNotIn("alias gk", rc)
        self.assertIn("alias ll='ls -l'\n", rc)
        self.assertTrue(rc.startswith("# ~/.bashrc\nexport PATH="))
        self.assertTrue(shell.has_block(rc))
        self.assertEqual((c / "agents" / "worker-opus.md").read_bytes(),
                         (Path(ROOT) / "agents" / "worker-opus.md").read_bytes())
        backup = Path(self.journal()["installs"][-1]["backup"])
        self.assertEqual((backup / "agents" / "worker-opus.md").read_text(encoding="utf-8"),
                         "---\nname: worker-opus\n---\nold opus worker\n")
        self.assertEqual((backup / "CLAUDE.md").read_text(encoding="utf-8"), OLD_POLICY)
        self.assertEqual((backup / "settings.json").read_text(encoding="utf-8"),
                         sm.dumps(before))
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(os.stat(str(c / "settings.json")).st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(os.stat(str(backup / "settings.json")).st_mode), 0o600)

        out, _ = self.ok("install", "--retire-legacy")
        self.assertIn("  %d old files or folders retired into " % len(legacy.RETIRE_PATHS), out)
        retired = self.journal()["installs"][-1]
        self.assertEqual({a["type"] for a in retired["actions"]}, {"legacy_move"})
        moved_to = Path(retired["backup"]) / "legacy"
        for rel in legacy.RETIRE_PATHS:
            self.assertFalse(os.path.lexists(str(c / rel)), rel)
            self.assertTrue(os.path.lexists(str(moved_to / rel)), rel)
        self.assertEqual((moved_to / "profiles" / "CLAUDE.md.next").read_text(encoding="utf-8"),
                         OLD_POLICY)
        self.assertTrue((c / "hooks" / "supabase-row-cap.sh").exists())

        self.ok("uninstall")
        self.assertTreeEqual(start, snapshot(self.home))

    def test_machine_notes_preserved(self):
        write(self.cdir / "CLAUDE.md", NOTES)
        start = snapshot(self.home)
        self.ok("install")
        md = (self.cdir / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertEqual(md, NOTES + "\n" + POLICY_BLOCK)
        self.ok("install")
        self.ok("uninstall")
        self.assertTreeEqual(start, snapshot(self.home))

        # Notes edited after install: the hash no longer matches, so the block is stripped and
        # the edited notes stay.
        self.ok("install")
        md_path = self.cdir / "CLAUDE.md"
        write(md_path, "# Added later\n" + md_path.read_text(encoding="utf-8"))
        self.ok("uninstall")
        self.assertEqual(md_path.read_text(encoding="utf-8"), "# Added later\n" + NOTES)

    def test_user_edit_survives_uninstall(self):
        start_settings = {"permissions": {"allow": ["Bash(git status)"]}, "model": "opus",
                          "ultracode": False}
        write(self.cdir / "settings.json", sm.dumps(start_settings))
        self.ok("install")
        edited = self.settings()
        edited["permissions"]["allow"].append("Bash(ls:*)")
        write(self.cdir / "settings.json", json.dumps(edited, indent=4))
        self.ok("uninstall")
        now = self.settings()
        self.assertEqual(sm.owned_entries(now), [])
        self.assertEqual(now["permissions"]["allow"], ["Bash(git status)", "Bash(ls:*)"])
        expected = json.loads(json.dumps(start_settings))
        expected["permissions"]["allow"].append("Bash(ls:*)")
        self.assertEqual(now, expected)


class PreflightTests(InstallBase):
    def fill_box(self) -> None:
        write(self.cdir / "CLAUDE.md", NOTES)
        write(self.cdir / "agents" / "worker-opus.md", "old\n")
        write(self.rc, BASHRC)

    def assertStopped(self, code: int, err: str, start: dict, *needles: str) -> None:
        self.assertEqual(code, 2, err)
        self.assertTrue(err.startswith("gk-harness: "), err)
        self.assertTrue(err.endswith("; nothing was changed.\n"), err)
        for needle in needles:
            self.assertIn(needle, err)
        self.assertEqual(snapshot(self.home, mtimes=True, skip=()), start)

    def test_invalid_settings_aborts_without_writes(self):
        self.fill_box()
        whole = sm.dumps(box_settings())
        for text in ('{"a": 1,}', "// c\n{}", whole[:len(whole) // 2]):
            with self.subTest(text=text[:20]):
                write(self.cdir / "settings.json", text)
                start = snapshot(self.home, mtimes=True, skip=())
                code, out, err = self.gk("install")
                self.assertStopped(code, err, start, "settings.json")

    def test_unquotable_paths_abort_without_writes(self):
        self.fill_box()
        for bad in ("a$b", "a`b"):
            with self.subTest(bad=bad, path="claude dir"):
                target = self.home / bad / ".claude"
                start = snapshot(self.home, mtimes=True, skip=())
                with mock.patch.dict(os.environ, {"GK_CLAUDE_DIR": str(target)}):
                    code, out, err = self.gk("install")
                self.assertStopped(code, err, start, "contains %s" % bad[1])
            with self.subTest(bad=bad, path="checkout"):
                root = copy_checkout(self.tmp / bad / "checkout")
                start = snapshot(self.home, mtimes=True, skip=())
                with mock.patch.object(paths, "harness_root", lambda: root):
                    code, out, err = self.gk("install")
                self.assertStopped(code, err, start, "contains %s" % bad[1])

    def test_old_python_aborts(self):
        self.fill_box()
        start = snapshot(self.home, mtimes=True, skip=())
        with mock.patch.object(sys, "version_info", (3, 8, 18, "final", 0)):
            code, out, err = self.gk("install")
        self.assertStopped(code, err, start, "Python 3.9 or newer is required")

    def test_missing_source_aborts(self):
        self.fill_box()
        root = copy_checkout(self.tmp / "checkout")
        (root / "agents" / "worker-opus.md").unlink()
        (root / "hooks" / "gk-seat.py").unlink()
        start = snapshot(self.home, mtimes=True, skip=())
        with mock.patch.object(paths, "harness_root", lambda: root):
            code, out, err = self.gk("install")
        self.assertStopped(code, err, start, "agents/worker-opus.md", "hooks/gk-seat.py")

    def test_unusable_routes_does_not_block_uninstall_or_status(self):
        self.ok("install")
        root = copy_checkout(self.tmp / "checkout")
        for breakage in ("{bad", None):
            with self.subTest(breakage=breakage):
                if breakage is None:
                    (root / "routes.json").unlink()
                else:
                    (root / "routes.json").write_text(breakage, encoding="utf-8")
                with mock.patch.object(paths, "harness_root", lambda: root):
                    code, out, err = self.gk("status")
                    self.assertNotIn("1 .", out)
                    self.assertIn("owned files: %d hooks," % (len(REPO_HOOKS) + (breakage is not None)), out)
                    code, out, err = self.gk("install")
                    self.assertEqual(code, 2)
                    self.assertIn("routes.json", err)
        with mock.patch.object(paths, "harness_root", lambda: root):
            code, out, err = self.gk("uninstall")
        self.assertEqual(code, 0, err)
        self.assertFalse((self.cdir / "hooks" / "gk-harness" / "routes.json").exists())

    def test_claude_md_that_cannot_be_edited_aborts(self):
        self.fill_box()
        cases = (("utf-16", codecs.BOM_UTF16_LE + NOTES.encode("utf-16-le"), "UTF-16"),
                 ("unpaired marker", ("notes\n%s\n" % claude_md.END_MARKER).encode(),
                  "end marker"))
        for name, data, needle in cases:
            with self.subTest(name):
                (self.cdir / "CLAUDE.md").write_bytes(data)
                start = snapshot(self.home, mtimes=True, skip=())
                code, out, err = self.gk("install")
                self.assertStopped(code, err, start, "CLAUDE.md", needle)

    def test_rc_file_that_cannot_be_edited_is_skipped_with_a_notice(self):
        data = "# profile\n".encode("utf-16-le")  # UTF-16 without a byte-order mark
        self.rc.write_bytes(data)
        out, err = self.ok("install")
        self.assertIn(paths.posix(self.rc), out)
        self.assertIn("gk shortcuts were not set up", out)
        self.assertEqual(self.rc.read_bytes(), data)
        self.assertEqual([a for a in self.journal()["installs"][0]["actions"]
                          if a["type"] == "shell_rc"], [])
        self.assertTrue((self.cdir / "settings.json").exists())

    def test_usage(self):
        for argv in (["frobnicate"], ["install", "--force"], ["uninstall", "--retire-legacy"],
                     ["status", "x"]):
            with self.subTest(argv=argv):
                code, out, err = self.gk(*argv)
                self.assertEqual(code, 2)
                self.assertTrue(err.startswith("usage: "), err)
        self.assertEqual(os.listdir(str(self.cdir)), [])


class UninstallTests(InstallBase):
    def test_nothing_recorded(self):
        out, err = self.ok("uninstall")
        self.assertIn("nothing to uninstall", out)
        self.assertEqual(os.listdir(str(self.cdir)), [])

    def test_uninstall_removes_the_update_stamp_and_an_empty_seat_folder(self):
        self.ok("install")
        state = self.cdir / "state"
        stamp = state / "gk-harness-update.json"
        write(stamp, json.dumps({"checked": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                 "available": True}))
        (state / "gk-seat").mkdir()
        write(state / "gk-peers" / "s1.json", "{}")
        self.ok("uninstall")
        self.assertFalse(os.path.lexists(str(stamp)))
        self.assertFalse(os.path.lexists(str(state / "gk-seat")))
        # state/ itself and the other hooks' folders stay
        self.assertTrue((state / "gk-peers" / "s1.json").is_file())

    def test_uninstall_keeps_a_seat_folder_that_still_holds_sessions(self):
        self.ok("install")
        seat = self.cdir / "state" / "gk-seat" / "s1.json"
        write(seat, "{}")
        self.ok("uninstall")
        self.assertTrue(seat.is_file())

    def test_files_put_back_by_hand_before_uninstall(self):
        # A file that already equals its backup is as it was before the install: uninstall leaves
        # it, adding no legacy entry twice and printing no changed-since notice.
        before = self.make_box()
        start = snapshot(self.home)
        self.ok("install")
        backup = Path(self.journal()["installs"][-1]["backup"])
        for rel in ("settings.json", "CLAUDE.md", "agents/worker-opus.md"):
            shutil.copyfile(str(backup / rel), str(self.cdir / rel))
        shutil.copyfile(str(backup / "rc" / self.rc.name), str(self.rc))
        out, err = self.ok("uninstall")
        self.assertEqual(err, "")
        self.assertNotIn("not put back", out)
        self.assertNotIn("old policy it replaced", out)
        self.assertEqual([x for x in commands_of(self.settings()) if legacy.is_legacy_command(x)],
                         [x for x in commands_of(before) if legacy.is_legacy_command(x)])
        self.assertTreeEqual(start, snapshot(self.home))

    def test_changed_copied_file_is_left_with_a_warning(self):
        start = snapshot(self.home)
        self.ok("install")
        seat = self.cdir / "hooks" / "gk-harness" / "gk-seat.py"
        write(seat, seat.read_text(encoding="utf-8") + "# a local fix\n")
        agent = self.cdir / "agents" / "worker-opus.md"
        write(agent, "my own opus worker\n")
        out, err = self.ok("uninstall")
        for path in (seat, agent):
            self.assertIn("changed since install, left in place: %s\n" % paths.posix(path), err)
            self.assertTrue(path.exists())
        self.assertEqual(sorted(os.listdir(str(seat.parent))), ["gk-seat.py"])
        self.assertEqual(sorted(os.listdir(str(agent.parent))), ["worker-opus.md"])
        now = snapshot(self.home)
        for rel in (".claude/hooks/", ".claude/hooks/gk-harness/",
                    ".claude/hooks/gk-harness/gk-seat.py", ".claude/agents/",
                    ".claude/agents/worker-opus.md"):
            now.pop(rel)
        self.assertTreeEqual(start, now)

    def test_unreadable_targets_are_warned_about_and_skipped(self):
        self.ok("install")
        targets = (self.cdir / "CLAUDE.md", self.rc, self.cdir / "settings.json",
                   self.cdir / "agents" / "worker-opus.md")
        for path in targets:
            path.unlink()
            (path / "inside").mkdir(parents=True)
        out, err = self.ok("uninstall")
        for path in targets:
            self.assertIn(paths.posix(path), err)
            self.assertTrue((path / "inside").is_dir())
        self.assertFalse((self.cdir / "hooks").exists())
        self.assertEqual(self.journal()["installs"], [])

    def test_created_files_edited_since(self):
        # Notes go in after any byte-order mark, as an editor writes them: the PowerShell profile
        # install creates on Windows starts with one.
        start = snapshot(self.home)
        md = self.cdir / "CLAUDE.md"
        for name, edit, expected in (
                ("edited inside the block", lambda t: insert_in_block(t, "# mine\n"), None),
                ("notes added", lambda t: claude_md.bom_of(t) + "# notes\n\n"
                 + t[len(claude_md.bom_of(t)):], "# notes\n")):
            with self.subTest(name):
                self.ok("install")
                marks = {}
                for path in (md, self.rc):
                    text = path.read_text(encoding="utf-8")
                    marks[path] = claude_md.bom_of(text)
                    edited = edit(text)
                    self.assertNotEqual(edited, text, path)  # so the surgical path runs
                    write(path, edited)
                self.ok("uninstall")
                for path in (md, self.rc):
                    if expected is None:
                        self.assertFalse(path.exists(), path)
                    else:
                        self.assertEqual(path.read_text(encoding="utf-8"),
                                         marks[path] + expected)
                        path.unlink()
                self.assertTreeEqual(start, snapshot(self.home))

    def test_edited_files_are_copied_before_uninstall_changes_them(self):
        # Nothing edited since install: nothing copied, no folder.
        self.ok("install")
        out, err, kept = self.uninstall_keeping()
        self.assertIsNone(kept)
        self.assertNotIn("were copied", out)

        # Each file uninstall rewrites or removes after an edit is copied first, as it found it:
        # settings.json (rewritten), CLAUDE.md (an edited block overwritten by the old policy it
        # replaced) and the rc file install created (removed once stripped, a line the user
        # wrote inside the block with it).
        write(self.cdir / "CLAUDE.md", OLD_POLICY)
        write(self.cdir / "settings.json", sm.dumps({"model": "opus"}), 0o600)
        self.ok("install")
        settings = self.settings()
        settings["permissions"] = {"allow": ["Bash(ls:*)"]}
        edited = {"settings.json": json.dumps(settings, indent=4),
                  "CLAUDE.md": insert_in_block(POLICY_BLOCK, "- my own rule\n"),
                  "rc/.bashrc": insert_in_block(self.rc.read_text(encoding="utf-8"),
                                                "alias gl='git log'\n")}
        for rel, text in edited.items():
            write(self.rc if rel.startswith("rc/") else self.cdir / rel, text)
        out, err, kept = self.uninstall_keeping()
        self.assertEqual(err, "")
        self.assertIsNotNone(kept)
        self.assertEqual(out.count(paths.posix(kept)), 1)
        self.assertEqual(self.settings(),
                         {"model": "opus", "permissions": {"allow": ["Bash(ls:*)"]}})
        self.assertEqual((self.cdir / "CLAUDE.md").read_text(encoding="utf-8"), OLD_POLICY)
        self.assertFalse(self.rc.exists())
        copies = sorted(p.relative_to(kept).as_posix() for p in kept.rglob("*") if p.is_file())
        self.assertEqual(copies, sorted(edited))
        for rel, text in edited.items():
            copy = kept.joinpath(*rel.split("/"))
            self.assertEqual(copy.read_bytes().decode("utf-8"), text, rel)
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(os.stat(str(copy)).st_mode), 0o600, rel)

    def test_replaced_old_policy_changed_since(self):
        md = self.cdir / "CLAUDE.md"
        cases = (
            ("notes added after the block", lambda t: t + "\n## Mine\n", "\n## Mine\n"),
            ("edited inside the block", lambda t: insert_in_block(t, "- my own rule\n"),
             OLD_POLICY),
            ("byte-order mark added", lambda t: "\ufeff" + t, OLD_POLICY),
            ("blank lines added", lambda t: t + "\n\n", OLD_POLICY),
        )
        for name, edit, expected in cases:
            with self.subTest(name):
                write(md, OLD_POLICY)
                self.ok("install")
                self.assertEqual(md.read_text(encoding="utf-8"), POLICY_BLOCK)
                saved = [a["prior"]["backup"] for a in self.journal()["installs"][-1]["actions"]
                         if a["type"] == "claude_md"][0]
                edited = edit(md.read_text(encoding="utf-8"))
                self.assertNotEqual(edited, POLICY_BLOCK)  # so the surgical path runs
                write(md, edited)
                out, err, kept = self.uninstall_keeping()
                self.assertEqual(md.read_bytes().decode("utf-8"), expected)
                self.assertEqual(Path(saved).read_text(encoding="utf-8"), OLD_POLICY)
                if expected != OLD_POLICY:
                    self.assertIn(saved, out)
                # The edited file, overwritten or rewritten, was copied first.
                self.assertEqual((kept / "CLAUDE.md").read_bytes().decode("utf-8"), edited)

    def test_two_installs_unwind_newest_first(self):
        # An update: the second install replaces what the first wrote, backing it up first, and
        # one uninstall unwinds both. CLAUDE.md is edited between them and after the second.
        root = copy_checkout(self.tmp / "checkout")
        write(self.cdir / "CLAUDE.md", NOTES)
        write(self.cdir / "settings.json", sm.dumps({"model": "opus"}))
        start = snapshot(self.home)
        md = self.cdir / "CLAUDE.md"
        with mock.patch.object(paths, "harness_root", lambda: root):
            self.ok("install")
            write(md, "# Between installs\n" + md.read_text(encoding="utf-8"))
            write(root / "policy.md", (root / "policy.md").read_text(encoding="utf-8")
                  + "\nA second-version line.\n")
            peers = root / "hooks" / "gk-peers.py"
            write(peers, peers.read_text(encoding="utf-8") + "# v2\n")
            out, _ = self.ok("install")
            self.assertIn("hooks/gk-harness: 1 file copied (1 backed up first)", out)
            self.assertIn("CLAUDE.md: policy block updated", out)
            second = self.journal()["installs"][-1]
            backup = Path(second["backup"])
            self.assertEqual(sorted(a["type"] for a in second["actions"]),
                             ["claude_md", "file"])
            self.assertEqual((backup / "hooks" / "gk-harness" / "gk-peers.py").read_bytes(),
                             (Path(ROOT) / "hooks" / "gk-peers.py").read_bytes())
            self.assertIn("A second-version line.", md.read_text(encoding="utf-8"))
            write(md, md.read_text(encoding="utf-8") + "# After both\n")
            self.ok("uninstall")
        # The blank line install put before the block stays, because the block no longer ends
        # the file.
        self.assertEqual(md.read_text(encoding="utf-8"),
                         "# Between installs\n" + NOTES + "\n# After both\n")
        md.unlink()
        start.pop(".claude/CLAUDE.md")
        self.assertTreeEqual(start, snapshot(self.home))

    def test_existing_workflow_is_backed_up_and_restored(self):
        root = copy_checkout(self.tmp / "checkout")
        write(root / "workflows" / "gk-review.js", "export const meta = {name: 'gk-review'};\n")
        write(root / "workflows" / "helper.js", "// not deployed\n")
        mine = write(self.cdir / "workflows" / "gk-review.js", "// my own review\n")
        write(self.cdir / "workflows" / "other.js", "// mine too\n")
        start = snapshot(self.home)
        with mock.patch.object(paths, "harness_root", lambda: root):
            self.ok("install")
            self.assertEqual(mine.read_bytes(), (root / "workflows" / "gk-review.js").read_bytes())
            self.assertFalse((self.cdir / "workflows" / "helper.js").exists())
            backup = Path(self.journal()["installs"][-1]["backup"])
            self.assertEqual((backup / "workflows" / "gk-review.js").read_text(encoding="utf-8"),
                             "// my own review\n")
            self.ok("uninstall")
        self.assertTreeEqual(start, snapshot(self.home))


class LinkTests(InstallBase):
    """Targets that are symlinks, as a dotfile manager (stow, home-manager) makes them, and
    targets that cannot be written, such as a link into a read-only store."""

    def test_symlinked_targets_are_edited_where_they_point(self):
        dots = self.tmp / "dotfiles"
        real = {"rc": write(dots / "bashrc", "# ~/.bashrc\nexport EDITOR=vi\n"),
                "settings": write(dots / "settings.json", sm.dumps({"model": "opus"})),
                "md": write(dots / "CLAUDE.md", NOTES)}
        links = {"rc": self.link(self.rc, Path("..") / "dotfiles" / "bashrc"),  # relative, as stow
                 "settings": self.link(self.cdir / "settings.json", real["settings"]),
                 "md": self.link(self.cdir / "CLAUDE.md", real["md"])}
        originals = {k: p.read_bytes() for k, p in real.items()}
        start = snapshot(self.home)
        for edited in (False, True):
            with self.subTest(edited=edited):
                self.ok("install")
                for k, link in links.items():
                    self.assertTrue(os.path.islink(str(link)), link)
                    self.assertEqual(os.path.realpath(str(link)), os.path.realpath(str(real[k])))
                self.assertTrue(shell.has_block(real["rc"].read_text(encoding="utf-8")))
                self.assertIs(json.loads(real["settings"].read_text(encoding="utf-8"))
                              ["ultracode"], True)
                self.assertIn(claude_md.END_MARKER, real["md"].read_text(encoding="utf-8"))
                if edited:  # so uninstall takes the surgical path through each link
                    for k in ("rc", "md"):
                        write(real[k], "# edited\n" + real[k].read_text(encoding="utf-8"))
                    write(real["settings"], json.dumps(json.loads(
                        real["settings"].read_text(encoding="utf-8")), indent=4))
                self.ok("uninstall")
                for k, link in links.items():
                    self.assertTrue(os.path.islink(str(link)), link)
                    added = b"# edited\n" if edited and k != "settings" else b""
                    self.assertEqual(real[k].read_bytes(), added + originals[k], k)
                for k, p in real.items():
                    p.write_bytes(originals[k])
                self.assertTreeEqual(start, snapshot(self.home))

    def test_dangling_symlinked_rc_is_created_where_it_points(self):
        target = self.tmp / "dotfiles" / "shell" / "bashrc"
        self.link(self.rc, target)
        self.ok("install")
        self.assertTrue(os.path.islink(str(self.rc)))
        self.assertTrue(shell.has_block(target.read_text(encoding="utf-8")))
        self.ok("uninstall")
        self.assertTrue(os.path.islink(str(self.rc)))
        link = os.readlink(str(self.rc))
        link = link[4:] if link.startswith("\\\\?\\") else link  # Windows: \\?\ prefix
        self.assertEqual(os.path.normcase(link), os.path.normcase(str(target)))
        self.assertFalse(os.path.lexists(str(target.parent)))  # the folders it made, too

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                     "folder write permission is POSIX, and root writes anyway")
    def test_targets_that_cannot_be_written(self):
        store = self.tmp / "store"
        for name, text in (("bashrc", "# managed\n"), ("settings.json", '{"model": "opus"}\n'),
                           ("CLAUDE.md", NOTES)):
            write(store / name, text)
        os.chmod(str(store), 0o555)
        self.addCleanup(os.chmod, str(store), 0o755)
        real_store = Path(os.path.realpath(str(store))).as_posix()

        with self.subTest("rc file: skipped with a notice, every time"):
            self.link(self.rc, store / "bashrc")
            notice = ("gk-harness: %s (a link to %s/bashrc) cannot be written: the folder %s is "
                      "not writable; the gk shortcuts were not set up\n"
                      % (paths.posix(self.rc), real_store, real_store))
            out, err = self.ok("install")
            self.assertIn(notice, out)
            self.assertEqual(err, "")
            self.assertTrue((self.cdir / "settings.json").is_file())
            out, err = self.ok("install")
            self.assertIn(notice, out)
            self.assertIn("nothing to change", out)
            self.assertEqual((store / "bashrc").read_text(encoding="utf-8"), "# managed\n")
            self.assertEqual([a for inst in self.journal()["installs"] for a in inst["actions"]
                              if a["type"] == "shell_rc"], [])
            self.ok("uninstall")

        for name in ("settings.json", "CLAUDE.md"):
            with self.subTest(name + ": the install stops"):
                self.fresh_home()
                link = self.link(self.cdir / name, store / name)
                start = snapshot(self.home, mtimes=True, skip=())
                code, out, err = self.gk("install")
                self.assertEqual(code, 2, out + err)
                self.assertEqual(err, "gk-harness: %s (a link to %s/%s) cannot be written: the "
                                      "folder %s is not writable; nothing was changed.\n"
                                 % (paths.posix(link), real_store, name, real_store))
                self.assertEqual(snapshot(self.home, mtimes=True, skip=()), start)

        with self.subTest("a folder of copied files: the install stops"):
            self.fresh_home()
            agents = self.cdir / "agents"
            agents.mkdir()
            os.chmod(str(agents), 0o555)
            self.addCleanup(os.chmod, str(agents), 0o755)
            start = snapshot(self.home, mtimes=True, skip=())
            code, out, err = self.gk("install")
            self.assertEqual(code, 2, out + err)
            self.assertIn("cannot be written: the folder %s is not writable; nothing was changed."
                          % paths.posix(agents), err)
            self.assertEqual(snapshot(self.home, mtimes=True, skip=()), start)


class InterruptionTests(InstallBase):
    """An install or uninstall stopped at any point (a failed write, or a kill that runs no
    cleanup) leaves a journal from which uninstall returns the home to where it started, and
    from which install finishes the job."""

    def installed_tree(self) -> dict:
        """The home as a snapshot, without gk-harness.json (it holds the install's time)."""
        tree = snapshot(self.home)
        tree.pop(".claude/gk-harness.json", None)
        return tree

    def test_install_write_fails_part_way(self):
        # Every write (a backup, the journal, a target) failing in turn. A failure, or a kill,
        # just before the journal is saved for a step leaves the same state, so this also
        # stands for a kill at that point.
        self.make_box()
        start = snapshot(self.home)
        with watch_writes() as log:
            self.ok("install", cli.RETIRE)
        backups = paths.posix(self.cdir / "backups") + "/"
        for k in range(1, len(log["calls"]) + 1):
            with self.subTest(failing_write=k, path=log["calls"][k - 1]):
                self.fresh_home()
                self.make_box()
                with watch_writes(k, OSError(5, "simulated write failure")) as failed:
                    code, out, err = self.gk("install", cli.RETIRE)
                self.assertEqual(code, 1, out + err)
                self.assertIn("simulated write failure; install stopped part-way", err)
                # The journal holds exactly the writes and moves made before the failure.
                made = [p for p in failed["written"] if not p.startswith(backups)]
                journalled = [a for inst in self.journal()["installs"] for a in inst["actions"]
                              if a["type"] != "folder"]
                self.assertEqual([a["path"] for a in journalled if "path" in a], made)
                moved = {a["src"] for a in journalled if a["type"] == "legacy_move"}
                for rel in legacy.RETIRE_PATHS:
                    self.assertEqual(paths.posix(self.cdir / rel) in moved,
                                     not (self.cdir / rel).exists(), rel)
                self.ok("uninstall")
                self.assertTreeEqual(start, snapshot(self.home))

    def test_install_killed_then_undone_or_finished(self):
        # Killed once the journal names a write (or a move) not yet made: uninstall passes over
        # that action, and install run again finishes the job.
        self.make_box()
        start = snapshot(self.home)
        with watch_writes() as log:
            self.ok("install", cli.RETIRE)
        installed = self.installed_tree()
        saves = log["calls"].count(paths.posix(self.journal_path()))
        for k in range(1, saves + 1):
            for then in ("uninstall", "install"):
                with self.subTest(killed_after_save=k, then=then):
                    self.fresh_home()
                    self.make_box()
                    with watch_writes(k, Killed(), after=True, only=self.journal_path()):
                        with self.assertRaises(Killed):
                            self.gk("install", cli.RETIRE)
                    if then == "install":
                        self.ok("install", cli.RETIRE)
                        self.assertTreeEqual(installed, self.installed_tree())
                    out, err = self.ok("uninstall")
                    self.assertEqual(err, "")
                    self.assertTreeEqual(start, snapshot(self.home))

    def test_uninstall_killed_then_run_again(self):
        # Killed after each reversal, before the journal records it: the next uninstall replays
        # that action and must find it already done.
        for edited in (False, True):
            self.fresh_home()
            before = self.make_box()
            start = snapshot(self.home)
            self.ok("install")
            self.ok("install", cli.RETIRE)
            if edited:
                # Each owned file changed since install, so each is reversed surgically.
                settings = self.settings()
                settings["permissions"]["allow"].append("Bash(ls:*)")
                write(self.cdir / "settings.json", json.dumps(settings, indent=4))
                md = self.cdir / "CLAUDE.md"
                write(md, md.read_text(encoding="utf-8") + "\n## Mine\n")
                write(self.rc, self.rc.read_text(encoding="utf-8") + "# mine\n")
            installed = self.tmp / ("installed-%s" % edited)
            shutil.copytree(str(self.home), str(installed), symlinks=True)
            with watch_writes() as log:
                self.ok("uninstall")
            final = snapshot(self.home)
            if edited:
                expected = json.loads(json.dumps(before))
                expected["permissions"]["allow"].append("Bash(ls:*)")
                self.assertEqual(self.settings(), expected)
            else:
                self.assertTreeEqual(start, final)
            saves = log["calls"].count(paths.posix(self.journal_path()))
            for k in range(1, saves + 1):
                with self.subTest(edited=edited, killed_before_save=k):
                    shutil.rmtree(str(self.home))
                    shutil.copytree(str(installed), str(self.home), symlinks=True)
                    with watch_writes(k, Killed(), only=self.journal_path()):
                        with self.assertRaises(Killed):
                            self.gk("uninstall")
                    out, err = self.ok("uninstall")
                    self.assertEqual(err, "")
                    self.assertTreeEqual(final, snapshot(self.home))

    def test_folders_made_meanwhile_are_journalled(self):
        # The folders a write will make are journalled before it. Should another program make or
        # remove one meanwhile, the journal is corrected to what the write did make.
        start = snapshot(self.home)
        with mock.patch.object(cli, "_missing_folders", lambda path: []):
            self.ok("install")
        folders = [a["path"] for a in self.journal()["installs"][0]["actions"]
                   if a["type"] == "folder"]
        self.assertIn(paths.posix(self.cdir / "hooks" / "gk-harness"), folders)
        self.ok("uninstall")
        self.assertTreeEqual(start, snapshot(self.home))


class CommandTests(InstallBase):
    home_name = "Carl Selby"

    def payload(self, event: str, matcher: str | None) -> dict:
        base = {"session_id": "s1", "hook_event_name": event, "cwd": str(self.tmp),
                "transcript_path": "", "permission_mode": "default"}
        if event == "SessionStart":
            base.update(source="resume", model="claude-fable-5-1", effort={"level": "high"})
        elif event == "PostModelSwitch":
            base.update(to_model="claude-opus-5-5", from_model="claude-fable-5-1")
        elif event == "UserPromptSubmit":
            base.update(prompt="hello")
        elif event == "PreToolUse":
            base.update(tool_name="Workflow", tool_input={"name": "gk-review", "args": {}})
        elif event == "PostToolUse" and matcher and matcher.startswith("Edit"):
            base.update(tool_name="Edit", tool_input={"file_path": str(self.tmp / "x.txt")},
                        tool_response={})
        elif event == "PostToolUse":
            base.update(tool_name="Artifact", tool_input={}, tool_response={})
        elif event == "SessionEnd":
            base.update(reason="exit")
        return base

    def test_registered_commands_execute_with_space_in_home(self):
        # The Python lives under the spaced home too, as it often does on Windows
        # (C:\Users\Carl Selby\AppData\Local\Programs\Python), so both paths in every command
        # hold a space: install runs with a venv's Python made there.
        venv = self.home / "venv"
        made = subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(made.returncode, 0, made.stdout + made.stderr)
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        self.assertTrue(python.is_file(), python)
        with mock.patch.object(paths, "python_exe", lambda: str(python)):
            self.ok("install")
        # The once-a-day update check has just run, so no hook here goes to the network.
        write(self.cdir / "state" / "gk-harness-update.json", json.dumps(
            {"checked": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "available": False}))
        entries = sm.owned_entries(self.settings())
        self.assertEqual(len(entries), sum(len(s) for _, _, s in EXPECTED_GROUPS))
        bash, why = find_bash()
        if bash is None:
            with self.subTest(shell="bash"):
                self.skipTest(why)  # recorded, so the run shows the bash leg did not run
        env = dict(os.environ, GK_NO_LAUNCH_ARGS="1")  # HOME, USERPROFILE and GK_CLAUDE_DIR point into the temp home
        for key in ("PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX"):
            env.pop(key, None)  # Python caches the hooks' imports, as under Claude Code
        seen_card = 0
        for event, matcher, command in entries:
            self.assertTrue(command.startswith('"%s" "' % python.as_posix()), command)
            self.assertIn(" Selby/venv/", command)
            self.assertIn(" Selby/.claude/hooks/gk-harness/", command)
            data = json.dumps(self.payload(event, matcher)).encode("utf-8")
            runs = [("platform shell", dict(args=command, shell=True))]
            if bash:
                runs.append(("bash", dict(args=[bash, "-c", command])))
            for how, kw in runs:
                with self.subTest(event=event, command=command, shell=how):
                    proc = subprocess.run(input=data, capture_output=True, timeout=60, env=env,
                                          **kw)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    self.assertNotIn(b"can't open file", proc.stderr)
                    if event == "SessionStart" and command.endswith('gk-seat.py"'):
                        reply = json.loads(proc.stdout.decode("utf-8"))
                        card = reply["hookSpecificOutput"]["additionalContext"]
                        self.assertIn("gk-harness seat: claude-fable-5-1 at high", card)
                        seen_card += 1
        self.assertEqual(seen_card, 2 if bash else 1)
        # The hooks have run, so Python left a __pycache__ beside them; uninstall still removes
        # the folder it created.
        self.assertTrue((self.cdir / "hooks" / "gk-harness" / "__pycache__").is_dir())
        self.ok("uninstall")
        self.assertFalse((self.cdir / "hooks").exists())


class StatusTests(InstallBase):
    def test_status_before_install(self):
        out, err = self.ok("status")
        self.assertIn("gk-harness %s" % VERSION, out)
        self.assertIn("not installed", out)
        self.assertEqual(os.listdir(str(self.cdir)), [])

    def test_status_reports_drift(self):
        self.ok("install")
        out, err = self.ok("status")
        self.assertEqual(err, "")
        self.assertIn(VERSION, out)
        self.assertIn(Path(ROOT).resolve().as_posix(), out)
        self.assertIn(Path(paths.python_exe()).as_posix(), out)
        for _, _, command in sm.owned_entries(self.settings()):
            self.assertIn(command, out)
        self.assertIn("drift: none", out)

        seat = self.cdir / "hooks" / "gk-harness" / "gk-seat.py"
        write(seat, seat.read_text(encoding="utf-8") + "# local edit\n")
        agent = self.cdir / "agents" / "worker-opus.md"
        agent.unlink()
        md = self.cdir / "CLAUDE.md"
        write(md, md.read_text(encoding="utf-8").replace("(read first)", "(read me)"))
        out, err = self.ok("status")
        self.assertNotIn("drift: none", out)
        drift = out[out.index("drift:"):]
        self.assertIn("changed: %s\n" % paths.posix(seat), drift)
        self.assertIn("missing: %s\n" % paths.posix(agent), drift)
        self.assertIn("changed: %s (policy block)\n" % paths.posix(md), drift)
        self.assertNotIn("gk-peers.py", drift)
        self.assertNotIn("settings.json", drift)

    def test_status_reports_missing_hook_entries(self):
        self.ok("install")
        path = self.cdir / "settings.json"
        full = self.settings()
        entries = expected_entries(runner_python(), self.cdir)
        no_hooks = {k: v for k, v in full.items() if k != "hooks"}
        no_stop = json.loads(json.dumps(full))
        del no_stop["hooks"]["Stop"]
        for name, text, gone in (
                ("absent", None, entries),
                ("unreadable", '{"hooks": {,}', entries),
                ("stripped", json.dumps(no_hooks, indent=2), entries),
                ("one group removed", json.dumps(no_stop, indent=2),
                 [e for e in entries if e[0] == "Stop"])):
            with self.subTest(name):
                if text is None:
                    path.unlink()
                else:
                    write(path, text)
                out, err = self.ok("status")
                self.assertNotIn("drift: none", out)
                drift = out[out.index("drift:"):]
                for event, matcher, command, _ in entries:
                    line = "missing: %s (hook entry %s%s: %s)\n" % (
                        paths.posix(path), event, " [%s]" % matcher if matcher else "", command)
                    if (event, matcher, command, 10) in gone:
                        self.assertIn(line, drift)
                    else:
                        self.assertNotIn(line, drift)
                self.assertEqual(drift.count("(hook entry "), len(gone))
                write(path, sm.dumps(full))
                self.assertIn("drift: none", self.ok("status")[0])


class RcChoiceTests(InstallBase):
    patch_rc = False

    def test_rc_choice_per_os(self):
        start = snapshot(self.home)
        root = Path(ROOT).resolve().as_posix()
        for platform, login_shell, name in (("darwin", "/bin/bash", ".zshrc"),
                                            ("linux", "/usr/bin/zsh", ".zshrc"),
                                            ("linux", "/bin/bash", ".bashrc"),
                                            ("linux", None, ".bashrc")):
            with self.subTest(platform=platform, shell=login_shell):
                with mock.patch.object(sys, "platform", platform), \
                        mock.patch.dict(os.environ):
                    os.environ.pop("SHELL", None)
                    if login_shell:
                        os.environ["SHELL"] = login_shell
                    out, err = self.ok("install")
                rc_files = sorted(p.name for p in self.home.iterdir() if p.is_file())
                self.assertEqual(rc_files, [name])
                text = (self.home / name).read_text(encoding="utf-8")
                self.assertIn('. "%s/shell/aliases.sh"\n' % root, text)
                self.assertNotIn(EXECUTION_POLICY, out)
                self.ok("uninstall")
                self.assertTreeEqual(start, snapshot(self.home))

        with self.subTest(platform="win32", profile="none"):
            with mock.patch.object(sys, "platform", "win32"), \
                    mock.patch.object(paths, "run_capture", lambda argv, timeout: None):
                out, err = self.ok("install")
            self.assertIn("PowerShell profile", out)
            self.assertNotIn(EXECUTION_POLICY, out)
            self.assertEqual([p.name for p in self.home.iterdir()], [".claude"])
            self.ok("uninstall")
            self.assertTreeEqual(start, snapshot(self.home))

        with self.subTest(platform="win32", profile="new"):
            (self.home / "Documents").mkdir()
            begin = snapshot(self.home)
            profile = self.home / "Documents" / "PowerShell" / "Microsoft.PowerShell_profile.ps1"
            with mock.patch.object(sys, "platform", "win32"), \
                    mock.patch.object(paths, "rc_file", lambda *a, **k: profile):
                out, err = self.ok("install")
                data = profile.read_bytes()
                self.assertTrue(data.startswith(codecs.BOM_UTF8))
                self.assertIn(('. "%s/shell/aliases.ps1"' % root).encode("utf-8"), data)
                self.assertEqual(out.count(EXECUTION_POLICY), 1)
                again, _ = self.ok("install")
                self.assertNotIn(EXECUTION_POLICY, again)
                self.ok("uninstall")
            self.assertTreeEqual(begin, snapshot(self.home))

        # New-Item $PROFILE -Force leaves an empty profile: it gets the mark a new one gets, and
        # uninstall returns it to zero bytes, whether or not it was edited inside the block. A
        # UTF-16 profile holding only its mark keeps that mark and gets no second one.
        profile = self.home / "Documents" / "WindowsPowerShell" / "Microsoft.PowerShell_profile.ps1"
        block = '# >>> gk-harness >>>\n. "%s/shell/aliases.ps1"\n# <<< gk-harness <<<\n' % root
        for original, edited, encoding, mark in (
                (b"", False, "utf-8", codecs.BOM_UTF8), (b"", True, "utf-8", codecs.BOM_UTF8),
                (codecs.BOM_UTF16_LE, False, "utf-16-le", codecs.BOM_UTF16_LE)):
            write(profile, "")
            profile.write_bytes(original)
            begin = snapshot(self.home)
            with self.subTest(platform="win32", profile=original, edited=edited), \
                    mock.patch.object(sys, "platform", "win32"), \
                    mock.patch.object(paths, "rc_file", lambda *a, **k: profile):
                self.ok("install")
                data = profile.read_bytes()
                self.assertEqual(data, mark + block.encode(encoding))
                self.assertEqual([a.get("mark_added") for a in self.journal()["installs"][-1][
                    "actions"] if a["type"] == "shell_rc"], [True if original == b"" else None])
                if edited:
                    write(profile, insert_in_block(data.decode("utf-8"), "Set-Alias ll ls\n"))
                self.ok("uninstall")
                self.assertEqual(profile.read_bytes(), original)
                self.assertTreeEqual(begin, snapshot(self.home))


@unittest.skipIf(shutil.which("git") is None, "git is not on PATH")
class UpdateTests(unittest.TestCase):
    """update against a local bare origin: never the network."""

    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.tmp = Path(tmp)
        self.home = self.tmp / "home"
        self.cdir = self.home / ".claude"
        self.cdir.mkdir(parents=True)
        gitconfig = self.tmp / "gitconfig"
        gitconfig.write_text("")
        env = dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home),
                   GK_CLAUDE_DIR=str(self.cdir), SHELL="/bin/bash", GIT_CONFIG_NOSYSTEM="1",
                   GIT_CONFIG_GLOBAL=str(gitconfig), GIT_AUTHOR_NAME="t",
                   GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@example.com")
        for key in ("GK_PROFILE", "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
            env.pop(key, None)
        if os.name == "nt":
            # No PowerShell on PATH, so install finds no $PROFILE and leaves the runner's alone.
            env["PATH"] = os.pathsep.join([os.path.dirname(shutil.which("git")),
                                           os.path.dirname(sys.executable)])
        self.env = env
        self.seed = copy_checkout(self.tmp / "seed")
        self.git(self.seed, "init", "-q")
        self.git(self.seed, "add", "-A")
        self.git(self.seed, "commit", "-q", "-m", "seed")
        self.origin = self.tmp / "origin.git"
        self.git(self.tmp, "clone", "-q", "--bare", str(self.seed), str(self.origin))
        self.clone = self.home / ".gk-harness"
        self.git(self.tmp, "clone", "-q", str(self.origin), str(self.clone))

    def git(self, cwd: Path, *args: str) -> str:
        proc = subprocess.run(["git", "-C", str(cwd)] + list(args), capture_output=True,
                              text=True, stdin=subprocess.DEVNULL, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.strip()

    def push_policy_line(self, line: str) -> None:
        policy = self.seed / "policy.md"
        policy.write_bytes(policy.read_bytes() + ("\n%s\n" % line).encode("utf-8"))
        self.git(self.seed, "commit", "-q", "-am", line)
        self.git(self.seed, "push", "-q", str(self.origin), "HEAD")

    def update(self) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(self.clone / "install.py"), "update"],
                              capture_output=True, text=True, env=self.env,
                              stdin=subprocess.DEVNULL, timeout=120)

    def test_update_pulls_then_installs(self):
        self.push_policy_line("Update probe line.")
        stamp = self.cdir / "state" / "gk-harness-update.json"
        stamp.parent.mkdir(parents=True)
        stamp.write_text(json.dumps({"checked": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                              time.gmtime()),
                                     "available": True}), encoding="utf-8")
        proc = self.update()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertFalse(os.path.lexists(str(stamp)))  # the next check starts afresh
        self.assertEqual(self.git(self.clone, "rev-parse", "HEAD"),
                         self.git(self.seed, "rev-parse", "HEAD"))
        md = (self.cdir / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("Update probe line.\n%s" % claude_md.END_MARKER, md)
        cfg = json.loads((self.cdir / "gk-harness.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["root"], self.clone.resolve().as_posix())

    def test_update_stops_when_the_pull_cannot_fast_forward(self):
        self.git(self.clone, "commit", "-q", "--allow-empty", "-m", "local")
        self.push_policy_line("Upstream line.")
        proc = self.update()
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("git pull --ff-only", proc.stderr)
        self.assertTrue(proc.stderr.endswith("; nothing was changed.\n"), proc.stderr)
        self.assertEqual(os.listdir(str(self.cdir)), [])


if __name__ == "__main__":
    unittest.main()
