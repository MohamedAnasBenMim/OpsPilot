import json
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent


def test_prometheusrule_in_sync():
    import importlib.util

    spec = importlib.util.spec_from_file_location("render", ROOT / "scripts" / "render_prometheusrule.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.render() == (ROOT / "deploy/k8s/base/prometheusrule.yaml").read_text(encoding="utf-8")


def test_alerts_carry_service_label():
    groups = yaml.safe_load((ROOT / "observability/prometheus/rules/slo.yml").read_text())["groups"]
    alerts = [r for g in groups for r in g["rules"] if "alert" in r]
    assert alerts and all(r["labels"]["service"] == "orders-api" for r in alerts)


def test_grafana_dashboard_is_valid_json():
    dash = json.loads((ROOT / "observability/grafana/dashboards/orders-slo.json").read_text(encoding="utf-8"))
    assert dash["uid"] == "opspilot-slo" and len(dash["panels"]) >= 10
