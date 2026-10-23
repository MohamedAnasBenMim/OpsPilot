"""Génère le jeu de scénarios d'incidents SYNTHÉTIQUES (déterministe, graine fixe).

21 scénarios = 7 causes racines x 3 variantes, dont certaines contiennent des fausses pistes
(déploiement récent sans rapport, logs de timeout trompeurs, signaux manquants).
Ils servent à tester et comparer les moteurs de diagnostic en CI.

Pour des résultats à citer, enregistre de vrais incidents provoqués par chaos
(`opspilot record`, voir docs/chaos.md) et ajoute-les dans eval/real/.

    python eval/generate_scenarios.py
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

OUT = Path(__file__).parent / "scenarios"
T0 = datetime(2026, 10, 1, 14, 0, tzinfo=UTC)
STEP = 30  # secondes
N = 80  # 40 minutes
ALERT_MIN = 30  # l'alerte se déclenche à la minute 30
SVC = "orders-api"


class Builder:
    def __init__(self, sid: str, description: str, category: str, alert: str, seed: int):
        self.rng = random.Random(seed)
        self.sid, self.category = sid, category
        self.doc = {
            "id": sid,
            "description": description,
            "synthetic": True,
            "alert": {
                "name": alert,
                "service": SVC,
                "severity": "critical",
                "starts_at": (T0 + timedelta(minutes=ALERT_MIN)).isoformat(),
                "summary": description,
            },
            "metrics": {},
            "limits": {"memory_limit_mb": 512, "cpu_limit_cores": 1.0, "db_pool_max": 20},
            "logs": [],
            "events": [],
            "deploys": [],
        }
        self.key: list[str] = []
        self.baseline()

    def t(self, minute: float) -> str:
        return (T0 + timedelta(minutes=minute)).isoformat()

    def series(self, name: str, base: float, noise: float = 0.08, effect=None, key: bool = False) -> None:
        pts = []
        for i in range(N):
            minute = i * STEP / 60
            v = base * (1 + self.rng.gauss(0, noise))
            if effect:
                v = effect(minute, v)
            pts.append([T0.timestamp() + i * STEP, round(max(v, 0.0), 4)])
        self.doc["metrics"][name] = pts
        if key:
            self.key.append(f"metric:{name}")

    def baseline(self) -> None:
        self.series("request_rate", 50)
        self.series("error_rate", 0.004, noise=0.3)
        self.series("latency_p95_ms", 120)
        self.series("upstream_error_rate", 0.002, noise=0.3)
        self.series("upstream_latency_ms", 60)
        self.series("db_pool_in_use", 6, noise=0.15)
        self.series("memory_mb", 210, noise=0.03)
        self.series("cpu_cores", 0.3)
        self.series("restarts", 0, noise=0)

    def log(self, minute: float, level: str, msg: str, key: bool = False, repeat: int = 1, service: str = SVC) -> None:
        for r in range(repeat):
            self.doc["logs"].append({"ts": self.t(minute + r * 0.4), "level": level, "service": service, "msg": msg})
            if key and r == 0:
                self.key.append(f"log:{len(self.doc['logs']) - 1}")

    def event(self, minute: float, reason: str, message: str, kind: str = "Pod", key: bool = False) -> None:
        self.doc["events"].append({"ts": self.t(minute), "kind": kind, "name": f"{SVC}-7d9f-x2k", "reason": reason, "message": message})
        if key:
            self.key.append(f"event:{len(self.doc['events']) - 1}")

    def deploy(self, minute: float, version: str, previous: str, change: str, service: str = SVC, key: bool = False) -> None:
        self.doc["deploys"].append({"ts": self.t(minute), "service": service, "version": version, "previous": previous, "change": change})
        if key:
            self.key.append(f"deploy:{len(self.doc['deploys']) - 1}")

    def noise_logs(self) -> None:
        self.log(5, "INFO", "GET /orders 200 in 112ms")
        self.log(12, "WARN", "slow response from cache, retrying", service="orders-api")

    def save(self) -> None:
        self.noise_logs()
        self.doc["logs"].sort(key=lambda x: x["ts"])
        # recalcul des index de logs/événements après tri
        self.doc["ground_truth"] = {"category": self.category, "key_evidence": self._reindex()}
        OUT.mkdir(exist_ok=True)
        (OUT / f"{self.sid}.json").write_text(json.dumps(self.doc, ensure_ascii=False, indent=1), encoding="utf-8")

    def _reindex(self) -> list[str]:
        # les preuves clés de type log sont identifiées par leur message, plus robuste qu'un index
        keys = []
        for k in self.key:
            if k.startswith("log:"):
                continue
            keys.append(k)
        for i, line in enumerate(self.doc["logs"]):
            if line.get("_key"):
                keys.append(f"log:{i}")
        for line in self.doc["logs"]:
            line.pop("_key", None)
        return sorted(set(keys))

    def keylog(self, minute: float, level: str, msg: str, repeat: int = 1) -> None:
        for r in range(repeat):
            self.doc["logs"].append({"ts": self.t(minute + r * 0.4), "level": level, "service": SVC, "msg": msg, "_key": r == 0})


def step_up(start: float, factor: float, ramp: float = 1.0):
    def f(m, v):
        if m < start:
            return v
        return v * (1 + (factor - 1) * min(1.0, (m - start) / ramp))

    return f


def build() -> list[str]:
    ids = []

    # ---------- bad_deploy ----------
    b = Builder("s01_bad_deploy", "Pic d'erreurs 5xx juste après un déploiement", "bad_deploy", "OrdersHighErrorRate", 1)
    b.deploy(22, "v1.8.0", "v1.7.3", "refactor du calcul de remise", key=True)
    b.series("error_rate", 0.004, 0.3, step_up(22.5, 30), key=True)
    b.keylog(23, "ERROR", "TypeError: unsupported operand type(s) for -: 'NoneType' and 'float' in apply_discount", repeat=4)
    b.save()
    ids.append(b.sid)

    b = Builder("s02_bad_deploy_noisy", "Erreurs après déploiement, avec des timeouts sans rapport", "bad_deploy", "OrdersHighErrorRate", 2)
    b.deploy(20, "v2.1.0", "v2.0.4", "nouvelle validation des paniers", key=True)
    b.series("error_rate", 0.004, 0.3, step_up(20.5, 18), key=True)
    b.keylog(21, "ERROR", "KeyError: 'shipping_zone' in validate_cart", repeat=3)
    b.log(24, "WARN", "request to recommendations timed out after 2s")
    b.save()
    ids.append(b.sid)

    b = Builder("s03_bad_deploy_latency", "Régression de latence après déploiement (requête N+1)", "bad_deploy", "OrdersHighLatency", 3)
    b.deploy(21, "v3.0.0", "v2.9.1", "ORM : chargement paresseux des lignes de commande", key=True)
    b.deploy(15, "v5.2.0", "v5.1.9", "mise à jour mineure", service="payments-api")
    b.series("latency_p95_ms", 120, 0.08, step_up(21.5, 7), key=True)
    b.series("error_rate", 0.004, 0.3, step_up(23, 4))
    b.keylog(22, "ERROR", "Exception: query count exceeded budget (412 queries) in list_orders")
    b.save()
    ids.append(b.sid)

    # ---------- oom_kill ----------
    b = Builder("s04_oom", "Fuite mémoire puis OOMKilled", "oom_kill", "OrdersPodRestarting", 4)
    b.series("memory_mb", 210, 0.02, lambda m, v: v + max(0, m - 12) * 18 if m < 29 else 505, key=True)
    b.series("restarts", 0, 0, lambda m, v: 0 if m < 29 else 2, key=True)
    b.event(29, "OOMKilled", "Container orders-api exceeded memory limit (512Mi)", key=True)
    b.keylog(28.5, "ERROR", "MemoryError: cannot allocate 64 MiB for report buffer")
    b.save()
    ids.append(b.sid)

    b = Builder("s05_oom_after_deploy", "OOMKilled avec un déploiement 25 min avant (fausse piste)", "oom_kill", "OrdersPodRestarting", 5)
    b.deploy(5, "v1.9.1", "v1.9.0", "correctif de traduction")
    b.series("memory_mb", 210, 0.02, lambda m, v: v + max(0, m - 15) * 22 if m < 29 else 508, key=True)
    b.series("restarts", 0, 0, lambda m, v: 0 if m < 29 else 1, key=True)
    b.series("error_rate", 0.004, 0.3, step_up(29, 6))
    b.event(29.2, "OOMKilled", "Container orders-api exceeded memory limit (512Mi)", key=True)
    b.save()
    ids.append(b.sid)

    b = Builder("s06_oom_no_metrics", "OOMKilled, métrique mémoire absente (scrape raté)", "oom_kill", "OrdersPodRestarting", 6)
    del b.doc["metrics"]["memory_mb"]
    b.series("restarts", 0, 0, lambda m, v: 0 if m < 28 else 3, key=True)
    b.event(28, "OOMKilled", "Container orders-api exceeded memory limit (512Mi)", key=True)
    b.event(28.5, "BackOff", "Back-off restarting failed container")
    b.save()
    ids.append(b.sid)

    # ---------- config_error ----------
    b = Builder("s07_config", "CrashLoopBackOff après modification de ConfigMap", "config_error", "OrdersUnavailable", 7)
    b.event(24, "ConfigMapUpdated", "ConfigMap orders-config updated", kind="ConfigMap", key=True)
    b.event(25, "BackOff", "Back-off restarting failed container", key=True)
    b.series("restarts", 0, 0, lambda m, v: 0 if m < 25 else 4, key=True)
    b.series("error_rate", 0.004, 0.3, step_up(25, 60))
    b.keylog(24.8, "FATAL", "startup failed: missing env variable PAYMENTS_URL", repeat=3)
    b.save()
    ids.append(b.sid)

    b = Builder(
        "s08_config_via_deploy", "Crash au démarrage après un déploiement qui change la config", "config_error", "OrdersUnavailable", 8
    )
    b.deploy(24, "v4.0.0", "v3.9.2", "nouvelle variable DB_SSL_MODE", key=True)
    b.event(24.5, "BackOff", "Back-off restarting failed container", key=True)
    b.series("restarts", 0, 0, lambda m, v: 0 if m < 24.5 else 5, key=True)
    b.series("error_rate", 0.004, 0.3, step_up(25, 40))
    b.keylog(24.4, "FATAL", "invalid config: DB_SSL_MODE not set", repeat=3)
    b.save()
    ids.append(b.sid)

    b = Builder("s09_secret", "Secret mis à jour, chargement de configuration en échec", "config_error", "OrdersUnavailable", 9)
    b.event(23, "SecretUpdated", "Secret orders-db-credentials updated", kind="Secret", key=True)
    b.event(23.5, "CrashLoopBackOff", "Back-off restarting failed container", key=True)
    b.series("restarts", 0, 0, lambda m, v: 0 if m < 23.5 else 3, key=True)
    b.keylog(23.4, "ERROR", "failed to load config: secret key 'password' not found")
    b.save()
    ids.append(b.sid)

    # ---------- dependency_down ----------
    b = Builder("s10_dependency", "Service de paiement injoignable", "dependency_down", "OrdersHighErrorRate", 10)
    b.series("upstream_error_rate", 0.002, 0.3, step_up(24, 300), key=True)
    b.series("error_rate", 0.004, 0.3, step_up(24, 25))
    b.keylog(24.2, "ERROR", "POST http://payments-api/charge failed: connection refused", repeat=5)
    b.save()
    ids.append(b.sid)

    b = Builder(
        "s11_dependency_deploy_herring",
        "Paiements en panne ; un déploiement d'orders-api 20 min avant (fausse piste)",
        "dependency_down",
        "OrdersHighErrorRate",
        11,
    )
    b.deploy(10, "v2.3.1", "v2.3.0", "mise à jour des dépendances de logging")
    b.deploy(23, "v7.0.0", "v6.9.0", "migration base paiements", service="payments-api")
    b.series("upstream_error_rate", 0.002, 0.3, step_up(23.5, 250), key=True)
    b.series("error_rate", 0.004, 0.3, step_up(24, 20))
    b.keylog(24, "ERROR", "upstream payments-api returned 503 Service Unavailable", repeat=4)
    b.save()
    ids.append(b.sid)

    b = Builder(
        "s12_dependency_breaker",
        "Disjoncteur ouvert vers le service stock, sans métrique amont",
        "dependency_down",
        "OrdersHighErrorRate",
        12,
    )
    del b.doc["metrics"]["upstream_error_rate"]
    b.series("error_rate", 0.004, 0.3, step_up(25, 15), key=True)
    b.keylog(25, "ERROR", "circuit breaker open for inventory-api after 20 consecutive failures", repeat=3)
    b.save()
    ids.append(b.sid)

    # ---------- db_saturation ----------
    b = Builder("s13_db_pool", "Pool de connexions DB épuisé", "db_saturation", "OrdersHighLatency", 13)
    b.series("db_pool_in_use", 6, 0.1, lambda m, v: v if m < 23 else 20, key=True)
    b.series("latency_p95_ms", 120, 0.08, step_up(23, 9))
    b.keylog(23.5, "ERROR", "timeout acquiring connection from pool after 5000ms", repeat=4)
    b.save()
    ids.append(b.sid)

    b = Builder("s14_db_pool_traffic", "Pool DB saturé avec légère hausse de trafic", "db_saturation", "OrdersHighLatency", 14)
    b.series("request_rate", 50, 0.08, step_up(21, 1.5))
    b.series("db_pool_in_use", 6, 0.1, lambda m, v: v if m < 22 else 19.5, key=True)
    b.series("latency_p95_ms", 120, 0.08, step_up(22, 6))
    b.keylog(22.5, "WARN", "connection pool exhausted, 34 requests waiting")
    b.save()
    ids.append(b.sid)

    b = Builder("s15_db_max_connections", "Base de données : trop de connexions", "db_saturation", "OrdersHighErrorRate", 15)
    b.doc["limits"].pop("db_pool_max")
    b.series("error_rate", 0.004, 0.3, step_up(24, 12))
    b.keylog(24, "ERROR", "psycopg.OperationalError: FATAL: sorry, too many connections for role orders", repeat=3)
    b.save()
    ids.append(b.sid)

    # ---------- cpu_saturation ----------
    b = Builder("s16_cpu_traffic", "Pic de trafic x4, CPU à la limite", "cpu_saturation", "OrdersHighLatency", 16)
    b.series("request_rate", 50, 0.08, step_up(22, 4), key=True)
    b.series("cpu_cores", 0.3, 0.05, lambda m, v: v if m < 22 else 0.99, key=True)
    b.series("latency_p95_ms", 120, 0.08, step_up(22.5, 5))
    b.save()
    ids.append(b.sid)

    b = Builder("s17_cpu_batch", "CPU saturé par un traitement lourd, timeouts trompeurs", "cpu_saturation", "OrdersHighLatency", 17)
    b.series("cpu_cores", 0.3, 0.05, lambda m, v: v if m < 23 else 0.98, key=True)
    b.series("latency_p95_ms", 120, 0.08, step_up(23, 8))
    b.log(25, "WARN", "request to recommendations timed out after 2s", repeat=2)
    b.save()
    ids.append(b.sid)

    b = Builder("s18_cpu_throttle", "Throttling CPU et quelques 5xx", "cpu_saturation", "OrdersHighLatency", 18)
    b.series("request_rate", 50, 0.08, step_up(20, 2.5), key=True)
    b.series("cpu_cores", 0.3, 0.05, lambda m, v: v if m < 20 else 0.95, key=True)
    b.series("latency_p95_ms", 120, 0.08, step_up(21, 6))
    b.series("error_rate", 0.004, 0.3, step_up(23, 3))
    b.save()
    ids.append(b.sid)

    # ---------- network_latency ----------
    b = Builder("s19_network", "Latence réseau vers le service de paiement", "network_latency", "OrdersHighLatency", 19)
    b.series("upstream_latency_ms", 60, 0.08, step_up(24, 8), key=True)
    b.series("latency_p95_ms", 120, 0.08, step_up(24, 4))
    b.keylog(24.5, "WARN", "payments-api call timed out after 800ms, retrying", repeat=3)
    b.save()
    ids.append(b.sid)

    b = Builder("s20_network_silent", "Latence amont sans aucun log", "network_latency", "OrdersHighLatency", 20)
    b.series("upstream_latency_ms", 60, 0.08, step_up(23, 10), key=True)
    b.series("latency_p95_ms", 120, 0.08, step_up(23, 5))
    b.save()
    ids.append(b.sid)

    b = Builder(
        "s21_network_with_errors",
        "Latence réseau provoquant des 504, déploiement d'un autre service",
        "network_latency",
        "OrdersHighLatency",
        21,
    )
    b.deploy(22, "v1.1.0", "v1.0.9", "nouvelle image", service="recommendations")
    b.series("upstream_latency_ms", 60, 0.08, step_up(24, 12), key=True)
    b.series("latency_p95_ms", 120, 0.08, step_up(24, 5))
    b.series("error_rate", 0.004, 0.3, step_up(25, 5))
    b.keylog(25, "ERROR", "deadline exceeded calling payments-api (504)", repeat=2)
    b.save()
    ids.append(b.sid)
    return ids


if __name__ == "__main__":
    print(f"{len(build())} scénarios écrits dans {OUT}")
