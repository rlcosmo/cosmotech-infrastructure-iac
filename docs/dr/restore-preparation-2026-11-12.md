# Ohio restore preparation after quota approval

AWS approval supplied by the owner increased Standard On-Demand capacity to 16
vCPUs. GetServiceQuota returned **16** at 2026-11-12 04:42 UTC. Case closure was
therefore not proof of rejection; the earlier five-vCPU observation was within
the announced propagation window. This supersedes the quota blocker in the
previous dated report without rewriting its historical observations.

The plan creates 79 isolated resources with two temporary setup workers (4
vCPUs), no optional management host yet, and no primary updates/deletes. The
standby target remains zero desired workers after validation. Cost assumptions
remain those documented in validation-2026-11-12.md; host and variable restore
costs require inclusion before expanding that scenario.

Added a dedicated destination AWS Backup restore role, constrained by the
bootstrap-owned runtime boundary, for new DR tables/EBS volumes. No source
restores or destructive cleanup are granted. Expanded only exact planned-role
provisioning and read-only backup/DynamoDB readiness permissions. Generated an
updated IAM baseline from the actual plan and validated the reviewed policies
with IAM Access Analyzer (no findings).

Live source readiness: advanced DynamoDB Backup management is already enabled;
Orders table is ACTIVE with 3,418 eventually consistent ItemCount / 1,058,809
reported bytes and deletion protection. PITR is DISABLED; this is a recovery gap,
not a reason to alter global Backup settings. No table data has been read or
changed in this readiness check.

Fixed an existing restore compiler/runtime defect using live AWS evidence:
EBS Backup points use accountless `arn:aws:ec2:REGION::snapshot/ID` ARNs. Validate
the canonical Ohio ARN and separately require the owned destination Backup
vault ARN before accepting a point or starting any restore. Foreign/primary
regions and malformed or foreign-account snapshot ARNs remain rejected. Tests
cover valid EBS points, foreign vaults, and stable restore idempotency tokens.

Checks: 125 local unit tests (2 skipped), 13 Terraform safety tests, 7 SDK Backup
unit tests, real Terraform plan and IAM validation passed. These are preparation
checks; no real failover, failback, or RTO/RPO result is claimed here.


## First live deployment and dependency corrections

The live apply created isolated DR resources and the EKS control plane. Initial
IAM denials were corrected narrowly: CreateSubnet/CreateRouteTable must also
be allowed on the tagged parent VPC; Backup vault creation requires the documented
backup-storage:MountCapsule plus KMS crypto/grant operations on the DR key. The
node group requires RunInstances/CreateTags for its custom launch template. Its
launch authorization is restricted to the recorded DR template and t3.large.
No AdministratorAccess or generic IAM administration was granted.

The dedicated DR/bootstrap remote state namespace already declared by the
repository was checked (404), initialized against the existing versioned backend
and used only for its IAM observer/publisher resources. This is a new state object
in the validated existing namespace, not a replacement backend or migration.
The primary state key remains protected from write. Short-lived role credentials
access the backend; the existing non-root maintenance identity performs only IAM
bootstrap. No permanent keys were created.

Orders backup 99d75f97-aa1c-4c5a-9948-cee09800ae2e completed; cross-region copy
82a2291e-9f4b-4466-9a3a-d757e2dd3047 started. Seven fresh EBS backups (197 GiB
allocated source volume sizes) also completed and their copies were requested.
These are real AWS jobs, not restore/integrity proof. All have seven-day retention;
source volumes, PVCs and older recovery points were preserved.

A new main-only OIDC workflow copies the already reviewed Orders image digests
with skopeo --all --preserve-digests. Source permissions are pull-only for the two
Lab repositories; writes target only the three Ohio DR repositories. Auth files
and 15-minute execution evidence live privately on the runner and are removed
on exit. DR_AWS_RELEASE_CONFIRMED was set true only after live service checks.


## Subsequent live evidence

Both workers reached Ready in us-east-2a/us-east-2b; node group ACTIVE. Its failed
creation also needed GetRole on the exact existing EKS nodegroup service-linked
role. The shared runtime boundary needed read-only pulls from the observed
AWS-owned EKS system image repositories. Pods confirmed the earlier ECR boundary
denial; no external repository writes were added. System pods then became Running.

The interrupted create marked two existing add-ons tainted. DescribeAddon proved
both existed with only missing-node replicas; they were untainted in the isolated
DR state and preserved. Versions are now pinned to actual Ohio observations.
The next reviewed plan created only the missing node group, with no deletion.

Orders restore completed and consistent scans compared all 3,418 records in each
region: identical aggregate SHA256, zero missing/extra/modified records. Private
per-record evidence stores hashes, never payloads. This is not a writer fence or
platform RPO proof. The existing restored table was imported into its DR owner;
Terraform enabled deletion protection and seven-day PITR, with prevent_destroy.
The data lookup fails closed if the separately restored table is absent.

Seven copied EBS points passed ownership/completion/encryption/regional-KMS
checks. Their correspondence was documented before the real seven-volume restore;
all seven jobs completed in us-east-2b. Application-level filesystem validation
and PV/PVC binding remain pending. A fresh consistent ITSM logical backup also
completed and its separate 50-GiB volume backup was initiated.

Both real GitHub Actions image publication workflows succeeded through OIDC,
including digest-preserving Orders copy and build/scan/push of the outbox image.
See live-data-validation-2026-11-12.json for run IDs and sanitized data evidence.
