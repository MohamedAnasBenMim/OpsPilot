output "kubeconfig_path" {
  value = kind_cluster.this.kubeconfig_path
}

output "grafana" {
  value = module.platform.grafana_port_forward
}

output "argocd" {
  value = module.platform.argocd_port_forward
}
