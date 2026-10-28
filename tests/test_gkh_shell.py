from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from gkh import claude_md, manifest, shell  # noqa: E402

ALIASES_SH = Path(ROOT) / "shell" / "aliases.sh"
ALIASES_PS1 = Path(ROOT) / "shell" / "aliases.ps1"
BASHRC = (
    "# ~/.bashrc\n"
    "export PATH=$HOME/bin:$PATH\n"
    "alias gk='claude'\n"
    "alias gk-opus='claude --settings ~/.claude/profiles/opus-seat.json'\n"
    "alias gk-sonnet='claude --settings ~/.claude/profiles/sonnet-seat.json'\n"
    "alias ll='ls -l'\n"
)
SEATS = (("gk", "fable"), ("gk-opus", "opus"), ("gk-sonnet", "sonnet"))


class BlockTests(unittest.TestCase):
    def test_markers(self):
        self.assertEqual(shell.BEGIN_MARKER, "# >>> gk-harness >>>")
        self.assertEqual(shell.END_MARKER, "# <<< gk-harness <<<")

    def test_posix_block(self):
        self.assertEqual(shell.render_block("/home/carl selby/.gk-harness", windows=False),
                         '# >>> gk-harness >>>\n. "/home/carl selby/.gk-harness/shell/aliases.sh"\n'
                         "# <<< gk-harness <<<\n")

    def test_windows_block(self):
        root = PureWindowsPath(r"C:\Users\Carl Selby\.gk-harness")
        self.assertEqual(shell.render_block(root, windows=True),
                         '# >>> gk-harness >>>\n. "C:/Users/Carl Selby/.gk-harness/shell/aliases.ps1"\n'
                         "# <<< gk-harness <<<\n")

    def test_apply_modes(self):
        b = shell.render_block("/r", windows=False)
        self.assertEqual(shell.apply(None, b), (b, "created"))
        self.assertEqual(shell.apply("x\n", b), ("x\n\n" + b, "appended"))
        installed = "x\n\n" + b
        self.assertEqual(shell.apply(installed, b), (installed, "unchanged"))
        moved = shell.render_block("/elsewhere", windows=False)
        self.assertEqual(shell.apply(installed + "y\n", moved), ("x\n\n" + moved + "y\n", "updated"))

    def test_strip(self):
        b = shell.render_block("/r", windows=False)
        self.assertEqual(shell.strip(shell.apply(BASHRC, b)[0]), BASHRC)
        self.assertEqual(shell.strip(b), "")
        self.assertEqual(shell.strip(BASHRC), BASHRC)
        self.assertTrue(shell.has_block(b))
        self.assertFalse(shell.has_block(BASHRC))

    def test_crlf_profile(self):
        b = shell.render_block("C:/r", windows=True)
        profile = "Set-PSReadLineOption -EditMode Emacs\r\n"
        new, _ = shell.apply(profile, b)
        self.assertEqual(new, profile + "\r\n" + b.replace("\n", "\r\n"))
        self.assertEqual(shell.strip(new), profile)

    def test_profile_resaved_with_a_byte_order_mark(self):
        # A created PowerShell profile that a Windows editor then saved with a byte-order mark:
        # the block on line 1 is still found, and the mark is kept.
        b = shell.render_block("C:/r", windows=True)
        created, mode = shell.apply(None, b)
        self.assertEqual(mode, "created")
        text = "\ufeff" + created.replace("\n", "\r\n") + "Set-Alias ll Get-ChildItem\r\n"
        self.assertTrue(shell.has_block(text))
        self.assertEqual(shell.apply(text, b), (text, "unchanged"))
        moved = shell.render_block("D:/r", windows=True)
        self.assertEqual(shell.apply(text, moved),
                         ("\ufeff" + moved.replace("\n", "\r\n") + "Set-Alias ll Get-ChildItem\r\n",
                          "updated"))
        self.assertEqual(shell.strip(text), "\ufeffSet-Alias ll Get-ChildItem\r\n")
        for start in ("\ufeff", "\ufeff" + BASHRC):
            new, mode = shell.apply(start, b)
            self.assertEqual(mode, "appended")
            self.assertEqual(shell.strip(new), start)

    def test_unbalanced_markers_raise(self):
        for text in ("# >>> gk-harness >>>\nx\n", "x\n# <<< gk-harness <<<\n"):
            with self.assertRaises(claude_md.BlockError) as ctx:
                shell.apply(text, shell.render_block("/r", windows=False), label="/home/c/.bashrc")
            self.assertIn("/home/c/.bashrc", str(ctx.exception))


