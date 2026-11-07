terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
  backend "s3" {
    bucket               = "aws-serverless-lab-414053644145-tfstate-use1"
    key                  = "lab-v2/power-manager/terraform.tfstate"
    region               = "us-east-1"
    use_lockfile         = true
    encrypt              = true
    workspace_key_prefix = "lab-v2/power-manager/workspaces"
  }
}
provider "aws" {
  region              = "us-east-1"
  allowed_account_ids = ["414053644145"]
  default_tags { tags = { Project = "cosmotech-power-manager", Classification = "ALWAYS-ON", ManagedBy = "terraform" } }
}
data "aws_iam_role" "runtime" { name = "cosmotech-power-runtime" }
resource "aws_dynamodb_table" "control" {
  name         = "cosmotech-power-control"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  attribute {
    name = "pk"
    type = "S"
  }
  point_in_time_recovery { enabled = true }
  server_side_encryption { enabled = true }
  deletion_protection_enabled = true
  lifecycle { prevent_destroy = true }
}
resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/cosmotech-power-api"
  retention_in_days = 14
}
resource "aws_lambda_function" "api" {
  function_name                  = "cosmotech-power-api"
  filename                       = "${path.module}/../../build/power-manager.zip"
  source_code_hash               = filebase64sha256("${path.module}/../../build/power-manager.zip")
  role                           = data.aws_iam_role.runtime.arn
  handler                        = "power_manager.api.handler"
  runtime                        = "python3.12"
  memory_size                    = 256
  timeout                        = 29
  reserved_concurrent_executions = 2
  environment {
    variables = {
      TABLE             = aws_dynamodb_table.control.name
      SIGNING_PARAMETER = "/cosmotech/power-manager/proxy-signing-key"
    }
  }
  depends_on = [aws_cloudwatch_log_group.api]
}
resource "aws_apigatewayv2_api" "power" {
  name          = "cosmotech-power-api"
  protocol_type = "HTTP"
}
resource "aws_apigatewayv2_integration" "api" {
  api_id                 = aws_apigatewayv2_api.power.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 29000
}
resource "aws_apigatewayv2_route" "api" {
  for_each  = toset(["GET /api/status", "GET /api/inventory", "POST /api/plan", "POST /api/operations"])
  api_id    = aws_apigatewayv2_api.power.id
  route_key = each.value
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"
}
resource "aws_apigatewayv2_stage" "api" {
  api_id      = aws_apigatewayv2_api.power.id
  name        = "$default"
  auto_deploy = true
  default_route_settings {
    throttling_burst_limit = 5
    throttling_rate_limit  = 2
  }
}
resource "aws_lambda_permission" "api" {
  statement_id   = "OnlyPowerApi"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.api.function_name
  principal      = "apigateway.amazonaws.com"
  source_arn     = "${aws_apigatewayv2_api.power.execution_arn}/*/*/api/*"
  source_account = "414053644145"
}
output "api_endpoint" { value = aws_apigatewayv2_api.power.api_endpoint }
