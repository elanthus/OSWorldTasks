# S7 proposal: a bounded scripted serving rehearsal

- **Status:** owner approved implementation and scripted qualification on 2026-10-20 under the
  proposed caps. Exact package activation and publication remain blocked pending owner review.
- **Reader and decision:** the project owner, deciding whether to implement and qualify the two
  packages and rehearsal below. Exact package approval and serving approval remain separate.
- **Inspected base:** `acaa0da8a6ec752e39510a3f05339a9f204fe1a7` (PR #249).
- **Purpose:** demonstrate environment correctness, durable evaluation evidence, and serving
  controls with scripted policies and fake transports. This is engineering validation; it supplies
  no model-quality result or D5.10 verdict.

Owner clarification: retain one local archive copy and one private LFS copy, preserving the
original run. This supersedes the original second-volume/two-local-copy prerequisite.
The local archive and original share one disk; independent recovery depends on the verified
private remote copy. The GitHub account budget remains $0 with stop usage enabled, and each
transfer still requires a fresh observation.

The implementation for this approval stops after qualification and exact package/report
generation. It exposes only `prepare`, `qualify`, `verify`, and `report` commands through
`python -m scripts.s7_serving`; there is no activation, approval, deployment, or publication
command. The full serving rehearsal below remains the next owner-controlled phase.

The local dependency constraint is generated from the already installed Python runtime,
distribution versions, and installed RECORD digests before any send. The lock binds interpreter
bytes and distribution metadata; it does not verify installed module bytes against RECORD.
Host replay dependencies therefore retain an operator-controlled installed-file integrity limit.
Qualification consumes and checks that exact runtime lock; it does not claim to have installed
an unrelated platform lock or downloaded packages. A's qualification uses the shared runner's
one-action caller limit
(`pilot_action_limit`) followed by explicit environment cleanup; `/api/v2` close behavior is
reserved for the later serving rehearsal.

The 30-minute allowance measures active execution. At the completed qualification boundary,
the durable fake-send guard pauses and refuses further sends. A later operator action must bind
the exact activation-approval digest before resuming its remaining time; counts are never reset.
Human review wait is excluded. Keep the sealed qualification ledger immutable and use its
verified SQLite backup in the later rehearsal state directory rather than modify the archive.

Startup repair prepared after the initial qualification attempt: at revision `73eebbb`, worker
initialization imported the host journal through the runner and failed because the existing
Darwin sandbox blocked Homebrew's SQLite dylib. No fake send was reserved. The stopped plan and
source are preserved. The same scripted class now lives in the stdlib-only
`pixelgym.scripted_policy` module and is re-exported from the v5 runner. This avoids importing
host journal code in the worker and changes no sandbox authority. A zero-send real-sandbox
startup regression passed. The owner explicitly approved a fresh frozen qualification plan for
this import-path repair on 2026-10-20, preserving the stopped attempt and the same caps. The total
qualification allowance remains 77, with zero consumed by the stopped attempt. Activation and
publication remain blocked.

The replacement freeze at `d3d439056759131d99e622950b950da38223e4a3` stopped after
73 qualification sends: golden 20, premature commit 26, step limit 26, and malformed response 1.
The malformed response was retained, but its parser rejection crossed the worker RPC as a generic
process error rather than the runner's expected invalid-output exception. No additional send was
made. The repair adds a closed `parse_failure` response code only for documented parser errors;
unexpected parser faults, malformed RPC, and timeouts remain infrastructure failures. The worker
still exits on error, and sandbox authority is unchanged. Worker proofs are now written per case
before any send so a stopped run retains them. A zero-send real-sandbox test exercises all policy
methods and the malformed parser boundary.

After that stopped freeze, the then-current approval left four qualification sends.
A repaired source revision requires a new
complete qualification; the earlier 73 sends cannot qualify that revision. A replacement run
therefore requires **separate approval for 77 additional fake sends**, raising cumulative
qualification consumption/allowance to at most **150** and the overall campaign ceiling to
**184** (150 + A 4 + B 24 + smokes 6). Both stopped runs remain immutable predecessor evidence.
The owner explicitly approved those 77 additional fake sends on 2026-10-20. Freeze both stopped
archives with `prepare --predecessor-archive PATH=sha256:<approved-manifest-digest>` and
`--expected-predecessor-sends 73` before running the replacement. A first freeze instead requires
`--no-predecessors`. These explicit declarations prevent accidental omission; they cannot detect
a dishonest operator declaration independently of the owner approval record. The replacement's
durable guard still limits it to 77 qualification sends and 111 across its phases; the retained
predecessor ledgers contribute 73 immutable sends, giving aggregate ceilings of 150 and 184.
Preparation and verification check the predecessor seals, source bytes, archive identities,
durable counts, and cumulative arithmetic; a duplicate predecessor is refused. Its approval
record must state the additional allowance. No automatic retry is authorized. Activation,
publication, real-provider requests, and spending remain blocked.

## Activation compatibility after review repairs

The retained completed qualification and exact package/report digests bind revision
`e6d61aea96a3b78fb0df9db65805cdc2cc8e3d03`. The current PR includes later worker-cleanup,
review fixes, and main-branch changes. `verify_worker_binding` requires the exact bound Git
revision and refuses those retained packages from this head or any later main checkout. It also
requires host `pixelgym` imports to resolve under the declared source checkout. At the time of
that PR, no rebinding or new qualification was authorized, and the owner still had to resolve the
source/version mismatch before activation; the old checkout lacks these later repairs. Frozen
evidence stays unchanged. That restriction is historical: the current approval status is the
successor qualification recorded below.

On 2026-10-26 the owner resolved that mismatch by approving a fresh successor qualification of
the repaired source with a further 77 fake sends. The predecessor loader now accepts a completed
sealed qualification as well as stopped ones, so the successor freeze declares all three retained
archives (0 + 73 + 77 = 150 prior sends) and carries cumulative ceilings of **227** qualification
and **261** campaign sends. A completed predecessor is counted from its stored bytes only: seal,
plan, source snapshot, results-to-ledger agreement, and the presence of packages and stored
verification, and the stored verification record's binding to those results and that source. It
is not replayed and its packages are not loaded. The private successor freeze binds revision
`3b2865877aa784cfcb12066fcccc3f617b657ad7`; review repairs after it move this branch past that
revision, so its exact packages load only from that checkout, as with v3. Its local archive,
restore drill, and verified private LFS copy are recorded in
[artifacts/evidence-retention-s7-20261026-successor](../artifacts/evidence-retention-s7-20261026-successor/REPORT.md). Activation, the full serving
rehearsal, publication, and milestone decisions remain separate owner gates.

## Decisions requested

Approve this specification before generating qualification outputs or configuring an approved
gate-policy digest. The proposed choices are:

| Decision | Proposal |
| --- | --- |
| Policy and transport | Existing `ScriptedStatefulPolicy` and in-process `ScriptedTransport`; no real-provider transport, credentials, model review CLI, or model-backed memory operation |
| Packages | A: one-NOOP rollback baseline. B: the frozen golden action stream for `generate_task(5000)` |
| Evidence class | Existing `calibration` class and required `d56-calibration` run-kind label, with a new S7 run reference and an explicit scripted-validation disclosure |
| Gate policy | `s7-scripted-serving-controls-v1`, with the exact rules below; no model accuracy, cost-performance, or latency-performance threshold |
| Serving terms | `demo` tier only; A: 4 fake attempts per exact version; B: 24; one active episode at a time |
| Bound | 77 qualification sends and 111 total per freeze; the owner-approved stopped predecessors plus final freeze have cumulative ceilings of 150/184. No automatic rerun, cap increase, package revision, or threshold adjustment; one later rehearsal remains separately gated |
| Infrastructure | Existing local Python environment, filesystem put-once store, SQLite ledger, and Darwin policy sandbox; HTTP listeners on `127.0.0.1` only |
| Recovery | Original run, one local archive copy and one private LFS copy with fresh-cache restore, subject to a fresh budget observation and GitHub's unchanged $0 stop-usage setting |

The evidence-class choice is a compatibility convention imposed by the existing schema, not an
assertion that this is historical D5.6 evidence. Every plan, summary, and report must identify it
as `credential-free-scripted-engineering-validation`. If that convention is unacceptable, stop
and propose a separately versioned scripted evidence class; do not silently change the frozen S2
schemas or use `confirmatory`.

Approval of this proposal permits the bounded implementation and qualification described here.
It does **not** approve an as-yet unbuilt package by digest, record a candidate approval, activate
serving, authorize publication, or decide S8/D5.10. After qualification, present the exact package
documents, gate-policy bytes and digest, gate reports, source bindings, and terms for explicit
owner approval before activation. Missing digests are blocking inputs, not placeholder hashes.

## Existing implementation and material gaps

Reuse these components rather than introduce a second serving system:

| Existing component | Evidence and use |
| --- | --- |
| Frozen package and API contracts | [`stateful_contracts.py`](../pixelgym/platform/stateful_contracts.py), [`stateful_control.py`](../pixelgym/platform/stateful_control.py), and their schema/contract tests |
| Durable attempts, checkpoints, intents, and sessions | [`serving_episode.py`](../pixelgym/platform/serving_episode.py), [`runner.py`](../pixelgym/grounding/v5/runner.py), [`test_serving_episode_host.py`](../tests/unit/platform/test_serving_episode_host.py) |
| Approval, terms, CAS activation, and history | [`control_store.py`](../pixelgym/platform/control_store.py), [`deployment.py`](../pixelgym/platform/deployment.py), [`test_stateful_control_plane.py`](../tests/unit/platform/test_stateful_control_plane.py) |
| Worker isolation and runtime recovery | [`policy_subprocess.py`](../pixelgym/platform/policy_subprocess.py), [`stateful_runtime.py`](../pixelgym/platform/stateful_runtime.py), [`test_stateful_serving_sandbox.py`](../tests/integration/test_stateful_serving_sandbox.py) |
| HTTP records and final screenshots | [`stateful_service.py`](../pixelgym/platform/stateful_service.py), [`test_stateful_service.py`](../tests/unit/platform/test_stateful_service.py) |
| Capture, backup, and restore | [Merged retention workflow](evidence-retention.md), [`retention.py`](../pixelgym/grounding/v5/retention.py), [`retention_lfs.py`](../pixelgym/grounding/v5/retention_lfs.py) |

The following list records gaps at the inspected PR #249 base. The S7 implementation now supplies
the gate evaluator, source-bound package builder, and guarded loader described here; see
[current implementation status](platform-design.md#current-status). The full serving rehearsal,
its recovery checks, and exact-package owner gates remain pending. References to absent tooling
or the "current loader" within this initial list describe the inspected base.

1. **S7 gate evaluation is absent.** S6 checks report consistency, identity bindings, and an
   approved policy digest; it does not define the required observations or derive them from
   stored evidence. Add a pure S7 evaluator that requires the exact observation set below and
   rejects missing, duplicate, extra, non-finite, or unsupported observations. The approved
   digest alone is not proof that the report used the approved rules.
2. **There is no ready first package.** Unit fixtures use placeholder hashes and a test-only
   threshold of 0.5. They are unsuitable approval inputs. The fixture manifest also uses dummy
   source/dependency digests. Construct real manifests and source bindings for A and B.
3. **Rollback needs a prior approved version.** A is a second, explicitly approved package so
   rollback has a healthy target with remaining capacity. An exhausted B cannot be reactivated;
   successful rollback must therefore target A, followed by a separate refusal to return to B.
4. **Serving receipts trust the client.** The server does not execute actions or verify task
   success. Retain the caller's FakeBackend checkpoints and dispatch ledger, then independently
   replay stored actions to verify rewards, termination, truncation, and screenshots.
5. **Worker source binding is an operator responsibility.** The loader resolves a registered
   source digest to a worker spec without hashing executable code again. Before registering that
   mapping, verify retained file hashes against the clean Git revision and bind the factory,
   kwargs/action stream, dependency graph, interpreter, and sandbox profile. A mismatch stops
   loading. Record this check; do not claim the current loader already performs it.
6. **Retention is necessary but insufficient.** The merged fixed seven-case verifier is not a
   serving-session verifier. Add a serving-specific verifier for the ledger, journals, sessions,
   client dispatches, immutable records, package bindings, and reports. A generic LFS receipt
   establishes restored bytes and SQLite integrity only.
7. **OS isolation needs a real local check.** Unit tests deliberately permit an unenforced
   worker. The rehearsal must require OS enforcement, retain its mechanism/profile digest,
   and stop if nested sandbox restrictions prevent it. No waiver is proposed. Local put-once
   storage does not establish storage-enforced WORM, power-loss durability, or availability.
8. **Publication can call models.** The checked-in Claude workflow runs on ready PR opening;
   the development record also describes CodeRabbit. All 29 locally inspected desktop
   automations were paused, but this does not establish GitHub App settings or future state.
   This implementation adds the `pixelgym:no-model-review` PR-body guard for Claude and
   documents the CodeRabbit `@coderabbitai ignore` marker in
   [the publication procedure](development-process.md). The owner authorized PR publication;
   verify both controls and the current automation state before each push. Activation and
   publication of qualification artifacts remain separate owner decisions.

These are bounded implementation prerequisites or disclosed limits. None requires reconstructing
the missing D5.9 journals, changing the environment contract, or starting a model experiment.

## Package and evidence specification

Use two exact `stateful-v5` packages, each with its own content-addressed identity and approval:

| Field | A: rollback baseline | B: golden script |
| --- | --- | --- |
| Proposed descriptive name | `s7-scripted-noop-v1` | `s7-scripted-seed5000-v1` |
| Factory | `pixelgym.scripted_policy.ScriptedStatefulPolicy` (the existing class, re-exported by the v5 runner; startup repair approved for a fresh qualification freeze) | Same existing factory |
| Actions | One literal `{action_type: 0, x: 0, y: 0, key: 0}` | Literal action stream generated once by `golden_actions` for task seed 5000, stored and hashed before qualification |
| Provider/model labels | `in-process-fake` / `no-cost-scripted-policy-v1` | Same labels; no model exists behind them |
| Expected task reward | 0; qualification stops after one NOOP with `pilot_action_limit`; explicit API close belongs to the rehearsal | Reward 1 exactly once on completion; all preceding rewards 0 |
| Qualification episode denominator | 1 assigned, 1 attempted, 0 exact successes | 1 assigned, 1 attempted, 1 exact success |
| Maximum steps | 1 | 26, the seed-5000 task horizon in the retained reference |
| Deployment attempt cap | 4 | 24, shared across restarts, redeploys, and rollback attempts for this exact version |

The [existing retention report](../artifacts/evidence-retention-20261020-v3/report.json) records
20 golden dispatches and a 26-step horizon for seed 5000. Those are planning references from a
known fixture, not new measurements or an independent holdout. Freeze the action-list digest and
assert the expected length before qualification; unexpected differences stop the campaign.
The golden script is privileged fixture data and cannot demonstrate visual grounding or an
answer-blind policy. Task specs, annotations, expected answers, and backend diagnostics must not
cross the serving request interface.

For both packages, freeze 1024×768 screenshots, `pixelgym-action-v1`, the current key allowlist,
the existing native coordinate adapter, parser/reducer versions, and these limits:

- One fake attempt per action; zero cancellation and reconciliation requests; no retries.
- A 5-second request deadline, cancellation mode `none`, reconciliation deadline 0, and the
  existing 10-second worker RPC timeout. Deadlines bound execution; they are not latency gates.
- A declared sandbox origin of `http://127.0.0.1:9`, matching the existing test convention.
  No listener or provider transport is attached there. The scripted transport is in-process.
  Any unexpected outbound connection or request to another transport stops the run.
- Clean, verified source, real SHA-256 digests, a recorded Python/dependency inventory and
  consumed dependency-lock digest, and `require_os_sandbox=True` for loader and preparer.
  Verify the installed runtime against the declared lock before qualification; hashing an
  unconsumed lock is insufficient. Do not resolve or download a new dependency graph during a run.
  Record resolved worker import roots and protected evidence paths privately.

Store an immutable qualification plan before execution. It names the two package specifications,
fixture/action hashes, exact code revision, gate-policy digest, case expectations, phase caps,
and every required evidence file. Resolve the execution manifest before calls. After qualification,
build the outer package using its stored summary digest, then create the gate report; this order
avoids a package/summary/report hash cycle. A later lifecycle report references these artifacts
without changing the qualification summary or package identity.

Reuse the retention campaign's seven fixed cases for shared runner controls: golden completion,
premature commit, step-limit exhaustion, malformed response, invalid action, call cap, and
interrupted-step recovery. Keep their outcomes and expected failures separate from each package's
one-episode denominator. The resume fixture recovers one step; it is not an extra completed
episode. Add A's one-NOOP qualification record separately. A and B's qualification episodes must
use their exact prospective worker specifications and real execution manifests. Reuse the case
definitions in the new driver; do not invoke the fixed campaign helper unchanged, because it
hardcodes a test manifest. Label the other six cases as shared control fixtures with their own
identities, rather than outputs of the golden package. The stored historical reports remain
unchanged; no historical journal or outcome is represented as new S7 evidence.

## Proposed gate rules

The pure evaluator reads the frozen plan and retained structured qualification results. It does
not execute policies, dispatch actions, retry a response, or run a restore. Every observation is
required, all must pass, comparisons use stored values, and equality passes. Structural errors
(malformed evidence, substituted policy rules, binding mismatches, and failed replay or artifact
verification) raise and stop before an approvable report exists. After sends begin, the driver
retains a sealed terminal failure with its durable count, including partial finalization outputs;
those outputs cannot be used for approval. A process kill or failed storage write can still
require manual recovery; never reset its ledger or hide its consumed sends. Missing observations
produce a failed gate report; supplied invalid numbers raise. A structurally valid package with
unverified/dirty provenance produces a failed report with stored reasons.

| Required observation | Comparator and threshold | Meaning |
| --- | --- | --- |
| `assigned_episode_coverage` | at least 1.0 | Exactly the one assigned package episode is present; duplicate, missing, or extra assignments are structural errors |
| `package_outcome_matches_plan` | at least 1 | A: exactly one NOOP, zero reward, `pilot_action_limit` followed by environment cleanup. B: 20 valid dispatches, one terminal reward of 1, earlier rewards 0, no truncation |
| `shared_control_cases_matched` | at least 7 | All seven retained case outcomes and counters match the frozen engineering plan, including expected failures |
| `invalid_backend_dispatches` | at most 0 | Invalid and unparseable actions never reach the backend |
| `duplicate_attempts_or_dispatches` | at most 0 | Recovery adds no duplicate send, action, or result application |
| `replay_mismatches` | at most 0 | Every replayed dispatch matches checkpoint, reward, terminal flags, and screenshot bytes; retain raw differing-pixel count and maximum channel delta before any tolerance |
| `unresolved_attempt_outcomes` | at most 0 | No unknown reservation is treated as a completed request; the unknown-outcome failure path is also tested separately |
| `artifact_verification_failures` | at most 0 | All plan, summary, source, action, journal, checkpoint, and immutable-object references resolve and verify |
| `real_provider_requests` | at most 0 | Declared constant of the fixture-only transport boundary, supported by retained fake-send counters and isolation checks; not an independent network measurement |
| `provider_control_requests` | at most 0 | No cancellation or reconciliation request in either proposed package |
| `monetary_spend_usd` | at most 0 | Declared zero-spend constant of the fixture-only boundary; no billing measurement or paid route is configured, and fake usage is never priced as model telemetry |

The detailed replay and artifact inspectors enforce `replay_mismatches`,
`artifact_verification_failures`, `invalid_backend_dispatches`, and
`shared_control_cases_matched` as exception-enforced preconditions. Completed reports contain
only the satisfied values for those checks; a discrepancy produces sealed failure evidence
before an approvable report exists. The stored report renders observation flags, provenance
flags, and reasons without making a milestone decision.

Additionally require `code_revision_passed=True`, correct package/report/plan/summary bindings,
the approved policy digest, an exact observation-name set, and no blocking reasons. Preserve
serving `attributed_cost_usd=null` where the implementation supplies no price; the no-spend
observation comes from the fixture-only execution boundary, not an invented provider cost.
Record local elapsed times and fake usage as diagnostics. There is no statistical inference,
provider-latency claim, quality ranking, or model promotion threshold.

Gate failure is terminal for that candidate. Do not replace a failing report or edit its summary
to obtain eligibility. Retain the failure and request approval for any successor plan/package.
Eligibility permits a human review; it never supplies candidate approval or serving terms. Run
the policy/report/evidence consistency check again at registration and before approval/activation,
so a hand-constructed report cannot bypass the proposed evaluator through the Python API. Bind
and verify the stored evaluation receipt rather than rerunning qualification.

## Phase caps and proposed rehearsal

Qualification permits at most **77 fake sends**: 76 across the existing seven cases (20 + 26 +
26 + 1 + 1 + 1 + 1), plus A's one NOOP. The real-provider cap is always zero. Unit regression
tests are recorded separately and never presented as qualification episodes.

After the exact package bundle has owner approval, record separate candidate approvals and
append-only demo terms through the existing Python control-store/coordinator APIs. Record the
owner's decision as the authority; a local synthetic audit actor is not authenticated human
identity. Do not fabricate that approval as part of the script.

Run this single ordered lifecycle in an isolated persistent state directory outside Git:

1. Register both packages with their stored reports and all bound immutable artifacts. Capture
   IDs, eligibility, and gate-policy binding. Attempt to deploy unapproved B and retain the
   rejection; the active pointer and attempt count must be unchanged.
2. Record A's authorized approval and demo terms, deploy A, execute one NOOP through `/api/v2`,
   and explicitly close with the caller's observed zero reward. A has consumed one of four.
3. Record B's authorized approval and demo terms, deploy B, and open its golden episode. On the
   first action, stop after `canonical_response_persisted`, before client dispatch. Preserve the
   server boundary and unchanged caller environment checkpoint. Recreate the app/runtime over
   the same stores, restoring the same deployment ID through `restore_active()`; do not redeploy.
   Resubmit the identical act context. The recovered action must consume no additional send.
4. Complete B's 20-action golden episode with the caller-owned `PixelGuiEnv`/`V5FakeBackend`.
   Retain every observed result and use `/close` for the final outstanding intent. Verify that
   reward 1 occurred exactly once. Repeating `/close` must not repeat work. B has consumed 20.
5. Redeploy the same B version with the existing coordinator. Its count must remain 20; episodes
   from an older deployment must be refused. In a new cap-probe episode, execute four more
   actions and submit the fourth result with the next act request. That request must seal
   `cap_reached` before a fifth send. B's total is exactly 24. A subsequent create must return
   `429 deployment_attempt_cap_reached`; repeated requests must not change the count.
6. Roll back to A, with a new deployment ID and its existing count of one. Demonstrate rejection
   of an old B episode as `deployment_changed`, then execute/close a new A NOOP. A's count is two.
   Attempt rollback to exhausted B: preparation must refuse it, append a failure event, and
   preserve A's active pointer. Neither redeploy nor rollback may reset B's count.
7. Stop workers, caller environments, service writers, and stores in `finally` cleanup; preserve
   all evidence. The final local state records A as the last selected version. Services stop
   after the demonstration. No unrelated stack or volume is removed.

The deployment ceilings total **28 fake attempts** (A 4, B 24); this sequence expects 26 sends.
Activation smoke episodes use separate temporary stores and do not consume these ceilings.
Permit at most **six one-send contract smokes**: A deploy, B deploy, B startup restore, B redeploy,
A rollback, and the refused return to exhausted B. The latter smoke currently occurs before the
preparer checks exhaustion. Candidate resets send nothing. Enforce a campaign guard of at most
**111 fake sends** across qualification, deployed traffic, and smokes (77 + 28 + 6), with zero
provider control requests and zero real-provider requests. Exceeding a phase or total cap stops
the campaign; no request is made to fill unused allowance. The campaign guard must reserve each
fake send durably before transport invocation, including smoke sends, and survive service restart.
Do not release an uncertain reservation. Local wall time is bounded to 30 minutes,
excluding a separately logged remote-transfer wait under the retention CLI's existing deadlines.

No cloud service, container/image download, OSWorld VM, live model, or full benchmark is needed.
If required local tools or dependencies are missing, report the prerequisite and stop; do not
add paid infrastructure or silently switch to an unenforced launcher.

## Evidence, reporting, and backup recovery

Keep the authoritative run directory private and outside every checkout. Retain at least:

- The approved proposal/qualification plan, gate-policy document, exact package documents,
  manifests, action streams, gate reports, serving terms, and the owner approval references.
- Source file manifest and bytes, clean Git revision binding, worker spec, dependency lock and
  installed-version inventory, sandbox profile/mechanism, and command/exit/status logs.
- Control ledger and append-only audit history; per-version attempt journals and session stores;
  immutable opened/step/closed records; request/intent identities and transport counts.
- Caller task/seed, screenshots, backend checkpoints, dispatch acknowledgments, replay results,
  interruption marker, before/after recovery counts, cap refusals, and rollback pointer checks.
- A structured lifecycle result, separate semantic-verification results, and generated report.
  Report raw command, exit status, counts, elapsed time, stdout, and stderr for acceptance checks.

A stored-results-only report subcommand must render qualification and lifecycle observations
without constructing a policy, transport, server, backend, or network client. Replay is a separate
explicit verifier operation, never a side effect of reporting. Test this boundary by denying those
constructors/network access during report generation. Keep all negative outcomes and missing data.
The human S8 review receives raw evidence and unmarked acceptance criteria; agents declare no
milestone verdict and change no public portfolio wording.

After quiescence, use the merged generic `capture`, `restore`, and `check-cleanup` operations to
retain one local archive copy under a new name and verify a fresh local restore. On each fresh
restored copy, run the retained serving verifier in an isolated credential-free interpreter:
verify all hashes/SQLite integrity,
replay stored caller actions, inspect recovery/cap/rollback evidence without invoking policy or
transport, and regenerate the report solely from stored results. Compare replay/report digests
with the originals and verify that inspection leaves the restored directory unchanged.

For the serving archive, use `backup_evidence_lfs` **without** `--validation-campaign`; that flag
executes only the trusted fixed seven-case verifier, not this new format. Preserve its generic
byte/SQLite receipt and a separate serving-semantic verification receipt. The unchanged fixed
retention campaign's existing dedicated verifier/receipt path remains unchanged; do not use it
for the new S7 qualification format either.

Before either upload or download, freshly observe GitHub's account-level Git LFS budget in the
billing UI: **$0**, **Stop usage enabled**, scope covering `elanthus/OSWorldTasks-evidence`.
Use a new private observation record, no more than 24 hours old at each transfer. A local timestamp
does not continuously verify GitHub's setting. Do not raise the budget if a transfer is refused.
Reverify repository privacy and preserve source/local archives on any failure.

Pin the uploaded full commit and independently retained archive-manifest digest. Restore from a
separate clone and empty LFS cache into a new directory, then repeat the serving-semantic checks
and stored-report comparison. Verify binding to the frozen source before executing its verifier.
A successful upload or generic receipt alone does not complete the rehearsal's recovery check.
Retain local evidence if remote recovery cannot complete; report that check as incomplete.

The owner approved one local archive and one private LFS copy because no writable second volume
was mounted. The local archive and original run share one disk; the verified private remote
copy provides the independent recovery path. Archive locations and approval records still need
operator access controls. Neither the rehearsal nor a cleanup receipt authorizes deleting the
source, archives, historical evidence, or a checkout.

## Acceptance evidence and implementation boundary

The following are proposed requirements, not completed checks:

| Requirement | Required observation or regression coverage |
| --- | --- |
| Gate evaluator fails closed | Missing/extra observations, wrong comparator/threshold, NaN/non-finite values, dirty/missing source, mismatched plan/summary/package, incomplete and duplicate assignments, tampered artifacts, unknown policy digest; threshold equality accepted |
| Rejection and separate approval | Unapproved B cannot deploy; failed gates cannot be approved; missing terms and calibration-to-standard promotion refused; rejection leaves pointer/counts unchanged |
| Real package binding | Actual factory/action/source/lock hashes verified; modified factory kwargs, source bytes, or lock prevent load; no placeholder digests accepted as provenance |
| Restart recovery | Existing per-boundary unit coverage retained; integrated app restart at canonical-response persistence restores the same deployment and one intent without duplicate send/dispatch/result application |
| Unsafe recovery refuses work | Unknown reserved outcomes seal infrastructure failure; changed screenshot/result context and unacknowledged client dispatch cannot trigger blind redispatch |
| Environment and API boundaries | Valid actions only, evaluator-owned reward timeline, caller dispatch evidence independently replayed, final result acknowledged once, stale-deployment requests refused |
| Caps and rollback | Exactly 24 B attempts, no send after exhaustion, count survives restart/redeploy, rollback to approved A succeeds, exhausted B cannot return, failed transition preserves pointer and appends audit evidence |
| Isolation and cleanup | Existing real Darwin sandbox check plus recorded rehearsal enforcement; credential-free worker; loopback listeners only; all providers/workers stopped on success, failure, or interruption |
| Report and restore | Reports read stored results only; one local archive restore and one fresh-cache private LFS restore reproduce evidence/report digests; corruption, missing objects, unsafe paths, stale budget observation, privacy mismatch, and changed source are refused |
| Evidence honesty | Raw negative outcomes retained, golden privilege and client trust disclosed, no model quality or availability claim, no historical reconstruction, no S8/D5.10 decision |

Expected implementation is a narrow S7 policy/evaluator plus a versioned package builder and
rehearsal/verify/report driver, with unit fixtures and opt-in local integration coverage. Reuse
existing host, worker, coordinator, API, and retention interfaces. No default stateful activation,
new UI, environment change, real transport, or frozen artifact edit is proposed. Publish successor
evidence only after separate publication authorization, redaction review, and a verified no-model
publication path. At that time use Agentic Preflight and a ready-for-review PR; never merge it
without authorization.

## Preparation verification

On the inspected base, the following existing tests were run as regression inspection, not as S7
qualification, a new experiment, or an acceptance gate:

```text
.venv/bin/python -m pytest -q tests/unit/platform/test_stateful_contracts.py tests/unit/platform/test_serving_episode_host.py tests/unit/platform/test_stateful_service.py tests/unit/platform/test_policy_subprocess.py tests/unit/platform/test_stateful_control_plane.py
exit 0; 119 passed, 4 warnings in 15.71s
```

The warnings were existing PyYAML/Metaflow deprecations. No rehearsal, OS sandbox integration,
budget observation, LFS transfer, or publication was performed in this preparation. The full
suite and retention results quoted in the handoff remain historical PR #249 results, not newly
executed checks. The D5.9 private journals remain missing. This successor plan cannot reconstruct
them, and D5.10 and public claims remain human-owned.
