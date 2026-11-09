# V2-003 continuation — durable outbox and recovery

Status: implemented and tested in code, NOT deployed or certified in AWS. The
existing primary API still ACKs SQS directly. No primary application repository,
manifest, deployment, state or Power Manager component is changed by this work.
Old acknowledged requests are not retroactively protected by a new journal.

## Durable acceptance / dispatch

`src/disaster_recovery/runtime.py` is a separate standby-only image supporting API,
publisher, worker and metrics modes. It requires a temporary dedicated DR role,
account 414053644145/us-east-2, fixed DR journal/queue/table names. The image vendors
the unchanged worker source at `3db4ff4db75394852ce4714d0796c6e36022e7ae`, the SHA
in the approved worker release evidence. Its transactional inventory/order state
machine is reused, not replaced by an in-memory deduplication toy.

HTTP POST requires stable explicit `order_id` + items. It validates the v2 contract,
normalizes whitespace and conditionally commits the complete event to the DynamoDB
journal BEFORE 202. A retry with equal business content returns the original event
and timestamp; a conflicting body returns 409. A journal failure/uncertain response
returns 503, never a false 202. SQS availability is not needed for acceptance.

The journal has no TTL, deletion protection, encryption, PITR and hourly AWS Backup.
A sharded KEYS_ONLY GSI finds pending events without full-table dispatch scans.
Publishers claim using atomic conditions and expiring ownership tokens/epochs;
a stale owner cannot mark another owner's work SENT. Restart reclaims expired
leases. A send whose acknowledgement is lost remains recoverable.

SQS send + journal mark cannot be atomic. Publication is AT LEAST ONCE, not
exactly once. Unknown sends, expired owners or queue loss can cause re-delivery;
the original ID/body survives. The existing worker uses conditional checkpoints
and an atomic stock/order transaction, so retries do not reserve stock twice in
the tested lab processor. Payments/shipments in that processor are simulations;
real external providers would need their own durable idempotency protocol.

All writer components require a matching epoch and a renewed <=5-minute local
activation lease. These are pause/drain controls, NOT a cross-region global fence.
Primary writes must be independently fenced before actual recovery activation.
Default GitOps replicas remain zero and control objects remain paused.

## Cross-region reconciliation / replay

1. Obtain authoritative immutable backup references and independently verified
   quiescence/fencing; a paginated Scan is not a transactionally consistent global
   snapshot during writes. `export_journal.py` reads only Ohio active/staging tables,
   validates DR role, emits a private 0600 canonical snapshot and digest, logs counts
   only. Verify origin/VersionId/access control independently: SHA256 is integrity,
   not authentication and not proof of completeness.
2. Restore journal AND business/inventory/idempotency checkpoints from a compatible
   recovery cohort. `reconcile_journal.py` compares canonical journal hashes,
   accepted bodies/timestamps, independent ACK receipts and recovered business
   records. Conflicting content, missing receipts or unaccounted target orders block
   automated replay/promotion. Completed/rejected matching orders are excluded.
3. Independent receipt inventory must be explicitly complete to claim zero missing
   journal records. Equal backup hashes/counts alone do not prove no ACK was lost.
   A lagged regional copy reports unknown/unrecovered acknowledgements, never RPO 0.
4. The offline CLI writes replay plans containing sensitive order bodies only to
   a NEW mode-0600 file. Its stdout has aggregate counts, no orders or credentials.
   Actual execution requires recorded AWS release, a fresh fully validated recovery
   plan/fence epoch and scoped `cosmotech-dr-reconciler` Pod Identity role.
5. `apply_replay.py` conditionally imports all immutable receipts, including terminal
   orders, without overwriting target leases/state. Pending events are rescheduled
   only in a strictly newer epoch. Retrying the same epoch does not reset an active
   lease or requeue a newly published record. It never writes business tables or
   receives/deletes/purges the primary queue. Publishers and unchanged business
   checkpoints perform the subsequent at-least-once, idempotent processing.
