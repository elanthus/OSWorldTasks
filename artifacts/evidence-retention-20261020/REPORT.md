# Evidence-retention engineering validation

Generated from stored scripted observations. No model was called. This is not D5.9 replication, a model-quality measurement, or a milestone verdict.

| Case | Stored outcome | Scripted sends | Replayed dispatches | Reward sum |
| --- | --- | ---: | ---: | ---: |
| golden | success_termination | 20 | 20 | 1.0 |
| premature_commit | step_limit_truncation | 26 | 26 | 0.0 |
| step_limit | step_limit_truncation | 26 | 26 | 0.0 |
| malformed | invalid_output | 1 | 0 | 0 |
| invalid_action | invalid_output | 1 | 0 | 0 |
| call_cap | call_cap_exceeded | 1 | 1 | 0.0 |
| resume | interrupted_step_recovered | 1 | 1 | 0.0 |

The resume case verifies one interrupted step, recovery with fresh instances, and a second recovery without another dispatch. It is not a completed episode.

Every replay compares stored screenshot bytes, semantic backend checkpoints, reward, termination, and truncation. Coverage is limited to these scripted fake-backend cases.

Restricted journals, screenshots, and captured source are archived outside Git. The public bundle contains projections and digests; regenerate the credential-free campaign to inspect its complete evidence locally. Neither archive replaces the missing historical D5.9 journals.
