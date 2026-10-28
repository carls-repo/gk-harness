from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from gkh import legacy, settings_merge as sm  # noqa: E402

BOX_HOOKS = Path(ROOT) / "tests" / "fixtures" / "settings" / "box-hooks.json"
PY = "/usr/bin/python3"
CDIR = "/home/carl/.claude"
EDIT = "Edit|Write|MultiEdit|NotebookEdit"
DOCS = "mcp__.*__(batch|update|create)|Artifact|SendUserFile"


def box_settings():
    """Today's box: its hooks section verbatim, wrapped in the keys the installer must not touch."""
    return {
        "permissions": {"defaultMode": "auto", "allow": ["Bash(git status:*)"]},
        "model": "opus",
        "hooks": json.loads(BOX_HOOKS.read_text(encoding="utf-8")),
        "effortLevel": "high",
        "modelSettings": {"fable": {"effort": "high"}},
        "ultracode": True,
        "autoMode": {"rules": ["no prod psql"]},
    }


def cmd(script, python=PY, cdir=CDIR):
    return '"%s" "%s/hooks/gk-harness/%s"' % (python, cdir, script)


def entry(script, python=PY, cdir=CDIR):
    return {"type": "command", "command": cmd(script, python, cdir), "timeout": 10}


def commands(settings):
    return [(ev, g.get("matcher"), e.get("command"))
            for ev, groups in settings.get("hooks", {}).items()
            for g in groups for e in g["hooks"]]


class RegistrationTests(unittest.TestCase):
    def test_table(self):
        self.assertEqual(sm.REGISTRATIONS, (
            ("SessionStart", None, ("gk-seat.py", "gk-peers.py")),
            ("PostModelSwitch", None, ("gk-seat.py",)),
            ("UserPromptSubmit", None, ("gk-peers.py",)),
            ("PreToolUse", "Workflow", ("gk-profile.py",)),
            ("PostToolUse", EDIT, ("gk-claim.py", "gk-remote-pending.py")),
            ("PostToolUse", DOCS, ("gk-remote-clear.py",)),
            ("Stop", None, ("gk-remote-stop.py",)),
            ("SessionEnd", None, ("gk-claims-end.py",)),
        ))
        self.assertEqual(sm.HOOK_TIMEOUT, 10)

    def test_every_registered_script_ships_in_the_repo(self):
        for _, _, scripts in sm.REGISTRATIONS:
            for s in scripts:
                self.assertTrue((Path(ROOT) / "hooks" / s).is_file(), s)

    def test_hook_command_shape(self):
        self.assertEqual(sm.hook_command(PY, CDIR, "gk-seat.py"),
                         '"/usr/bin/python3" "/home/carl/.claude/hooks/gk-harness/gk-seat.py"')

    def test_hook_command_windows_paths_become_posix(self):
        got = sm.hook_command(PureWindowsPath(r"C:\Program Files\Python312\python.exe"),
                              PureWindowsPath(r"C:\Users\Carl Selby\.claude"), "gk-seat.py")
        self.assertEqual(got, '"C:/Program Files/Python312/python.exe" '
                              '"C:/Users/Carl Selby/.claude/hooks/gk-harness/gk-seat.py"')
        self.assertNotIn(" -I", got)
        self.assertNotIn(" -E", got)

    def test_hook_command_refuses_what_a_shell_reads_inside_double_quotes(self):
        # '"' ends the quotes; sh, bash, zsh and PowerShell expand "$" and "`" inside them, so
        # the command would run a different path.
        for ch in ('"', "$", "`"):
            for python, cdir, bad in (("/opt/a%sb/python3" % ch, CDIR, "/opt/a%sb/python3" % ch),
                                      (PY, "/home/c%sx/.claude" % ch, "/home/c%sx/.claude" % ch),
                                      (PY, PureWindowsPath("C:\\Users\\c%sx\\.claude" % ch),
                                       "C:/Users/c%sx/.claude" % ch)):
                with self.subTest(ch=ch, bad=bad):
                    with self.assertRaises(ValueError) as ctx:
                        sm.hook_command(python, cdir, "gk-seat.py")
                    self.assertIn(bad, str(ctx.exception))
                    self.assertIn("contains %s," % ch, str(ctx.exception))
                    with self.assertRaises(ValueError):
                        sm.merge({}, python, cdir)

    def test_is_owned(self):
        self.assertTrue(sm.is_owned(cmd("gk-seat.py")))
        self.assertTrue(sm.is_owned(r'"C:\py.exe" "C:\Users\C\.claude\hooks\gk-harness\gk-seat.py"'))
        for c in ('python3 "$HOME/.claude/hooks/gk-peers.py"', "hooks/gk-harnessx/a.py", "", None, 3):
            self.assertFalse(sm.is_owned(c), repr(c))


