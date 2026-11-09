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
