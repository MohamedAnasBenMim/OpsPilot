"""orders-api : service de démonstration instrumenté, avec injection de pannes.

Il expose les métriques que l'agent sait lire (Prometheus), écrit des logs JSON
(collectés par Loki) et permet de provoquer chacune des causes racines de la taxonomie
via POST /chaos — de quoi enregistrer de vrais incidents reproductibles.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import sys
import time

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, ProcessCollector, generate_latest
from pydantic import BaseModel

SERVICE = os.getenv("SERVICE_NAME", "orders-api")
VERSION = os.getenv("APP_VERSION", "v1.0.0")
PAYMENTS_URL = os.getenv("PAYMENTS_URL")  # obligatoire : son absence simule une erreur de configuration
CHAOS_TOKEN = os.getenv("CHAOS_TOKEN", "")
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "20"))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "level": record.levelname,
                "service": SERVICE,
                "version": VERSION,
                "msg": record.getMessage(),
            }
        )


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JsonFormatter())
log = logging.getLogger(SERVICE)
log.addHandler(handler)
log.setLevel(logging.INFO)

if not PAYMENTS_URL:
    log.critical("startup failed: missing env variable PAYMENTS_URL")
    sys.exit(1)

REGISTRY = CollectorRegistry()
ProcessCollector(registry=REGISTRY)  # process_resident_memory_bytes, process_cpu_seconds_total
REQUESTS = Counter("http_requests_total", "Requêtes HTTP", ["route", "status"], registry=REGISTRY)
LATENCY = Histogram(
    "http_request_duration_seconds",
    "Durée des requêtes",
    ["route"],
    buckets=(0.025, 0.05, 0.1, 0.2, 0.4, 0.8, 1.6, 3.2, 6.4),
    registry=REGISTRY,
)
UPSTREAM = Counter("upstream_requests_total", "Appels au service de paiement", ["outcome"], registry=REGISTRY)
UPSTREAM_LATENCY = Histogram(
    "upstream_request_duration_seconds", "Durée des appels amont", buckets=(0.025, 0.05, 0.1, 0.2, 0.4, 0.8, 1.6, 3.2), registry=REGISTRY
)
POOL_IN_USE = Gauge("db_pool_in_use", "Connexions DB utilisées", registry=REGISTRY)
POOL_MAX = Gauge("db_pool_max", "Taille du pool DB", registry=REGISTRY)
POOL_MAX.set(DB_POOL_MAX)
BUILD = Gauge("app_build_info", "Version déployée", ["version"], registry=REGISTRY)
BUILD.labels(VERSION).set(1)


class Chaos(BaseModel):
    """Pannes injectables. `mode` = none | errors | latency | memory_leak | cpu | db_pool | dependency_down."""

    mode: str = "none"
    intensity: float = 1.0
    duration_s: int = 600


app = FastAPI(title=SERVICE, version=VERSION)
state = {"mode": "none", "intensity": 1.0, "until": 0.0}
pool = asyncio.Semaphore(DB_POOL_MAX)
leak: list[bytes] = []


def active(mode: str) -> bool:
    return state["mode"] == mode and time.time() < state["until"]


async def db_query() -> None:
    """Simule une requête SQL qui occupe une connexion du pool."""
    hold = 0.02 * (20 * state["intensity"] if active("db_pool") else 1)
    try:
        await asyncio.wait_for(pool.acquire(), timeout=2.0)
    except TimeoutError:
        log.error("timeout acquiring connection from pool after 2000ms")
        raise HTTPException(503, "database unavailable") from None
    POOL_IN_USE.inc()
    try:
        await asyncio.sleep(hold)
    finally:
        POOL_IN_USE.dec()
        pool.release()


async def charge_payment() -> None:
    t0 = time.perf_counter()
    try:
        if active("dependency_down"):
            raise httpx.ConnectError("connection refused")
        if active("latency"):
            await asyncio.sleep(0.6 * state["intensity"] * random.uniform(0.7, 1.3))
        if PAYMENTS_URL.startswith("http") and "simulated" not in PAYMENTS_URL:
            async with httpx.AsyncClient(timeout=2.0) as c:
                (await c.get(PAYMENTS_URL)).raise_for_status()
        else:
            await asyncio.sleep(random.uniform(0.02, 0.06))
        UPSTREAM.labels("ok").inc()
    except (httpx.HTTPError, TimeoutError) as exc:
        UPSTREAM.labels("error").inc()
        log.error(f"POST {PAYMENTS_URL}/charge failed: {exc}")
        raise HTTPException(502, "payment provider unavailable") from None
    finally:
        UPSTREAM_LATENCY.observe(time.perf_counter() - t0)


@app.middleware("http")
async def metrics_mw(request: Request, call_next):
    route = request.url.path
    if route in ("/metrics", "/health", "/chaos"):
        return await call_next(request)
    t0 = time.perf_counter()
    try:
        resp = await call_next(request)
        status = resp.status_code
    except Exception:
        log.exception("unhandled exception")
        resp, status = JSONResponse({"error": "internal"}, 500), 500
    LATENCY.labels(route).observe(time.perf_counter() - t0)
    REQUESTS.labels(route, str(status)).inc()
    return resp


@app.get("/orders")
async def list_orders() -> dict:
    await db_query()
    if active("errors") and random.random() < 0.3 * state["intensity"]:
        log.error("TypeError: unsupported operand type(s) for -: 'NoneType' and 'float' in apply_discount")
        raise HTTPException(500, "internal error")
    if active("cpu"):
        sum(i * i for i in range(int(200_000 * state["intensity"])))  # travail CPU volontaire
    if active("memory_leak"):
        leak.append(os.urandom(int(1_000_000 * state["intensity"])))
    return {"orders": [{"id": i, "total": round(random.uniform(10, 200), 2)} for i in range(5)]}


@app.post("/orders", status_code=201)
async def create_order() -> dict:
    await db_query()
    await charge_payment()
    return {"id": random.randint(1000, 9999), "status": "paid"}


@app.post("/chaos")
def chaos(c: Chaos, x_chaos_token: str = Header(default="")) -> dict:
    if not CHAOS_TOKEN or x_chaos_token != CHAOS_TOKEN:
        raise HTTPException(403, "chaos disabled or bad token")
    state.update(mode=c.mode, intensity=c.intensity, until=time.time() + c.duration_s)
    if c.mode == "none":
        leak.clear()
    log.warning(f"chaos mode set to {c.mode} (intensity={c.intensity}, {c.duration_s}s)")
    return {"mode": c.mode, "until": state["until"]}


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": VERSION}


@app.get("/metrics")
def metrics() -> PlainTextResponse:
    return PlainTextResponse(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
