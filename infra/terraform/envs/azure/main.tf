# Environnement Azure : AKS + Log Analytics + socle plateforme (même module que l'environnement local).
#   export ARM_SUBSCRIPTION_ID=...   # crédit Azure for Students suffisant
#   cd infra/terraform/envs/azure && terraform init && terraform apply
# Pensez à `terraform destroy` après la démo pour ne pas consommer de crédit.

terraform {
  required_version = ">= 1.6"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.15"
    }
  }
}

provider "azurerm" {
  features {}
}

locals {
  tags = {
    project     = "opspilot"
    environment = var.environment
    managed_by  = "terraform"
  }
}

resource "azurerm_resource_group" "this" {
  name     = "rg-opspilot-${var.environment}"
  location = var.location
  tags     = local.tags
}

resource "azurerm_log_analytics_workspace" "this" {
  name                = "log-opspilot-${var.environment}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.tags
}

resource "azurerm_kubernetes_cluster" "this" {
  name                = "aks-opspilot-${var.environment}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  dns_prefix          = "opspilot-${var.environment}"
  sku_tier            = "Free"
  tags                = local.tags

  default_node_pool {
    name       = "system"
    node_count = var.node_count
    vm_size    = var.node_vm_size
  }

  identity {
    type = "SystemAssigned"
  }

  oms_agent {
    log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id
  }
}

provider "helm" {
  kubernetes {
    host                   = azurerm_kubernetes_cluster.this.kube_config[0].host
    client_certificate     = base64decode(azurerm_kubernetes_cluster.this.kube_config[0].client_certificate)
    client_key             = base64decode(azurerm_kubernetes_cluster.this.kube_config[0].client_key)
    cluster_ca_certificate = base64decode(azurerm_kubernetes_cluster.this.kube_config[0].cluster_ca_certificate)
  }
}

module "platform" {
  source = "../../modules/platform"

  grafana_admin_password = var.grafana_admin_password
  enable_chaos_mesh      = var.enable_chaos_mesh
  overlay                = "local"
  containerd_socket      = "/run/containerd/containerd.sock"
}
