# Fresh Haiku Claude Code CLI calibration

The fresh matched calibration completed all 100 assignments: 50 screenshot-history and 50 stateless episodes on the PR196 calibration task panel. It used the exact `claude-haiku-4-5-20251001` model through Claude Code CLI 2.1.267 with default reasoning effort. These are calibration observations, not confirmatory results, a model ranking, or a human milestone verdict.

## Outcomes

| Condition | Episodes | Successes | Step limits | Invalid outputs |
|---|---:|---:|---:|---:|
| history | 50 | 28 | 4 | 18 |
| stateless | 50 | 5 | 41 | 4 |

Every assignment contributes its first terminal outcome. Invalid outputs remain failures; none was discarded or rescored. The matched pairs comprise 0 both-success, 28 history-only, 5 stateless-only, and 17 neither-success outcomes.

## Memory observations

| Condition | Reached both consumers | Correct first choices / attempted | Valid first choices | Actions | Model attempts |
|---|---:|---:|---:|---:|---:|
| history | 36/50 | 67/72 | 70 | 1044 | 1062 |
| stateless | 45/50 | 34/92 | 91 | 1350 | 1354 |

First-choice denominators count observed consumer-choice attempts. Episodes that never reached a consumer remain in the episode denominator but cannot contribute a choice observation. Measurements come from stored host checkpoints and committed dispatches, not model self-reports.

## Execution and caps

The run used 2394 of 2862 allowed environment actions and 2416 of 5724 allowed model attempts. It recorded 2416 of 5724 allowed CLI wire requests and 0 of 0 provider-control requests. All 2416 started CLI processes have terminal response records; unresolved invocations are zero and subprocess closure is recorded.

Elapsed execution time was 9.88 hours within the approved 12-hour window. Incremental experiment charge is recorded as USD 0.00 under the existing Max subscription; that excludes the subscription fee and is not a zero inference-cost claim.

## Provenance and limits

The execution plan digest is `sha256:6b2fa2ccf8ae9f2d5c42666167116a2f6dc6ed00c6b39cce4c0d62bdbdadc476` and the adapter revision is `6128428008d3c40e38851817a98893239a144596`. The task identities exactly match the checked-in PR196 calibration plan and preserve its frozen benchmark revision `1e5d9c0d19acf51505919deefe0d155c2ab22b26`. The fresh cohort reused no prior outcome and exposed no confirmatory task.

The private audit opened both SQLite journals read-only, verified every stored-object and event-chain digest, matched all result events to the sealed summary, recomputed episode measurements, checked success against privileged host dispatches, verified every invocation receipt, and checked the exact caps. It made zero provider calls and did not replay an episode or reinterpret an invalid output.

The checked-in snapshot excludes raw provider responses, prompts, screenshots, checkpoint contents, credentials, private paths, and process IDs. Public verification checks its hash, task-panel binding, response-free derivation, and deterministic report bytes; it cannot repeat the private-journal audit without the retained local journals.

```sh
.venv/bin/python -m scripts.publish_haiku_cli_replication --verify
```

The earlier consolidated PR196 Haiku result combined interrupted and successor CLI/API cohorts. This fresh result uses one Claude Code route and should remain separately identified. No D5.9 execution is authorized, and no human gate is declared here.
