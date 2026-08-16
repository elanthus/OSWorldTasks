# D4.11 lifecycle rehearsal script

Target runtime: 2–4 minutes. Everything shown is local, no-cost, and driven by a scripted provider.
Accuracy, cost, and latency in this rehearsal are synthetic governance fixtures, not model-quality
evidence.

1. **Fresh stack and empty history — 15 seconds.** Start the Compose stack from empty volumes at the
   recorded clean Git revision. Open **Runs** and show `No evaluated candidates yet.` Point out the
   footer label: `synthetic metrics are not model-quality evidence`.
2. **Candidate A — 20 seconds.** Run the fixed baseline evaluation using
   `demo-baseline-prompt-v1`. Open its candidate page and show 56% accuracy, $0.00 cost per 100,
   25 ms provider p95, the frozen 100-example dataset, and the `GateFailed` state.
3. **MLflow lineage — 20 seconds.** Open A's or B's linked MLflow run. Show the MLflow run ID,
   Metaflow pathspec tag, exact code revision, dataset fingerprint, prompt version, policy model,
   synthetic-provider tag, metrics, and immutable-artifact index.
4. **Blocked approval — 20 seconds.** Show that candidate A has no approval control. Replay the
   direct approval API request from `demo-api-transcript.jsonl`; it returns HTTP 409 with
   `only an eligible candidate can be approved` and does not create an approval event.
5. **Candidate B — 20 seconds.** Open the fixed revised evaluation. Before discussing its metric,
   point out the on-screen fixture disclosure: B's scripted `revised` responses are derived from
   the frozen Day 3 rows where `condition == "marks"`, then relabeled for this policy's raw-condition
   demonstration. They are not raw-prompt results. Compare A and B and show that the dataset
   fingerprint, scorer, target semantics, and primary metric are compatible. Show B at 100%
   synthetic accuracy, $0.00 cost per 100, 25 ms p95, and `Eligible`—not approved.
6. **Human approval — 20 seconds.** As `local-reviewer`, approve B with the recorded reason. Show
   that the state changes from `Eligible` to `Approved`; no deployment exists yet.
7. **Deploy exact policy — 25 seconds.** Approve and deploy the distinct revised-response rollback
   seed first, then deploy B. The seed exists only to provide an older approved deployment with a
   different immutable policy ID. Show the active B policy and deployment generation in the ledger.
8. **Versioned serving API — 25 seconds.** Replay `/api/v1/policy` and one bounded
   `/api/v1/ground` request using a frozen screenshot. Show API version `v1`, exact B policy ID,
   exact candidate version, deployment ID, provider request ID, and the returned prediction.
9. **Rollback — 20 seconds.** Execute rollback with a separate reason. Replay `/api/v1/policy` and
   show that the API schema/version remains `v1` while the policy ID, exact policy version, and
   deployment ID change back to the earlier approved seed.
10. **Audit and integrity — 25 seconds.** Open **Deployment** and show the append-only approval,
    deploy, and rollback events. Open `demo-mlflow-lineage.jsonl` and the immutable raw-response
    indexes, then show `immutable-artifact-verification.json`: every selected object verifies by its
    pinned version and SHA-256 digest with zero failures.

The reviewer separately confirms the blocked approval, the human-only transition from eligible to
approved, the serving identity changes, and immutable verification. Public wording, paid calls,
external deployment, and the D4.12 milestone verdict remain separate human gates.