class LegacyAliasTests(unittest.TestCase):
    def test_todays_three_lines_are_removed_and_recorded(self):
        new, removed = shell.strip_legacy_aliases(BASHRC)
        self.assertEqual(new, "# ~/.bashrc\nexport PATH=$HOME/bin:$PATH\nalias ll='ls -l'\n")
        self.assertEqual(removed, [
            {"line": 3, "text": "alias gk='claude'"},
            {"line": 4, "text": "alias gk-opus='claude --settings ~/.claude/profiles/opus-seat.json'"},
            {"line": 5, "text": "alias gk-sonnet='claude --settings ~/.claude/profiles/sonnet-seat.json'"},
        ])

    def test_crlf_and_unterminated_last_line(self):
        text = "a\r\nalias gk='claude'\r\nb\r\nalias gk-opus='x'"
        new, removed = shell.strip_legacy_aliases(text)
        self.assertEqual(new, "a\r\nb\r\n")
        self.assertEqual([r["text"] for r in removed], ["alias gk='claude'", "alias gk-opus='x'"])

    def test_alias_on_line_one_after_a_byte_order_mark(self):
        new, removed = shell.strip_legacy_aliases("﻿" + BASHRC[len("# ~/.bashrc\n"):])
        self.assertEqual(new, "﻿export PATH=$HOME/bin:$PATH\nalias ll='ls -l'\n")
        self.assertEqual(removed[0], {"line": 2, "text": "alias gk='claude'"})
        new, removed = shell.strip_legacy_aliases("﻿alias gk='claude'\r\nx\r\n")
        self.assertEqual((new, removed), ("﻿x\r\n", [{"line": 1, "text": "alias gk='claude'"}]))
        self.assertEqual(shell.strip_legacy_aliases("﻿"), ("﻿", []))

    def test_nothing_to_remove(self):
        self.assertEqual(shell.strip_legacy_aliases("alias ll='ls'\n"), ("alias ll='ls'\n", []))
        self.assertEqual(shell.strip_legacy_aliases(""), ("", []))


