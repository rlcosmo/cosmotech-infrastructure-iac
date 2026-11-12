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
variable "operator_source_role_arn" {
  description = "Existing authorized human/SSO role; empty means no recovery operator is created."
  type        = string
  default     = ""
  validation {
    condition     = var.operator_source_role_arn == "" || can(regex("^arn:aws:iam::414053644145:role/[A-Za-z0-9+=,.@_/-]+$", var.operator_source_role_arn))
    error_message = "Only an explicitly authorized non-root local role may assume the recovery operator."
  }
}
resource "aws_iam_role" "operator" {
  count                = var.enabled && var.operator_source_role_arn != "" ? 1 : 0
  name                 = "cosmotech-dr-operator"
  max_session_duration = 3600
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Action = "sts:AssumeRole", Principal = { AWS = var.operator_source_role_arn }
  }] })
  tags = { Project = "CosmoTech", Environment = "dr", Purpose = "secondary-recovery-only" }
}
resource "aws_iam_role_policy" "operator" {
  count = var.enabled && var.operator_source_role_arn != "" ? 1 : 0
  role  = aws_iam_role.operator[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["eks:DescribeCluster"], Resource = "arn:aws:eks:us-east-2:414053644145:cluster/cosmotech-dr" },
    { Effect = "Allow", Action = ["eks:DescribeNodegroup", "eks:UpdateNodegroupConfig"], Resource = "arn:aws:eks:us-east-2:414053644145:nodegroup/cosmotech-dr/dr-ondemand/*" },
    { Effect = "Allow", Action = ["ec2:DescribeInstances", "ec2:DescribeVolumes"], Resource = "*", Condition = { StringEquals = { "aws:RequestedRegion" = "us-east-2" } } },
    { Effect = "Allow", Action = ["ec2:StartInstances"], Resource = "arn:aws:ec2:us-east-2:414053644145:instance/*", Condition = { StringEquals = { "ec2:ResourceTag/Name" = "cosmotech-dr-itsm-recovery" } } },
    { Effect = "Allow", Action = ["ssm:StartSession"], Resource = "arn:aws:ec2:us-east-2:414053644145:instance/*", Condition = { StringEquals = { "ssm:resourceTag/Name" = "cosmotech-dr-itsm-recovery" } } },
    { Effect = "Allow", Action = ["ssm:StartSession"], Resource = ["arn:aws:ssm:us-east-2::document/AWS-StartPortForwardingSession", "arn:aws:ssm:us-east-2:414053644145:document/SSM-SessionManagerRunShell"] },
    { Effect = "Deny", Action = ["ec2:TerminateInstances", "ec2:DeleteVolume", "eks:DeleteCluster", "route53:ChangeResourceRecordSets", "elasticloadbalancing:DeleteLoadBalancer", "ec2:DeleteNatGateway", "ec2:ReleaseAddress"], Resource = "*" }
  ] })
}
output "operator_role_arn" { value = try(aws_iam_role.operator[0].arn, null) }
resource "aws_iam_role" "image_publisher" {
  count                = var.enabled ? 1 : 0
  name                 = "cosmotech-dr-image-publisher"
  max_session_duration = 3600
  assume_role_policy   = aws_iam_role.observer[0].assume_role_policy
  tags                 = { Project = "CosmoTech", Environment = "dr", Purpose = "dr-image-publication-only" }
}
resource "aws_iam_role_policy" "image_publisher" {
  count = var.enabled ? 1 : 0
  role  = aws_iam_role.image_publisher[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Effect = "Allow", Action = ["ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:DescribeImages", "ecr:BatchGetImage"], Resource = ["arn:aws:ecr:us-east-2:414053644145:repository/cosmotech-dr-orders-outbox", "arn:aws:ecr:us-east-2:414053644145:repository/aws-eks-istio-lab-dev-orders-api", "arn:aws:ecr:us-east-2:414053644145:repository/aws-eks-istio-lab-dev-orders-worker"] },
    { Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability", "ecr:DescribeImages"], Resource = ["arn:aws:ecr:us-east-1:414053644145:repository/aws-eks-istio-lab-dev-orders-api", "arn:aws:ecr:us-east-1:414053644145:repository/aws-eks-istio-lab-dev-orders-worker"] },
    { Effect = "Deny", Action = "*", Resource = "*", Condition = { StringNotEquals = { "aws:RequestedRegion" = ["us-east-1", "us-east-2"] } } }
  ] })
}
resource "aws_iam_role_policy" "observer_outbox_metadata" {
  count = var.enabled ? 1 : 0
  role  = aws_iam_role.observer[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["dynamodb:DescribeTable", "dynamodb:DescribeContinuousBackups", "dynamodb:DescribeTimeToLive", "dynamodb:DescribeContributorInsights", "dynamodb:DescribeKinesisStreamingDestination", "dynamodb:ListTagsOfResource"], Resource = "arn:aws:dynamodb:us-east-2:414053644145:table/cosmotech-dr-outbox" },
    { Effect = "Allow", Action = ["ecr:DescribeRepositories", "ecr:ListTagsForResource"], Resource = "arn:aws:ecr:us-east-2:414053644145:repository/cosmotech-dr-orders-outbox" },
    { Effect = "Allow", Action = ["backup:GetBackupPlan", "backup:GetBackupSelection", "backup:ListTags"], Resource = "arn:aws:backup:us-east-2:414053644145:backup-plan:*" }
  ] })
}
