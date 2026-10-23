"""Extraction de signaux et de preuves à partir d'un snapshot.

Chaque signal notable produit une *preuve* identifiée (ex. `metric:error_rate`, `log:4`),
que le diagnostic doit citer. C'est ce qui rend le raisonnement de l'agent vérifiable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta
from statistics import median

from .models import Evidence, Snapshot

BASELINE_GAP = timedelta(minutes=12)  # la ligne de base s'arrête 12 min avant l'alerte
INCIDENT_LOOKBACK = timedelta(minutes=10)  # la fenêtre d'incident commence 10 min avant l'alerte
ONGOING_WINDOW = timedelta(minutes=3)  # une anomalie doit être encore présente autour de l'alerte
DEPLOY_WINDOW = timedelta(minutes=30)  # un déploiement est "suspect" s'il précède l'alerte de moins de 30 min

LOG_PATTERNS: dict[str, re.Pattern] = {
    "exception": re.compile(r"traceback|exception|nullpointer|keyerror|typeerror|attributeerror|panic", re.I),
    "oom": re.compile(r"out ?of ?memory|memoryerror|oom|heap", re.I),
    "config": re.compile(r"missing (env|config|variable)|invalid config|config(uration)? error|not set|failed to load config", re.I),
    "upstream": re.compile(r"connection refused|upstream|503 from|unreachable|circuit (breaker )?open|no route to host", re.I),
    "db_pool": re.compile(r"pool (exhausted|timeout)|timeout acquiring|too many connections|connection pool", re.I),
    "timeout": re.compile(r"timed? ?out|deadline exceeded|slow response", re.I),
}

EVENT_CLASSES = {
    "OOMKilled": "oom",
    "BackOff": "crashloop",
    "CrashLoopBackOff": "crashloop",
    "Unhealthy": "unhealthy",
    "ConfigMapUpdated": "config_change",
    "SecretUpdated": "config_change",
}


@dataclass
class Signals:
    """Indicateurs numériques et preuves associées, prêts pour les règles ou pour un LLM."""

    values: dict[str, float] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)
    refs: dict[str, list[str]] = field(default_factory=dict)  # signal -> ids de preuves

    def add(self, signal: str, value: float, ev: Evidence | None = None) -> None:
        self.values[signal] = max(value, self.values.get(signal, 0.0))
        if ev is not None:
            if ev.id not in {e.id for e in self.evidence}:
                self.evidence.append(ev)
            self.refs.setdefault(signal, [])
            if ev.id not in self.refs[signal]:
                self.refs[signal].append(ev.id)

    def get(self, signal: str) -> float:
        return self.values.get(signal, 0.0)


# en dessous de ces valeurs, une hausse relative n'est pas significative (ex. 0 -> 0,1 % d'erreurs)
MIN_PEAK = {
    "error_rate": 0.01,
    "upstream_error_rate": 0.01,
    "latency_p95_ms": 50,
    "upstream_latency_ms": 50,
    "request_rate": 1,
    "cpu_cores": 0.05,
    "memory_mb": 20,
    "db_pool_in_use": 5,
}


def _split(series: list[tuple[float, float]], alert_ts: float) -> tuple[list[float], list[float]]:
    base = [v for t, v in series if t <= alert_ts - BASELINE_GAP.total_seconds()]
    inc = [v for t, v in series if t >= alert_ts - INCIDENT_LOOKBACK.total_seconds()]
    if len(base) < 3:
        # historique trop court (service récent, rétention faible) : on prend le premier tiers
        # des points antérieurs à l'alerte comme ligne de base, plutôt que de supposer une base nulle
        before = [v for t, v in series if t < alert_ts]
        base = before[: max(3, len(before) // 3)] if len(before) >= 6 else base
    return base, inc


def _fmt(v: float) -> str:
    return f"{v:.3g}"


def extract(snap: Snapshot) -> Signals:
    s = Signals()
    alert_ts = snap.alert.starts_at.timestamp()
    svc = snap.alert.service

    # ---------- métriques ----------
    for name, series in snap.metrics.items():
        base, inc = _split(series, alert_ts)
        if not inc:
            continue
        b = median(base) if base else 0.0  # médiane : robuste à un incident antérieur dans la ligne de base
        peak = max(inc)
        ongoing = [v for t, v in series if t >= alert_ts - ONGOING_WINDOW.total_seconds()] or inc
        if name == "restarts":
            delta = peak - (base[-1] if base else min(inc))
            if delta >= 1:
                s.add(
                    "restarts",
                    delta,
                    Evidence(id="metric:restarts", kind="metric", text=f"{int(delta)} redémarrage(s) du conteneur pendant l'incident"),
                )
            continue
        if not base:
            s.values[f"{name}_peak"] = peak  # pas de ligne de base : seules les limites absolues s'appliquent
            continue
        ratio = peak / b if b > 0 else (10.0 if peak > 0 else 1.0)
        s.values[f"{name}_ratio"] = ratio
        s.values[f"{name}_peak"] = peak
        # l'anomalie doit être significative ET toujours active au moment de l'alerte :
        # un incident précédent déjà résorbé ne doit pas être pris pour la cause
        still_active = max(ongoing) >= (1.8 * b if b > 0 else MIN_PEAK.get(name, 0))
        if ratio >= 1.8 and peak >= MIN_PEAK.get(name, 0) and still_active:
            change = f"×{ratio:.1f}" if b > 0 else "apparition"
            s.add(f"{name}_up", ratio, Evidence(id=f"metric:{name}", kind="metric", text=f"{name} : {_fmt(b)} → {_fmt(peak)} ({change})"))
        # tendance monotone (utile pour la mémoire)
        if name == "memory_mb" and len(inc) >= 4 and all(x <= y for x, y in zip(inc, inc[1:])) and inc[-1] > inc[0] * 1.3:
            s.add(
                "memory_growing",
                1.0,
                Evidence(id="metric:memory_mb", kind="metric", text=f"mémoire en hausse continue : {_fmt(inc[0])} → {_fmt(inc[-1])} Mo"),
            )

    # saturation par rapport aux limites connues
    for metric, limit_key, signal in [
        ("memory_mb", "memory_limit_mb", "memory_saturated"),
        ("cpu_cores", "cpu_limit_cores", "cpu_saturated"),
        ("db_pool_in_use", "db_pool_max", "db_pool_saturated"),
    ]:
        peak, limit = s.values.get(f"{metric}_peak"), snap.limits.get(limit_key)
        if peak is not None and limit and peak >= 0.9 * limit:
            s.add(
                signal,
                peak / limit,
                Evidence(
                    id=f"metric:{metric}", kind="metric", text=f"{metric} à {peak / limit:.0%} de la limite ({_fmt(peak)}/{_fmt(limit)})"
                ),
            )

    # ---------- logs ----------
    window_start = alert_ts - INCIDENT_LOOKBACK.total_seconds()
    seen: set[tuple[str, str]] = set()
    for i, log in enumerate(snap.logs):
        if log.ts.timestamp() < window_start or log.level.upper() not in {"ERROR", "FATAL", "CRITICAL", "WARN", "WARNING"}:
            continue
        for cls, pat in LOG_PATTERNS.items():
            if pat.search(log.msg):
                template = re.sub(r"\d+", "#", log.msg)[:80]
                if (cls, template) in seen:
                    s.values[f"log_{cls}"] = s.get(f"log_{cls}") + 1
                    continue
                seen.add((cls, template))
                s.add(
                    f"log_{cls}",
                    s.get(f"log_{cls}") + 1,
                    Evidence(id=f"log:{i}", kind="log", text=f"[{log.level}] {log.service}: {log.msg}"),
                )

    # ---------- événements Kubernetes ----------
    for i, ev in enumerate(snap.events):
        if ev.ts.timestamp() < alert_ts - DEPLOY_WINDOW.total_seconds():
            continue
        cls = EVENT_CLASSES.get(ev.reason)
        if cls:
            s.add(
                f"event_{cls}",
                s.get(f"event_{cls}") + 1,
                Evidence(id=f"event:{i}", kind="event", text=f"{ev.reason} sur {ev.kind}/{ev.name} : {ev.message}"),
            )

    # ---------- déploiements ----------
    for i, d in enumerate(snap.deploys):
        age = alert_ts - d.ts.timestamp()
        if 0 <= age <= DEPLOY_WINDOW.total_seconds():
            signal = "recent_deploy" if d.service == svc else "recent_deploy_other"
            minutes = age / 60
            s.add(
                signal,
                1.0 / (1 + minutes / 10),
                Evidence(
                    id=f"deploy:{i}",
                    kind="deploy",
                    text=f"Déploiement {d.service} {d.previous or '?'} → {d.version}, {minutes:.0f} min avant l'alerte. {d.change}".strip(),
                ),
            )
    return s
