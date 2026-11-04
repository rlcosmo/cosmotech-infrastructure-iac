# CI is read-only apart from the existing backend lock object. Its no-refresh
# plan checks source changes; deployment still requires a fresh refreshed plan.
resource "aws_iam_role" "terraform_plan_ci" {
  name = "cosmotech-terraform-github-plan"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = "arn:aws:iam::414053644145:oidc-provider/token.actions.githubusercontent.com" }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = { StringEquals = {
        "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        "token.actions.githubusercontent.com:sub" = "repo:rlcosmo@124368446/cosmotech-infrastructure-iac@1445869018:ref:refs/heads/main"
      } }
    }]
  })
  tags = merge(local.common_tags, { Repository = "rlcosmo/cosmotech-infrastructure-iac" })
}
resource "aws_iam_role_policy" "terraform_plan_ci" {
  role = aws_iam_role.terraform_plan_ci.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:ListBucket", "s3:GetBucketLocation"],
      Resource = "arn:aws:s3:::aws-serverless-lab-414053644145-tfstate-use1" },
      { Effect = "Allow", Action = ["s3:GetObject"],
      Resource = "arn:aws:s3:::aws-serverless-lab-414053644145-tfstate-use1/aws-serverless-lab/dev/terraform.tfstate" },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
      Resource = "arn:aws:s3:::aws-serverless-lab-414053644145-tfstate-use1/aws-serverless-lab/dev/terraform.tfstate.tflock" },
      { Effect = "Allow", Action = [
        "ec2:DescribeVpcs", "ec2:DescribeSubnets", "ec2:DescribeSecurityGroups",
        "ec2:DescribeSecurityGroupRules", "ec2:DescribeVolumes", "ec2:DescribeTags",
        "elasticloadbalancing:DescribeLoadBalancers", "elasticloadbalancing:DescribeListeners",
        "elasticloadbalancing:DescribeLoadBalancerAttributes", "elasticloadbalancing:DescribeTags"
      ], Resource = "*" },
      { Effect = "Allow", Action = ["ec2:DescribeVpcAttribute"],
      Resource = data.aws_vpc.eks_lab.arn },
      { Effect = "Allow", Action = ["iam:GetPolicy", "iam:GetPolicyVersion"],
      Resource = "arn:aws:iam::aws:policy/AmazonEBSCSIDriverPolicyV2" },
      { Effect = "Allow", Action = ["dynamodb:DescribeTable"],
      Resource = "arn:aws:dynamodb:us-east-1:414053644145:table/aws-serverless-lab-dev-orders" },
      { Effect = "Allow", Action = ["eks:DescribeCluster"], Resource = data.aws_eks_cluster.lab.arn },
      { Effect = "Allow", Action = ["ssm:GetParameter"],
      Resource = "arn:aws:ssm:us-east-1:*:parameter/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id" }
    ]
  })
}
