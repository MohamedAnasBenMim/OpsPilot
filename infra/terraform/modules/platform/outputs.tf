output "namespaces" {
  description = "Namespaces créés par le socle plateforme"
  value       = compact(["monitoring", "argocd", var.enable_chaos_mesh ? "chaos-mesh" : ""])
}

output "grafana_port_forward" {
  description = "Commande pour ouvrir Grafana"
  value       = "kubectl -n monitoring port-forward svc/kube-prometheus-stack-grafana 3000:80"
}

output "argocd_port_forward" {
  description = "Commande pour ouvrir Argo CD (mot de passe : secret argocd-initial-admin-secret)"
  value       = "kubectl -n argocd port-forward svc/argocd-server 8443:443"
}
