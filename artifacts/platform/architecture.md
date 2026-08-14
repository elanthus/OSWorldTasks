# Grounding evaluation platform architecture

The local control plane accepts only frozen experiment options and launches one fixed Metaflow.
Metaflow owns execution and resume boundaries. Provider responses are written to content-addressed,
version-pinned storage before the existing Day 3 parser and point-inside-box scorer run. MLflow
holds searchable run metadata, prompt versions, metrics, and artifact references; it is not the
approval or deployment authority.

SQLite is the local transactional control ledger. Approval, deployment, and rollback are separate
reviewer actions. Deployment history, approvals, and audit events are append-only; the active
deployment changes through a compare-and-swap generation. The serving API receives the exact
approved policy from that activation and discloses policy and deployment identity on every call.

The Compose demo uses PostgreSQL for MLflow metadata and versioned MinIO buckets for artifacts and
immutable response envelopes. Production should replace MinIO governance retention with an
approved S3 Object Lock compliance policy, backup, replication, access controls, and recovery
testing.
