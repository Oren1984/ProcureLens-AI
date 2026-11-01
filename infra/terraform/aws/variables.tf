variable "region" {
  description = "AWS region for the demo host."
  type        = string
  default     = "eu-central-1"
}

variable "name" {
  description = "Name prefix for resources."
  type        = string
  default     = "procurelens-demo"
}

variable "instance_type" {
  description = "EC2 instance type. t3.medium (4 GiB) fits app + PostgreSQL; the optional Ollama profile needs >= 8 GiB (e.g. t3.large)."
  type        = string
  default     = "t3.medium"
}

variable "root_volume_gb" {
  description = "Encrypted root volume size (OS, Docker images)."
  type        = number
  default     = 30
}

variable "data_volume_gb" {
  description = "Encrypted data volume mounted at /srv/procurelens (Docker volumes, backups)."
  type        = number
  default     = 20
}

variable "allowed_https_cidrs" {
  description = "CIDRs allowed to reach HTTPS (443) on the host after TLS termination is configured. Empty = no inbound access at all; use SSM port forwarding instead (recommended)."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for c in var.allowed_https_cidrs : can(cidrhost(c, 0)) && c != "0.0.0.0/0"])
    error_message = "Each entry must be a valid CIDR, and 0.0.0.0/0 is not allowed."
  }
}

variable "snapshot_retention_days" {
  description = "Daily EBS snapshots of the data volume kept for this many days (0 disables)."
  type        = number
  default     = 7

  validation {
    condition     = var.snapshot_retention_days >= 0 && var.snapshot_retention_days <= 35
    error_message = "snapshot_retention_days must be between 0 and 35."
  }
}
