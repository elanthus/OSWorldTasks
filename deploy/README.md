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
Open the control plane at <http://localhost:5800> and MLflow at <http://localhost:5500>. Both host
ports are configurable in `.env.example`.

Stop the stack without deleting evidence:

```bash
python3.12 scripts/platform_compose.py down
```

To remove local demo volumes, the human operator must explicitly add `--volumes`. That operation
deletes local evidence and is intentionally not part of the normal workflow.

## Production reference

The immutable adapter pins S3 object version IDs and verifies SHA-256 on every boundary. Use a
versioned bucket with S3 Object Lock in compliance mode, least-privilege roles, backup/replication,
and an approved retention policy. The local demo uses MinIO governance retention for compatibility;
it is not a claim of production WORM administration.
