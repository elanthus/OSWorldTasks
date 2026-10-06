# Local platform stack

This stack is for the no-cost scripted lifecycle demo. It starts PostgreSQL-backed MLflow,
versioned MinIO buckets, the Metaflow-capable control plane, and the versioned serving API. The
public values in `.env.example` are local demo credentials, not production secrets.

From the repository root:

```bash
python3.12 scripts/platform_compose.py up --build --wait
```

The wrapper derives a source-provenance manifest from the current Git checkout and binds it to the
exact files packaged in the image. A gate-eligible run therefore requires a clean, verifiable
checkout and matching packaged source; do not paste a revision into an environment file. Dirty,
missing, malformed, or mismatched provenance is explicitly recorded and fails a policy with
`dirty_code_allowed: false`. Local non-gate experiments can use a policy with
`dirty_code_allowed: true`; that fallback remains visibly dirty/unverifiable in run evidence.
When provenance cannot be verified, the run evidence, control plane, and operator log record one
safe reason code (for example `manifest_missing`, `manifest_malformed_json`, or
`manifest_invalid_utf8`/`revision_invalid`/`source_digest_mismatch`) without exposing the manifest contents or local
filesystem paths.
Do not invoke `docker compose` against `deploy/compose.yaml` directly: Docker can create a
directory at the file bind-mount path, allowing a stack to start without verifiable source
provenance. The wrapper refuses that condition before an `up` command reaches Docker.
Open the control plane at <http://localhost:5800> and MLflow at <http://localhost:5500>. Both host
ports are configurable in `.env.example`; the PostgreSQL and MinIO API loopback ports are also
configurable for isolated integration runs.

Both UIs are local-demo surfaces. The control plane has CSRF protection but no caller
authentication, and neither UI should be exposed or proxied onto a shared network. Before any
shared deployment, add authentication and authorization in front of both the control plane and
MLflow.

### Control-plane session cookie

The control-plane bootstrap derives the `pixelgym_session` cookie's `Secure` attribute from its
deployment exposure. `localhost` and any IP address for which Python's `ipaddress` module reports
`is_loopback` (including the full `127.0.0.0/8` range, `::1`, bracketed IPv6, and IPv4-mapped IPv6
loopback) default to `Secure` off; every other address defaults to `Secure` on. Keep the deployment
loopback-only when using plain HTTP; any non-loopback deployment must terminate TLS before sending
this cookie. A loopback-bound app behind a TLS-terminating proxy must set
`PIXELGYM_SESSION_COOKIE_SECURE=true`.

`PIXELGYM_BIND_ADDRESS` is the single source of truth for both Uvicorn's listening address and the
bootstrap exposure decision, and defaults to `127.0.0.1`. The image entrypoint disables proxy-header
trust, and bootstrap rejects `FORWARDED_ALLOW_IPS`; the control plane resolves client addresses from
the connection rather than forwarded headers. `PIXELGYM_LOOPBACK_ONLY_DEPLOYMENT` accepts only
case-insensitive `true` or `false` and is unset by default. Setting it to `true` is an explicit
operator attestation that a non-loopback-bound process is published only on host loopback, as in
the Compose demo; bootstrap logs a warning because the app cannot verify that publishing rule.
Cookie security precedence is: an explicit `session_cookie_secure` argument, then
`PIXELGYM_SESSION_COOKIE_SECURE` (`true` or `false`, case-insensitive), then the loopback-only
attestation, then the bind-address default. Environment values are validated even when a
higher-precedence setting wins.

The cookie is always server-issued and has the exact format `<session-id>.<tag>`. `session-id` is
the 32-character URL-safe Base64 output of `secrets.token_urlsafe(24)`. `tag` is the 64-character
lowercase hexadecimal HMAC-SHA256 digest whose key is the UTF-8 encoded `PIXELGYM_CSRF_SECRET` and
whose message is the ASCII bytes `pixelgym-session-v1\0<session-id>` (where `\0` is one NUL
byte). The server retains a presented cookie only when its shape and tag verify; otherwise it
replaces it with a fresh value. No session table is used. The remaining attributes are always
`HttpOnly` and `SameSite=Strict`.

