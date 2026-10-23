# Socle plateforme installé sur n'importe quel cluster Kubernetes :
# observabilité (Prometheus, Alertmanager, Grafana, Loki), GitOps (Argo CD) et chaos (Chaos Mesh).

terraform {
  required_providers {
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.15"
    }
  }
}

resource "helm_release" "kube_prometheus_stack" {
  name             = "kube-prometheus-stack"
  repository       = "https://prometheus-community.github.io/helm-charts"
  chart            = "kube-prometheus-stack"
  version          = var.kube_prometheus_stack_version
  namespace        = "monitoring"
  create_namespace = true
  timeout          = 900

  values = [templatefile("${path.module}/values/kube-prometheus-stack.yaml", {
    grafana_admin_password = var.grafana_admin_password
  })]
}

resource "helm_release" "loki" {
  name       = "loki"
  repository = "https://grafana.github.io/helm-charts"
  chart      = "loki-stack"
  version    = var.loki_stack_version
  namespace  = "monitoring"

  values     = [file("${path.module}/values/loki-stack.yaml")]
  depends_on = [helm_release.kube_prometheus_stack]
}

resource "helm_release" "argocd" {
  name             = "argocd"
  repository       = "https://argoproj.github.io/argo-helm"
  chart            = "argo-cd"
  version          = var.argocd_version
  namespace        = "argocd"
  create_namespace = true
}

# Application GitOps : le cluster suit le dossier deploy/k8s/overlays/<overlay> du dépôt
resource "helm_release" "argocd_apps" {
  name       = "argocd-apps"
  repository = "https://argoproj.github.io/argo-helm"
  chart      = "argocd-apps"
  version    = var.argocd_apps_version
  namespace  = "argocd"

  values = [templatefile("${path.module}/values/argocd-apps.yaml", {
    repo_url = var.repo_url
    revision = var.repo_revision
    overlay  = var.overlay
  })]
  depends_on = [helm_release.argocd, helm_release.kube_prometheus_stack]
}

resource "helm_release" "chaos_mesh" {
  count            = var.enable_chaos_mesh ? 1 : 0
  name             = "chaos-mesh"
  repository       = "https://charts.chaos-mesh.org"
  chart            = "chaos-mesh"
  version          = var.chaos_mesh_version
  namespace        = "chaos-mesh"
  create_namespace = true

  set {
    name  = "chaosDaemon.runtime"
    value = "containerd"
  }
  set {
    name  = "chaosDaemon.socketPath"
    value = var.containerd_socket
  }
}
