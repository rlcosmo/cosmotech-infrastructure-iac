# Read-only repository audit

No source primary state was loaded or imported; no AWS mutation was made.

| Repository | Inspected main revision |
|---|---|
| cosmotech-gitops | `3ea5fad056bf23c92ffbdd1357e17f2270688b8c` |
| cosmotech-orders-api | `b0ba40ead29bfe6083203e25f09ac18b939f8868` |
| cosmotech-orders-worker | `330c51834e169eb5d6ad665573d29b3816cb9df3` |
| aws-eks-istio-lab | `b6f61512af11ecf105f2b1ed2df590ce74a9ea20` |

Infrastructure baseline: `f1930e9d45fddd49b35374f3970e967dcb4f429a`.

Primary code ownership and data limitations are described in architecture.md.
Orders API/Worker and source EKS repositories were read only. Changes are confined
on infrastructure to new DR paths/existing DR root/docs, and in GitOps to a new
`disaster-recovery/` directory and a validation-only workflow. No existing primary
manifest, delivery filter, IAM role, Terraform state or Power Manager file changes.

Regional metadata queries are recorded in regional-service-access.json; successful
read APIs are not proof that account restrictions are cleared or deployment will work.
