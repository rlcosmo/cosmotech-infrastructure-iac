terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "= 6.62.0" }
  }
}
locals {
  azs = ["${var.region}a", "${var.region}b", "${var.region}c"]
}
resource "aws_vpc" "standby" {
  cidr_block           = "10.84.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = var.name }
}
resource "aws_internet_gateway" "standby" { vpc_id = aws_vpc.standby.id }
# Cost-conscious lab: no NAT. Workers have public egress IPs; no inbound SSH.
resource "aws_subnet" "standby" {
  count                   = 3
  vpc_id                  = aws_vpc.standby.id
  cidr_block              = cidrsubnet(aws_vpc.standby.cidr_block, 8, count.index)
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = true
  tags                    = { "kubernetes.io/role/elb" = "1" }
}
resource "aws_route_table" "standby" { vpc_id = aws_vpc.standby.id }
resource "aws_route" "egress" {
  route_table_id         = aws_route_table.standby.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.standby.id
}
resource "aws_route_table_association" "standby" {
  count          = 3
  subnet_id      = aws_subnet.standby[count.index].id
  route_table_id = aws_route_table.standby.id
}
resource "aws_iam_role" "cluster" {
  permissions_boundary = var.permissions_boundary_arn
  name                 = "${var.name}-cluster"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "eks.amazonaws.com" }, Action = "sts:AssumeRole"
  }] })
}
resource "aws_iam_role_policy_attachment" "cluster" {
  role       = aws_iam_role.cluster.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}