### Reviewer attribution

Every control-plane mutation resolves its reviewer identity once from the connection context. A
configured header, `X-Forwarded-User` by default (override with `PIXELGYM_PRINCIPAL_HEADER`), is
accepted only when the connection peer belongs to `PIXELGYM_TRUSTED_PROXY_ADDRESSES`, a
comma-separated IP/CIDR allowlist. Default routes are rejected. Duplicate, malformed, missing, or
reserved identities from a trusted proxy are rejected before mutation, and submitted form or JSON
fields cannot select the actor. Uvicorn is started with `--no-proxy-headers`; bootstrap also refuses
`FORWARDED_ALLOW_IPS`, so forwarded client-address headers do not participate in this decision.

When `resolve_deployment_exposure()` treats the deployment as loopback—because the bind address is
loopback or `PIXELGYM_LOOPBACK_ONLY_DEPLOYMENT=true` attests that the container port is published
only on host loopback—requests not arriving through an allowlisted identity proxy use the fixed
`synthetic-demo` actor. Without that attestation, a non-loopback bind requires a nonempty trusted
proxy allowlist or startup fails. Compose keeps the platform port published on `127.0.0.1` and sets
the attestation because the process itself listens on the container interface.

New audit events record `actor_verification_source` in `details_json` as `proxy_header`,
`synthetic_demo`, or an internal source. Append-only rows created before this change are not
rewritten; rows without the field remain readable and the UI labels them `legacy/unverified`.

Stop the stack without deleting evidence:

```bash
python3.12 scripts/platform_compose.py down
```

To remove local demo volumes, the human operator must explicitly add `--volumes`. That operation
deletes local evidence and is intentionally not part of the normal workflow.

## Test-suite boundaries

The ordinary fast path remains browser-free, Docker-free, network-free, and independent of the
optional platform services:

```bash
.venv/bin/python -m pytest -q tests/unit
```

The real local Metaflow runtime and MLflow adapter suite requires the optional platform extra. It
uses temporary local state and real `run`/`resume` commands, but it does not provision Compose:

```bash
.venv/bin/pip install -e ".[dev,platform]"
.venv/bin/python -m pytest -q -m platform_integration tests/integration/platform/test_metaflow_runtime.py tests/integration/platform/test_mlflow_tracking.py
```

The manually dispatched [platform workflow](../.github/workflows/platform-integration.yml) runs
this Metaflow/MLflow command only. It does not run the fresh-stack Docker/Playwright lifecycle
suite below.

The fresh-stack suite additionally requires a running Docker daemon and Playwright Chromium. It
creates a unique Compose project, binds dynamically selected loopback ports, uses new project-scoped
PostgreSQL/MinIO/control volumes, and deletes only that project's containers, volumes, and locally
built images in fixture cleanup. It does not touch a long-lived `pixelgym-platform` project. Allow
up to ten minutes for a cold image build and lifecycle run:

```bash
.venv/bin/pip install -e ".[dev,platform]"
.venv/bin/python -m playwright install chromium
PIXELGYM_RUN_COMPOSE_TESTS=1 .venv/bin/python -m pytest -q -m platform_compose_integration tests/integration/platform/test_compose_lifecycle.py
```

The Compose assertion exercises PostgreSQL as MLflow's metadata backend. The authoritative
approval/deployment ledger remains the explicit SQLite control store implemented by
`ControlStore`; its supported legacy migration is rehearsed on the isolated `control-data` volume.
There is no PostgreSQL control-ledger schema to migrate. Diagnostics redact repository/home paths
and local demo credentials before pytest can print them.

SQLite is the local-first control-ledger choice, not a multi-host high-availability design.
File-backed stores, including `file:` URI paths, use WAL with `synchronous=NORMAL`, foreign-key
enforcement, and a bounded busy timeout (5 seconds by default, configurable with
`PIXELGYM_SQLITE_BUSY_TIMEOUT_MS`). Pure `:memory:` and URI memory databases use SQLite's `memory`
journal instead of inapplicable WAL, while retaining the same synchronization, foreign-key, and
busy-timeout settings. Exhausted lock waits return a retryable, redacted service error.
With `synchronous=NORMAL` under WAL, an OS crash or power loss can lose a committed control-ledger
transaction (never corrupt it), so post-commit mirroring to MLflow may run ahead of the ledger.

