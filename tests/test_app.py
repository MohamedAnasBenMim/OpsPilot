import importlib
import sys

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("PAYMENTS_URL", "simulated")
    monkeypatch.setenv("CHAOS_TOKEN", "secret")
    sys.modules.pop("orders.main", None)
    module = importlib.import_module("orders.main")
    return TestClient(module.app)


def test_orders_and_metrics(client):
    assert client.get("/orders").status_code == 200
    assert client.post("/orders").status_code == 201
    metrics = client.get("/metrics").text
    assert 'http_requests_total{route="/orders",status="200"}' in metrics
    assert "db_pool_max 20.0" in metrics


def test_chaos_requires_token(client):
    assert client.post("/chaos", json={"mode": "errors"}).status_code == 403


def test_dependency_down_produces_upstream_errors(client):
    client.post("/chaos", json={"mode": "dependency_down", "duration_s": 60}, headers={"x-chaos-token": "secret"})
    assert client.post("/orders").status_code == 502
    assert 'upstream_requests_total{outcome="error"}' in client.get("/metrics").text


def test_missing_config_exits(monkeypatch):
    monkeypatch.delenv("PAYMENTS_URL", raising=False)
    sys.modules.pop("orders.main", None)
    with pytest.raises(SystemExit):
        importlib.import_module("orders.main")
