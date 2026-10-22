# D5.9 replication pilot approval

Recorded at: 2026-10-22T01:12:03.677093-07:00

Owner statement:

> I approve the pilot exactly as specified. Record the approval, help me configure the OAuth token, and run the no-call auth/sandbox checks. Stop before any model calls.

The exact machine-readable approval is [pilot-owner-approval.json](pilot-owner-approval.json), validated against the frozen plan. It approves pilot phase only: 4 assignments, up to 4 workers, 104 environment actions, 208 model attempts, 0 control requests, 208 provider wire requests, a 6-hour runtime window, and USD 0.00 incremental experiment charge. Model attempts and wire requests count the same ceiling, including retries.

Plan digest: `sha256:d47fa2b18588d0e29534a1dea0ef3c0f2679bd2f063dfb7b14540140a6cbb233`

Source revision: `7c2834f5ac91e628b7681c6b501044c7da546bc8`

Model: `claude-haiku-4-5-20251001`, default reasoning effort, Claude Code Max subscription via the OAuth token route.

- History policy: `policy-ce207861ec66ca1f6ae4`
- Stateless policy: `policy-6c465c794ad1d22dd1de`

Current authorized work is recording this approval, configuring the runner token, and running no-call auth/sandbox preparation. **Stop before any model calls.** This record is not an instruction to start execution; an explicit start instruction is still required. The full campaign remains separately approval-gated. The frozen candidate is unchanged and retains its disabled execution flags and zero approved-cap placeholders; this separate approval supplies only the pilot phase limits.

No credential value belongs in this record, its JSON approval, or any check receipt.

## No-call preparation outcome

The owner completed local token configuration. The isolated auth probe exited 0 and reported `loggedIn: true`, `authMethod: oauth_token`, and `subscriptionType: null`. Installed CLI version `2.1.292 (Claude Code)` and its help digest match the frozen identity. The plan requires `subscription_type: max`, so the live identity check rejects preparation with `Claude Code must use the approved Max subscription authentication`. No model request was made to resolve or bypass this mismatch.

The synthetic sandbox probe exited 0 with the expected blocked-egress observations: 13 refused proxy attempts, `ERR_PROXY_TUNNEL`, direct `ENOTFOUND`, and zero direct targets. This synthetic probe does not establish the real account's subscription tier.

The structured [no-call receipt](pilot-no-call-checks.json) records the raw non-secret identity fields, expected fields, and preparation failure. Pilot execution was not invoked. No prepared execution binding was created. The approval remains valid for its exact plan, and the frozen plan remains unchanged. Resolving the subscription identity mismatch is required before launch; the instruction to stop before model calls remains in force.
