locals {
  snapshot_bucket = "cosmotech-power-snapshots-414053644145-us-east-1"
  agent_hash      = substr(sha256("${filesha256("${path.module}/../../ops/power-manager/agent.py")}:${filesha256("${path.module}/../../ops/power-manager/host_agent.py")}"), 0, 16)
  agent_script    = base64encode(file("${path.module}/../../ops/power-manager/agent.py"))
  agent_parameters = {
    PlanId          = { type = "String", allowedPattern = "^[0-9a-f-]{36}$" }
    OperationId     = { type = "String", allowedPattern = "^[0-9a-f-]{36}$" }
    SnapshotVersion = { type = "String", allowedPattern = "^[A-Za-z0-9+/=._-]{1,200}$", default = "NONE" }
  }
}
resource "aws_s3_bucket" "snapshots" {
  bucket = local.snapshot_bucket
  lifecycle { prevent_destroy = true }
}
resource "aws_s3_bucket_versioning" "snapshots" {
  bucket = aws_s3_bucket.snapshots.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "snapshots" {
  bucket = aws_s3_bucket.snapshots.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
resource "aws_s3_bucket_public_access_block" "snapshots" {
  bucket                  = aws_s3_bucket.snapshots.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_policy" "snapshots" {
  bucket = aws_s3_bucket.snapshots.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Sid = "TLSOnly", Effect = "Deny", Principal = "*", Action = "s3:*", Resource = [aws_s3_bucket.snapshots.arn, "${aws_s3_bucket.snapshots.arn}/*"], Condition = { Bool = { "aws:SecureTransport" = "false" } } }
  ] })
}
resource "aws_ssm_document" "agent" {
  for_each = {
    read    = { name = "CosmoTech-Power-Read", phases = ["SNAPSHOT", "SAFE_TEST"] }
    execute = { name = "CosmoTech-Power-Execute", phases = ["PREPARE", "QUIESCE", "RESUME", "VERIFY"] }
  }
  name            = each.value.name
  document_type   = "Command"
  document_format = "JSON"
  content = jsonencode({
    schemaVersion = "2.2"
    description   = "Fixed, reviewed lab power agent; no arbitrary shell parameters"
    parameters    = merge(local.agent_parameters, { Phase = { type = "String", allowedValues = each.value.phases } })
    mainSteps = [{ action = "aws:runShellScript", name = "powerAgent", inputs = {
      timeoutSeconds = "600"
      runCommand     = ["#!/bin/bash", "set -Eeuo pipefail", "umask 077", "install -d -m 0700 /opt/cosmotech/power-manager", "printf '%s' '${local.agent_script}' | base64 --decode > /opt/cosmotech/power-manager/agent-${local.agent_hash}.py", "exec python3 /opt/cosmotech/power-manager/agent-${local.agent_hash}.py --bucket ${local.snapshot_bucket} --plan-id '{{ PlanId }}' --operation-id '{{ OperationId }}' --version '{{ SnapshotVersion }}' --phase '{{ Phase }}'"]
    } }]
  })
}
data "aws_iam_role" "worker" { name = "cosmotech-power-worker" }
data "aws_iam_role" "orchestrator" { name = "cosmotech-power-orchestrator" }
resource "aws_cloudwatch_log_group" "worker" {
  name              = "/aws/lambda/cosmotech-power-worker"
  retention_in_days = 14
}
resource "aws_lambda_function" "worker" {
  function_name                  = "cosmotech-power-worker"
  filename                       = "${path.module}/../../build/power-manager.zip"
  source_code_hash               = filebase64sha256("${path.module}/../../build/power-manager.zip")
  role                           = data.aws_iam_role.worker.arn
  handler                        = "power_manager.executor.handler"
  runtime                        = "python3.12"
  memory_size                    = 256
  timeout                        = 90
  reserved_concurrent_executions = 1
  environment {
    variables = {
      TABLE                 = aws_dynamodb_table.control.name
      SNAPSHOT_BUCKET       = aws_s3_bucket.snapshots.id
      HOST_READ_DOCUMENT    = aws_ssm_document.host["read"].name
      HOST_EXECUTE_DOCUMENT = aws_ssm_document.host["execute"].name
      READ_DOCUMENT         = aws_ssm_document.agent["read"].name
      AGENT_HASH            = local.agent_hash
      AGENT_DOCUMENT        = aws_ssm_document.agent["execute"].name
      SIGNING_PARAMETER     = "/cosmotech/power-manager/proxy-signing-key"
    }
  }
  depends_on = [aws_cloudwatch_log_group.worker, aws_s3_bucket_versioning.snapshots]
}
resource "aws_sfn_state_machine" "operations" {
  name     = "cosmotech-power-operations"
  role_arn = data.aws_iam_role.orchestrator.arn
  type     = "STANDARD"
  definition = jsonencode({
    Comment        = "Serialized, checkpointed power operations with immutable restore snapshot"
    TimeoutSeconds = 3600
    StartAt        = "Tick"
    States = {
      Tick = {
        Type       = "Task", Resource = "arn:aws:states:::lambda:invoke"
        Parameters = { FunctionName = aws_lambda_function.worker.arn, "Payload.$" = "$" }
        ResultPath = "$.tick", Next = "Check"
        Retry      = [{ ErrorEquals = ["Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException", "Lambda.TooManyRequestsException"], IntervalSeconds = 2, MaxAttempts = 3, BackoffRate = 2 }]
        Catch      = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.failure", Next = "RecordFailure" }]
      }
      Check = { Type = "Choice", Choices = [
        { Variable = "$.tick.Payload.state", StringEquals = "done", Next = "Complete" },
        { Variable = "$.tick.Payload.state", StringEquals = "failed", Next = "Failed" }
      ], Default = "Wait" }
      Wait = { Type = "Wait", Seconds = 20, Next = "Tick" }
      RecordFailure = {
        Type       = "Task", Resource = "arn:aws:states:::lambda:invoke"
        Parameters = { FunctionName = aws_lambda_function.worker.arn, "Payload.$" = "$" }
        ResultPath = "$.tick", Next = "Failed"
      }
      Complete = { Type = "Succeed" }
      Failed   = { Type = "Fail", Error = "RecoveryRequired", Cause = "Original operational snapshot preserved; use the recovery flow" }
    }
  })
}
resource "aws_apigatewayv2_route" "detail" {
  for_each  = toset(["GET /api/plans/{id}", "GET /api/operations/{id}"])
  api_id    = aws_apigatewayv2_api.power.id
  route_key = each.value
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"
}

