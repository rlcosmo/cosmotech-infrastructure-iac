resource "aws_iam_role_policy" "lab_monitoring" {
  name   = "cosmotech-lab-monitoring-readonly"
  role   = "cosmotech-itsm-dev-instance-role"
  policy = file("${path.module}/../../../ops/monitoring/read-policy.json")
}
import {
  to = aws_iam_role_policy.lab_monitoring
  id = "cosmotech-itsm-dev-instance-role:cosmotech-lab-monitoring-readonly"
}
resource "aws_ssm_document" "application_health" {
  name          = "CosmoTech-Read-Application-Health"
  document_type = "Command"
  content       = jsonencode({ schemaVersion = "2.2", description = "Read only application readiness and persistent filesystem use; no parameters or environment values", mainSteps = [{ action = "aws:runShellScript", name = "readApplicationHealth", inputs = { timeoutSeconds = "90", runCommand = ["python3 - <<'COSMOTECH_PROBE'\n${file("${path.module}/../../../ops/monitoring/host_application_probe.py")}\nCOSMOTECH_PROBE"] } }] })
  tags          = local.common_tags
}
resource "aws_iam_role" "codex_usage_publisher" {
  name                 = "cosmotech-codex-usage-publisher"
  assume_role_policy   = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { AWS = "arn:aws:iam::414053644145:user/serverless-lab-admin" } }] })
  max_session_duration = 3600
  tags                 = local.common_tags
}
resource "aws_iam_role_policy" "codex_usage_publisher" {
  name   = "publish-sanitized-quota"
  role   = aws_iam_role.codex_usage_publisher.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = ["ssm:PutParameter"], Resource = "arn:aws:ssm:us-east-1:414053644145:parameter/cosmotech/monitoring/codex-usage" }] })
}
