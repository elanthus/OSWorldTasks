# D5.8 transport repair and bounded reliability diagnostic

The owner approved the proposed transport replacement and bounded retries, asking
that random, retryable network failures stop interrupting the calibration. The
repair retries within a logical action: the screenshots, request body, policy
state, and environment remain fixed until one response produces a valid action.
The runner journals each wire attempt under a distinct identity and dispatches at
most one GUI action. It permits an initial send and two retries per action.

## Transport and rate limits

`CurlWire` uses one system curl process per send with HTTP/1.1, certificate
verification, a 20-second connection limit, and at most 180 seconds for the
transfer. Credentials travel through stdin, not argv or credential files. Default
curl configuration and automatic retries are disabled. The process is killed and
reaped on a client deadline. A runner-abandoned worker retires its lifecycle and
cannot overlap another request. Killing the local client does not prove that
remote generation stopped or that an uncertain request was free.

This Mac's curl build reports LibreSSL/SecureTransport, separate from the Python
OpenSSL build used by the failed runs. Its executable digest and version are bound
to the diagnostic. This is a tested alternate client path, not a claim that the
underlying TLS fault has been located. The [curl manual](https://curl.se/docs/manpage.html)
documents the transport limits and disabling retries.

The new transport recognizes HTTP 429 and provider rate-limit errors inside
HTTP 200 bodies, including top-level and choice-level error objects. The old
transport only applied cooldowns to HTTP errors and shortened server delays above
60 seconds. [OpenRouter's error documentation](https://openrouter.ai/docs/api_reference/errors-and-debugging)
describes body-level errors and `Retry-After`.

The repair honors a valid `Retry-After` in full. Otherwise consecutive failures
wait 5, 15, 45, then at most 60 seconds. A logical action permits only two retries;
the longer sequence applies if failures continue across actions. Cooldowns are
journaled and survive transport reconstruction. The runner waits before starting
the next request's deadline, so a ten-minute cooldown can fit within the phase's
90-minute limit. It stops before sending when the server delay cannot fit in the
remaining phase time. No cooldown is shortened to squeeze in another call.

Identified transient TLS/connection errors and retryable provider statuses receive
bounded retries on the same model and route. Nonempty model output, malformed
actions, refusals, credential/certificate errors, and price or identity violations
are not retried into a better answer. Generic empty completions are not enough to
establish a retryable provider failure. Every received envelope is retained in the
private journal; public receipts contain bounded transport metrics and digests.

## Accounting and version boundaries

Each send reserves its own screenshot-based bound before reaching the wire. A
known charge replaces that hold; a confirmed zero charge accounts for zero; an
unobservable charge keeps its request-sized reservation. Both the aggregate cap
and phase caps survive reconstruction. No old reservation is reset or released by
this repair. A local process exit cannot establish remote billing.

The aggregate authorization remains USD 28. The diagnostic has an additional
limit of USD 1 in new charges plus unresolved holds and at most 20 wire calls,
including retries. Ten logical actions cover five fixed development states in
both modes, including small requests and histories reaching the second memory
consumer. Prefix actions are explicitly scripted and never counted as model
successes or independent memory evidence.

The candidate manifests now bind the repair sources and the new retry rule.
Prompts, screenshots, task generator, action limits, and delayed feedback are
unchanged. Historical executed files and all ten failed calibration assignments
remain intact. A subsequent continuation must bind the new manifests for the 90
untouched assignments and disclose the execution-version boundary. This work does
not declare a final D5.8 verdict or authorize confirmatory tasks.

## Verification and execution

The offline fixtures cover mixed TLS/429 retries, identical request bodies,
distinct attempt identities, one GUI dispatch, exhausted retries, large histories,
long server delays, durable limits/cooldowns, invalid model output, and abandoned
workers. A separate integration fixture sends a large POST through real curl to a
temporary local TLS server with certificate verification and checks byte equality.
It makes no provider calls.

```bash
.venv/bin/pytest -q tests/unit/test_grounding_v5_curl_wire.py tests/unit/test_grounding_v5_reliable_transport.py
.venv/bin/pytest -q tests/integration/test_grounding_v5_curl_wire.py
.venv/bin/python -m scripts.run_grounding_v5_reliable_diagnostic prepare
.venv/bin/python -m scripts.run_grounding_v5_reliable_diagnostic execute --approved-plan-digest <frozen-digest>
```

Source and plan must be committed before paid calls. The driver verifies source
digests, the previous journal prefix, current prices, and curl identity. An
interrupted or closed diagnostic cannot restart. Raw results determine whether
all ten logical actions dispatched once within the caps; a small successful
diagnostic would not guarantee reliability across the full calibration.
