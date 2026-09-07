# Completed D5.6 V5 Calibration Results

**Status:** descriptive completed calibration evidence; not a benchmark score or milestone-gate verdict

This report publishes every retained D5.6 full-calibration run that completed its assigned denominator and passed the committed-evidence checks. It was generated without provider calls from the response-content-free derivative.

## Calibration table

| Policy slot | Assigned | Attempted | Success | Invalid output | Request failure | Infrastructure failure | Policy violation | Truncation |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `A-gemini-stateful-v3` | 50 | 50 | 35 (70.0%) | 1 | 1 | 0 | 0 | 13 |
| `B-qwen-stateful-v3` | 50 | 50 | 0 (0.0%) | 3 | 0 | 0 | 0 | 47 |

Null and negative results are shown unchanged. Attempted is kept separate from every terminal classification.

## Policy identity, dates, spend, and stopping

### `A-gemini-stateful-v3`

- Provider/model alias: `openrouter/google-vertex/global` / `google/gemini-3.7-flash`
- Execution date: not recorded in committed plan or summary
- Endpoint record observed: `2026-08-28T00:32:11Z`; source evidence author date: `2026-09-02T22:49:08-07:00` (committed-history date, not an execution timestamp)
- Policy manifest / code revision / runtime: `sha256:90bb53a933a13b8bf91631a6590694ef032e8567bf37cd3e349c5c7c54c97f15` / `d534091983db650c0b7c5bd9fe0db0cf32bbc729` / `sha256:075818c0943959b0be4aa5782d5672c74a7089823a15335e1e87b941889e5cb5`
- Calibration-partition manifest: `sha256:41034ec1ddaf31be37e54d0fe2889cb1b9e48763504ec04857b03cae3aa8f6f9`
- Prompt digest: `sha256:e749173bb13d9c54487fcd3e353effe073cc1308c4c82d27169b9ea7405209be`
- Memory/parser/response policy: `pixelgym-agent-v5-visible-action-history-v1` / `pixelgym-agent-v5-json-action-normalized-1000x1000-parser-v1` / `pixelgym-agent-v5-canonical-response-v2`
- Coordinate/retry policy: `normalized-1000x1000` / `bounded-same-route-zero-completion-http-429-or-transient-transport-fault-after-bounded-backoff-v3`
- Temperature: `not recorded`; response validation: `{"invalid_or_unparseable_output_rule":"retain_fail_the_assignment_and_continue_without_retry","local_exact_action_parser":true,"upstream_json_schema_strict":true,"upstream_response_format":"json_schema"}`
- Known run spend: `$4.911249750`; unknown-charge reservation: `$1.990656000` across 20 outcomes; budget-accounted run spend: `$6.901905750`
- Observed stop: `completed_assigned_denominator`; stop guard tripped: `False`; trip reason: `None`
- Approved hard stops: `run spend ledger blocked`, `policy or request identity mismatch`, `a charge above the per-request theoretical maximum`, `non-retryable HTTP status`, `evidence-integrity failure`

### `B-qwen-stateful-v3`

- Provider/model alias: `openrouter/alibaba` / `qwen/qwen3-vl-8b-instruct`
- Execution date: not recorded in committed plan or summary
- Endpoint record observed: `2026-08-28T13:34:08Z`; source evidence author date: `2026-09-02T22:49:08-07:00` (committed-history date, not an execution timestamp)
- Policy manifest / code revision / runtime: `sha256:151a04d1e2a54c121bbf5d86db42713d4632d65019836edd4b75122c9922d535` / `0ed7ec2c84f510c7ea814024a0785436c808d9ed` / `sha256:38f7d6cb71a3595678ff3e08f6b438078f5922fd0bdd2c8f5011d3feeeffb2b2`
- Calibration-partition manifest: `sha256:41034ec1ddaf31be37e54d0fe2889cb1b9e48763504ec04857b03cae3aa8f6f9`
- Prompt digest: `sha256:e749173bb13d9c54487fcd3e353effe073cc1308c4c82d27169b9ea7405209be`
- Memory/parser/response policy: `pixelgym-agent-v5-visible-action-history-v1` / `pixelgym-agent-v5-json-action-normalized-1000x1000-parser-v1` / `pixelgym-agent-v5-canonical-response-v2`
- Coordinate/retry policy: `normalized-1000x1000` / `bounded-same-route-zero-completion-http-429-or-transient-transport-fault-after-bounded-backoff-v3`
- Temperature: `0`; response validation: `not recorded`
- Known run spend: `$0.498141709`; unknown-charge reservation: `$0.000000000` across 0 outcomes; budget-accounted run spend: `$0.498141709`
- Observed stop: `completed_assigned_denominator`; stop guard tripped: `False`; trip reason: `None`
- Approved hard stops: `run spend ledger blocked`, `policy or request identity mismatch`, `missing, invalid, or exceeded price guard`, `non-retryable HTTP status`, `evidence-integrity failure`

