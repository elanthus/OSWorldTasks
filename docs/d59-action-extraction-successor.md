# D5.9 action-extraction successor: owner review

The owner approved this exact candidate on 2026-10-23; the durable decision and
phase limits are in [`owner-direction.json`](../artifacts/grounding-v5-d59-action-extraction-successor/owner-direction.json).
Pilot execution is authorized, and full execution is authorized only after the
operational pilot gate passes; task failures are allowed. The original candidate
bytes and digest stay unchanged, with execution authority in separate phase records.

This was prepared as a disabled candidate for a **post-hoc parser-sensitivity study on
previously inspected tasks**. It does not replace the completed replication or
provide an independent confirmatory result. Freezing or executing it requires the
owner's approval of its exact digest, now recorded above. No model calls were made during preparation.

The candidate and generated field inventory live in
[`artifacts/grounding-v5-d59-action-extraction-successor/`](../artifacts/grounding-v5-d59-action-extraction-successor/).
The executed replication plan remains
`sha256:ce6776def9066d2c3b4a1b2701b25567dc182364dfa3cf694b4044d865d59159`.
Its results, pilot evidence and unresolved timeout retain their original disposition.
The other agent's current-results analysis is outside this change.

## Offline diagnostic

`diagnose_grounding_v5_d59_action_extraction.py` opens the retained attempt journal
read-only, matches the v2 event-chain digest and event/object counts against the
original audited report, verifies each canonical-response object's digest, and compares the
original parser with the opt-in adapter. A quiescent journal without an existing WAL
uses SQLite immutable mode to avoid creating sidecars. The script never launches a
CLI, authenticates, advances an environment, or changes a recorded outcome.

The generated `offline-diagnostic.json` contains hashes, trial identities, parser
decisions and counts, without response text or action coordinates. It records:

| Observation | Count |
| --- | ---: |
| Retained canonical responses examined | 8,759 |
| Previously valid actions, unchanged by the adapter | 8,572 |
| Original terminal parse failures | 187 |
| Failed responses with a valid fenced body under original schema and bounds | 186 |
| Failed responses accepted by the conservative successor adapter | 185 |
| Failed responses still rejected | 2 |

The difference between 186 valid bodies and 185 accepted responses is deliberate:
`d59-rep-primary-6111-history-r0`, step 20, has bracketed content outside its fence.
The adapter rejects that as ambiguous. It does not make a case-specific exception.
`d59-rep-primary-6032-stateless-r0`, step 3, still violates the exact action schema.

Recovering one action is not evidence that the episode would finish successfully.
The original 157 step-limit outcomes remain. No continuation responses exist after
terminal parse failures, and the diagnostic does not estimate an end-to-end success
rate. The original confirmed-stopped timeout still has unknown provider completion
and usage; extraction does not resolve it or remove its counted retry.

## Accepted envelope and evidence

The new parser version is
`pixelgym-agent-v5-claude-single-fenced-action-parser-v1`. The existing parser remains
the default. The successor accepts either bare JSON, or exactly one complete
unlabeled/`json` fence with optional surrounding prose. It rejects:

- extra, malformed, differently labeled or longer fences;
- braces, brackets or backticks outside the fence, including alternative objects;
- duplicate, missing or extra action fields, non-integer values, and out-of-range
  values in every field, including fields inactive for the selected action type.

It never searches for an arbitrary brace-delimited substring, coerces values,
repairs a schema, invokes a repair model, or retries a format failure. Bare JSON and
whole-fence responses still receive the original decoder's duplicate-key checks.

`ClaudeCodePolicy.parse` checks the exact model, runtime route, command contract,
accounting, effort and policy-violation status **before** invoking the adapter.
The existing stream parser, credential-output rejection, kernel sandbox, egress
proxy, same-credential Max verification and authentication recovery remain in
place. The new manifest binds the adapter and action-validator sources and changes
the parser identity and policy IDs. Its sandbox runtime digest changes to bind the
new source, while its enforcement contract remains identical.

`ExtractionMemoryRunner` writes an `action_extraction` event before the inherited
candidate/dispatch boundary. The receipt binds the unchanged canonical-response
hash, parser/adapter versions, accept/reject decision, selected character span and
content hash when available, and accepted candidate hash. It contains no prose.
Rejected candidates remain terminal invalid outputs with no format retry. Failure
to retain the receipt prevents normal continuation.

## Candidate and approval boundary at preparation

The successor has its own artifact directory and `d59-extract-` trial namespace.
Its plan binds the original completed replication and response-free report by hash.
`plan-field-changes.json` lists every changed field as a JSON pointer with old/new
value digests; arrays are treated as whole fields. No original artifact is edited.

The changed fields describe the new schema/status, preparation-only owner direction,
source binding, policy manifests, predecessor/relationship, extraction protocol,
post-hoc reporting rule, and assignment namespaces. Caps, allocation, statistical
mechanics, model, prompt, route, stop rule, retry budgets, sandbox enforcement and
OAuth-login clock handling are preserved. The preserved statistics are sensitivity
analysis mechanics, not a fresh pre-registration after inspecting the task outcomes.

| Proposed phase | Assignments | Action cap | Attempt / wire cap | Control cap | Active window |
| --- | ---: | ---: | ---: | ---: | ---: |
| Pilot | 4 | 104 | 208 / 208 | 0 | 6 hours |
| Full, separately approved after the pilot | 432 | 12,134 | 24,268 / 24,268 | 0 | 168 hours |

Only drained owner-login waits pause the active clock. Existing bounded retry and
shared-ledger stops apply. The public candidate remains `execution_enabled: false`,
`subscription_execution_authorized: false`, with both approved caps zero. No owner
approval was written during preparation; the subsequent owner approval is linked above. Prior campaign approvals cannot
validate against its new digest. D5.10 remains a human decision.

To rebuild or verify **without CLI or network calls**, use the source revision in
the prepared plan (not an arbitrary newer HEAD):

```sh
.venv/bin/python -m scripts.prepare_grounding_v5_d59_extraction \
  --source-revision <source_binding.source_revision> --verify
```

The shared runner accepts the successor only through an explicit `--plan` path.
It regenerates the exact source-bound candidate and validates a separate exact
phase approval before any authentication or sandbox probe. Future execution also
requires fresh live identity and sandbox checks. Preparation here does not run those
checks or authorize either phase. A fresh held-out confirmatory workload would need
a separate design decision rather than a change hidden in this parser successor.

## Approved phase activation

The 2026-10-23 owner decision authorizes the listed pilot caps and window and,
conditionally, the listed full caps and window. The full record is activated only
after four completed pilot assignments satisfy operational checks: no campaign
guard stop or infrastructure/request failure; strict identity/sandbox bindings;
valid accounting and extraction receipts; closed subprocesses; and verified archive
and backup. Task failures, including invalid outputs, remain in the denominator.

An expired OAuth credential pauses dispatch for the owner to renew it through the
hidden-input helper. Renewal never clears an unresolved-ledger block or resets caps.
Original replication evidence and the D5.10 human decision remain separate.

## Completed sensitivity campaign

The approved pilot and all 432 full assignments completed. The
[separate D5.10 review package](../artifacts/grounding-v5-d59-action-extraction-results/review-guide.md)
contains the frozen statistical calculations, read-only evidence audit and five
individually disclosed unresolved timeout attempts. Original results and this
candidate remain unchanged. This is post-hoc sensitivity on inspected tasks;
the D5.10 verdict and any public wording remain human-owned.
