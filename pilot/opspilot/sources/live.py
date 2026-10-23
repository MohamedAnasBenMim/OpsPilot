"""Collecte en direct : Prometheus (métriques), Loki (logs), API Kubernetes (événements, déploiements).

Chaque source est optionnelle : si elle n'est pas configurée ou ne répond pas,
le snapshot est construit avec ce qui est disponible (le diagnostic le signale).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from ..config import settings
from ..models import Alert, Deploy, K8sEvent, LogLine, Snapshot

log = logging.getLogger(__name__)

# Nom de série -> requête PromQL ({svc} est remplacé par le nom du service = job Prometheus)
QUERIES: dict[str, str] = {
    "request_rate": 'sum(rate(http_requests_total{{job="{svc}"}}[1m]))',
    # "or vector(0)" : sans aucune erreur, la série 5xx n'existe pas encore et la requête renverrait du vide
    "error_rate": '(sum(rate(http_requests_total{{job="{svc}",status=~"5.."}}[1m])) or vector(0)) / clamp_min(sum(rate(http_requests_total{{job="{svc}"}}[1m])), 1e-9)',
    "latency_p95_ms": '1000 * histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{{job="{svc}"}}[1m])))',
    "upstream_error_rate": '(sum(rate(upstream_requests_total{{job="{svc}",outcome="error"}}[1m])) or vector(0)) / clamp_min(sum(rate(upstream_requests_total{{job="{svc}"}}[1m])), 1e-9)',
    "upstream_latency_ms": '1000 * histogram_quantile(0.95, sum by (le) (rate(upstream_request_duration_seconds_bucket{{job="{svc}"}}[1m])))',
    "db_pool_in_use": 'max(db_pool_in_use{{job="{svc}"}})',
    "memory_mb": 'max(process_resident_memory_bytes{{job="{svc}"}}) / 1048576',
    "cpu_cores": 'sum(rate(process_cpu_seconds_total{{job="{svc}"}}[1m]))',
    "restarts": 'sum(kube_pod_container_status_restarts_total{{container="{svc}"}})',
}
LIMIT_QUERIES: dict[str, str] = {
    "db_pool_max": 'max(db_pool_max{{job="{svc}"}})',
    "memory_limit_mb": 'max(kube_pod_container_resource_limits{{container="{svc}",resource="memory"}}) / 1048576',
    "cpu_limit_cores": 'sum(kube_pod_container_resource_limits{{container="{svc}",resource="cpu"}})',
}

SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")


class Prometheus:
    def __init__(self, url: str, client: httpx.Client | None = None):
        self.url = url.rstrip("/")
        self.client = client or httpx.Client(timeout=10)

    def range(self, query: str, start: datetime, end: datetime, step: int = 30) -> list[tuple[float, float]]:
        r = self.client.get(
            f"{self.url}/api/v1/query_range", params={"query": query, "start": start.timestamp(), "end": end.timestamp(), "step": step}
        )
        r.raise_for_status()
        result = r.json()["data"]["result"]
        if not result:
            return []
        return [(float(t), float(v)) for t, v in result[0]["values"] if v not in ("NaN", "+Inf", "-Inf")]

    def instant(self, query: str) -> float | None:
        r = self.client.get(f"{self.url}/api/v1/query", params={"query": query})
        r.raise_for_status()
        result = r.json()["data"]["result"]
        return float(result[0]["value"][1]) if result else None


class Loki:
    def __init__(self, url: str, client: httpx.Client | None = None):
        self.url = url.rstrip("/")
        self.client = client or httpx.Client(timeout=10)

    def errors(self, service: str, start: datetime, end: datetime, limit: int = 200) -> list[LogLine]:
        selector = settings.loki_selector.replace("$SVC", service)
        query = f'{selector} |~ "(?i)(error|exception|warn|fatal|refused|timeout|memory)"'
        r = self.client.get(
            f"{self.url}/loki/api/v1/query_range",
            params={
                "query": query,
                "start": int(start.timestamp() * 1e9),
                "end": int(end.timestamp() * 1e9),
                "limit": limit,
                "direction": "forward",
            },
        )
        r.raise_for_status()
        lines = []
        for stream in r.json()["data"]["result"]:
            labels = stream.get("stream", {})
            for ts, line in stream["values"]:
                level = labels.get("level") or ("ERROR" if "error" in line.lower() else "WARN")
                lines.append(LogLine(ts=datetime.fromtimestamp(int(ts) / 1e9, UTC), level=level.upper(), service=service, msg=line[:500]))
        return sorted(lines, key=lambda x: x.ts)


class Kubernetes:
    def __init__(self, api: str = "", client: httpx.Client | None = None):
        if api:
            self.api, headers, verify = api.rstrip("/"), {}, True
        else:  # in-cluster
            self.api = "https://kubernetes.default.svc"
            headers = {"Authorization": f"Bearer {(SA_DIR / 'token').read_text()}"}
            verify = str(SA_DIR / "ca.crt")
        self.client = client or httpx.Client(timeout=10, headers=headers, verify=verify)

    def events(self, namespace: str, since: datetime) -> list[K8sEvent]:
        r = self.client.get(f"{self.api}/api/v1/namespaces/{namespace}/events")
        r.raise_for_status()
        out = []
        for e in r.json().get("items", []):
            ts = e.get("lastTimestamp") or e.get("eventTime") or e["metadata"]["creationTimestamp"]
            when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if when >= since:
                obj = e.get("involvedObject", {})
                out.append(
                    K8sEvent(
                        ts=when,
                        kind=obj.get("kind", ""),
                        name=obj.get("name", ""),
                        reason=e.get("reason", ""),
                        message=e.get("message", ""),
                    )
                )
        return sorted(out, key=lambda x: x.ts)

    def deploys(self, namespace: str, since: datetime) -> list[Deploy]:
        """Déploiements = ReplicaSets créés récemment (une nouvelle révision par rollout)."""
        r = self.client.get(f"{self.api}/apis/apps/v1/namespaces/{namespace}/replicasets")
        r.raise_for_status()
        out = []
        for rs in r.json().get("items", []):
            meta = rs["metadata"]
            created = datetime.fromisoformat(meta["creationTimestamp"].replace("Z", "+00:00"))
            owner = next((o["name"] for o in meta.get("ownerReferences", []) if o["kind"] == "Deployment"), None)
            if owner and created >= since:
                images = [c["image"] for c in rs["spec"]["template"]["spec"]["containers"]]
                rev = meta.get("annotations", {}).get("deployment.kubernetes.io/revision", "?")
                out.append(Deploy(ts=created, service=owner, version=f"rev{rev} ({', '.join(images)})"))
        return sorted(out, key=lambda x: x.ts)


def collect(alert: Alert, extra_deploys: list[Deploy] | None = None) -> Snapshot:
    end = datetime.now(UTC)
    start = min(alert.starts_at, end) - timedelta(minutes=settings.lookback_minutes)
    snap = Snapshot(id=f"live-{alert.service}-{int(alert.starts_at.timestamp())}", alert=alert)
    svc = alert.service

    try:
        prom = Prometheus(settings.prometheus_url)
        for name, q in QUERIES.items():
            series = prom.range(q.format(svc=svc), start, end)
            if series:
                snap.metrics[name] = series
        for name, q in LIMIT_QUERIES.items():
            v = prom.instant(q.format(svc=svc))
            if v:
                snap.limits[name] = v
    except Exception as exc:  # noqa: BLE001
        log.warning("Prometheus indisponible : %s", exc)

    if settings.loki_url:
        try:
            snap.logs = Loki(settings.loki_url).errors(svc, start, end)
        except Exception as exc:  # noqa: BLE001
            log.warning("Loki indisponible : %s", exc)

    if settings.kubernetes_api or SA_DIR.exists():
        try:
            k8s = Kubernetes(settings.kubernetes_api)
            snap.events = k8s.events(settings.namespace, start)
            snap.deploys = k8s.deploys(settings.namespace, start)
        except Exception as exc:  # noqa: BLE001
            log.warning("API Kubernetes indisponible : %s", exc)

    snap.deploys += [d for d in (extra_deploys or []) if d.ts >= start]
    snap.deploys.sort(key=lambda d: d.ts)
    return snap