6. Failback uses the secondary's fenced cumulative journal as the authoritative
   source, includes all new accepted IDs and compares it with the old primary
   recovery cohort. Reverse comparison can be done offline. No script here writes
   to us-east-1. Real primary reintegration needs a separate approved data-owner
   operation after consistency/fencing/E2E proof; no blind reverse DNS/snapshot.

Example offline preparation (file inputs must be private, verified evidence):

```
python scripts/dr/reconcile_journal.py --source source.json --target target.json \
  --business business.json --expected-business expected-business.json \
  --receipts receipts.json --receipt-inventory-complete \
  --epoch 2 --now-ms <reviewed-time-ms> --output private-replay.json
python scripts/dr/apply_replay.py --source source.json --report private-replay.json --epoch 2
```

The second command is PLAN ONLY. No `--execute` is used while the account is held.
The private source/target/receipt files are deliberately not committed as examples.

## Backup/restore automation

Existing isolated Terraform source-copy plan remains disabled. The NEW secondary
outbox additionally has PITR, explicit single-table backup selection and a scoped
regional backup role. No source Global Table, source PITR, source queue or source
backup settings are changed. Regional journal backups do NOT create synchronous
cross-region acceptance durability. Source journal rollout/replication is still
required before protecting primary ACKs against complete regional loss.

`backup_readiness` checks paginated destination vault inventory, COMPLETED status,
encryption and actual freshness for EVERY explicit expected component. A schedule
is not a completed backup and neither proves application consistency. Restore
compilation supports new Orders/journal tables and encrypted gp3 volumes in Ohio;
execution revalidates the whole batch and destination recovery point metadata
before any StartRestoreJob. Stable intent+role tokens protect bounded retries.
The CLI requires fresh release/budget evidence, non-root DR identity and reserves
an audit file before writes. A STARTING/partial audit or lost response must be
reconciled with AWS job IDs before retrying; do not assume indefinite idempotency
or create a new operation after the AWS token retention window.

Version-pinned ITSM/other persistent artifacts are downloaded from the private
secondary bucket, encryption/size/SHA256 checked, fsynced, mode 0600 and atomically
published without overwriting existing data. Archives are NEVER auto-extracted or
executed. Logical dump consistency and restored table/volume application checks
remain mandatory. No source snapshot/volume is detached/deleted.

## Metrics / chaos / cost

Runtime metrics expose publication backlog, oldest pending age, observed attempts,
SQS visible/inflight counts and observation time with bounded region labels (no IDs
or payloads). Gauges are not lifetime counters. Reconciliation metrics distinguish
unknown loss from verified receipt coverage; missing data is not zero. RTO/RPO
remain on the existing real-E2E evidence path, never synthesized from chaos models.
The dashboard and alerts cover backlog age, recovery backlog and missing receipts.
A lab-sized metrics Scan has read costs; production scale requires aggregate/index
telemetry rather than unbounded full-journal scraping.

Models cover SQS outage/ambiguous sends/queue loss, publisher/node restart,
AZ client isolation, full regional recovery vs lagged copies, corrupt backups and
business dependency failure. SDK/Moto tests cover persistence across fresh clients,
conditional claims, pagination, replay races, S3 versions/checksums and real worker
stock transactions. No real AWS AZ/region failure or destructive test is executed.
No local/model timing certifies AWS RTO/RPO.

The prior cost comparison is a pre-outbox scenario. New journal/GSI storage,
PITR, journal backups, requests and telemetry reads add variable cost. For the
previous Ohio quote, an illustrative 1 GiB combined table/index footprint adds
$0.25 storage + $0.20 PITR + $0.10 backup = $0.55/month, excluding requests/retention
churn and cross-region copies. This is NOT a new complete forecast; update the
full budget/volume/write/replication estimate before enabling any resources.

## Publication boundaries

