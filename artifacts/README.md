# Evidence store

This directory is append-only: recorded SHA-256 digests and provenance files bind file paths, so
nothing here is moved or renamed. The canonical evidence for public claims is the set of files
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
| Environment validation | [validation-report.json](validation-report.json) | Environment contract validation report |
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
| Portfolio wording | [resume-bullets-v2-review.md](resume-bullets-v2-review.md) | Qualified resume-bullet review candidate |
| Portfolio wording | [resume-bullets.md](resume-bullets.md) | Digest-bound historical approved artifact |

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

| Prefix | Top-level entries | Plan | Status |
|---|---|---|---|
| `grounding-v3*` | 48 | [Historical v3 report](grounding-v3-haiku-gemini-report.md) | Historical, non-canonical evidence. |
| `grounding-v4-pilot*` | 9 | [v4 pilot plan](../plans/grounding-v4-pilot.md) | frozen evidence |
| `grounding-v4b*` | 20 | [v4b multistep pilot plan](../plans/grounding-v4b-multistep-pilot.md) | frozen evidence |
| `grounding-v4c*` | 26 | [v4c longer-horizon pilot plan](../plans/grounding-v4c-longer-horizon-pilot.md) | frozen evidence |
| `grounding-v5*` | 70 | [v5 agent benchmark plan](../plans/grounding-v5-agent-benchmark.md) | These runs do not constitute a completed confirmatory benchmark or a v5 gate. |

**Naming conventions**

- `-pre-review`: retained; see the file header.
- `-errata`: correct interpretation of immutable evidence without rewriting approved plans or run summaries; see the [calibration errata](grounding-v5-d56-gemini-qwen-v3-calibration-evidence-errata.json).
- `-invalid-do-not-execute`: retained; see the file header of the [panel smoke plan](grounding-v5-d56-panel-smoke-plan-v2-invalid-do-not-execute.json).
- `-vN` suffixes: retained; see the file header.
- `SUPERSEDED-SOURCES`: records changed layout sources while retaining the frozen earlier capture; see the [source notice](grounding-capture.SUPERSEDED-SOURCES.md).
- `luna` / `terra`: model codenames in [codex_cli_policy.py](../pixelgym/grounding/v5/codex_cli_policy.py), not hosts.

## Release and process records

- [public-release/](public-release/) — release-review records; [latest inventory and privacy refresh](public-release/release-review-2026-09-12.md).
- [public-release-inventory.json](public-release-inventory.json) — revision-bound tracked-file, link, licensing, redaction, and reachable-history inventory.
- [agent-assisted-workflow-history.json](agent-assisted-workflow-history.json) — agent-assisted workflow history.
- [ci-coverage-baseline-issue-114.md](ci-coverage-baseline-issue-114.md) — CI coverage baseline record.

## How to verify

Run `.venv/bin/python scripts/verify_grounding_report.py` from the repository root to recompute the
headline and verify stored hashes without changing tracked files.

`.venv/bin/python scripts/verify_immutable_artifacts.py` is the entry point for verifying pinned
candidate artifacts without parsing, scoring, or calling a provider; it requires `--database` and
storage configuration (`--local-root` or `PIXELGYM_IMMUTABLE_BUCKET`), with invocation details
available through `--help`.
