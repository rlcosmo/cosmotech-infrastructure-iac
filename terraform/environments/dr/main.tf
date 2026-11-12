# Isolated passive DR. Disabled by default; no primary/global resources imported.
# Primary/global resources remain owned by dev; do not import them into DR state.
terraform {
  required_version = "= 1.17.1"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "= 6.62.0"
    }
  }
  backend "s3" {
    bucket       = "aws-serverless-lab-414053644145-tfstate-use1"
    key          = "aws-serverless-lab/dr/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  alias               = "standby"
  region              = var.standby_region
  allowed_account_ids = ["414053644145"]
  default_tags {
    tags = { Project = "CosmoTech", Environment = "dr", ManagedBy = "Terraform", Mode = "passive" }
  }
}

variable "standby_region" {
  description = "Ohio selected by versioned multi-service cost evidence; alternatives require a new review."
  type        = string
  default     = "us-east-2"
  validation {
    condition     = contains(["us-east-2", "us-west-2", "sa-east-1"], var.standby_region)
    error_message = "Use an explicitly reviewed candidate different from primary us-east-1."
  }
}

variable "enable_standby_queues" {
  description = "Disabled by default; no source message replication or automatic failover."
  type        = bool
  default     = false
  validation {
    condition = !var.enable_standby_queues || (
      var.standby_region == "us-east-2" && !var.account_restricted && length(var.release_evidence) > 10 &&
      var.monthly_budget_usd > 0 && var.estimated_monthly_cost_usd > 0 &&
      var.estimated_monthly_cost_usd <= var.monthly_budget_usd
    )
    error_message = "A positive reviewed estimate within an explicit financial ceiling is required."
  }
}

variable "monthly_budget_usd" {
  description = "Explicit owner ceiling; zero means no provisioning. Not a billing hard cap."
  type        = number
  default     = 0
  validation {
    condition     = var.monthly_budget_usd >= 0
    error_message = "Budget cannot be negative."
  }
}

variable "estimated_monthly_cost_usd" {
  description = "Reviewed total architecture estimate, including replication and transfer."
  type        = number
  default     = 0
  validation {
    condition     = var.estimated_monthly_cost_usd >= 0
    error_message = "Estimate cannot be negative."
  }
}

module "standby_sqs" {
  count                    = var.enable_standby_queues || var.enable_standby ? 1 : 0
  source                   = "../../modules/sqs"
  providers                = { aws = aws.standby }
  name_prefix              = "cosmotech-dr-passive"
  consumer_timeout_seconds = 30
  source_retention_seconds = 345600
  dlq_retention_seconds    = 1209600
  max_receive_count        = 5
  tags                     = { Project = "CosmoTech", Environment = "dr", ManagedBy = "Terraform", Mode = "passive" }
}

output "readiness" {
  value = {
    mode                  = "passive"
    selected_region       = var.standby_region
    state_key             = "aws-serverless-lab/dr/terraform.tfstate"
    queue_replication     = "not-native-requires-durable-outbox"
    data_replication      = "backup-restore-with-fencing-no-native-sqs-replication"
    automatic_failover    = false
    provisioning_enabled  = var.enable_standby || var.enable_standby_queues
    financial_ceiling_usd = var.monthly_budget_usd
  }
}