CI builds/scans the image without AWS and saves a short-lived artifact. Optional
ECR publication is manual-dispatch-only, main-only and gated by BOTH a recorded AWS
release and repository release variable. The OIDC publisher can push only the new
Ohio DR repository. It never promotes the primary, syncs Argo or applies Terraform.
GitOps renderer requires a verified immutable DR digest and emits only passive
resources. Current legacy passive bundle is not silently replaced or activated.
The account-clearance flag remains FALSE and issue #47 stays open.

IAM analysis command (analysis only, never upload the raw wildcard output):

```
DISABLE_IAM_POLICY_AUTOPILOT_TELEMETRY=true uvx iam-policy-autopilot@0.4.0 generate-policies \
  src/disaster_recovery/dynamo_outbox.py src/disaster_recovery/runtime.py \
  src/disaster_recovery/backup.py --region us-east-2 --account 414053644145 \
  --pretty --service-hints dynamodb sqs s3 backup
```

The static tool misses injected Table write methods and over-suggests some dependent
KMS/replication/ObjectLambda permissions. The versioned advisory report records this
limitation; SDK tests exercise actual conditional writes. The Terraform API role has
only journal Put/Get and business Get, publisher only journal Query/Update + SQS send,
metrics only journal Scan + queue metadata, and a separate reconciler handles guarded
journal imports. Pod roles deny other regions. Effective IAM/Pod Identity remains
unvalidated until AWS release; no raw generated policy is attached now.

Online reconciliation metrics are emitted only from an authorized, fresh
`aws-verified-reconciliation` envelope matching the current journal fingerprint.
Offline/simulated, stale or changed-dataset reports cannot fill loss metrics with
zero. The metrics process can read a read-only report via DR_RECONCILIATION_REPORT;
without such evidence its reconciliation/loss panels deliberately remain unknown.
Use the optional online verification mode only after release/quiescence. Report
mounting/publication and live alert delivery remain part of AWS certification.

Before actual replay, the reconciler compares the ENTIRE restored business dataset
against the expected recovery cohort, including inventory and idempotency/checkpoint
rows. Lossless numeric/binary/map fingerprints detect stock changes even when order
bodies match. Execution rereads business/journal data and rejects stale/divergent
reports before any import. This is a consistency check, not a promise that separately
scheduled regional backups form an application-consistent cohort automatically.

## Primary rollout kit, intentionally NOT applied/deployed

`ops/dr/integrations/primary-outbox.patch` prepares the API hook and a separate
publisher against the approved API source SHA
`8cf95ca042ee90a8c5697603029d0bb8be4eca62`. The preparation tool only accepts a
pristine disposable checkout at that exact revision, applies the patch and vendors
the reviewed core with per-file SHA256s. CI exercises legacy behavior unchanged,
202-after-journal, conflict/503 handling, no SQS dependency for acceptance and
explicit IDs when opted in. No branch/commit/PR is published in the primary API
repository, so its automatic image promotion cannot run.

The feature flag defaults OFF. The kit includes `orders.producer.outbox_dispatcher`
for a separate primary publisher process with an explicit renewed source-region
activation file; it is not a hidden API-side synchronous send. The new
`orders-outbox-source` Terraform root defaults OFF, creates only a NEW protected
source journal and unattached least-scope policies, and never imports/modifies the
existing table/API/role/queue. The existing source backup root can explicitly
include the new source journal only after verified readiness. No role is attached
and no setting is enabled automatically.

After release: verify source ownership/effective Pod Identity permissions, deploy
new journal/backups, publish the reviewed kit through the existing primary GMUD
pipeline, stage the paused publisher, migrate clients to stable order IDs, then
activate both only with journal/queue/rollback probes. SQS failure should grow the
journal backlog without losing acknowledged orders. Rolling the flag back must
NOT abandon new pending journal records: stop new accepts/drain the durable backlog
or preserve the publisher until all durable records are reconciled. Never revert
to direct SQS acceptance and discard the journal during failback. None of this
primary rollout was executed while the account remains restricted.
