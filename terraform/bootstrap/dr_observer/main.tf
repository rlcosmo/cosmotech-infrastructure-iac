terraform {
  required_version = "= 1.17.1"
  required_providers {
    aws = { source = "hashicorp/aws", version = "= 6.62.0" }
  }
  backend "s3" {
    bucket       = "aws-serverless-lab-414053644145-tfstate-use1"
    key          = "aws-serverless-lab/dr-bootstrap/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
provider "aws" {
  region              = "us-east-2"
  allowed_account_ids = ["414053644145"]
}
variable "enabled" {
  type    = bool
  default = false
  validation {
    condition     = !var.enabled || var.account_release_confirmed
    error_message = "No bootstrap under account restriction."
  }
}
variable "account_release_confirmed" {
  type    = bool
  default = false
}
resource "aws_iam_role" "observer" {
  count                = var.enabled ? 1 : 0
  name                 = "cosmotech-dr-observer"
  max_session_duration = 3600
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Action = "sts:AssumeRoleWithWebIdentity",
    Principal = { Federated = "arn:aws:iam::414053644145:oidc-provider/token.actions.githubusercontent.com" },
    Condition = { StringEquals = {
      "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com",
      "token.actions.githubusercontent.com:sub" = "repo:rlcosmo@124368446/cosmotech-infrastructure-iac@1445869018:ref:refs/heads/main"
    } }
  }] })
  tags = { Project = "CosmoTech", Environment = "dr", Purpose = "read-only-plan" }
}
resource "aws_iam_role_policy" "observer" {
  count = var.enabled ? 1 : 0
  role  = aws_iam_role.observer[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ec2:Describe*", "eks:DescribeClusterVersions", "eks:DescribeAddonVersions", "servicequotas:ListServiceQuotas"], Resource = "*", Condition = { StringEquals = { "aws:RequestedRegion" = "us-east-2" } } },
    { Effect = "Allow", Action = ["eks:DescribeCluster", "eks:DescribeNodegroup", "eks:DescribeAddon", "eks:ListAccessEntries", "eks:DescribeAccessEntry", "eks:ListAssociatedAccessPolicies", "eks:ListPodIdentityAssociations", "eks:DescribePodIdentityAssociation"], Resource = ["arn:aws:eks:us-east-2:414053644145:cluster/cosmotech-dr", "arn:aws:eks:us-east-2:414053644145:*/cosmotech-dr/*"] },
    { Effect = "Allow", Action = ["iam:GetRole", "iam:GetRolePolicy", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies"], Resource = "arn:aws:iam::414053644145:role/cosmotech-dr-*" },
    { Effect = "Allow", Action = ["iam:GetInstanceProfile"], Resource = "arn:aws:iam::414053644145:instance-profile/cosmotech-dr-*" },
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = "arn:aws:s3:::aws-serverless-lab-414053644145-tfstate-use1", Condition = { StringLike = { "s3:prefix" = ["aws-serverless-lab/dr/*", "env:/"] } } },
    { Effect = "Allow", Action = ["s3:GetBucketLocation"], Resource = "arn:aws:s3:::aws-serverless-lab-414053644145-tfstate-use1" },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = "arn:aws:s3:::aws-serverless-lab-414053644145-tfstate-use1/aws-serverless-lab/dr/terraform.tfstate" },
    { Effect = "Allow", Action = ["s3:GetBucket*", "s3:GetEncryptionConfiguration", "s3:GetLifecycleConfiguration"], Resource = "arn:aws:s3:::cosmotech-dr-414053644145-us-east-2" },
    { Effect = "Allow", Action = ["sqs:GetQueueAttributes", "sqs:ListQueueTags"], Resource = "arn:aws:sqs:us-east-2:414053644145:cosmotech-dr-passive-*" },
    { Effect = "Allow", Action = ["ecr:DescribeRepositories", "ecr:ListTagsForResource"], Resource = "arn:aws:ecr:us-east-2:414053644145:repository/aws-eks-istio-lab-dev-orders-*" },
    { Effect = "Allow", Action = ["backup:DescribeBackupVault", "backup:ListTags"], Resource = "arn:aws:backup:us-east-2:414053644145:backup-vault:cosmotech-dr" },
    { Effect = "Allow", Action = ["logs:DescribeLogGroups", "logs:ListTagsForResource"], Resource = "*", Condition = { StringEquals = { "aws:RequestedRegion" = "us-east-2" } } },
    { Effect = "Allow", Action = ["kms:DescribeKey", "kms:GetKeyPolicy", "kms:GetKeyRotationStatus", "kms:ListResourceTags"], Resource = "arn:aws:kms:us-east-2:414053644145:key/*", Condition = { StringEquals = { "aws:ResourceTag/Environment" = "dr" } } }
  ] })
}
output "role_arn" { value = try(aws_iam_role.observer[0].arn, null) }
