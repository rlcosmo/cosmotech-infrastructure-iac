# V2-003: S3 normalized; Ohio activation remains incomplete

Validated against account `414053644145` on 2026-11-12 UTC. Primary recovery
(PR #60 and observability PR #10) was accepted as completed and not repeated.
This report records live reads and an actual cloud plan, not a DR certification.

## S3 and existing backend

STS returned the permanent `cosmotech-lab-operator` role. S3 HeadBucket,
GetBucketLocation and ListObjectsV2 succeeded **before** the IAM update in this
validation. No environment/profile endpoint overrides were configured. The bucket
belongs to us-east-1; probing it through the Ohio endpoint returned
PermanentRedirect (301), not NotSignedUp.

A bounded CloudTrail sample contains historical GetBucketLocation NotSignedUp
at 2026-11-11 22:38:01 UTC and success at 22:39:00 UTC. Sampling was truncated;
these timestamps establish an observed transition, not the exact account release
time or cause. No present account restriction was reproduced. Do not open an S3
activation case based only on those historical events.

The remaining GetObject 403 was an IAM omission, distinct from NotSignedUp.
Added GetObject/GetObjectVersion for only the configured dev/dr state objects,
plus scoped audit reads. A subsequent download of a pinned version succeeded.

| Backend attribute | Live evidence |
| --- | --- |
| Bucket | aws-serverless-lab-414053644145-tfstate-use1 |
| Primary key | aws-serverless-lab/dev/terraform.tfstate |
| Version | VkGP7NWfofaRRdt_Ks98IL6URxW1fx9z |
| Lineage / serial | d8f04c08-f16f-dbab-dd6d-52997f781bf2 / 107 |
| Last modified | 2026-11-09 15:10 UTC |
| Versioning / encryption | Enabled / AES256 |
| Existing configured DR key | aws-serverless-lab/dr/terraform.tfstate; 404 |

The primary object version/ETag was checked again and remained unchanged. No
migration, new backend, primary state write or replacement was performed. DR init
used the already versioned bucket/key configuration. Read-only plans used
`-lock=false`; a future apply must use normal locking. Raw state, plans, signed
headers and operator CIDR remain private outside Git.

## Capacity and actual plan

Ohio Standard On-Demand quota `L-1216C47A` is 5 vCPUs. No existing instances or
EKS clusters were returned. t3.large offerings exist in all three planned AZs;
EKS 1.36 is in standard support.

| Proposed topology | vCPUs | Fits current quota |
| --- | ---: | --- |
| Passive EKS with zero workers | 0 | Yes; control plane still costs money |
| Two t3.large workers | 4 | Yes; full application capacity unproven |
| Two workers plus optional t3.large management host | 6 | No |
| Previous three-worker maximum plus management host | 8 | No |

Changed the default maximum to two, added fail-closed observed quota/reserved
usage inputs, and selected MINIMAL node updates to avoid update surge. The CLI
preflight queries actual instance CPU metadata and conservatively reserves all
pending/running On-Demand usage before considering the entire proposed topology.
Existing usage may overlap a previously deployed DR topology; re-running the
preflight deliberately over-reserves rather than claiming unverified spare capacity.

The live Terraform plan contains **77 creates, zero updates/deletes and zero
primary changes**. The scope checker passed. The plan was also generated with the
new restricted provisioner role. It is not applied: destination backup/restore,
images, full-service capacity and application activation are not yet validated.
The private plan's USD 300 ceiling is an internal planning guard, **not** an
owner-supplied numeric monthly authorization.

Automatic requests for 8 and then 6 vCPUs were NOT_APPROVED without a Support
case. The owner subsequently authorized a technical Support case; an 8-vCPU
request was submitted with SupportCaseAllowed=true. Case **179445768400848** is
now CASE_CLOSED; GetServiceQuota still returns five. DescribeCases returned
SubscriptionRequiredException after a narrowly time-bounded temporary Support
permission propagated. That permission was physically removed. The detailed
technical context remains prepared in the companion document; it was not sent
as a separate case communication. Reviewing the closure rationale requires
Support Center access; no paid Support plan was enabled. No quota increase is assumed until GetServiceQuota
confirms it. AWS documents this flag in the
[RequestServiceQuotaIncrease API](https://docs.aws.amazon.com/servicequotas/2019-06-24/apireference/API_RequestServiceQuotaIncrease.html).

## IAM changes actually applied

Created `arn:aws:iam::414053644145:role/cosmotech-dr-provisioner`, trusting only
`arn:aws:iam::414053644145:role/cosmotech-lab-operator`. A chained one-hour STS
identity was verified. Root was not used; no permanent credentials were created.

The baseline was generated from the real plan with the project-pinned
`iam-policy-autopilot==0.4.0` in an isolated temporary environment, telemetry off.
Removed unrelated service actions, business data writes and generic IAM access;
scoped actions to Ohio and planned Lab names/tags. Three managed policies stay
below the individual policy size limit. IAM Access Analyzer returned no findings
for these policies, the operator update and runtime boundary.

CreateRole requires the bootstrap-owned
`cosmotech-dr-runtime-boundary`; all planned runtime roles carry that boundary.
The provisioner cannot change it, itself or the main operator. PassRole and
managed-policy attachment are allowlisted. Backend writes cover only the known
DR object and lock; only the lock can be deleted. Primary state has no write grant.
Authorization analysis and a successful plan do not prove every create/restore
path; denied operations must be corrected narrowly from actual evidence.

The existing initialized local IAM bootstrap state was reused. The applied IAM
change is separate from the unapplied Ohio infrastructure plan.

## Data dependencies and cost

Primary Backup inventory returned 31 COMPLETED points: 19 EC2 and 12 EBS. Ohio
returned no vaults. This is not evidence of a destination DynamoDB/outbox copy.
The source currently retains the legacy direct-SQS producer; the versioned
outbox integration kit has not been rolled out. Full lab recovery additionally
requires management data, Zabbix deployment, routing/TLS and fencing verification.
Do not run a failover against an empty or unverified destination.

Price List verified Ohio Linux shared t3.large at USD 0.0832/hour on 2026-11-12.
Using the versioned 2026-11-08 storage/control-plane assumptions and an outbox
allowance, indicative monthly totals are USD 117.60 passive and USD 278.15 with
two active workers. They exclude optional management host, variable requests,
restore bursts, surplus CPU credits, extra retained disks, external ingress and
taxes. No Ohio infrastructure spend was started in this validation; IAM has been
prepared. Update the complete quote before provisioning a larger architecture.

## Execution sequence after the quota decision

1. Confirm actual Ohio quota, reserve existing usage and validate full application
   resource requests; the optional host needs at least six vCPUs with two nodes.
2. Complete destination copy/restore identities, create the isolated destination
   vault/bucket through a reviewed plan, and copy known valid recovery points.
3. Test isolated DynamoDB/outbox and management restores; verify manifests,
   freshness, checksums, encryption keys and application-level reads.
4. Publish/mirror pinned ECR images, configure GitOps and implement the remaining
   full-service recovery components. Keep schedules, mail and automation fenced.
5. Regenerate the live plan with the scoped role and normal locking for apply;
   reject any primary replacement/deletion. Activate a controlled test endpoint.
6. Deploy/verify the durable journal and replication before claiming regional
   loss protection. Test real failover, message replay/idempotency, data integrity
   and failback. Record timestamps and measured RTO/RPO; keep them null until then.

Local tests cover configuration safety only. No real failover/failback occurred.

Validation: 124 local unit tests (2 skipped), including 55 DR tests, and 13
Terraform mock safety tests passed. Six live IAM policy simulations verified
primary-state/role/boundary protection and the required CreateRole boundary.
Terraform formatting and DR/bootstrap validation passed. These do not measure RTO/RPO.