resource "aws_cloudwatch_log_group" "cluster" {
  name              = "/aws/eks/${var.name}/cluster"
  retention_in_days = 7
}
resource "aws_eks_cluster" "standby" {
  name                      = var.name
  version                   = var.cluster_version
  role_arn                  = aws_iam_role.cluster.arn
  enabled_cluster_log_types = ["api", "audit", "authenticator"]
  deletion_protection       = true
  access_config {
    authentication_mode                         = "API"
    bootstrap_cluster_creator_admin_permissions = false
  }
  upgrade_policy { support_type = "STANDARD" }
  vpc_config {
    subnet_ids              = aws_subnet.standby[*].id
    endpoint_private_access = true
    endpoint_public_access  = true
    public_access_cidrs     = var.admin_cidrs
  }
  depends_on = [aws_iam_role_policy_attachment.cluster, aws_cloudwatch_log_group.cluster]
  lifecycle { prevent_destroy = true }
}
resource "aws_eks_access_entry" "operator" {
  cluster_name  = aws_eks_cluster.standby.name
  principal_arn = var.admin_role_arn
  type          = "STANDARD"
}
resource "aws_eks_access_policy_association" "operator" {
  cluster_name  = aws_eks_cluster.standby.name
  principal_arn = aws_eks_access_entry.operator.principal_arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"
  access_scope { type = "cluster" }
}
resource "aws_iam_role" "nodes" {
  permissions_boundary = var.permissions_boundary_arn
  name                 = "${var.name}-nodes"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole"
  }] })
}
resource "aws_iam_role_policy_attachment" "nodes" {
  for_each   = toset(["AmazonEKSWorkerNodePolicy", "AmazonEC2ContainerRegistryPullOnly"])
  role       = aws_iam_role.nodes.name
  policy_arn = "arn:aws:iam::aws:policy/${each.value}"
}
resource "aws_launch_template" "nodes" {
  name_prefix = "${var.name}-"
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }
  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size           = 20
      volume_type           = "gp3"
      encrypted             = true
      delete_on_termination = false
    }
  }
  tag_specifications {
    resource_type = "instance"
    tags          = { Name = var.name, Environment = "dr", DRTarget = "true" }
  }
  tag_specifications {
    resource_type = "volume"
    tags          = { Name = var.name, Environment = "dr", DRTarget = "true" }
  }
}
resource "aws_eks_node_group" "standby" {
  cluster_name    = aws_eks_cluster.standby.name
  node_group_name = "dr-ondemand"
  node_role_arn   = aws_iam_role.nodes.arn
  subnet_ids      = aws_subnet.standby[*].id
  instance_types  = ["t3.large"]
  capacity_type   = "ON_DEMAND"
  ami_type        = "AL2023_x86_64_STANDARD"
  launch_template {
    id      = aws_launch_template.nodes.id
    version = aws_launch_template.nodes.latest_version
  }
  scaling_config {
    min_size     = 0
    desired_size = var.desired_nodes
    max_size     = var.max_nodes
  }
  update_config {
    max_unavailable = 1
    update_strategy = "MINIMAL"
  }
  depends_on = [aws_iam_role_policy_attachment.nodes, aws_route.egress]
}
# Separate CNI identity; applications cannot inherit node IAM via IMDS.
resource "aws_iam_role" "pod" {
  permissions_boundary = var.permissions_boundary_arn
  for_each             = toset(["cni", "api", "worker", "ebs", "publisher", "metrics", "reconciler"])
  name                 = "${var.name}-${each.key}"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "pods.eks.amazonaws.com" },
    Action = ["sts:AssumeRole", "sts:TagSession"],
    Condition = { StringEquals = { "aws:SourceAccount" = var.account_id },
    ArnEquals = { "aws:SourceArn" = "arn:aws:eks:${var.region}:${var.account_id}:cluster/${var.name}" } }
  }] })
}
resource "aws_iam_role_policy_attachment" "cni" {
  role       = aws_iam_role.pod["cni"].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
}
resource "aws_iam_role_policy_attachment" "ebs" {
  role       = aws_iam_role.pod["ebs"].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEBSCSIDriverPolicyV2"
}
resource "aws_iam_role_policy" "orders" {
  for_each = toset(["api", "worker"])
  role     = aws_iam_role.pod[each.key].id
  policy = jsonencode({ Version = "2012-10-17", Statement = concat(
    each.key == "worker" ? [{ Effect = "Allow", Action = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility", "sqs:GetQueueAttributes"], Resource = var.queue_arn }] : [],
    [{ Effect = "Allow", Action = each.key == "api" ? ["dynamodb:GetItem", "dynamodb:DescribeTable"] : ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:ConditionCheckItem", "dynamodb:DescribeTable"], Resource = "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${var.restored_table_name}" }]
  ) })
}
resource "aws_eks_pod_identity_association" "pod" {
  for_each = {
    cni        = { namespace = "kube-system", service_account = "aws-node" }
    ebs        = { namespace = "kube-system", service_account = "ebs-csi-controller-sa" }
    api        = { namespace = "orders-api", service_account = "orders-api-sa" }
    worker     = { namespace = "orders-worker", service_account = "orders-worker-sa" }
    publisher  = { namespace = "orders-api", service_account = "dr-outbox-publisher" }
    metrics    = { namespace = "orders-api", service_account = "dr-outbox-metrics" }
    reconciler = { namespace = "orders-api", service_account = "dr-outbox-reconciler" }
  }
  cluster_name    = aws_eks_cluster.standby.name
  namespace       = each.value.namespace
  service_account = each.value.service_account
  role_arn        = aws_iam_role.pod[each.key].arn
}
# Versions are pinned to live Ohio observations; upgrades require a new review.
resource "aws_eks_addon" "agent" {
  cluster_name  = aws_eks_cluster.standby.name
  addon_name    = "eks-pod-identity-agent"
  addon_version = var.addon_versions["eks-pod-identity-agent"]
}
resource "aws_eks_addon" "network" {
  for_each             = toset(["vpc-cni", "kube-proxy", "coredns", "aws-ebs-csi-driver"])
  cluster_name         = aws_eks_cluster.standby.name
  addon_name           = each.key
  addon_version        = var.addon_versions[each.key]
  configuration_values = each.key == "vpc-cni" ? jsonencode({ enableNetworkPolicy = "true" }) : null
  depends_on           = [aws_eks_addon.agent, aws_eks_pod_identity_association.pod, aws_iam_role_policy_attachment.cni, aws_iam_role_policy_attachment.ebs]
}
resource "aws_s3_bucket" "recovery" {
  bucket        = "${var.name}-${var.account_id}-${var.region}"
  force_destroy = false
  lifecycle { prevent_destroy = true }
}
resource "aws_s3_bucket_versioning" "recovery" {
  bucket = aws_s3_bucket.recovery.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "recovery" {
  bucket = aws_s3_bucket.recovery.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}
resource "aws_s3_bucket_public_access_block" "recovery" {
  bucket                  = aws_s3_bucket.recovery.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_policy" "recovery" {
  bucket = aws_s3_bucket.recovery.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Deny", Principal = "*", Action = "s3:*",
    Resource  = [aws_s3_bucket.recovery.arn, "${aws_s3_bucket.recovery.arn}/*"],
    Condition = { Bool = { "aws:SecureTransport" = "false" } }
  }] })
}
resource "aws_kms_key" "backup" {
  description             = "DR destination backup vault only"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  lifecycle { prevent_destroy = true }
}
resource "aws_backup_vault" "standby" {
  name        = var.name
  kms_key_arn = aws_kms_key.backup.arn
  lifecycle { prevent_destroy = true }
}
resource "aws_ecr_repository" "orders" {
  for_each             = toset(["aws-eks-istio-lab-dev-orders-api", "aws-eks-istio-lab-dev-orders-worker"])
  name                 = each.key
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration { encryption_type = "AES256" }
  lifecycle { prevent_destroy = true }
}
output "cluster_name" { value = aws_eks_cluster.standby.name }
output "backup_vault_arn" { value = aws_backup_vault.standby.arn }
output "recovery_bucket" { value = aws_s3_bucket.recovery.id }
output "endpoint" { value = aws_eks_cluster.standby.endpoint }
# Optional CLEAN recovery VM; never boot a primary AMI with active automation.
resource "aws_security_group" "recovery" {
  count       = var.enable_recovery_host ? 1 : 0
  name_prefix = "${var.name}-recovery-"
  description = "No ingress; clean ITSM recovery via SSM only"
  vpc_id      = aws_vpc.standby.id
  tags        = { Name = "${var.name}-itsm-recovery" }
  egress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
resource "aws_iam_role" "recovery" {
  permissions_boundary = var.permissions_boundary_arn
  count                = var.enable_recovery_host ? 1 : 0
  name                 = "${var.name}-itsm-recovery"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole"
  }] })
}
resource "aws_iam_role_policy_attachment" "recovery" {
  count      = var.enable_recovery_host ? 1 : 0
  role       = aws_iam_role.recovery[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
resource "aws_iam_instance_profile" "recovery" {
  count = var.enable_recovery_host ? 1 : 0
  name  = "${var.name}-itsm-recovery"
  role  = aws_iam_role.recovery[0].name
}
resource "aws_launch_template" "recovery" {
  count         = var.enable_recovery_host ? 1 : 0
  name          = "${var.name}-itsm-recovery"
  image_id      = var.recovery_host_ami
  instance_type = "t3.large"
  tags          = { Name = "${var.name}-itsm-recovery" }
  iam_instance_profile { name = aws_iam_instance_profile.recovery[0].name }
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }
  credit_specification { cpu_credits = "standard" }
  block_device_mappings {
    device_name = "/dev/sda1"
    ebs {
      volume_size           = 40
      volume_type           = "gp3"
      encrypted             = true
      kms_key_id            = aws_kms_key.backup.arn
      delete_on_termination = false
    }
  }
  tag_specifications {
    resource_type = "instance"
    tags          = { Name = "${var.name}-itsm-recovery", Project = "CosmoTech", Environment = "dr", DRTarget = "true" }
  }
  tag_specifications {
    resource_type = "volume"
    tags          = { Name = "${var.name}-itsm-recovery-root", Project = "CosmoTech", Environment = "dr", DRTarget = "true" }
  }
}
resource "aws_instance" "recovery" {
  count                       = var.enable_recovery_host ? 1 : 0
  subnet_id                   = aws_subnet.standby[1].id
  vpc_security_group_ids      = [aws_security_group.recovery[0].id]
  associate_public_ip_address = true
  launch_template {
    id      = aws_launch_template.recovery[0].id
    version = aws_launch_template.recovery[0].latest_version
  }
  tags = { Name = "${var.name}-itsm-recovery", DRTarget = "true" }
  lifecycle { prevent_destroy = true }
  depends_on = [aws_iam_role_policy_attachment.recovery, aws_route.egress]
}
resource "aws_volume_attachment" "recovery_data" {
  for_each = var.enable_recovery_host ? {
    for name, volume in var.restored_volumes : name => volume if contains(["itsm_root", "zabbix_root"], name)
  } : {}
  device_name = each.key == "itsm_root" ? "/dev/sdf" : "/dev/sdg"
  volume_id   = each.value.volume_id
  instance_id = aws_instance.recovery[0].id
  lifecycle {
    prevent_destroy = true
    precondition {
      condition     = each.value.availability_zone == "us-east-2b"
      error_message = "Recovery data disks must have their real AZ equal to the clean host AZ."
    }
  }
}
output "recovery_instance_id" { value = try(aws_instance.recovery[0].id, null) }
# Even if a CSI pod is compromised, its AWS calls cannot target the primary region.
resource "aws_iam_role_policy" "regional_boundary" {
  for_each = aws_iam_role.pod
  role     = each.value.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Deny", Action = "*", Resource = "*",
    Condition = { StringNotEquals = { "aws:RequestedRegion" = var.region } }
  }] })
}
resource "aws_iam_role_policy" "recovery_read" {
  count = var.enable_recovery_host ? 1 : 0
  role  = aws_iam_role.recovery[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["s3:GetObject", "s3:GetObjectVersion"], Resource = "${aws_s3_bucket.recovery.arn}/itsm/*" },
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.recovery.arn, Condition = { StringLike = { "s3:prefix" = "itsm/*" } } },
    { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = "arn:aws:secretsmanager:${var.region}:${var.account_id}:secret:cosmotech/dr/itsm/*" }
  ] })
}
resource "aws_dynamodb_table" "outbox" {
  name                        = "cosmotech-dr-outbox"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "order_id"
  deletion_protection_enabled = true
  attribute {
    name = "order_id"
    type = "S"
  }
  attribute {
    name = "dispatch_bucket"
    type = "S"
  }
  attribute {
    name = "due_at"
    type = "N"
  }
  global_secondary_index {
    name = "dispatch-due"
    key_schema {
      attribute_name = "dispatch_bucket"
      key_type       = "HASH"
    }
    key_schema {
      attribute_name = "due_at"
      key_type       = "RANGE"
    }
    projection_type = "KEYS_ONLY"
  }
  point_in_time_recovery { enabled = true }
  server_side_encryption { enabled = true }
  lifecycle { prevent_destroy = true }
  # Authoritative acceptance journal: no TTL or destructive lifecycle.
}
resource "aws_iam_role_policy" "outbox" {
  for_each = {
    api        = ["dynamodb:PutItem", "dynamodb:GetItem"]
    publisher  = ["dynamodb:UpdateItem", "dynamodb:Query"]
    metrics    = ["dynamodb:Scan"]
    reconciler = ["dynamodb:Scan", "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
  }
  role = aws_iam_role.pod[each.key].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = each.value, Resource = [aws_dynamodb_table.outbox.arn, "${aws_dynamodb_table.outbox.arn}/index/dispatch-due"] }
  ] })
}
resource "aws_iam_role_policy" "publisher_queue" {
  for_each = toset(["publisher", "metrics"])
  role     = aws_iam_role.pod[each.key].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = each.key == "publisher" ? ["sqs:SendMessage"] : ["sqs:GetQueueAttributes"], Resource = var.queue_arn }
  ] })
}
resource "aws_iam_role" "outbox_backup" {
  permissions_boundary = var.permissions_boundary_arn
  name                 = "${var.name}-outbox-backup"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "backup.amazonaws.com" }, Action = "sts:AssumeRole",
    Condition = { StringEquals = { "aws:SourceAccount" = var.account_id },
    ArnLike = { "aws:SourceArn" = "arn:aws:backup:${var.region}:${var.account_id}:*" } }
  }] })
}
resource "aws_iam_role_policy_attachment" "outbox_backup" {
  role       = aws_iam_role.outbox_backup.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForBackup"
}
resource "aws_iam_role_policy" "outbox_backup_scope" {
  role = aws_iam_role.outbox_backup.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Deny", Action = "*", Resource = "*", Condition = { StringNotEquals = { "aws:RequestedRegion" = var.region } } },
    { Effect = "Deny", Action = ["dynamodb:CreateBackup", "dynamodb:Scan", "dynamodb:DescribeTable"], NotResource = aws_dynamodb_table.outbox.arn }
  ] })
}
resource "aws_backup_plan" "outbox" {
  name = "${var.name}-outbox-hourly"
  rule {
    rule_name         = "journal-hourly"
    target_vault_name = aws_backup_vault.standby.name
    schedule          = "cron(15 * * * ? *)"
    start_window      = 60
    completion_window = 180
    lifecycle { delete_after = 7 }
  }
}
resource "aws_backup_selection" "outbox" {
  name         = "explicit-outbox-only"
  plan_id      = aws_backup_plan.outbox.id
  iam_role_arn = aws_iam_role.outbox_backup.arn
  resources    = [aws_dynamodb_table.outbox.arn]
  depends_on   = [aws_iam_role_policy_attachment.outbox_backup, aws_iam_role_policy.outbox_backup_scope]
}
output "outbox_table" { value = aws_dynamodb_table.outbox.name }
resource "aws_ecr_repository" "outbox" {
  name                 = "cosmotech-dr-orders-outbox"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration { encryption_type = "AES256" }
  lifecycle { prevent_destroy = true }
}
resource "aws_iam_role_policy" "reconciliation_read" {
  role = aws_iam_role.pod["reconciler"].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["dynamodb:Scan", "dynamodb:GetItem"], Resource = ["arn:aws:dynamodb:${var.region}:${var.account_id}:table/cosmotech-dr-outbox-restored*", "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${var.restored_table_name}"] }
  ] })
}

