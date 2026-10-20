# Retain and restore evaluation evidence

Use this workflow before removing a checkout or cleaning an evaluation directory. It captures
restricted evidence outside Git, verifies a second copy, and restores both copies before issuing
a cleanup receipt. It never deletes the original evidence or calls a model.

The reader is the local evaluation operator. Python 3.12 and the normal `.[dev]` installation
from the [reproduction guide](reproduction.md) are sufficient. No extra storage service, browser,
VM, credentials, or provider subscription is required.

## Storage and capture

Choose persistent directories outside every Git checkout. The commands below use two local
directories under the user's state directory. They protect against removing a checkout; copies
on the same disk do not protect against disk loss. For that protection, place the backup on a
separate durable volume and protect its access appropriately.

```bash
EVIDENCE_STATE="${XDG_STATE_HOME:-$HOME/.local/state}/pixelgym"
```

Keep authoritative journals, responses, screenshots, and policy checkpoints private. Archives
use private directories and files. Do not commit or upload them as public CI artifacts.

1. Stop all writers and provider subprocesses for the run. Quiescence must cover the entire run
   directory, including invocation journals, summaries, and checkpoint files.
2. Capture the directory and a backup under a new name:

   ```bash
   .venv/bin/python -m scripts.retain_v5_evidence capture \
     --source "$RUN_DIRECTORY" \
     --archive "$EVIDENCE_STATE/evidence/archives/run-001" \
     --backup "$EVIDENCE_STATE/evidence-backup/run-001"
   ```

3. Save the returned `archive.manifest_digest` separately from the archive as the expected
   digest for subsequent commands. Capture refuses existing destinations. If the second copy
   fails, the first copy is preserved, but no cleanup receipt is issued. Preserve the source
   and investigate the failure before trying again with a new destination name.

SQLite files are captured with SQLite's backup API, including committed WAL data, and checked
with `PRAGMA integrity_check`. Restored databases are self-contained and require no WAL or SHM
sidecars. Other regular files are copied exactly. The source fingerprint binds names and bytes,
including WAL files but excluding SQLite's volatile SHM index. Source changes detected during
capture abort it. This is not a transaction across databases: stopping writers remains required.
An existing SQLite rollback journal is refused; recover and stop that database before capture.

Symlinks, special files, missing or unlisted archive members, unsafe manifest paths, digest
mismatches, and overlapping source/archive/backup paths are refused. Archive manifests bind
contents; they are not signatures or proof against an actor who can replace both the archive and
the operator's expected digest.

## Restore and check cleanup

Set `ARCHIVE_DIGEST` to the digest saved at capture. Restore into a new directory:

```bash
.venv/bin/python -m scripts.retain_v5_evidence restore \
  --archive "$EVIDENCE_STATE/evidence/archives/run-001" \
  --destination "$EVIDENCE_STATE/restored/run-001" \
  --digest "$ARCHIVE_DIGEST"
```

Restore verifies every file before and after copying and refuses existing destinations. For
generic evaluation archives, run that evaluation's own journal verifier and report generator
on the restored directory. A successful byte restore alone does not establish semantic validity.

Immediately before cleanup, verify both copies against the still-present original:

```bash
.venv/bin/python -m scripts.retain_v5_evidence check-cleanup \
  --source "$RUN_DIRECTORY" \
  --archive "$EVIDENCE_STATE/evidence/archives/run-001" \
  --backup "$EVIDENCE_STATE/evidence-backup/run-001" \
  --digest "$ARCHIVE_DIGEST" \
  --receipt "$EVIDENCE_STATE/receipts/run-001.json"
```

This command restores both copies into separate temporary directories, checks every restored
file, and compares the current source fingerprint with capture. It writes a receipt only after
those checks complete. Keep the receipt outside the source directory. Any later source change
invalidates it; rerun the check immediately before cleanup. This workflow has no deletion
command and cannot intercept an external `git worktree remove`, archive action, or manual removal.

## Reproduce the credential-free validation

This command captures seven scripted cases through the existing v5 runner: a complete golden
episode, premature commit, step-limit exhaustion, malformed output, an invalid action, a call
cap, and one interrupted step followed by recovery. All requests use the in-process fake
transport. The golden policy uses privileged fixtures and provides no model-quality evidence.

```bash
env -i PATH="$PATH" LANG=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python -B -m scripts.validate_evidence_retention run \
  --store "$EVIDENCE_STATE/evidence" \
  --backup-store "$EVIDENCE_STATE/evidence-backup" \
  --run-id retention-001 \
  --public-output artifacts/evidence-retention-001
```

Use a new run ID and public-output directory for each run. The command retains:

- `evidence/runs/<run-id>/`: original journals, observations, replay results, report, and source.
- `evidence/archives/<run-id>/` and `evidence-backup/<run-id>/`: verified private copies.
- `evidence/receipts/<run-id>.json`: cleanup precondition and independent verification results.
- `evidence/checks/<run-id>/`: private stdout, stderr, and process outcomes for restore checks,
  including failed or timed-out checks. A failed check leaves the original run and archives
  intact and produces no cleanup receipt or public bundle.
- The public-output directory: scripted projections, source hashes, archive manifest, receipt,
  and generated report. It contains no database, screenshots, or provider responses.

For each copy, the command restores into a fresh temporary directory and launches an isolated
Python interpreter using the retained source with inherited credentials and Python/Git controls
removed. Replay compares every stored dispatch's screenshot bytes, backend checkpoint, reward,
termination, and truncation. A second recovery of the interrupted step must not send or dispatch
again. Verification must leave the restored files unchanged. Both copies must reproduce the
stored replay results and report before a receipt and public bundle are written.

The `verify` subcommand repeats replay checks on a restored validation run. The `report`
subcommand only renders stored JSON; it never executes a policy, transport, or replay:

```bash
.venv/bin/python -m scripts.validate_evidence_retention verify --run "$RESTORED_RUN"
.venv/bin/python -m scripts.validate_evidence_retention report --run "$RESTORED_RUN"
```

These two commands use the current checkout's implementation. The automatic fresh-interpreter
checks use the archived implementation and include the interpreter/dependency versions and
source binding in the bundle. A reviewer can regenerate this fixed campaign locally to inspect
all private scripted evidence. The checked-in projections alone do not contain that evidence.

Check the published bundle without access to private storage:

```bash
.venv/bin/python -m scripts.verify_retention_bundle \
  --bundle artifacts/evidence-retention-20261020
```

This verifies the published hash relations, case allocation, restore observations, source
bindings, and generated Markdown. It cannot verify the absent private archive bytes or replay
their actions. The [recorded bundle](../artifacts/evidence-retention-20261020/REPORT.md) retains
the scripted observations from the implementation revision named in its source binding.

## Historical limits

The D5.9 attempt and invocation journals were untracked in a temporary checkout that was removed.
Their recorded digests cannot reconstruct them. The [evidence index](evidence-index.md) retains
that disclosure. This workflow and its new validation bundle do not repair historical evidence,
replicate D5.9, authorize model traffic, change public model-quality claims, or declare D5.10.
