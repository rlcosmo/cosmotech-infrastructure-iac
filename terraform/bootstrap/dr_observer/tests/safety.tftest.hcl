mock_provider "aws" {}
run "default_no_bootstrap" {
  command = plan
  assert {
    condition     = length(aws_iam_role.observer) == 0
    error_message = "No IAM change by default."
  }
}
run "held_account_rejected" {
  command = plan
  variables { enabled = true }
  expect_failures = [var.enabled]
}
run "root_operator_source_rejected" {
  command = plan
  variables { operator_source_role_arn = "arn:aws:iam::414053644145:root" }
  expect_failures = [var.operator_source_role_arn]
}
