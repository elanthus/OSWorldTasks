# v5 policy sandbox for CLI launches

- **Scope:** OS-level enforcement around each Claude Code CLI policy launch in the v5 agent
  benchmark, the runner-owned egress proxy that is its only network path, and the evidence that
  binds both to a policy manifest
- **Status:** mechanism implemented with no-cost verification; not yet bound into any frozen
  policy pair or execution plan, and no provider call has been made under it
- **Terms:** see the [glossary](glossary.md) and the [v5 benchmark design](v5-benchmark-design.md)

## Why the D5.9 launches ran without OS enforcement

The v5 contract says provider access is the only outbound network use allowed from the policy
sandbox. The first OS sandbox, a deny-by-default `sandbox-exec` profile, was verified only with a
Python probe against a loopback provider stub. It could not host the Claude Code CLI for two
reasons:

1. The Max subscription credential lives in the macOS Keychain. Keychain access needs the
   security daemon and the real home directory. Inside the profile, even with those services
   allowed, the CLI reports that it is not logged in.
2. The profile allows outbound traffic only to a loopback IP and port. SBPL cannot allowlist a
   hostname, so `api.anthropic.com` had no legal path out.

The owner therefore waived OS enforcement for the exact D5.9 Haiku pair. The policies ran with
CLI flags and an environment allowlist, and every manifest recorded `os_sandbox_applied: false`
([exception](../artifacts/grounding-v5-d59-haiku-freeze/owner-exception.json)).

## Design

Three parts, each with its own evidence.

### 1. Kernel boundary around each launch

Every launch that opts into this mechanism (`ClaudeCodeTransport(sandbox_launch=...)`) runs
under `/usr/bin/sandbox-exec` with a profile rendered from a frozen template (`pixelgym.grounding.v5.os_sandbox.darwin_cli_profile`). The profile is deny-by-default
and allows only:

- executing the pinned CLI binary, and reading its install directory and system libraries;
- reading and writing one per-launch policy workspace, which the launch also uses as `HOME`,
  `CLAUDE_CONFIG_DIR`, and `TMPDIR`;
- listing and entry metadata of the CLI's shared scratch root `/private/tmp/claude-<uid>`, and
  the right to create and use exactly one entry there: the project directory the CLI derives
  from the launch working directory (every character other than a letter or digit becomes a
  dash). Other sessions' scratch contents stay unreadable;
- outbound TCP to one loopback port, the egress proxy below.

Everything else is denied: the real home directory and therefore the Keychain, runner storage,
the peer policy's workspace, inbound listeners, and every other network destination. The template
digest and the rendered profile digest are both recorded per launch.

### 2. Token authentication without the Keychain

The runner holds the owner's OAuth credential in its own process environment and passes it to
the sandboxed child as `CLAUDE_CODE_OAUTH_TOKEN`. For the replication, that credential must also
be authorized to read its own OAuth profile, so its Max organization can be verified before
execution. Standard `claude setup-token` credentials are documented as model-request-only;
they can report `subscriptionType: null` and refuse profile access with HTTP 403. The
value never appears in argv, journals, records, or reports; the launch record keeps the
environment variable names only, and `validate_credential_free` rejects any journal payload that
carries a credential-shaped value. Verified without a model call: with the home directory hidden
and an isolated config directory, `claude auth status --json` inside the profile reports
`loggedIn: true` and `authMethod: oauth_token`, and the CLI populates the config directory itself.
That observation establishes the selected auth route, not the account's subscription tier.

The prepared subscription-verification fix requires a fresh HTTPS GET to
`https://api.anthropic.com/api/oauth/profile` using exactly the credential supplied to the CLI.
Only the authenticated organization's `organization_type: claude_max` is accepted. A null CLI
tier is filled from that proof; an explicit conflicting CLI tier is rejected. Environment tier
hints, cached profiles, other account logins, unknown tiers, unavailable profile access, and
redirects cannot substitute for proof. Each preparation and execution preflight verifies again.
The one read-only auth request is outside campaign model/control-request accounting and makes
no model request. The check uses a 10-second socket timeout and a 64-KiB response limit, retains
no raw profile or account identifiers, and records sanitized verification evidence in the binding.
The [identity investigation and proposed re-freeze](d59-subscription-verification.md) records
the remaining credential prerequisite and the separate approval boundary.

### 3. Egress through a runner-owned CONNECT proxy

