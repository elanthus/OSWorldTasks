# D5.9 subscription identity verification

Status: PR #258 is merged. After the first pilot stream failures, the owner authorized the
strict parser compatibility fix, replacement freeze, a new four-assignment pilot, and full
execution only after operational checks pass. The original investigation
and its earlier digests below are retained as history. The active freeze is now
`sha256:ce6776def9066d2c3b4a1b2701b25567dc182364dfa3cf694b4044d865d59159`.
No experiment call occurred while preparing this replacement; the separate owner-authorized
Fable code-verification call is recorded outside experiment accounting.

The previous pilot consumed four model attempts and dispatched zero environment actions. All four
responses reported the frozen Haiku model and available usage, but the strict parser rejected
`system/ui_invalidate` before `system/init`. The campaign's raw stop reason was
`completed_all_assignments`; infrastructure failures do not satisfy the owner's operational
pilot condition. The local controller initially accepted those rows, was stopped before
full dispatch, and now explicitly rejects infrastructure/request-failure rows. Thirteen local
offline gate checks passed in 0.56 seconds. No full-phase model attempt occurred; every
experiment process is closed. Both private retention copies verify. The generated
[pilot launch report](../artifacts/grounding-v5-d59-replication/pilot-launch-report.json)
records the raw counts, classifications, stream violations, and retention digest. The owner subsequently authorized a strict
parser successor and a new pilot;
the frozen results are preserved without replay or post-result reinterpretation.

## Cause and observations

Claude Code 2.1.292's installed implementation constructs its environment-token identity
with `subscriptionType` taken from an optional environment hint, or null. Its auth-status
command reports that value; it does not authenticate the Max tier. The runner deliberately
omits the hint and hides the real home/config, which exposed this mismatch. Supplying `max`
as an environment hint would assert the desired identity without proving it.

After the owner re-entered the local credential, the no-model retry still reported logged in,
`authMethod: oauth_token`, and `subscriptionType: null`. CLI version and help digest matched
the frozen identity. A read-only GET to the CLI's own OAuth profile endpoint returned HTTP 403.
The new verifier also stopped at HTTP 403 in 0.545 seconds, with an explicit requirement for
a profile-readable OAuth login. The sanitized observations are retained in
`artifacts/grounding-v5-d59-replication/subscription-verification-investigation.json`.

Anthropic's [authentication documentation](https://code.claude.com/docs/en/authentication#generate-a-long-lived-token)
states that the standard long-lived `setup-token` credential is for model requests. A 403
does not prove that the account lacks Max, or that the owner copied the wrong value; it leaves
the tier unverified. The installed CLI uses `/api/oauth/profile` and maps the authenticated
organization type `claude_max` to Max. The provider endpoint is internal rather than a stable
public profile API; schema or access changes fail closed.

## Fix and remaining prerequisite

`claude_subscription.py` performs one fresh, direct HTTPS GET to the fixed profile endpoint,
using exactly the credential passed to the CLI. It requires nonempty account/organization
identity fields and `organization_type: claude_max`. It does not retain those identifiers.
No local declaration, account-level Max flag under another organization, different credential,
or stored CLI login can satisfy the check. Explicit conflicting CLI tiers remain rejected.

HTTP 401/403, other non-200 responses, redirects, malformed or oversized JSON, and transport
errors stop preparation. There are no retries or cached proofs. Requests use a 10-second socket
timeout, a 64-KiB response limit, default certificate/hostname verification, and no environment
proxy. Diagnostics contain neither response bodies nor request headers. The execution binding
records the verification version, endpoint, organization type, and one non-model auth read.
Model and campaign control-request caps remain unchanged. The verifier source is mandatory
in token-route manifests; historical Keychain-route evidence retains its prior runtime-input
schema and reproduces without a file that did not exist at its recorded revision.

The current credential still cannot verify Max through this endpoint. Before a no-call
preparation can succeed, the owner must provision a profile-readable OAuth credential and load
it through the existing hidden-input helper. The same value must serve the CLI and the profile
check; this fix does not create credentials, broaden scopes, read the Keychain, or import cached
account metadata. No model call may be used to work around missing profile access.

## Candidate re-freeze and approval

The proposed source revision is `39e3bca4c1549868bf23274d08ae6e7410b6324e`. Its candidate plan digest is
`sha256:b20558c056bf0596d8faecffd692e9b16dc724fb3a59dc2de26f8ac904fecc53`, with history policy `policy-937dd894fc9badcdc29a` and stateless policy
`policy-fb8747c41ec03f11896d`. The candidate is
`artifacts/grounding-v5-d59-replication/subscription-verification-candidate.json`.