variable "enable_standby" {
  type    = bool
  default = false
  validation {
    condition = !var.enable_standby || (
      var.standby_region == "us-east-2" && !var.account_restricted && length(var.release_evidence) > 10 &&
      var.monthly_budget_usd > 0 && var.estimated_monthly_cost_usd > 0 &&
      var.estimated_monthly_cost_usd <= var.monthly_budget_usd &&
      length(var.admin_cidrs) > 0 && var.admin_role_arn != ""
    )
    error_message = "Require AWS release evidence, scoped operator/CIDRs and reviewed cost within budget."
  }
}
variable "account_restricted" {
  type    = bool
  default = true
}
variable "release_evidence" {
  description = "Official AWS release case/reference; not proof by itself. Preflight must verify it."
  type        = string
  default     = ""
}
variable "cluster_version" {
  type    = string
  default = "1.36"
}
variable "admin_role_arn" {
  type    = string
  default = ""
  validation {
    condition     = var.admin_role_arn == "" || can(regex("^arn:aws:iam::414053644145:role/[A-Za-z0-9+=,.@_/-]+$", var.admin_role_arn))
    error_message = "Use a non-root account-local operator role."
  }
}
variable "admin_cidrs" {
  type    = list(string)
  default = []
  validation {
    condition     = alltrue([for c in var.admin_cidrs : can(cidrnetmask(c)) && try(tonumber(split("/", c)[1]) >= 24, false)])
    error_message = "Explicit IPv4 admin ranges /24 or narrower; never 0.0.0.0/0."
  }
}
variable "desired_nodes" {
  type    = number
  default = 0
  validation {
    condition     = contains([0, 1, 2, 3], var.desired_nodes) && var.desired_nodes <= var.max_nodes
    error_message = "Desired nodes must not exceed the reviewed maximum."
  }
}
module "standby" {
  count                        = var.enable_standby ? 1 : 0
  source                       = "../../modules/dr_standby"
  providers                    = { aws = aws.standby }
  account_id                   = "414053644145"
  region                       = var.standby_region
  name                         = "cosmotech-dr"
  cluster_version              = var.cluster_version
  admin_cidrs                  = var.admin_cidrs
  admin_role_arn               = var.admin_role_arn
  desired_nodes                = var.desired_nodes
  max_nodes                    = var.max_nodes
  permissions_boundary_arn     = var.permissions_boundary_arn
  manage_restored_orders_table = var.manage_restored_orders_table
  restored_volumes             = var.restored_volumes
  enable_stateful_pool         = var.enable_stateful_pool
  desired_stateful_nodes       = var.desired_stateful_nodes
  queue_arn                    = module.standby_sqs[0].orders_queue_arn
  restored_table_name          = "cosmotech-dr-orders-restored"
  enable_recovery_host         = var.enable_recovery_host
  recovery_host_ami            = var.recovery_host_ami
  prepare_dr_tls               = var.prepare_dr_tls
}
output "standby" {
  value = module.standby
}
variable "enable_recovery_host" {
  type    = bool
  default = false
  validation {
    condition     = !var.enable_recovery_host || (var.enable_standby && can(regex("^ami-[0-9a-f]+$", var.recovery_host_ami)))
    error_message = "Only a reviewed CLEAN Ohio AMI, after standby/release/cost validation. Never copy a primary host boot image."
  }
}
variable "recovery_host_ami" {
  type    = string
  default = ""
}

variable "verified_vcpu_quota" {
  description = "Fresh observed Ohio Standard On-Demand vCPU quota; zero blocks standby provisioning."
  type        = number
  default     = 0
  validation {
    condition     = var.verified_vcpu_quota >= 0
    error_message = "Quota must not be negative."
  }
}
variable "reserved_vcpus" {
  description = "Observed non-standby Standard On-Demand vCPUs, including pending instances."
  type        = number
  default     = 0
  validation {
    condition     = var.reserved_vcpus >= 0
    error_message = "Reserved vCPUs must not be negative."
  }
}
variable "max_nodes" {
  description = "Two t3.large nodes use four vCPUs; optional t3.large recovery host uses two more. MINIMAL updates avoid surge."
  type        = number
  default     = 2
  validation {
    condition = contains([1, 2, 3], var.max_nodes) && (!var.enable_standby || (
      2 * var.max_nodes + (var.enable_stateful_pool ? 2 : 0) + (var.enable_recovery_host ? 2 : 0) + var.reserved_vcpus <= var.verified_vcpu_quota
    ))
    error_message = "Peak nodes plus optional host and existing usage exceed the observed On-Demand vCPU quota."
  }
}

variable "permissions_boundary_arn" {
  description = "Bootstrap-owned runtime boundary; mandatory on every DR service/application role."
  type        = string
  default     = ""
  validation {
    condition     = !var.enable_standby || var.permissions_boundary_arn == "arn:aws:iam::414053644145:policy/cosmotech-dr-runtime-boundary"
    error_message = "Use the reviewed bootstrap-owned DR runtime boundary."
  }
}

variable "manage_restored_orders_table" {
  description = "Requires a completed isolated restore and import; never create an empty substitute."
  type        = bool
  default     = false
}

variable "restored_volumes" {
  description = "Verified completed restores, adopted into this isolated DR owner."
  type = map(object({
    volume_id         = string
    snapshot_id       = string
    size_gib          = number
    availability_zone = string
    source_volume     = string
    csi_managed       = bool
  }))
  default = {}
}

variable "enable_stateful_pool" {
  type    = bool
  default = false
}
variable "desired_stateful_nodes" {
  type    = number
  default = 0
  validation {
    condition     = contains([0, 1], var.desired_stateful_nodes) && (var.enable_stateful_pool || var.desired_stateful_nodes == 0)
    error_message = "Only a reviewed zero/one-node stateful pool is permitted."
  }
}

variable "prepare_dr_tls" {
  type    = bool
  default = false
  validation {
    condition     = !var.prepare_dr_tls || (var.enable_standby && var.standby_region == "us-east-2")
    error_message = "TLS preparation requires the released isolated Ohio standby."
  }
}
