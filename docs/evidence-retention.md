# Retain and restore evaluation evidence

Use this workflow before removing a checkout or cleaning an evaluation directory. It captures
restricted evidence outside Git, verifies a second copy, and restores both copies before issuing
a cleanup receipt. The private Git LFS extension below also downloads and verifies a remote
copy. Neither workflow deletes the original evidence or calls a model.

The reader is the local evaluation operator. Local capture, restore, and scripted validation need
Python 3.12 and the normal `.[dev]` installation from the [reproduction guide](reproduction.md).
These local operations need no extra storage service, browser, VM, credentials, or provider
subscription. Remote Git LFS transfers also require the CLI tools and GitHub authentication
listed in the private-backup section below.

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

This command restores each copy into its own temporary directory and removes that temporary
copy before restoring the next. It checks every restored file and compares the current source
fingerprint with capture. Set `TMPDIR` to a volume with room for one unpacked archive when the
system temporary volume is small. It writes a receipt only after those checks complete. The
receipt must be new and outside the source, archive, and backup; the command enforces this.
Any later source change
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

A new campaign requires Git and a source root that is the checkout root. Before capture, it resolves
Git from the operator's PATH, refuses modified or untracked files in the selected source paths,
and checks the selected file names and SHA-256 hashes against `git archive` at the recorded
revision. This also rejects selected source files that Git ignores. It checks the captured
source against that revision again before writing the binding. Existing run, archive, backup,
public-output, check-log, or receipt paths fail before capture. Use a new run ID and public-output
directory for each run. The command retains:

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
subcommand rechecks the retained source hashes and prints regenerated report JSON from stored
observations. It never executes a policy, transport, or replay. Markdown is generated by `run`:

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
  --bundle artifacts/evidence-retention-20261020-v2
```

This verifies the published hash relations, case allocation, restore observations, source
bindings, and generated Markdown. It cannot verify the absent private archive bytes or replay
their actions. The [recorded bundle](../artifacts/evidence-retention-20261020-v2/REPORT.md) retains
the scripted observations from the implementation revision named in its source binding.
The [first bundle](../artifacts/evidence-retention-20261020/REPORT.md) remains unchanged; the v2
bundle records the successor implementation that refuses unreadable directory scans.
The [review successor](../artifacts/evidence-retention-20261020-v3/REPORT.md) records the source
provenance and read-only journal repairs. Its [private LFS restore receipt](../artifacts/evidence-retention-20261020-v3-lfs/REPORT.md)
records a separate fresh remote download and retained-source verification. Earlier bundles and
remote receipts remain unchanged.

New publications screen every JSON value and the generated Markdown for credential-shaped
content before creating the public-output directory. The private journals remain outside Git.

## Private Git LFS backup and remote restore

The approved remote is the separate private repository `elanthus/OSWorldTasks-evidence` on
GitHub. Raw archives must never enter this public repository or its release assets. Git and
`git-lfs` plus an authenticated `gh` CLI are required for remote transfers; they are not Python
dependencies and are not required for the fast suite. Git LFS 3.8.0 was used for the recorded
transfer. The optional local transport check uses a filesystem remote, without credentials or
network sockets:

```bash
PIXELGYM_RUN_LFS_INTEGRATION=1 .venv/bin/pytest -q tests/integration/test_retention_lfs_local.py
```

Before a transfer, open [GitHub budgets and alerts](https://github.com/settings/billing/budgets).
Confirm the **Git LFS** budget covers this repository, has amount **$0**, and has **Stop usage
when budget limit is reached** enabled. Save the setting and reopen it to check that it persisted.
Do not raise the limit if GitHub refuses a transfer. Preserve the source and local copies.
GitHub enforces the spending limit; a local size guard does not establish available free quota.
The owner account's allowance is shared across its repositories, and Actions downloads consume
that allowance too. See [GitHub's billing rules](https://docs.github.com/en/billing/concepts/product-billing/git-lfs).

GitHub's public budget API covers organizations and enterprises, not personal-account budget
settings. The command therefore requires a separate operator observation made within the last
24 hours. This observation records the UI check; it cannot configure the remote setting or
prove the setting remained unchanged. Keep it outside Git and make a new observation after any
budget change. After checking the UI, create a new record:

```bash
env EVIDENCE_STATE="$EVIDENCE_STATE" .venv/bin/python - <<'PY'
import os
from datetime import UTC, datetime
from pathlib import Path
from pixelgym.grounding.v5.retention import write_private
from pixelgym.serialization import canonical_json_bytes

