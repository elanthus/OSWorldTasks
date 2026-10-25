# D5.10 raw evidence index

Evidence revision: `290d3b626cd5f67e5ede1dd2eed086d06dd18b2f`

Evidence branch: `evidence/d510-offhost-restore`

Observation window: `2026-10-25T23:49:41.089648+00:00` to `2026-10-25T23:55:52.469977+00:00`

Verdict: `null`

Verdict owner: project owner

This index copies and links stored observations only. It does not rerun commands, reinterpret gate semantics, declare a milestone verdict, approve public wording, or change human-owned checklist state.

## Path placeholders

| Placeholder | Meaning |
| --- | --- |
| `<home>` | Recording account home. |
| `<path-0>` | Collection checkout (absolute cwd). |
| `<path-1>` | Fresh private directory holding the copies downloaded back from the Backblaze B2 bucket. |
| `<path-2>` | Local private archive store (first retained copy). |
| `<path-3>` | Local private backup store on the same disk (second retained copy). |
| `<path-4>` | Private retained parser-sensitivity execution directory (run root). |
| `<path-5>` | Private retained replication execution directory (run root). |
| `<path-6>` | Fresh private restore destinations for this drill. |
| `<path-7>` | Private receipts written by this drill. |
| `<system-temp>` | System temporary directory. |

## Recording notes

- This supplement records the first off-host copy of the two retained D5.9 campaign archives and a restore drill from that copy. It confers no verdict: D5.10 was declared separately in artifacts/decisions/d5.10.json against fd54dc2782ac8e7b3526f19e7fa46a0131175e5e, and that record is unchanged.
- Upload: both archive trees were copied with rclone 1.75.2 (--checksum) to the private Backblaze B2 bucket named in BACKBLAZE_BUCKET_NAME. A first attempt on 2026-10-25 stopped at the account storage cap after 67 small files; rclone cancelled its partial multipart upload (zero unfinished parts observed). After the owner raised the cap, the rerun completed: 68 + 23 files, 26,085,481,886 bytes, both rclone checks reporting 0 differences. Those upload logs are retained privately and summarized here; the recorded evidence starts at the read-back and download checks.
- Lock: a one-year governance-mode Object Lock retention was applied per file by a one-off owner-approved script run on 2026-10-25 after verification; record 03 is the independent read-only read-back of that state through the committed scripts/observe_b2_object_lock.py.
- Credentials were read from the ignored .env file into the environment of the recorded processes and never appear in any record, argv, or output; the recorder stores only explicitly passed environment variables, and none were passed.
- Records 04-05 compare the downloaded copies against the local originals. Record 06 restores the parser-sensitivity pilot and full archives from the downloaded copy and from the local backup and reproduces the published projections. Records 07-08 restore the replication archives from the downloaded copy with manifest-digest verification. Record 09 reruns the replication audit with the downloaded copy as the archive and verifies the stored projection byte for byte.
- No model or authentication-probe call was made. No original, retained, downloaded or restored evidence was deleted. No cleanup authorization was issued. Both local copies remain on one disk; the off-host copy is the third copy.
- Record 06's helper receipt carries off_host_restore_performed=false because scripts/restore_grounding_v5_d510.py hard-codes that field; the record's argv shows the archive side was the downloaded copy. offhost-restore-drill.json wraps the receipt with that provenance without altering it.
- Record 10 was first attempted with rg, which was absent from the driver's PATH, so the recorder raised before writing a record; the stored record 10 uses grep. Record 14 was added after record 11 as a control for record 09.

## Checklist evidence

Checklist text is copied verbatim from `f0bcbd6f3e379f880baeca233d4b7e05528bcfc5:plans/grounding-v5-agent-benchmark.md`. The observation column copies exit statuses and parsed pytest counts from the listed records.

