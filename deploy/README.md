# Local platform stack

This stack is for the no-cost scripted lifecycle demo. It starts PostgreSQL-backed MLflow,
versioned MinIO buckets, the Metaflow-capable control plane, and the versioned serving API. The
public values in `.env.example` are local demo credentials, not production secrets.

From the repository root:

```bash
docker compose --env-file deploy/.env.example -f deploy/compose.yaml up --build --wait
```

Before a gate-eligible demo, replace `PIXELGYM_CODE_REVISION` with the exact 40-character lowercase
clean Git commit being evaluated. Any other value—including the checked-in `unknown-dirty`
fallback—fails the code-revision gate. Open the control plane at <http://localhost:5800> and MLflow at
<http://localhost:5500>. Both host ports are configurable in `.env.example`.

Stop the stack without deleting evidence:

```bash
docker compose --env-file deploy/.env.example -f deploy/compose.yaml down
```

To remove local demo volumes, the human operator must explicitly add `--volumes`. That operation
deletes local evidence and is intentionally not part of the normal workflow.

## Production reference

The immutable adapter pins S3 object version IDs and verifies SHA-256 on every boundary. Use a
versioned bucket with S3 Object Lock in compliance mode, least-privilege roles, backup/replication,
and an approved retention policy. The local demo uses MinIO governance retention for compatibility;
it is not a claim of production WORM administration.
