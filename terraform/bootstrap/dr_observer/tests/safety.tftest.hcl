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
