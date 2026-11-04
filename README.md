# cosmotech-infrastructure-iac

Only this repository manages the existing S3 backend/key. No state was copied or migrated. ops/itsm, automation source and Lambda source are pinned bootstrap/build snapshots required by existing Terraform file() references; migrate these references to component artifacts before removing snapshots.

Extracted from [aws-serverless-lab](https://github.com/rlcosmo/aws-serverless-lab) at `6f3e67f9e93d626393b23b6884b031767a63fea0` with original commit ancestry preserved. Original files remain available there as migration snapshots. No infrastructure was recreated by extraction.
