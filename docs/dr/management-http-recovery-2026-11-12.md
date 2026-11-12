# Management HTTP recovery — 2026-11-12

Verified using permanent assumed cosmotech-lab-operator in account 414053644145.
The operator now has the existing narrowly scoped SSM policy for clean Ohio host
i-0ef16232880370903. The authorized maintenance principal performs IAM bootstrap
only; application/data operations use the permanent operator. No root was used.

## Actual failure and correction

The pinned Docker 29.1.3 containers were serving internally, but their sole network
was internal=true. Inspect showed requested loopback PortBindings with actual
NetworkSettings.Ports=null; the host had no 8080/8081/8082 listeners. Internal HTTP
requests to GLPI and both Kestra container IPs returned 200. Database restore was
not repeated. A GLPI PHP mysqli check from inside the app connected successfully.

A dedicated Nginx service now proxies only 127.0.0.1:8080/8081/8082 to the private
container addresses. It preserves the isolated application network, forwards the
request host/protocol, supports websocket upgrade and disables access logging.
No public listener, AWS inbound rule or additional compute was introduced. Its
renderer resolves the exact DR container network addresses when the service starts.

The pinned GLPI release handles CSRF without hidden tokens, confirmed in its served
public JS. The initial validator assumed the older token field and was corrected
to submit the real login form with the same cookie jar and Origin/Referer. A second
failure was a stale bootstrap admin password in the old runtime archive. The
existing restricted technical identity preserved with the same recovery cohort
successfully authenticated; no user password, profile or approval right changed.

## Live proof

SSM command 9d55a175-6d05-4907-b1d6-76bda5099da8 returned Success:

| Service | Frontend HTTP | Authenticated check | Anonymous protected API | Recovered flows |
| --- | ---: | --- | ---: | ---: |
| GLPI | 200 | Login succeeded, authenticated central page | n/a | n/a |
| Kestra | 200 | Flow API 200 | 401 | 17 |
| Kestra automation | 200 | Flow API 200 | 401 | 10 |

GLPI has 447 restored tables; each Kestra database has 25. Copied GLPI cron jobs are
disabled (active count zero). Only Kestra webserver roles run; no scheduler,
executor, worker or Docker socket was activated. Source application encryption keys
and user authentication records were recovered securely; regional database
credentials are fresh and remain in root-only files on encrypted storage.

Ohio Kubernetes pods remained Running. Inventory found 22 relevant Service
EndpointSlices with ready addresses and zero empty sets. In-cluster DNS resolved
Grafana, Tempo, Loki, Istiod and Argo CD to their expected ClusterIPs. There are no
public DR Ingress objects yet: these are private recovery readiness checks, not a
completed public regional failover or a measured RTO/RPO.

## Zabbix gate

The preserved 120-GiB Zabbix copy is mounted kernel read-only with norecovery.
The logical SQL gzip is readable (130,091,369 uncompressed bytes), but a few
ClickHouse directory entries returned Structure needs cleaning. Journal replay
must be tested on an independent encrypted snapshot copy, retaining the first copy
and snapshot. See the pre-creation mapping and proportional USD 9.60/month storage
estimate. The first incomplete clone was automatically removed by AWS after KMS
ReEncryptFrom authorization failed; CloudTrail identified the exact permission.
Only ReEncryptFrom/To on the existing Ohio CMK via EC2 was added before retry.
That filesystem gate passed on the independent working copy; application checks below subsequently passed.

The 79 approved Outbox tests were not repeated for this frontend repair. Full
replication certification, public routing, actual failover/failback and real RTO/RPO
remain subsequent steps. Existing primary services and Power Manager were preserved.

The working retry vol-03d3f51aca62b1d7a reached available in us-east-2b and attached
to the clean host as /dev/sdh. SSM b90b06ac-793a-4ea0-9d80-bc25ad440096 confirms:
offline ext4 check passed, 22,488,293,269 ClickHouse directory bytes readable,
zero directory read errors, and both first restored management copies still kernel
read-only. Journal replay and filesystem checking ran only on the independent copy.
The copied operating system was never booted. Zabbix logical/runtime recovery now
uses that verified working disk while the original copies and snapshot are retained.

To honor the permanent-operator execution path for subsequent DR Terraform work,
the existing scoped DR policies are attached directly to cosmotech-lab-operator.
These are the same permissions already delegated through cosmotech-dr-provisioner;
there is no AdministratorAccess or generic IAM grant. Role creation remains limited
to the fixed DR role allowlist and mandatory runtime boundary. The primary backend
namespace is still read-only, and operator/boundary self-modification stays denied.


## Zabbix application and integrity proof

SSM 190b29dd-a0f7-4048-8f93-4f3309d91ce2 completed successfully at
2026-11-12T17:01:18Z. The private loopback frontend returned HTTP 200, the restored
identity authenticated to Zabbix API 8.0.0, and anonymous host data was denied.
All four required events 118–121 were returned with their recovery-event references.
The SQL restore contains 237 events, matching the source dump. Copied actions,
media notifications and active hosts are disabled on this DR copy; source settings
were not changed.

ClickHouse authenticated with its fresh regional credential. CHECK TABLE returned
successful per-part checks for all 15 active fragments across six tables (one table
is empty). Active parts contain 52,473 rows and 1,343,371 bytes; this is distinct
from the 22.49-GB filesystem directory inventory, which also includes ClickHouse internal system
logs, inactive parts and metadata. TTL merges are paused to preserve recovered telemetry during
certification. The validator understands the actual per-part JSON result rather
than the older single-value CHECK TABLE response. Successful checks are cached
only while the active-part set remains identical.

The Zabbix frontend uses the same private host proxy on 127.0.0.1:8088. HTTP and
authentication success through this private path does not establish public DNS/TLS
failover. Both original management recovery volumes remain kernel read-only.

The imported working EBS volume and attachment are now owned by the isolated Ohio
state. A refreshed real Terraform plan has zero managed-resource changes: no
primary change, disk replacement or deletion is proposed.


A follow-up read-only part inventory (SSM 8fb4a346-1a06-4a6a-a26a-28e0bc4ca75a)
explains the directory size: a recovered internal system.trace_log_0 alone uses
20,690,191,488 bytes. Some internal system-log fragments were detached as
broken-on-start after crash recovery; none of the six application tables has a
detached fragment. Application checksum success therefore does not claim that all
internal ClickHouse diagnostic logs are intact. Original snapshots/copies retain
the recoverable evidence; no detached-part deletion was performed.

The dedicated source publisher Pod Identity association a-8tbqpyq3jqp4gdd6v now
exists for namespace orders-api and service account orders-outbox-publisher.
Its bounded role is cosmotech-source-outbox-publisher. The API deployment remained
2/2 available; this association does not start dispatch or enable durable acceptance.
At 17:12 UTC, hourly source backup metadata showed 40 completed backups and 40
completed Ohio copies since 13:00 UTC, with ten newer jobs still CREATED and no
failed/aborted/expired jobs in that query. This proves scheduled copy progress,
not a zero-RPO application-consistent failover cohort.

Read-only routing discovery confirms Cloudflare authoritative DNS, an issued
wildcard ACM certificate in us-east-1, and no certificate in us-east-2. Scoped
operator metadata reads now cover ACM/ELB in those two regions only; no DNS write,
load balancer mutation or certificate request is included in that discovery grant.
