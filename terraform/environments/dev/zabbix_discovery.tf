# Native AWS templates discover resources using short-lived role credentials only.
resource "aws_iam_role" "zabbix_aws_monitoring" {
  name = "cosmotech-zabbix-aws-monitoring"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { AWS = aws_iam_role.zabbix_host.arn }, Action = "sts:AssumeRole"
  }] })
  tags = local.common_tags
}
resource "aws_iam_role_policy" "zabbix_aws_monitoring" {
  name = "read-discovery-and-cloudwatch"
  role = aws_iam_role.zabbix_aws_monitoring.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = [
      "cloudwatch:GetMetricData", "cloudwatch:DescribeAlarms", "ec2:DescribeInstances", "ec2:DescribeRegions",
      "ec2:DescribeVolumes", "ec2:DescribeSecurityGroups", "ecs:ListClusters", "ecs:DescribeClusters",
      "ecs:ListServices", "ecs:ListTasks", "elasticloadbalancing:DescribeLoadBalancers",
      "elasticloadbalancing:DescribeTargetGroups", "lambda:ListFunctions", "rds:DescribeDBInstances", "rds:DescribeEvents",
      "backup:ListBackupVaults", "backup:ListBackupJobs", "backup:ListCopyJobs", "backup:ListRestoreJobs"
    ], Resource = "*", Condition = { StringEquals = { "aws:RequestedRegion" = "us-east-1" } } },
    { Effect = "Allow", Action = ["backup:DescribeBackupVault"], Resource = "arn:aws:backup:us-east-1:414053644145:backup-vault:*" },
    { Effect = "Allow", Action = ["s3:ListAllMyBuckets"], Resource = "*" },
    { Effect = "Allow", Action = ["s3:GetBucketLocation", "s3:GetMetricsConfiguration"], Resource = "arn:aws:s3:::*" }
  ] })
}
resource "aws_iam_role_policy" "zabbix_assume_monitoring" {
  name   = "assume-native-aws-monitoring"
  role   = aws_iam_role.zabbix_host.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Resource = aws_iam_role.zabbix_aws_monitoring.arn }] })
}
resource "aws_ssm_document" "zabbix_discovery" {
  name          = "CosmoTech-Configure-Zabbix-Discovery"
  document_type = "Command"
  content = jsonencode({ schemaVersion = "2.2", description = "Reconcile native role-authenticated AWS discovery, standard monitoring and dashboards", mainSteps = [{ action = "aws:runShellScript", name = "configureDiscovery", inputs = { timeoutSeconds = "600", runCommand = [
    "#!/bin/bash", "set -Eeuo pipefail", "umask 077", "install -d -m 0700 /opt/cosmotech/zabbix-monitoring-source",
    "printf '%s' '${base64encode(file("${path.module}/../../../ops/zabbix-monitoring/configure_discovery.py"))}' | base64 --decode > /opt/cosmotech/zabbix-monitoring-source/configure_discovery.py",
    "python3 /opt/cosmotech/zabbix-monitoring-source/configure_discovery.py"
  ] } }] })
  tags = local.common_tags
}
resource "aws_ssm_association" "zabbix_discovery" {
  name             = aws_ssm_document.zabbix_discovery.name
  association_name = "cosmotech-zabbix-aws-discovery-and-dashboards"
  targets {
    key    = "InstanceIds"
    values = [aws_instance.zabbix.id]
  }
  schedule_expression = "rate(30 minutes)"
  depends_on          = [aws_iam_role_policy.zabbix_aws_monitoring, aws_iam_role_policy.zabbix_assume_monitoring]
}
