variable "account_id" { type = string }
variable "region" { type = string }
variable "name" { type = string }
variable "cluster_version" { type = string }
variable "admin_cidrs" { type = list(string) }
variable "admin_role_arn" { type = string }
variable "desired_nodes" { type = number }
variable "queue_arn" { type = string }
variable "restored_table_name" { type = string }
variable "recovery_host_ami" {
  type    = string
  default = ""
}
variable "enable_recovery_host" {
  type    = bool
  default = false
}

variable "max_nodes" {
  type = number
}

variable "permissions_boundary_arn" { type = string }

variable "addon_versions" {
  description = "Pinned versions verified on the Ohio EKS 1.36 cluster on 2026-11-12."
  type        = map(string)
  default = {
    "eks-pod-identity-agent" = "v1.4.1-eksbuild.3"
    "vpc-cni"                = "v1.22.4-eksbuild.14"
    "kube-proxy"             = "v1.36.0-eksbuild.65"
    "coredns"                = "v1.14.7-eksbuild.35"
    "aws-ebs-csi-driver"     = "v1.68.0-eksbuild.1"
  }
}

variable "manage_restored_orders_table" {
  description = "Enable only after a completed verified AWS Backup restore; import before applying."
  type        = bool
  default     = false
}

variable "restored_volumes" {
  description = "Only already completed Ohio Backup restores; import each volume before apply."
  type = map(object({
    volume_id         = string
    snapshot_id       = string
    size_gib          = number
    availability_zone = string
    source_volume     = string
    csi_managed       = bool
  }))
  default = {}
  validation {
    condition = alltrue([for v in values(var.restored_volumes) :
      can(regex("^vol-[a-f0-9]+$", v.volume_id)) && can(regex("^snap-[a-f0-9]+$", v.snapshot_id)) &&
      contains(["us-east-2a", "us-east-2b", "us-east-2c"], v.availability_zone) && v.size_gib > 0 && v.size_gib <= 120
    ])
    error_message = "Require explicit existing Ohio volume/snapshot IDs, compatible AZ and reviewed size."
  }
}

variable "enable_stateful_pool" {
  type    = bool
  default = false
}
variable "desired_stateful_nodes" {
  type    = number
  default = 0
}
