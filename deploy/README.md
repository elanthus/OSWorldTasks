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

### Control-plane session cookie

The control-plane bootstrap derives the `pixelgym_session` cookie's `Secure` attribute from the
bind address supplied to `create_app`: `127.0.0.1`, `::1`, and `localhost` default to `Secure`
off, while every other address defaults to `Secure` on. Callers may explicitly override that
choice with the `session_cookie_secure` parameter. Keep the bind address loopback-only when using
plain HTTP; any non-loopback deployment must terminate TLS before sending this cookie.
When no `bind_address` argument is supplied, `PIXELGYM_BIND_ADDRESS` supplies the bind address and
defaults to `127.0.0.1` when unset. An explicit `session_cookie_secure` argument takes precedence
over `PIXELGYM_SESSION_COOKIE_SECURE` (`true` or `false`, case-insensitive), after which the resolved
bind address determines the default as described above.

The cookie is always server-issued and has the exact format `<session-id>.<tag>`. `session-id` is
the 32-character URL-safe Base64 output of `secrets.token_urlsafe(24)`. `tag` is the 64-character
lowercase hexadecimal HMAC-SHA256 digest whose key is the UTF-8 encoded `PIXELGYM_CSRF_SECRET` and
whose message is the ASCII bytes `pixelgym-session-v1\0<session-id>` (where `\0` is one NUL
byte). The server retains a presented cookie only when its shape and tag verify; otherwise it
replaces it with a fresh value. No session table is used. The remaining attributes are always
`HttpOnly` and `SameSite=Strict`.

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

The Metaflow suite also sends `SIGKILL` to the entire local flow process group after durable
evidence persistence and resumes the recorded origin run. It proves no duplicate fixture-provider
billing in that bounded local runtime. A remote scheduler that loses its parent independently can
still leave an orphan run; production use needs a separate orphan-run reconciler and this test does
not claim otherwise.

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

`requirements/platform-py312.lock` is the complete, hash-verified Python 3.12 runtime graph for
the platform image. The image verifies that the lock still corresponds to the platform inputs in
`pyproject.toml`, installs it with `pip --require-hashes`, then installs this repository with
`--no-deps`; it never resolves `.[platform]` during an ordinary build. The evaluation flow records
the SHA-256 of this exact consumed lock in its policy and run manifests.

When an intentional platform-runtime dependency change is approved, regenerate the lock with
Python 3.12 and the `pip-tools` included in the documented editable developer setup:

```bash
.venv/bin/pip-compile --extra platform --generate-hashes --resolver=backtracking --output-file requirements/platform-py312.lock pyproject.toml
```

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

## Serving operational records

Every `/api/v1/ground` request creates one immutable, independently retrievable JSON record under
`serving-operational-records/`. It records the server-generated request ID, UTC receipt time,
policy/deployment/exact-policy identity when one was loaded, terminal status, HTTP status,
end-to-end latency, provider request ID and latency, and normalized provider usage. A missing
usage value is recorded as `null`; an empty usage map remains `{}`. The response includes the same
server-generated ID in `X-PixelGym-Request-ID`.

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

## Production reference

The immutable adapter pins S3 object version IDs and verifies SHA-256 on every boundary. Use a
versioned bucket with S3 Object Lock in compliance mode, least-privilege roles, backup/replication,
and an approved retention policy. The local demo uses MinIO governance retention for compatibility;
it is not a claim of production WORM administration.
