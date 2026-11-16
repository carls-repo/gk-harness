"""Tests for hooks/gk_ledger.py: the receiver's pure functions (Task 2 of the usage ledger)."""
from __future__ import annotations

import json
import os
import re
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "hooks"))

import gk_ledger  # noqa: E402

FIXTURES = Path(ROOT) / "tests" / "fixtures" / "otlp"
API_FIXTURE = FIXTURES / "logs-api-request.json"
SUB_FIXTURE = FIXTURES / "logs-subagent.json"

SESSION_MAP = {"session": "5ef9c6c7-bb4c-4f7c-a33f-bd682961fc6e", "cwd": "/w/proj/sub",
               "root": "/w/proj", "project": "Proj", "branch": "feat/x",
               "updated": "2026-11-16T08:00:00Z"}


def fake_lookup(sid):
    return SESSION_MAP if sid == SESSION_MAP["session"] else None


def lines_from(path, lookup=fake_lookup, names=None):
    out = []
    for res, attrs, ev, t in gk_ledger.parse_logs(path.read_bytes()):
        line = gk_ledger.to_line(res, attrs, ev, t, lookup, names)
        if line is not None:
            out.append(line)
    return out


def api_records_of(body: dict):
    """Yield every api_request log record of a parsed fixture body."""
    for rl in body["resourceLogs"]:
        for sl in rl["scopeLogs"]:
            for rec in sl["logRecords"]:
                if rec.get("body", {}).get("stringValue") == "claude_code.api_request":
                    yield rec


class AnyValueTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(gk_ledger.any_value({"stringValue": "a"}), "a")
        self.assertEqual(gk_ledger.any_value({"intValue": 5}), 5)
        self.assertEqual(gk_ledger.any_value({"intValue": "12"}), 12)
        self.assertEqual(gk_ledger.any_value({"doubleValue": 0.5}), 0.5)
        self.assertIs(gk_ledger.any_value({"boolValue": False}), False)
        self.assertIsNone(gk_ledger.any_value({"arrayValue": {"values": []}}))
        self.assertIsNone(gk_ledger.any_value({"intValue": "x"}))
        self.assertIsNone(gk_ledger.any_value("not a dict"))


class ParseLogsTests(unittest.TestCase):
    def test_rejects_non_json_and_wrong_shape(self):
        for body in (b"not json", b"[]", b'{"resourceLogs": 3}', b"\xff\xfe"):
            with self.assertRaises(ValueError):
                gk_ledger.parse_logs(body)

    def test_fixture_records(self):
        recs = gk_ledger.parse_logs(API_FIXTURE.read_bytes())
        self.assertTrue(recs)
        res, attrs, ev, t = [r for r in recs if r[2] == "claude_code.api_request"][0]
        self.assertEqual(res["service.name"], "claude-code")
        self.assertEqual(attrs["input_tokens"], 2)
        self.assertEqual(t, 1794818070261000000)


