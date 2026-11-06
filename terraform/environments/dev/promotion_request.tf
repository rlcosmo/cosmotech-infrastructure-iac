locals {
  promotion_request_services = {
    orders-api    = "API"
    orders-worker = "Worker"
  }
}

resource "aws_ssm_document" "promotion_request" {
  for_each      = local.promotion_request_services
  name          = "CosmoTech-Request-Orders-${each.value}-Promotion"
  document_type = "Command"
  content = jsonencode({
    schemaVersion = "2.2"
    description   = "Request Kestra authorization; cannot write GitOps or accept caller approval claims"
    parameters = {
      Commit     = { type = "String", allowedPattern = "^[a-f0-9]{40}$" }
      Digest     = { type = "String", allowedPattern = "^sha256:[a-f0-9]{64}$" }
      RunId      = { type = "String", allowedPattern = "^[0-9]{1,20}$" }
      RunAttempt = { type = "String", allowedPattern = "^[0-9]{1,4}$" }
    }
    mainSteps = [{
      action = "aws:runShellScript"
      name   = "requestPromotion"
      inputs = {
        timeoutSeconds = "60"
        runCommand = [
          "#!/bin/bash", "set -Eeuo pipefail", "umask 077",
          "PYTHONPATH=/opt/cosmotech/promotion/source BEDROCK_PROMOTION_ENABLED=false /opt/cosmotech/promotion/venv/bin/python -m automation.submit_promotion --service '${each.key}' --commit '{{ Commit }}' --digest '{{ Digest }}' --run-id '{{ RunId }}' --run-attempt '{{ RunAttempt }}'"
        ]
      }
    }]
  })
  tags = merge(local.common_tags, { Purpose = "single-promotion-authority" })
}

resource "aws_iam_role_policy" "promotion_request" {
  for_each = local.promotion_request_services
  name     = "cosmotech-request-kestra-promotion"
  role     = "cosmotech-${each.key}-github-ecr"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["ssm:SendCommand"]
        Resource = [
          aws_ssm_document.promotion_request[each.key].arn,
          "arn:aws:ec2:us-east-1:${data.aws_caller_identity.current.account_id}:instance/i-0def4e806c49cf60e"
        ]
      },
      # GetCommandInvocation has no resource-level authorization support.
      { Effect = "Allow", Action = ["ssm:GetCommandInvocation"], Resource = "*" }
    ]
  })
}
