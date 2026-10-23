from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from opspilot import api
from opspilot.diagnose import diagnose
from opspilot.diagnose.llm import parse
from opspilot.models import Alert, Category, Deploy, Snapshot
from opspilot.report import markdown
from opspilot.signals import extract
from opspilot.sources import live
from opspilot.sources.fixture import load

SCEN = Path(__file__).parent.parent / "eval" / "scenarios"


@pytest.mark.parametrize("path", sorted(SCEN.glob("*.json")), ids=lambda p: p.stem)
def test_scenarios_are_diagnosed(path):
    snap = load(path)
    d = diagnose(snap, engine="rules")
    assert d.root_cause.value == snap.ground_truth["category"]
    assert d.evidence, "un diagnostic doit citer des preuves"


def test_calm_snapshot_is_unknown():
    snap = load(SCEN / "s01_bad_deploy.json")
    calm = Snapshot(alert=snap.alert, metrics={k: v[:20] for k, v in snap.metrics.items()}, limits=snap.limits)
    assert diagnose(calm, engine="rules").root_cause == Category.UNKNOWN


def test_old_deploy_is_ignored():
    snap = load(SCEN / "s01_bad_deploy.json")
    snap.deploys[0].ts -= timedelta(minutes=40)
    assert "recent_deploy" not in extract(snap).values


def test_report_contains_root_cause_and_evidence():
    md = markdown(diagnose(load(SCEN / "s13_db_pool.json"), engine="rules"))
    assert "pool de connexions" in md.lower() and "metric:db_pool_in_use" in md


def test_llm_output_parsing_filters_unknown_evidence():
    text = 'Voici : {"root_cause": "oom_kill", "confidence": 0.9, "evidence": ["event:0", "inventé"], "summary": "x", "alternatives": ["bad_deploy"]}'
    main, alts, summary = parse(text, {"event:0"})
    assert main.category == Category.OOM_KILL and main.evidence == ["event:0"]
    assert alts[0].category == Category.BAD_DEPLOY


def test_llm_failure_falls_back_to_rules(monkeypatch):
    monkeypatch.setattr("opspilot.diagnose.settings.llm_api_key", "x")
    monkeypatch.setattr("opspilot.diagnose.llm.settings.llm_base_url", "http://127.0.0.1:9")
    d = diagnose(load(SCEN / "s10_dependency.json"), engine="llm")
    assert d.engine == "rules" and d.root_cause == Category.DEPENDENCY_DOWN


def test_prometheus_range_parsing():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/query_range"
        return httpx.Response(200, json={"data": {"result": [{"values": [[1, "0.5"], [2, "NaN"], [3, "0.7"]]}]}})

    prom = live.Prometheus("http://prom", client=httpx.Client(transport=httpx.MockTransport(handler)))
    now = datetime.now(UTC)
    assert prom.range("up", now - timedelta(minutes=5), now) == [(1.0, 0.5), (3.0, 0.7)]


def test_kubernetes_events_and_deploys():
    now = datetime.now(UTC)
    iso = now.isoformat().replace("+00:00", "Z")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/events"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "reason": "OOMKilled",
                            "message": "limit",
                            "lastTimestamp": iso,
                            "metadata": {"creationTimestamp": iso},
                            "involvedObject": {"kind": "Pod", "name": "orders-1"},
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "metadata": {
                            "creationTimestamp": iso,
                            "annotations": {"deployment.kubernetes.io/revision": "7"},
                            "ownerReferences": [{"kind": "Deployment", "name": "orders-api"}],
                        },
                        "spec": {"template": {"spec": {"containers": [{"image": "orders:v2"}]}}},
                    }
                ]
            },
        )

    k8s = live.Kubernetes("http://k8s", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert k8s.events("shop", now - timedelta(minutes=1))[0].reason == "OOMKilled"
    assert k8s.deploys("shop", now - timedelta(minutes=1))[0].version.startswith("rev7")


def test_api_webhook_flow(monkeypatch):
    snap = load(SCEN / "s04_oom.json")
    monkeypatch.setattr(api.live, "collect", lambda alert, deploys: snap)
    client = TestClient(api.app)
    payload = {
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": "OrdersPodRestarting", "service": "orders-api"},
                "annotations": {},
                "startsAt": snap.alert.starts_at.isoformat(),
            }
        ],
    }
    assert client.post("/webhook/alertmanager", json=payload).json() == {"accepted": 1}
    incidents = client.get("/incidents").json()
    assert incidents[0]["root_cause"] == "oom_kill"
    assert "OOMKilled" in client.get(f"/incidents/{incidents[0]['id']}").text
    assert "opspilot_diagnoses_total" in client.get("/metrics").text


def test_register_deploy():
    client = TestClient(api.app)
    body = Deploy(ts=datetime.now(UTC), service="orders-api", version="v9").model_dump(mode="json")
    assert client.post("/deploys", json=body).status_code == 201


def test_alert_model_roundtrip():
    a = Alert(name="X", service="s", starts_at=datetime.now(UTC))
    assert Alert.model_validate_json(a.model_dump_json()) == a