The Metaflow suite also sends `SIGKILL` to the entire local flow process group after durable
evidence persistence and resumes the recorded origin run. It proves no duplicate fixture-provider
billing in that bounded local runtime. A remote scheduler that loses its parent independently can
still leave an orphan run; production use needs a separate orphan-run reconciler and this test does
not claim otherwise.

## Control ledger, MLflow mirror, and reconciliation

The SQLite control store is the only authority for candidate state, approvals, deployments, the
active pointer, and audit events. MLflow model-version tags and the `champion` alias are a mirror
of that ledger for discovery. MLflow never drives a transition, and nothing reads MLflow to decide
what to approve, deploy, or serve.

**Mirror writes.** Each mirror write runs only after its authoritative transaction commits:

| Ledger transition | MLflow write | Operation name on failure |
| --- | --- | --- |
| Approval commits | `gate_status` and `approval_status` tags on the policy's model version | `mirror_approval_tags` |
| Deploy or rollback commits and the runtime handoff completes | `champion` alias to the active policy's model version | `set_champion_alias` |
| Serving startup restores the active deployment | `champion` alias | `set_champion_alias` |

A failed mirror write never fails the transition and never changes candidate, deployment, or
serving state. It appends a `tracking.reconciliation_required` audit event with the subject, the
operation name, and a fixed error string (`TrackingMirrorError: failed to mirror ...`); no
credentials or MLflow response bodies are stored. A tracking client that cannot be created at
startup is recorded the same way with operation `initialize_tracking`.

**Reconciliation.** When `MLFLOW_TRACKING_URI` is set, startup runs
`DeploymentCoordinator.reconcile_tracking()` on a background thread. It derives the desired mirror
from the ledger alone: for every candidate, `gate_status` is `eligible` or `failed` and
`approval_status` is `approved` or `pending`; the `champion` alias names the active deployment's
policy. The writes are idempotent, so a repeated run converges to the same state and creates no
approval or deployment. Each write that fails appends `tracking.reconciliation_required`
(`reconcile_candidate_status` or `reconcile_champion_alias`). A run with no failures appends one
`tracking.reconciliation_resolved` event (`reconcile_lifecycle_mirror`). An unexpected error in
the reconciler is recorded as `startup_reconciliation`.

Mirror state is therefore stale in two visible cases: between a failed write and the next
successful reconciliation, and after an OS crash loses a committed WAL transaction that MLflow had
already mirrored (see above). In both cases the ledger wins. The deployment page's audit timeline
shows the `tracking.*` events.

### Failed deploy and rollback events

Every failed deploy or rollback by a verified reviewer appends one `deployment.deploy_failed` or
`deployment.rollback_failed` audit event. Its `details.stage` says where the attempt stopped:

| Stage | What failed | Ledger effect |
| --- | --- | --- |
| `preactivation` | Stale form precondition, approval or evidence verification, renderer binding, artifact verification, load, smoke, or runtime-handoff validation | None; traffic unchanged |
| `transaction` | The compare-and-swap transaction (lost race, contention, rejected target) | None; the transaction rolled back |
| `runtime_activation` | The serving-runtime handoff after the compare-and-swap committed | The new deployment is active in the ledger; `details.committed_deployment_id` names it |

The event also records the actor, reason, target candidate, the expected deployment and generation
used for the compare-and-swap, and the exception class. Only control-plane exception messages are
copied into `details.error_message`; for any other exception it is `null`. A failure event never
changes candidates, deployments, or the active pointer. An unverified actor is rejected before any
verification work and leaves no row. If the failure event itself cannot be written, the original
error is still raised, with a note.

### Runtime handoff after the compare-and-swap

The post-commit runtime handoff is total by construction. Every check that could reject a prepared
candidate runs before the compare-and-swap: load and smoke, then `validate_activation`, which in the
assembled app requires a `LoadedPolicy` that `PolicyRuntime.check_activatable` accepts. After the
commit, the handoff only sets the committed deployment ID on that already-validated frozen value and
installs it. The platform does not run an automatic compensating rollback, because automatic
rollback is out of scope and compensation would need its own post-commit handoff that could fail
the same way.

