# D5.9 counted network retries

The owner requested on 2026-09-23: “I would like retries to be allowed, these are just intermittent
network issues. Only count errors as failures.” The new transport permits one runner-managed retry
for a confirmed-stopped timeout or connection reset, including Claude's synthetic
`API Error: Connection dropped (ECONNRESET)` response. The two cases share one retry budget per
action. A successful retry continues the episode without classifying it as a failure. A second
transport error remains an infrastructure failure. Authentication errors, malformed model output,
and invalid actions are not retried.

Each send has its own durable attempt reservation and invocation record. The retry uses the same
request and policy checkpoint and dispatches no action until a valid response arrives. Both sends
count against the existing two-attempt limit and aggregate 24,268-attempt / 24,268-wire-request
ceilings. Unknown provider completion remains recorded as unknown. Internal Claude retries remain
disabled so they cannot escape this accounting. No response is discarded to improve a score.

This change creates an r3 protocol candidate. It does not alter either stopped campaign, replace
its recorded failure, or resume its assignments. The prompts, tasks, comparison, reliability
schedule, action limit, and OS-sandbox exception are unchanged. The candidate binds the changed
source files, new manifests, and fresh trial IDs; preparing it makes no provider calls.

After committing the source, prepare the candidate:

```sh
.venv/bin/python -m scripts.prepare_grounding_v5_d59_haiku_network_retry \
  --source-revision <full-source-commit>
```

The candidate is stored in `artifacts/grounding-v5-d59-haiku-network-retry/execution-plan.json`.
Use `--verify` to reproduce it from the source checkout. Historical r2 evidence must instead be
reproduced from its recorded source revision; the current sources intentionally differ.

The execution entry point accepts a separate owner approval file binding the new plan digest,
existing caps, 168-hour runtime window, subscription execution, and zero incremental charge cap:

```json
{
  "execution_plan_digest": "<digest printed by preparation>",
  "approved_caps": {
    "environment_action_cap": 12134,
    "model_attempt_cap": 24268,
    "provider_control_request_cap": 0,
    "provider_wire_request_cap": 24268
  },
  "approved_runtime_window_hours": 168,
  "subscription_execution_authorized": true,
  "incremental_experiment_charge_cap_usd": "0.00"
}
```

This example describes the approval shape; it is not an execution approval. Once that exact
campaign is authorized, use a new output directory:

```sh
.venv/bin/python -m scripts.run_grounding_v5_d59_haiku_network_retry prepare \
  --approval <approval.json> --output <fresh-evidence-directory>
.venv/bin/python -m scripts.run_grounding_v5_d59_haiku_network_retry execute \
  --approval <approval.json> --output <fresh-evidence-directory>
```

Preparation validates source, runtime, policy, and approval before calls. Execution refuses existing
journals and preserves the historical fail-stop behavior after an unrecovered error. D5.10 and
public claims remain human-owned. No live model calls were made to implement or test this change.