record = {
    "schema_version": "pixelgym-lfs-zero-budget-observation-v1",
    "repository": "elanthus/OSWorldTasks-evidence",
    "product": "git_lfs",
    "budget_usd": 0,
    "stop_usage": True,
    "scope": "account",
    "observed_via": "github-billing-ui",
    "observed_at_utc": datetime.now(UTC).isoformat(),
}
write_private(Path(os.environ["EVIDENCE_STATE"]) / "lfs-budget-001.json",
              canonical_json_bytes(record))
PY
```

For the fixed seven-case validation archive, back up and verify a remote restore:

```bash
.venv/bin/python -m scripts.backup_evidence_lfs backup \
  --repository elanthus/OSWorldTasks-evidence \
  --archive "$EVIDENCE_STATE/evidence/archives/run-001" \
  --digest "$ARCHIVE_DIGEST" \
  --budget-record "$EVIDENCE_STATE/lfs-budget-001.json" \
  --destination "$EVIDENCE_STATE/restored-lfs/run-001" \
  --receipt "$EVIDENCE_STATE/receipts/run-001-lfs.json" \
  --logs "$EVIDENCE_STATE/checks/run-001-lfs" \
  --validation-campaign
```

The command verifies the source archive and packs it with fixed file metadata. It checks the
remote's private status before each transfer. The dedicated remote keeps only a fixed README,
LFS attributes, a catalog, and archives named by their manifest digest. Each new archive adds a
commit to `main` with a normal push; an existing digest reuses its recorded object. Conflicting
pushes fail. No force push, automatic deletion, or routine CI download is used.

After upload, the command removes its temporary upload checkout. A separate clone with a new
LFS cache downloads only the requested archive at the recorded full commit. It checks the LFS
object's size and SHA-256, unpacks only safe regular files, checks the archive manifest and SQLite
integrity, and restores to the new destination. `--validation-campaign` then runs the trusted
retained verifier in an isolated Python process with credentials removed. Stored actions and
the report must reproduce without changing restored evidence. A receipt is written only after
all checks succeed. A transfer or verification failure leaves the original evidence intact;
a successful upload followed by a failed check can leave an unverified remote copy.

The local guards allow at most 512 MiB per archive and 5 GiB in the current catalog. They reduce
accidental use, but do not account for other repositories or removed historical objects. Keep
the actual GitHub $0 limit enabled. Keep VM images, container images, and model weights in their
appropriate stores. This remote is for evidence and small policy/source records.

To repeat recovery later, use the receipt's full `commit` and the separately retained expected
manifest digest. A new destination and a current budget observation are required:

```bash
.venv/bin/python -m scripts.backup_evidence_lfs restore \
  --repository elanthus/OSWorldTasks-evidence \
  --commit "$REMOTE_COMMIT" --digest "$ARCHIVE_DIGEST" \
  --budget-record "$EVIDENCE_STATE/lfs-budget-001.json" \
  --destination "$EVIDENCE_STATE/restored-lfs/run-001-repeat" \
  --receipt "$EVIDENCE_STATE/receipts/run-001-repeat-lfs.json" \
  --logs "$EVIDENCE_STATE/checks/run-001-repeat-lfs" \
  --validation-campaign
```

Use `--validation-campaign` only with a trusted fixed validation archive: it executes that
archive's retained source. Omit it for other sealed evidence; the resulting receipt explicitly
covers archive bytes and SQLite integrity only. Run that experiment's own semantic checks
separately. A remote receipt is evidence of one recovery, not continuous availability, permanent
privacy, or a milestone verdict. Before cleanup, retain the remote receipt and also run the
local `check-cleanup` command against the still-present source. Neither command deletes evidence.

The [recorded remote-recovery observations](../artifacts/evidence-retention-20261020-lfs/REPORT.md)
bind the two original validation bundles to their private remote commits and repeated recovery.
Regenerate that report from stored receipts without network access or replay:

```bash
.venv/bin/python -m scripts.backup_evidence_lfs report \
  --bundle artifacts/evidence-retention-20261020 \
  --bundle artifacts/evidence-retention-20261020-v2 \
  --receipt artifacts/evidence-retention-20261020-lfs/v1-receipt.json \
  --receipt artifacts/evidence-retention-20261020-lfs/v2-receipt.json \
  --receipt artifacts/evidence-retention-20261020-lfs/v2-repeat-receipt.json
```

The offline report command checks receipt bindings to the original public manifests, case counts,
and stored report/replay digests. It cannot download or verify the private remote bytes.

## Historical limits

The D5.9 attempt and invocation journals were untracked in a temporary checkout that was removed.
Their recorded digests cannot reconstruct them. The [evidence index](evidence-index.md) retains
that disclosure. This workflow and its new validation bundle do not repair historical evidence,
replicate D5.9, authorize model traffic, change public model-quality claims, or declare D5.10.
