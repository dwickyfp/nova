"""Focused checks for acceptance failures that could look healthy in Grafana."""

import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

import check_stack


class StackAcceptanceTests(unittest.TestCase):
    def test_host_mode_rejects_missing_and_wrong_mode_app_targets(self) -> None:
        targets = [
            {"labels": {"job": job, "instance": job}, "health": "up"}
            for job in check_stack.CORE_JOBS
        ]
        targets.append({"labels": {"job": "nova-backend", "instance": "nova-backend:8000"},
                        "health": "up"})
        checks = {check.name: check for check in check_stack.check_targets(
            {"data": {"activeTargets": targets}}, "host")}

        self.assertFalse(checks["Scrape nova-backend"].ok)
        self.assertFalse(checks["Scrape nova-scheduler"].ok)
        self.assertFalse(checks["Scrape nova-worker"].ok)
        self.assertFalse(checks["Scrape nova-agent-worker"].ok)
        self.assertFalse(checks["Scrape probe stage-storage"].ok)

    def _targets(self, jobs: dict[str, list[str]]) -> dict:
        targets = [
            {"labels": {"job": job, "instance": job}, "health": "up"}
            for job in check_stack.CORE_JOBS
        ]
        for job, instances in jobs.items():
            targets.extend(
                {"labels": {"job": job, "instance": instance}, "health": "up"}
                for instance in instances
            )
        return {"data": {"activeTargets": targets}}

    def test_split_topology_requires_the_query_tier_and_the_standalone_proxy(self) -> None:
        payload = self._targets({"nova-backend": ["172.18.0.5:8000", "172.18.0.6:8000"]})
        checks = {check.name: check for check in check_stack.check_targets(
            payload, "container", "split")}

        # Replicas are discovered by address, so no fixed web instance is expected.
        self.assertTrue(checks["Scrape nova-backend"].ok)
        self.assertFalse(checks["Scrape nova-query"].ok)
        self.assertFalse(checks["Scrape nova-proxy"].ok)

    def test_split_topology_passes_when_every_replica_is_up(self) -> None:
        payload = self._targets({
            "nova-backend": ["172.18.0.5:8000"],
            "nova-query": ["172.18.0.7:8000", "172.18.0.8:8000"],
            "nova-proxy": ["172.18.0.9:9104"],
        })
        checks = {check.name: check for check in check_stack.check_targets(
            payload, "container", "split")}

        self.assertTrue(checks["Scrape nova-query"].ok)
        self.assertEqual(checks["Scrape nova-query"].detail, "2/2 up")
        self.assertTrue(checks["Scrape nova-proxy"].ok)

    def test_single_topology_does_not_ask_for_split_tiers(self) -> None:
        names = {check.name for check in check_stack.check_targets(
            self._targets({}), "container")}

        self.assertNotIn("Scrape nova-query", names)
        self.assertNotIn("Scrape nova-proxy", names)

    def test_split_topology_reads_the_proxy_listener_from_the_proxy_job(self) -> None:
        single, split = check_stack.metric_queries(), check_stack.metric_queries("split")

        self.assertIn('job="nova-backend"', single["SQL proxy listener"])
        self.assertIn('job="nova-proxy"', split["SQL proxy listener"])
        self.assertIn('service="query"', split["Startup query"])
        self.assertNotIn("Startup query", single)

    def test_split_prometheus_config_is_the_base_config_with_dns_discovery(self) -> None:
        import build_prometheus_split as builder

        self.assertEqual(builder.SPLIT.read_text(), builder.render(builder.BASE.read_text()))
        self.assertNotIn("targets/query.yml", builder.SPLIT.read_text())

    def test_empty_or_failed_probe_sample_is_not_healthy(self) -> None:
        empty = {"data": {"resultType": "vector", "result": []}}
        failed = {"data": {"resultType": "vector", "result": [
            {"value": [0, "0"]}]}}
        self.assertFalse(check_stack.check_instant_vector(empty, "Probe").ok)
        self.assertFalse(check_stack.check_instant_vector(failed, "Probe").ok)
        self.assertFalse(check_stack.check_instant_vector({"data": []}, "Probe").ok)

    def test_missing_rule_and_wrong_dashboard_slug_fail(self) -> None:
        rules = {"data": {"groups": [{"rules": [{"name": "NovaA", "health": "ok"}]}]}}
        checks = check_stack.check_rules(rules, {"NovaA", "NovaB"})
        self.assertFalse(checks[0].ok)
        dashboards = [{"uid": "nova-overview", "type": "dash-db",
                       "url": "/d/nova-overview/wrong-slug"}]
        self.assertFalse(check_stack.check_dashboards(dashboards)[0].ok)

    def test_process_credentials_override_default_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("GRAFANA_ADMIN_USER='from-file'\n"
                            "GRAFANA_ADMIN_PASSWORD=from-file # comment\n", encoding="utf-8")
            with patch.dict(os.environ, {"GRAFANA_ADMIN_USER": "from-process",
                                        "GRAFANA_ADMIN_PASSWORD": "from-process"}):
                self.assertEqual(check_stack.grafana_credentials(path),
                                 ("from-process", "from-process"))

    def test_credentials_cannot_be_sent_over_remote_http(self) -> None:
        with self.assertRaises(ValueError):
            check_stack.validate_url("http://grafana.example:3000", credentialed=True)
        check_stack.validate_url("http://127.0.0.1:3001", credentialed=True)

    def test_application_metrics_are_scoped_to_their_scrape_job(self) -> None:
        root = Path(__file__).resolve().parent
        expressions = [
            target["expr"]
            for path in (root / "grafana" / "dashboards").glob("nova-*.json")
            for panel in json.loads(path.read_text(encoding="utf-8"))["panels"]
            for target in panel.get("targets", [])
        ]
        expressions.append((root / "prometheus" / "alerts.yml").read_text(encoding="utf-8"))
        for expression in expressions:
            for match in re.finditer(r"\b(nova_[A-Za-z0-9_]+)(\{[^}]*\})?", expression):
                self.assertIn("job=", match.group(2) or "", expression)


if __name__ == "__main__":
    unittest.main()
