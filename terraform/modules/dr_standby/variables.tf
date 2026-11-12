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
