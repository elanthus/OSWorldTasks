# Evidence index

Use the README for the implementation and measured claims. These records retain the audit detail.
Reports summarize stored evidence; reading or regenerating them does not authorize provider calls.

| Question | Evidence and limits |
|---|---|
| What establishes the environment contract? | [Validation JSON](../artifacts/validation-report.json), [generated report](../artifacts/validation-report.md), and [reward-hacking audit](../artifacts/day-2-rev-2026-09-06-issues-95-101/raw/reward-hacking.json). |
| What changed in the visual evidence? | [Revision report](../artifacts/day-2-rev-2026-09-06-issues-95-101/validation-report.json) and [raw renderer comparisons](../artifacts/day-2-rev-2026-09-06-issues-95-101/raw/renderer-screenshot-differences.json) retain intermediate-readiness and historical differences. One-host bitwise evidence does not establish portability. |
| What does the grounding improvement measure? | [Frozen results](../artifacts/grounding-results.json), [canonical analysis](../artifacts/grounding-report.md), and [report provenance](../artifacts/grounding-report-provenance-v1.json). V1 aliases target with state; [unrun v2](../artifacts/grounding-v2-manifest.json) crosses them. The moving model alias is not an immutable model snapshot. |
| What are the separate Haiku/Gemini results? | The [historical v3 report](../artifacts/grounding-v3-haiku-gemini-report.md) retains the prompt-v2 experiments, transport confound, calibration coverage limits, and original-revision provenance. It is not evidence for the canonical `gpt-5.4-mini` headline. |
| What did the platform demonstrate? | [Scripted fan-out](../artifacts/platform/seed-policy-fanout-evidence-v1.json), [D4.11 rehearsal](../artifacts/platform/d4.11/78c801c514a83d74111da14ffef89714427570ec/EVIDENCE_REVIEW.md), and [owner D4.12 record](../artifacts/platform/human-gate.json). Synthetic orchestration evidence, not production or model-quality evidence. |
| What happened in the latest calibration? | [Continuation report](../artifacts/grounding-v5-d58-focus-continuation/report.md), [verification receipt](../artifacts/grounding-v5-d58-focus-continuation/verification.json), and [decision package](../plans/grounding-v5-d58-final-design.md) retain ten infrastructure-failed episodes, 90 unrun assignments, and three stateless episodes reaching both memory consumers. The owner increased aggregate authorization to USD 28; transport failures stopped the continuation. No final evaluation approval is claimed. |
| What happened before the focus repair? | [Earlier Gemini 3.8 report](../artifacts/grounding-v5-d58-gemini38-calibration/report.md) and [verification receipt](../artifacts/grounding-v5-d58-gemini38-calibration/verification.json) preserve the separate cohort stopped at 51/100 episodes, including the stateless text-entry floor and account reconciliation. |
| What did the focus and timeout repair diagnostic show? | [Twenty-call report](../artifacts/grounding-v5-d58-focus-diagnostic/report.md), [journal verification](../artifacts/grounding-v5-d58-focus-diagnostic/verification.json), and [repair design](../plans/grounding-v5-d58-focus-diagnostic.md) record 6/6 desired text transitions and 3/4 correct memory choices in each mode, including one zero-charge empty response. Supplied prefixes do not establish end-to-end memory exposure. |
| What happened in the D5.6 supplemental calibration? | [Generated supplement](../artifacts/grounding-v5-calibration-supplement/report.md) publishes Mistral and the matched Qwen stateful/stateless pair, with task outcomes, accounting, source hashes, and local audit receipts. Public verification reproduces the derivative without restricted journals. |
| What happened in historical v5 calibration? | [Generated calibration report](../artifacts/grounding-v5-d56-completed-calibrations-report.md) and [structured derivative](../artifacts/grounding-v5-d56-completed-calibrations-publishable.json) retain completed and negative results, incomplete runs, policy identities, spend and unknown-charge reservations, dates, taxonomy, and predecessor disclosures. |
| What can a public clone verify? | [Integrity audit](../artifacts/grounding-v5-d56-completed-calibrations-integrity-audit.json), [publication relation](../artifacts/grounding-v5-d56-completed-calibrations-publication-relation.json), and [errata](../artifacts/grounding-v5-d56-gemini-qwen-v3-calibration-evidence-errata.json). Aggregate bindings are published; restricted journals, raw provider responses, screenshots, and private checkpoints remain `must_not_commit`. Journal file hashes and row-level contents cannot be verified from a public clone. |

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

Use the [grounding verification guide](grounding-verification.md) for the read-only headline check.
Follow the [reproduction guide](reproduction.md) for local checks, capture, and optional OSWorld.
Use the [public release checklist](../plans/public-release-checklist.md) for inventory and privacy
review. Public wording, publication approval, and any new milestone verdict remain owner decisions.
