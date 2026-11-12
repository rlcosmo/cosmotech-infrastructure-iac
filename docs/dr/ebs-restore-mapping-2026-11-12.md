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

An additional 50-GiB ITSM source volume contains freshly completed GLPI/Kestra logical dumps; its separate backup/copy is in progress and is not included in this first seven-volume batch. Logical integrity and mount/application compatibility will be tested after restore.
