# Orders Worker DEV delivery prerequisites

Owner requested worker extraction, CI/CD, HPA and per-application hostnames.
This change adds an independent GitHub OIDC publisher role for repository ID
1446246146, owner ID 124368446 and refs/heads/main only. ECR permissions match the
existing API publisher but target only aws-eks-istio-lab-dev-orders-worker.
No EKS/SQS/DynamoDB/SSM write permission is added to GitHub Actions.

WAF adds exact anchored API/worker host matching and a shared per-IP limit of
1200 requests per five minutes. Unknown hosts remain blocked. The existing shared
ALB and wildcard ACM certificate are reused by GitOps Ingress definitions.
DNS is managed in Cloudflare outside Terraform: create CNAMEs orders-api-dev and
orders-worker-dev targeting the current shared ALB DNS. Verify TLS and WAF before
claiming endpoints are live. No extra ALB or DNS provider credentials are added.

Automatic DEV delivery is enabled for both applications without human approval.
Approval stages will be added later. Bootstrap prerequisites were deployed after
reviewing a refreshed targeted plan, Terraform validation/formatting and IAM
Access Analyzer findings. The existing WAF rules were compared and preserved
exactly. Unrelated Lambda artifact and infrastructure changes were excluded.

Worker CI owns its dedicated publisher role. The existing GitOps App credential
was provisioned using a sealed box addressed to the worker repository public key;
no plaintext key was downloaded or logged. Both app repositories enable
DEV_AUTO_PROMOTE. Main source/workflow changes test, build, scan, publish immutable
ECR images and automatically merge the validated image-only GitOps proposal.

GitOps main runs deliver-dev with the independent cosmotech-gitops-github-sync
OIDC role. It may invoke only CosmoTech-Sync-Orders-GitOps on the existing dedicated
Argo host and read command status. The fixed document verifies an exact current
main commit over the existing read-only SSH connection, validates manifests,
preserves the cluster exec configuration while updating its namespace scope,
reconciles the versioned AppProject/Applications and waits for Synced/Healthy at
that exact revision. Actions checks external TLS health afterwards. Argo alone
reconciles application images; no EKS image patch is performed by Actions.

The scoped Argo RBAC was server dry-run validated and installed from the merged
cosmotech-kubernetes-rbac source. It grants application resource writes in the two
namespaces, workload/log reads and no Secrets. Worker automatic sync was enabled
only after its first health-enabled digest was successfully published/promoted.

Both DNS names already resolve to the shared ALB; no DNS credentials or mutation
were needed. Current source preserves this external ownership. Verify HPA
AbleToScale/ScalingActive, Argo Synced/Healthy, new pod readiness and the public
/health/live and /health/ready routes on each release. Check queue-age/DLQ and
processing-error alerts. No valid order is submitted by these health checks.

The general Terraform workflow still validates/plans; application delivery is
automatic through the dedicated GitOps workflow, not a general-purpose Terraform
apply identity. Temporary bootstrap permissions are removed after verification.

HPA caps 4 replicas per app and downscales one pod/minute after 300 seconds.
Replicas may cause node scale-up and increased AWS calls; no new fixed compute or
load balancer is defined. WAF adds two rules (pricing depends on account/region).
Data model, retry/DLQ configuration and data retention remain unchanged. Existing
WAF log redaction and 30-day retention apply to these hosts.

Rollback: revert GitOps image/config and pause worker automated sync; restore the
previous WAF configuration and namespace-scoped RBAC via reviewed delivery. Keep
queues, tables, inventory and existing API infrastructure intact. Never destroy
or recreate resources to recover a failed worker rollout.
