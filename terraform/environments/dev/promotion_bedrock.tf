# Owner-authorized lab promotion controls. Bedrock integration remains disabled in runtime.
# Reuse the Kestra instance role and existing email topic.
resource "aws_iam_role_policy" "promotion_bedrock_and_notification" {
  name = "cosmotech-promotion-bedrock-notification"
  role = "cosmotech-itsm-dev-instance-role"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "OnlyRegionalNovaMicro"
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel"]
        Resource = "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-micro-v1:0"
      },
      {
        Sid      = "ExistingNotificationTopicOnly"
        Effect   = "Allow"
        Action   = ["sns:Publish", "sns:ListSubscriptionsByTopic"]
        Resource = "arn:aws:sns:us-east-1:${data.aws_caller_identity.current.account_id}:cosmotech-lab-alerts"
      },
      {
        Sid      = "EncryptOnlyExistingNotificationTopic"
        Effect   = "Allow"
        Action   = ["kms:GenerateDataKey", "kms:Decrypt"]
        Resource = aws_kms_key.alerts.arn
        Condition = {
          StringEquals = {
            "kms:ViaService"                         = "sns.us-east-1.amazonaws.com"
            "kms:EncryptionContext:aws:sns:topicArn" = "arn:aws:sns:us-east-1:${data.aws_caller_identity.current.account_id}:cosmotech-lab-alerts"
          }
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "promotion_evidence_read" {
  name = "cosmotech-promotion-evidence-read"
  role = "cosmotech-itsm-dev-instance-role"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = "arn:aws:secretsmanager:us-east-1:${data.aws_caller_identity.current.account_id}:secret:cosmotech/github/app-*" },
      { Effect = "Allow", Action = ["ecr:DescribeImages"], Resource = [for app in ["orders-api", "orders-worker"] : "arn:aws:ecr:us-east-1:${data.aws_caller_identity.current.account_id}:repository/aws-eks-istio-lab-dev-${app}"] },
      { Effect = "Allow", Action = ["ssm:SendCommand"], Resource = [aws_ssm_document.promotion_argo_state.arn, "arn:aws:ec2:us-east-1:${data.aws_caller_identity.current.account_id}:instance/i-0b33fdd51332e04e5"] },
      { Effect = "Allow", Action = ["ssm:GetCommandInvocation"], Resource = "*" },
      { Effect = "Allow", Action = ["eks:DescribeCluster"], Resource = "arn:aws:eks:us-east-1:${data.aws_caller_identity.current.account_id}:cluster/aws-eks-istio-lab-dev" },
      # Metric reads have no resource-level authorization; only the fixed collector uses them.
      { Effect = "Allow", Action = ["cloudwatch:GetMetricStatistics"], Resource = "*" }
    ]
  })
}

resource "aws_ssm_document" "promotion_argo_state" {
  name          = "CosmoTech-Promotion-Argo-State"
  document_type = "Command"
  content = jsonencode({
    schemaVersion = "2.2"
    description   = "Read only Orders application and signature enforcement evidence"
    parameters = {
      Application = { type = "String", allowedValues = ["orders-api", "orders-worker"] }
    }
    mainSteps = [{ action = "aws:runShellScript", name = "readState", inputs = { runCommand = ["#!/bin/bash", "set -Eeuo pipefail", "export KUBECONFIG=/etc/rancher/k3s/k3s.yaml", "install -d -m 0700 /opt/cosmotech/promotion", "printf '%s' '${base64encode(file("${path.module}/../../../ops/promotion/read_argo_state.py"))}' | base64 --decode > /opt/cosmotech/promotion/read_argo_state.py", "python3 /opt/cosmotech/promotion/read_argo_state.py '{{ Application }}'"] } }]
  })
  tags = merge(local.common_tags, { Purpose = "promotion-evidence" })
}

