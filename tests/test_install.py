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
        return manifest.load(self.cdir / "backups" / "gk-harness" / "manifest.json")

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
        self.assertEqual(err, "")
        first = snapshot(self.home, mtimes=True, skip=())

        hooks = self.cdir / "hooks" / "gk-harness"
        self.assertEqual(sorted(os.listdir(str(hooks))), REPO_HOOKS)
        for name in REPO_HOOKS:
            self.assertEqual((hooks / name).read_bytes(),
                             (Path(ROOT) / "hooks" / name).read_bytes(), name)
        self.assertEqual(sorted(os.listdir(str(self.cdir / "agents"))), REPO_AGENTS)
        self.assertEqual((self.cdir / "CLAUDE.md").read_text(encoding="utf-8"), POLICY_BLOCK)
        settings = self.settings()
        self.assertIs(settings["ultracode"], True)
        self.assertEqual(sm.dumps(settings), sm.dumps(sm.merge({}, paths.python_exe(),
                                                                self.cdir)[0]))
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

    def test_round_trip_legacy_box(self):
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
        start = snapshot(self.home)

        self.ok("install")
        self.assertEqual((c / "CLAUDE.md").read_text(encoding="utf-8"), POLICY_BLOCK)
        after = self.settings()
        self.assertEqual([x for x in commands_of(after) if legacy.is_legacy_command(x)], [])
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
        start = snapshot(self.home)
        md = self.cdir / "CLAUDE.md"
        for name, edit, expected in (
                ("edited inside the block", lambda t: t.replace(">>>\n", ">>>\n# mine\n").replace(
                    "(read first)", "(read me)"), None),
                ("notes added", lambda t: "# notes\n\n" + t, "# notes\n")):
            with self.subTest(name):
                self.ok("install")
                for path in (md, self.rc):
                    write(path, edit(path.read_text(encoding="utf-8")))
                self.ok("uninstall")
                for path in (md, self.rc):
                    if expected is None:
                        self.assertFalse(path.exists(), path)
                    else:
                        self.assertEqual(path.read_text(encoding="utf-8"), expected)
                        path.unlink()
                self.assertTreeEqual(start, snapshot(self.home))

    def test_replaced_old_policy_changed_since(self):
        md = self.cdir / "CLAUDE.md"
        cases = (
            ("notes added after the block", lambda t: t + "\n## Mine\n", "\n## Mine\n"),
            ("edited inside the block", lambda t: t.replace("(read first)", "(read me)"),
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
                write(md, edit(md.read_text(encoding="utf-8")))
                out, err = self.ok("uninstall")
                self.assertEqual(md.read_bytes().decode("utf-8"), expected)
                self.assertEqual(Path(saved).read_text(encoding="utf-8"), OLD_POLICY)
                if expected != OLD_POLICY:
                    self.assertIn(saved, out)

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
        self.ok("install")
        entries = sm.owned_entries(self.settings())
        self.assertEqual(len(entries), sum(len(s) for _, _, s in sm.REGISTRATIONS))
        bash = shutil.which("bash")
        if bash and os.name == "nt" and "system32" in bash.lower():
            bash = None  # the WSL launcher, not the Git Bash that Claude Code uses
        env = dict(os.environ)  # HOME, USERPROFILE and GK_CLAUDE_DIR point into the temp home
        for key in ("PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX"):
            env.pop(key, None)  # Python caches the hooks' imports, as under Claude Code
        seen_card = 0
        for event, matcher, command in entries:
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
        proc = self.update()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
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
