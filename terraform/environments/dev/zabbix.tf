resource "aws_iam_role" "zabbix_host" {
  name = "cosmotech-zabbix-dev-host"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
  tags = merge(local.common_tags, { System = "zabbix-apm" })
}
resource "aws_iam_role_policy_attachment" "zabbix_ssm" {
  role       = aws_iam_role.zabbix_host.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
resource "aws_iam_role_policy" "zabbix_credentials" {
  role = aws_iam_role.zabbix_host.name
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = ["ssm:PutParameter"], Resource = "arn:aws:ssm:us-east-1:414053644145:parameter/cosmotech/zabbix/credentials" }]
  })
}
resource "aws_iam_instance_profile" "zabbix" {
  name = "cosmotech-zabbix-dev"
  role = aws_iam_role.zabbix_host.name
  tags = local.common_tags
}
resource "aws_security_group" "zabbix" {
  name        = "cosmotech-zabbix-dev"
  description = "Private Zabbix APM. ALB web and EKS-only OTLP; SSM administration."
  vpc_id      = data.aws_vpc.eks_lab.id
  tags        = merge(local.common_tags, { System = "zabbix-apm" })
}
resource "aws_vpc_security_group_ingress_rule" "zabbix_alb" {
  security_group_id            = aws_security_group.zabbix.id
  referenced_security_group_id = data.aws_security_group.observability_alb.id
  ip_protocol                  = "tcp"
  from_port                    = 8088
  to_port                      = 8088
  description                  = "Existing TLS ALB only."
}
resource "aws_vpc_security_group_ingress_rule" "zabbix_otel" {
  security_group_id            = aws_security_group.zabbix.id
  referenced_security_group_id = data.aws_eks_cluster.lab.vpc_config[0].cluster_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 4317
  to_port                      = 4317
  description                  = "OTLP from the existing EKS cluster; no public ingestion."
}
resource "aws_vpc_security_group_egress_rule" "zabbix_https" {
  security_group_id = aws_security_group.zabbix.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}
resource "aws_vpc_security_group_egress_rule" "zabbix_dns" {
  for_each          = toset(["tcp", "udp"])
  security_group_id = aws_security_group.zabbix.id
  cidr_ipv4         = data.aws_vpc.eks_lab.cidr_block
  ip_protocol       = each.key
  from_port         = 53
  to_port           = 53
}
resource "aws_instance" "zabbix" {
  ami                         = data.aws_ssm_parameter.ubuntu_2404_amd64.value
  instance_type               = "m7i.xlarge"
  subnet_id                   = data.aws_subnet.private_a.id
  associate_public_ip_address = false
  vpc_security_group_ids      = [aws_security_group.zabbix.id]
  iam_instance_profile        = aws_iam_instance_profile.zabbix.name
  monitoring                  = true
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }
  root_block_device {
    volume_type           = "gp3"
    volume_size           = 120
    encrypted             = true
    delete_on_termination = false
  }
  user_data = "#!/bin/bash\nset -eu\nsystemctl enable --now snap.amazon-ssm-agent.amazon-ssm-agent.service\n"
  tags      = merge(local.common_tags, { Name = "cosmotech-zabbix-dev", System = "zabbix-apm" })
  lifecycle { prevent_destroy = true }
}
resource "aws_lb_target_group" "zabbix" {
  name                 = "cosmotech-zabbix-dev"
  port                 = 8088
  protocol             = "HTTP"
  target_type          = "instance"
  vpc_id               = data.aws_vpc.eks_lab.id
  deregistration_delay = 30
  health_check {
    path    = "/index.php"
    matcher = "200-399"
  }
  tags = local.common_tags
}
resource "aws_lb_target_group_attachment" "zabbix" {
  target_group_arn = aws_lb_target_group.zabbix.arn
  target_id        = aws_instance.zabbix.id
  port             = 8088
}
resource "aws_backup_selection" "zabbix" {
  name         = "cosmotech-zabbix-apm"
  plan_id      = "26e6a57c-13e7-485d-94d7-9ccd19c0af58"
  iam_role_arn = "arn:aws:iam::414053644145:role/cosmotech-itsm-dev-backup-role"
  resources    = [aws_instance.zabbix.arn]
}
resource "aws_cloudwatch_metric_alarm" "zabbix_status" {
  alarm_name          = "cosmotech-zabbix-instance-status"
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed"
  dimensions          = { InstanceId = aws_instance.zabbix.id }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "missing"
  alarm_actions       = ["arn:aws:sns:us-east-1:414053644145:cosmotech-lab-alerts"]
  tags                = local.common_tags
}
resource "aws_ssm_document" "zabbix_install" {
  name          = "CosmoTech-Install-Zabbix-APM"
  document_type = "Command"
  content = jsonencode({
    schemaVersion = "2.2"
    description   = "Install official Zabbix 8.0 LTS on a new host; existing data requires validated V2-010 upgrade"
    mainSteps = [{ action = "aws:runShellScript", name = "installZabbixAPM", inputs = {
      timeoutSeconds = "1800"
      runCommand = concat(["#!/bin/bash", "set -Eeuo pipefail", "umask 077", "install -d -m 0700 /opt/cosmotech/zabbix-source/v2010"],
        [for name in concat(tolist(fileset("${path.module}/../../../ops/zabbix", "*")), ["v2010/Dockerfile", "v2010/Dockerfile.web", "v2010/notifications.py", "v2010/index.html", "v2010/nginx-location.conf", "v2010/bootstrap.py", "v2010/fault_lab.py", "v2010/install_runtime.py"]) : "printf '%s' '${base64gzip(file("${path.module}/../../../ops/zabbix/${name}"))}' | base64 --decode | gzip --decompress > '/opt/cosmotech/zabbix-source/${name}'"],
      ["bash /opt/cosmotech/zabbix-source/install.sh"])
    } }]
  })
  tags = local.common_tags
}
output "zabbix_instance_id" { value = aws_instance.zabbix.id }
output "zabbix_private_ip" { value = aws_instance.zabbix.private_ip }
output "zabbix_target_group_arn" { value = aws_lb_target_group.zabbix.arn }
output "zabbix_url" { value = "https://zabbix.cosmotechsolutions.com.br" }
