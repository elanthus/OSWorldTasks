# D5.8 continuation under the USD 28 aggregate cap

The owner authorized an additional USD 8 and explicitly selected “Retain five
results; run remaining 95 (recommended)”. The [budget amendment](../artifacts/grounding-v5-d58-budget-amendment/approval.json)
raises the total D5.8 authorization from USD 20 to USD 28, including historical
charges and unresolved holds. It does not authorize confirmatory evaluation or
decide the final D5.8 gate.

The [preceding phase](../artifacts/grounding-v5-d58-focus-calibration/report.md)
stopped at five consecutive infrastructure failures. This continuation preserves
all five results and their original trial IDs; only the 95 untouched assignments
receive new trial IDs. Their order, seeds, task hashes, limits, focus renderer,
deferred feedback, and candidate policy manifests remain identical. There are
47 history and 48 stateless episodes left. Reports keep all 100 assignments and
disclose the continuation boundary, while reporting new-phase spend separately.

At authorization, the journal accounted for USD 5.931273525 in known charges and
USD 1.52342730 in unknown holds. Applying the previous completed-episode means to
the remaining assignments projects USD 19.169116444 in new charges, or about
USD 26.623817269 including the carried holds. The USD 28 cap provides about
USD 1.38 of estimated margin. This is a planning estimate, not a completion guarantee.

## Transport diagnosis

The historical errors retain only exception classes: three SSL errors, one URL
error, and one empty response. Their precise TLS reasons cannot be recovered from
those records. Ten unauthenticated public endpoint GETs all returned valid JSON
with HTTP 200; these free checks do not reproduce large screenshot POSTs or
upstream model generation.

`DiagnosticUrlopen` observes the existing request and response read without
altering their bytes, timeout, route, or retries. On a failure it records the
request stage, body size, exception classes, numeric error codes, and bounded SSL
library/reason enums. It excludes exception messages, URLs, headers, credentials,
and provider content. The original exception is re-raised unchanged. This improves
diagnosis; it does not claim to repair an unidentified TLS fault.

The same request-local transport serves both modes sequentially. No retry is
allowed. Any abandoned active send retires the continuation transport durably.
The phase also stops at five consecutive non-normal episodes within this newly
authorized continuation, a provider identity/price violation, the USD 28 aggregate
cap, or 90 minutes since its durable start. Already-started episodes are never
resent, and closed phases cannot restart. The five retained failures are not retried
or counted as five new failures in the continuation.

## Execution and evidence

The plan binds the amendment, all 100 assignments, the 95 executable assignments,
the five preserved results, source and policy identities, connectivity evidence,
prices, and the preceding 10,904-event journal prefix. Sources and the plan must be
committed before calls; the tracked worktree must be clean.

```bash
.venv/bin/python -m scripts.run_grounding_v5_focus_continuation prepare
.venv/bin/python -m scripts.run_grounding_v5_focus_continuation execute --approved-plan-digest <execution_plan_digest>
.venv/bin/python -m scripts.run_grounding_v5_focus_continuation report
```

Reports derive from stored evidence. Historical cohorts with another renderer and
the supplied-state diagnostic remain separate. Calibration thresholds and final
approval boundaries remain in the [D5.8 decision package](grounding-v5-d58-final-design.md).
