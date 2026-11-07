# No WAF, Lambda, compute, DNS, DR or paid Secrets Manager resources in this scope.
# Initial IAM bootstrap needs an existing write-authorized temporary role; the
# read-only plan/maintenance roles cannot manufacture their own permissions.
locals {
  lab_v2_oidc_trust = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Action = "sts:AssumeRoleWithWebIdentity"
    Principal = { Federated = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/token.actions.githubusercontent.com" }
    Condition = { StringEquals = {
      "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
      "token.actions.githubusercontent.com:sub" = "repo:rlcosmo@124368446/cosmotech-infrastructure-iac@1445869018:ref:refs/heads/main"
    } }
  }] })
  lab_v2_bucket = "aws-serverless-lab-${data.aws_caller_identity.current.account_id}-tfstate-use1"
  lab_v2_state  = "arn:aws:s3:::${local.lab_v2_bucket}/aws-serverless-lab/dev/terraform.tfstate"
  lab_v2_prefix = "lab-v2/iac-evidence/"
  lab_v2_parameters = [for name in ["portal-auth", "evidence-public-key", "grafana-publisher"] :
    "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/cosmotech/lab-v2/${name}"
  ]
  lab_v2_evidence_policy = jsondecode(templatefile("${path.module}/../../../ops/iac-portal/evidence-policy.json.tftpl", {
    account = data.aws_caller_identity.current.account_id, region = var.aws_region
  }))
}

resource "aws_iam_role" "iac_evidence" {
  name                 = "cosmotech-iac-evidence"
  assume_role_policy   = local.lab_v2_oidc_trust
  max_session_duration = 3600
  tags                 = merge(local.common_tags, { Purpose = "lab-v2-metadata-evidence" })
  lifecycle { prevent_destroy = true }
}
resource "aws_iam_role_policy" "iac_evidence" {
  name = "lab-v2-metadata-and-encrypted-evidence"
  role = aws_iam_role.iac_evidence.name
  policy = jsonencode({ Version = "2012-10-17", Statement = concat(local.lab_v2_evidence_policy.Statement, [{
    Sid       = "EncryptedEvidenceOnly", Effect = "Allow", Action = ["s3:PutObject"]
    Resource  = "arn:aws:s3:::${local.lab_v2_bucket}/${local.lab_v2_prefix}*"
    Condition = { StringEquals = { "s3:x-amz-server-side-encryption" = "AES256" } }
  }]) })
}
resource "aws_iam_role" "lab_v2_deploy" {
  name                 = "cosmotech-lab-v2-deploy"
  assume_role_policy   = local.lab_v2_oidc_trust
  max_session_duration = 3600
  tags                 = merge(local.common_tags, { Purpose = "lab-v2-publication-only" })
  lifecycle { prevent_destroy = true }
}
resource "aws_iam_role_policy" "lab_v2_deploy" {
  name = "lab-v2-scoped-publication"
  role = aws_iam_role.lab_v2_deploy.name
  policy = jsonencode({ Version = "2012-10-17", Statement = concat([
    for statement in local.lab_v2_evidence_policy.Statement : statement
    if contains(["iamMetadata", "ssmMetadata", "BackendRead", "BackendList"], statement.Sid)
    ], [
    { Sid = "SaveCanonicalState", Effect = "Allow", Action = ["s3:PutObject"], Resource = local.lab_v2_state },
    { Sid = "TransientStateLock", Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = "${local.lab_v2_state}.tflock" },
    { Sid = "PublishFixedDocument", Effect = "Allow", Action = ["ssm:CreateDocument", "ssm:UpdateDocument", "ssm:UpdateDocumentDefaultVersion", "ssm:AddTagsToResource"], Resource = aws_ssm_document.lab_v2_publication.arn },
    { Sid = "RunFixedPublisher", Effect = "Allow", Action = ["ssm:SendCommand"], Resource = [aws_ssm_document.lab_v2_publication.arn, "arn:aws:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:instance/${module.itsm_host.instance_id}"] },
    { Sid = "ReadCommandStatus", Effect = "Allow", Action = ["ssm:GetCommandInvocation"], Resource = "*" },
    { Sid = "ReadPublicRecipient", Effect = "Allow", Action = ["ssm:GetParameter"], Resource = local.lab_v2_parameters[1] }
  ]) })
}

