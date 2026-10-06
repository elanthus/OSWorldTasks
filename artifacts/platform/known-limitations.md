# Known limitations

- This is a single-reviewer local control plane, not a multi-tenant service or enterprise identity
  system.
- The scripted provider validates orchestration and governance behavior, not real-model quality,
  cost, or latency.
- Third-party provider aliases can drift when an immutable model snapshot is unavailable.
- One synthetic vendor-form dataset cannot establish general GUI-grounding performance.
- MinIO in Compose demonstrates versioning and Object Lock compatibility; production WORM posture
  still requires approved retention administration, backup, replication, and recovery testing.
- The default 100-example flow uses four bounded Metaflow shards of 25 examples each. Increasing
  provider concurrency or paid-call caps requires a new human approval.
- When enabled by a predeclared policy, the confidence gate uses a one-sided Wilson lower bound
  over every scored record. Invalid parses and request failures remain retained as incorrect
  observations; the bounded synthetic demo policy leaves this optional gate disabled.
- The serving v1 boundary supports raw-coordinate policies. A deployable live set-of-marks policy
  would need a target-neutral proposal generator that does not expose build-time boxes.
- Runtime policy activation is process-local in the MVP combined control/serving process. A
  multi-replica deployment requires an authenticated activation channel and readiness-aware router.
- MLflow mirror reconciliation runs only at process startup; there is no periodic or on-demand
  reconciler. Stale mirror state is visible only as `tracking.reconciliation_required` events in
  the audit timeline, not as a per-candidate status, and a later
  `tracking.reconciliation_resolved` event marks a whole successful pass rather than closing
  individual failures.
- If the post-commit runtime handoff fails despite being total by construction, the process keeps
  serving the previous policy until restart. The divergence is recorded as a `runtime_activation`
  failure event, but readiness does not change and no automatic compensating rollback runs.
- The approved real-provider path admits only the OpenRouter transport, the `raw` condition, and
  no coordinate rescaling; models that emit normalized coordinates cannot be evaluated on it until
  rescaling moves behind the parse boundary. The registry ships empty and no approved provider
  run has been executed.
- Candidate identity includes the stored run summary. A candidate row migrated from a database
  that predates `summary_json` carries the `'{}'` default, so registering the same policy and run
  again with a real summary is rejected with "candidate identity already has different evidence"
  instead of rewriting stored evidence. Re-registering it without a summary stays idempotent.
