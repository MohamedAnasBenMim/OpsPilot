# Provoquer et enregistrer de vrais incidents

Le jeu de scénarios synthétiques (`eval/scenarios/`) sert de test de non-régression.
Les résultats qui comptent viennent d'incidents **réellement provoqués** puis enregistrés
dans `eval/real/`. Voici la méthode.

## 1. Pannes applicatives (docker compose ou Kubernetes)

`orders-api` expose `POST /chaos` (protégé par un jeton) :

| Mode | Effet | Cause racine attendue |
|---|---|---|
| `errors` | 30 % de 500 avec une `TypeError` dans les logs | `bad_deploy` (si un déploiement précède) |
| `latency` | +600 ms sur les appels au service de paiement | `network_latency` |
| `dependency_down` | appels au paiement en échec (connection refused) | `dependency_down` |
| `db_pool` | requêtes SQL 20x plus longues, pool saturé | `db_saturation` |
| `cpu` | calcul inutile à chaque requête | `cpu_saturation` |
| `memory_leak` | 1 Mo retenu par requête jusqu'à l'OOMKill | `oom_kill` |

```bash
# docker compose : jeton "demo"
curl -X POST localhost:8001/chaos -H 'x-chaos-token: demo' -H 'content-type: application/json' \
     -d '{"mode": "dependency_down", "intensity": 1, "duration_s": 600}'
```

Pour simuler une régression de déploiement, enregistre d'abord le déploiement auprès d'OpsPilot
(c'est ce que ferait la CI/CD), puis injecte `errors` :

```bash
curl -X POST localhost:8080/deploys -H 'content-type: application/json' \
     -d '{"ts": "2026-10-23T10:00:00Z", "service": "orders-api", "version": "v1.1.0", "previous": "v1.0.0"}'
```

L'erreur de configuration se provoque en supprimant `PAYMENTS_URL` de la ConfigMap : le pod part en CrashLoopBackOff.

## 2. Pannes d'infrastructure (Kubernetes + Chaos Mesh)

```bash
kubectl apply -f deploy/chaos/experiments.yaml -l scenario=network-latency
kubectl delete -f deploy/chaos/experiments.yaml -l scenario=network-latency   # arrêt
```

## 3. Enregistrer l'incident

Une fois l'alerte déclenchée (ou quelques minutes après l'injection) :

```bash
opspilot record --service orders-api --alert OrdersHighErrorRate \
  --starts-at 2026-10-23T10:02:00+00:00 --opspilot-url http://localhost:8080 \
  --out eval/real/r04_network_latency_k8s.json
```

Complète ensuite le champ `ground_truth` (catégorie injectée + preuves clés), puis :

```bash
PYTHONPATH=pilot python eval/run_eval.py --scenarios eval/real --out eval/results_real
```

Pour mesurer le **temps de diagnostic avec et sans l'agent**, chronomètre une personne qui
diagnostique le même incident à partir de Grafana seul, puis à partir du rapport OpsPilot.
