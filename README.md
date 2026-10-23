# OpsPilot

**Une plateforme Kubernetes GitOps observable, avec un agent qui diagnostique les incidents à la place de l'astreinte.**

![CI](https://github.com/MohamedAnasBenMim/opspilot/actions/workflows/ci.yml/badge.svg)

Quand une alerte se déclenche à 3 h du matin, l'ingénieur d'astreinte passe l'essentiel de son temps
à **chercher** : quel graphe regarder, quels logs, quel déploiement récent. OpsPilot fait cette recherche
automatiquement : il reçoit l'alerte, collecte métriques, logs, événements Kubernetes et déploiements,
identifie la cause racine la plus probable **en citant ses preuves**, propose une action et publie un
rapport d'incident.

```
Alertmanager ──► OpsPilot ──► Prometheus (métriques) ─┐
                     │    ──► Loki (logs)             ├─► signaux + preuves ─► règles / LLM ─► diagnostic
                     │    ──► API Kubernetes          ┘                                         │
                     │         (événements, rollouts)                                           ▼
                     └──────────────────────────────────────────────► rapport + message Slack/Teams
```

## Ce que contient le projet

| Brique | Contenu |
|---|---|
| **Infrastructure as Code** | Terraform : cluster local **kind** ou **AKS** (Azure), même module « plateforme » pour les deux |
| **GitOps** | **Argo CD** suit `deploy/k8s/overlays/local` : tout changement passe par Git |
| **Observabilité** | **Prometheus Operator**, Alertmanager, **Grafana** (tableau de bord SLO), **Loki** |
| **SLO / SRE** | SLO 99,5 % et p95 < 400 ms, alertes de *burn rate* du budget d'erreur, runbooks |
| **Chaos engineering** | **Chaos Mesh** (pod-kill, latence réseau, stress CPU/mémoire) + pannes applicatives à la demande |
| **Agent de diagnostic** | 7 causes racines, preuves citées, moteur à règles hors ligne ou **LLM** (OpenAI, Mistral, Ollama…) |
| **Application de démo** | `orders-api` (FastAPI) instrumentée, durcie (non-root, FS en lecture seule), avec HPA |
| **CI/CD** | GitHub Actions : tests, lint, évaluation de l'agent, `kubeconform`, `promtool`, `amtool`, `terraform validate`, images sur GHCR |

## Démarrage rapide

### Sans Kubernetes (5 minutes)

```bash
docker compose up --build
# Grafana http://localhost:3000 (admin/admin) · Prometheus :9090 · OpsPilot http://localhost:8080/incidents

# provoquer une panne du service de paiement
curl -X POST localhost:8001/chaos -H 'x-chaos-token: demo' -H 'content-type: application/json' \
     -d '{"mode": "dependency_down", "duration_s": 600}'
# ~3 min plus tard : l'alerte part, OpsPilot publie son diagnostic sur /incidents
```

### Sur Kubernetes (kind + Terraform + Argo CD)

```bash
cd infra/terraform/envs/local
terraform init && terraform apply          # cluster kind, Prometheus, Grafana, Loki, Argo CD, Chaos Mesh
kubectl -n shop get pods                   # Argo CD a déployé orders-api, loadgen et OpsPilot
kubectl apply -f ../../../../deploy/chaos/experiments.yaml -l scenario=network-latency
```

Sur Azure : `infra/terraform/envs/azure` (AKS, Log Analytics). Pensez au `terraform destroy`.

Avec un LLM : `kubectl -n shop create secret generic opspilot-llm --from-literal=api-key=...`
(ou `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` en docker compose).

## Exemple de diagnostic

Incident **réel** provoqué sur la stack locale (panne du service de paiement), diagnostiqué par le moteur à règles :

> **Cause racine probable : Dépendance externe indisponible**
>
> upstream_error_rate : 0 → 1 (apparition) ; error_rate : 0 → 0,319 (apparition).
>
> **Action recommandée :** activer le disjoncteur / mode dégradé et contacter l'équipe propriétaire de la dépendance.

## Évaluation

L'agent est évalué comme un modèle : sur des incidents dont la cause est connue, on mesure s'il la retrouve
(**top-1**, **top-3**) et s'il cite les bonnes preuves. La CI bloque toute régression.

### Incidents réels (stack locale, cause injectée puis enregistrée avec `opspilot record`)

| Incident | Cause injectée | Diagnostic | Preuves citées |
|---|---|---|---|
| r01 | panne du service de paiement | ✅ dépendance indisponible | erreurs amont, erreurs 5xx |
| r02 | requêtes SQL ralenties (+ fin de r01 dans la fenêtre) | ✅ saturation base de données | connexions DB, latence |
| r03 | déploiement v1.1.0 + erreurs (+ fin de r02 dans la fenêtre) | ✅ régression de déploiement | déploiement, erreurs 5xx |

**3/3 causes retrouvées.** C'est un premier échantillon, trop petit pour une statistique : il valide la chaîne
complète (Prometheus → Alertmanager → OpsPilot) et montre que l'agent écarte les incidents précédents encore
visibles dans la fenêtre d'analyse. Prochaine étape : 20+ incidents sur cluster Kubernetes.

### Scénarios synthétiques (21 incidents, 7 causes, avec fausses pistes)

| Top-1 | Top-3 | Preuves clés citées | Latence |
|---|---|---|---|
| 100 % | 100 % | 100 % | < 2 ms |

Ce score n'est **pas** une mesure de performance : les scénarios et les règles ont été écrits ensemble.
Ils servent de **test de non-régression**. Les chiffres qui comptent sont ceux des incidents réels,
dont le nombre augmente avec chaque expérience de chaos ([méthode](docs/chaos.md)).

### Ce que les incidents réels ont appris

Rejouer de vrais incidents a révélé trois défauts invisibles sur les données synthétiques, corrigés depuis :

- **Série vide en PromQL** : tant qu'aucune erreur 5xx n'existe, la série n'existe pas et la requête
  renvoyait du vide au lieu de 0 (correction : `or vector(0)`).
- **Historique trop court** : sur un service démarré récemment, l'absence de ligne de base faisait
  passer *toutes* les métriques pour anormales. L'agent utilise désormais le début de la fenêtre comme référence
  et ignore les hausses relatives sans importance absolue.
- **Incident précédent dans la fenêtre** : la fin d'un incident déjà résorbé était prise pour la cause du suivant.
  Une anomalie doit désormais être **toujours active** au moment de l'alerte, et la ligne de base utilise la médiane.

## Causes racines reconnues

| Cause | Signaux principaux |
|---|---|
| Régression de déploiement | déploiement < 30 min avant l'alerte, exceptions, hausse d'erreurs ou de latence |
| OOMKilled | événement OOMKilled, mémoire à la limite ou en hausse continue, redémarrages |
| Erreur de configuration | CrashLoopBackOff, logs « missing env / invalid config », ConfigMap ou Secret modifié |
| Dépendance indisponible | erreurs des appels amont, logs « connection refused » / 503 / disjoncteur |
| Saturation base de données | pool de connexions au maximum, « timeout acquiring connection » |
| Saturation CPU | CPU à la limite, pic de trafic, latence |
| Latence réseau | latence amont en hausse sans erreur ni saturation, timeouts |

Le moteur à règles pondère ces signaux (positivement ou négativement, pour écarter les fausses pistes comme
un déploiement récent sans rapport). Avec un LLM, le modèle reçoit les mêmes preuves identifiées, doit répondre
en JSON en les citant, et toute réponse invalide déclenche un repli sur les règles.

## API de l'agent

| Méthode | Route | Rôle |
|---|---|---|
| POST | `/webhook/alertmanager` | réception des alertes |
| POST / GET | `/deploys` | enregistrement des déploiements par la CI/CD |
| GET | `/incidents`, `/incidents/{id}` | diagnostics et rapport Markdown |
| GET | `/metrics` | métriques de l'agent (diagnostics par cause, durée) |

```bash
opspilot diagnose eval/scenarios/s04_oom.json        # rejouer un incident enregistré
opspilot record --service orders-api --out inc.json  # capturer un incident en direct
```

## Structure

```
app/                  orders-api (démo instrumentée + injection de pannes) et générateur de trafic
pilot/opspilot/       agent : collecte (Prometheus, Loki, Kubernetes), signaux, diagnostic, API, CLI
eval/                 scénarios synthétiques, incidents réels, script d'évaluation
deploy/k8s/           manifestes Kustomize (base + overlay), PrometheusRule, AlertmanagerConfig
deploy/argocd/        application Argo CD
deploy/chaos/         expériences Chaos Mesh
infra/terraform/      module plateforme + environnements kind et AKS
observability/        Prometheus, Alertmanager, Grafana, Loki pour docker compose
docs/                 méthode de chaos, runbooks
```

## Feuille de route

- [x] Agent de diagnostic avec preuves citées, moteur à règles et LLM avec repli
- [x] Plateforme Terraform (kind, AKS), GitOps Argo CD, Prometheus/Grafana/Loki, Chaos Mesh
- [x] SLO, alertes de burn rate, runbooks, CI complète
- [x] Premiers incidents réels enregistrés et évalués
- [ ] 20+ incidents réels sur cluster Kubernetes (Chaos Mesh), comparaison règles vs LLM
- [ ] Mesure du temps de diagnostic avec et sans l'agent
- [ ] Traces distribuées (OpenTelemetry + Tempo) comme source de preuves supplémentaire

## Licence

MIT
