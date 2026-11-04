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

Deployment order:
1. Review format, validate, security findings and a fresh refreshed Terraform plan
   in this canonical repository. Current IaC CI is validate/plan only; it cannot
   apply. Do not use the frozen lab snapshot or grant CI administrator access.
2. Deploy the scoped role and WAF through an authorized infrastructure delivery
   process. Set worker repo ECR_PUBLISH_ROLE_ARN to the new role ARN.
3. Configure the GitOps App ID/private key through the approved secret-management
   path for the worker repository, then enable DEV_AUTO_PROMOTE=true. No secret
   values are stored in this change. Run worker-ci to build/scan/publish/promote.
4. Install cosmotech-kubernetes-rbac namespaced roles and bindings. Add
   orders-worker to the existing Argo cluster Secret namespaces while preserving
   its renewable exec configuration. configure.py persists this list for bootstrap;
   do not rerun all bootstrap credential generation to change the existing Secret.
5. Apply the reviewed GitOps AppProject/Application on management K3s. Enable
   worker automatic sync only after a health-enabled digest is promoted. API
   bootstrap also needs HPA replica-ignore settings. No direct EKS image patches.
6. Verify HPA metrics-server/ContainerResource support, ScalingActive/AbleToScale,
   ready replicas, Argo Synced/Healthy, DNS/TLS and both external health endpoints.
   Confirm existing queue age/DLQ/processing-error alerts and exercise a synthetic
   lab event only when separately authorized.

HPA caps 4 replicas per app and downscales one pod/minute after 300 seconds.
Replicas may cause node scale-up and increased AWS calls; no new fixed compute or
load balancer is defined. WAF adds two rules (pricing depends on account/region).
Data model, retry/DLQ configuration and data retention remain unchanged. Existing
WAF log redaction and 30-day retention apply to these hosts.

Rollback: revert GitOps image/config and pause worker automated sync; restore the
previous WAF configuration and namespace-scoped RBAC via reviewed delivery. Keep
queues, tables, inventory and existing API infrastructure intact. Never destroy
or recreate resources to recover a failed worker rollout.
