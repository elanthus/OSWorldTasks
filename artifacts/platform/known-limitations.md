# Known limitations

- This is a single-reviewer local control plane, not a multi-tenant service or enterprise identity
  system.
- The scripted provider validates orchestration and governance behavior, not real-model quality,
  cost, or latency.
- Third-party provider aliases can drift when an immutable model snapshot is unavailable.
- One synthetic vendor-form dataset cannot establish general GUI-grounding performance.
- MinIO in Compose demonstrates versioning and Object Lock compatibility; production WORM posture
  still requires approved retention administration, backup, replication, and recovery testing.
- The MVP uses one bounded Metaflow shard. Provider concurrency and paid-call caps require a new
  human approval before expansion.
- The serving v1 boundary supports raw-coordinate policies. A deployable live set-of-marks policy
  would need a target-neutral proposal generator that does not expose build-time boxes.
- Runtime policy activation is process-local in the MVP combined control/serving process. A
  multi-replica deployment requires an authenticated activation channel and readiness-aware router.
