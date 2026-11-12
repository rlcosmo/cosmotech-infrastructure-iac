# Clean management recovery host — Ohio

Scope: one Ubuntu 24.04 x86_64 t3.large in the existing DR VPC and us-east-2b subnet,
with a fresh encrypted 40-GiB gp3 root disk. The Canonical-owned available AMI was
verified as ami-096d6538c463eb1db, owner 099720109477, image date 20261108. IMDSv2 is
required with hop limit one; containers must not obtain host credentials. T3 uses
standard CPU credits to avoid unlimited-credit charges. No key pair or inbound
security-group rule is created. Outbound HTTPS supplies SSM and package/image access.

A versioned launch template supplies the exact AMI, instance profile and encrypted
root mapping. Primary disks are never booted. The already restored ITSM and Zabbix
data copies attach only to this zonal host. Inspect partitions and mount read-only
with filesystem recovery disabled before copying logical dumps/configuration into
fresh runtime storage. Keep source credentials private and regenerate regional
credentials. Do not start copied cron, Kestra schedules, mail, Zabbix actions or
primary integration agents. Source volumes, snapshots, PVCs and workloads stay intact.

## Plan and IAM audit

The real plan contains nine creates: bounded EC2 role, instance profile, SSM policy
attachment, regional data-read inline policy, security group, launch template, clean
instance and two attachments. It has zero updates/replacements/deletions of existing
resources. The plan guard only admits new attachments for the two explicitly
recorded restored management copies in us-east-2b.

The existing authorized maintainer identity performs IAM bootstrap only; all AWS
resource provisioning/operations use assumed cosmotech-dr-provisioner. No root or
new IAM user/permanent credential is used. Added managed policies:

- arn:aws:iam::414053644145:policy/cosmotech-dr-provisioner-4
- arn:aws:iam::414053644145:policy/cosmotech-dr-provisioner-5

The host role is arn:aws:iam::414053644145:role/cosmotech-dr-itsm-recovery, trusted only
to EC2 and constrained by cosmotech-dr-runtime-boundary. Provisioner CreateRole
requires that boundary; PassRole permits only that role to EC2. RunInstances is
restricted to t3.large, the exact host instance profile, approved AMI, zonal subnet,
tagged recovery security group/template and lab tags. No ingress, disk modification
or destructive permission was added. The runtime boundary permits AWS-managed SSM
agent actions only for this host role, excluding Parameter Store secret reads.
Five real IAM simulations confirm: unbounded role creation denied; operator policy
mutation denied; boundary mutation denied; bounded recovery role creation allowed;
primary Terraform state write denied.

The baseline was generated from the real plan with the project's pinned installed
IAM Policy Autopilot 0.4.0 (no new tool installation). Reproducible equivalent:

```sh
DISABLE_IAM_POLICY_AUTOPILOT_TELEMETRY=true \
  iam-policy-autopilot generate-policies /private/ohio-management-plan.json \
  --region us-east-2 --account 414053644145 --resource-cutoff 100 --pretty
```

Generated provider-wide action supersets were reduced to the reviewed creates and
readbacks, exact host resources and existing AWS-managed SSM policy actions. No
AdministratorAccess or generic IAM mutation was accepted. Raw plans and credentials
remain private and are never committed.

## Cost and completion gates

Increment: USD 0.0832/hour compute plus public IPv4 (outbound connectivity only),
40-GiB gp3 USD 3.20/month and requests/transfer. Continuous operation would be about
USD 68/month under the documented pricing assumptions; a four-hour compute run is
USD 0.3328 before small proportional storage/IP/transfer costs. The full active
scenario is conservatively estimated USD 375/month including retained data copies;
the USD 400 Terraform ceiling is an internal guard, not an owner-provided budget.
Stop this host when the controlled exercise ends. Retain recoverable data/backups.

Provisioning, filesystem integrity, logical database restore, application health,
notification fencing and real regional failover/failback each need live evidence.
This plan alone does not establish any of those outcomes.