class MergeTests(unittest.TestCase):
    def test_empty_settings(self):
        new, undo = sm.merge({}, PY, CDIR)
        self.assertEqual(undo, {"removed": [], "ultracode": {"present": False}, "events": None,
                                "created_events": list(sm.REGISTERED_EVENTS)})
        self.assertIs(new["ultracode"], True)
        self.assertEqual(new["hooks"], {
            "SessionStart": [{"hooks": [entry("gk-seat.py"), entry("gk-peers.py")]}],
            "PostModelSwitch": [{"hooks": [entry("gk-seat.py")]}],
            "UserPromptSubmit": [{"hooks": [entry("gk-peers.py")]}],
            "PreToolUse": [{"matcher": "Workflow", "hooks": [entry("gk-profile.py")]}],
            "PostToolUse": [{"matcher": EDIT, "hooks": [entry("gk-claim.py"),
                                                        entry("gk-remote-pending.py")]},
                            {"matcher": DOCS, "hooks": [entry("gk-remote-clear.py")]}],
            "Stop": [{"hooks": [entry("gk-remote-stop.py")]}],
            "SessionEnd": [{"hooks": [entry("gk-claims-end.py")]}],
        })
        self.assertEqual(list(new["hooks"]), ["SessionStart", "PostModelSwitch", "UserPromptSubmit",
                                              "PreToolUse", "PostToolUse", "Stop", "SessionEnd"])
        self.assertEqual(list(new["hooks"]["PreToolUse"][0]), ["matcher", "hooks"])

    def test_box_settings(self):
        before = box_settings()
        frozen = copy.deepcopy(before)
        new, undo = sm.merge(before, PY, CDIR)
        removed = undo["removed"]
        self.assertEqual(before, frozen, "merge must not change its input")
        self.assertEqual(undo["ultracode"], {"present": True, "value": True})
        self.assertEqual(undo["events"], list(before["hooks"]))
        # Untouched keys: same values, same order.
        self.assertEqual(list(new), list(before))
        for key in ("permissions", "model", "effortLevel", "modelSettings", "autoMode"):
            self.assertEqual(json.dumps(new[key]), json.dumps(before[key]), key)
        # The two project hooks stay where they were, in their own group.
        self.assertEqual(new["hooks"]["PostToolUse"][0], before["hooks"]["PostToolUse"][0])
        # Every legacy entry is gone and recorded; only owned and project entries remain.
        self.assertEqual(len(removed), 8)
        self.assertTrue(all(legacy.is_legacy_command(r["entry"]["command"]) for r in removed))
        for ev, matcher, c in commands(new):
            self.assertFalse(legacy.is_legacy_command(c), c)
        self.assertEqual([c for _, _, c in commands(new) if not sm.is_owned(c)],
                         ['"$HOME/.claude/hooks/supabase-row-cap.sh"',
                          '"$HOME/.claude/hooks/pii-in-logs.sh"'])
        self.assertEqual(len([c for _, _, c in commands(new) if sm.is_owned(c)]), 10)
        # Existing events keep their order; the new event is added at the end.
        self.assertEqual(list(new["hooks"]), list(before["hooks"]) + ["PostModelSwitch"])
        # The legacy-only PreToolUse group is replaced by the owned one, in its place.
        self.assertEqual(new["hooks"]["PreToolUse"],
                         [{"matcher": "Workflow", "hooks": [entry("gk-profile.py")]}])

    def test_idempotent(self):
        once, _ = sm.merge(box_settings(), PY, CDIR)
        twice, undo = sm.merge(once, PY, CDIR)
        self.assertEqual(sm.dumps(twice), sm.dumps(once))
        self.assertEqual(undo["removed"], [])
        self.assertEqual(undo["ultracode"], {"present": True, "value": True})

    def test_new_python_path_replaces_commands_in_place(self):
        once, _ = sm.merge(box_settings(), PY, CDIR)
        again, _ = sm.merge(once, "/opt/py3.13/bin/python3", CDIR)
        old = [c for _, _, c in commands(again) if PY in c]
        self.assertEqual(old, [])
        self.assertEqual(len([c for _, _, c in commands(again) if sm.is_owned(c)]), 10)
        self.assertEqual([(e, m) for e, m, _ in commands(again)], [(e, m) for e, m, _ in commands(once)])

    def test_owned_groups_keep_their_place_after_a_user_adds_one(self):
        once, _ = sm.merge({}, PY, CDIR)
        once["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": "notify-send done"}]})
        again, _ = sm.merge(once, PY, CDIR)
        self.assertEqual(sm.dumps(again), sm.dumps(once))

    def test_stray_owned_entry_inside_a_user_group_is_moved_out(self):
        s = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "mine"},
                                           entry("gk-remote-stop.py")]}]}}
        new, _ = sm.merge(s, PY, CDIR)
        self.assertEqual(new["hooks"]["Stop"], [
            {"hooks": [{"type": "command", "command": "mine"}]},
            {"hooks": [entry("gk-remote-stop.py")]},
        ])

    def test_unrelated_odd_shapes_are_left_alone(self):
        s = {"hooks": {"Notification": "not-a-list", "Stop": [7, {"matcher": "x"},
                                                               {"hooks": "nope"}]}}
        new, _ = sm.merge(s, PY, CDIR)
        self.assertEqual(new["hooks"]["Notification"], "not-a-list")
        self.assertEqual(new["hooks"]["Stop"][:3], [7, {"matcher": "x"}, {"hooks": "nope"}])
        self.assertEqual(new["hooks"]["Stop"][3], {"hooks": [entry("gk-remote-stop.py")]})

    def test_shapes_it_cannot_merge_into_raise(self):
        for bad in ({"hooks": []}, {"hooks": "x"}, {"hooks": {"Stop": {}}},
                    {"hooks": {"PreToolUse": "x"}}):
            with self.assertRaises(sm.SettingsError):
                sm.merge(bad, PY, CDIR)

    def test_an_event_left_empty_by_legacy_removal_goes(self):
        s = {"hooks": {"Notification": [{"hooks": [
            {"type": "command", "command": "python3 ~/.claude/hooks/gk-peers.py"}]}], "Stop": []}}
        new, undo = sm.merge(s, PY, CDIR)
        self.assertNotIn("Notification", new["hooks"])
        self.assertEqual(len(undo["removed"]), 1)
        self.assertTrue(undo["removed"][0]["group_removed"])
        # An event list that was already empty is not the merge's to remove.
        self.assertEqual(new["hooks"]["Stop"], [{"hooks": [entry("gk-remote-stop.py")]}])

    def test_ultracode_false_is_recorded_and_set(self):
        new, undo = sm.merge({"ultracode": False}, PY, CDIR)
        self.assertEqual(undo["ultracode"], {"present": True, "value": False})
        self.assertIs(new["ultracode"], True)


