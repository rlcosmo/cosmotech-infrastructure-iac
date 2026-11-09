mock_provider "aws" {}
run "disabled_by_default" {
  command   = plan
  providers = { aws.source = aws }
  assert {
    condition     = length(aws_backup_plan.hourly) == 0 && length(aws_backup_selection.orders) == 0
    error_message = "Backups must not change primary/account configuration by default."
  }
}
run "account_hold_blocks_backup" {
  command   = plan
  providers = { aws.source = aws }
  variables { enabled = true }
  expect_failures = [var.enabled]
}
run "reject_foreign_volume" {
  command   = plan
  providers = { aws.source = aws }
  variables { volume_arns = ["arn:aws:ec2:us-west-2:414053644145:volume/vol-123"] }
  expect_failures = [var.volume_arns]
}