If the handoff raises anyway (for example, an interpreter-level error), the coordinator appends a
`runtime_activation` failure event and re-raises. The ledger is not rewritten, the champion alias
is not mirrored for that attempt, and the runtime keeps serving the previous policy until the
process restarts. On restart, serving-startup restore re-verifies and loads exactly the ledger's
active deployment, then re-mirrors the alias.

## Recover a directory created by a direct Compose invocation

If the wrapper reports that `.cache/platform/source-provenance.json` is a directory, first stop
the stack through the wrapper; `down` deliberately works without rewriting provenance:

```bash
python3.12 scripts/platform_compose.py down
```

Inspect the path. Only if it is an empty directory created by Docker, remove that empty directory
with the non-recursive command below, then rerun the documented `up` command. `rmdir` refuses to
remove any directory that contains data.

```bash
ls -ld .cache/platform/source-provenance.json
rmdir .cache/platform/source-provenance.json
python3.12 scripts/platform_compose.py up --build --wait
```

## Platform dependency lock

`requirements/platform-py312-v3.lock` is the complete, hash-verified Python 3.12 runtime graph
for the platform image. The image verifies that the lock still corresponds to the platform inputs in
`pyproject.toml`, installs it with `pip --require-hashes`, then installs this repository with
`--no-deps`; it never resolves `.[platform]` during an ordinary build. The evaluation flow records
the SHA-256 of this exact consumed lock in its policy and run manifests.

`requirements/mlflow-py312.lock` is the matching hash-verified graph for the MLflow image. It is
compiled from `requirements/mlflow-py312.in` and installed with `pip --require-hashes`.

When an intentional platform-runtime dependency change is approved, regenerate the lock with
Python 3.12 and the `pip-tools` included in the documented editable developer setup:

```bash
.venv/bin/pip-compile --extra platform --generate-hashes --resolver=backtracking --output-file requirements/platform-py312-v3.lock pyproject.toml
```

Regenerate the MLflow lock the same way when its pins change:

```bash
.venv/bin/pip-compile --generate-hashes --strip-extras --output-file requirements/mlflow-py312.lock requirements/mlflow-py312.in
```

`pip-compile` relies on pip internals, so the installed `pip-tools` must match the installed `pip`.
If `pip-compile` fails on import or option parsing after a `pip` upgrade, see the pip-tools
compatibility notes before changing either version.

Replace the `pixelgym-platform-input-sha256` header with the value printed by:

```bash
python3.12 -c 'from pathlib import Path; from pixelgym.platform.dependency_lock import platform_input_sha256; print(platform_input_sha256(Path("pyproject.toml")))'
```

Then verify locally before review:

```bash
python3.12 pixelgym/platform/dependency_lock.py --repository-root .
python3.12 -m pytest tests/unit/platform/test_dependency_lock.py -q
```

This does not change the documented developer setup: `python3.12 -m venv .venv && pip install -e
".[dev]"` remains the sole setup step for the fast suite and lint.

### Container user and existing volumes

The platform and MLflow containers run as the unprivileged user uid 10001 (gid 10001). A
`control-data` volume created while the containers still ran as root is owned by root, and the
control service cannot write to it. Either recreate the volume, which discards its local control
database, or change its ownership once:

```bash
docker compose -f deploy/compose.yaml run --rm --no-deps --user 0 --entrypoint chown migrate -R 10001:10001 /state
```

The Compose project is `pixelgym-platform`, so the volume is `pixelgym-platform_control-data`.

## Serving request and provider bounds

`POST /api/v1/ground` applies fixed request bounds before provider execution: the request body is
limited to 7,006,892 bytes, `image_base64` to 6,990,508 characters, the decoded screenshot to 5
MiB, and each screenshot dimension to 4,096 pixels. PNG and JPEG are the only accepted media
types. Equality with each limit is accepted; a value above it is rejected.

Provider execution has four deployment settings. Invalid, non-finite, or non-positive settings
stop application construction (the queue wait alone may be zero):

