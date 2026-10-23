variable "repo_url" {
  description = "Dépôt Git suivi par Argo CD"
  type        = string
  default     = "https://github.com/MohamedAnasBenMim/opspilot.git"
}

variable "repo_revision" {
  description = "Branche ou tag suivi par Argo CD"
  type        = string
  default     = "main"
}

variable "overlay" {
  description = "Overlay Kustomize déployé (dossier deploy/k8s/overlays/<overlay>)"
  type        = string
  default     = "local"
}

variable "grafana_admin_password" {
  description = "Mot de passe admin Grafana"
  type        = string
  sensitive   = true
}

variable "enable_chaos_mesh" {
  description = "Installer Chaos Mesh pour les expériences de chaos"
  type        = bool
  default     = true
}

variable "containerd_socket" {
  description = "Socket containerd des nœuds (kind : /run/containerd/containerd.sock)"
  type        = string
  default     = "/run/containerd/containerd.sock"
}

variable "kube_prometheus_stack_version" {
  type    = string
  default = "65.5.1"
}

variable "loki_stack_version" {
  type    = string
  default = "2.10.2"
}

variable "argocd_version" {
  type    = string
  default = "7.6.12"
}

variable "argocd_apps_version" {
  type    = string
  default = "2.0.2"
}

variable "chaos_mesh_version" {
  type    = string
  default = "2.7.0"
}
