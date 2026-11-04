resource "aws_iam_role" "orders_api_ci" {
  name = "cosmotech-orders-api-github-ecr"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = "arn:aws:iam::414053644145:oidc-provider/token.actions.githubusercontent.com" }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = { StringEquals = {
        "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        "token.actions.githubusercontent.com:sub" = "repo:rlcosmo@124368446/cosmotech-orders-api@1445869622:ref:refs/heads/main"
      } }
    }]
  })
  tags = merge(local.common_tags, { Repository = "rlcosmo/cosmotech-orders-api" })
}
resource "aws_iam_role_policy" "orders_api_ci" {
  role = aws_iam_role.orders_api_ci.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
      { Effect = "Allow", Action = [
        "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
        "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage",
        "ecr:GetDownloadUrlForLayer", "ecr:DescribeImages"
      ], Resource = "arn:aws:ecr:us-east-1:414053644145:repository/aws-eks-istio-lab-dev-orders-api" }
    ]
  })
}
