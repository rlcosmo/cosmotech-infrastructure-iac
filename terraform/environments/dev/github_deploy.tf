resource "aws_iam_role" "github_deploy" {
  name                 = "cosmotech-serverless-lab-github-dev-deploy"
  max_session_duration = 3600
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Federated = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/token.actions.githubusercontent.com"
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "repository_id:1439600540:repository_owner_id:124368446:ref:refs/heads/main:environment:infra-sre"
        }
      }
    }]
  })
  tags = merge(local.common_tags, { Repository = "rlcosmo/aws-serverless-lab", Environment = "dev" })
}

resource "aws_iam_role_policy" "github_deploy" {
  name = "dev-orders-images-and-cluster"
  role = aws_iam_role.github_deploy.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage",
          "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:DescribeImages"
        ]
        Resource = [
          "arn:aws:ecr:${var.aws_region}:${data.aws_caller_identity.current.account_id}:repository/aws-eks-istio-lab-dev-orders-api",
          "arn:aws:ecr:${var.aws_region}:${data.aws_caller_identity.current.account_id}:repository/aws-eks-istio-lab-dev-orders-worker"
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["eks:DescribeCluster"]
        Resource = data.aws_eks_cluster.lab.arn
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem"]
        Resource = "arn:aws:dynamodb:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/aws-eks-istio-lab-dev-orders-data"
        Condition = {
          "ForAllValues:StringEquals" = { "dynamodb:LeadingKeys" = ["INVENTORY#LAB"] }
          Null                        = { "dynamodb:LeadingKeys" = "false" }
        }
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:StartSession"]
        Resource = "arn:aws:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:instance/${module.itsm_host.instance_id}"
        Condition = {
          Bool = { "ssm:SessionDocumentAccessCheck" = "true" }
        }
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:StartSession"]
        Resource = aws_ssm_document.github_eks_tunnel.arn
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:TerminateSession", "ssmmessages:OpenDataChannel"]
        Resource = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:session/github-dev-*"
      }
    ]
  })
}

# A fixed destination prevents this deployment identity from opening an
# interactive shell or forwarding arbitrary private services.
resource "aws_ssm_document" "github_eks_tunnel" {
  name          = "CosmoTech-GitHub-EKS-Tunnel"
  document_type = "Session"
  content = jsonencode({
    schemaVersion = "1.0"
    description   = "Forward only the lab EKS HTTPS API through the private ITSM host"
    sessionType   = "Port"
    properties = {
      host            = trimprefix(data.aws_eks_cluster.lab.endpoint, "https://")
      portNumber      = "443"
      localPortNumber = "8443"
    }
  })
  tags = local.common_tags
}

# The existing worker role is maintained by the EKS project. This separate,
# additive policy supports the new checkpoints on its existing table.
resource "aws_iam_role_policy" "eks_orders_checkpoints" {
  name = "cosmotech-lab-order-checkpoints"
  role = "aws-eks-istio-lab-dev-orders-worker-role"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["dynamodb:GetItem", "dynamodb:UpdateItem"]
      Resource = "arn:aws:dynamodb:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/aws-eks-istio-lab-dev-orders-data"
      }, {
      Effect   = "Allow"
      Action   = ["sqs:ChangeMessageVisibility"]
      Resource = "arn:aws:sqs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:aws-eks-istio-lab-dev-orders-eks"
    }]
  })
}

# Kubernetes RBAC, not cluster-admin, grants the image update boundary.
resource "aws_eks_access_entry" "github_deploy" {
  cluster_name      = data.aws_eks_cluster.lab.name
  principal_arn     = aws_iam_role.github_deploy.arn
  type              = "STANDARD"
  kubernetes_groups = ["cosmotech-dev-deployers"]
  tags              = local.common_tags
}

output "github_dev_deploy_role_arn" {
  value = aws_iam_role.github_deploy.arn
}
