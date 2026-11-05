# Secret values are written through role-based operational tooling, never Terraform/state.
locals {
  tool_secret_names = jsondecode(file("${path.module}/../../../ops/credential-catalog/catalog.json"))
}
resource "aws_secretsmanager_secret" "tool_credentials" {
  for_each                = local.tool_secret_names
  name                    = each.value
  description             = "CosmoTech ${each.key} credentials; values maintained outside Terraform"
  recovery_window_in_days = 30
  tags                    = merge(local.common_tags, { System = "tool-credentials", DataClassification = "secret" })
  lifecycle { prevent_destroy = true }
}
