# NEW source journal only. No existing API, role, queue, state or network is managed.
terraform {
  required_version = "= 1.17.1"
  required_providers {
    aws = { source = "hashicorp/aws", version = "= 6.62.0" }
  }
  backend "s3" {
    bucket       = "aws-serverless-lab-414053644145-tfstate-use1"
    key          = "aws-serverless-lab/orders-outbox-source/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
provider "aws" {
  region              = "us-east-1"
  allowed_account_ids = ["414053644145"]
  default_tags { tags = { Project = "CosmoTech", Environment = "dr-preparation", ManagedBy = "Terraform" } }
}
variable "enabled" {
  type    = bool
  default = false
  validation {
    condition     = !var.enabled || (var.account_release_confirmed && var.budget_usd > 0 && var.estimate_usd > 0 && var.estimate_usd <= var.budget_usd)
    error_message = "Official account release and reviewed cost within explicit ceiling required."
  }
}
variable "account_release_confirmed" {
  type    = bool
  default = false
}
variable "budget_usd" {
  type    = number
  default = 0
}
variable "estimate_usd" {
  type    = number
  default = 0
}
resource "aws_dynamodb_table" "journal" {
  count                       = var.enabled ? 1 : 0
  name                        = "cosmotech-orders-outbox"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "order_id"
  deletion_protection_enabled = true
  attribute {
    name = "order_id"
    type = "S"
  }
  attribute {
    name = "dispatch_bucket"
    type = "S"
  }
  attribute {
    name = "due_at"
    type = "N"
  }
  global_secondary_index {
    name            = "dispatch-due"
    projection_type = "KEYS_ONLY"
    key_schema {
      attribute_name = "dispatch_bucket"
      key_type       = "HASH"
    }
    key_schema {
      attribute_name = "due_at"
      key_type       = "RANGE"
    }
  }
  point_in_time_recovery { enabled = true }
  server_side_encryption { enabled = true }
  lifecycle { prevent_destroy = true }
}
variable "manage_application_policies" {
  description = "False when the permanent IAM bootstrap owns the reviewed producer/publisher policies."
  type        = bool
  default     = false
}
resource "aws_iam_policy" "producer" {
  count = var.enabled && var.manage_application_policies ? 1 : 0
  name  = "cosmotech-orders-outbox-producer"
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:PutItem"], Resource = aws_dynamodb_table.journal[0].arn
  }] })
}
resource "aws_iam_policy" "publisher" {
  count = var.enabled && var.manage_application_policies ? 1 : 0
  name  = "cosmotech-orders-outbox-publisher"
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["dynamodb:Query", "dynamodb:UpdateItem"], Resource = [aws_dynamodb_table.journal[0].arn, "${aws_dynamodb_table.journal[0].arn}/index/dispatch-due"] },
    { Effect = "Allow", Action = ["sqs:SendMessage"], Resource = "arn:aws:sqs:us-east-1:414053644145:aws-eks-istio-lab-dev-orders-eks" }
  ] })
}
# Policies are deliberately NOT attached to existing roles by this root.
# Bind via the real role owner's reviewed rollout after release/effective-access tests.
output "journal_arn" { value = try(aws_dynamodb_table.journal[0].arn, null) }

variable "publisher_role_arn" {
  description = "Reviewed bounded role from the permanent IAM bootstrap; association is opt-in."
  type        = string
  default     = ""
  validation {
    condition     = contains(["", "arn:aws:iam::414053644145:role/cosmotech-source-outbox-publisher"], var.publisher_role_arn)
    error_message = "Only the dedicated bounded source publisher role may be associated."
  }
}
resource "aws_eks_pod_identity_association" "publisher" {
  count                = var.enabled && var.publisher_role_arn != "" ? 1 : 0
  cluster_name         = "aws-eks-istio-lab-dev"
  namespace            = "orders-api"
  service_account      = "orders-outbox-publisher"
  role_arn             = var.publisher_role_arn
  disable_session_tags = false
  tags                 = { DRPublisher = "source-outbox" }
}
