"""Service OpsPilot : reçoit les alertes d'Alertmanager, diagnostique, notifie.

Endpoints :
- POST /webhook/alertmanager : webhook Alertmanager (alertes en cours)
- POST /deploys              : enregistre un déploiement (appelé par la CI/CD)
- GET  /incidents            : liste des diagnostics
- GET  /incidents/{id}       : rapport Markdown
- GET  /metrics              : métriques Prometheus de l'agent lui-même
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from datetime import UTC, datetime

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel

from . import __version__
from .config import settings
from .diagnose import diagnose
from .models import Alert, Deploy, Diagnosis
from .report import chat_message, markdown
from .sources import live

log = logging.getLogger("opspilot")
app = FastAPI(title="OpsPilot", version=__version__)

INCIDENTS: OrderedDict[str, Diagnosis] = OrderedDict()
DEPLOYS: list[Deploy] = []
MAX_INCIDENTS = 200

DIAGNOSES = Counter("opspilot_diagnoses_total", "Diagnostics produits", ["root_cause", "engine"])
DURATION = Histogram("opspilot_diagnosis_seconds", "Durée collecte + diagnostic")


class AMAlert(BaseModel):
    status: str
    labels: dict[str, str]
    annotations: dict[str, str] = {}
    startsAt: datetime


class AMPayload(BaseModel):
    status: str
    alerts: list[AMAlert]


def _notify(d: Diagnosis) -> None:
    if not settings.chat_webhook_url:
        return
    try:
        httpx.post(settings.chat_webhook_url, json=chat_message(d, f"{settings.public_url}/incidents/{d.incident_id}"), timeout=10)
    except httpx.HTTPError as exc:
        log.warning("notification échouée : %s", exc)


def handle(alert: Alert) -> Diagnosis:
    with DURATION.time():
        snap = live.collect(alert, DEPLOYS)
        d = diagnose(snap)
    INCIDENTS[d.incident_id] = d
    while len(INCIDENTS) > MAX_INCIDENTS:
        INCIDENTS.popitem(last=False)
    DIAGNOSES.labels(d.root_cause.value, d.engine).inc()
    _notify(d)
    return d


@app.post("/webhook/alertmanager", status_code=202)
def alertmanager(payload: AMPayload, tasks: BackgroundTasks) -> dict:
    accepted = 0
    for a in payload.alerts:
        if a.status != "firing":
            continue
        service = a.labels.get("service") or a.labels.get("job") or "unknown"
        alert = Alert(
            name=a.labels.get("alertname", "alert"),
            service=service,
            severity=a.labels.get("severity", "warning"),
            starts_at=a.startsAt,
            summary=a.annotations.get("summary", ""),
            labels=a.labels,
        )
        tasks.add_task(handle, alert)
        accepted += 1
    return {"accepted": accepted}


@app.post("/deploys", status_code=201)
def register_deploy(d: Deploy) -> dict:
    DEPLOYS.append(d)
    del DEPLOYS[:-100]
    return {"registered": d.service, "version": d.version}


@app.get("/deploys")
def list_deploys() -> list[Deploy]:
    return DEPLOYS


@app.get("/incidents")
def incidents() -> list[dict]:
    return [
        {
            "id": d.incident_id,
            "title": d.title,
            "root_cause": d.root_cause,
            "confidence": d.confidence,
            "service": d.alert.service,
            "starts_at": d.alert.starts_at,
        }
        for d in reversed(INCIDENTS.values())
    ]


@app.get("/incidents/{incident_id}", response_class=PlainTextResponse)
def incident(incident_id: str) -> str:
    if incident_id not in INCIDENTS:
        raise HTTPException(404, "incident inconnu")
    return markdown(INCIDENTS[incident_id])


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__, "time": datetime.now(UTC).isoformat()}


@app.get("/metrics")
def metrics() -> PlainTextResponse:
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)
