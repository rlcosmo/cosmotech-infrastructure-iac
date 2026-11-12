# Technical context for the authorized Ohio quota case

Subject: Standard On-Demand vCPU quota for recovered personal DR Lab, us-east-2

Account: 414053644145. Quota: EC2 L-1216C47A, us-east-2.
Current value: 5. Requested: 8; minimum target for the reviewed topology: 6.

AWS confirmed removal of the account's temporary hold (prior case
179418083900558). Current STS and regional read APIs work. S3 backend requests now
succeed; historical NotSignedUp is no longer reproducible. This request does not
assert that the account remains suspended.

The active-passive CosmoTech Lab uses an isolated Ohio EKS standby with zero
worker nodes when passive. Activation needs two t3.large workers (4 vCPUs), plus
one optional t3.large management recovery host (2 vCPUs). Eight vCPUs provide
limited activation headroom; the current five cannot run that six-vCPU topology.
No existing Ohio EC2 instances/EKS clusters were observed. No Ohio cluster was
provisioned merely to test permission; the primary region remains untouched.

Requests without automatic Support case creation:
- 8 vCPUs: 86fbc06d37884306a4eef095dfff4ce19UHurkGO, NOT_APPROVED.
- 6 vCPUs: 1f2f6bbec17c47f180557ba330534d7e2LWElFnT, NOT_APPROVED.

Owner authorized opening a technical case. Request submitted with
SupportCaseAllowed=true: 232a009f82d94d50856e96c9b050d420F6pqNZpo.
AWS case: **179445768400848**, status CASE_OPENED. The quota request opened the
case; this detailed context is prepared, not separately transmitted.

Please review the requested regional quota and indicate whether any remaining
account eligibility restriction requires action. We will retain zero standby
workers and scale only for controlled recovery tests. This document contains no
credentials, Terraform state, secrets or signed request headers.