output "outbox_protection" {
  value = {
    deletion_protection = aws_dynamodb_table.outbox.deletion_protection_enabled
    pitr_enabled        = aws_dynamodb_table.outbox.point_in_time_recovery[0].enabled
    index_projection    = one(aws_dynamodb_table.outbox.global_secondary_index).projection_type
  }
}

# Dedicated Backup restore identity: destination-only data creation, no source
# permissions or destructive cleanup. All restored resources are retained.
resource "aws_iam_role" "restore" {
  name                 = "${var.name}-restore"
  permissions_boundary = var.permissions_boundary_arn
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "backup.amazonaws.com" }, Action = "sts:AssumeRole",
    Condition = { StringEquals = { "aws:SourceAccount" = var.account_id },
    ArnLike = { "aws:SourceArn" = "arn:aws:backup:${var.region}:${var.account_id}:*" } }
  }] })
}
resource "aws_iam_role_policy" "restore" {
  role = aws_iam_role.restore.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect   = "Allow", Action = ["dynamodb:RestoreTableFromAwsBackup", "dynamodb:RestoreTableFromBackup", "dynamodb:DescribeTable", "dynamodb:TagResource"],
      Resource = ["arn:aws:dynamodb:${var.region}:${var.account_id}:table/cosmotech-dr-*-restored*"],
    Condition = { StringEquals = { "aws:RequestedRegion" = var.region } } },
    { Effect = "Allow", Action = ["ec2:DescribeVolumes", "ec2:DescribeSnapshots", "ec2:DescribeAvailabilityZones"], Resource = "*",
    Condition = { StringEquals = { "aws:RequestedRegion" = var.region } } },
    { Effect   = "Allow", Action = ["ec2:CreateVolume", "ec2:CreateTags"],
      Resource = ["arn:aws:ec2:${var.region}:${var.account_id}:volume/*", "arn:aws:ec2:${var.region}::snapshot/*"],
    Condition = { StringEquals = { "aws:RequestedRegion" = var.region } } },
    { Effect   = "Allow", Action = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey", "kms:GenerateDataKeyWithoutPlaintext", "kms:ReEncryptFrom", "kms:ReEncryptTo", "kms:DescribeKey"],
      Resource = "arn:aws:kms:${var.region}:${var.account_id}:key/*",
    Condition = { StringEquals = { "aws:RequestedRegion" = var.region } } },
    { Effect = "Allow", Action = ["kms:CreateGrant"], Resource = "arn:aws:kms:${var.region}:${var.account_id}:key/*",
    Condition = { Bool = { "kms:GrantIsForAWSResource" = "true" }, StringEquals = { "aws:RequestedRegion" = var.region } } }
  ] })
}
output "restore_role_arn" { value = aws_iam_role.restore.arn }

