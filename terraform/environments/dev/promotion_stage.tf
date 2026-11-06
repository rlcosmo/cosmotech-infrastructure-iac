resource "aws_ssm_document" "promotion_stage" {
  name          = "CosmoTech-Execute-Promotion-Stage"
  document_type = "Command"
  content = jsonencode({
    schemaVersion = "2.2"
    description   = "Fixed trusted Kestra stage; no caller shell, classifications or approval claims"
    parameters = {
      Stage       = { type = "String", allowedValues = ["collect", "policy", "dispatch", "tick"] }
      ExecutionId = { type = "String", allowedPattern = "^[A-Za-z0-9_-]{1,100}$" }
    }
    mainSteps = [{
      action = "aws:runShellScript", name = "executeTrustedStage",
      inputs = {
        timeoutSeconds = "240"
        runCommand = [
          "#!/bin/bash", "set -Eeuo pipefail", "umask 077",
          "export PYTHONPATH=/opt/cosmotech/promotion/source PYTHONDONTWRITEBYTECODE=1 BEDROCK_PROMOTION_ENABLED=false",
          "/opt/cosmotech/promotion/venv/bin/python -m automation.gate_runner --stage '{{ Stage }}' --execution-id '{{ ExecutionId }}'"
        ]
      }
    }]
  })
  tags = merge(local.common_tags, { Purpose = "single-promotion-authority" })
}

resource "aws_iam_role_policy" "promotion_stage" {
  name = "cosmotech-fixed-promotion-stage"
  role = "cosmotech-itsm-dev-instance-role"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow", Action = ["ssm:SendCommand"], Resource = [
          aws_ssm_document.promotion_stage.arn,
          aws_ssm_document.orders_gitops_sync.arn,
          "arn:aws:ec2:us-east-1:${data.aws_caller_identity.current.account_id}:instance/i-0def4e806c49cf60e",
          "arn:aws:ec2:us-east-1:${data.aws_caller_identity.current.account_id}:instance/i-0b33fdd51332e04e5"
        ]
      },
      { Effect = "Allow", Action = ["ssm:GetCommandInvocation"], Resource = "*" }
    ]
  })
}
