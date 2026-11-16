"""Tests for hooks/gk_ledger.py: the receiver's HTTP server (Task 3 of the usage ledger).

Every server here binds port 0 on the loopback address, never the real port 4318.
"""
from __future__ import annotations

import errno
import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "hooks"))

import gk_common  # noqa: E402
import gk_ledger  # noqa: E402

FIXTURES = Path(ROOT) / "tests" / "fixtures" / "otlp"
API_FIXTURE = FIXTURES / "logs-api-request.json"


class _Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.claude = Path(tmp.name) / "claude"
        self.claude.mkdir()
        env = mock.patch.dict(os.environ, {"GK_CLAUDE_DIR": str(self.claude)})
        env.start()
        self.addCleanup(env.stop)
        self.ledger = self.claude / "state" / "gk-ledger"


class ServerTests(_Base):
    def setUp(self):
        super().setUp()
        self.server = gk_ledger.make_server(self.ledger, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            return resp.status, resp.getheader("Content-Type"), resp.read()
        finally:
            conn.close()

    def post(self, body, ctype="application/json"):
        return self.request("POST", "/v1/logs", body, {"Content-Type": ctype})

    def ledger_lines(self):
        out = []
        for f in sorted(self.ledger.glob("*.jsonl")):
            out.extend(f.read_text().splitlines())
        return out

    def test_post_fixture_writes_line(self):
        status, ctype, body = self.post(API_FIXTURE.read_bytes())
        self.assertEqual(status, 200)
        self.assertEqual(ctype, "application/json")
        self.assertEqual(json.loads(body), {})
        lines = self.ledger_lines()
        self.assertEqual(len(lines), 1)
        line = json.loads(lines[0])
        self.assertEqual(line["event"], "api_request")
        self.assertEqual(list(line), list(gk_ledger.LINE_KEYS))
        self.assertEqual(oct(self.ledger.stat().st_mode & 0o777), oct(0o700))
        # The receiver also makes the session-map folder, private.
        self.assertEqual(oct((self.ledger / "sessions").stat().st_mode & 0o777), oct(0o700))

    def test_health(self):
        status, ctype, body = self.request("GET", "/gk-ledger/health")
        self.assertEqual(status, 200)
        self.assertEqual(ctype, "application/json")
        self.assertEqual(json.loads(body), {"gk-ledger": gk_ledger.LEDGER_VERSION})

    def test_metrics_after_post(self):
        self.post(API_FIXTURE.read_bytes())
        status, ctype, body = self.request("GET", "/metrics")
        self.assertEqual(status, 200)
        self.assertEqual(ctype, "text/plain; version=0.0.4")
        self.assertIn('event="api_request"} 0', body.decode())  # a new series shows 0 first
        status, ctype, body = self.request("GET", "/metrics")
        text = body.decode()
        self.assertIn('event="api_request"} 1', text)
        self.assertIn("gk_ledger_tokens_total{", text)

    def test_404_other_path(self):
        self.assertEqual(self.request("GET", "/nope")[0], 404)
        self.assertEqual(self.request("GET", "/v1/logs")[0], 404)
        self.assertEqual(self.post(API_FIXTURE.read_bytes(), "application/json")[0], 200)
        self.assertEqual(self.request("POST", "/v1/traces", b"{}",
                                      {"Content-Type": "application/json"})[0], 404)
        self.assertEqual(self.request("PUT", "/v1/logs", b"{}",
                                      {"Content-Type": "application/json"})[0], 404)

    def test_400_not_json(self):
        self.assertEqual(self.post(b"not json")[0], 400)
        self.assertEqual(self.post(b"[1, 2]")[0], 400)
        self.assertEqual(self.ledger_lines(), [])

    def test_411_no_length(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.putrequest("POST", "/v1/logs")
            conn.putheader("Content-Type", "application/json")
            conn.endheaders()
            self.assertEqual(conn.getresponse().status, 411)
        finally:
            conn.close()

    def test_413_over_cap(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.putrequest("POST", "/v1/logs")
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(gk_ledger.MAX_BODY + 1))
            conn.endheaders()  # the body is never sent: the reply must not wait for it
            self.assertEqual(conn.getresponse().status, 413)
        finally:
            conn.close()

    def test_415_wrong_type(self):
        self.assertEqual(self.post(API_FIXTURE.read_bytes(), "text/plain")[0], 415)
        self.assertEqual(self.post(API_FIXTURE.read_bytes(), "application/x-protobuf")[0], 415)
        self.assertEqual(self.ledger_lines(), [])

    def test_binds_loopback_only(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_twenty_concurrent_posts_keep_lines_whole(self):
        body = API_FIXTURE.read_bytes()
        results = []
        start = threading.Barrier(20)

        def send():
            start.wait()
            results.append(self.post(body)[0])

        threads = [threading.Thread(target=send) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(20)
        self.assertEqual(results, [200] * 20)
        lines = self.ledger_lines()
        self.assertEqual(len(lines), 20)
        for line in lines:
            self.assertEqual(json.loads(line)["event"], "api_request")

    def test_disk_full_logged_once_keeps_serving(self):
        body = API_FIXTURE.read_bytes()
        with mock.patch("os.write", side_effect=OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))):
            self.assertEqual(self.post(body)[0], 200)
            self.assertEqual(self.post(body)[0], 200)
        log = (self.ledger / "receiver.log").read_text()
        self.assertEqual(log.count(os.strerror(errno.ENOSPC)), 1, log)
        self.assertEqual(log.count("first write_failed"), 1, log)
        self.assertEqual(self.server.errors.counts.get("write_failed"), 2)
        self.assertEqual(self.request("GET", "/gk-ledger/health")[0], 200)
        self.assertEqual(self.ledger_lines(), [])

    def test_handler_error_goes_to_log_not_stderr(self):
        with mock.patch.object(self.server, "ingest", side_effect=RuntimeError("boom")), \
                mock.patch("sys.stderr") as err:
            with self.assertRaises((http.client.HTTPException, ConnectionError)):
                self.post(API_FIXTURE.read_bytes())
        err.write.assert_not_called()
        self.assertTrue(self.wait_for_log("first handler: RuntimeError: boom"))
        self.assertEqual(self.request("GET", "/gk-ledger/health")[0], 200)

    def wait_for_log(self, text, timeout=5.0):
        end = time.monotonic() + timeout
        log = self.ledger / "receiver.log"
        while time.monotonic() < end:
            if log.exists() and text in log.read_text():
                return True
            time.sleep(0.02)
        return False

    def test_no_request_log_lines(self):
        with mock.patch("sys.stderr") as err:
            self.request("GET", "/gk-ledger/health")
            self.request("GET", "/nope")
        err.write.assert_not_called()

    def test_400_deeply_nested_body(self):
        # json.loads raises RecursionError here; the client must get 400, not a hang-up.
        self.assertEqual(self.post(b"[" * 100000)[0], 400)
        self.assertEqual(self.post(b'{"resourceLogs": ' + b"[" * 100000)[0], 400)
        self.assertEqual(self.request("GET", "/gk-ledger/health")[0], 200)
        self.assertEqual(self.ledger_lines(), [])

    def test_reuse_port_disabled(self):
        # Spec 6: a second bind must fail with EADDRINUSE on any Python version (3.13's
        # HTTPServer turns SO_REUSEPORT on by default).
        self.assertIs(gk_ledger.LedgerServer.allow_reuse_port, False)
        with self.assertRaises(OSError):
            second = gk_ledger.make_server(self.ledger, port=self.port)
            second.server_close()


class ServeTests(_Base):
    def run_serve(self, **kw):
        result = {}

        def target():
            result["rc"] = gk_ledger.serve(self.ledger, **kw)

        t = threading.Thread(target=target, daemon=True)
        t.start()
        return t, result

    def wait_for(self, cond, timeout=5.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cond():
                return True
            time.sleep(0.02)
        return False

    def free_port(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port

    def test_second_serve_on_bound_port_returns_0(self):
        holder = socket.socket()
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        self.addCleanup(holder.close)
        port = holder.getsockname()[1]
        self.assertEqual(gk_ledger.serve(self.ledger, port, idle_seconds=1,
                                         live=lambda: False), 0)
        self.assertFalse(self.ledger.exists())

    def test_idle_exit_removes_pidfile(self):
        pidfile = self.ledger / "receiver.pid"
        t, result = self.run_serve(port=self.free_port(), idle_seconds=1, live=lambda: False)
        self.assertTrue(self.wait_for(pidfile.exists))
        self.assertEqual(pidfile.read_text().strip(), str(os.getpid()))
        t.join(10)
        self.assertFalse(t.is_alive())
        self.assertEqual(result["rc"], 0)
        self.assertFalse(pidfile.exists())

    def test_live_session_keeps_it_running(self):
        pidfile = self.ledger / "receiver.pid"
        live = [True]
        t, result = self.run_serve(port=self.free_port(), idle_seconds=1,
                                   live=lambda: live[0])
        self.assertTrue(self.wait_for(pidfile.exists))
        time.sleep(2)
        self.assertTrue(t.is_alive())
        self.assertTrue(pidfile.exists())
        live[0] = False
        t.join(10)
        self.assertFalse(t.is_alive())
        self.assertEqual(result["rc"], 0)
        self.assertFalse(pidfile.exists())

    def test_prunes_old_session_maps(self):
        sessions = self.ledger / "sessions"
        sessions.mkdir(parents=True)
        old, fresh = sessions / "old.json", sessions / "fresh.json"
        old.write_text("{}")
        fresh.write_text("{}")
        aged = time.time() - 91 * 86400
        os.utime(old, (aged, aged))
        t, _ = self.run_serve(port=self.free_port(), idle_seconds=1, live=lambda: False)
        t.join(10)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    def test_sigterm_stops_script_and_removes_pidfile(self):
        port = self.free_port()
        pidfile = self.ledger / "receiver.pid"
        proc = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "hooks", "gk_ledger.py"), "serve",
             "--port", str(port)],
            env=dict(os.environ, GK_CLAUDE_DIR=str(self.claude)),
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            self.assertTrue(self.wait_for(pidfile.exists, 10))
            self.assertEqual(pidfile.read_text().strip(), str(proc.pid))
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/gk-ledger/health")
            resp = conn.getresponse()
            self.assertEqual((resp.status, json.loads(resp.read())),
                             (200, {"gk-ledger": gk_ledger.LEDGER_VERSION}))
            conn.close()
            proc.terminate()
            _, err = proc.communicate(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        self.assertEqual(proc.returncode, 0, err)
        self.assertEqual(err, b"")
        self.assertFalse(pidfile.exists())

    def test_main_parses_port(self):
        with mock.patch.object(gk_ledger, "serve", return_value=0) as serve:
            self.assertEqual(gk_ledger.main(["serve", "--port", "5123"]), 0)
            serve.assert_called_once_with(gk_common.ledger_dir(), 5123)
            serve.reset_mock()
            with mock.patch.dict(os.environ, {"GK_LEDGER_PORT": "5124"}):
                gk_ledger.main(["serve"])
            serve.assert_called_once_with(gk_common.ledger_dir(), 5124)
        with mock.patch("sys.stderr"):
            self.assertNotEqual(gk_ledger.main(["bogus"]), 0)


class LiveSessionTests(_Base):
    def write_session(self, name, pid):
        d = self.claude / "sessions"
        d.mkdir(exist_ok=True)
        (d / ("%s.json" % name)).write_text(json.dumps({"sessionId": name, "pid": pid}))

    def dead_pid(self):
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        return p.pid

    def test_no_sessions_folder(self):
        self.assertFalse(gk_common.live_session())

    def test_dead_pid_is_not_live(self):
        self.write_session("a", self.dead_pid())
        self.write_session("b", "123")
        self.assertFalse(gk_common.live_session())

    def test_live_pid_is_live(self):
        self.write_session("a", self.dead_pid())
        self.write_session("b", os.getpid())
        self.assertTrue(gk_common.live_session())


if __name__ == "__main__":
    unittest.main()
