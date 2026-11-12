# Regional TLS and source delivery trust — 2026-11-12

## Regional certificate

The permanent operator requested a non-exportable RSA-2048 ACM wildcard certificate
in us-east-2, ARN arn:aws:acm:us-east-2:414053644145:certificate/8c0ea41a-9730-4d15-bc95-c1c74d13a160.
It reached ISSUED using the existing published CNAME; no DNS record was changed.
The exact request is now owned by the isolated DR Terraform state. The reviewed
plan created only that certificate; no healthy primary resource changed. IAM
Policy Autopilot derived the baseline from the actual plan; the applied policy
excludes certificate import, export, removal and unrelated-domain requests.

AWS documents reuse of DNS validation tokens across regions:
https://docs.aws.amazon.com/acm/latest/userguide/dns-validation.html

## Native release blocker

API PR #1 was merged after real CI tests/build/Trivy passed. Main publication
43266912085 published api-2.0.5-lab-24 and submitted native Kestra execution
35jOi0blLaOxNDJDWMEeqh. That execution failed at deterministic_policy with
runtime_evidence_unreliable; the application was not promoted or enabled.

Read-only diagnosis found the primary Prometheus freshness around 15 seconds and
zero firing critical alerts. Both Orders Argo applications retain Synced/Healthy
status but have ComparisonError: HEAD 04e75cc15c443d88a3e776374ddeeebf4ec37dde is
signed by GitHub key B5690EEEBB952194, outside the critical project signer scope.
This condition correctly blocks the policy. Do not add the broad GitHub merge key
to the critical Orders project or clear the error manually.

The native publisher checkout has cryptographically trusted baseline
5d4c01f9a5f5567c973f01c28f9102436ac9c086, signed by
00025CFAA41EEE11096FC58E6C140B9EE5CA76A6. Its approved Orders bundles, release proofs,
application policies and bootstrap project are byte-identical to current main.
Only independently reviewed metadata/DR/observability changes differ.

The bounded metadata checkpoint helper runs only on the existing publisher host,
under its existing lock and adapters. It verifies the baseline cryptographically,
requires an exact reviewed HEAD, rejects critical application/security paths,
creates a signed commit with the exact same tree, writes protected audit evidence
before push, and pushes fast-forward only. It cannot promote an image, change an
application, mint an approval, broaden trusted keys or rewrite history. A moved
HEAD, dirty checkout, signature failure, unexpected path or uncertain push aborts.
Private signing/App credentials remain on the native host and are never returned.

GitOps PR #29 pins the separate observability application to its already verified
GitHub-signed commit before a native-publisher checkpoint. This retains both
projects' original trust scopes; its eight alerts and dashboard bytes stay intact.
Checkpoint execution and the retry result must be recorded before declaring this
blocker resolved. No failover/failback/RTO/RPO is claimed by these preparations.

## Network cost preparation

The public Ohio Price List reports ALB USD 0.0225/hour, an IPv4 USD 0.005/hour and
one used LCU USD 0.008/hour. With two IPv4s and one LCU, a four-hour exercise is
USD 0.162; keeping that route for 730 hours is USD 29.565, plus variable transfer.
No ALB has been created by this TLS-only stage. Authoritative DNS is Cloudflare,
outside this Terraform backend. No connected DNS integration was found; its
execution mechanism remains an owner dependency for a real public DNS switch.
