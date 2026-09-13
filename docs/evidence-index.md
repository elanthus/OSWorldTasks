# Evidence index

Use the README for the implementation and headline measured claims. This index routes reviewers to
current evidence, historical records, withdrawals, residual risks, and public-verification limits.
Reports summarize stored evidence; reading or regenerating them does not authorize provider calls.

| Question | Evidence and limits |
|---|---|
| What establishes the environment contract? | [Validation JSON](../artifacts/validation-report.json), [generated report](../artifacts/validation-report.md), and [reward-hacking audit](../artifacts/day-2-rev-2026-09-06-issues-95-101/raw/reward-hacking.json). |
| What are the core environment-validation counts? | [Fake-reset evidence](../artifacts/day-2/raw/fake-reset.json) records 10 deterministic resets; [reward-timing evidence](../artifacts/day-2/raw/reward-timing.json) records 122/122 passing trajectories; and [space-integrity evidence](../artifacts/day-2/raw/space-integrity.json) records the Gymnasium checker and 500 sampled actions. These measurements cover the synthetic vendor form, not general desktop tasks. |
| What changed in the visual evidence? | [Revision report](../artifacts/day-2-rev-2026-09-06-issues-95-101/validation-report.json) and [raw renderer comparisons](../artifacts/day-2-rev-2026-09-06-issues-95-101/raw/renderer-screenshot-differences.json) retain intermediate-readiness and historical differences. One-host bitwise evidence does not establish portability. |
| What does the grounding improvement measure? | [Frozen results](../artifacts/grounding-results.json), [canonical report](../artifacts/grounding-report.md), [detailed set-of-marks analysis](../artifacts/grounding-set-of-marks-analysis.md), and [report provenance](../artifacts/grounding-report-provenance-v1.json). V1 aliases target with state; [unrun v2](../artifacts/grounding-v2-manifest.json) crosses them. The moving model alias is not an immutable model snapshot. |
| What are the separate Haiku/Gemini results? | The [historical v3 report](../artifacts/grounding-v3-haiku-gemini-report.md) retains the prompt-v2 experiments, transport confound, calibration coverage limits, and original-revision provenance. It is not evidence for the canonical `gpt-5.4-mini` headline. |
| What did the platform demonstrate? | [Scripted fan-out](../artifacts/platform/seed-policy-fanout-evidence-v1.json), [lifecycle demo](../artifacts/platform/demo-script.md), [redacted API transcript](../artifacts/platform/demo-api-transcript.jsonl), [D4.11 rehearsal](../artifacts/platform/d4.11/78c801c514a83d74111da14ffef89714427570ec/EVIDENCE_REVIEW.md), and [owner D4.12 record](../artifacts/platform/human-gate.json). Synthetic orchestration evidence, not production or model-quality evidence. |
| What platform risks remain? | [Known limitations](../artifacts/platform/known-limitations.md) records the single-reviewer local scope, synthetic provider, non-production WORM posture, single-form generalization limit, and serving/proposal gaps. |
| Which owner gate records are retained? | [D1.8](../artifacts/day-1/human-gate.json), the [historical D2.11 record](../artifacts/day-2/raw/human-gate.json), the [re-graded D2.11 revision](../artifacts/day-2-rev-2026-09-06-issues-95-101/raw/human-gate.json), [D3.11](../artifacts/day-3/raw/human-gate.json), and [D4.12](../artifacts/platform/human-gate.json). Each decision remains limited to its recorded evidence and revision. |
| What did the transport repair show? | [Reliability diagnostic](../artifacts/grounding-v5-d58-reliable-diagnostic/report.md), [verification receipt](../artifacts/grounding-v5-d58-reliable-diagnostic/verification.json), and [repair design](../plans/grounding-v5-d58-reliable-transport.md) record ten actions from ten calls, no network errors or retries, and USD 0.099531750 in new charges. These supplied-state checks add no end-to-end calibration observations. |
| What happened in the latest calibration? | [Final continuation report](../artifacts/grounding-v5-d58-owner-budget-continuation/report.md), [verification receipt](../artifacts/grounding-v5-d58-owner-budget-continuation/verification.json), and [decision package](../plans/grounding-v5-d58-final-design.md) record all 100 assignments, including ten earlier failures: history 42/50 terminal successes and stateless 6/50, with both consumers reached on 43/50 and 45/50. The final history episode was cut short at the authorized six-hour limit. Confirmed aggregate charges were USD 23.978227275 under the USD 28 cap; unresolved holds carry owner-authorized zero budget weight. The owner accepted the observed difficulty as an exception to the proposed 40/50 ceiling. |
| Can the reserved final sample meet the memory power target? | The [prospective power and cost check](../artifacts/grounding-v5-d58-final-design/power.md) gives 71.4% power for 120 independent pairs at the agreed 20-point difference. The proposed 168-pair design gives 85.9% at observed discordance and 80.3% at the upper sensitivity endpoint, with roughly USD 109 aggregate projected cost including reliability repeats. [Sample size and the proposed USD 120 planning cap](../artifacts/grounding-v5-d58-final-design/decision.json) await owner selection. Confirmatory tasks and paid calls remain disabled. |
| What happened before the focus repair? | [Earlier Gemini 3.8 report](../artifacts/grounding-v5-d58-gemini38-calibration/report.md) and [verification receipt](../artifacts/grounding-v5-d58-gemini38-calibration/verification.json) preserve the separate cohort stopped at 51/100 episodes, including the stateless text-entry floor and account reconciliation. |
| Which D5.8 review corrections supersede historical tooling? | [Review corrections](../plans/grounding-v5-d58-review-corrections.md) separate the pilot's 20 wire requests from zero analysis calls, derive report denominators from stored rows, and provide a verifier that remains effective under `python -O`. Original bundles and hashes are preserved. |
| What did the focus and timeout repair diagnostic show? | [Twenty-call report](../artifacts/grounding-v5-d58-focus-diagnostic/report.md), [journal verification](../artifacts/grounding-v5-d58-focus-diagnostic/verification.json), and [repair design](../plans/grounding-v5-d58-focus-diagnostic.md) record 6/6 desired text transitions and 3/4 correct memory choices in each mode, including one zero-charge empty response. Supplied prefixes do not establish end-to-end memory exposure. |
| What happened in the D5.6 supplemental calibration? | [Generated supplement](../artifacts/grounding-v5-calibration-supplement/report.md) publishes Mistral and the matched Qwen stateful/stateless pair, with task outcomes, accounting, source hashes, and local audit receipts. Public verification reproduces the derivative without restricted journals. |
| What happened in historical v5 calibration? | [Generated calibration report](../artifacts/grounding-v5-d56-completed-calibrations-report.md) and [structured derivative](../artifacts/grounding-v5-d56-completed-calibrations-publishable.json) retain completed and negative results, incomplete runs, policy identities, spend and unknown-charge reservations, dates, taxonomy, and predecessor disclosures. |
| What can a public clone verify? | [Integrity audit](../artifacts/grounding-v5-d56-completed-calibrations-integrity-audit.json), [publication relation](../artifacts/grounding-v5-d56-completed-calibrations-publication-relation.json), and [errata](../artifacts/grounding-v5-d56-gemini-qwen-v3-calibration-evidence-errata.json). Aggregate bindings are published; restricted journals, raw provider responses, screenshots, and private checkpoints remain `must_not_commit`. Journal file hashes and row-level contents cannot be verified from a public clone. |
| What does the release inventory cover? | The revision-bound [public-release inventory](../artifacts/public-release-inventory.json) records tracked files, links, licensing, redaction, and reachable-history findings for its measured candidate. The [release-review record](../artifacts/public-release/release-review-2026-09-12.md) states its snapshot and post-submission limits; the [unticked checklist](../plans/public-release-checklist.md) retains the final owner review and refresh steps. |

