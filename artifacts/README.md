# Evidence store

This directory is append-only since `a07069d4dfefe2ff05c1cb6d865053e8682925c3`; earlier
withdrawals are listed in the [evidence index](../docs/evidence-index.md#withdrawn-and-historical-work).
Recorded SHA-256 digests and provenance files bind file paths. The canonical evidence for public claims is the set of files
linked by the [root README](../README.md). Everything else is a historical revision, a superseded
capture, a calibration campaign, or a release record, retained on purpose. Use the
[evidence index](../docs/evidence-index.md) for context and public-verification limits.

## Canonical evidence cited by the README

| Claim area | File | What it is |
|---|---|---|
| Environment validation | [day-2-rev-2026-09-06-issues-95-101/raw/real-reset.json](day-2-rev-2026-09-06-issues-95-101/raw/real-reset.json) | Real OSWorld reset evidence |
| Environment validation | [day-2-rev-2026-09-06-issues-95-101/validation-report.json](day-2-rev-2026-09-06-issues-95-101/validation-report.json) | Revision environment record |
| Environment validation | [day-2-rev-2026-09-06-issues-95-101/raw/reward-hacking.json](day-2-rev-2026-09-06-issues-95-101/raw/reward-hacking.json) | Reward-hacking audit |
| Environment validation | [day-2/raw/real-reset.json](day-2/raw/real-reset.json) | Historical reset evidence |
| Environment validation | [validation-report.json](validation-report.json) | Superseded 1920x1080 validation run (with its [generated report](validation-report.md)); the current record is the [revision report](day-2-rev-2026-09-06-issues-95-101/validation-report.md) |
| Environment validation | [day-2-rev-2026-09-06-issues-95-101/raw/renderer-screenshot-differences.json](day-2-rev-2026-09-06-issues-95-101/raw/renderer-screenshot-differences.json) | Visual differences |
| Grounding v1 experiment | [grounding-protocol.md](grounding-protocol.md) | Protocol and leakage controls |
| Grounding v1 experiment | [grounding-report.md](grounding-report.md) | Canonical report |
| Grounding v1 experiment | [grounding-results.json](grounding-results.json) | Structured results |
| Grounding v1 experiment | [grounding-report-provenance-v1.json](grounding-report-provenance-v1.json) | Recorded provider, prompt, data, and collection window |
| Grounding v2 design (not run) | [grounding-v2-protocol.md](grounding-v2-protocol.md) | Crossed allocation protocol |
| Grounding v2 design (not run) | [grounding-v2-manifest.json](grounding-v2-manifest.json) | Crossed allocation manifest |
| Platform | [platform/architecture.md](platform/architecture.md) | Platform architecture |
| Platform | [platform/seed-policy-fanout-evidence-v1.json](platform/seed-policy-fanout-evidence-v1.json) | Synthetic orchestration fixtures |
| Owner gate records | [day-1/human-gate.json](day-1/human-gate.json) | Owner-recorded D1.8 decision |
| Owner gate records | [day-2-rev-2026-09-06-issues-95-101/raw/human-gate.json](day-2-rev-2026-09-06-issues-95-101/raw/human-gate.json) | Owner-recorded D2.11 decision |
| Owner gate records | [day-3/raw/human-gate.json](day-3/raw/human-gate.json) | Owner-recorded D3.11 decision |
| Owner gate records | [platform/human-gate.json](platform/human-gate.json) | Owner-recorded D4.12 decision |
| Demo media | [day-3/review/real-osworld-episode.gif](day-3/review/real-osworld-episode.gif) | Real OSWorld episode |
| Demo media | [grounding/figures/raw-vs-marks-accuracy.png](grounding/figures/raw-vs-marks-accuracy.png) | Raw-coordinate versus set-of-marks accuracy |

## Historical revisions

- [day-1/](day-1/) — retained historical record.
- [day-1-revision-2-layout-equivalence/](day-1-revision-2-layout-equivalence/) — retained historical record.
- [day-1-revision-3-layout-equivalence/](day-1-revision-3-layout-equivalence/) — retained historical record.
- [day-2/](day-2/) — retained historical record.
- [day-2-rev-2026-09-04-issues-95-101/](day-2-rev-2026-09-04-issues-95-101/) — retained historical record.
- [day-2-rev-2026-09-06-issues-95-101/](day-2-rev-2026-09-06-issues-95-101/) — retained historical record.
- [day-3/](day-3/) — retained historical record.

## Grounding calibration campaigns

These are calibration and pilot records, not headline results, and none carries a milestone gate.
Counts are top-level entries before this map was added; status phrases are copied from the
[evidence index](../docs/evidence-index.md) or the linked historical report.

| Prefix | Top-level entries | Design record | Status |
|---|---|---|---|
| `grounding-v3*` | 48 | [Historical v3 report](grounding-v3-haiku-gemini-report.md) | Historical, non-canonical evidence. |
| `grounding-v4-pilot*` | 9 | — | frozen evidence |
| `grounding-v4b*` | 20 | — | frozen evidence |
| `grounding-v4c*` | 26 | — | frozen evidence |
| `grounding-v5*` | 70 | [v5 benchmark design](../docs/v5-benchmark-design.md) | These runs do not constitute a completed confirmatory benchmark or a v5 gate. |

The D5.9 Haiku freeze successor's
[owner exception](grounding-v5-d59-haiku-freeze/owner-exception.json) and
[execution plan](grounding-v5-d59-haiku-freeze/execution-plan.json) record the narrow
OS-sandbox exception and proposed limits. They do not authorize a model call: execution is
disabled and the approved attempt and wire-request caps are zero.

The [D5.9 pre-registration record](grounding-v5-d59-freeze/analysis-plan.md) keeps the
frozen analysis plan and stop rules beside the D5.9 evidence.

**Naming conventions**

- `-pre-review`: the original v4b Luna pilot collection, kept after review found an inconsistent amount in two captured triage scenarios; the canonical files are the corrected rerun (commit `1da5d6e`).
- `-errata`: correct interpretation of immutable evidence without rewriting approved plans or run summaries; see the [calibration errata](grounding-v5-d56-gemini-qwen-v3-calibration-evidence-errata.json).
- `-vN` suffixes: retained; see the file header.
- `SUPERSEDED-SOURCES`: records changed layout sources while retaining the frozen earlier capture; see the [source notice](grounding-capture.SUPERSEDED-SOURCES.md).
- `luna` / `terra`: model codenames in [codex_cli_policy.py](../pixelgym/grounding/v5/codex_cli_policy.py), not hosts.

## Release and process records

- [public-release/](public-release/) — release-review records; [latest inventory and privacy refresh](public-release/release-review-2026-09-12.md).
- [public-release-inventory.json](public-release-inventory.json) — revision-bound tracked-file, link, licensing, redaction, and reachable-history inventory.
- [agent-assisted-workflow-history.json](agent-assisted-workflow-history.json) — agent-assisted workflow history.

## How to verify

Run `.venv/bin/python scripts/verify_grounding_report.py` from the repository root to recompute the
headline and verify stored hashes without changing tracked files.

`.venv/bin/python scripts/verify_immutable_artifacts.py` is the entry point for verifying pinned
candidate artifacts without parsing, scoring, or calling a provider; it requires `--database` and
storage configuration (`--local-root` or `PIXELGYM_IMMUTABLE_BUCKET`), with invocation details
available through `--help`.
