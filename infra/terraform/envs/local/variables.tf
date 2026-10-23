variable "cluster_name" {
  type    = string
  default = "opspilot"
}

variable "grafana_admin_password" {
  type      = string
  sensitive = true
  default   = "admin"
}

variable "enable_chaos_mesh" {
  type    = bool
  default = true
}

variable "repo_url" {
  type    = string
  default = "https://github.com/MohamedAnasBenMim/opspilot.git"
}
