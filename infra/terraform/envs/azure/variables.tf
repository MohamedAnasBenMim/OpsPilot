variable "environment" {
  type    = string
  default = "dev"
}

variable "location" {
  type    = string
  default = "francecentral"
}

variable "node_count" {
  type    = number
  default = 2
}

variable "node_vm_size" {
  description = "Petite taille pour rester dans le crédit étudiant"
  type        = string
  default     = "Standard_B2s"
}

variable "grafana_admin_password" {
  type      = string
  sensitive = true
}

variable "enable_chaos_mesh" {
  type    = bool
  default = true
}
