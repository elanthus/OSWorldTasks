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
`revision_invalid`/`source_digest_mismatch`) without exposing the manifest contents or local
filesystem paths.
Do not invoke `docker compose` against `deploy/compose.yaml` directly: Docker can create a
directory at the file bind-mount path, allowing a stack to start without verifiable source
provenance. The wrapper refuses that condition before an `up` command reaches Docker.
Open the control plane at <http://localhost:5800> and MLflow at <http://localhost:5500>. Both host
ports are configurable in `.env.example`.

Stop the stack without deleting evidence:

```bash
python3.12 scripts/platform_compose.py down
```

To remove local demo volumes, the human operator must explicitly add `--volumes`. That operation
deletes local evidence and is intentionally not part of the normal workflow.

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
