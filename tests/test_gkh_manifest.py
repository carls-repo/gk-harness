from __future__ import annotations

import hashlib
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from gkh import manifest as mf  # noqa: E402

SHA = "0" * 64


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = self.tmp / "backups" / "gk-harness" / "manifest.json"

    def test_absent_is_empty(self):
        self.assertEqual(mf.load(self.path), {"format": 1, "installs": []})
        self.assertEqual(mf.empty(), {"format": 1, "installs": []})

    def test_save_load_round_trip(self):
        m = mf.empty()
        inst = mf.new_install("1.0.0", self.tmp / "b", at="2026-10-28T09:54:56Z")
        inst["actions"].append(mf.file_action(self.tmp / "x", mf.prior_absent(), SHA))
        m["installs"].append(inst)
        mf.save(self.path, m)
        self.assertEqual(mf.load(self.path), m)
        self.assertTrue(self.path.read_bytes().endswith(b"}\n"))

    def test_invalid_manifests_raise(self):
        self.path.parent.mkdir(parents=True)
        for data in (b"{", b"[]", b'{"format": 1}', b'{"format": 1, "installs": {}}',
                     b'{"format": 2, "installs": []}', b'{"format": 1, "installs": [7]}',
                     b'{"format": 1, "installs": [{"actions": {}}]}', b"\xff"):
            with self.subTest(data=data):
                self.path.write_bytes(data)
                with self.assertRaises(mf.ManifestError) as ctx:
                    mf.load(self.path)
                self.assertIn("manifest.json", str(ctx.exception))

    def test_new_install_shape(self):
        inst = mf.new_install("1.0.0", PureWindowsPath(r"C:\Users\C S\.claude\backups\gk-harness\x"),
                              at="2026-10-28T09:54:56Z")
        self.assertEqual(inst, {"at": "2026-10-28T09:54:56Z", "version": "1.0.0",
                                "backup": "C:/Users/C S/.claude/backups/gk-harness/x", "actions": []})
        self.assertIsNone(mf.new_install("1.0.0", None)["backup"])
        self.assertRegex(mf.new_install("1.0.0", None)["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_action_shapes(self):
        w = PureWindowsPath
        self.assertEqual(mf.prior_absent(), {"state": "absent"})
        self.assertEqual(mf.prior_backup(w(r"C:\b\agents\worker-opus.md")),
                         {"state": "backup", "backup": "C:/b/agents/worker-opus.md"})
        self.assertEqual(mf.file_action(w(r"C:\c\agents\worker-opus.md"), mf.prior_absent(), SHA),
                         {"type": "file", "path": "C:/c/agents/worker-opus.md",
                          "prior": {"state": "absent"}, "sha256": SHA})
        undo = {"removed": [], "ultracode": {"present": False}, "events": None}
        self.assertEqual(mf.settings_action("/c/settings.json", mf.prior_backup("/b/settings.json"),
                                            SHA, undo),
                         {"type": "settings", "path": "/c/settings.json",
                          "prior": {"state": "backup", "backup": "/b/settings.json"},
                          "sha256": SHA, "undo": undo})
        self.assertEqual(mf.claude_md_action("/c/CLAUDE.md", mf.prior_absent(), SHA, "created"),
                         {"type": "claude_md", "path": "/c/CLAUDE.md", "prior": {"state": "absent"},
                          "sha256": SHA, "mode": "created"})
        removed = [{"line": 3, "text": "alias gk='claude'"}]
        self.assertEqual(mf.shell_rc_action("/h/.bashrc", mf.prior_backup("/b/rc"), SHA, removed),
                         {"type": "shell_rc", "path": "/h/.bashrc",
                          "prior": {"state": "backup", "backup": "/b/rc"}, "sha256": SHA,
                          "removed_aliases": removed})
        self.assertEqual(mf.legacy_move_action("/c/profiles", "/b/legacy/profiles"),
                         {"type": "legacy_move", "src": "/c/profiles", "dst": "/b/legacy/profiles"})
        self.assertEqual(mf.ACTION_TYPES, ("file", "settings", "claude_md", "shell_rc", "legacy_move"))

    def test_actions_newest_first(self):
        m = mf.empty()
        for n in (1, 2):
            inst = mf.new_install("1.0.0", None)
            inst["actions"] = [mf.legacy_move_action("/s%d%s" % (n, k), "/d") for k in "ab"]
            m["installs"].append(inst)
        self.assertEqual([a["src"] for a in mf.actions_newest_first(m)], ["/s2b", "/s2a", "/s1b", "/s1a"])


class FileHelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def leftovers(self, folder):
        return [p.name for p in Path(folder).iterdir() if p.name.endswith(".tmp")]

    def test_sha256(self):
        p = self.tmp / "a"
        self.assertIsNone(mf.sha256_file(p))
        p.write_bytes(b"abc\r\n")
        self.assertEqual(mf.sha256_file(p), hashlib.sha256(b"abc\r\n").hexdigest())
        self.assertEqual(mf.sha256_bytes(b"abc\r\n"), hashlib.sha256(b"abc\r\n").hexdigest())

    def test_text_is_written_and_read_without_newline_translation(self):
        p = self.tmp / "deep" / "er" / "f.txt"
        sha = mf.write_text_atomic(p, "a\nb\r\nc")
        self.assertEqual(p.read_bytes(), b"a\nb\r\nc")
        self.assertEqual(sha, hashlib.sha256(b"a\nb\r\nc").hexdigest())
        self.assertEqual(mf.read_text(p), "a\nb\r\nc")
        self.assertEqual(self.leftovers(p.parent), [])

    def test_read_text_absent_is_none(self):
        self.assertIsNone(mf.read_text(self.tmp / "nope"))

    def test_non_utf8_bytes_survive_a_round_trip(self):
        p = self.tmp / "rc"
        p.write_bytes(b"caf\xe9\n")
        text = mf.read_text(p)
        mf.write_text_atomic(p, text + "more\n")
        self.assertEqual(p.read_bytes(), b"caf\xe9\nmore\n")

    def test_marked_utf16_and_utf32_are_decoded_and_re_encoded_with_their_mark(self):
        text = "Set-Alias ll Get-ChildItem\r\n# Zoë \U0001f600\r\n"
        for mark, encoding in ((b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be"),
                               (b"\xff\xfe\x00\x00", "utf-32-le"), (b"\x00\x00\xfe\xff", "utf-32-be")):
            with self.subTest(encoding=encoding):
                data = mark + text.encode(encoding)
                self.assertEqual(mf.decode_text(data), (text, encoding))
                self.assertEqual(mf.encode_text(text, encoding), data)
                p = self.tmp / ("profile-%s.ps1" % encoding)
                p.write_bytes(data)
                self.assertEqual(mf.read_text_encoded(p), (text, encoding))
                with self.assertRaises(mf.TextEncodingError) as ctx:
                    mf.read_text(p)
                self.assertIn(str(p), str(ctx.exception))
                sha = mf.write_text_atomic(p, text + "x\r\n", encoding)
                self.assertEqual(p.read_bytes(), mark + (text + "x\r\n").encode(encoding))
                self.assertEqual(sha, hashlib.sha256(p.read_bytes()).hexdigest())

    def test_utf8_keeps_its_mark_in_the_text(self):
        data = b"\xef\xbb\xbfalias ll='ls'\n"
        self.assertEqual(mf.decode_text(data), ("﻿alias ll='ls'\n", "utf-8"))
        self.assertEqual(mf.encode_text("﻿alias ll='ls'\n"), data)
        p = self.tmp / "rc"
        p.write_bytes(data)
        self.assertEqual(mf.read_text(p), "﻿alias ll='ls'\n")
        self.assertEqual(mf.read_text_encoded(p), ("﻿alias ll='ls'\n", "utf-8"))
        self.assertIsNone(mf.read_text_encoded(self.tmp / "nope"))

    def test_text_an_edit_would_corrupt_is_refused(self):
        p = self.tmp / "profile.ps1"
        for data in (b"\xff\xfeS\x00e\x00t", b"S\x00e\x00t\x00\r\x00\n\x00"):
            with self.subTest(data=data):
                p.write_bytes(data)
                with self.assertRaises(mf.TextEncodingError) as ctx:
                    mf.read_text_encoded(p)
                self.assertIn(str(p), str(ctx.exception))
                with self.assertRaises(mf.TextEncodingError):
                    mf.read_text(p)
        with self.assertRaises(ValueError):
            mf.encode_text("x", "latin-1")

    def test_lone_utf16_surrogates_round_trip(self):
        data = b"\xff\xfe" + b"a\x00\x00\xd8b\x00"  # an unpaired high surrogate between a and b
        text, encoding = mf.decode_text(data)
        self.assertEqual(mf.encode_text(text, encoding), data)

    @unittest.skipIf(os.name == "nt", "POSIX permissions")
    def test_existing_mode_is_kept(self):
        p = self.tmp / "settings.json"
        p.write_text("{}")
        os.chmod(str(p), 0o600)
        mf.write_text_atomic(p, "{}\n")
        self.assertEqual(stat.S_IMODE(os.stat(str(p)).st_mode), 0o600)

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_a_symlinked_target_is_written_through(self):
        real = self.tmp / "dotfiles" / "bashrc"
        real.parent.mkdir()
        real.write_text("old\n")
        link = self.tmp / ".bashrc"
        os.symlink(str(real), str(link))
        mf.write_text_atomic(link, "new\n")
        self.assertTrue(link.is_symlink())
        self.assertEqual(real.read_text(), "new\n")
        self.assertEqual(self.leftovers(self.tmp), [])
        self.assertEqual(self.leftovers(real.parent), [])

    def test_failed_replace_raises_and_leaves_no_temp_file(self):
        p = self.tmp / "settings.json"
        p.write_text("{}")
        with mock.patch.object(mf.os, "replace", side_effect=PermissionError("locked")), \
                mock.patch.object(mf.time, "sleep"):
            with self.assertRaises(PermissionError):
                mf.write_text_atomic(p, "{}\n")
        self.assertEqual(p.read_text(), "{}")
        self.assertEqual(self.leftovers(self.tmp), [])

    def test_replace_is_retried_while_locked(self):
        p = self.tmp / "settings.json"
        real = os.replace
        calls = []

        def flaky(src, dst):
            calls.append(1)
            if len(calls) < 3:
                raise PermissionError("locked")
            return real(src, dst)

        with mock.patch.object(mf.os, "replace", side_effect=flaky), mock.patch.object(mf.time, "sleep"):
            mf.write_text_atomic(p, "x")
        self.assertEqual(p.read_text(), "x")
        self.assertEqual(len(calls), 3)

    def test_copy_file(self):
        src = self.tmp / "src.md"
        src.write_bytes(b"line\r\nline\n")
        dst = self.tmp / "out" / "dst.md"
        sha = mf.copy_file(src, dst)
        self.assertEqual(dst.read_bytes(), b"line\r\nline\n")
        self.assertEqual(sha, hashlib.sha256(b"line\r\nline\n").hexdigest())

    def test_write_json_atomic(self):
        p = self.tmp / "gk-harness.json"
        sha = mf.write_json_atomic(p, {"b": "é", "a": 1})
        self.assertEqual(p.read_bytes(), '{\n  "b": "é",\n  "a": 1\n}\n'.encode("utf-8"))
        self.assertEqual(sha, mf.sha256_file(p))


if __name__ == "__main__":
    unittest.main()
