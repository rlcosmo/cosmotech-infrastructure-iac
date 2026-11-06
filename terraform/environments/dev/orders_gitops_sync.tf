# Application CI publishes images; this identity only invokes fixed management-plane reconciliation.
resource "aws_iam_role" "orders_gitops_sync" {
  name = "cosmotech-gitops-github-sync"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = "arn:aws:iam::414053644145:oidc-provider/token.actions.githubusercontent.com" }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = { StringEquals = {
        "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        "token.actions.githubusercontent.com:sub" = "repo:rlcosmo@124368446/cosmotech-gitops@1445869422:ref:refs/heads/main"
      } }
    }]
  })
  tags = merge(local.common_tags, { Repository = "rlcosmo/cosmotech-gitops" })
}
resource "aws_iam_role_policy" "orders_gitops_sync" {
  role = aws_iam_role.orders_gitops_sync.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["ssm:SendCommand"], Resource = [
        aws_ssm_document.orders_gitops_sync.arn,
        "arn:aws:ec2:us-east-1:414053644145:instance/i-0b33fdd51332e04e5"
      ] },
      { Effect = "Allow", Action = ["ssm:GetCommandInvocation"], Resource = "*" }
    ]
  })
}
resource "aws_ssm_document" "orders_gitops_sync" {
  name          = "CosmoTech-Sync-Orders-GitOps"
  document_type = "Command"
  content = jsonencode({
    schemaVersion = "2.2"
    description   = "Reconcile the exact GitOps main commit on the existing dedicated Argo management host"
    parameters = {
      GitRevision = { type = "String", allowedPattern = "^[a-f0-9]{40}$" }
    }
    mainSteps = [{
      action = "aws:runShellScript"
      name   = "syncOrdersGitOps"
      inputs = {
        timeoutSeconds = "900"
        runCommand = [
          "#!/bin/bash", "set -Eeuo pipefail", "umask 077",
          "install -d -m 0700 /opt/cosmotech/promotion",
          "printf '%s' '${base64encode(file("${path.module}/../../../ops/argocd/validate_app_manifests.py"))}' | base64 --decode > /opt/cosmotech/promotion/validate_app_manifests.py",
          "printf '%s' '${base64encode(file("${path.module}/../../../ops/argocd/sync-orders-gitops.sh"))}' | base64 --decode > /opt/cosmotech/argocd/sync-orders-gitops.sh",
          "bash /opt/cosmotech/argocd/sync-orders-gitops.sh '{{ GitRevision }}'"
        ]
      }
    }]
  })
  tags = local.common_tags
}
