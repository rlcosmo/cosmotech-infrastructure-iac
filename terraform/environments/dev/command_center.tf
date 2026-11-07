# Secret values are published by the Zabbix host, never by Terraform.
resource "aws_iam_role_policy" "command_center_publish" {
  name   = "publish-command-center-service-identity"
  role   = aws_iam_role.zabbix_host.name
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = ["secretsmanager:GetSecretValue", "secretsmanager:PutSecretValue"], Resource = aws_secretsmanager_secret.tool_credentials["grafana-command-center"].arn }] })
}
resource "aws_iam_role_policy" "command_center_read" {
  name   = "read-command-center-service-identity"
  role   = "cosmotech-itsm-dev-instance-role"
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = aws_secretsmanager_secret.tool_credentials["grafana-command-center"].arn }] })
}
resource "aws_ssm_document" "command_center_identity" {
  name          = "CosmoTech-Provision-Command-Center"
  document_type = "Command"
  content = jsonencode({ schemaVersion = "2.2", description = "Provision a read-only Zabbix service identity and publish it directly to Secrets Manager", mainSteps = [{ action = "aws:runShellScript", name = "provisionIdentity", inputs = { timeoutSeconds = "300", runCommand = [
    "#!/bin/bash", "set -Eeuo pipefail", "umask 077", "install -d -m 0700 /opt/cosmotech/zabbix-monitoring-source",
    "printf '%s' '${base64encode(file("${path.module}/../../../ops/zabbix-monitoring/configure_command_center.py"))}' | base64 --decode > /opt/cosmotech/zabbix-monitoring-source/configure_command_center.py",
    "python3 /opt/cosmotech/zabbix-monitoring-source/configure_command_center.py"
  ] } }] })
  tags = local.common_tags
}
resource "aws_ssm_document" "command_center_install" {
  name          = "CosmoTech-Install-Command-Center"
  document_type = "Command"
  content = jsonencode({ schemaVersion = "2.2", description = "Install the existing monitoring collector with read-only Zabbix event export; keep service timers and metrics endpoint", mainSteps = [{ action = "aws:runShellScript", name = "installCollector", inputs = { timeoutSeconds = "300", runCommand = [
    "#!/bin/bash", "set -Eeuo pipefail", "umask 077",
    "test -f /etc/systemd/system/cosmotech-monitoring.service", "install -d -m 0755 /opt/cosmotech/monitoring",
    "printf '%s' '${base64encode(file("${path.module}/../../../ops/monitoring/lab_monitoring.py"))}' | base64 --decode > /opt/cosmotech/monitoring/lab_monitoring.py",
    "printf '%s' '${base64encode(file("${path.module}/../../../ops/monitoring/sqs_canary_metrics.py"))}' | base64 --decode > /opt/cosmotech/monitoring/sqs_canary_metrics.py",
    "chmod 0644 /opt/cosmotech/monitoring/sqs_canary_metrics.py", "python3 -m py_compile /opt/cosmotech/monitoring/sqs_canary_metrics.py",
    "chmod 0644 /opt/cosmotech/monitoring/lab_monitoring.py", "python3 -m py_compile /opt/cosmotech/monitoring/lab_monitoring.py",
    "systemctl start --no-block cosmotech-monitoring.service"
  ] } }] })
  tags = local.common_tags
}
