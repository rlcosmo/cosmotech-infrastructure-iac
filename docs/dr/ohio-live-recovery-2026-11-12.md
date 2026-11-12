# Ohio recovery evidence — 2026-11-12

This is an intermediate live recovery record, not a completed DR certificate.
The primary recovery from Infrastructure #60 and Observability #10 was preserved.

## AWS, backend and capacity

STS operates under the permanent lab operator / chained DR provisioner. S3
NotSignedUp is no longer reproduced. Existing primary state/version/lineage were
validated without migration or replacement. Ohio uses the already versioned DR
backend key in the existing us-east-1 bucket. IAM/OIDC bootstrap uses its separate
versioned namespace. No source state was overwritten. The Terraform backend must
not be required for operational activation during a real primary-region outage.

Service Quotas reports 16 Standard On-Demand vCPUs in Ohio after AWS approval.
Two t3.large workers (four vCPUs) are Ready in us-east-2a and us-east-2b. A separate
one-node maximum stateful group guarantees the actual recovery AZ when scaling
from zero; the general group now has a one-node maximum. The quota guard includes
both groups, optional clean host and reserved usage. There is no cluster autoscaler.

## Data protection and proof

The restored Orders table has deletion protection and seven-day PITR. A consistent
scan compared 3,418 source and 3,418 restored records, including stock/idempotency
rows, with zero missing, extra or changed records. Aggregate SHA256:
`7060fd7c04203bffc868ce68e6074854de4c1c401f8fc1141e17356d9c62d6c2`.
This is a point-in-time integrity result, not ongoing replication or measured RPO.

Eight real EBS restore jobs completed in Ohio: five observability copies, Argo and
Zabbix data roots, and the 50-GiB ITSM root containing verified completed GLPI/Kestra
logical dumps. See the pre-execution snapshot/source/PV/AZ/cost mapping. All copies
were checked against their completed account-owned snapshot, exact Ohio CMK and
us-east-2b placement. The ITSM copy is vol-0184f553ed58cf951 from
snap-09e3d2b614fee9b1b (restore 91dc73ec-e0da-4b64-8912-3bd0b1da0bea).
Five original CSI copies have Bound PV/PVC pairs with Retain and truthful affinity.
Read-only filesystem probes succeeded; a RAM copy of Grafana SQLite passed
integrity_check. Management roots are data copies, not booted primary machines.

Terraform imports own the restored volumes; prevent_destroy preserves them.
Provider 6.62.0 attempted ModifyVolume while reconciling its imported final_snapshot
flag, despite no size/IOPS change. IAM denied the calls. The flag is now ignored;
no ModifyVolume permission was granted. Subsequent plans permit metadata-only
updates, with explicit tests rejecting resize/replacement/foreign resources.
Recovery protection is prevent_destroy plus verified backups, not final_snapshot.

## Ohio platform and telemetry

Pinned chart archives were SHA256 checked before rendering. Runtime image versions
match the recovered primary databases where applicable: Grafana 13.3.0,
Prometheus 3.15.1, Loki 3.7.9 and Tempo 3.1.1. All are private ClusterIP services.
Argo CD, Istio, Prometheus, Alertmanager, Loki, Tempo and OpenTelemetry run in Ohio.

Grafana's restored administrator password was rotated via stdin, kept outside Git
and logs. Database health is OK. Authenticated search returns 37 dashboards and two
folders; anonymous search returns HTTP 401. Four original data-source UIDs remain.
Prometheus/Loki/Tempo health checks pass. Grafana's Alertmanager plugin health route
returns Plugin unavailable; Alertmanager's actual API still requires separate
validation, so four healthy sources are not claimed.

Real OTLP traces and logs returned HTTP 200 and were then read from Tempo and Loki:
trace `c16ddb8fbcf936efec63c92794cae2e2`, marker
`cosmotech-dr-otlp-e6f96b96af61`. The isolated validation Job completed successfully.
Prometheus has 35 groups / 250 rules, all health=ok. Both nodes report Ready=1,
MemoryPressure=0, with approximately 6.3 / 5.6 GiB available at observation time.
The Kubernetes Metrics API is not installed; these are real Prometheus samples.

GitOps #26 merged as e8680748d79d6e7860171fae952a90a37fc997d8 after CI passed.
It versions the five exact PVs/PVCs, dashboard ConfigMaps, eight Helm applications
and paused Orders/outbox runtime with an immutable published image. Projects target
only the local Ohio cluster; sync is manual, pruning disabled. Storage and dashboards
are already Synced/Healthy. Orders is synced at zero replicas; its WFFC evidence
claim waits for a consumer. Platform adoption is in progress. Istio's default HPAs
are being disabled in favor of fixed replicas appropriate to the reviewed topology.

## Costs and remaining certification gates

Live Ohio prices checked: t3.large Linux shared USD 0.0832/hour and gp3
USD 0.08/GiB-month. Eight retained data copies total 247 GiB / USD 19.76 monthly.
Running two workers costs USD 0.1664/hour, plus EKS, node root disks, snapshots,
logs, transfers and API requests. A terminated worker retained a 20-GiB root disk;
include it in the final inventory before any safe cleanup. Existing passive/active
scenario estimates USD 117.60 / 278.15 monthly are assumptions, not an actual bill;
retained recovery copies and any clean management host change those totals. The
USD 300 Terraform guard is internal and is not a numerical budget supplied by the
owner. No extraordinary spend or public load balancer was introduced.

Remaining: finish GitOps adoption and application-level checks, recover GLPI/Kestra/
Zabbix safely, provision the source durable journal and continuous backup/copy,
validate stable client IDs and idempotency, fence writers, synchronize receipts/data,
activate real regional routing, execute real failover/failback and measure RTO/RPO.
No simulated test or marker-ingestion timing is reported as regional RTO/RPO.
