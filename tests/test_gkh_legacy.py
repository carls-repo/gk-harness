from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from gkh import legacy  # noqa: E402

BOX_HOOKS = Path(ROOT) / "tests" / "fixtures" / "settings" / "box-hooks.json"


def box_commands():
    hooks = json.loads(BOX_HOOKS.read_text(encoding="utf-8"))
    return [e["command"] for groups in hooks.values() for g in groups for e in g["hooks"]]


class LegacyCommandTests(unittest.TestCase):
    def test_todays_box_entries(self):
        legacy_cmds = [c for c in box_commands() if legacy.is_legacy_command(c)]
        kept = [c for c in box_commands() if not legacy.is_legacy_command(c)]
        self.assertEqual(kept, ['"$HOME/.claude/hooks/supabase-row-cap.sh"',
                                '"$HOME/.claude/hooks/pii-in-logs.sh"'])
        self.assertEqual(len(legacy_cmds), 8)

    def test_each_listed_name(self):
        for name in ("gk-profile-guard.sh", "gk-claim.py", "gk-claims-end.py", "gk-peers.py",
                     "gk-remote-clear.py", "gk-remote-pending.py", "gk-remote-stop.py"):
            self.assertTrue(legacy.is_legacy_command('python3 "$HOME/.claude/hooks/%s"' % name), name)
            self.assertTrue(legacy.is_legacy_command(r'py C:\Users\Carl Selby\.claude\hooks\%s' % name),
                            name)

    def test_owned_commands_are_not_legacy(self):
        for cmd in ('"/usr/bin/python3" "/home/carl/.claude/hooks/gk-harness/gk-claim.py"',
                    '"C:/py/python.exe" "C:/Users/Carl Selby/.claude/hooks/gk-harness/gk-peers.py"',
                    r'"C:\py\python.exe" "C:\Users\Carl\.claude\hooks\gk-harness\gk-remote-stop.py"',
                    # Only the folder tells these apart: the bare name is on the legacy list.
                    '"/usr/bin/python3" "/home/carl/.claude/hooks/gk-harness/gk-profile-guard.sh"'):
            self.assertFalse(legacy.is_legacy_command(cmd), cmd)

    def test_skip_hook_and_others_are_not_settings_legacy(self):
        # gk-remote-skip.py was never a registered hook, only a command the policy names.
        for cmd in ('python3 "$HOME/.claude/hooks/gk-remote-skip.py"',
                    '"$HOME/.claude/hooks/supabase-row-cap.sh"', "echo gk-claim", "", None, 7,
                    ["gk-claim.py"]):
            self.assertFalse(legacy.is_legacy_command(cmd), repr(cmd))


class RcAliasTests(unittest.TestCase):
    def test_todays_aliases_match(self):
        for line in ("alias gk='claude'",
                     "alias gk-opus='claude --settings ~/.claude/profiles/opus-seat.json'",
                     "alias gk-sonnet='claude --settings ~/.claude/profiles/sonnet-seat.json'"):
            self.assertTrue(legacy.RC_ALIAS_RE.match(line), line)

    def test_other_lines_do_not(self):
        for line in ("  alias gk='claude'", "alias gkx='x'", "alias gk-fable='x'", "# alias gk='x'",
                     "alias ll='ls -l'", "export gk=1"):
            self.assertIsNone(legacy.RC_ALIAS_RE.match(line), line)


class RetireTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cdir = self.tmp / ".claude"
        self.backup = self.cdir / "backups" / "gk-harness" / "20261028T000000Z"

    def test_fixed_list(self):
        self.assertEqual(set(legacy.RETIRE_PATHS), {
            "hooks/gk-profile-guard.sh", "hooks/gk-profile-guard.test.sh",
            "hooks/gk-claim.py", "hooks/gk-claims-end.py", "hooks/gk-peers.py",
            "hooks/gk-remote-clear.py", "hooks/gk-remote-pending.py", "hooks/gk-remote-skip.py",
            "hooks/gk-remote-stop.py", "hooks/gk-coord.test.sh", "hooks/gk-remote.test.sh",
            "profiles",
        })
        self.assertEqual(len(legacy.RETIRE_PATHS), 12)

    def test_plan_lists_only_what_exists(self):
        (self.cdir / "hooks").mkdir(parents=True)
        for name in ("gk-claim.py", "gk-profile-guard.sh", "supabase-row-cap.sh"):
            (self.cdir / "hooks" / name).write_text("x")
        (self.cdir / "profiles").mkdir()
        (self.cdir / "profiles" / "opus-seat.json").write_text("{}")
        plan = legacy.retire_plan(self.cdir, self.backup)
        self.assertEqual([p["rel"] for p in plan],
                         ["hooks/gk-profile-guard.sh", "hooks/gk-claim.py", "profiles"])
        for p in plan:
            self.assertEqual(p["src"], self.cdir.joinpath(*p["rel"].split("/")))
            self.assertEqual(p["dst"], self.backup.joinpath("legacy", *p["rel"].split("/")))

    def test_plan_is_empty_on_a_fresh_machine(self):
        self.assertEqual(legacy.retire_plan(self.cdir, self.backup), [])


if __name__ == "__main__":
    unittest.main()
