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
run "reuse_verified_foundation_without_new_iam_or_kms" {
  command   = plan
  providers = { aws.source = aws }
  variables {
    enabled                       = true
    account_release_confirmed     = true
    advanced_ddb_backup_confirmed = true
    destination_vault_arn         = "arn:aws:backup:us-east-2:414053644145:backup-vault:cosmotech-dr"
    budget_usd                    = 50
    estimate_usd                  = 40
    existing_source_vault_name    = "cosmotech-itsm-dev-vault"
    existing_source_role_arn      = "arn:aws:iam::414053644145:role/cosmotech-itsm-dev-backup-role"
  }
  assert {
    condition     = length(aws_kms_key.backup) == 0 && length(aws_iam_role.backup) == 0 && length(aws_backup_vault.source) == 0
    error_message = "Reuse must not create duplicate keys, roles or vaults."
  }
  assert {
    condition     = aws_backup_selection.orders[0].iam_role_arn == var.existing_source_role_arn && one(aws_backup_plan.hourly[0].rule).target_vault_name == var.existing_source_vault_name
    error_message = "Only the verified existing foundation may be selected."
  }
}
run "reject_unrelated_backup_foundation" {
  command   = plan
  providers = { aws.source = aws }
  variables {
    existing_source_vault_name = "unrelated-vault"
    existing_source_role_arn   = "arn:aws:iam::414053644145:role/unrelated"
  }
  expect_failures = [var.existing_source_vault_name]
}
