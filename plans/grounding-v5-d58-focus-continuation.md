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

## Recorded outcome and diagnosis

The [continuation report](../artifacts/grounding-v5-d58-focus-continuation/report.md)
records five new infrastructure failures, triggering the same five-failure stop
rule. With the five preserved failures, ten assignments have been attempted and
90 remain unrun. Each mode has five attempts and no terminal success. Three
stateless episodes reached both memory consumers and made six valid first choices,
four correct; no history episode reached a memory consumer.

The continuation's 67 requests produced 62 positive charges, one confirmed zero
charge, and four unknown outcomes. New known charges were USD 0.302328000 and new
unknown holds USD 0.17013825. Aggregate known charges are USD 6.233601525; unresolved
holds are USD 1.69356555, with no request in flight. USD 7.927167075 is accounted
against the USD 28 authorization. Budget was not the stopping condition.

The [verification receipt](../artifacts/grounding-v5-d58-focus-continuation/verification.json)
preserves concrete error details:

- Three `SSLV3_ALERT_BAD_RECORD_MAC` alerts during the `urlopen` phase, on request
  bodies of 50,649, 62,502 and 490,268 bytes.
- One `URLError` wrapping `BrokenPipeError`, errno 32, on a 577,128-byte request.
- One received provider response with `finish_reason=error`, provider error code
  429, zero completion tokens, and a confirmed zero charge.

The trace label `open_connect_or_send` covers the entire `urlopen` call, including
connection setup, request transmission, and response headers. It cannot identify
which of those operations failed or prove that generation never began. Unknown
charges therefore retain their bounded holds. No runner deadline or transport
retirement occurred.

Python exposes OpenSSL's library and reason enums through
[`SSLError`](https://docs.python.org/3.12/library/ssl.html#ssl.SSLError).
The [TLS 1.3 specification](https://www.rfc-editor.org/rfc/rfc8446#section-6.2)
defines bad-record-MAC alerts for failures to decrypt and authenticate a record.
These observations identify a TLS-channel failure but do not locate its cause in
the client, an intermediary, or the remote endpoint. A separate
[successful public GET](../artifacts/grounding-v5-d58-focus-continuation/tls-probe.json)
negotiated TLS 1.3 with AES-256-GCM; the `SSLV3` prefix in an error enum is not evidence
that the failed request negotiated SSLv3. The failed connection's protocol was not
captured. Changing the screenshot history size alone would not explain the small
stateless-request failures.

The read-only verifier reconstructs all 120 cohort requests, remeasures all ten
attempted episodes, checks the five retained results, and preserves the prior
10,904-event prefix. It records the continuation's settlement counts separately.
The frozen zero-retry policy remains unchanged. Transport reliability and handling
of provider-body rate-limit errors need a versioned repair before further paid
continuation; this diagnostic does not claim that repair is complete.

```bash
PYTHONPATH=. .venv/bin/python artifacts/grounding-v5-d58-focus-continuation/analyze.py
PYTHONPATH=. .venv/bin/python artifacts/grounding-v5-d58-focus-continuation/analyze.py --journal .cache/d58-memory-calibration/aggregate.sqlite
```
