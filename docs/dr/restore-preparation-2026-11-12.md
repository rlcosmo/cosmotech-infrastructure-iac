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
