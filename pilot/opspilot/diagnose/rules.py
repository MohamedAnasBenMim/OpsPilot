"""Moteur de diagnostic par règles pondérées (baseline hors ligne, sans LLM).

Chaque cause racine est une somme de signaux pondérés (positifs ou négatifs).
Les signaux qui contribuent le plus sont cités comme preuves. Ce moteur sert
de référence dans l'évaluation et de repli si le LLM est indisponible.
"""

from __future__ import annotations

import math

from ..models import Category, Hypothesis
from ..signals import Signals

# signal -> poids ; un signal vaut entre 0 et 1 (normalisé ci-dessous)
RULES: dict[Category, dict[str, float]] = {
    Category.BAD_DEPLOY: {
        "recent_deploy": 3.0,
        "log_exception": 1.5,
        "error_rate_up": 1.0,
        "latency_p95_ms_up": 0.5,
        "log_upstream": -1.5,
        "log_db_pool": -1.5,
        "event_oom": -2.0,
        "log_config": -1.0,
    },
    Category.OOM_KILL: {"event_oom": 3.0, "memory_saturated": 2.0, "memory_growing": 1.5, "log_oom": 1.5, "restarts": 0.5},
    Category.CONFIG_ERROR: {
        "event_crashloop": 2.0,
        "log_config": 2.5,
        "event_config_change": 1.5,
        "restarts": 0.5,
        "recent_deploy": 0.3,
        "event_oom": -2.0,
        "memory_saturated": -1.0,
    },
    Category.DEPENDENCY_DOWN: {
        "log_upstream": 2.5,
        "upstream_error_rate_up": 2.0,
        "error_rate_up": 0.5,
        "cpu_saturated": -1.0,
        "db_pool_saturated": -1.0,
    },
    Category.DB_SATURATION: {"db_pool_saturated": 3.0, "log_db_pool": 2.5, "latency_p95_ms_up": 0.5, "db_pool_in_use_up": 1.0},
    Category.CPU_SATURATION: {
        "cpu_saturated": 3.0,
        "request_rate_up": 1.5,
        "latency_p95_ms_up": 0.5,
        "cpu_cores_up": 0.5,
        "db_pool_saturated": -1.0,
    },
    Category.NETWORK_LATENCY: {
        "upstream_latency_ms_up": 2.5,
        "log_timeout": 1.5,
        "latency_p95_ms_up": 0.5,
        "error_rate_up": -0.5,
        "cpu_saturated": -1.0,
        "log_upstream": -0.5,
    },
}

THRESHOLD = 1.5  # en dessous, la cause est jugée indéterminée


def _norm(signal: str, value: float) -> float:
    if signal.endswith("_up"):  # ratios : ×1.8 -> 0, ×5 et plus -> 1
        return max(0.0, min(1.0, (value - 1.8) / 3.2)) * 0.5 + 0.5
    if signal.startswith(("log_", "event_")) or signal == "restarts":
        return min(1.0, 0.6 + 0.2 * value)  # 1 occurrence -> 0.8, 2+ -> 1
    return min(1.0, value)


def score(sig: Signals) -> list[Hypothesis]:
    raw: list[tuple[Category, float, list[str]]] = []
    for cat, weights in RULES.items():
        total, contributions = 0.0, []
        for signal, w in weights.items():
            v = sig.get(signal)
            if v <= 0:
                continue
            c = w * _norm(signal, v)
            total += c
            if c > 0:
                contributions.append((c, signal))
        evidence = [eid for _, sname in sorted(contributions, reverse=True) for eid in sig.refs.get(sname, [])]
        raw.append((cat, total, list(dict.fromkeys(evidence))))

    raw.sort(key=lambda r: r[1], reverse=True)
    exps = [math.exp(max(t, 0)) for _, t, _ in raw]
    z = sum(exps)
    hyps = [Hypothesis(category=c, confidence=round(e / z, 3), evidence=ev[:5]) for (c, _, ev), e in zip(raw, exps)]
    if raw[0][1] < THRESHOLD:
        hyps.insert(0, Hypothesis(category=Category.UNKNOWN, confidence=0.0, evidence=[]))
    return hyps
