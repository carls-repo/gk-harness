from __future__ import annotations

import codecs
import json
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
# Each shortcut and the arguments it passes before the user's own.
SHORTCUTS = (("gk", "--model opus --effort medium"),
             ("gk-fable", "--model fable --effort medium"),
             ("gk-ultra", "--model opus --effort medium --settings {\"ultracode\":true}"))
SH_FUNCTIONS = [
    'gk() { claude --model opus --effort medium "$@"; }',
    'gk-fable() { claude --model fable --effort medium "$@"; }',
    'gk-ultra() { claude --model opus --effort medium --settings \'{"ultracode":true}\' "$@"; }',
    'gkh() { python3 "${GK_HARNESS_ROOT:?%s}/install.py" "$@"; }' % (
        "gk-harness: open a new shell, or run install.py install from the gk-harness checkout"),
]
UNALIAS = ('for _gk_n in gk gk-opus gk-sonnet gk-fable gk-ultra gkh; do unalias "$_gk_n" '
           "2>/dev/null; done; unset _gk_n")
UNSET_ROOT_MESSAGE = ("gk-harness: open a new shell, or run install.py install from the "
                      "gk-harness checkout")


class BlockTests(unittest.TestCase):
    def test_markers(self):
        self.assertEqual(shell.BEGIN_MARKER, "# >>> gk-harness >>>")
        self.assertEqual(shell.END_MARKER, "# <<< gk-harness <<<")

    def test_posix_block(self):
        self.assertEqual(shell.render_block("/home/carl selby/.gk-harness", windows=False),
                         '# >>> gk-harness >>>\n'
                         'export GK_HARNESS_ROOT="/home/carl selby/.gk-harness"\n'
                         '. "/home/carl selby/.gk-harness/shell/aliases.sh"\n'
                         "# <<< gk-harness <<<\n")

    def test_windows_block(self):
        root = PureWindowsPath(r"C:\Users\Carl Selby\.gk-harness")
        self.assertEqual(shell.render_block(root, windows=True),
                         '# >>> gk-harness >>>\n'
                         '$env:GK_HARNESS_ROOT = "C:/Users/Carl Selby/.gk-harness"\n'
                         '. "C:/Users/Carl Selby/.gk-harness/shell/aliases.ps1"\n'
                         "# <<< gk-harness <<<\n")

    def test_a_root_a_shell_would_expand_or_unquote_is_refused(self):
        for ch in ('"', "$", "`"):
            for windows in (False, True):
                root = "/home/c%sx/.gk-harness" % ch
                with self.subTest(ch=ch, windows=windows):
                    with self.assertRaises(ValueError) as ctx:
                        shell.render_block(root, windows=windows)
                    self.assertIn(root, str(ctx.exception))
                    self.assertIn("contains %s," % ch, str(ctx.exception))

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

    def test_strip_undoes_apply_for_lf_crlf_and_mixed_newlines(self):
        for windows in (False, True):
            b = shell.render_block("/r", windows=windows)
            for start in ("a\nb\n", "a\r\nb\r\n", "a\r\nb\n", "a\nb\r\n", "a\r\nb\n\n",
                          "a\r\nb\n\r\n"):
                with self.subTest(windows=windows, start=start):
                    new, mode = shell.apply(start, b)
                    self.assertEqual(mode, "appended")
                    self.assertEqual(shell.strip(new).encode("utf-8"), start.encode("utf-8"))

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
        new, removed = shell.strip_legacy_aliases("\ufeff" + BASHRC[len("# ~/.bashrc\n"):])
        self.assertEqual(new, "\ufeffexport PATH=$HOME/bin:$PATH\nalias ll='ls -l'\n")
        self.assertEqual(removed[0], {"line": 2, "text": "alias gk='claude'"})
        new, removed = shell.strip_legacy_aliases("\ufeffalias gk='claude'\r\nx\r\n")
        self.assertEqual((new, removed), ("\ufeffx\r\n", [{"line": 1, "text": "alias gk='claude'"}]))
        self.assertEqual(shell.strip_legacy_aliases("\ufeff"), ("\ufeff", []))

    def test_nothing_to_remove(self):
        self.assertEqual(shell.strip_legacy_aliases("alias ll='ls'\n"), ("alias ll='ls'\n", []))
        self.assertEqual(shell.strip_legacy_aliases(""), ("", []))


