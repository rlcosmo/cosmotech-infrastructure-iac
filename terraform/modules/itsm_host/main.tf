data "aws_iam_policy_document" "assume_ec2" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  name               = "${var.name_prefix}-instance-role"
  assume_role_policy = data.aws_iam_policy_document.assume_ec2.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "instance" {
  statement {
    sid       = "DescribeLabResources"
    actions   = ["ec2:Describe*", "eks:Describe*", "eks:List*", "sts:GetCallerIdentity"]
    resources = ["*"]
  }

  statement {
    sid = "ReadTerraformManagedResources"
    actions = [
      "apigateway:GET",
      "backup:Describe*",
      "backup:Get*",
      "backup:List*",
      "cloudwatch:Describe*",
      "cloudwatch:Get*",
      "cloudwatch:List*",
      "dynamodb:Describe*",
      "dynamodb:ListTagsOfResource",
      "iam:Get*",
      "iam:List*",
      "lambda:Get*",
      "lambda:List*",
      "logs:Describe*",
      "logs:Get*",
      "logs:List*",
      "sqs:Get*",
      "sqs:List*",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "PublishBootstrapPasswords"
    actions   = ["ssm:PutParameter"]
    resources = [var.bootstrap_parameter_arn_prefix]
  }

  statement {
    sid       = "ReadTerraformStateBucketLocation"
    actions   = ["s3:GetBucketLocation"]
    resources = [var.state_bucket_arn]
  }

  statement {
    sid       = "ListTerraformStateBucket"
    actions   = ["s3:ListBucket"]
    resources = [var.state_bucket_arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${var.state_key}*"]
    }
  }

  statement {
    sid       = "ReadTerraformState"
    actions   = ["s3:GetObject"]
    resources = [var.state_object_arn]
  }

  statement {
    sid       = "LockTerraformState"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = [var.state_lock_object_arn]
  }
}

resource "aws_iam_role_policy" "instance" {
  name   = "${var.name_prefix}-runtime-policy"
  role   = aws_iam_role.instance.id
  policy = data.aws_iam_policy_document.instance.json
}

resource "aws_iam_instance_profile" "this" {
  name = "${var.name_prefix}-instance-profile"
  role = aws_iam_role.instance.name
  tags = var.tags
}

resource "aws_security_group" "this" {
  name        = "${var.name_prefix}-sg"
  description = "Private GLPI and Kestra host; administration uses Systems Manager."
  vpc_id      = var.vpc_id
  ingress     = []
  egress      = []
  tags        = merge(var.tags, { Name = "${var.name_prefix}-sg" })

  lifecycle {
    create_before_destroy = true
    # Ingress and egress rules are managed as standalone rule resources.
    ignore_changes = [egress, ingress]
  }
}

resource "aws_vpc_security_group_egress_rule" "https" {
  security_group_id = aws_security_group.this.id
  description       = "HTTPS to package repositories, AWS APIs, and container registries via the existing NAT."
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "dns_udp" {
  security_group_id = aws_security_group.this.id
  description       = "VPC DNS resolution."
  cidr_ipv4         = var.vpc_cidr
  from_port         = 53
  to_port           = 53
  ip_protocol       = "udp"
}

resource "aws_vpc_security_group_egress_rule" "dns_tcp" {
  security_group_id = aws_security_group.this.id
  description       = "VPC DNS resolution over TCP."
  cidr_ipv4         = var.vpc_cidr
  from_port         = 53
  to_port           = 53
  ip_protocol       = "tcp"
}

resource "aws_eks_access_entry" "this" {
  cluster_name      = var.eks_cluster_name
  principal_arn     = aws_iam_role.instance.arn
  type              = "STANDARD"
  kubernetes_groups = concat(["cosmotech-tempo-backup"], var.additional_kubernetes_groups)
  tags              = var.tags
}

resource "aws_eks_access_policy_association" "view" {
  cluster_name  = var.eks_cluster_name
  principal_arn = aws_iam_role.instance.arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSViewPolicy"

  access_scope {
    type = "cluster"
  }

  depends_on = [aws_eks_access_entry.this]
}

resource "aws_instance" "this" {
  ami                         = var.ami_id
  instance_type               = var.instance_type
  subnet_id                   = var.subnet_id
  associate_public_ip_address = false
  vpc_security_group_ids      = [aws_security_group.this.id]
  iam_instance_profile        = aws_iam_instance_profile.this.name
  user_data                   = templatefile("${path.module}/user_data.sh.tftpl", { repository_url = var.repository_url })
  user_data_replace_on_change = false

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
    instance_metadata_tags      = "disabled"
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = 50
    encrypted             = true
    delete_on_termination = false
    tags                  = merge(var.tags, { Name = "${var.name_prefix}-root" })
  }

  monitoring = true
  tags       = merge(var.tags, { Name = var.name_prefix, Backup = "daily" })

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_iam_role" "backup" {
  name = "${var.name_prefix}-backup-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "backup.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "backup" {
  role       = aws_iam_role.backup.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForBackup"
}

resource "aws_backup_vault" "this" {
  name = "${var.name_prefix}-vault"
  tags = var.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_backup_plan" "this" {
  name = "${var.name_prefix}-daily"
  tags = var.tags

  rule {
    rule_name         = "daily-14-day-retention"
    target_vault_name = aws_backup_vault.this.name
    schedule          = "cron(0 5 * * ? *)"
    start_window      = 60
    completion_window = 180

    lifecycle {
      delete_after = 14
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_backup_selection" "this" {
  name         = "${var.name_prefix}-instance"
  plan_id      = aws_backup_plan.this.id
  iam_role_arn = aws_iam_role.backup.arn
  resources    = [aws_instance.this.arn]
}
