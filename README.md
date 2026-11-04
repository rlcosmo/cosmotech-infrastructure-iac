# cosmotech-infrastructure-iac

Only this repository manages the existing S3 backend/key. No state was copied or migrated. ops/itsm, automation source and Lambda source are pinned bootstrap/build snapshots required by existing Terraform file() references; migrate these references to component artifacts before removing snapshots.

Extracted from [aws-serverless-lab](https://github.com/rlcosmo/aws-serverless-lab) at `6f3e67f9e93d626393b23b6884b031767a63fea0` with original commit ancestry preserved. Original files remain available there as migration snapshots. No infrastructure was recreated by extraction.

## Operations

This repository is the sole Terraform manager. Backend bucket/key, encryption, versioning and S3 locking are unchanged. Never apply the frozen snapshot in aws-serverless-lab. CI has no resource-write permission; its `-refresh=false` plan checks source changes only. Before any deployment, generate and review a fresh refreshed plan locally, including security findings. Two legacy Lambda artifact updates were deliberately excluded from this delivery and remain for explicit review.

Dedicated Argo CD host: `i-0b33fdd51332e04e5`, private subnet, no public IP/SSH, 40 GiB encrypted gp3, IMDSv2 required. Reconstruct with reviewed Terraform, then SSM document `CosmoTech-Install-Dedicated-ArgoCD` (`$LATEST`). Versions: K3s `v1.37.1+k3s1`, Helm `v4.3.1`, Argo chart `10.12.1`. Run `ops/argocd/connect-gitops.sh` on the host after the read-only public deploy key is registered in GitHub. The private key restores from Secrets Manager automatically; never copy it into Git or command output.

Credentials: Secrets Manager `cosmotech/argocd/admin` and `cosmotech/argocd/github-deploy-key`. No secret values enter Terraform. GitOps Helm values are a bootstrap snapshot; this repository's `ops/argocd/values.yaml` controls the installed management plane until self-management is explicitly adopted. Application manifests and release proposals belong in cosmotech-gitops. RBAC belongs in cosmotech-kubernetes-rbac.

Backup: root-only SQLite online backup with integrity check, K3s token/encryption configuration and SHA256 captures daily at 04:20 UTC; existing encrypted EC2 backup plan captures the host. Cleanup of logical captures remains manual. Restore the EC2 recovery point to an isolated private host, restore the captured SQLite database/config/token/encryption files before starting K3s, preserve network restrictions, then reconnect the restored read-only GitHub key and verify EKS access/public authentication. Do not run the original and restored Argo application controllers against the same workloads simultaneously. Full boot restore remains pending. Rollback management-plane config with the prior reviewed Helm values; leave EKS workloads/data intact.

Approximate incremental baseline: t3.large USD 0.0832/hour × 730 = USD 60.74/month; 40 GiB gp3 about USD 3.20/month; two standard Secrets about USD 0.80/month; two Argo WAF rules about USD 2/month, plus detailed monitoring, snapshots, traffic/logs and taxes. Grafana added two separate WAF rules (~USD 2/month). Existing ALB/NAT/EKS are reused. No spending cap is enforced. Rates: AWS Pricing API for EC2 on 2026-11-04; [EBS](https://aws.amazon.com/ebs/pricing/), [Secrets Manager](https://aws.amazon.com/secrets-manager/pricing/), [WAF](https://aws.amazon.com/waf/pricing/).
