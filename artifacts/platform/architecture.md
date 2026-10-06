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

Every failed deploy or rollback appends a `deployment.deploy_failed` or
`deployment.rollback_failed` audit event whose stage is `preactivation`, `transaction`, or
`runtime_activation`; failure events never move the active pointer. The runtime handoff after the
compare-and-swap is total by construction: all checks that could reject the prepared policy run
before the commit, and the handoff only installs the validated value. There is no automatic
compensating rollback. If the handoff still raises, the failure event names the committed
deployment, and serving-startup restore loads that deployment on the next start.

MLflow tags and the `champion` alias mirror the ledger. Each mirror write runs after its
authoritative transaction commits; a failed write appends `tracking.reconciliation_required` and
changes no control or serving state. Startup reconciliation derives the desired tags and alias from
the ledger alone and rewrites them idempotently. The full contract is in the
[deployment guide](../../deploy/README.md#control-ledger-mlflow-mirror-and-reconciliation).

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
Serving audit I/O uses a dedicated, bounded worker limiter rather than the shared request-worker
pool. S3 adapters configure finite connect/read operation deadlines; local adapters retain atomic
filesystem commits. A request remains fail-closed until its record is durably verified.

Packaged source-provenance verification also fails closed. Its persisted policy/run diagnostic and
operator log use a bounded reason code such as `manifest_missing`, `manifest_schema_invalid`, or
`manifest_invalid_utf8`/`revision_invalid`/`source_digest_mismatch`; they never include a provenance file path or its
contents.

Startup readiness contract: a persisted active-deployment pointer is not evidence that the policy
may serve. When the combined control/serving process starts, `DeploymentCoordinator.restore_active`
(`pixelgym/platform/deployment.py`) re-verifies the active candidate exactly as a deploy or rollback
does: approval evidence, manifest and renderer binding, gate-report digest, and every pinned
artifact. It then re-runs `CandidateServiceSmoke` against the frozen smoke fixture before loading
the policy into the traffic runtime. Any failure aborts application construction, so the process
never accepts traffic with an unverified policy. With no active deployment, the service starts
with no policy loaded.

Stateful-v5 policies (v5 serving stage S6) share the same ledger and active pointer. A
`stateful-v5` candidate needs a gate report from an approved v5 gate policy, human approval, and
append-only serving terms (deployment attempt cap and tier) for its exact version. Deploy,
rollback, and startup restore reverify those records and every pinned artifact, run a no-cost
fake-policy `/api/v2` episode in an isolated app, and load the candidate's policy into the
credential-free worker before the compare-and-swap. A kind-aware runtime then serves exactly one
kind: `/api/v1/ground` for a grounding deployment or `/api/v2/episodes` for a stateful one. The
caller owns the environment; the server validates each action against the PixelGym action
contract and never executes it.
