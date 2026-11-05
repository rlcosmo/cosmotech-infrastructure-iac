locals {
  zabbix_agent_hosts = {
    itsm   = { hostname = "cosmotech-itsm-dev", instance_id = "i-0def4e806c49cf60e", role = "cosmotech-itsm-dev-instance-role", ip = "10.20.11.150", sg = module.itsm_host.security_group_id }
    argocd = { hostname = "cosmotech-argocd-dev", instance_id = "i-0b33fdd51332e04e5", role = "cosmotech-argocd-dev-host", ip = "10.20.11.199", sg = aws_security_group.argocd.id }
    server = { hostname = "cosmotech-zabbix-dev", instance_id = "i-02878f35eed1572d0", role = "cosmotech-zabbix-dev-host", ip = "10.20.11.207", sg = aws_security_group.zabbix.id }
  }
}
resource "aws_iam_role_policy" "zabbix_agent_identity" {
  for_each = local.zabbix_agent_hosts
  name     = "read-own-zabbix-agent-identity"
  role     = each.value.role
  policy   = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = aws_secretsmanager_secret.tool_credentials["zabbix-agent-${each.key}"].arn }] })
}
resource "aws_iam_role_policy" "zabbix_agent_catalog" {
  name   = "read-zabbix-agent-enrollment-identities"
  role   = "cosmotech-zabbix-dev-host"
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = [for key in keys(local.zabbix_agent_hosts) : aws_secretsmanager_secret.tool_credentials["zabbix-agent-${key}"].arn] }] })
}
resource "aws_vpc_security_group_ingress_rule" "zabbix_active_agent" {
  for_each                     = local.zabbix_agent_hosts
  security_group_id            = aws_security_group.zabbix.id
  referenced_security_group_id = each.value.sg
  ip_protocol                  = "tcp"
  from_port                    = 10051
  to_port                      = 10051
  description                  = "TLS PSK active agent from ${each.value.hostname} only"
}
resource "aws_vpc_security_group_egress_rule" "zabbix_agent_send" {
  for_each                     = { for key, value in local.zabbix_agent_hosts : key => value if key != "server" }
  security_group_id            = each.value.sg
  referenced_security_group_id = aws_security_group.zabbix.id
  ip_protocol                  = "tcp"
  from_port                    = 10051
  to_port                      = 10051
  description                  = "Active Zabbix agent to private server"
}
resource "aws_vpc_security_group_ingress_rule" "zabbix_inventory_read" {
  security_group_id            = module.itsm_host.security_group_id
  referenced_security_group_id = aws_security_group.zabbix.id
  ip_protocol                  = "tcp"
  from_port                    = 8088
  to_port                      = 8088
  description                  = "Zabbix reads existing private inventory and service metrics"
}
resource "aws_vpc_security_group_egress_rule" "zabbix_inventory_read" {
  security_group_id            = aws_security_group.zabbix.id
  referenced_security_group_id = module.itsm_host.security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 8088
  to_port                      = 8088
  description                  = "Zabbix to existing private inventory metrics"
}
resource "aws_ssm_document" "zabbix_agent_install" {
  name          = "CosmoTech-Install-Zabbix-Agent2"
  document_type = "Command"
  content       = jsonencode({ schemaVersion = "2.2", description = "Install pinned active-only Agent 2 with own-host Secrets Manager TLS PSK, no public agent listener", parameters = { Hostname = { type = "String", allowedValues = [for host in values(local.zabbix_agent_hosts) : host.hostname] } }, mainSteps = [{ action = "aws:runShellScript", name = "installAgent", inputs = { timeoutSeconds = "900", runCommand = ["#!/bin/bash", "set -Eeuo pipefail", "umask 077", "install -d -m 0700 /opt/cosmotech/zabbix-agent-source", "printf '%s' '${base64encode(file("${path.module}/../../../ops/zabbix-monitoring/install_agent.py"))}' | base64 --decode > /opt/cosmotech/zabbix-agent-source/install_agent.py", "python3 /opt/cosmotech/zabbix-agent-source/install_agent.py --hostname '{{ Hostname }}'"] } }] })
  tags          = local.common_tags
}
resource "aws_ssm_association" "zabbix_agent" {
  for_each         = local.zabbix_agent_hosts
  name             = aws_ssm_document.zabbix_agent_install.name
  association_name = "cosmotech-zabbix-agent-${each.key}"
  parameters       = { Hostname = each.value.hostname }
  targets {
    key    = "InstanceIds"
    values = [each.value.instance_id]
  }
  schedule_expression = "rate(1 day)"
  depends_on          = [aws_iam_role_policy.zabbix_agent_identity, aws_vpc_security_group_ingress_rule.zabbix_active_agent]
}
resource "aws_ssm_document" "zabbix_monitoring" {
  name          = "CosmoTech-Configure-Zabbix-Monitoring"
  document_type = "Command"
  content       = jsonencode({ schemaVersion = "2.2", description = "Enroll TLS agents and configure read-only AWS, EKS, application availability and APM data checks", mainSteps = [{ action = "aws:runShellScript", name = "configureMonitoring", inputs = { timeoutSeconds = "600", runCommand = ["#!/bin/bash", "set -Eeuo pipefail", "umask 077", "install -d -m 0700 /opt/cosmotech/zabbix-monitoring-source", "printf '%s' '${base64encode(file("${path.module}/../../../ops/zabbix-monitoring/configure_monitoring.py"))}' | base64 --decode > /opt/cosmotech/zabbix-monitoring-source/configure_monitoring.py", "python3 /opt/cosmotech/zabbix-monitoring-source/configure_monitoring.py"] } }] })
  tags          = local.common_tags
}
