# Runbooks

## Error budget

**Alerte :** `OrdersErrorBudgetFastBurn` — le service consomme son budget d'erreur mensuel
(SLO 99,5 %) 14 fois plus vite que prévu : à ce rythme, le budget est épuisé en ~2 jours.

1. Ouvrir le rapport OpsPilot lié à l'alerte (`/incidents`) : cause probable et preuves.
2. Si un déploiement est en cause : `argocd app rollback opspilot-demo` (ou `kubectl rollout undo deploy/orders-api -n shop`).
3. Si une dépendance est en cause : activer le mode dégradé, prévenir l'équipe propriétaire.
4. Geler les déploiements non urgents tant que le budget n'est pas revenu au-dessus de 50 %.

## Latence

**Alerte :** `OrdersHighLatency` (p95 > 400 ms pendant 3 min).

1. Vérifier dans Grafana : trafic, CPU, pool DB, latence amont (tableau « OpsPilot — orders-api SLO »).
2. CPU saturé : vérifier le HPA (`kubectl get hpa -n shop`), scaler si besoin.
3. Pool DB saturé : chercher les requêtes lentes, augmenter `DB_POOL_MAX` si la base le permet.
4. Latence amont : vérifier le réseau et l'état de la dépendance.

## Indisponibilité

**Alerte :** `OrdersDown` ou CrashLoopBackOff.

1. `kubectl describe pod -n shop -l app=orders-api` : raison du redémarrage (OOMKilled, erreur de config).
2. Erreur de config : comparer la ConfigMap avec la dernière version Git ; Argo CD montre le diff.
3. OOMKilled : augmenter temporairement la limite mémoire et ouvrir un ticket de fuite mémoire.
