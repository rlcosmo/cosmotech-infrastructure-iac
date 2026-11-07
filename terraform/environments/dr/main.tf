# Preparation only. EKS, images, data replication and routing are not provisioned.
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
  alias  = "standby"
  region = var.standby_region
  default_tags {
    tags = { Project = "CosmoTech", Environment = "dr", ManagedBy = "Terraform", Mode = "passive" }
  }
}

variable "standby_region" {
  description = "Candidate only; no cheapest-region claim without current total-cost evidence."
  type        = string
  default     = "us-east-2"
  validation {
    condition     = contains(["us-east-2", "us-west-2", "sa-east-1"], var.standby_region)
    error_message = "Use an explicitly reviewed candidate different from primary us-east-1."
  }
}

variable "enable_standby_queues" {
  description = "Disabled by default; source-only scaffold, not an automatic failover controller."
  type        = bool
  default     = false
  validation {
    condition = !var.enable_standby_queues || (
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
  count                    = var.enable_standby_queues ? 1 : 0
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
    candidate_region      = var.standby_region
    state_key             = "aws-serverless-lab/dr/terraform.tfstate"
    queue_replication     = "not-native-requires-durable-outbox"
    data_replication      = "pending-primary-table-ownership-and-schema-review"
    automatic_failover    = false
    provisioning_enabled  = var.enable_standby_queues
    financial_ceiling_usd = var.monthly_budget_usd
  }
}
