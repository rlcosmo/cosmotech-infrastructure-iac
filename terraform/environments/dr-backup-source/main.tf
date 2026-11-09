# Independent BACKUP owner only: does not manage/import any source application resource.
terraform {
  required_version = "= 1.17.1"
  required_providers {
    aws = { source = "hashicorp/aws", version = "= 6.62.0" }
  }
  backend "s3" {
    bucket       = "aws-serverless-lab-414053644145-tfstate-use1"
    key          = "aws-serverless-lab/dr-backup-source/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
provider "aws" {
  alias               = "source"
  region              = "us-east-1"
  allowed_account_ids = ["414053644145"]
  default_tags { tags = { Project = "CosmoTech", Environment = "dr", ManagedBy = "Terraform", Purpose = "backup-only" } }
}
variable "enabled" {
  type    = bool
  default = false
  validation {
    condition = !var.enabled || (
      var.account_release_confirmed && var.advanced_ddb_backup_confirmed &&
      length(var.destination_vault_arn) > 0 && var.budget_usd > 0 &&
      var.estimate_usd > 0 && var.estimate_usd <= var.budget_usd
    )
    error_message = "Require AWS release, verified advanced DynamoDB backup, destination vault and reviewed budget."
  }
}
variable "account_release_confirmed" {
  type    = bool
  default = false
}
variable "advanced_ddb_backup_confirmed" {
  type    = bool
  default = false
}
variable "destination_vault_arn" {
  type    = string
  default = ""
  validation {
    condition     = var.destination_vault_arn == "" || var.destination_vault_arn == "arn:aws:backup:us-east-2:414053644145:backup-vault:cosmotech-dr"
    error_message = "Only the isolated Ohio vault is allowed."
  }
}
variable "volume_arns" {
  description = "Explicit source EBS allowlist after PVC/Retain/consistency inventory. No wildcard selection."
  type        = list(string)
  default     = []
  validation {
    condition     = alltrue([for arn in var.volume_arns : can(regex("^arn:aws:ec2:us-east-1:414053644145:volume/vol-[a-f0-9]+$", arn))])
    error_message = "Only explicit source volumes; no deletion or attachment operation is granted by this configuration."
  }
}
variable "budget_usd" {
  type    = number
  default = 0
}
variable "estimate_usd" {
  type    = number
  default = 0
}
resource "aws_kms_key" "backup" {
  count                   = var.enabled ? 1 : 0
  provider                = aws.source
  description             = "CosmoTech source backup vault only"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  lifecycle { prevent_destroy = true }
}
resource "aws_backup_vault" "source" {
  count       = var.enabled ? 1 : 0
  provider    = aws.source
  name        = "cosmotech-dr-source"
  kms_key_arn = aws_kms_key.backup[0].arn
  lifecycle { prevent_destroy = true }
}
resource "aws_iam_role" "backup" {
  count    = var.enabled ? 1 : 0
  provider = aws.source
  name     = "cosmotech-dr-backup-source"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "backup.amazonaws.com" }, Action = "sts:AssumeRole",
    Condition = { StringEquals = { "aws:SourceAccount" = "414053644145" },
    ArnLike = { "aws:SourceArn" = "arn:aws:backup:us-east-1:414053644145:*" } }
  }] })
}
resource "aws_iam_role_policy_attachment" "backup" {
  count      = var.enabled ? 1 : 0
  provider   = aws.source
  role       = aws_iam_role.backup[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForBackup"
}
# AWS standard backup role requires service-specific read/snapshot permissions.
# Explicit deny prevents its use to modify protected primary configuration/data.
resource "aws_iam_role_policy" "safety" {
  count    = var.enabled ? 1 : 0
  provider = aws.source
  role     = aws_iam_role.backup[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Deny", Resource = "*", Action = [
      "ec2:TerminateInstances", "ec2:StopInstances", "ec2:DeleteVolume", "ec2:DetachVolume",
      "ec2:DeleteNatGateway", "ec2:ReleaseAddress", "ec2:ReplaceRoute", "ec2:DeleteVpc",
      "ec2:AuthorizeSecurityGroupIngress", "ec2:RevokeSecurityGroupIngress",
      "elasticloadbalancing:DeleteLoadBalancer", "route53:ChangeResourceRecordSets",
      "dynamodb:DeleteTable", "dynamodb:UpdateTable", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem",
      "eks:DeleteCluster", "eks:UpdateNodegroupConfig", "s3:DeleteObject", "s3:DeleteBucket"
    ]
  }] })
}
resource "aws_backup_plan" "hourly" {
  count    = var.enabled ? 1 : 0
  provider = aws.source
  name     = "cosmotech-dr-hourly"
  rule {
    rule_name         = "hourly-to-ohio"
    target_vault_name = aws_backup_vault.source[0].name
    schedule          = "cron(0 * * * ? *)"
    start_window      = 60
    completion_window = 180
    lifecycle { delete_after = 7 }
    copy_action {
      destination_vault_arn = var.destination_vault_arn
      lifecycle { delete_after = 7 }
    }
  }
}
resource "aws_backup_selection" "orders" {
  count        = var.enabled ? 1 : 0
  provider     = aws.source
  name         = "explicit-lab-data-only"
  plan_id      = aws_backup_plan.hourly[0].id
  iam_role_arn = aws_iam_role.backup[0].arn
  resources    = concat(["arn:aws:dynamodb:us-east-1:414053644145:table/aws-eks-istio-lab-dev-orders-data"], var.volume_arns)
  depends_on   = [aws_iam_role_policy_attachment.backup, aws_iam_role_policy.safety]
}
