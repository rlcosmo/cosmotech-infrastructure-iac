# EBS restore mapping, before execution

Copied points verified COMPLETED, owned by 414053644145, encrypted, with KMS keys in Ohio. Target AZ us-east-2b has a live Ready worker. Source volumes/PVCs remain untouched. Prices use the versioned Ohio gp3 rate USD 0.08/GiB-month; allocated restored storage below totals 197 GiB / USD 15.76 monthly, within the documented active scenario. Backup/copy/API costs are additional. Management roots are data-only recovery copies; never boot primary images or activate copied schedules/agents.

| Component | Source volume | Copied Ohio snapshot | Source PV / PVC | Target AZ | GiB | USD/month |
| --- | --- | --- | --- | --- | ---: | ---: |
| alertmanager | vol-08248fc11ad49dbef | snap-01b48635ad655aaba | cosmotech-recovery-alertmanager-20261111-1b / observability/cosmotech-recovery-alertmanager-20261111 | us-east-2b | 2 | 0.16 |
| argocd_root | vol-00b73044d14b85491 | snap-05209fc777574ec03 | EC2 root; no PV/PVC | us-east-2b | 40 | 3.20 |
| grafana | vol-01a5598e02e7ffa77 | snap-012bfecb7accf6629 | cosmotech-recovery-grafana-20261111-1b / observability/cosmotech-recovery-grafana-20261111 | us-east-2b | 5 | 0.40 |
| loki | vol-08faf2b2dd32e18dd | snap-0c6f12ada2d4a7102 | cosmotech-recovery-loki-20261111-1b / loki/cosmotech-recovery-loki-20261111 | us-east-2b | 10 | 0.80 |
| prometheus | vol-099efa3ce5b06c0a9 | snap-0292583c01ccf8293 | cosmotech-recovery-prometheus-20261111-1b / observability/cosmotech-recovery-prometheus-20261111 | us-east-2b | 10 | 0.80 |
| tempo | vol-083afcbd83df50607 | snap-0b2b93b9cd47c39ac | cosmotech-recovery-tempo-20261111-1b / observability/cosmotech-recovery-tempo-20261111 | us-east-2b | 10 | 0.80 |
| zabbix_root | vol-030290eae0dc50960 | snap-0e89b80997e9a7349 | EC2 root; no PV/PVC | us-east-2b | 120 | 9.60 |

## ITSM extension, validated before restore

| Component | Source volume | Copied Ohio snapshot | Source PV / PVC | Target AZ | GiB | USD/month |
| --- | --- | --- | --- | --- | ---: | ---: |
| GLPI/Kestra logical dumps and data | vol-001ef2ffb9310ec4c | snap-09e3d2b614fee9b1b | EC2 root; no PV/PVC | us-east-2b | 50 | 4.00 |

Copy job 5a2cce71-f7d4-40bd-81a2-306b822b911a completed. DescribeSnapshots verified owner 414053644145, completed state, 50 GiB, encryption and the destination CMK before restore. Logical backup service completed successfully at 20261112T051322Z before the source EBS backup. The copied restore metadata still names the primary KMS key; explicitly replace it with the verified Ohio CMK and set the actual destination AZ. Restore as a data copy; do not boot the primary root or enable copied schedules/notifications. Eight retained data volumes total 247 GiB / USD 19.76 monthly, plus backups and any clean compute/root disks. Application-level logical integrity remains to be tested.


## Zabbix filesystem working copy, mapped before creation

| Purpose | Original primary volume | Preserved Ohio copy | Copied snapshot | Destination AZ | GiB | USD/month |
| --- | --- | --- | --- | --- | ---: | ---: |
| Replay ext4 journal and validate ClickHouse on an independent working disk | vol-030290eae0dc50960 | vol-08e83f8bac7a9f1e7 (read-only) | snap-0e89b80997e9a7349 | us-east-2b | 120 | 9.60 |

Read-only/norecovery inspection reported Structure needs cleaning on a few ClickHouse
files. This alone does not prove irrecoverable corruption: the crash-consistent ext4
journal has not been replayed. Preserve the first restored copy and the verified,
completed, account-owned encrypted snapshot. Create one tagged independent copy,
attach it only to clean host i-0ef16232880370903, and replay/validate there. Never boot
the copied operating system or copied automation. Native creation uses a fixed
idempotency token. Storage is proportional: about USD 0.053 for four hours, excluding
small requests/IO. No compute capacity increase is needed. The 22.5-GB ClickHouse data
will remain on this working disk rather than exhaust the clean 40-GiB host root.


The first working-copy request returned creating for vol-067cf978175892545, then
AWS removed the incomplete volume asynchronously. CloudTrail at 16:23:48Z confirms
kms:ReEncryptFrom AccessDenied for the permanent operator on the exact Ohio CMK.
The completed source snapshot and original read-only copy remain intact. Add only
ReEncryptFrom/To on that CMK via EC2 and retry with a new per-attempt idempotency
reference; require DescribeVolumes=available before attachment or ownership import.
