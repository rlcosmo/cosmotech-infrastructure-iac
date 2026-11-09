mock_provider "aws" {}

run "hold_blocks_full_standby" {
  command   = plan
  providers = { aws.standby = aws }
  variables { enable_standby = true }
  expect_failures = [var.enable_standby]
}
run "reject_public_admin_access" {
  command   = plan
  providers = { aws.standby = aws }
  variables { admin_cidrs = ["0.0.0.0/0"] }
  expect_failures = [var.admin_cidrs]
}
run "reject_root_operator" {
  command   = plan
  providers = { aws.standby = aws }
  variables { admin_role_arn = "arn:aws:iam::414053644145:root" }
  expect_failures = [var.admin_role_arn]
}
run "standby_zero_nodes" {
  command   = plan
  providers = { aws.standby = aws }
  variables {
    enable_standby             = true
    account_restricted         = false
    release_evidence           = "MOCK-AWS-RELEASE-NOT-REAL"
    monthly_budget_usd         = 500
    estimated_monthly_cost_usd = 150
    admin_cidrs                = ["192.0.2.10/32"]
    admin_role_arn             = "arn:aws:iam::414053644145:role/cosmotech-dr-operator"
  }
  assert {
    condition     = module.standby[0].cluster_name == "cosmotech-dr"
    error_message = "Isolated cluster name required."
  }
}

run "outbox_retains_durable_journal" {
  command   = plan
  providers = { aws.standby = aws }
  variables {
    enable_standby             = true
    account_restricted         = false
    release_evidence           = "MOCK-NOT-AWS-RELEASE"
    monthly_budget_usd         = 500
    estimated_monthly_cost_usd = 150
    admin_cidrs                = ["192.0.2.10/32"]
    admin_role_arn             = "arn:aws:iam::414053644145:role/cosmotech-dr-operator"
  }
  assert {
    condition     = module.standby[0].outbox_protection.deletion_protection && module.standby[0].outbox_protection.pitr_enabled && module.standby[0].outbox_protection.index_projection == "KEYS_ONLY"
    error_message = "Outbox must retain durable records, PITR and minimal index projection."
  }
}

run "runtime_cannot_target_an_unreviewed_region" {
  command   = plan
  providers = { aws.standby = aws }
  variables {
    enable_standby             = true
    standby_region             = "us-west-2"
    account_restricted         = false
    release_evidence           = "MOCK-NOT-AWS-RELEASE"
    monthly_budget_usd         = 500
    estimated_monthly_cost_usd = 150
    admin_cidrs                = ["192.0.2.10/32"]
    admin_role_arn             = "arn:aws:iam::414053644145:role/cosmotech-dr-operator"
  }
  expect_failures = [var.enable_standby]
}
