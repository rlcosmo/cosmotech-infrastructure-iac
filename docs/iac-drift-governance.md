# V2-007 — evidence-based drift governance

Implemented in the existing automatic Git IaC → refreshed Terraform → AWS metadata
observer and portal. No extra user evidence file or application CI is required.
No resources are deleted, imported, retagged, scaled or modified by the observer.

| Management | Evidence | Result |
| --- | --- | --- |
| Terraform | Evaluated canonical configuration + physical ID/ARN in refreshed state | in_sync, missing, different or extra |
| AWS-managed compute | EKS node group → actual ASG relationship, ASG instance membership; same region | derived, visible, not independent drift |
| AWS-managed backup | Reserved AWS DLM/Backup system ownership tag on snapshot | retained artifact, visible, excluded from Terraform drift |
| GitOps storage | Canonical Application destination + main revision + plain PVC manifest + exact Bound PV/PVC UID and EBS handle | GitOps-owned, visible |
| Unknown CSI/PVC | CSI/PVC marker without verified physical binding | needs_review; never falsely compliant or orphaned |
| Unmanaged | No established owner in evaluated sources | extra candidate for review |
| Orphan candidate | Unmanaged available/unattached EBS + successful regional EBS inventory | candidate only; retention/backup/Kubernetes review required |

Explicit Terraform ownership takes precedence, including Terraform-managed snapshots.
An extra EKS node group absent from Git remains a real extra; deriving its instances
never hides the extra group. Parents are scoped to their region. Names and arbitrary
Name tags alone establish no ownership. Manual snapshots remain reviewable extras;
a missing source volume does not imply an orphan backup.

The GitOps collector reads the existing canonical repository automatically. It does
not execute repository code, Helm plugins, generators or remote Kustomize bases.
Only plain PVC manifests with a resolvable Argo Application destination are eligible.
Helm/Kustomize templates and StatefulSet-generated claims remain unverified in this
phase, rather than guessed. The Kubernetes adapter uses the observer's temporary
role, a short-lived cluster-bound IAM token, the EKS certificate authority and GETs
for paginated PV/PVC lists. Missing eks:DescribeCluster or RBAC list access records
unavailable coverage; no fallback credentials, IAM escalation or RBAC mutation.

Portal controls filter region, management, unresolved ownership and orphan
candidates, and show the reason for each classification. Existing schema version 2
remains readable. Arbitrary AWS tags, descriptions, payloads and tokens are not
copied to the public comparison report. Coverage is still explicitly incomplete.

## Validation and rollout

106 Python tests pass locally with the pinned SDK, including real token presigning
against fake credentials and mocked TLS/HTTP. The scoped governance CI installs
pinned boto3/botocore/PyYAML, runs the full suite, builds the code-only publication
bundle and syntax-checks portal JavaScript. It holds no AWS credentials.

Publication uses the existing `Lab V2 scoped publication` workflow on main:
`phase=plan`, inspect its existing scope guard, then `phase=deploy` only if successful.
Merging code is not evidence of deployment or a fresh AWS inventory. Live ownership
classification remains pending successful publication, observer read permissions
and an authenticated portal refresh. Power Manager is outside this change.

The DR attempt is recorded in
[provisioning-attempt-2026-11-09.json](dr/provisioning-attempt-2026-11-09.json).
STS failed with an expired session before cloud planning/apply. No Support query,
AWS write, secondary provisioning or destructive rollback was performed. Current
account restriction status is unknown. Automatic DR attempts remain disabled;
issue #47 stays open. V2-002 is not declared complete or started by this change.

## Sources

- [Managed node groups and their Auto Scaling groups](https://docs.aws.amazon.com/eks/latest/userguide/managed-node-groups.html).
- [DLM system tags](https://docs.aws.amazon.com/ebs/latest/userguide/dlm-elements.html).
- [CSI tagging contract](https://github.com/kubernetes-sigs/aws-ebs-csi-driver/blob/master/docs/tagging.md).