- `PIXELGYM_PROVIDER_TIMEOUT_SECONDS` defaults to `30.0`. The service returns `504`, records
  `provider_timeout_enforced`, and does not retry or fall back when the call exceeds this
  duration. Because the provider contract is synchronous, a timed-out call is abandoned, never
  cancelled; it retains its concurrency slot until the underlying call finishes, and the worker
  thread pool is shut down (without waiting for abandoned calls) when the application stops.
- `PIXELGYM_PROVIDER_CONCURRENCY` defaults to `4`. This provider-call bulkhead is independent of
  the immutable-audit I/O limiter.
- `PIXELGYM_PROVIDER_QUEUE_TIMEOUT_SECONDS` defaults to `0.25`. A request that cannot enter the
  provider bulkhead within this bounded wait returns `503` and records
  `provider_concurrency_saturated`; the provider is not invoked for that request.
- `PIXELGYM_MAX_PROVIDER_OUTPUT_BYTES` defaults to `65536`. The limit is measured on the raw
  response's UTF-8 bytes before metadata normalization or prediction parsing. Equality is accepted;
  an over-limit response returns `502`, records `provider_output_too_large` plus the observed byte
  count, and is never partially parsed.

The service-enforced timeout above (`provider_timeout_enforced`) stays distinct from a
provider-reported timeout: a provider that raises its own timeout failure records
`provider_timeout` instead, so the operational record always shows which side gave up. Other
provider-reported failures remain distinct too, as `429` `provider_rate_limit` and `502`
`provider_<code>`. Attributable errors, including validation (`422`), saturation (`503`), timeout
(`504`), and oversized output (`502`), include API, policy, deployment, and exact-policy identity
in the structured `identity` object and the corresponding `X-PixelGym-*` response headers. Errors
before a policy can be resolved do not claim an identity.

## Serving operational records

Every `/api/v1/ground` request creates one immutable, independently retrievable JSON record under
`serving-operational-records/`. It records the server-generated request ID, UTC receipt time,
policy/deployment/exact-policy identity when one was loaded, terminal status, HTTP status,
end-to-end latency, provider request ID and latency, raw provider-output byte count when one was
returned, and normalized provider usage. A missing usage value is recorded as `null`; an empty
usage map remains `{}`. The response includes the same server-generated ID in
`X-PixelGym-Request-ID`.

Operational records intentionally exclude prompts, targets, screenshots, request bodies, raw
provider responses, expected answers, credentials, and provider error details. The service rejects
malformed provider telemetry rather than writing ambiguous evidence. If immutable record writing or
verification fails, the request is returned as `503` even if inference completed: traffic is not
allowed to receive an unrecorded result. Consequently an operator should alert on that response and
restore immutable-store access before retrying.

The Compose stack stores these records in the same versioned MinIO bucket as other platform
evidence, with the configured 30-day governance retention. Local filesystem runs use the existing
application put-once adapter and do **not** claim storage-enforced WORM retention. Restrict object
read access to the operational-review role; records are not exposed through the serving API.

## Stateful serving (`/api/v2`)

The application mounts the `/api/v2/episodes` routes. They serve only while a `stateful-v5`
deployment holds the active pointer; otherwise they return `503 no_active_deployment`, and an
episode whose deployment has since been replaced returns `409 deployment_changed`. The default
`create_app()` configures no stateful preparer and no approved v5 gate policy, so the control
plane refuses to register or deploy a stateful package. Enabling it requires passing
`StatefulServingConfig` (approved gate-policy digests and a preparer) to `create_app`; the gate
policy, the first package, and its attempt cap are human decisions (v5 serving stage S7), and any
real provider transport needs its own approval. Episode records and final screenshots are written
put-once under `serving-episode-records/` and `serving-final-screenshots/`.

## Approved real-provider evaluations