# Fail closed if the separately restored table does not exist. Terraform never
# substitutes an empty table for a missing backup restore.
data "aws_dynamodb_table" "restored_orders" {
  count = var.manage_restored_orders_table ? 1 : 0
  name  = var.restored_table_name
}
resource "aws_dynamodb_table" "restored_orders" {
  count                       = var.manage_restored_orders_table ? 1 : 0
  name                        = data.aws_dynamodb_table.restored_orders[0].name
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "order_id"
  deletion_protection_enabled = true
  attribute {
    name = "order_id"
    type = "S"
  }
  point_in_time_recovery {
    enabled                 = true
    recovery_period_in_days = 7
  }
  lifecycle { prevent_destroy = true }
}

# The AWS-managed CSI policy does not grant use of this customer-managed key.
resource "aws_iam_role_policy" "ebs_recovery_kms" {
  role = aws_iam_role.pod["ebs"].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncryptFrom", "kms:ReEncryptTo", "kms:GenerateDataKey", "kms:GenerateDataKeyWithoutPlaintext", "kms:DescribeKey"],
    Resource = aws_kms_key.backup.arn },
    { Effect = "Allow", Action = ["kms:CreateGrant", "kms:ListGrants"], Resource = aws_kms_key.backup.arn,
    Condition = { Bool = { "kms:GrantIsForAWSResource" = "true" } } }
  ] })
}

