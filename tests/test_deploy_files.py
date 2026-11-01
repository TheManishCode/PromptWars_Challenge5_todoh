"""The deploy files parse and agree with the code: every metric an alert or dashboard names is one the server emits."""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
yaml = pytest.importorskip("yaml")


def exported():
    from eventmate.web import observe

    m = observe.Metrics()
    m.end(200, 1, "/x")
    gauges = {k: 1 for k in ("uptime_seconds", "workspaces", "workspaces_active_last_minute", "searches_in_flight", "searches_capacity", "poll_failures", "disk_free_bytes")}
    return set(re.findall(r"^(eventmate_[a-z_]+)", observe.prometheus(m, gauges, {"calls": 1}), re.M)) | {"eventmate_http_request_duration_seconds_bucket"}


def names(expr):
    return set(re.findall(r"eventmate_[a-z_]+", expr))


def test_every_alert_names_a_metric_the_server_exports():
    doc = yaml.safe_load((ROOT / "deploy/prometheus/alerts.yml").read_text())
    have = exported()
    for rule in doc["groups"][0]["rules"]:
        for n in names(rule["expr"]) if "eventmate_" in rule["expr"] else []:
            assert n in have or n.replace("_total", "") in have or n + "_total" in have, (rule["alert"], n)
        assert rule["labels"]["severity"] in ("page", "ticket") and rule["annotations"]["summary"]


def test_the_dashboard_and_compose_and_prometheus_config_parse_and_are_consistent():
    d = json.loads((ROOT / "deploy/grafana/dashboard.json").read_text())
    have = exported()
    for p in d["panels"]:
        for t in p["targets"]:
            for n in names(t["expr"]):
                assert n in have or n + "_total" in have, (p["title"], n)
    c = yaml.safe_load((ROOT / "deploy/docker-compose.yml").read_text())
    assert {"todoh", "prometheus", "grafana"} <= set(c["services"]) and c["services"]["todoh"]["read_only"] is True
    prom = yaml.safe_load((ROOT / "deploy/prometheus/prometheus.yml").read_text())
    assert prom["scrape_configs"][0]["metrics_path"] == "/metrics"


def test_the_dockerfile_runs_unprivileged_with_a_healthcheck_and_the_locked_install():
    text = (ROOT / "Dockerfile").read_text()
    assert "USER todoh" in text and "HEALTHCHECK" in text and "--require-hashes -r requirements-runtime.lock" in text


def test_the_example_environment_allows_the_name_prometheus_scrapes_by_and_the_stack_has_a_fixed_project_name():
    """Found by running the stack: Prometheus reaches the app as `todoh:8000`, which the host check refused (HTTP 400), and the folder name `deploy` collided with another project."""
    env = (ROOT / "deploy/env.example").read_text()
    line = next(entry for entry in env.splitlines() if entry.startswith("EVENTMATE_ALLOWED_HOSTS="))
    assert "todoh" in line.split("=", 1)[1].split(",")
    assert yaml.safe_load((ROOT / "deploy/docker-compose.yml").read_text())["name"] == "todoh"
