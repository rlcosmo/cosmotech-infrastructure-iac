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
      !var.account_restricted && length(var.release_evidence) > 10 &&
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
      !var.account_restricted && length(var.release_evidence) > 10 &&
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
    condition     = contains([0, 1, 2, 3], var.desired_nodes)
    error_message = "At most three DR nodes."
  }
}
module "standby" {
  count                = var.enable_standby ? 1 : 0
  source               = "../../modules/dr_standby"
  providers            = { aws = aws.standby }
  account_id           = "414053644145"
  region               = var.standby_region
  name                 = "cosmotech-dr"
  cluster_version      = var.cluster_version
  admin_cidrs          = var.admin_cidrs
  admin_role_arn       = var.admin_role_arn
  desired_nodes        = var.desired_nodes
  queue_arn            = module.standby_sqs[0].orders_queue_arn
  restored_table_name  = "cosmotech-dr-orders-restored"
  enable_recovery_host = var.enable_recovery_host
  recovery_host_ami    = var.recovery_host_ami
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