The runner starts a loopback HTTP CONNECT proxy (`pixelgym.grounding.v5.egress_proxy`) before a
launch and passes it as `HTTPS_PROXY` and `HTTP_PROXY`. The proxy allows `CONNECT` to the frozen
provider target, `api.anthropic.com:443`, and refuses every other request with `403`. It is a
tunnel, not an interceptor: TLS stays end to end, so the runner never sees request bodies or the
credential. Every attempt is recorded with a sequence number, timestamp, target, and verdict.

Verified without a model call, using a placeholder token and a proxy that refuses everything:

| Launch | Observation |
| --- | --- |
| Through the proxy | CLI returns a structured `ERR_PROXY_TUNNEL` result; the only target requested was `api.anthropic.com:443`, thirteen times for one logical request |
| Without the proxy variables | CLI fails on DNS (`ENOTFOUND`); the kernel denied direct egress |
| Telemetry switches | `DISABLE_TELEMETRY`, `DISABLE_ERROR_REPORTING`, and `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` set; no other host was requested |

The thirteen attempts for one request are the CLI's internal connection retries. They were invisible
in the first D5.9 campaign and are the reason it was discarded. At the proxy they are counted.

## What a launch records

The transport records, per invocation, a `runtime_enforcement` block with
`mechanism_name: darwin_sandbox_exec_cli_launch`, `os_sandbox_applied: true`, the profile
template digest, the rendered profile digest, the egress allowlist digest, the argv digest, and
the environment variable names. It also records `egress_attempts`: the proxy's attempt list for
that invocation, with allowed and refused counts. A refused attempt is a policy violation: the
policy process tried a channel the contract forbids, and the episode fails closed.

The sandbox manifest schema moves from v3 to v4 (`pixelgym.grounding.v5.sandbox`). v3 types
hard-code `os_sandbox_applied: false`; v4 allows the sandboxed launch mechanism with the bound
digests and keeps v3 loading unchanged, so every frozen artifact still verifies byte for byte.

## Verification without provider calls

1. The existing Python probe (`run_darwin_probe`) is unchanged.
2. The egress proxy is unit-tested on loopback: an allowed target tunnels bytes both ways, a
   refused target gets `403` and is logged, a malformed request is refused, and the allowlist
   digest is stable.
3. The CLI profile is unit-tested for its deny-by-default shape and its digest.
4. `pixelgym.grounding.v5.cli_sandbox_probe` runs the real CLI under the real profile with a
   synthetic placeholder token: `auth status` reports the token route, a `--print` through a
   refusing proxy requests only the provider target, and a `--print` without the proxy cannot
   resolve any host. No request reaches the provider. The opt-in integration test
   `tests/integration/test_grounding_v5_cli_sandbox.py` (`PIXELGYM_RUN_V5_CLI_SANDBOX_TESTS=1`,
   Darwin, `claude` installed) runs that probe and then the full transport path with the real
   CLI against a refusing proxy. `scripts/probe_grounding_v5_cli_sandbox.py` prints the probe
   result for an evidence package.

The first call under an allowing proxy is a smoke test and needs the owner's approval like any
other model call.

## Binding into a replication freeze

A versioned successor that uses this mechanism must bind: the CLI binary path and version, the
profile template digest, the egress allowlist digest, the token route (`authMethod:
oauth_token`) as the runtime identity, and the environment variable names. Changing any of them
is a new policy ID. The D5.9 Haiku pair is unchanged; its manifests keep
`os_sandbox_applied: false`.

## Known limitations

- The CLI's scratch root `/private/tmp/claude-<uid>` is shared with the owner's own Claude Code
  sessions and holds their live state, so the runner never empties it. The CLI opens that root
  itself, so a launch can list the root and read entry metadata, which exposes other sessions'
  entry names; it can create, read, and write only its own project directory there. In
  `--print` mode with session persistence off the CLI was observed to create nothing.
- The hostname allowlist is enforced by runner code at the proxy. The kernel enforces only that
  the loopback port is the sole destination.
- `claude --version` fails inside the profile for a reason not yet traced. The runtime identity
  probe needs no credential and runs outside the profile.
- `sandbox-exec` cannot nest. The runner must start from an unsandboxed parent process.
- The proxy counts CONNECT attempts, not API requests. The CLI's internal retries are therefore
  visible but not individually attributable to API responses.
