"""Modèles de données partagés par la collecte, le diagnostic, l'API et l'évaluation.

Un *snapshot* est une photo de la télémétrie autour d'une alerte : séries de métriques,
logs, événements Kubernetes et déploiements récents. Il peut venir d'un cluster réel
(`sources.live`) ou d'un fichier JSON (`sources.fixture`), ce qui rend le diagnostic
rejouable et mesurable.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Category(StrEnum):
    """Taxonomie des causes racines reconnues par l'agent."""

    BAD_DEPLOY = "bad_deploy"
    OOM_KILL = "oom_kill"
    CONFIG_ERROR = "config_error"
    DEPENDENCY_DOWN = "dependency_down"
    DB_SATURATION = "db_saturation"
    CPU_SATURATION = "cpu_saturation"
    NETWORK_LATENCY = "network_latency"
    UNKNOWN = "unknown"


CATEGORY_LABELS = {
    Category.BAD_DEPLOY: "Régression introduite par un déploiement",
    Category.OOM_KILL: "Dépassement mémoire (OOMKilled)",
    Category.CONFIG_ERROR: "Erreur de configuration au démarrage",
    Category.DEPENDENCY_DOWN: "Dépendance externe indisponible",
    Category.DB_SATURATION: "Saturation du pool de connexions base de données",
    Category.CPU_SATURATION: "Saturation CPU / pic de charge",
    Category.NETWORK_LATENCY: "Latence réseau vers une dépendance",
    Category.UNKNOWN: "Cause indéterminée",
}

REMEDIATIONS = {
    Category.BAD_DEPLOY: "Revenir à la version précédente (argocd app rollback / kubectl rollout undo), puis analyser le diff.",
    Category.OOM_KILL: "Augmenter temporairement la limite mémoire et chercher la fuite (profilage, heap dump).",
    Category.CONFIG_ERROR: "Corriger la ConfigMap/Secret fautive et relancer le déploiement ; ajouter une validation en CI.",
    Category.DEPENDENCY_DOWN: "Activer le disjoncteur / mode dégradé et contacter l'équipe propriétaire de la dépendance.",
    Category.DB_SATURATION: "Augmenter la taille du pool ou la capacité DB ; chercher les requêtes lentes ou les connexions non libérées.",
    Category.CPU_SATURATION: "Scaler horizontalement (HPA) et vérifier l'origine du pic de trafic.",
    Category.NETWORK_LATENCY: "Vérifier le réseau et la dépendance distante ; ajuster timeouts et retries.",
    Category.UNKNOWN: "Escalader à l'astreinte avec les éléments collectés.",
}


class Alert(BaseModel):
    name: str
    service: str
    severity: str = "warning"
    starts_at: datetime
    summary: str = ""
    labels: dict[str, str] = Field(default_factory=dict)


class LogLine(BaseModel):
    ts: datetime
    level: str = "INFO"
    service: str = ""
    msg: str


class K8sEvent(BaseModel):
    ts: datetime
    kind: str = "Pod"
    name: str = ""
    reason: str
    message: str = ""


class Deploy(BaseModel):
    ts: datetime
    service: str
    version: str
    previous: str | None = None
    change: str = ""


class Snapshot(BaseModel):
    id: str = "live"
    description: str = ""
    synthetic: bool = False
    alert: Alert
    # nom de série -> liste de [timestamp unix, valeur]
    metrics: dict[str, list[tuple[float, float]]] = Field(default_factory=dict)
    # limites connues (ex. memory_limit_mb, cpu_limit_cores, db_pool_max)
    limits: dict[str, float] = Field(default_factory=dict)
    logs: list[LogLine] = Field(default_factory=list)
    events: list[K8sEvent] = Field(default_factory=list)
    deploys: list[Deploy] = Field(default_factory=list)
    ground_truth: dict | None = None


class Evidence(BaseModel):
    id: str  # ex. "metric:error_rate", "log:3", "event:1", "deploy:0"
    kind: str
    text: str


class Hypothesis(BaseModel):
    category: Category
    confidence: float
    evidence: list[str] = Field(default_factory=list)


class Diagnosis(BaseModel):
    incident_id: str
    alert: Alert
    root_cause: Category
    title: str
    summary: str
    confidence: float
    hypotheses: list[Hypothesis]
    evidence: list[Evidence]
    remediation: str
    engine: str
    duration_ms: float