resource "aws_iam_role_policy" "lab_v2_runtime" {
  name = "lab-v2-portal-and-grafana-publication"
  role = "cosmotech-itsm-dev-instance-role"
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Sid = "QueueMetrics", Effect = "Allow", Action = ["cloudwatch:GetMetricStatistics"], Resource = "*", Condition = { StringEquals = { "aws:RequestedRegion" = var.aws_region } } },
    { Sid = "QueueMetadata", Effect = "Allow", Action = ["sqs:GetQueueAttributes"], Resource = [for queue in ["aws-eks-istio-lab-dev-orders-eks", "aws-eks-istio-lab-dev-orders-eks-dlq"] : "arn:aws:sqs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:${queue}"] },
    { Sid = "PortalRuntimeCredentials", Effect = "Allow", Action = ["ssm:GetParameter", "ssm:PutParameter"], Resource = local.lab_v2_parameters },
    { Sid = "ParameterEncryptionOnly", Effect = "Allow", Action = ["kms:Decrypt", "kms:Encrypt"], Resource = "arn:aws:kms:${var.aws_region}:${data.aws_caller_identity.current.account_id}:key/*", Condition = { StringEquals = { "kms:ViaService" = "ssm.${var.aws_region}.amazonaws.com", "kms:EncryptionContext:PARAMETER_ARN" = [local.lab_v2_parameters[0], local.lab_v2_parameters[2]] } } },
    { Sid = "ReadEncryptedReports", Effect = "Allow", Action = ["s3:GetObject"], Resource = "arn:aws:s3:::${local.lab_v2_bucket}/${local.lab_v2_prefix}*" },
    { Sid = "ListReportPrefixOnly", Effect = "Allow", Action = ["s3:ListBucket"], Resource = "arn:aws:s3:::${local.lab_v2_bucket}", Condition = { StringLike = { "s3:prefix" = "${local.lab_v2_prefix}*" } } },
    { Sid = "ProvisionGrafanaEditor", Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = aws_secretsmanager_secret.tool_credentials["grafana"].arn }
  ] })
}
resource "aws_iam_role_policy" "lab_v2_maintenance" {
  name = "lab-v2-queue-metadata"
  role = aws_iam_role.maintenance.name
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Action = ["sqs:GetQueueAttributes"], Resource = [for queue in ["aws-eks-istio-lab-dev-orders-eks", "aws-eks-istio-lab-dev-orders-eks-dlq"] : "arn:aws:sqs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:${queue}"]
  }] })
}

resource "aws_ssm_document" "lab_v2_publication" {
  name          = "CosmoTech-Publish-Lab-V2"
  document_type = "Command"
  content = jsonencode({ schemaVersion = "2.2", parameters = { Mode = { type = "String", default = "install", allowedValues = ["install", "validate"] } }, description = "Publish only the authenticated Lab V2 portal and fixed Grafana dashboards on the existing private ITSM host", mainSteps = [{
    action = "aws:runShellScript", name = "publishLabV2", inputs = { timeoutSeconds = "600", runCommand = [
      "#!/bin/bash", "set -Eeuo pipefail", "umask 077", "install -d -m 0700 /opt/cosmotech/lab-v2-source",
      "printf '%s' '${filebase64("${path.module}/../../../dist/lab-v2-publication.tgz")}' | base64 --decode > /opt/cosmotech/lab-v2-source/bundle.tgz",
      "tar -xzf /opt/cosmotech/lab-v2-source/bundle.tgz --no-same-owner --no-same-permissions -C /opt/cosmotech/lab-v2-source",
      "python3 /opt/cosmotech/lab-v2-source/publication_runtime.py {{ Mode }}"
    ] }
  }] })
  tags = merge(local.common_tags, { Purpose = "lab-v2-publication" })
}
output "iac_evidence_role_arn" { value = aws_iam_role.iac_evidence.arn }
output "lab_v2_deploy_role_arn" { value = aws_iam_role.lab_v2_deploy.arn }
output "lab_v2_portal_url" { value = "https://kestra.cosmotechsolutions.com.br/iac/" }

output "lab_v2_publication_document" {
  value = {
    version   = aws_ssm_document.lab_v2_publication.document_version
    hash      = aws_ssm_document.lab_v2_publication.hash
    hash_type = aws_ssm_document.lab_v2_publication.hash_type
  }
}