| # | Checklist line | Source | Stored raw observation | Records | Artifacts |
| ---: | --- | --- | --- | --- | --- |
| 1 | Review raw commands, outputs, evidence integrity, limitations, benchmark verdict, and any public wording | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:658` | `00-git-revision`: exit 0<br>`01-worktree-status`: exit 0<br>`02-rclone-version`: exit 0<br>`03-object-lock-readback`: exit 0<br>`04-downloaded-vs-local-extraction`: exit 0<br>`05-downloaded-vs-local-replication`: exit 0<br>`06-offhost-restore-drill-extraction`: exit 0<br>`07-offhost-restore-replication-full`: exit 0<br>`08-offhost-restore-replication-pilot`: exit 0<br>`09-replication-audit-verify-offhost`: exit 1<br>`10-public-wording-inventory`: exit 0<br>`11-worktree-status-after`: exit 0<br>`12-evidence-branch`: exit 0<br>`13-checklist-source`: exit 0<br>`14-replication-audit-verify-local-control`: exit 1 | `commands/00-git-revision.json`, `commands/01-worktree-status.json`, `commands/02-rclone-version.json`, `commands/03-object-lock-readback.json`, `commands/04-downloaded-vs-local-extraction.json`, `commands/05-downloaded-vs-local-replication.json`, `commands/06-offhost-restore-drill-extraction.json`, `commands/07-offhost-restore-replication-full.json`, `commands/08-offhost-restore-replication-pilot.json`, `commands/09-replication-audit-verify-offhost.json`, `commands/10-public-wording-inventory.json`, `commands/11-worktree-status-after.json`, `commands/12-evidence-branch.json`, `commands/13-checklist-source.json`, `commands/14-replication-audit-verify-local-control.json` | `artifacts/decisions/d5.10.json`, `artifacts/grounding-v5-d510/human-gate.json`, `artifacts/grounding-v5-d510/168598c243df24ba4f773cbc5696a1572532f532/REVIEW-NOTE.md`, `artifacts/grounding-v5-d510/168598c243df24ba4f773cbc5696a1572532f532/restore-drill.json`, `docs/v5-benchmark-design.md`, `docs/evidence-retention.md`, `scripts/restore_grounding_v5_d510.py`, `scripts/retain_v5_evidence.py`, `scripts/report_grounding_v5_d59_replication.py`, `scripts/observe_b2_object_lock.py`, `tests/unit/test_observe_b2_object_lock.py`, `artifacts/grounding-v5-d59-action-extraction-results/report.json`, `artifacts/grounding-v5-d59-action-extraction-results/pilot-report.json`, `artifacts/grounding-v5-d59-replication-results/report.json`, `artifacts/grounding-v5-d59-replication-results/pilot-report.json`, `artifacts/grounding-v5-d510/290d3b626cd5f67e5ede1dd2eed086d06dd18b2f/REVIEW-NOTE.md`, `artifacts/grounding-v5-d510/290d3b626cd5f67e5ede1dd2eed086d06dd18b2f/object-lock-observation.json`, `artifacts/grounding-v5-d510/290d3b626cd5f67e5ede1dd2eed086d06dd18b2f/offhost-restore-drill.json` |
| 2 | Stateful policy reset, policy-egress isolation, pre-send attempt persistence, unknown-attempt reconciliation, bounded deadline/cancellation settlement, post-attempt and post-dispatch state reconstruction, canonical parser-input recovery, atomic candidate/checkpoint persistence, sealed parser failure, exact backend-state restore or reconnect, durable dispatch ordering, invalid output, call caps, resume, authoritative replay, credential-free policy inputs, secret exclusion, redaction binding, and evidence integrity have no-cost failure-path tests. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:793` | `06-offhost-restore-drill-extraction`: exit 0<br>`07-offhost-restore-replication-full`: exit 0<br>`08-offhost-restore-replication-pilot`: exit 0<br>`09-replication-audit-verify-offhost`: exit 1 | `commands/06-offhost-restore-drill-extraction.json`, `commands/07-offhost-restore-replication-full.json`, `commands/08-offhost-restore-replication-pilot.json`, `commands/09-replication-audit-verify-offhost.json` | `scripts/restore_grounding_v5_d510.py`, `tests/unit/test_grounding_v5_d510_restore.py`, `pixelgym/grounding/v5/retention.py`, `artifacts/grounding-v5-d510/290d3b626cd5f67e5ede1dd2eed086d06dd18b2f/offhost-restore-drill.json` |
| 3 | The confirmatory report is generated only from sealed evidence, retains every failure, and separates primary, diagnostic, ablation, reliability, and exploratory results. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:807` | `06-offhost-restore-drill-extraction`: exit 0<br>`09-replication-audit-verify-offhost`: exit 1<br>`14-replication-audit-verify-local-control`: exit 1 | `commands/06-offhost-restore-drill-extraction.json`, `commands/09-replication-audit-verify-offhost.json`, `commands/14-replication-audit-verify-local-control.json` | `artifacts/grounding-v5-d59-replication-results/report.json`, `artifacts/grounding-v5-d59-action-extraction-results/sensitivity-analysis.json` |
| 4 | The agent reports raw milestone-gate commands, exit statuses, counts, runtimes, and full output without declaring the human gate passed or approving public wording. | `f0bcbd6:plans/grounding-v5-agent-benchmark.md:809` | `00-git-revision`: exit 0<br>`01-worktree-status`: exit 0<br>`02-rclone-version`: exit 0<br>`03-object-lock-readback`: exit 0<br>`04-downloaded-vs-local-extraction`: exit 0<br>`05-downloaded-vs-local-replication`: exit 0<br>`06-offhost-restore-drill-extraction`: exit 0<br>`07-offhost-restore-replication-full`: exit 0<br>`08-offhost-restore-replication-pilot`: exit 0<br>`09-replication-audit-verify-offhost`: exit 1<br>`10-public-wording-inventory`: exit 0<br>`11-worktree-status-after`: exit 0<br>`12-evidence-branch`: exit 0<br>`13-checklist-source`: exit 0<br>`14-replication-audit-verify-local-control`: exit 1 | `commands/00-git-revision.json`, `commands/01-worktree-status.json`, `commands/02-rclone-version.json`, `commands/03-object-lock-readback.json`, `commands/04-downloaded-vs-local-extraction.json`, `commands/05-downloaded-vs-local-replication.json`, `commands/06-offhost-restore-drill-extraction.json`, `commands/07-offhost-restore-replication-full.json`, `commands/08-offhost-restore-replication-pilot.json`, `commands/09-replication-audit-verify-offhost.json`, `commands/10-public-wording-inventory.json`, `commands/11-worktree-status-after.json`, `commands/12-evidence-branch.json`, `commands/13-checklist-source.json`, `commands/14-replication-audit-verify-local-control.json` | none |

## Recorded exceptions, waivers, and limitations

Checklist row 1: `artifacts/grounding-v5-d510/human-gate.json`

> "surface": "Both retained campaign copies on one local disk; no off-host recovery drilled",
>       "classification": "known limitation"

Checklist row 1: `docs/evidence-retention.md` lines 17-19

> They protect against removing a checkout; copies
> on the same disk do not protect against disk loss. For that protection, place the backup on a
> separate durable volume and protect its access appropriately.

## Records withdrawn before packaging

None.

## Items not run

- `Replication audit (--verify) at the current checkout`: Records 09 (off-host archive) and 14 (local archive control) both fail in under one second with 'source file hash mismatch': scripts/report_grounding_v5_d59_replication.py checks the campaign's bound source files against the current checkout, and those files changed after the campaign. The failure is identical for both copies and is independent of the off-host copy. The replication audit at its historical source revision is retained in the earlier c398557 package; here the replication off-host copy is established by records 05, 07 and 08 only.
  - Record: `commands/09-replication-audit-verify-offhost.json`
  - Record: `commands/14-replication-audit-verify-local-control.json`
- `Off-host copy of the original D5.9 Haiku journals`: Those journals were not retained (evidence index, 2026-09-30 note); nothing exists to copy. D5.9 still closes on its published projection.
- `Independent custody of the off-host copy`: The bucket, its key and both local copies are controlled by the same owner account; governance-mode retention resists deletion but an account holder with bypass rights could still remove versions after the lock. No third-party escrow was used.
- `Provider usage settlement for the five unknown timeouts`: Unchanged; the off-host copy adds durability, not new provider information.

## Public wording inventory

Copied verbatim from `commands/10-public-wording-inventory.json` (exit 0). Each line is `file:line:text` as printed by:

`grep -n -E 'D5.10|off-host|same local disk|single disk' README.md docs/v5-benchmark-design.md docs/evidence-index.md`

```
README.md:58:| v5 agent benchmark and calibration | Done at revision fd54dc2 for the environment, platform, and evidence-discipline claim; the Haiku stateful-versus-stateless result is descriptive (same direction in three campaigns, magnitude unresolved), not a pre-registered confirmation or model ranking | v5 gate ([D5.10](artifacts/decisions/d5.10.json), [gate record](artifacts/grounding-v5-d510/human-gate.json)), [v5 design](docs/v5-benchmark-design.md), [evidence index](docs/evidence-index.md) |
docs/v5-benchmark-design.md:4:- **Status:** v5 benchmark gate (D5.10) declared on 2026-10-25 (UTC) against `fd54dc27` for the environment, platform, and evidence-discipline claim; the Haiku result is descriptive ([decision record](../artifacts/decisions/d5.10.json), [gate record](../artifacts/grounding-v5-d510/human-gate.json))
docs/v5-benchmark-design.md:150:| D5.9 replication: pre-registered successor beside D5.9 | All 432 assignments recorded; frozen analysis and retained-journal audit prepared for owner review. One timeout retains unknown provider completion/usage. The historical and replication differences exceed the frozen disagreement threshold; both are reported. An exploratory [parse-failure signature](../artifacts/grounding-v5-d59-replication-results/prose-signature-diagnostic.md) from the retained journal is reported separately. D5.10 remains human-owned. | [replication review guide](../artifacts/grounding-v5-d59-replication-results/review-guide.md), [analysis](../artifacts/grounding-v5-d59-replication-results/confirmatory-analysis.md), [replication plan](../artifacts/grounding-v5-d59-replication/execution-plan.json), [addendum](../artifacts/grounding-v5-d59-freeze/analysis-plan.md) |
docs/v5-benchmark-design.md:151:| D5.10: milestone verdict and public wording | Declared `PASS` against `fd54dc27` on 2026-10-25 (UTC) for the environment, platform, and evidence-discipline claim. The Haiku stateful-versus-stateless result is reported descriptively: history beat stateless in the same direction in all three campaigns (historical 106 vs 15, replication 62 vs 20, post-hoc parser sensitivity 132 vs 20, each of 192 pairs); the magnitude is unresolved and no campaign is selected. Not a pre-registered confirmation, causal parser estimate, OS-enforced isolation claim, or model ranking. Every limitation is classified in the gate record. | [decision record](../artifacts/decisions/d5.10.json), [gate record](../artifacts/grounding-v5-d510/human-gate.json), [fresh validation supplement](../artifacts/grounding-v5-d510/168598c243df24ba4f773cbc5696a1572532f532/REVIEW-NOTE.md), [sensitivity analysis](../artifacts/grounding-v5-d59-action-extraction-results/sensitivity-analysis.md) |
docs/evidence-index.md:23:| What did the D5.9 parser-sensitivity successor record? | [Owner review guide](../artifacts/grounding-v5-d59-action-extraction-results/review-guide.md), [frozen-mechanics sensitivity analysis](../artifacts/grounding-v5-d59-action-extraction-results/sensitivity-analysis.md), and [raw commands](../artifacts/grounding-v5-d510/8327aac872715febe05d7c8e5534e68c2f39c2c2/REPORT.md) retain all 432 outcomes and five unresolved timeouts. This is post-hoc sensitivity on inspected tasks, not independent confirmation; original results are preserved and D5.10 remains human-owned. |
docs/evidence-index.md:24:| Which fresh no-call checks support the D5.10 review? | [Fresh validation and restore supplement](../artifacts/grounding-v5-d510/168598c243df24ba4f773cbc5696a1572532f532/REVIEW-NOTE.md), [raw command index](../artifacts/grounding-v5-d510/168598c243df24ba4f773cbc5696a1572532f532/REPORT.md), and [four-copy restore receipt](../artifacts/grounding-v5-d510/168598c243df24ba4f773cbc5696a1572532f532/restore-drill.json) retain fresh browser, OSWorld, CLI sandbox and unit observations, explain all six skips, and verify original inputs unchanged. Five timeout usages remain unresolved; both stores are on the same local disk. D5.10 remains human-owned. |
docs/evidence-index.md:25:| What did D5.10 decide, and what does it not claim? | The [decision record](../artifacts/decisions/d5.10.json) and [gate record](../artifacts/grounding-v5-d510/human-gate.json) declare `PASS` against `fd54dc27` for the environment, platform, and evidence-discipline claim and classify every remaining limitation. The Haiku result is descriptive only: history beat stateless in the same direction in [historical D5.9](../artifacts/grounding-v5-d59-haiku-results/confirmatory-analysis.json) (106 vs 15), [replication](../artifacts/grounding-v5-d59-replication-results/confirmatory-analysis.json) (62 vs 20), and [parser sensitivity](../artifacts/grounding-v5-d59-action-extraction-results/sensitivity-analysis.json) (132 vs 20) of 192 pairs; magnitude unresolved; no pre-registered confirmation, OS-enforced isolation claim, or model ranking. Five timeout usages, the lost D5.9 journals, and single-disk retention remain known limitations. |
docs/evidence-index.md:29:| What did the D5.9 Haiku execution record? | The [generated report](../artifacts/grounding-v5-d59-haiku-results/report.md) and [per-assignment projection](../artifacts/grounding-v5-d59-haiku-results/report.json) retain all 432 outcomes, with primary and reliability cases separated. The [authorization history](../artifacts/grounding-v5-d59-haiku-results/authorization-history.json) discloses the owner-approved continuations after malformed output and exhausted connection resets. Prior failures, reservations, and the original runtime deadline were retained. Raw journals remain local; D5.10 and public model-quality claims remain human-owned. |
docs/evidence-index.md:30:| What does the D5.9 confirmatory analysis show? | The [confirmatory analysis](../artifacts/grounding-v5-d59-haiku-results/confirmatory-analysis.md) and its [JSON source](../artifacts/grounding-v5-d59-haiku-results/confirmatory-analysis.json) are generated by `scripts/analyze_grounding_v5_d59.py` from the public projection and the bound execution plan. They report the pre-registered paired McNemar test and family-stratified logical-cluster bootstrap for history minus stateless on the primary phase, the reliability subset separately, and labelled exploratory rates, termination profiles, and resource use. They name the executed policy IDs read from the bound execution plan beside the pre-registered IDs, which differ. Diagnostics that need the private journals are listed as not computable. D5.10 is the owner's review. |
docs/evidence-index.md:32:| Where is the completed replication prepared for D5.10 review? | Start with the [review guide](../artifacts/grounding-v5-d59-replication-results/review-guide.md), [generated analysis](../artifacts/grounding-v5-d59-replication-results/confirmatory-analysis.md), and [raw command index](../artifacts/grounding-v5-d510/c398557ca05b8cc87d2f4371fe6b1335d427e5c5/REPORT.md). The response-free full projection retains all 432 assignments; the operational pilot remains separate. The package discloses the unresolved confirmed-stopped timeout, incomplete provider usage, disagreement with historical D5.9, undefined secondary estimators, and two archive copies on the same local disk. Historical full-gate observations remain at their recorded revision; this supplement adds the offline replication audit and targeted regression checks, not a fresh full-gate run or a D5.10 verdict. A separately labelled exploratory [parse-failure signature](../artifacts/grounding-v5-d59-replication-results/prose-signature-diagnostic.md), regenerated read-only from the retained journal by `scripts/analyze_grounding_v5_d59_replication_prose_signature.py`, publishes counts only and is never pooled with the primary result. |
```

## Raw command inventory

Each JSON record stores the exact argv/command, cwd, public environment, UTC timestamps, process runtime, exit status, and complete combined output. Failed and skipped observations remain in the inventory.

| Record | Exit | Runtime (s) | Parsed pytest summary |
| --- | ---: | ---: | --- |
| `commands/00-git-revision.json` | 0 | 0.012536 | `null` |
| `commands/01-worktree-status.json` | 0 | 0.036662 | `null` |
| `commands/02-rclone-version.json` | 0 | 0.065761 | `null` |
| `commands/03-object-lock-readback.json` | 0 | 1.072357 | `null` |
| `commands/04-downloaded-vs-local-extraction.json` | 0 | 21.620249 | `null` |
| `commands/05-downloaded-vs-local-replication.json` | 0 | 14.769418 | `null` |
| `commands/06-offhost-restore-drill-extraction.json` | 0 | 230.606248 | `null` |
| `commands/07-offhost-restore-replication-full.json` | 0 | 16.565195 | `null` |
| `commands/08-offhost-restore-replication-pilot.json` | 0 | 0.56672 | `null` |
| `commands/09-replication-audit-verify-offhost.json` | 1 | 0.534095 | `null` |
| `commands/10-public-wording-inventory.json` | 0 | 0.006549 | `null` |
| `commands/11-worktree-status-after.json` | 0 | 0.029013 | `null` |
| `commands/12-evidence-branch.json` | 0 | 0.015268 | `null` |
| `commands/13-checklist-source.json` | 0 | 0.026603 | `null` |
| `commands/14-replication-audit-verify-local-control.json` | 1 | 0.865945 | `null` |

## Integrity and redaction indexes

`evidence-manifest.json` lists the SHA-256 and size of every other file in this directory and of each referenced repository artifact. `redaction-scan.json` lists the prohibited-pattern counts for every file in this directory except itself.
