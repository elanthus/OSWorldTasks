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