The evaluation flow's default and only web-submittable provider is the no-cost scripted replay.
A second path, added for the D4.5 residual (issue #164), lets a command-line launch select one
*preconfigured, allowlisted* real provider policy. It is designed so that nothing about the
provider, model, prompt, price, or cap can be chosen at launch time: the launcher can only name a
registry record and prove that the exact record was approved.

- **Registry.** `config/approved-providers.json` lists `ApprovedProviderPolicy` records
  (`pixelgym/platform/approved_providers.py`). Each record fixes the transport (`openrouter` is
  the only one this version admits), the policy-manifest provider label, the exact model string,
  the prompt version, the `raw` condition, the per-run call cap, the maximum provider
  concurrency, the frozen price-catalog version, the **name** of the credential environment
  variable, scalar request parameters, and provider routing. Records are data: no value is ever
  interpreted as a path, module, command, or flow name, unknown keys are rejected, and a record
  that names the scripted demo, the demo price catalog, a `marks` condition, or a coordinate
  rescaling adapter is refused. The checked-in registry ships empty.
- **Approval digest.** Every record has a content-bound `approval_sha256` over its canonical
  JSON. The flow requires `--approved-provider <reference>` together with
  `--approved-provider-sha256 <digest>`, and refuses to start when the digest differs from the
  record on disk or when `--model`, `--prompt-version`, or `--maximum-calls` do not restate the
  record exactly. Changing any field therefore invalidates the previous approval. A scripted
  launch that carries a digest is also refused.
- **Price catalog.** The record's `price_catalog_version` selects
  `config/price-catalog.<version>.json`, validated by the existing price-catalog schema, and the
  catalog must contain an entry for the record's provider/model. Cost per call is derived from the
  provider's reported token usage and that entry; a response without usage is retained as an
  unpriced call, never estimated.
- **Credential.** The named environment variable must be set when the flow starts, before any
  MLflow run, storage write, or request. Only the variable name enters the policy manifest,
  tracking parameters, or raw envelopes.
- **Call cap and retries.** Every attempt is reserved in a durable per-submission SQLite ledger
  (`.cache/platform/approved-calls/<submission>.sqlite`, or `PIXELGYM_APPROVED_CALL_LEDGER_ROOT`)
  before the request is sent, so the cap holds across every Metaflow shard task and every resume
  of the same run, not only within one process. A reservation is never released; an attempt
  with an unknown outcome still counts. Request failures, unparseable answers, and wrong answers
  are stored once and never retried; the raw provider text is stored byte-for-byte before
  parsing. Run-wide provider concurrency is bounded by the supported launch commands
  (`--max-workers 1`) together with the per-task `--provider-concurrency` value, which the record's
  `max_concurrency` caps. The frozen dataset
  ([`artifacts/grounding-dataset.jsonl`](../artifacts/grounding-dataset.jsonl)) has 100 examples
  and the runner rejects a cap below the example count
  (`build_shards` in [`pixelgym/platform/evaluation.py`](../pixelgym/platform/evaluation.py)),
  so an approved grounding record's `call_cap` bounds the paid calls the run may make.

Print the digest to approve, and launch only after a human has approved that exact value:

```bash
.venv/bin/python -c "from pathlib import Path; from pixelgym.platform.approved_providers import load_approved_providers; [print(p.reference, p.approval_sha256) for p in load_approved_providers(Path('.')).values()]"
```

```bash
<record credential_env>=... .venv/bin/python flows/grounding_evaluation_flow.py run --submission-id <id> --approved-provider <reference> --approved-provider-sha256 sha256:<digest> --model <record model> --prompt-version <record prompt_version> --maximum-calls <record call_cap> --provider-concurrency 1 --max-workers 1
```

Adding a registry record, a price-catalog file, and the approval itself are human decisions under
AGENTS.md §4 (provider choice and cloud spend). Agents may add records and tests but must not
launch this path. Candidates produced this way carry `provider` other than `scripted-demo`, are
labelled non-synthetic in the control plane, and remain subject to the same promotion gates. The
registry's `kind` field is fixed to `grounding`; a stateful v5 policy kind requires the separate
versioned flow described in the v5 plan's platform-boundary section and its own approval.

## Production reference

The immutable adapter pins S3 object version IDs and verifies SHA-256 on every boundary. Use a
versioned bucket with S3 Object Lock in compliance mode, least-privilege roles, backup/replication,
and an approved retention policy. The local demo uses MinIO governance retention for compatibility;
it is not a claim of production WORM administration.