class UnmergeTests(unittest.TestCase):
    def round_trip(self, settings):
        new, undo = sm.merge(settings, PY, CDIR)
        self.assertEqual(json.loads(json.dumps(undo)), undo)  # the journal holds it as JSON
        back = sm.unmerge(new, undo)
        self.assertEqual(sm.dumps(back), sm.dumps(settings))

    def test_round_trips_exactly(self):
        cases = [
            {},
            box_settings(),
            {"ultracode": False, "model": "sonnet"},
            {"hooks": {}, "model": "x"},
            {"hooks": {"Stop": []}},
            # A box whose hooks hold nothing but a legacy entry, with a key after `hooks`: the
            # entry and the key order both come back.
            {"model": "m", "hooks": {"Stop": [{"hooks": [
                {"type": "command", "command": 'python3 "$HOME/.claude/hooks/gk-remote-stop.py"'}]}]},
             "theme": "dark"},
            # Legacy entries mixed into a user group, on both sides of a user entry, and a
            # legacy-only group with the same matcher ahead of it.
            {"hooks": {"PostToolUse": [
                {"matcher": "Edit", "hooks": [
                    {"type": "command", "command": "python3 ~/.claude/hooks/gk-claim.py"}]},
                {"matcher": "Edit", "hooks": [
                    {"type": "command", "command": "python3 ~/.claude/hooks/gk-remote-pending.py"},
                    {"type": "command", "command": "mine.sh", "timeout": 3},
                    {"type": "command", "command": "python3 ~/.claude/hooks/gk-peers.py"}]},
            ], "Notification": [{"hooks": [
                {"type": "command", "command": "\"$HOME/.claude/hooks/gk-profile-guard.sh\""}]}]}},
            # An unregistered event holding only a legacy entry goes, and comes back in its place
            # ahead of the events after it.
            {"hooks": {"Notification": [{"matcher": "", "hooks": [
                {"type": "command", "command": "python3 ~/.claude/hooks/gk-peers.py"}]}],
                "PreCompact": [{"hooks": [{"type": "command", "command": "mine.sh"}]}]}},
            # A legacy entry in the second of two matcher-less user groups goes back into that
            # group, not the first.
            {"hooks": {"SessionStart": [
                {"hooks": [{"type": "command", "command": "userA.sh"}]},
                {"hooks": [{"type": "command", "command": "userB.sh"},
                           {"type": "command", "command": "python3 ~/.claude/hooks/gk-peers.py"}]},
            ]}},
            # Two groups with the same matcher, each mixing a user entry and a legacy entry: each
            # legacy entry goes back into its own group, in its own place.
            {"hooks": {"PostToolUse": [
                {"matcher": "Edit", "hooks": [
                    {"type": "command", "command": "u1.sh"},
                    {"type": "command", "command": "python3 ~/.claude/hooks/gk-claim.py"}]},
                {"matcher": "Edit", "hooks": [
                    {"type": "command", "command": "python3 ~/.claude/hooks/gk-remote-pending.py"},
                    {"type": "command", "command": "u2.sh"}]},
            ]}},
            # The same with a legacy-only group between them, so the second survivor's place
            # among the kept groups differs from its original place.
            {"hooks": {"PostToolUse": [
                {"matcher": "Edit", "hooks": [
                    {"type": "command", "command": "u1.sh"},
                    {"type": "command", "command": "python3 ~/.claude/hooks/gk-claim.py"}]},
                {"matcher": "Edit", "hooks": [
                    {"type": "command", "command": "python3 ~/.claude/hooks/gk-peers.py"}]},
                {"matcher": "Edit", "hooks": [
                    {"type": "command", "command": "python3 ~/.claude/hooks/gk-remote-pending.py"},
                    {"type": "command", "command": "u2.sh"}]},
            ]}},
        ]
        for case in cases:
            with self.subTest(case=json.dumps(case)[:80]):
                self.round_trip(case)

    def test_survivors_are_found_by_their_kept_place(self):
        # An owned group from an earlier install stands first, so each survivor's original
        # index is one more than its place among the kept groups. unmerge drops the owned group
        # and puts each legacy entry back into its own group.
        def group(*commands):
            return {"matcher": "Edit", "hooks": [{"type": "command", "command": c} for c in commands]}
        start = {"hooks": {"PostToolUse": [
            group(cmd("gk-claim.py")),
            group("u1.sh", "python3 ~/.claude/hooks/gk-claim.py"),
            group("python3 ~/.claude/hooks/gk-remote-pending.py", "u2.sh"),
        ]}}
        new, undo = sm.merge(start, PY, CDIR)
        self.assertEqual([(r["group_index"], r["kept_index"]) for r in undo["removed"]],
                         [(1, 0), (2, 1)])
        expected = copy.deepcopy(start)
        del expected["hooks"]["PostToolUse"][0]
        self.assertEqual(sm.dumps(sm.unmerge(new, undo)), sm.dumps(expected))

    def test_survivor_lookup_falls_back_to_unclaimed_keys(self):
        # Without kept_index (or when the group at that place no longer has the same keys),
        # the first group with the same keys that no other original group claimed is used.
        start = {"hooks": {"PostToolUse": [
            {"matcher": "Edit", "hooks": [
                {"type": "command", "command": "u1.sh"},
                {"type": "command", "command": "python3 ~/.claude/hooks/gk-claim.py"}]},
            {"matcher": "Edit", "hooks": [
                {"type": "command", "command": "python3 ~/.claude/hooks/gk-remote-pending.py"},
                {"type": "command", "command": "u2.sh"}]},
        ]}}
        new, undo = sm.merge(start, PY, CDIR)
        for r in undo["removed"]:
            del r["kept_index"]
        self.assertEqual(sm.dumps(sm.unmerge(new, undo)), sm.dumps(start))
        # The first record takes the second survivor; the second record's place is out of
        # range, and the fallback skips the group the first record claimed.
        new, undo = sm.merge(start, PY, CDIR)
        undo["removed"][0]["kept_index"] = 1
        undo["removed"][1]["kept_index"] = 7
        back = sm.unmerge(new, undo)
        self.assertEqual(sorted(len(g["hooks"]) for g in back["hooks"]["PostToolUse"]), [2, 2])
        # A place that now holds a group with other keys is not used.
        start = {"hooks": {"PostToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "u0.sh"}]},
            {"matcher": "Edit", "hooks": [
                {"type": "command", "command": "u1.sh"},
                {"type": "command", "command": "python3 ~/.claude/hooks/gk-claim.py"}]},
        ]}}
        new, undo = sm.merge(start, PY, CDIR)
        self.assertEqual(undo["removed"][0]["kept_index"], 1)
        undo["removed"][0]["kept_index"] = 0
        self.assertEqual(sm.dumps(sm.unmerge(new, undo)), sm.dumps(start))

    def test_user_edits_survive(self):
        before = box_settings()
        new, undo = sm.merge(before, PY, CDIR)
        new["permissions"]["allow"].append("Bash(ls:*)")
        new["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": "notify-send done"}]})
        back = sm.unmerge(new, undo)
        self.assertIn("Bash(ls:*)", back["permissions"]["allow"])
        self.assertEqual([c for _, _, c in commands(back) if sm.is_owned(c)], [])
        self.assertIn(("Stop", None, "notify-send done"), commands(back))
        for r in undo["removed"]:
            self.assertIn(r["entry"]["command"], [c for _, _, c in commands(back)])
        self.assertIs(back["ultracode"], True)

    def test_created_events_are_recorded(self):
        _, undo = sm.merge({}, PY, CDIR)
        self.assertEqual(undo["created_events"], list(sm.REGISTERED_EVENTS))
        _, undo = sm.merge(box_settings(), PY, CDIR)
        self.assertEqual(undo["created_events"], ["PostModelSwitch"])
        s1, _ = sm.merge({}, PY, CDIR)
        self.assertEqual(sm.merge(s1, PY, CDIR)[1]["created_events"], [])

    def test_an_empty_event_list_added_after_install_is_kept(self):
        # Only the event lists the merge created go once empty; an empty list the user added
        # since is a key the installer does not own, whatever its name.
        for start in ({}, {"model": "x"}, box_settings(), {"hooks": {"Stop": []}}):
            with self.subTest(start=json.dumps(start)[:60]):
                new, undo = sm.merge(start, PY, CDIR)
                new["hooks"]["Notification"] = []
                new["hooks"]["PreCompact"] = []
                back = sm.unmerge(new, undo)
                expected = copy.deepcopy(start)
                expected.setdefault("hooks", {}).update({"Notification": [], "PreCompact": []})
                self.assertEqual(sm.dumps(back), sm.dumps(expected))

    def test_two_installs_unwind_in_reverse(self):
        # A second install after a Python upgrade, then both journal entries reversed newest
        # first, gives back the starting file.
        for start in ({}, box_settings(), {"hooks": {"Stop": []}}):
            with self.subTest(start=json.dumps(start)[:60]):
                s1, undo1 = sm.merge(start, PY, CDIR)
                s2, undo2 = sm.merge(s1, "/opt/py3.13/bin/python3", CDIR)
                back = sm.unmerge(sm.unmerge(s2, undo2), undo1)
                self.assertEqual(sm.dumps(back), sm.dumps(start))

    def test_malformed_undo_records_are_skipped(self):
        new, _ = sm.merge({"model": "x"}, PY, CDIR)
        for undo in ({"removed": [7, {"event": "Stop"}, {"entry": {}}], "ultracode": "?", "events": 3},
                     {"removed": {}, "events": [{}]}, None, []):
            back = sm.unmerge(new, undo)
            self.assertEqual(sm.owned_entries(back), [])
            self.assertEqual(back["model"], "x")
            # No usable created_events: only the lists that held nothing but owned entries go.
            self.assertEqual(back["hooks"], {})

    def test_prior_absent_ultracode_is_removed(self):
        new, undo = sm.merge({"model": "x"}, PY, CDIR)
        self.assertNotIn("ultracode", sm.unmerge(new, undo))

    def test_strip_owned_only_drops_owned(self):
        new, _ = sm.merge(box_settings(), PY, CDIR)
        stripped = sm.strip_owned(new)
        self.assertEqual([c for _, _, c in commands(stripped)],
                         ['"$HOME/.claude/hooks/supabase-row-cap.sh"',
                          '"$HOME/.claude/hooks/pii-in-logs.sh"'])
        self.assertIs(stripped["ultracode"], True)

    def test_strip_owned_removes_only_containers_it_emptied(self):
        new, _ = sm.merge({}, PY, CDIR)
        self.assertEqual(sm.strip_owned(new), {"ultracode": True})
        self.assertEqual(sm.strip_owned({"hooks": {"Stop": []}}), {"hooks": {"Stop": []}})
        self.assertEqual(sm.strip_owned({"hooks": {}}), {"hooks": {}})

    def test_owned_entries_report(self):
        new, _ = sm.merge({}, PY, CDIR)
        got = sm.owned_entries(new)
        self.assertEqual(got[0], ("SessionStart", None, cmd("gk-seat.py")))
        self.assertEqual(got[7], ("PostToolUse", DOCS, cmd("gk-remote-clear.py")))
        self.assertEqual(len(got), 10)
        self.assertEqual(sm.owned_entries({}), [])


class LoadStrictTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = self.tmp / "settings.json"

    def load_bytes(self, data):
        self.path.write_bytes(data)
        return sm.load_strict(self.path)

    def assert_rejected(self, data):
        with self.assertRaises(sm.SettingsError) as cm:
            self.load_bytes(data)
        self.assertIn("settings.json", str(cm.exception))
        return str(cm.exception)

    def test_absent_is_none(self):
        self.assertIsNone(sm.load_strict(self.path))

    def test_valid(self):
        self.assertEqual(self.load_bytes(b'{"a": 1}'), {"a": 1})

    def test_trailing_comma_comment_and_truncation(self):
        for data in (b'{"a": 1,}', b"// c\n{}", b'{"hooks": {"Stop": [', b"", b"{} {}"):
            with self.subTest(data=data):
                self.assert_rejected(data)

    def test_message_has_the_location(self):
        msg = self.assert_rejected(b'{\n  "a": 1,\n}')
        self.assertIn("line 3", msg)

    def test_non_standard_values_and_duplicates(self):
        for data in (b'{"a": NaN}', b'{"a": Infinity}', b'{"a": -Infinity}', b'{"a": 1, "a": 2}'):
            with self.subTest(data=data):
                self.assert_rejected(data)

    def test_a_number_that_overflows_to_infinity_is_refused(self):
        # json reads 1e400 as inf, which dumps would write back as Infinity: not JSON.
        for data in (b'{"x": 1e400}', b'{"x": -1e400}', b'{"a": {"b": [1E+999]}}'):
            with self.subTest(data=data):
                msg = self.assert_rejected(data)
                self.assertIn("is not strict JSON", msg)
                self.assertIn("out of range", msg)
        loaded = self.load_bytes(b'{"x": 1.5, "y": -1e308, "z": 1e-400, "n": 1' + b"0" * 400 + b"}")
        self.assertEqual((loaded["x"], loaded["y"], loaded["z"]), (1.5, -1e308, 0.0))
        self.assertEqual(json.loads(sm.dumps(loaded)), loaded)

    def test_shapes(self):
        for data in (b"[]", b'"x"', b'{"hooks": []}', b'{"hooks": {"SessionStart": {}}}'):
            with self.subTest(data=data):
                self.assert_rejected(data)

    def test_not_utf8(self):
        self.assert_rejected(b'{"a": "\xff"}')

    def test_bom_is_accepted(self):
        self.assertEqual(self.load_bytes(b'\xef\xbb\xbf{"a": 1}'), {"a": 1})

    def test_a_folder_in_its_place(self):
        self.path.mkdir()
        with self.assertRaises(sm.SettingsError) as cm:
            sm.load_strict(self.path)
        self.assertIn("settings.json", str(cm.exception))

    def test_dumps_matches_claude_code_style(self):
        s = {"a": {"b": ["é", 1]}, "c": True}
        self.assertEqual(sm.dumps(s), '{\n  "a": {\n    "b": [\n      "é",\n      1\n    ]\n  },\n'
                                      '  "c": true\n}\n')


class CommandExecutionTests(unittest.TestCase):
    """The registered command string runs from the platform shell, and from bash, with a space
    in the home path."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cdir = self.tmp / "Carl Selby" / ".claude"
        target = self.cdir / "hooks" / "gk-harness"
        target.mkdir(parents=True)
        (target / "probe.py").write_text("import sys\nprint('ran:' + sys.stdin.read())\n")
        self.command = sm.hook_command(sys.executable, self.cdir, "probe.py")

    def test_platform_shell(self):
        proc = subprocess.run(self.command, shell=True, input="{}", capture_output=True, text=True)
        self.assertEqual((proc.returncode, proc.stdout.strip()), (0, "ran:{}"), proc.stderr)

    def test_bash(self):
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("no bash on PATH")
        if os.name == "nt" and "system32" in bash.lower():
            self.skipTest("the WSL launcher is not the bash Claude Code uses")
        proc = subprocess.run([bash, "-c", self.command], input="{}", capture_output=True, text=True)
        self.assertEqual((proc.returncode, proc.stdout.strip()), (0, "ran:{}"), proc.stderr)


if __name__ == "__main__":
    unittest.main()
