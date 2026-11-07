# One-time administrator stack; isolated from the V1/dev state and paid resources.
terraform {
  required_version = "= 1.17.1"
  required_providers {
    aws = { source = "hashicorp/aws", version = "= 6.62.0" }
  }
  backend "s3" {
    key          = "lab-v2/admin-bootstrap/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
variable "expected_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.expected_account_id))
    error_message = "Use the account already owning the canonical backend."
  }
}
variable "itsm_instance_id" {
  type = string
  validation {
    condition     = can(regex("^i-[a-f0-9]{17}$", var.itsm_instance_id))
    error_message = "Use only the existing fixed ITSM target."
  }
}
variable "authorization_expires_at" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$", var.authorization_expires_at))
    error_message = "An explicit UTC authorization expiry is required."
  }
}
provider "aws" {
  region              = "us-east-1"
  allowed_account_ids = [var.expected_account_id]
  default_tags {
    tags = { Project = "CosmoTech", Environment = "lab-v2", ManagedBy = "Terraform", Purpose = "bounded-admin-bootstrap" }
  }
}
locals {
  account = var.expected_account_id
  bucket  = "aws-serverless-lab-${local.account}-tfstate-use1"
  state   = "arn:aws:s3:::${local.bucket}/aws-serverless-lab/dev/terraform.tfstate"
  trust_subjects = {
    infrastructure = "repo:rlcosmo@124368446/cosmotech-infrastructure-iac@1445869018:ref:refs/heads/main"
    rbac           = "repo:rlcosmo@124368446/cosmotech-kubernetes-rbac@1445869219:ref:refs/heads/main"
  }
  metadata = jsondecode(templatefile("${path.module}/../../../ops/iac-portal/evidence-policy.json.tftpl", { account = local.account, region = "us-east-1" }))
  read_statements = [for statement in local.metadata.Statement : statement
    if contains(["iamMetadata", "ec2Metadata", "eksMetadata", "elasticloadbalancingMetadata", "ssmMetadata", "secretsmanagerMetadata", "BackendRead", "BackendList", "PublicAMIOnly"], statement.Sid)
  ]
  writes = [
    { Sid = "CreateOnlyReviewedRoles", Effect = "Allow", Action = ["iam:CreateRole", "iam:TagRole"], Resource = [for name in ["cosmotech-iac-evidence", "cosmotech-lab-v2-deploy"] : "arn:aws:iam::${local.account}:role/${name}"] },
    { Sid = "ReviewedInlinePoliciesOnly", Effect = "Allow", Action = ["iam:PutRolePolicy"], Resource = [for name in ["cosmotech-iac-evidence", "cosmotech-lab-v2-deploy", "cosmotech-itsm-dev-instance-role", "cosmotech-lab-maintenance"] : "arn:aws:iam::${local.account}:role/${name}"] },
    { Sid = "SaveCanonicalState", Effect = "Allow", Action = ["s3:PutObject"], Resource = [local.state] },
    { Sid = "TransientLockOnly", Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = ["${local.state}.tflock"] },
    { Sid = "FixedPublisherDocument", Effect = "Allow", Action = ["ssm:CreateDocument", "ssm:UpdateDocument", "ssm:UpdateDocumentDefaultVersion", "ssm:AddTagsToResource"], Resource = ["arn:aws:ssm:us-east-1:${local.account}:document/CosmoTech-Publish-Lab-V2"] },
    { Sid = "ExecuteFixedPublisher", Effect = "Allow", Action = ["ssm:SendCommand"], Resource = ["arn:aws:ssm:us-east-1:${local.account}:document/CosmoTech-Publish-Lab-V2", "arn:aws:ec2:us-east-1:${local.account}:instance/${var.itsm_instance_id}"] },
    { Sid = "ReadInvocationStatus", Effect = "Allow", Action = ["ssm:GetCommandInvocation"], Resource = ["*"] }
  ]
}
resource "aws_iam_role" "bootstrap" {
  for_each             = local.trust_subjects
  name                 = each.key == "infrastructure" ? "cosmotech-lab-v2-bootstrap" : "cosmotech-lab-v2-rbac-bootstrap"
  max_session_duration = 3600
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Action = "sts:AssumeRoleWithWebIdentity"
    Principal = { Federated = "arn:aws:iam::${local.account}:oidc-provider/token.actions.githubusercontent.com" }
    Condition = {
      StringEquals = { "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com", "token.actions.githubusercontent.com:sub" = each.value }
      DateLessThan = { "aws:CurrentTime" = var.authorization_expires_at }
    }
  }] })
  lifecycle { prevent_destroy = true }
}
resource "aws_iam_role_policy" "infrastructure" {
  name = "bounded-reviewed-lab-v2-bootstrap"
  role = aws_iam_role.bootstrap["infrastructure"].id
  policy = jsonencode({ Version = "2012-10-17", Statement = concat(local.read_statements, [for statement in local.writes : merge(statement, {
    Condition = { DateLessThan = { "aws:CurrentTime" = var.authorization_expires_at } }
  })]) })
}
resource "aws_iam_role_policy" "rbac" {
  name = "describe-only-existing-cluster"
  role = aws_iam_role.bootstrap["rbac"].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Action = ["eks:DescribeCluster"], Resource = "arn:aws:eks:us-east-1:${local.account}:cluster/aws-eks-istio-lab-dev"
  }] })
}
resource "aws_eks_access_entry" "rbac" {
  cluster_name      = "aws-eks-istio-lab-dev"
  principal_arn     = aws_iam_role.bootstrap["rbac"].arn
  kubernetes_groups = ["cosmotech-lab-v2-rbac-bootstrap"]
  type              = "STANDARD"
  lifecycle { prevent_destroy = true }
}
output "bootstrap_role_arn" { value = aws_iam_role.bootstrap["infrastructure"].arn }
output "rbac_role_arn" { value = aws_iam_role.bootstrap["rbac"].arn }
output "authorization_expires_at" { value = var.authorization_expires_at }