output "state_machine_arn" { value = aws_sfn_state_machine.operations.arn }
output "agent_hash" { value = local.agent_hash }

resource "aws_ssm_document" "host" {
  for_each = {
    read    = { name = "CosmoTech-Power-Host-Read", modes = ["SNAPSHOT", "SAFE_TEST"] }
    execute = { name = "CosmoTech-Power-Host-Execute", modes = ["RESTORE", "VERIFY", "PAUSE_AUTOMATION", "RESUME_AUTOMATION"] }
  }
  name            = each.value.name
  document_type   = "Command"
  document_format = "JSON"
  content = jsonencode({
    schemaVersion = "2.2"
    description   = "Restore only existing original processes; no config/image/network changes"
    parameters    = merge(local.agent_parameters, { Mode = { type = "String", allowedValues = each.value.modes } })
    mainSteps = [{ action = "aws:runShellScript", name = "hostPower", inputs = {
      timeoutSeconds = "600"
      runCommand     = ["#!/bin/bash", "set -Eeuo pipefail", "umask 077", "install -d -m 0700 /opt/cosmotech/power-manager", "printf '%s' '${base64encode(file("${path.module}/../../ops/power-manager/host_agent.py"))}' | base64 --decode > /opt/cosmotech/power-manager/host-${substr(filesha256("${path.module}/../../ops/power-manager/host_agent.py"), 0, 16)}.py", "exec python3 /opt/cosmotech/power-manager/host-${substr(filesha256("${path.module}/../../ops/power-manager/host_agent.py"), 0, 16)}.py --bucket ${local.snapshot_bucket} --plan-id '{{ PlanId }}' --operation-id '{{ OperationId }}' --version '{{ SnapshotVersion }}' --mode '{{ Mode }}'"]
    } }]
  })
}
