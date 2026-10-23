output "get_credentials" {
  description = "Commande pour configurer kubectl"
  value       = "az aks get-credentials -g ${azurerm_resource_group.this.name} -n ${azurerm_kubernetes_cluster.this.name}"
}

output "grafana" {
  value = module.platform.grafana_port_forward
}
