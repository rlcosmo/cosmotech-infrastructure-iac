mock_provider "aws" {}
run "default_no_primary_changes" {
  command = plan
  assert {
    condition     = length(aws_dynamodb_table.journal) == 0 && length(aws_iam_policy.producer) == 0 && length(aws_iam_policy.publisher) == 0
    error_message = "Source preparation must be disabled by default."
  }
}
run "account_hold_blocks_new_journal" {
  command = plan
  variables { enabled = true }
  expect_failures = [var.enabled]
}
run "budget_ceiling_blocks_new_journal" {
  command = plan
  variables {
    enabled                   = true
    account_release_confirmed = true
    estimate_usd              = 20
    budget_usd                = 5
  }
  expect_failures = [var.enabled]
}
run "journal_can_reuse_bootstrap_owned_application_policies" {
  command = plan
  variables {
    enabled                     = true
    account_release_confirmed   = true
    budget_usd                  = 10
    estimate_usd                = 1
    manage_application_policies = false
  }
  assert {
    condition     = length(aws_dynamodb_table.journal) == 1 && length(aws_iam_policy.producer) == 0 && length(aws_iam_policy.publisher) == 0
    error_message = "The data role must not create IAM policies already owned by the permanent bootstrap."
  }
}
run "reject_foreign_publisher_role" {
  command = plan
  variables { publisher_role_arn = "arn:aws:iam::414053644145:role/cosmotech-lab-operator" }
  expect_failures = [var.publisher_role_arn]
}
