# D5.9 Haiku zero-API-retry execution

**Status:** authorized on 2026-09-23 for the exact successor plan and awaiting execution.

The owner approved successor digest
`sha256:c123aad69824e2ec352fd1751fafd762e43a0f2697cdd00e4521efe6509d5cf7`,
12,134 environment actions, 24,268 model attempts, 24,268 provider wire requests, zero
provider control requests, and a 168-hour subscription execution window. The checked-in
[approval receipt](../artifacts/grounding-v5-d59-haiku-api-retry-execution/owner-approval.json)
records that decision without changing the immutable
[successor candidate](../artifacts/grounding-v5-d59-haiku-api-retry-successor/execution-plan.json).

The runner validates the candidate digest, approval receipt, phase and aggregate caps, task
assignments, runtime identity, and both live policy manifests before it creates an evidence
directory or makes a provider call. It refuses a pre-existing attempt or invocation journal, so
an interrupted campaign cannot be restarted or overwritten. Any unauthorized Claude CLI API
retry remains a fail-closed policy violation.

The approved OS-sandbox exception is unchanged. The run cannot support an OS-enforced policy
isolation claim. The D5.10 verdict and public model-quality or security claims remain human-owned.

## Execution boundary

The campaign must use a new evidence directory and the committed execution source. Stop on the
first nonterminal benchmark classification, blocked invocation ledger, runtime-window exhaustion,
or execution error. Preserve the summary, attempt journal, invocation journal, binding, plan, and
approval at the retained state; do not replay completed or in-progress assignments.