class ToLineTests(unittest.TestCase):
    def test_fixture_line_exact(self):
        lines = lines_from(API_FIXTURE)
        self.assertEqual(len(lines), 1)
        expected = {
            "ts": "2026-11-16T08:34:30.261Z", "event": "api_request",
            "session": "5ef9c6c7-bb4c-4f7c-a33f-bd682961fc6e",
            "prompt": "ac3ddb40-7e1a-4fb3-b847-e0d5686972cf",
            "request": "req_011Ch6JKipFXx9WUsBARF9UT", "model": "claude-haiku-5-5",
            "effort": "medium", "speed": "normal", "source": "sdk",
            "agent": None, "parent": None, "agent_name": None,
            "workflow_run": None, "workflow": None,
            "project": "Proj", "root": "/w/proj", "branch": "feat/x",
            "input": 2, "output": 109, "cache_read": 39356, "cache_write": 212,
            "cache_write_5m": 0, "cache_write_1h": 212, "cost_usd": 0.00049066,
            "duration_ms": 1527, "status": None, "attempt": None,
        }
        self.assertEqual(lines[0], expected)
        self.assertEqual(list(lines[0]), list(gk_ledger.LINE_KEYS))
        self.assertIsInstance(lines[0]["cost_usd"], float)

    def test_subagent_fixture_carries_agent_and_workflow(self):
        lines = {ln["request"]: ln for ln in lines_from(SUB_FIXTURE)}
        self.assertEqual(len(lines), 4)
        wf = lines["req_011Ch6JExf4NNSa5CDWWwnxC"]
        self.assertEqual(wf["agent"], "aa7e1a96e001d2b70")
        self.assertEqual(wf["workflow_run"], "wf_f8803e0f-b36")
        self.assertEqual(wf["workflow"], "custom")
        self.assertEqual(wf["agent_name"], "workflow-subagent")
        self.assertEqual(wf["source"], "agent:builtin:workflow-subagent")
        self.assertEqual(wf["effort"], "low")
        self.assertIsNone(wf["parent"])
        ex = lines["req_011Ch6JEPX1nqbSFvc521CqE"]
        self.assertEqual(ex["agent"], "a16b52a2f13903ba1")
        self.assertEqual(ex["agent_name"], "custom")
        self.assertIsNone(ex["workflow_run"])
        main = [ln for ln in lines.values() if ln["source"] == "sdk"]
        self.assertEqual(len(main), 2)
        for ln in main:
            self.assertIsNone(ln["agent"])
            self.assertIsNone(ln["workflow"])
        # No session map for this session: the identity fields are null.
        for ln in lines.values():
            self.assertEqual(ln["session"], "5dbefd3d-e540-4c6e-92c1-765bcfee770b")
            self.assertIsNone(ln["project"])

    def _api(self):
        res, attrs, ev, t = [r for r in gk_ledger.parse_logs(API_FIXTURE.read_bytes())
                             if r[2] == "claude_code.api_request"][0]
        return dict(res), dict(attrs), ev, t

    def test_other_event_dropped(self):
        for res, attrs, ev, t in gk_ledger.parse_logs(API_FIXTURE.read_bytes()):
            if ev not in gk_ledger.EVENTS:
                self.assertIsNone(gk_ledger.to_line(res, attrs, ev, t, fake_lookup))
        res, attrs, _ev, t = self._api()
        self.assertIsNone(gk_ledger.to_line(res, attrs, "claude_code.user_prompt", t,
                                            fake_lookup))

    def test_event_name_with_or_without_prefix(self):
        res, attrs, _ev, t = self._api()
        a = gk_ledger.to_line(res, attrs, "claude_code.api_request", t, fake_lookup)
        b = gk_ledger.to_line(res, attrs, "api_request", t, fake_lookup)
        c = gk_ledger.to_line(res, attrs, None, t, fake_lookup)  # falls back to event.name
        self.assertEqual(a, b)
        self.assertEqual(a, c)
        self.assertEqual(a["event"], "api_request")

    def test_other_service_dropped(self):
        res, attrs, ev, t = self._api()
        res["service.name"] = "some-other-tool"
        self.assertIsNone(gk_ledger.to_line(res, attrs, ev, t, fake_lookup))
        del res["service.name"]
        self.assertIsNone(gk_ledger.to_line(res, attrs, ev, t, fake_lookup))

    def test_unknown_and_content_attributes_dropped(self):
        body = json.loads(API_FIXTURE.read_text())
        for rec in api_records_of(body):
            rec["attributes"] += [
                {"key": "prompt", "value": {"stringValue": "SECRET-PROMPT"}},
                {"key": "body.content", "value": {"stringValue": "SECRET-BODY"}},
                {"key": "error", "value": {"stringValue": "SECRET-ERROR"}},
            ]
        lines = [gk_ledger.to_line(*r, fake_lookup)
                 for r in gk_ledger.parse_logs(json.dumps(body).encode())]
        lines = [ln for ln in lines if ln]
        self.assertEqual(len(lines), 1)
        text = json.dumps(lines[0])
        for needle in ("SECRET", "user@example.invalid", "user.email", "body.content",
                       "00000000-0000-4000-8000-0000000000a1", "terminal", "ttft"):
            self.assertNotIn(needle, text)
        self.assertEqual(list(lines[0]), list(gk_ledger.LINE_KEYS))

    def test_int_value_as_string(self):
        body = json.loads(API_FIXTURE.read_text())
        for rec in api_records_of(body):
            for a in rec["attributes"]:
                if "intValue" in a["value"]:
                    a["value"]["intValue"] = str(a["value"]["intValue"])
            rec["timeUnixNano"] = str(rec["timeUnixNano"])
        lines = [gk_ledger.to_line(*r, fake_lookup)
                 for r in gk_ledger.parse_logs(json.dumps(body).encode())]
        line = [ln for ln in lines if ln][0]
        self.assertEqual(line["input"], 2)
        self.assertEqual(line["cache_read"], 39356)
        self.assertEqual(line["duration_ms"], 1527)
        self.assertIsInstance(line["output"], int)

    def test_api_error_line(self):
        res = {"service.name": "claude-code"}
        attrs = {"session.id": "s1", "event.name": "api_error", "model": "m",
                 "error": "model: m", "status_code": "404", "duration_ms": 12,
                 "attempt": 1, "request_id": "req_1", "speed": "normal",
                 "query_source": "sdk", "effort": "low"}
        line = gk_ledger.to_line(res, attrs, "claude_code.api_error", None, None)
        self.assertEqual(line["event"], "api_error")
        self.assertEqual(line["status"], 404)
        self.assertEqual(line["attempt"], 1)
        self.assertIsNone(line["input"])
        self.assertIsNone(line["cost_usd"])
        self.assertNotIn("model: m", json.dumps(line))
        self.assertRegex(line["ts"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")

    def test_ts_from_time_ns_when_no_event_timestamp(self):
        res, attrs, ev, t = self._api()
        del attrs["event.timestamp"]
        line = gk_ledger.to_line(res, attrs, ev, t, fake_lookup)
        self.assertEqual(line["ts"], "2026-11-16T08:34:30.261Z")

    def test_missing_or_torn_session_map_gives_nulls(self):
        res, attrs, ev, t = self._api()
        sid = attrs["session.id"]
        with tempfile.TemporaryDirectory() as d:
            sessions = Path(d)
            lookup = gk_ledger.SessionLookup(sessions)
            line = gk_ledger.to_line(res, attrs, ev, t, lookup)
            self.assertEqual((line["project"], line["root"], line["branch"]), (None,) * 3)
            path = sessions / ("%s.json" % sid)
            path.write_text('{"session": "%s", "project": "Pr' % sid)  # torn write
            line = gk_ledger.to_line(res, attrs, ev, t, lookup)
            self.assertEqual((line["project"], line["root"], line["branch"]), (None,) * 3)
            path.write_text("[1, 2]")
            os.utime(path, ns=(1, 1))
            self.assertIsNone(lookup(sid))
            full = dict(SESSION_MAP, session=sid)
            path.write_text(json.dumps(full))
            os.utime(path, ns=(2_000_000_000, 2_000_000_000))
            line = gk_ledger.to_line(res, attrs, ev, t, lookup)
            self.assertEqual((line["project"], line["root"], line["branch"]),
                             ("Proj", "/w/proj", "feat/x"))
            # A raising lookup must not break the line.
            def boom(_sid):
                raise OSError("x")
            line = gk_ledger.to_line(res, attrs, ev, t, boom)
            self.assertIsNone(line["project"])

    def test_session_lookup_rejects_unsafe_sid(self):
        with tempfile.TemporaryDirectory() as d:
            lookup = gk_ledger.SessionLookup(Path(d))
            self.assertIsNone(lookup("../etc/passwd"))
            self.assertIsNone(lookup(None))


class LineLengthTests(unittest.TestCase):
    def _api(self):
        res, attrs, ev, t = [r for r in gk_ledger.parse_logs(API_FIXTURE.read_bytes())
                             if r[2] == "claude_code.api_request"][0]
        return dict(res), dict(attrs), ev, t

    def _write_and_read(self, line):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(gk_ledger.append_line(Path(tmp), line))
            (name,) = os.listdir(tmp)
            return (Path(tmp) / name).read_bytes()

    def test_long_model_line_stays_under_4096_bytes(self):
        res, attrs, ev, t = self._api()
        attrs["model"] = "m" * 10_000
        line = gk_ledger.to_line(res, attrs, ev, t, fake_lookup)
        raw = self._write_and_read(line)
        self.assertLess(len(raw), 4096)
        self.assertEqual(json.loads(raw)["model"], "m" * gk_ledger.FIELD_MAX_BYTES)

    def test_long_non_ascii_agent_name_stays_under_4096_and_decodes(self):
        res, attrs, ev, t = self._api()
        attrs["agent.name"] = "\u00e9\u4e2d\U0001F600" * 3_334  # 10,002 chars, 2-4 bytes each
        line = gk_ledger.to_line(res, attrs, ev, t, fake_lookup)
        raw = self._write_and_read(line)
        self.assertLess(len(raw), 4096)
        got = json.loads(raw.decode("utf-8"))["agent_name"]
        self.assertTrue(attrs["agent.name"].startswith(got))
        self.assertLessEqual(len(got.encode("utf-8")), gk_ledger.FIELD_MAX_BYTES)

    def test_every_field_at_worst_case_stays_under_4096(self):
        res, attrs, ev, t = self._api()
        for _k, a in gk_ledger._STRINGS:
            attrs[a] = "\x01" * 10_000  # escapes to six bytes each
        for _k, a in gk_ledger._INTS:
            attrs[a] = "9" * 30
        attrs["session.id"] = "s" * 10_000
        attrs["model"] = "\ud800" * 500  # lone surrogates
        def big_lookup(_sid):
            return {"project": "\"" * 5000, "root": "\\" * 5000, "branch": "b" * 5000}
        line = gk_ledger.to_line(res, attrs, ev, t, big_lookup)
        raw = self._write_and_read(line)
        self.assertLess(len(raw), 4096)
        json.loads(raw.decode("utf-8"))

    def test_append_line_refuses_4096_bytes_or_more(self):
        with tempfile.TemporaryDirectory() as tmp:
            line = {"ts": "2026-11-16T00:00:00.000Z", "x": ""}
            overhead = len(json.dumps(line, separators=(",", ":")).encode()) + 1
            line["x"] = "a" * (4096 - overhead)  # exactly 4096 bytes encoded
            self.assertFalse(gk_ledger.append_line(Path(tmp), line))
            line["x"] = "a" * (4095 - overhead)  # 4095 bytes: written
            self.assertTrue(gk_ledger.append_line(Path(tmp), line))
            self.assertEqual(os.listdir(tmp), ["2026-11-16.jsonl"])
            self.assertEqual(len((Path(tmp) / "2026-11-16.jsonl").read_bytes()), 4095)

    def test_ts_from_time_ns_is_exact(self):
        res, attrs, ev, _t = self._api()
        del attrs["event.timestamp"]
        line = gk_ledger.to_line(res, attrs, ev, 1794818070999999999, fake_lookup)
        self.assertEqual(line["ts"], "2026-11-16T08:34:30.999Z")
        # 23:59:59.9999995 stays on its own day.
        last = 1794873599999999500  # 2026-11-16T23:59:59.9999995Z
        line = gk_ledger.to_line(res, attrs, ev, last, fake_lookup)
        self.assertEqual(line["ts"], "2026-11-16T23:59:59.999Z")
        self.assertEqual(gk_ledger.day_file(Path("/x"), line["ts"]).name, "2026-11-16.jsonl")


class NameLookupTests(unittest.TestCase):
    SID = "5dbefd3d-e540-4c6e-92c1-765bcfee770b"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.projects = Path(self.tmp.name)
        self.sess = self.projects / "-scratch-slug" / self.SID

    def tearDown(self):
        self.tmp.cleanup()

    def test_custom_workflow_name_resolved_from_run_record(self):
        (self.sess / "workflows").mkdir(parents=True)
        (self.sess / "workflows" / "wf_f8803e0f-b36.json").write_text(
            json.dumps({"runId": "wf_f8803e0f-b36", "workflowName": "haiku-ok-ping"}))
        names = gk_ledger.NameLookup(self.projects)
        lines = {ln["request"]: ln for ln in lines_from(SUB_FIXTURE, names=names)}
        self.assertEqual(lines["req_011Ch6JExf4NNSa5CDWWwnxC"]["workflow"], "haiku-ok-ping")
        # Main-session lines have no workflow and stay null.
        self.assertIsNone(lines["req_011Ch6JFBUVdQUJihFVSWsrC"]["workflow"])

    def test_custom_agent_name_resolved_from_meta(self):
        sub = self.sess / "subagents"
        sub.mkdir(parents=True)
        (sub / "agent-a16b52a2f13903ba1.meta.json").write_text(
            json.dumps({"agentType": "Explore", "isBuiltIn": False}))
        wfdir = sub / "workflows" / "wf_f8803e0f-b36"
        wfdir.mkdir(parents=True)
        (wfdir / "agent-aa7e1a96e001d2b70.meta.json").write_text(
            json.dumps({"agentType": "should-not-replace"}))
        names = gk_ledger.NameLookup(self.projects)
        lines = {ln["request"]: ln for ln in lines_from(SUB_FIXTURE, names=names)}
        self.assertEqual(lines["req_011Ch6JEPX1nqbSFvc521CqE"]["agent_name"], "Explore")
        # A real (non-custom) agent name is kept even when a meta file says otherwise.
        self.assertEqual(lines["req_011Ch6JExf4NNSa5CDWWwnxC"]["agent_name"],
                         "workflow-subagent")
        self.assertEqual(names.agent(self.SID, "aa7e1a96e001d2b70"), "should-not-replace")

    def test_name_lookup_missing_files_keeps_event_value(self):
        names = gk_ledger.NameLookup(self.projects)
        lines = {ln["request"]: ln for ln in lines_from(SUB_FIXTURE, names=names)}
        self.assertEqual(lines["req_011Ch6JExf4NNSa5CDWWwnxC"]["workflow"], "custom")
        self.assertEqual(lines["req_011Ch6JEPX1nqbSFvc521CqE"]["agent_name"], "custom")
        # None is not cached: a file that appears later is found.
        (self.sess / "workflows").mkdir(parents=True)
        (self.sess / "workflows" / "wf_f8803e0f-b36.json").write_text(
            json.dumps({"workflowName": "later"}))
        self.assertEqual(names.workflow(self.SID, "wf_f8803e0f-b36"), "later")
        # A found answer is cached.
        (self.sess / "workflows" / "wf_f8803e0f-b36.json").unlink()
        self.assertEqual(names.workflow(self.SID, "wf_f8803e0f-b36"), "later")

    def test_name_lookup_unsafe_ids_and_bad_files(self):
        names = gk_ledger.NameLookup(self.projects)
        self.assertIsNone(names.workflow("../x", "wf_1"))
        self.assertIsNone(names.workflow(self.SID, "*"))
        self.assertIsNone(names.agent(self.SID, None))
        (self.sess / "workflows").mkdir(parents=True)
        (self.sess / "workflows" / "wf_1.json").write_text("{torn")
        self.assertIsNone(names.workflow(self.SID, "wf_1"))
        (self.sess / "workflows" / "wf_2.json").write_text(json.dumps({"workflowName": 3}))
        self.assertIsNone(names.workflow(self.SID, "wf_2"))
        (self.sess / "workflows" / "wf_3.json").write_text(json.dumps({"workflowName": "big"}))
        with mock.patch.object(gk_ledger, "NAME_FILE_MAX", 4):
            self.assertIsNone(names.workflow(self.SID, "wf_3"))
        self.assertEqual(names.workflow(self.SID, "wf_3"), "big")

    def test_default_projects_dir(self):
        with mock.patch.dict(os.environ, {"GK_CLAUDE_DIR": self.tmp.name}):
            self.assertEqual(gk_ledger.NameLookup().projects_dir,
                             Path(self.tmp.name) / "projects")


class FileTests(unittest.TestCase):
    def test_day_boundary(self):
        d = Path("/x")
        a = gk_ledger.day_file(d, "2026-11-15T23:59:59.999Z")
        b = gk_ledger.day_file(d, "2026-11-16T00:00:00.000Z")
        self.assertEqual(a, d / "2026-11-15.jsonl")
        self.assertEqual(b, d / "2026-11-16.jsonl")
        # An offset is converted to UTC.
        self.assertEqual(gk_ledger.day_file(d, "2026-11-16T00:30:00.000+01:00"),
                         d / "2026-11-15.jsonl")
        with tempfile.TemporaryDirectory() as tmp:
            for ts in ("2026-11-15T23:59:59.999Z", "2026-11-16T00:00:00.000Z"):
                line = dict.fromkeys(gk_ledger.LINE_KEYS)
                line["ts"] = ts
                self.assertTrue(gk_ledger.append_line(Path(tmp), line))
            self.assertEqual(sorted(os.listdir(tmp)), ["2026-11-15.jsonl", "2026-11-16.jsonl"])

    def test_day_file_bad_ts_falls_back_to_today(self):
        import datetime
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        for ts in (None, "", "garbage", "2026-13-40T00:00:00Z", 12):
            self.assertEqual(gk_ledger.day_file(Path("/x"), ts).name, today + ".jsonl")

    def test_append_line_is_one_line_and_mode_0600(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "nested" / "gk-ledger"
            line = gk_ledger.to_line(*[r for r in gk_ledger.parse_logs(API_FIXTURE.read_bytes())
                                       if r[2] == "claude_code.api_request"][0], fake_lookup)
            writes = []
            real_write = os.write

            def counting_write(fd, data):
                writes.append(data)
                return real_write(fd, data)
            with mock.patch.object(gk_ledger.os, "write", counting_write):
                self.assertTrue(gk_ledger.append_line(ledger, line))
                self.assertTrue(gk_ledger.append_line(ledger, line))
            self.assertEqual(len(writes), 2)
            path = ledger / "2026-11-16.jsonl"
            raw = path.read_bytes()
            self.assertEqual(raw.count(b"\n"), 2)
            first = raw.split(b"\n")[0]
            self.assertEqual(json.loads(first), line)
            self.assertNotIn(b": ", first)  # compact
            self.assertLess(len(first), 4096)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_append_line_disk_full_returns_false(self):
        import errno
        with tempfile.TemporaryDirectory() as tmp:
            line = dict.fromkeys(gk_ledger.LINE_KEYS)
            line["ts"] = "2026-11-16T00:00:00.000Z"

            def full(fd, data):
                raise OSError(errno.ENOSPC, "No space left on device")
            with mock.patch.object(gk_ledger.os, "write", full):
                self.assertFalse(gk_ledger.append_line(Path(tmp), line))

            def short(fd, data):
                return 1
            with mock.patch.object(gk_ledger.os, "write", short):
                self.assertFalse(gk_ledger.append_line(Path(tmp), line))


_SAMPLE_RE = re.compile(r'^([a-z_]+)\{(.*)\} (\S+)$')
_LABEL_RE = re.compile(r'([a-z_]+)="((?:[^"\\]|\\.)*)"(?:,|$)')


def parse_metrics(text):
    """{(name, frozenset(labels.items())): float} from Prometheus text, asserting the format."""
    out = {}
    types = {}
    for raw in text.splitlines():
        if raw.startswith("# TYPE "):
            _, _, name, kind = raw.split(" ")
            types[name] = kind
            continue
        if raw.startswith("# HELP ") or not raw:
            continue
        m = _SAMPLE_RE.match(raw)
        assert m, raw
        name, labels, value = m.groups()
        assert types.get(name) == "counter", name
        pairs = _LABEL_RE.findall(labels)
        assert ",".join('%s="%s"' % p for p in pairs) == labels, labels
        out[(name, frozenset(pairs))] = float(value)
    assert text.endswith("\n")
    return out


def mline(**kw):
    line = dict.fromkeys(gk_ledger.LINE_KEYS)
    line.update(event="api_request", model="claude-haiku-5-5", effort="low",
                agent_name=None, workflow=None, source="sdk", project="Proj",
                session="SESSION-ID", request="REQ-ID", agent="AGENT-ID", prompt="PROMPT-ID",
                workflow_run="WF-RUN-ID", input=10, output=20, cache_read=30, cache_write=40,
                cache_write_5m=15, cache_write_1h=25, cost_usd=0.25)
    line.update(kw)
    return line


BASE = {"model": "claude-haiku-5-5", "effort": "low", "agent_name": "", "workflow": "",
        "source": "sdk", "project": "Proj"}


def key(name, **extra):
    return (name, frozenset(dict(BASE, **extra).items()))


class MetricsTests(unittest.TestCase):
    def test_metrics_render_parses(self):
        m = gk_ledger.Metrics()
        m.add(mline())
        m.add(mline(input=1, output=2, cache_read=3, cache_write=4, cache_write_5m=4,
                    cache_write_1h=0, cost_usd=0.5))
        m.add(mline(event="api_error", input=None, output=None, cache_read=None,
                    cache_write=None, cache_write_5m=None, cache_write_1h=None, cost_usd=None,
                    status=500, attempt=2))
        got = parse_metrics(m.render())
        self.assertEqual(got[key("gk_ledger_requests_total", event="api_request")], 2)
        self.assertEqual(got[key("gk_ledger_requests_total", event="api_error")], 1)
        self.assertEqual(got[key("gk_ledger_tokens_total", kind="input")], 11)
        self.assertEqual(got[key("gk_ledger_tokens_total", kind="output")], 22)
        self.assertEqual(got[key("gk_ledger_tokens_total", kind="cache_read")], 33)
        self.assertEqual(got[key("gk_ledger_tokens_total", kind="cache_write_5m")], 19)
        self.assertEqual(got[key("gk_ledger_tokens_total", kind="cache_write_1h")], 25)
        self.assertAlmostEqual(got[key("gk_ledger_cost_usd_total")], 0.75)
        self.assertEqual(got.get(key("gk_ledger_large_prompts_total"), 0), 0)
        names = {k[0] for k in got}
        self.assertEqual(names, {"gk_ledger_requests_total", "gk_ledger_tokens_total",
                                 "gk_ledger_cost_usd_total"} | (
                                     names & {"gk_ledger_large_prompts_total"}))
        self.assertIn("# TYPE gk_ledger_large_prompts_total counter", m.render())

    def test_metrics_empty_render(self):
        text = gk_ledger.Metrics().render()
        self.assertEqual(parse_metrics(text), {})

    def test_metrics_label_escaping(self):
        m = gk_ledger.Metrics()
        m.add(mline(project='a\\b"c\nd', workflow=None, agent_name="x"))
        text = m.render()
        self.assertIn('project="a\\\\b\\"c\\nd"', text)
        self.assertNotIn('c\nd', text)
        got = parse_metrics(text)
        self.assertTrue(got)

    def test_large_prompt_threshold(self):
        m = gk_ledger.Metrics()
        m.add(mline(input=50_000, cache_read=40_000, cache_write=10_000))  # exactly 100,000
        got = parse_metrics(m.render())
        self.assertEqual(got.get(key("gk_ledger_large_prompts_total"), 0), 0)
        m.add(mline(input=50_001, cache_read=40_000, cache_write=10_000))
        got = parse_metrics(m.render())
        self.assertEqual(got[key("gk_ledger_large_prompts_total")], 1)

    def test_metrics_labels_carry_no_ids(self):
        m = gk_ledger.Metrics()
        m.add(mline())
        for res, attrs, ev, t in gk_ledger.parse_logs(SUB_FIXTURE.read_bytes()):
            line = gk_ledger.to_line(res, attrs, ev, t, None)
            if line:
                m.add(line)
        text = m.render()
        for needle in ("SESSION-ID", "REQ-ID", "AGENT-ID", "PROMPT-ID", "WF-RUN-ID",
                       "5dbefd3d", "req_", "a16b52a2f13903ba1", "aa7e1a96e001d2b70",
                       "wf_f8803e0f"):
            self.assertNotIn(needle, text)
        for k in parse_metrics(text):
            labels = {n for n, _ in k[1]}
            extra = labels - set(BASE)
            self.assertLessEqual(extra, {"event", "kind"})
            self.assertTrue(set(BASE) <= labels)


class ConstantsTests(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(gk_ledger.LEDGER_VERSION, 1)
        self.assertEqual(gk_ledger.EVENTS, ("claude_code.api_request", "claude_code.api_error"))
        self.assertEqual(gk_ledger.SERVICE, "claude-code")
        self.assertEqual(gk_ledger.LARGE_PROMPT, 100_000)
        self.assertEqual(gk_ledger.LINE_KEYS, (
            "ts", "event", "session", "prompt", "request", "model", "effort", "speed",
            "source", "agent", "parent", "agent_name", "workflow_run", "workflow", "project",
            "root", "branch", "input", "output", "cache_read", "cache_write",
            "cache_write_5m", "cache_write_1h", "cost_usd", "duration_ms", "status",
            "attempt"))

    def test_cli_requires_gk_ledger(self):
        sys.path.insert(0, ROOT)
        from gkh import cli
        self.assertIn("gk_ledger.py", cli.HOOK_SUPPORT)


if __name__ == "__main__":
    unittest.main()