class AliasFileTests(unittest.TestCase):
    def test_sh_defines_exactly_the_four_functions(self):
        lines = [l for l in ALIASES_SH.read_text(encoding="utf-8").splitlines()
                 if l.strip() and not l.lstrip().startswith("#")]
        self.assertEqual(lines, [UNALIAS] + SH_FUNCTIONS)
        text = ALIASES_SH.read_text(encoding="utf-8")
        self.assertNotIn("gk-opus()", text)
        self.assertNotIn("gk-sonnet()", text)

    def test_ultracode_settings_file(self):
        data = json.loads((Path(ROOT) / "shell" / "gk-ultra.json").read_text(encoding="utf-8"))
        self.assertEqual(data, {"ultracode": True})

    def test_sh_parses(self):
        # The installer writes the block to ~/.bashrc or ~/.zshrc only (paths.rc_file); a POSIX
        # sh such as dash would refuse the hyphenated function names.
        for sh in ("bash", "zsh"):
            exe = shutil.which(sh)
            if exe is None or (os.name == "nt" and "system32" in exe.lower()):
                continue
            with self.subTest(shell=sh):
                proc = subprocess.run([exe, "-n", str(ALIASES_SH)], capture_output=True,
                                      text=True)
                self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_ps1_defines_four_functions_passing_args(self):
        text = ALIASES_PS1.read_text(encoding="utf-8")
        funcs = re.findall(r"^function (\S+) \{", text, re.M)
        self.assertEqual(funcs, ["gk", "gk-fable", "gk-ultra", "gkh"])
        for name in ("gk", "gk-fable", "gk-ultra", "gkh"):
            line = re.search(r"^function %s \{.*\}\s*$" % re.escape(name), text, re.M).group(0)
            self.assertIn("@args", line)
        self.assertNotIn("function gk-opus", text)
        self.assertNotIn("function gk-sonnet", text)
        self.assertIn("$env:GK_HARNESS_ROOT/install.py", text)

    def bash_rc(self, root: Path) -> tuple:
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("no bash on PATH")
        if os.name == "nt" and "system32" in bash.lower():
            self.skipTest("the WSL launcher is not the bash Claude Code uses")
        (root / "shell").mkdir(parents=True)
        shutil.copyfile(str(ALIASES_SH), str(root / "shell" / "aliases.sh"))
        rc = root.parent / ".bashrc"
        rc.write_bytes(shell.apply(BASHRC, shell.render_block(root, windows=False))[0].encode())
        return bash, rc

    def test_rc_block_sources_the_functions_in_bash_with_a_space_in_the_path(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        root = tmp / "Carl Selby" / ".gk-harness"
        bash, rc = self.bash_rc(root)
        # The rc still holds the three old aliases; sourcing the block after them must work.
        (root / "install.py").write_text("import sys\nprint('GKH:' + ' '.join(sys.argv[1:]))\n")
        script = ('shopt -s expand_aliases\nclaude() { echo "ARGS:$*"; }\n. "$1"\n'
                  'gk -p hi\ngk-fable -p hi\ngk-ultra -p hi\ngkh fable status\n'
                  'echo "ROOT:$GK_HARNESS_ROOT"\n'
                  'if type gk-opus >/dev/null 2>&1; then echo "gk-opus still defined"; fi\n'
                  '. "$1"\ngk -p again\n')
        env = dict(os.environ)
        env.pop("GK_HARNESS_ROOT", None)
        proc = subprocess.run([bash, "-c", script, "bash", Path(rc).as_posix()],
                              capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.splitlines(),
                         ["ARGS:%s -p hi" % a for _, a in SHORTCUTS]
                         + ["GKH:fable status", "ROOT:%s" % root.as_posix(),
                            "ARGS:--model opus --effort medium -p again"])

    def test_resourcing_over_the_old_aliases_in_an_interactive_bash(self):
        # A shell that loaded the old file has alias gk=...; re-sourcing the new one would
        # expand it inside "gk() {" and fail to parse without the unalias line.
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        root = tmp / "h"
        bash, _ = self.bash_rc(root)
        script = ("shopt -s expand_aliases\nalias gk='claude --model fable'\n"
                  "alias gk-opus='claude --model opus'\nalias gk-sonnet='claude --model sonnet'\n"
                  'claude() { echo "ARGS:$*"; }\n. "$1"\ngk x\n'
                  "if alias gk-opus >/dev/null 2>&1; then echo stale; fi\n")
        proc = subprocess.run([bash, "-c", script, "bash",
                               (root / "shell" / "aliases.sh").as_posix()],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.splitlines(), ["ARGS:--model opus --effort medium x"])

    def test_gkh_without_the_root_says_why_and_fails_in_bash(self):
        # A shell that sourced aliases.sh without GK_HARNESS_ROOT (an rc edited by hand, or a
        # variable unset later) must not run python3 "/install.py".
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        root = tmp / "h"
        bash, _ = self.bash_rc(root)
        script = ('python3() { echo "RAN:$*"; }\n. "$1"\nunset GK_HARNESS_ROOT\n'
                  "gkh status\necho after\n")
        env = dict(os.environ)
        env.pop("GK_HARNESS_ROOT", None)
        proc = subprocess.run([bash, "-c", script, "bash",
                               (root / "shell" / "aliases.sh").as_posix()],
                              capture_output=True, text=True, env=env)
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("RAN:", proc.stdout)
        self.assertIn(UNSET_ROOT_MESSAGE, proc.stderr)

    def test_unalias_runs_name_by_name(self):
        # One unalias per name: a shell that stops at the first missing name (zsh) still drops
        # the later ones.
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        root = tmp / "h"
        bash, _ = self.bash_rc(root)
        script = ("shopt -s expand_aliases\nalias gkh='echo stale'\n"
                  'claude() { echo "ARGS:$*"; }\n. "$1"\n'
                  "if alias gkh >/dev/null 2>&1; then echo stale-alias; fi\n"
                  'if [ -n "${_gk_n+x}" ]; then echo leaked; fi\ngk-fable y\n')
        proc = subprocess.run([bash, "-c", script, "bash",
                               (root / "shell" / "aliases.sh").as_posix()],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.splitlines(), ["ARGS:--model fable --effort medium y"])

    def test_ps1_gkh_without_the_root_writes_an_error_and_returns(self):
        text = ALIASES_PS1.read_text(encoding="utf-8")
        line = re.search(r"^function gkh \{.*\}\s*$", text, re.M).group(0)
        guard = line.index("if (-not $env:GK_HARNESS_ROOT)")
        self.assertLess(guard, line.index("python "))
        self.assertIn("Write-Error '%s'" % UNSET_ROOT_MESSAGE, line)
        self.assertIn("return", line[guard:line.index("python ")])

    def test_ps1_gkh_without_the_root_in_powershell(self):
        exe = shutil.which("pwsh") or (shutil.which("powershell") if os.name == "nt" else None)
        if exe is None:
            self.skipTest("no PowerShell on PATH")
        command = ("function python { 'RAN:' + ($args -join ' ') }; "
                   "$env:GK_HARNESS_ROOT = ''; . '%s'; gkh status" % ALIASES_PS1.as_posix())
        proc = subprocess.run([exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
                              capture_output=True, text=True, timeout=60)
        self.assertNotIn("RAN:", proc.stdout)
        self.assertIn("open a new shell", proc.stderr)

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
                   "gk -p hi; gk-fable -p hi; gk-ultra -p hi" % profile.as_posix())
        proc = subprocess.run([exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # gk-ultra names a settings file in PowerShell (see aliases.ps1) rather than inline JSON.
        ultra = "--model opus --effort medium --settings %s/shell/gk-ultra.json" % root.as_posix()
        self.assertEqual(proc.stdout.splitlines(),
                         ["ARGS:%s -p hi" % a for a in (SHORTCUTS[0][1], SHORTCUTS[1][1], ultra)])

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
        self.assertEqual(proc.stdout.splitlines(), ["kept", "ARGS:--model opus --effort medium -p hi"])

    def test_created_profile_with_a_non_ascii_root_loads_in_powershell(self):
        # Windows PowerShell 5.1 (powershell.exe) is the reader that needs the mark; pwsh too.
        exes = [e for e in ((shutil.which("powershell") if os.name == "nt" else None),
                            shutil.which("pwsh")) if e]
        if not exes:
            self.skipTest("no PowerShell on PATH")
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        root = tmp / "Zoë" / ".gk-harness"
        (root / "shell").mkdir(parents=True)
        shutil.copyfile(str(ALIASES_PS1), str(root / "shell" / "aliases.ps1"))
        profile = tmp / "profile.ps1"
        text, mode = shell.apply(None, shell.render_block(root, windows=True), powershell=True)
        self.assertEqual(mode, "created")
        manifest.write_text_atomic(profile, text)
        command = ("function claude { 'ARGS:' + ($args -join ' ') }; . '%s'; gk -p hi"
                   % profile.as_posix())
        for exe in exes:
            with self.subTest(exe=exe):
                proc = subprocess.run([exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                                       command], capture_output=True, text=True, timeout=60)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout.splitlines(), ["ARGS:--model opus --effort medium -p hi"])


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

    def test_a_created_profile_is_utf8_with_a_mark(self):
        # Windows PowerShell 5.1 reads a .ps1 without a mark in the ANSI code page, so a new
        # profile starts with the UTF-8 mark and the non-ASCII root comes through.
        profile = self.tmp / "Microsoft.PowerShell_profile.ps1"
        b = shell.render_block(PureWindowsPath("C:\\Users\\Zo\u00eb\\.gk-harness"), windows=True)
        text, mode = shell.apply(None, b, powershell=True)
        self.assertEqual((text, mode), ("\ufeff" + b, "created"))
        manifest.write_text_atomic(profile, text)
        data = profile.read_bytes()
        self.assertEqual(data, codecs.BOM_UTF8 + b.encode("utf-8"))
        again, encoding = manifest.read_text_encoded(profile)
        self.assertEqual(shell.apply(again, b, powershell=True), (again, "unchanged"))
        self.assertEqual(shell.strip(again), "\ufeff")
        # Not a PowerShell profile: a created rc file has no mark.
        posix_block = shell.render_block("/home/zo\u00eb/.gk-harness", windows=False)
        self.assertEqual(shell.apply(None, posix_block), (posix_block, "created"))

    def test_an_existing_profile_keeps_its_encoding_and_mark(self):
        profile = self.tmp / "profile.ps1"
        b = shell.render_block(PureWindowsPath("C:\\Users\\Zo\u00eb\\.gk-harness"), windows=True)
        body = "Set-Alias ll Get-ChildItem\r\n"
        for mark, original in ((b"", body.encode("utf-8")),
                               (codecs.BOM_UTF8, codecs.BOM_UTF8 + body.encode("utf-8")),
                               (codecs.BOM_UTF8, codecs.BOM_UTF8),
                               (codecs.BOM_UTF16_LE,
                                codecs.BOM_UTF16_LE + body.encode("utf-16-le")),
                               (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_LE)):
            with self.subTest(original=original[:4]):
                profile.write_bytes(original)
                text, encoding = manifest.read_text_encoded(profile)
                # A UTF-16 or UTF-32 profile's mark comes with its encoding: powershell is for
                # a profile read as UTF-8 (what the installer passes).
                new, mode = shell.apply(text, b, powershell=encoding == "utf-8")
                self.assertEqual(mode, "appended")
                manifest.write_text_atomic(profile, new, encoding)
                data = profile.read_bytes()
                self.assertTrue(data.startswith(mark))
                self.assertFalse(data[len(mark):].startswith(codecs.BOM_UTF8))
                self.assertEqual(data[len(mark):].decode(encoding), new.lstrip("\ufeff"))
                text, encoding = manifest.read_text_encoded(profile)
                manifest.write_text_atomic(profile, shell.strip(text), encoding)
                self.assertEqual(profile.read_bytes(), original)

    def test_an_empty_profile_gets_a_mark(self):
        # New-Item $PROFILE -Force leaves a zero-byte profile, which Windows PowerShell 5.1 would
        # read in the ANSI code page: it gets the mark a created profile gets.
        profile = self.tmp / "profile.ps1"
        profile.write_bytes(b"")
        b = shell.render_block(PureWindowsPath("C:\\Users\\Zo\u00eb\\.gk-harness"), windows=True)
        text, encoding = manifest.read_text_encoded(profile)
        self.assertEqual((text, encoding), ("", "utf-8"))
        new, mode = shell.apply(text, b, powershell=True)
        self.assertEqual((new, mode), ("\ufeff" + b, "appended"))
        manifest.write_text_atomic(profile, new, encoding)
        self.assertEqual(profile.read_bytes(), codecs.BOM_UTF8 + b.encode("utf-8"))
        again, encoding = manifest.read_text_encoded(profile)
        self.assertEqual(shell.apply(again, b, powershell=True), (again, "unchanged"))
        self.assertEqual(shell.strip(again), "\ufeff")
        # Not a PowerShell profile: an empty rc file gets no mark.
        self.assertEqual(shell.apply("", b), (b, "appended"))

    def test_utf16le_profile_cannot_be_read_as_utf8(self):
        profile = self.tmp / "profile.ps1"
        profile.write_bytes(b"\xff\xfe" + "x\r\n".encode("utf-16-le"))
        with self.assertRaises(manifest.TextEncodingError):
            manifest.read_text(profile)


if __name__ == "__main__":
    unittest.main()