Exactly these top-level plan fields change: `source_binding`, `policy_manifests`, `route`,
and `execution_plan_digest`. Within `route`, `login_source` now requires profile-read
permission, `confirmed_before_execution` specifies the fresh check, and
`subscription_verification` binds its method, endpoint, same-authentication requirement,
organization type, verification version, and separate non-model preparation request.

The required runtime identity still says Max, with the same CLI/model/help identity. Tasks,
statistical analysis, stop-rule version/options, assignments, concurrency, and runtime/call
caps do not change. The pilot remains four assignments, at most four workers, 104 environment
actions, 208 model attempts, zero control requests, 208 wire requests, six hours, and USD 0.00
incremental experiment charge. The candidate keeps execution disabled and approved caps zero.

The owner must approve replacing the active freeze and approve the pilot against the new
digest/policy pair. The old exact-digest pilot approval is deliberately rejected for the
candidate. The instruction to stop before model calls remains in force. Full execution and
the D5.10 verdict remain separate human decisions.

## Regression evidence

Offline regressions cover null CLI tiers with fresh authenticated Max proof; explicit CLI
conflicts; local Max hints with non-Max server responses; missing active credentials; unrelated
fallback login prevention; missing identity fields; profile access failures; redirects; malformed
and oversized responses; safe errors; mandatory verification receipts; and fresh verification
on every probe. These cases make no network, browser, OSWorld, CLI, or wall-clock sleep calls.
The PR validation record states actual counts and runtimes, including omitted existing
loopback/socket and sleep-based tests.

## Owner-approved recovery amendment (2026-10-22)

The owner subsequently provisioned a full browser OAuth login through the ignored local helper.
Fresh same-credential profile verification returned HTTP 200 with `claude_max`; the token-route
CLI identity matched the freeze, and the auth-status-only kernel-sandbox check passed with no
allowed proxy connections. The earlier 403 observations above remain historical evidence.

The owner now authorizes a replacement freeze with shared OAuth-expiry recovery. Workers pause
before a new attempt reservation or request deadline, drain existing sends, and wait for the
owner to run `.venv/bin/python .cache/d59-replication-pilot/configure-token.py --login` in their
own Terminal. The same-credential Max/CLI/model/help check remains mandatory. Runtime excludes
owner-login waiting only after all in-flight attempts settle. The six-hour pilot and 168-hour
full-run windows are active time; request deadlines and counted attempt/wire caps are unchanged.
A known zero-usage synthetic expiry rejection can use the remaining existing retry; no assignment
or committed action is replayed, and unknown-invocation stops are never cleared by login.

After the new freeze, the owner authorizes pilot model calls and conditionally authorizes the
full campaign when every pilot assignment completes with valid operational evidence and verified
retention, without a guard stop. Task failures do not defeat that operational gate. The dated
[pre-registration addendum](../artifacts/grounding-v5-d59-freeze/analysis-plan.md) records the
owner's instructions and the exact phase limits. Model calls remain prohibited until the new
freeze and its separate machine-readable approvals are recorded.

The active OAuth-recovery freeze binds source `0c7836ff995575ec889a44841b542dff7377e1cc`,
history policy `policy-bf07cf314a4cc448f498`, and stateless policy
`policy-adda157ee17a0833a036`. The machine-readable pilot approval and conditional owner
direction are stored beside `execution-plan.json`. Its disabled flags and zero approved-cap
placeholders remain intact; separate phase approvals supply the execution limits.

The first launch stopped during preparation/execution binding comparison, before creating
an attempt journal or dispatching an assignment. The rendered sandbox profile includes a
fresh temporary path and proxy port, so its digest changes on each probe. Execution now
compares all identity, source, template, allowlist, and probe outcome fields exactly while
allowing the rendered-profile digest and positive refused-connection count to vary. Both
raw receipts are retained (`execution-binding.json` and `execution-live-binding.json`),
and the final summary binds the execution receipt. The replacement freeze changes only
source/policy provenance and the digest; phase limits and the approved recovery protocol
remain the same. `prelaunch-binding-stop.json` records the zero-attempt launch stop.

The parser successor accepts exactly one `system/ui_invalidate` preamble with `event: ui.render`
before init. Its envelope contains exactly the five observed fields; the session must match
every record and its UUID must be unique. Unknown, duplicate, misplaced, or expanded UI
events still fail closed. Model, route, tool/MCP, overage, retry, and sandbox checks remain
strict. The same validated preamble is recognized for confirmed zero-usage OAuth-expiry and
connection-reset envelopes under the existing bounded retry. The new parser version is
`pixelgym-agent-v5-claude-stream-json-action-parser-v4-ui-render-preamble`. Four retained streams were checked offline; the
previous pilot evidence is unchanged and remains separate from the newly approved pilot.
