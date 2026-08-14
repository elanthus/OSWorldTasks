# Reviewer lifecycle demo script

1. Start the local stack using `deploy/README.md`. Confirm the UI labels all runs as synthetic.
2. Submit baseline prompt v1. Open its candidate page and observe stored accuracy, cost, and p95
   latency together. Confirm the failed accuracy gate removes the approval control.
3. Attempt the documented direct approval API request for candidate A; retain the blocked response.
4. Submit revised prompt v2 and compare it with candidate A. Confirm the dataset fingerprint,
   scorer, and target semantics are compatible and all three metrics remain visible.
5. Inspect candidate B's immutable policy ID and gate report. As `local-reviewer`, enter an approval
   reason. Confirm this changes `Eligible` to `Approved` without deploying it.
6. Deploy B with a separate reason. Call `/api/v1/policy`, then ground one frozen screenshot. Capture
   the API schema, policy ID, deployment ID, exact policy version, and provider request ID.
7. To rehearse rollback, first ensure an earlier eligible policy was separately approved and
   deployed. Activate the newer approved version, then use Rollback. Confirm `/api/v1` is unchanged
   while the returned exact policy and deployment IDs change.
8. Open the audit trail and MLflow run. Run `scripts/verify_immutable_artifacts.py` and retain its
   raw output. Export stored evidence with `scripts/export_platform_evidence.py`.

Do not treat scripted accuracy, zero cost, or synthetic latency as evidence about a real model.
Public wording, paid calls, external deployment, and the milestone verdict remain separate human
gates.
