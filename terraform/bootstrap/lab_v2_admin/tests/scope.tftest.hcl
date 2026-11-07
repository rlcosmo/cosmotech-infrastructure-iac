mock_provider "aws" {}
run "only_bounded_bootstrap_identities" {
  command = plan
  variables {
    expected_account_id      = "000000000000"
    itsm_instance_id         = "i-0123456789abcdef0"
    authorization_expires_at = "2099-01-01T00:00:00Z"
  }
  assert {
    condition     = length(aws_iam_role.bootstrap) == 2
    error_message = "Only the two bootstrap identities may be created."
  }
  assert {
    condition     = length(aws_iam_role_policy.infrastructure.policy) < 10240
    error_message = "The inline policy exceeds the IAM aggregate size limit."
  }
  assert {
    condition     = aws_eks_access_entry.rbac.kubernetes_groups == toset(["cosmotech-lab-v2-rbac-bootstrap"])
    error_message = "EKS must map only the bounded namespace publisher group."
  }
}
