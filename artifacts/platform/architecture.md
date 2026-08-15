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

Each serving request emits one redacted immutable operational record before a response is released.
That append has a fixed two-operation immutable-store policy: `put_once` followed by a verified
read-back of the returned pinned reference. The I/O runs in the serving framework's worker
threadpool so remote or filesystem latency cannot block the async request loop; an append or
verification failure fails the request closed with HTTP 503.
If a client disconnect cancels a request before it has a response, the service records a
`cancelled` terminal status with conventional operational status 499 when the append completes,
then propagates the cancellation; an audit failure must not replace that cancellation with a 503.
The service deliberately does not impose a response timeout on this synchronous immutable write:
racing a timeout against a non-cancellable write could leave a late record claiming completed/200
after a client received 503. A finite cancellation-latency bound requires a future cancellable or
transactional storage commit protocol; it is not approximated at the expense of truthful evidence.

Packaged source-provenance verification also fails closed. Its persisted policy/run diagnostic and
operator log use a bounded reason code such as `manifest_missing`, `manifest_schema_invalid`, or
`manifest_invalid_utf8`/`revision_invalid`/`source_digest_mismatch`; they never include a provenance file path or its
contents.
