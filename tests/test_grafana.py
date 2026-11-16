"""Tests for deploy/grafana/ (the dashboard) and `gkh grafana up|down|status` (Task 7 of the
usage ledger). Nothing here runs sudo or docker, and nothing probes the real ports 3300, 9090
or 4318: the runner is a fake and the health checks go to local servers on ephemeral ports."""
from __future__ import annotations

import contextlib
import http.server
import importlib.util
import io
import json
import os
import re
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

from gkh import cli  # noqa: E402

GRAFANA = ROOT / "deploy" / "grafana"
DASHBOARD = GRAFANA / "dashboards" / "gk-harness-usage.json"
API_FIXTURE = ROOT / "tests" / "fixtures" / "otlp" / "logs-api-request.json"
COMPOSE = ["sudo", "-n", "/usr/bin/docker", "compose", "-f", "/etc/gk-grafana/compose.yml"]


def _load_ledger():
    """hooks/gk_ledger.py loaded by path, with hooks/ on sys.path for its own imports."""
    hooks = str(ROOT / "hooks")
    if hooks not in sys.path:
        sys.path.insert(0, hooks)
    spec = importlib.util.spec_from_file_location("_grafana_gk_ledger", ROOT / "hooks"
                                                  / "gk_ledger.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _panels(dash: dict) -> list:
    out = []
    for p in dash.get("panels", []):
        out.append(p)
        out.extend(p.get("panels", []))  # collapsed rows hold their panels
    return out


def _exprs(dash: dict) -> list:
    return [t["expr"] for p in _panels(dash) for t in p.get("targets", []) if "expr" in t]


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.dash = json.loads(DASHBOARD.read_text(encoding="utf-8"))

    def test_dashboard_json_parses_and_uses_datasource_uid(self):
        self.assertEqual(self.dash["uid"], "gk-harness-usage")
        panels = [p for p in _panels(self.dash) if p.get("type") != "row"]
        self.assertGreaterEqual(len(panels), 12)
        for p in panels:
            with self.subTest(panel=p.get("title")):
                self.assertEqual(p["datasource"]["uid"], "gk-prometheus")
                self.assertTrue(p["targets"])
                for t in p["targets"]:
                    self.assertEqual(t["datasource"]["uid"], "gk-prometheus")
                    self.assertTrue(t["expr"].strip())
        titles = {p["title"] for p in panels}
        for want in ("Spend today", "Spend, last 7 days", "Spend per day by model",
                     "Fable share", "Tokens per hour by kind", "Spend by project",
                     "Spend by workflow", "Spend by agent", "Spend by source",
                     "Errors per hour", "Haiku calls over 100K", "Receiver up"):
            self.assertIn(want, titles)
        # Every gk_ledger counter is read through increase(); "today so far" is the default.
        for expr in _exprs(self.dash):
            if "gk_ledger_" in expr:
                self.assertIn("increase(", expr)
        self.assertEqual(self.dash["time"], {"from": "now/d", "to": "now"})
        source = next(p for p in panels if p["title"] == "Spend by source")
        for value in ("sdk", "agent:custom", "agent:builtin:general-purpose",
                      "agent:builtin:workflow-subagent", "repl_main_thread"):
            self.assertIn(value, source["description"])
        up = next(p for p in panels if p["title"] == "Receiver up")
        self.assertEqual(up["targets"][0]["expr"], 'up{job="gk-ledger"}')

    def test_dashboard_metric_names_exist_in_receiver_output(self):
        ledger = _load_ledger()
        metrics = ledger.Metrics()
        lines = []
        for res, attrs, ev, t in ledger.parse_logs(API_FIXTURE.read_bytes()):
            line = ledger.to_line(res, attrs, ev, t, lambda sid: None)
            if line is not None:
                lines.append(line)
        self.assertTrue(lines)
        big = dict(lines[0], input=ledger.LARGE_PROMPT + 50_000)
        error = dict(lines[0], event="api_error")
        for line in lines + [big, error]:
            metrics.add(line)
        rendered = metrics.render()
        series = {}
        for row in rendered.splitlines():
            m = re.match(r"^([a-z_]+)\{(.*)\} ", row)
            if m:
                series.setdefault(m.group(1), set()).update(
                    re.findall(r'([a-z_]+)="', m.group(2)))
        used = set()
        for expr in _exprs(self.dash):
            for name in re.findall(r"\bgk_ledger_[a-z_]+", expr):
                used.add(name)
                self.assertIn(name, series, "%s is not in the receiver's output" % name)
            # Every label the dashboard groups by or filters on is one the receiver sends.
            labels = set()
            for group in re.findall(r"\bby\s*\(([^)]*)\)", expr):
                labels.update(x.strip() for x in group.split(",") if x.strip())
            labels.update(re.findall(r"([a-z_]+)\s*=~?\s*\"", expr.replace('job="', "")))
            for label in labels:
                self.assertTrue(any(label in s for s in series.values()),
                                "label %s is not in the receiver's output" % label)
        self.assertEqual(used, {"gk_ledger_requests_total", "gk_ledger_tokens_total",
                                "gk_ledger_cost_usd_total", "gk_ledger_large_prompts_total"})

    def test_provisioning_points_at_the_dashboard_and_datasource(self):
        ds = (GRAFANA / "provisioning" / "datasources" / "prometheus.yml").read_text()
        self.assertIn("uid: gk-prometheus", ds)
        self.assertIn("http://127.0.0.1:9090", ds)
        dp = (GRAFANA / "provisioning" / "dashboards" / "gk.yml").read_text()
        self.assertIn("/var/lib/grafana/dashboards", dp)
        prom = (GRAFANA / "prometheus.yml").read_text()
        for want in ("scrape_interval: 15s", "job_name: gk-ledger", "127.0.0.1:4318",
                     "metrics_path: /metrics"):
            self.assertIn(want, prom)
        compose = (GRAFANA / "compose.yml").read_text()
        for want in ("prom/prometheus:v2.55.1", "grafana/grafana-oss:11.3.0",
                     "--web.listen-address=127.0.0.1:9090",
                     "--storage.tsdb.retention.time=400d", "GF_SERVER_HTTP_ADDR=127.0.0.1",
                     "GF_SERVER_HTTP_PORT=3300", "env_file: grafana.env"):
            self.assertIn(want, compose)
        self.assertEqual(compose.count("network_mode: host"), 2)
        self.assertEqual(compose.count("restart: unless-stopped"), 2)

    def test_sudoers_allows_exactly_three_commands(self):
        text = (GRAFANA / "sudoers-gk-grafana").read_text()
        self.assertTrue(text.endswith("\n"))
        rule = " ".join(x for x in text.replace("\\\n", " ").splitlines()
                        if x.startswith("carls "))
        rule = re.sub(r"\s+", " ", rule)
        base = "/usr/bin/docker compose -f /etc/gk-grafana/compose.yml"
        self.assertEqual(rule, "carls ALL=(root) NOPASSWD: %s up -d, %s down, %s ps"
                         % (base, base, base))


class _Health(http.server.BaseHTTPRequestHandler):
    seen: list = []
    status = 200

    def do_GET(self):  # noqa: N802
        type(self).seen.append(self.path)
        self.send_response(type(self).status)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


def _server(status: int):
    handler = type("H", (_Health,), {"seen": [], "status": status})
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, handler


class GrafanaCommandTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def fake(argv):
            self.calls.append(list(argv))
            return 0
        patcher = mock.patch.object(cli, "_compose_run", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Never the real ports, even if a test forgets to patch them: point both at port 9,
        # which nothing on this box answers on 127.0.0.1.
        for name in ("GRAFANA_PORT", "PROMETHEUS_PORT"):
            p = mock.patch.object(cli, name, 9)
            p.start()
            self.addCleanup(p.stop)

    def _run(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(sys, "platform", "linux"):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_grafana_command_argv_exact(self):
        self.assertEqual(self._run("grafana", "up")[0], 0)
        self.assertEqual(self._run("grafana", "down")[0], 0)
        self._run("grafana", "status")
        self.assertEqual(self.calls, [COMPOSE + ["up", "-d"], COMPOSE + ["down"],
                                      COMPOSE + ["ps"]])

    def test_grafana_usage_errors(self):
        for argv in (("grafana",), ("grafana", "restart"), ("grafana", "up", "down"),
                     ("grafana", "up", "--json")):
            with self.subTest(argv=argv):
                code, _, err = self._run(*argv)
                self.assertEqual(code, 2)
                self.assertIn("grafana up|down|status", err)
        self.assertEqual(self.calls, [])

    def test_grafana_passes_on_a_failed_compose(self):
        with mock.patch.object(cli, "_compose_run", lambda argv: 1):
            code, _, err = self._run("grafana", "up")
        self.assertEqual(code, 1)
        self.assertIn("sudoers", err)

    def test_grafana_refuses_off_linux(self):
        for platform in ("darwin", "win32"):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                    mock.patch.object(sys, "platform", platform):
                code = cli.main(["grafana", "up"])
            with self.subTest(platform=platform):
                self.assertEqual(code, 2)
                self.assertIn("Linux", err.getvalue())
        self.assertEqual(self.calls, [])

    def test_grafana_status_reads_both_health_urls(self):
        g, gh = _server(200)
        p, ph = _server(200)
        self.addCleanup(g.server_close)
        self.addCleanup(p.server_close)
        self.addCleanup(g.shutdown)
        self.addCleanup(p.shutdown)
        with mock.patch.object(cli, "GRAFANA_PORT", g.server_address[1]), \
                mock.patch.object(cli, "PROMETHEUS_PORT", p.server_address[1]):
            code, out, _ = self._run("grafana", "status")
        self.assertEqual(code, 0)
        self.assertEqual(gh.seen, ["/api/health"])
        self.assertEqual(ph.seen, ["/-/ready"])
        self.assertIn("grafana: up", out)
        self.assertIn("prometheus: up", out)
        self.assertEqual(self.calls, [COMPOSE + ["ps"]])

    def test_grafana_status_reports_a_down_endpoint(self):
        g, gh = _server(503)
        self.addCleanup(g.server_close)
        self.addCleanup(g.shutdown)
        with mock.patch.object(cli, "GRAFANA_PORT", g.server_address[1]):
            code, out, _ = self._run("grafana", "status")  # Prometheus stays on port 9
        self.assertEqual(code, 1)
        self.assertEqual(gh.seen, ["/api/health"])
        self.assertIn("grafana: down (HTTP 503)", out)
        self.assertIn("prometheus: down", out)

    def test_health_limit_is_two_seconds(self):
        seen = []

        def fake_open(url, timeout):
            seen.append((url, timeout))
            raise OSError("refused")
        with mock.patch.object(cli.urllib.request, "urlopen", fake_open):
            self._run("grafana", "status")
        self.assertEqual(seen, [("http://127.0.0.1:9/api/health", 2.0),
                                ("http://127.0.0.1:9/-/ready", 2.0)])


if __name__ == "__main__":
    unittest.main()
