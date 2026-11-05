resource "aws_cloudwatch_log_group" "external_http_probe" {
  name              = "/aws/lambda/cosmotech-external-http-probe"
  retention_in_days = 7
  tags              = local.common_tags
}
resource "aws_iam_role" "external_http_probe" {
  name               = "cosmotech-external-http-probe"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }] })
  tags               = local.common_tags
}
resource "aws_iam_role_policy" "external_http_probe" {
  name = "write-synthetic-logs-and-failure-notification"
  role = aws_iam_role.external_http_probe.name
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.external_http_probe.arn}:*" },
    { Effect = "Allow", Action = ["sns:Publish"], Resource = "arn:aws:sns:us-east-1:414053644145:cosmotech-lab-alerts" }
  ] })
}
resource "aws_lambda_function" "external_http_probe" {
  function_name                  = "cosmotech-external-http-probe"
  role                           = aws_iam_role.external_http_probe.arn
  filename                       = "${path.module}/../../../dist/external-http-probe.zip"
  source_code_hash               = filebase64sha256("${path.module}/../../../dist/external-http-probe.zip")
  handler                        = "lambda_function.handler"
  runtime                        = "python3.12"
  memory_size                    = 128
  timeout                        = 20
  reserved_concurrent_executions = 1
  depends_on                     = [aws_iam_role_policy.external_http_probe]
  tags                           = local.common_tags
}
resource "aws_lambda_function_event_invoke_config" "external_http_probe" {
  function_name                = aws_lambda_function.external_http_probe.function_name
  maximum_event_age_in_seconds = 300
  maximum_retry_attempts       = 0
  destination_config {
    on_failure {
      destination = "arn:aws:sns:us-east-1:414053644145:cosmotech-lab-alerts"
    }
  }
}
resource "aws_scheduler_schedule_group" "observability" {
  name = "cosmotech-observability"
  tags = local.common_tags
}
resource "aws_iam_role" "external_http_probe_scheduler" {
  name               = "cosmotech-external-http-probe-scheduler"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "scheduler.amazonaws.com" }, Action = "sts:AssumeRole", Condition = { StringEquals = { "aws:SourceAccount" = "414053644145", "aws:SourceArn" = aws_scheduler_schedule_group.observability.arn } } }] })
  tags               = local.common_tags
}
resource "aws_iam_role_policy" "external_http_probe_scheduler" {
  name   = "invoke-fixed-public-probe"
  role   = aws_iam_role.external_http_probe_scheduler.name
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = ["lambda:InvokeFunction"], Resource = aws_lambda_function.external_http_probe.arn }] })
}
resource "aws_scheduler_schedule" "external_http_probe" {
  name                = "external-app-http"
  group_name          = aws_scheduler_schedule_group.observability.name
  schedule_expression = "rate(5 minutes)"
  flexible_time_window { mode = "OFF" }
  target {
    arn      = aws_lambda_function.external_http_probe.arn
    role_arn = aws_iam_role.external_http_probe_scheduler.arn
    input    = jsonencode({ synthetic = true, interval_seconds = 300 })
    retry_policy {
      maximum_event_age_in_seconds = 300
      maximum_retry_attempts       = 0
    }
  }
  depends_on = [aws_iam_role_policy.external_http_probe_scheduler]
}
resource "aws_cloudwatch_metric_alarm" "external_http_probe" {
  alarm_name          = "cosmotech-external-http-probe-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.external_http_probe.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = ["arn:aws:sns:us-east-1:414053644145:cosmotech-lab-alerts"]
  tags                = local.common_tags
}
