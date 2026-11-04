locals {
  cluster_pipeline_apps = { "orders-api" : { "document" : "CosmoTech-Record-Orders-API-Pipeline", "role" : "cosmotech-orders-api-github-ecr", "stages" : ["tests", "build_scan", "publish", "gitops", "rollout", "health"] }, "orders-worker" : { "document" : "CosmoTech-Record-Orders-Worker-Pipeline", "role" : "cosmotech-orders-worker-github-ecr", "stages" : ["tests", "build_scan", "publish", "gitops", "rollout", "health"] }, "gitops" : { "document" : "CosmoTech-Record-GitOps-Pipeline", "role" : "cosmotech-gitops-github-sync", "stages" : ["validate", "rollout", "health"] } }
}

resource "aws_ssm_document" "pipeline_console_install" {
  name          = "CosmoTech-Install-Cluster-Pipelines"
  document_type = "Command"
  content = jsonencode({
    schemaVersion = "2.2"
    description   = "Publish real per-cluster CI/CD flows in the existing public Kestra console without changing accounts or approval policy"
    mainSteps = [{ action = "aws:runShellScript", name = "installPipelines", inputs = {
      timeoutSeconds = "300"
      runCommand = concat(["#!/bin/bash", "set -Eeuo pipefail", "umask 077", "install -d -m 0755 /opt/cosmotech/pipelines/flows"],
        [for name in setunion(fileset("${path.module}/../../../ops/pipelines", "*.py"), fileset("${path.module}/../../../ops/pipelines", "*.sh"), fileset("${path.module}/../../../ops/pipelines", "flows/*.yaml")) : "printf '%s' '${base64encode(file("${path.module}/../../../ops/pipelines/${name}"))}' | base64 --decode > '/opt/cosmotech/pipelines/${name}'"],
      ["bash /opt/cosmotech/pipelines/install.sh"])
    } }]
  })
  tags = local.common_tags
}
resource "aws_ssm_document" "pipeline_report" {
  for_each      = local.cluster_pipeline_apps
  name          = each.value.document
  document_type = "Command"
  content = jsonencode({
    schemaVersion = "2.2"
    description   = "Record real ${each.key} CI stage evidence and its idempotent Kestra execution"
    parameters = {
      RunId      = { type = "String", allowedPattern = "^[0-9]{1,20}$" }
      RunAttempt = { type = "String", default = "1", allowedPattern = "^[0-9]{1,4}$" }
      CommitSha  = { type = "String", allowedPattern = "^[a-f0-9]{40}$" }
      Stage      = { type = "String", allowedValues = each.value.stages }
      State      = { type = "String", allowedValues = ["running", "success", "failure", "cancelled", "skipped"] }
    }
    mainSteps = [{ action = "aws:runShellScript", name = "recordPipelineStage", inputs = {
      timeoutSeconds = "60"
      runCommand = ["#!/bin/bash", "set -Eeuo pipefail", "umask 077",
      "python3 /opt/cosmotech/pipelines/bridge.py record --app '${each.key}' --run '{{ RunId }}' --attempt '{{ RunAttempt }}' --sha '{{ CommitSha }}' --stage '{{ Stage }}' --state '{{ State }}'"]
    } }]
  })
  tags = local.common_tags
}
resource "aws_iam_role_policy" "pipeline_report" {
  for_each = local.cluster_pipeline_apps
  name     = "cosmotech-kestra-stage-evidence"
  role     = each.value.role
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["ssm:SendCommand"], Resource = concat(
        ["arn:aws:ec2:us-east-1:414053644145:instance/i-0def4e806c49cf60e"],
        each.key == "gitops" ? [for app in values(local.cluster_pipeline_apps) : "arn:aws:ssm:us-east-1:414053644145:document/${app.document}"] : ["arn:aws:ssm:us-east-1:414053644145:document/${each.value.document}"]
      ) },
      { Effect = "Allow", Action = ["ssm:GetCommandInvocation"], Resource = "*" }
    ]
  })
}
