mock_provider "aws" {}

run "disabled_by_default" {
  command   = plan
  providers = { aws.standby = aws }
  assert {
    condition     = length(module.standby_sqs) == 0
    error_message = "No standby resources may be provisioned by default."
  }
}

run "reject_missing_financial_ceiling" {
  command   = plan
  providers = { aws.standby = aws }
  variables {
    enable_standby_queues      = true
    monthly_budget_usd         = 0
    estimated_monthly_cost_usd = 10
  }
  expect_failures = [var.enable_standby_queues]
}

run "reject_estimate_over_budget" {
  command   = plan
  providers = { aws.standby = aws }
  variables {
    enable_standby_queues      = true
    monthly_budget_usd         = 5
    estimated_monthly_cost_usd = 10
  }
  expect_failures = [var.enable_standby_queues]
}
