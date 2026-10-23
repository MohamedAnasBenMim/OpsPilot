"""Génère la ressource Kubernetes PrometheusRule à partir de observability/prometheus/rules/slo.yml.

Une seule source de vérité pour les règles d'alerte (docker compose et Kubernetes).
La CI vérifie que le fichier généré est à jour.

    python scripts/render_prometheusrule.py
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "observability" / "prometheus" / "rules" / "slo.yml"
DST = ROOT / "deploy" / "k8s" / "base" / "prometheusrule.yaml"


def render() -> str:
    groups = yaml.safe_load(SRC.read_text(encoding="utf-8"))["groups"]
    doc = {
        "apiVersion": "monitoring.coreos.com/v1",
        "kind": "PrometheusRule",
        "metadata": {"name": "orders-api-slo", "namespace": "shop"},
        "spec": {"groups": groups},
    }
    header = "# FICHIER GÉNÉRÉ par scripts/render_prometheusrule.py depuis observability/prometheus/rules/slo.yml — ne pas éditer\n"
    return header + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=200)


if __name__ == "__main__":
    DST.write_text(render(), encoding="utf-8")
    print(f"écrit : {DST.relative_to(ROOT)}")