## Withdrawn and historical work

The earlier `A-gemini-stateful-v2` calibration remains withdrawn. Its evidence and reports were
removed rather than corrected in place because absolute operator paths occurred in digest-bound
plans and summaries. Removing the paths would invalidate the recorded hashes. It is not repaired
or included in the retained calibration result. The producer-side fix is in
[`evidence.py`](../pixelgym/grounding/v5/evidence.py); the historical withdrawal disclosure is in
`README.md` at revision `06d695a327b02cb83e22f25cc47407d0b344bff5`.

The older incomplete [Qwen report](../artifacts/grounding-v5-d56-qwen-full-calibration-report.md)
remains available. The completed-calibration report preserves the separate Gemini ledger stop,
HTTP infrastructure predecessor, and CLI-versus-HTTP fault-taxonomy disclosure. Stored labels are
not rewritten. These runs do not constitute a completed confirmatory benchmark or a v5 gate.

Superseded v3/v4 calibration apps and v5 D5.6 experiment drivers remain reproducible at the
`legacy-grounding-final` tag. Their removal from the current tree does not remove their frozen
evidence or turn calibration into a benchmark score.

## Reproduce and review

- Use the [artifacts map](../artifacts/README.md) to locate a file by claim area.

Use the [grounding verification guide](grounding-verification.md) for the read-only headline check.
Follow the [reproduction guide](reproduction.md) for local checks, capture, and optional OSWorld.
Use the [public release checklist](../plans/public-release-checklist.md) for inventory and privacy
review. Public wording, publication approval, and any new milestone verdict remain owner decisions.
