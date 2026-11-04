# Worker publisher has no EKS, SQS or DynamoDB permissions.
resource "aws_iam_role" "orders_worker_ci" {
  name = "cosmotech-orders-worker-github-ecr"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = "arn:aws:iam::414053644145:oidc-provider/token.actions.githubusercontent.com" }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = { StringEquals = {
        "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        "token.actions.githubusercontent.com:sub" = "repo:rlcosmo@124368446/cosmotech-orders-worker@1446246146:ref:refs/heads/main"
      } }
    }]
  })
  tags = merge(local.common_tags, { Repository = "rlcosmo/cosmotech-orders-worker" })
}
resource "aws_iam_role_policy" "orders_worker_ci" {
  role = aws_iam_role.orders_worker_ci.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
      { Effect = "Allow", Action = [
        "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
        "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage",
        "ecr:GetDownloadUrlForLayer", "ecr:DescribeImages"
      ], Resource = "arn:aws:ecr:us-east-1:414053644145:repository/aws-eks-istio-lab-dev-orders-worker" }
    ]
  })
}