## Retained runs not published

| Run | Policy slot | Attempted | Failed publication checks |
|---|---|---:|---|
| `qwen-v2` | `B-qwen-stateful-v2` | 13 | `completed_assigned_denominator` |
| `gemini-v3` | `A-gemini-stateful-v3` | 5 | `completed_assigned_denominator`, `publication_relation_verified` |

Gemini v3 stopped after 5 of 50 assignments when its run ledger blocked. The older Qwen v2 run stopped after 13 of 50 assignments under its approved first-invalid-output rule. Neither incomplete run is included in the calibration table.

## Fault-taxonomy disclosure

PR #155 changed classification of CLI process failures. These four retained runs used HTTP/OpenRouter transports and no episode or transport row carries a `cli_fault` key. The derived upper bound on `invalid_output` rows that can be former-taxonomy CLI process failures is therefore zero for every run. Stored labels were not rewritten or reinterpreted.

## Predecessor disclosures

- `gemini-v3b` policy predecessor: distinct successor bound to frozen plan `sha256:8144512f2790333874a706681860e6d7c4def4aa98d4d33b21e03c16302a71a6` and summary `sha256:db7a80b5c2c623d370678d6c610578721b5551b68a1fd9055c23b54a5a267741`; stored rule: this v2 policy is a distinct successor run; do not resume, retry, replace, or reinterpret any frozen predecessor Slot A request or assignment
- `gemini-v3b` frozen infrastructure predecessor: none recorded
- `qwen-v3` policy predecessor: none recorded
- `qwen-v3` frozen infrastructure predecessor: 1 attempted pair ended as `unknown_outcome_infrastructure_failure` after HTTP 429 with request outcome `unknown`; predecessor summary `sha256:630f765bc44ce7cb85c91e0ae3b906fcedf4e1374556d6074d84cc2e95b447f4`

## Evidence and redaction

- Report schema: `pixelgym-agent-v5-d56-completed-calibrations-report-v1`
- Publishable derivative: `sha256:17e599ce1d701073e057a67f79f57371cb93c83217c321f33ac1218b8197b901`
- [Publishable derivative](grounding-v5-d56-completed-calibrations-publishable.json)
- [Integrity audit](grounding-v5-d56-completed-calibrations-integrity-audit.json)
- [Publication relation](grounding-v5-d56-completed-calibrations-publication-relation.json)
- [Evidence errata](grounding-v5-d56-gemini-qwen-v3-calibration-evidence-errata.json)
- [V5 protocol](../plans/grounding-v5-agent-benchmark.md)

Errata corrections applied to interpretation (source evidence remains unchanged):

- `per-run-ledger-wording`: Each listed plan used an independent ledger initialized at zero and bounded by its own caps.maximum_run_spend_usd. The stale shared-ledger and ten-dollar phrases are non-operative prose and do not supersede caps.spend_lineage, caps.enforcement, the approved numeric cap, or the recorded per-run accounting.
- `gemini-policy-generation-label`: Both plans and their summaries are Gemini v3 evidence. policy.slot is A-gemini-stateful-v3 and policy.policy_manifest.transport_retry_rule is the v3 retry rule. Historical d56-gemini-v2 trial identifiers and v2 prose are retained as immutable recorded identifiers and must not be used to relabel the policy generation.

The committed summaries expose aggregate transport rows and journal event-chain digests, but not restricted journals or their file hashes; row-level journal verification is unavailable from a public clone.

The derivative and report contain no response bodies, request bodies, screenshots, checkpoint contents, credentials, or absolute operator paths. Restricted journals remain untracked and ignored.

## Limitations

- Calibration is development evidence, not a benchmark score or milestone-gate verdict.
- Gemini v3b reserves USD 1.990656000 for 20 outcomes whose charges are unknown.
- Qwen v3 completed 50 assignments with zero exact-success terminations.
- The two completed slots use different model/provider routes and are descriptive, not a controlled model comparison.
- The completed slots bind the same calibration-partition manifest, prompt, memory, parser, response-schema, coordinate-adapter, and retry-policy versions.
- Their policy-manifest digests, code revisions, and runtime digests differ.
- Qwen v3 records temperature 0 while Gemini v3b records no temperature; Gemini v3b records strict upstream json_schema response validation while Qwen v3 has no response_validation block.
- Execution dates and restricted-journal file hashes were not retained in committed evidence.
- The historical classification labels are preserved without reinterpretation.