# The data lookup fails if a restore is missing; no empty replacement volume.
data "aws_ebs_volume" "restored" {
  for_each = var.restored_volumes
  filter {
    name   = "volume-id"
    values = [each.value.volume_id]
  }
}
resource "aws_ebs_volume" "restored" {
  for_each          = var.restored_volumes
  availability_zone = each.value.availability_zone
  snapshot_id       = each.value.snapshot_id
  size              = each.value.size_gib
  type              = "gp3"
  encrypted         = true
  kms_key_id        = aws_kms_key.backup.arn
  iops              = 3000
  throughput        = 125
  tags = merge({
    Name              = "cosmotech-dr-restored-${replace(each.key, "_", "-")}-20261112"
    RecoveryOperation = "cosmotech-dr-20261112"
    DRComponent       = each.key
    }, each.value.csi_managed ? {
    "ebs.csi.aws.com/cluster"           = "true"
    "ebs.csi.aws.com/cluster-name"      = var.name
    "kubernetes.io/cluster/${var.name}" = "owned"
  } : {})
  lifecycle {
    prevent_destroy = true
    # The imported local-only default is absent from AWS. Provider 6.62.0
    # otherwise calls ModifyVolume for this flag alone. Keep imported metadata;
    # real recovery protection is prevent_destroy plus verified Backup points.
    ignore_changes = [final_snapshot]
    precondition {
      condition = data.aws_ebs_volume.restored[each.key].availability_zone == each.value.availability_zone && (
        data.aws_ebs_volume.restored[each.key].snapshot_id == each.value.snapshot_id &&
        data.aws_ebs_volume.restored[each.key].encrypted &&
        data.aws_ebs_volume.restored[each.key].kms_key_id == aws_kms_key.backup.arn
      )
      error_message = "Live restored volume does not match the verified snapshot, AZ or DR encryption key."
    }
  }
}


