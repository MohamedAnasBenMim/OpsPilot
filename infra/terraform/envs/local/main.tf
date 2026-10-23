# Environnement local : cluster kind (1 control-plane + 2 workers) + socle plateforme.
#   cd infra/terraform/envs/local && terraform init && terraform apply

terraform {
  required_version = ">= 1.6"
  required_providers {
    kind = {
      source  = "tehcyx/kind"
      version = "~> 0.6"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.15"
    }
  }
}

resource "kind_cluster" "this" {
  name           = var.cluster_name
  node_image     = "kindest/node:v1.31.1"
  wait_for_ready = true

  kind_config {
    kind        = "Cluster"
    api_version = "kind.x-k8s.io/v1alpha4"

    node {
      role = "control-plane"
    }
    node {
      role = "worker"
    }
    node {
      role = "worker"
    }
  }
}

provider "helm" {
  kubernetes {
    host                   = kind_cluster.this.endpoint
    client_certificate     = kind_cluster.this.client_certificate
    client_key             = kind_cluster.this.client_key
    cluster_ca_certificate = kind_cluster.this.cluster_ca_certificate
  }
}

module "platform" {
  source = "../../modules/platform"

  grafana_admin_password = var.grafana_admin_password
  enable_chaos_mesh      = var.enable_chaos_mesh
  repo_url               = var.repo_url
  overlay                = "local"
}
