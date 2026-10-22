# D5.9 subscription identity verification

Status: implementation and successor candidate prepared for owner review. The active
`execution-plan.json` and its pilot approval remain bound to
`sha256:d47fa2b18588d0e29534a1dea0ef3c0f2679bd2f063dfb7b14540140a6cbb233`.
No model calls or pilot execution were performed. The replacement is not approved or activated.

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
Model and campaign control-request caps remain unchanged.

The current credential still cannot verify Max through this endpoint. Before a no-call
preparation can succeed, the owner must provision a profile-readable OAuth credential and load
it through the existing hidden-input helper. The same value must serve the CLI and the profile
check; this fix does not create credentials, broaden scopes, read the Keychain, or import cached
account metadata. No model call may be used to work around missing profile access.

## Candidate re-freeze and approval

The proposed source revision is `1cec5031597b1ed55f6d769464d9cbc060d5b5cb`. Its candidate plan digest is
`sha256:d8bb0c24c3d415c96cb6f3c537652630302c6143260dc2bec84237374706fe2c`, with history policy `policy-51247a1463cba56dbde6` and stateless policy
`policy-ed6f5bb11f346cd4c25a`. The candidate is
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
