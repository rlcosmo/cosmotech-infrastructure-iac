# Runbooks V2-003 — checkpoint-driven, no production failover in this delivery

## 0. Release and bootstrap (currently BLOCKED)

1. Obtain explicit AWS release confirmation for case `179418083900558`; record it
   outside public Git if it includes private information. ACTIVE plan/credits,
   working RDS or listing service metadata do not establish account clearance.
2. Use temporary non-root role credentials. Never modify persistent AWS profiles.
   Verify STS account 414053644145/assumed-role and region us-east-2. Read-only
   `scripts/dr/preflight.py --aws-release-reference <official-reference>` checks
   scope, versions, offerings and a minimum vCPU quota; its `safe_to_apply=false`
   is intentional until spare usage, dependencies/cost/fence are reviewed.
3. Refresh quotes (max seven days), region/AMI/add-on/chart compatibility, all
   source owner/state references, volume/PVC retention and logical dump freshness.
   Check current quota usage (not just quota limits), EC2 capacity, EKS/add-ons,
   S3/Backup/DynamoDB/ECR/KMS permissions, image/scanning and cross-region transfer.
4. Bootstrap ONLY `terraform/bootstrap/dr_observer` after AWS release; default
   `enabled=false`. Scope OIDC to the existing immutable repo/main subject. Set
   `DR_AWS_RELEASE_CONFIRMED=true` only after verification, not to bypass the hold.
5. The release-gated DR workflow can run a READ-ONLY plan. Configure reviewed
   `DR_PLAN_TFVARS_JSON`, including cost ceiling, complete estimate, admin role and
   narrow CIDRs. Do not upload plan/state JSON publicly: it can contain metadata.
   Generate minimum deployment permissions from the actual approved plan:
   `uvx iam-policy-autopilot@latest generate-policies --help` then use its documented
   Terraform plan input with account 414053644145/us-east-2. Review output against
   resource allowlist and forbid primary IDs, network/DNS mutations and deletions.
   No blanket permanent AdministratorAccess or root workflow.
6. Create destination first using its isolated backend. Defaults are all OFF;
   enable only after the full preflight, allowlisted plan and financial limit.
   `terraform -chdir=terraform/environments/dr plan -out=<private-plan>`;
   render `terraform show -json` privately and run `scripts/dr/check_plan.py`.
   Apply ONLY the exact reviewed plan with a scoped temporary provisioning role.
   Never apply `dev`, Power Manager, or source EKS roots. No automatic apply job
   is present while the account is restricted.
7. Enable source backup-only root AFTER destination vault exists, advanced DynamoDB
   backup opt-in is verified and explicit volume ARNs are approved. No global
   backup settings are changed automatically. Check first COMPLETED copy and a
   successful isolated restore before considering the backups effective.
8. Securely stage encrypted/checksummed ITSM bundles, necessary regional secrets
   and immutable image mirrors. `copy_images.py <gitops-release-lock>` only prints
   destination copy commands unless explicitly executed with short-lived verified
   preparation evidence. Skopeo `--all --preserve-digests` preserves manifest lists;
   inspect every destination digest before setting `images_verified=true`.

Abort if identity, account release, quota, scope, cost, integrity or telemetry is
uncertain. Do not fall back to another account/region/service to bypass a hold.

## 1. Baseline and passive bootstrap

- Capture approved primary network/config hash and health WITHOUT modifying it.
  Record source repo SHAs, Terraform state owners/serials (no secrets), PVC/PV IDs,
  actual backup timestamps, table schema/item counts/inventory/transactions and
  queue age/inflight/DLQ counts. Do not Receive/Delete/Purge source SQS messages.
- Save baseline and audit in encrypted destination storage with immutable object
  VersionId; do not paste state, credentials or customer orders into GitHub logs.
- Use a NEW temporary KUBECONFIG. Assert its server equals the Terraform output for
  `cosmotech-dr`, account 414053644145/us-east-2. Do not change the user's default
  context. Ensure primary Argo and Power Manager never register this cluster.
- Scale destination nodes only; record prior capacity. EKS control plane, nodes,
  CNI/Pod Identity, EBS CSI and DNS must be healthy before restores. Three AZs are
  configured, but actual node placement/spare subnet IPs must be verified.
- Render chart bundle (`python scripts/dr/render_platform.py --output <private-dir>`)
  and verify SHA256; review the rendered resources before a destination-only
  install in order: namespaces/storage class, Istio base/istiod/gateway, local Argo,
  secret provisioner/manual secure secret, monitoring/OTel. No public LB or DNS.
  Grafana admin secret must exist; never use a default/shared password.
- Bootstrap the independent DR AppProject/Application from cosmotech-gitops
  `disaster-recovery/bootstrap.json`. Render `render_bootstrap.py --revision <reviewed-full-commit>` before the first sync; automated sync is OFF. Application destinations
  are local-only. Orders replicas stay zero, worker control remains `pause`, no
  HPA/canary can recreate capacity. Do not patch primary manifests.

## 2. Restore and isolated exercise (not a production switch)

1. Create a change/audit record using the existing approved process or an offline
   signed record if GLPI/Kestra is unavailable; recovery must not require primary
   applications. Acquire an external single-operation lock, record increasing epoch
   and immutable evidence reference. The planner does NOT acquire a distributed
   lock: its booleans are evidence inputs, not proof or a runtime fencing service.
2. Record latest completed destination backups and measured lag. Retrieve
   `get-recovery-point-restore-metadata` for each recovery point and compile the
   destination-only commands with `restore_plan.py`. It rejects primary ARNs,
   unsupported types, empty backups, mutable identifiers and source-table names.