# Keep an AZ-specific activation path for retained EBS volumes. A mixed-AZ
# group cannot guarantee a node in the volume's zone when scaling from zero.
resource "aws_eks_node_group" "stateful" {
  count           = var.enable_stateful_pool ? 1 : 0
  cluster_name    = aws_eks_cluster.standby.name
  node_group_name = "dr-stateful-2b"
  node_role_arn   = aws_iam_role.nodes.arn
  subnet_ids      = [aws_subnet.standby[1].id]
  instance_types  = ["t3.large"]
  capacity_type   = "ON_DEMAND"
  ami_type        = "AL2023_x86_64_STANDARD"
  labels          = { "cosmotech.io/workload" = "stateful" }
  launch_template {
    id      = aws_launch_template.nodes.id
    version = aws_launch_template.nodes.latest_version
  }
  scaling_config {
    min_size     = 0
    desired_size = var.desired_stateful_nodes
    max_size     = 1
  }
  update_config {
    max_unavailable = 1
    update_strategy = "MINIMAL"
  }
  depends_on = [aws_iam_role_policy_attachment.nodes, aws_route.egress]
}

# Independent filesystem replay copy; the first restored Zabbix disk remains untouched.
resource "aws_volume_attachment" "recovery_working_data" {
  for_each = var.enable_recovery_host ? {
    for name, volume in var.restored_volumes : name => volume if name == "zabbix_working_root"
  } : {}
  device_name = "/dev/sdh"
  volume_id   = each.value.volume_id
  instance_id = aws_instance.recovery[0].id
  lifecycle {
    prevent_destroy = true
    precondition {
      condition     = each.value.availability_zone == "us-east-2b"
      error_message = "The independent working disk must share the clean recovery host's real AZ."
    }
  }
}
