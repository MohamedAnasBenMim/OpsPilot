# OpsPilot

**An observable GitOps Kubernetes platform, with an agent that diagnoses incidents so the on-call engineer doesn't have to.**

![CI](https://github.com/MohamedAnasBenMim/OpsPilot/actions/workflows/ci.yml/badge.svg)

When an alert fires at 3 a.m., the on-call engineer spends most of their time **searching**: which dashboard
to check, which logs, which recent deployment. OpsPilot does that search automatically: it receives the alert,
collects metrics, logs, Kubernetes events and deployments, identifies the most likely root cause
**citing its evidence**, suggests an action and publishes an incident report.

```
Alertmanager ──► OpsPilot ──► Prometheus (metrics) ───┐
                     │    ──► Loki (logs)             ├─► signals + evidence ─► rules / LLM ─► diagnosis
                     │    ──► Kubernetes API          ┘                                         │
                     │         (events, rollouts)                                               ▼
                     └──────────────────────────────────────────────► report + Slack/Teams message
```

## What's in the project

| Component | Contents |
|---|---|
| **Infrastructure as Code** | Terraform: local **kind** cluster or **AKS** (Azure), same "platform" module for both |
| **GitOps** | **Argo CD** tracks `deploy/k8s/overlays/local`: every change goes through Git |
| **Observability** | **Prometheus Operator**, Alertmanager, **Grafana** (SLO dashboard), **Loki** |
| **SLO / SRE** | 99.5% SLO and p95 < 400 ms, error budget *burn rate* alerts, runbooks |
| **Chaos engineering** | **Chaos Mesh** (pod-kill, network latency, CPU/memory stress) + on-demand application faults |
| **Diagnosis agent** | 7 root causes, cited evidence, offline rule engine or **LLM** (OpenAI, Mistral, Ollama…) |
| **Demo application** | Instrumented, hardened `orders-api` (FastAPI) (non-root, read-only FS), with HPA |
| **CI/CD** | GitHub Actions: tests, lint, agent evaluation, `kubeconform`, `promtool`, `amtool`, `terraform validate`, images on GHCR |

## Quick start

### Without Kubernetes (5 minutes)

```bash
docker compose up --build
# Grafana http://localhost:3000 (admin/admin) · Prometheus :9090 · OpsPilot http://localhost:8080/incidents

# trigger an outage of the payment service
curl -X POST localhost:8001/chaos -H 'x-chaos-token: demo' -H 'content-type: application/json' \
     -d '{"mode": "dependency_down", "duration_s": 600}'
# ~3 min later: the alert fires, OpsPilot publishes its diagnosis on /incidents
```

### On Kubernetes (kind + Terraform + Argo CD)

```bash
cd infra/terraform/envs/local
terraform init && terraform apply          # kind cluster, Prometheus, Grafana, Loki, Argo CD, Chaos Mesh
kubectl -n shop get pods                   # Argo CD has deployed orders-api, loadgen and OpsPilot
kubectl apply -f ../../../../deploy/chaos/experiments.yaml -l scenario=network-latency
```

On Azure: `infra/terraform/envs/azure` (AKS, Log Analytics). Don't forget to `terraform destroy`.

With an LLM: `kubectl -n shop create secret generic opspilot-llm --from-literal=api-key=...`
(or `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` with docker compose).

## Example diagnosis

A **real** incident triggered on the local stack (payment service outage), diagnosed by the rule engine:

> **Probable root cause: External dependency unavailable**
>
> upstream_error_rate: 0 → 1 (new); error_rate: 0 → 0.319 (new).
>
> **Recommended action:** enable the circuit breaker / degraded mode and contact the team that owns the dependency.

## Evaluation

The agent is evaluated like a model: on incidents with a known cause, we measure whether it finds it
(**top-1**, **top-3**) and whether it cites the right evidence. CI blocks any regression.

### Real incidents (local stack, cause injected then recorded with `opspilot record`)

| Incident | Injected cause | Diagnosis | Cited evidence |
|---|---|---|---|
| r01 | payment service outage | ✅ dependency unavailable | upstream errors, 5xx errors |
| r02 | slowed-down SQL queries (+ tail of r01 in the window) | ✅ database saturation | DB connections, latency |
| r03 | v1.1.0 deployment + errors (+ tail of r02 in the window) | ✅ deployment regression | deployment, 5xx errors |

**3/3 causes found.** This is a first sample, too small to be statistically meaningful: it validates the end-to-end
pipeline (Prometheus → Alertmanager → OpsPilot) and shows that the agent rules out previous incidents still
visible in the analysis window. Next step: 20+ incidents on a Kubernetes cluster.

### Synthetic scenarios (21 incidents, 7 causes, with red herrings)

| Top-1 | Top-3 | Key evidence cited | Latency |
|---|---|---|---|
| 100% | 100% | 100% | < 2 ms |

This score is **not** a performance measure: the scenarios and the rules were written together.
They serve as a **regression test**. The numbers that matter are those from real incidents,
which grow with every chaos experiment ([method](docs/chaos.md)).

### What real incidents taught us

Replaying real incidents exposed three flaws that were invisible on synthetic data, all since fixed:

- **Empty series in PromQL**: as long as no 5xx error exists, the series doesn't exist and the query
  returned nothing instead of 0 (fix: `or vector(0)`).
- **History too short**: on a recently started service, the lack of a baseline made *every* metric
  look anomalous. The agent now uses the start of the window as its reference
  and ignores relative increases that are insignificant in absolute terms.
- **Previous incident in the window**: the tail end of an already-resolved incident was mistaken for the cause of the next one.
  An anomaly must now be **still active** when the alert fires, and the baseline uses the median.

## Supported root causes

| Cause | Main signals |
|---|---|
| Deployment regression | deployment < 30 min before the alert, exceptions, rise in errors or latency |
| OOMKilled | OOMKilled event, memory at the limit or steadily rising, restarts |
| Configuration error | CrashLoopBackOff, "missing env / invalid config" logs, modified ConfigMap or Secret |
| Dependency unavailable | upstream call errors, "connection refused" / 503 / circuit breaker logs |
| Database saturation | connection pool maxed out, "timeout acquiring connection" |
| CPU saturation | CPU at the limit, traffic spike, latency |
| Network latency | rising upstream latency with no errors or saturation, timeouts |

The rule engine weights these signals (positively or negatively, to rule out red herrings such as
an unrelated recent deployment). With an LLM, the model receives the same identified evidence, must answer
in JSON citing it, and any invalid response triggers a fallback to the rules.

## Agent API

| Method | Route | Purpose |
|---|---|---|
| POST | `/webhook/alertmanager` | receives alerts |
| POST / GET | `/deploys` | records deployments from CI/CD |
| GET | `/incidents`, `/incidents/{id}` | diagnoses and Markdown report |
| GET | `/metrics` | agent metrics (diagnoses per cause, duration) |

```bash
opspilot diagnose eval/scenarios/s04_oom.json        # replay a recorded incident
opspilot record --service orders-api --out inc.json  # capture a live incident
```

## Structure

```
app/                  orders-api (instrumented demo + fault injection) and traffic generator
pilot/opspilot/       agent: collection (Prometheus, Loki, Kubernetes), signals, diagnosis, API, CLI
eval/                 synthetic scenarios, real incidents, evaluation script
deploy/k8s/           Kustomize manifests (base + overlay), PrometheusRule, AlertmanagerConfig
deploy/argocd/        Argo CD application
deploy/chaos/         Chaos Mesh experiments
infra/terraform/      platform module + kind and AKS environments
observability/        Prometheus, Alertmanager, Grafana, Loki for docker compose
docs/                 chaos method, runbooks
```

## Roadmap

- [x] Diagnosis agent with cited evidence, rule engine and LLM with fallback
- [x] Terraform platform (kind, AKS), Argo CD GitOps, Prometheus/Grafana/Loki, Chaos Mesh
- [x] SLOs, burn rate alerts, runbooks, full CI
- [x] First real incidents recorded and evaluated
- [ ] 20+ real incidents on a Kubernetes cluster (Chaos Mesh), rules vs LLM comparison
- [ ] Measure time-to-diagnosis with and without the agent
- [ ] Distributed tracing (OpenTelemetry + Tempo) as an additional evidence source

## License

MIT