3. Restore a NEW table and NEW encrypted volumes only. Wait for all jobs/table
   readiness; verify order_id schema, inventory, idempotency/lease/transaction
   records, item counts/hashes and expiry semantics. Enable destination PITR and
   deletion protection without reseeding/resetting business data. Do not reuse
   leases copied from a still-active worker. Bind static retained PV/PVCs with the
   new volume IDs/AZ topology; never point to or detach primary volumes.
4. Reconcile acknowledged journal/checkpoint with recovered records and pending
   events. `replay_events` validates v2 event bodies/immutable IDs and produces a
   deduplicated list, but does not send messages or create a source outbox. Current
   API lacks durable acceptance journal: if the source queue is lost and no audited
   journal exists, record data loss/UNKNOWN RPO and BLOCK promotion. For isolated
   drills use ONLY synthetic NEW queues/table/inventory, never real pending orders.
5. Verify regional image digests, scoped Pod Identity, secrets and dependency URLs.
   No source ARN/endpoints in destination ConfigMaps or runtime credentials.
   `plan_recovery.py <evidence>` requires fresh <=15-minute evidence, AWS release,
   fence/checkpoint references, no concurrent writers/inflight, and all recovery
   checks. It emits an audit hash and plan ONLY; it cannot activate consumers or DNS.
6. For a synthetic rehearsal, approve a NEW immutable GitOps DR commit enabling
   only synthetic API/worker replicas and worker `run`; source stays untouched.
   Do not allow a second real writer. Sync that exact DR revision manually through
   the secondary Argo. Health/live returning 200 is insufficient: `probe_e2e.py`
   POSTs a synthetic order through its own verified local DR tunnel, polls COMPLETED and
   records outcome. Use `--kubeconfig <temporary-DR-file>`; the script verifies STS role,
   destination cluster ARN/endpoint against that kubeconfig and creates its OWN
   port-forward. A pre-existing localhost tunnel is not accepted. Validate stock/side effects and duplicated-order behavior.
7. Restore ITSM using clean optional VM and `ops/dr/itsm/compose.json`: decrypt
   backup bundle and SHA256-check locally; secrets go in mode-0600 local files via
   secure APIs, never argv/Git/logs. Start database services first with internal-only
   network, restore glpi.sql/kestra.dump and matching GLPI files/Kestra storage.
   Run mariadb-check, pg_restore checks, compare ticket/flow counts and decrypt a
   harmless known Kestra value using the restored key. Sanitize regional URLs;
   disable schedules/triggers/webhooks/mail before opting into `recovery-apps`.
   Keep external egress disabled; no Docker socket, no primary AWS/GitHub credentials.
   Grafana needs its DB/plugins/datasource secrets; Loki/Tempo/Zabbix restoration
   remains a separate unvalidated checklist (do not claim recovery from pod Ready).
8. End RTO only at successful application E2E + storage/integrity evidence. Compute
   RPO against last client ACK and last verified recovered commit, not snapshot age
   alone. `metrics.py` rejects simulation sources and omits unknown RTO entirely.
   Record public-safe summaries separately from private backup/data evidence.

## 3. Actual production failover (NOT AUTHORIZED/EXECUTED in this phase)

Requires independent proof the old writer cannot resume: source-side IAM/traffic
fencing under a NEW reviewed change or an external authority that the application
enforces. DNS, a local boolean or eventually consistent DynamoDB lease is not a
fence. If source region is unreachable and no durable fence can be proved, STOP.
After integrity/E2E and approval, provision public secondary TLS/ingress with a new
cost review; change routing only via its source owner. Existing primary LB/NAT/EIP
must not be removed/recreated. A production DNS change is absent from this code.

## 4. Failback / rollback

- Treat failback as a new operation, never blindly reverse DNS or replay snapshots.
  Capture new secondary writes/checkpoints, validate reverse data reconciliation,
  stop/drain secondary writers and prove fencing before allowing primary writers.
  Restore primary-side data only through a separate approved data-owner workflow;
  no script here mutates primary. Verify duplicate effects, inventory, payments and
  fulfillment consistency before traffic switch. Record RTO/RPO and audit hashes.
- During an isolated drill rollback, return DR replicas to zero and worker `pause`
  by reviewed GitOps commit; restore its original capacity. Retain restored EBS/PVC,
  backup points and encrypted evidence. Never delete source data/queues to clean up.
- Abort on missing telemetry, unsafe target, quota/cost breach, integrity mismatch,
  duplicate side effects or a change in protected-primary hash. Stop destination
  consumers first, preserve evidence, recover the destination only. There is no
  `terraform destroy` command in the runbook.

## 5. Chaos Engineering

`simulate_chaos.py` executes local models only (pod loss, node loss, AZ loss and
regional dependency outage); outputs explicitly say simulation and have null AWS
RTO/RPO. They exercise journal/replay/control safety and DO NOT prove fault
injection, Kubernetes rescheduling or AZ resilience in AWS.

After provisioning, target ONLY `cosmotech-dr`, namespace `dr-test`, synthetic
requests and labeled secondary resources. Maximum fault duration 120s, secondary
spare capacity required, stop immediately on lost telemetry or primary change.
Pod: kill synthetic probe pod and observe restart. Node: cordon/drain ONE secondary
synthetic node with Retain volumes and bounded PDB checks; uncordon in finally.
AZ: simulate unavailability of the synthetic deployment's AZ with scheduling
constraints; do not modify routes/subnets/SGs or terminate all nodes. Dependency:
block only the synthetic client's destination in its own namespace/proxy, then
restore in finally. Fault Injection Service or Chaos Mesh is NOT deployed now.
Record timeline, completed IDs, queue age/DLQ, metrics, integrity and target IDs.
No automatic destructive runner exists while the account remains restricted.