class AliasFileTests(unittest.TestCase):
    def test_sh_defines_exactly_the_three_aliases(self):
        lines = [l for l in ALIASES_SH.read_text(encoding="utf-8").splitlines()
                 if l.strip() and not l.lstrip().startswith("#")]
        self.assertEqual(lines, ["alias gk='claude --model fable'",
                                 "alias gk-opus='claude --model opus'",
                                 "alias gk-sonnet='claude --model sonnet'"])

    def test_ps1_defines_three_functions_passing_args(self):
        text = ALIASES_PS1.read_text(encoding="utf-8")
        funcs = re.findall(r"^function (\S+) \{ claude --model (\S+) @args \}\s*$", text, re.M)
        self.assertEqual(funcs, [("gk", "fable"), ("gk-opus", "opus"), ("gk-sonnet", "sonnet")])

    def test_rc_block_sources_the_aliases_in_bash_with_a_space_in_the_path(self):
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("no bash on PATH")
        if os.name == "nt" and "system32" in bash.lower():
            self.skipTest("the WSL launcher is not the bash Claude Code uses")
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        root = tmp / "Carl Selby" / ".gk-harness"
        (root / "shell").mkdir(parents=True)
        shutil.copyfile(str(ALIASES_SH), str(root / "shell" / "aliases.sh"))
        rc = tmp / "Carl Selby" / ".bashrc"
        rc.write_bytes(shell.apply(BASHRC, shell.render_block(root, windows=False))[0].encode())
        script = ('shopt -s expand_aliases\nclaude() { echo "ARGS:$*"; }\n. "$1"\n'
                  'gk -p hi\ngk-opus -p hi\ngk-sonnet -p hi\n')
        proc = subprocess.run([bash, "-c", script, "bash", Path(rc).as_posix()],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.splitlines(),
                         ["ARGS:--model %s -p hi" % m for _, m in SEATS])

    def test_rc_block_dot_sources_the_functions_in_powershell(self):
        exe = shutil.which("pwsh") or (shutil.which("powershell") if os.name == "nt" else None)
        if exe is None:
            self.skipTest("no PowerShell on PATH")
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        root = tmp / "Carl Selby" / ".gk-harness"
        (root / "shell").mkdir(parents=True)
        shutil.copyfile(str(ALIASES_PS1), str(root / "shell" / "aliases.ps1"))
        profile = tmp / "Carl Selby" / "profile.ps1"
        profile.write_bytes(shell.render_block(root, windows=True).encode())
        command = ("function claude { 'ARGS:' + ($args -join ' ') }; . '%s'; "
                   "gk -p hi; gk-opus -p hi; gk-sonnet -p hi" % profile.as_posix())
        proc = subprocess.run([exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.splitlines(),
                         ["ARGS:--model %s -p hi" % m for _, m in SEATS])

    def test_utf16_profile_still_loads_in_powershell(self):
        exe = shutil.which("pwsh") or (shutil.which("powershell") if os.name == "nt" else None)
        if exe is None:
            self.skipTest("no PowerShell on PATH")
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        root = tmp / "Carl Selby" / ".gk-harness"
        (root / "shell").mkdir(parents=True)
        shutil.copyfile(str(ALIASES_PS1), str(root / "shell" / "aliases.ps1"))
        profile = tmp / "Carl Selby" / "profile.ps1"
        profile.write_bytes(b"\xff\xfe" + "$gkBefore = 'kept'\r\n".encode("utf-16-le"))
        text, encoding = manifest.read_text_encoded(profile)
        manifest.write_text_atomic(profile, shell.apply(text, shell.render_block(root, True))[0],
                                   encoding)
        command = ("function claude { 'ARGS:' + ($args -join ' ') }; . '%s'; $gkBefore; "
                   "gk -p hi" % profile.as_posix())
        proc = subprocess.run([exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.splitlines(), ["kept", "ARGS:--model fable -p hi"])


class EncodedProfileTests(unittest.TestCase):
    """Windows PowerShell 5.1's >, >> and Out-File write the profile as UTF-16LE with a
    byte-order mark. The block goes in as UTF-16LE too, and comes out leaving the file as it was."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_utf16le_profile_round_trip(self):
        profile = self.tmp / "Microsoft.PowerShell_profile.ps1"
        before = "Set-PSReadLineOption -EditMode Emacs\r\nfunction gk { claude }\r\n"
        original = b"\xff\xfe" + before.encode("utf-16-le")
        profile.write_bytes(original)
        b = shell.render_block(PureWindowsPath("C:\\Users\\Zo\u00eb\\.gk-harness"), windows=True)

        text, encoding = manifest.read_text_encoded(profile)
        self.assertEqual((text, encoding), (before, "utf-16-le"))
        new, mode = shell.apply(text, b)
        self.assertEqual(mode, "appended")
        sha = manifest.write_text_atomic(profile, new, encoding)

        data = profile.read_bytes()
        self.assertEqual(sha, manifest.sha256_bytes(data))
        self.assertTrue(data.startswith(b"\xff\xfe"))
        self.assertEqual(data[2:].decode("utf-16-le"), before + "\r\n" + b.replace("\n", "\r\n"))
        self.assertIn('. "C:/Users/Zo\u00eb/.gk-harness/shell/aliases.ps1"', data[2:].decode("utf-16-le"))

        again, encoding = manifest.read_text_encoded(profile)
        self.assertEqual(shell.apply(again, b), (again, "unchanged"))
        manifest.write_text_atomic(profile, shell.strip(again), encoding)
        self.assertEqual(profile.read_bytes(), original)

    def test_utf16le_profile_cannot_be_read_as_utf8(self):
        profile = self.tmp / "profile.ps1"
        profile.write_bytes(b"\xff\xfe" + "x\r\n".encode("utf-16-le"))
        with self.assertRaises(manifest.TextEncodingError):
            manifest.read_text(profile)


if __name__ == "__main__":
    unittest.main()
