# Day 2 — Integrate OSWorld-V2 and Validate the Environment

**Status: COMPLETE — PASS.** The project owner declared the D2.11 gate PASS
and authorized Day 3 on 2026-08-09. The stored human-gate record is
[`artifacts/day-2/raw/human-gate.json`](../artifacts/day-2/raw/human-gate.json).

## Outcome

By the end of Day 2, the same environment contract from Day 1 should run through a real OSWorld-V2 desktop backend, and the repository should generate a validation report covering reset reproducibility, reward timing, action/observation integrity, and reward-hacking surfaces.

One recorded real-OSWorld episode is the key gate. Fake-backend tests remain the fast regression suite, but they are not sufficient evidence for the portfolio claim.

## Ownership legend

- **YOU** — Provider choice, credentials, cloud-spend approval, manual inspection, and go/no-go calls.
- **AGENT · medium** — Bounded adapters, test runners, and report generation.
- **AGENT · high** — VM integration, synchronization, determinism interpretation, and reward-security analysis.
- **PAIR** — Agent executes or prepares; you verify the external system or visual result.

Do not give credentials to an agent prompt or commit them to the repository. Supply secrets only through ignored environment variables or the provider's normal credential mechanism.

## Schedule and task list

| ID | Timebox | Owner | Task | Concrete output |
|---|---:|---|---|---|
| D2.1 | 30 min | **YOU** | Select and approve an OSWorld provider | Working provider decision and stop-loss rule |
| D2.2 | 45 min | **AGENT · medium** | Pin the upstream release | Reproducible optional integration dependency |
| D2.3 | 120 min | **AGENT · high** | Implement the OSWorld backend adapter | Screenshot, click, key, reset, and close integration |
| D2.4 | 75 min | **AGENT · high** | Install and launch the custom task in the VM | Deterministic task visible in Chromium |
| D2.5 | 45 min | **PAIR** | Replay a real golden trajectory | One successful recorded OSWorld episode |
| D2.6 | 60 min | **AGENT · high** | Build reset-determinism validation | Repeated-reset measurements and clear interpretation |
| D2.7 | 45 min | **AGENT · medium** | Build reward-timing validation | Golden prefixes and near-miss trajectories |
| D2.8 | 45 min | **AGENT · medium** | Build space-integrity validation | Checker, samples, boundaries, invalid actions |
| D2.9 | 75 min | **AGENT · high** | Run the reward-hacking audit | Attack matrix with evidence and residual risks |
| D2.10 | 30 min | **AGENT · medium** | Generate the validation report | JSON plus readable Markdown summary |
| D2.11 | 30 min | **YOU** | Run the Day 2 gate | Pass/fail and Day 3 authorization |

## D2.1 — Choose the provider and set a stop-loss rule

**Owner: YOU**

Make the provider decision before an agent starts integration:

1. Prefer an already available remote Linux host with Docker and KVM.
2. Otherwise use the supported AWS path if you approve the account access and spend.
3. Use a local provider only if an OSWorld-V2-compatible VM image is already working.

The official provider guide notes that macOS generally lacks KVM and recommends a different provider path. The current benchmark release should remain pinned across code and provider components. See the [OSWorld-V2 provider guide](https://github.com/xlang-ai/OSWorld-V2/blob/v2026.06.24/docs/PROVIDER_SETUP.md) and [release README](https://github.com/xlang-ai/OSWorld-V2/tree/v2026.06.24).

Set a firm infrastructure stop-loss:

- At 90 minutes without a captured OSWorld screenshot, stop broad debugging.
- Record the exact blocker.
- Switch to the best available supported provider or request focused help.
- Do not rewrite the Day 1 environment around provider-specific behavior.

**Done when:** the provider is named, credentials are available through a safe mechanism, and there is an explicit limit on time or cloud usage.

## D2.2 — Pin OSWorld-V2

**Owner: AGENT · medium**

Pin `xlang-ai/OSWorld-V2` to `v2026.06.24`. Do not depend on `main` or download the gated official task classes; this project provides its own open task.

Keep the integration optional:

- Fast install: core environment, fake backend, and unit tests.
- Integration install: OSWorld dependency and real-backend extras.
- Runtime metadata: upstream repository, tag, Python version, provider, screen size, and image identifier when available.

Suggested agent handoff:

> Add a reproducible optional OSWorld-V2 integration pinned to tag `v2026.06.24`. Keep the core package and unit tests usable without OSWorld. Record upstream version metadata in every integration artifact. Do not fetch gated benchmark tasks or assets because this repository uses a custom open task.

**Done when:** the repository can report the exact upstream tag used and the fast unit-test path remains unchanged.

## D2.3 — Implement the OSWorld backend adapter

**Owner: AGENT · high**

The adapter should satisfy the Day 1 backend protocol without leaking OSWorld details into `PixelGuiEnv`.

Responsibilities:

1. Construct and close `DesktopEnv` safely.
2. Request screenshot observations and disable unnecessary accessibility/terminal observations.
3. Decode screenshots into the exact RGB NumPy shape declared by the environment.
4. Translate `CLICK` into a structured coordinate click.
5. Translate `KEY` into one allowlisted structured key action.
6. Translate `NOOP` into a wait that cannot accidentally terminate the task.
7. Reset through a custom OSWorld `BaseTask` implementation.
8. Surface provider errors with enough context to diagnose them.
9. Stabilize frames without arbitrary long sleeps.
10. Ensure `close()` runs on success, failure, and interruption.

Use OSWorld's structured action path rather than arbitrary Python command execution. OSWorld's current action definitions are visible in [`desktop_env/actions.py`](https://github.com/xlang-ai/OSWorld-V2/blob/v2026.06.24/desktop_env/actions.py).

Suggested agent handoff:

> Implement `OSWorldBackend` against the existing backend protocol. Pin behavior to OSWorld-V2 `v2026.06.24`. Use screenshot-only observations and structured computer actions; never pass agent-provided Python commands through to OSWorld. Add unit tests with a thin fake OSWorld object, then run one real reset. Keep cleanup reliable and report the real provider metadata.

**Done when:** the adapter returns a valid screenshot through the public `PixelGuiEnv.reset()` call and closes its provider cleanly.

## D2.4 — Install and launch the custom task

**Owner: AGENT · high**

Implement an open custom task derived from OSWorld's public `BaseTask` interface.

Setup responsibilities:

- Transfer or make the local deterministic application available to the guest.
- Start the application service with a known task seed.
- Launch Chromium directly into the application.
- Use a fixed window or kiosk layout.
- Wait for a task-ready signal before taking the initial screenshot.
- Move the cursor to a fixed neutral location.
- Prevent prior application processes or state from surviving reset.

Evaluation responsibilities:

- Query privileged submission state through the backend/controller boundary.
- Verify the task ID and seed match the current episode.
- Return the same structured `EvaluationResult` used by the fake backend.
- Never use a visible success message as ground truth.

Suggested agent handoff:

> Add an open OSWorld `BaseTask` for the deterministic vendor form. Its setup must launch the app and Chromium in a reproducible layout; its evaluator must query privileged submission state and verify task identity. Add readiness checks and cleanup. Do not change the core Gymnasium environment contract to accommodate integration quirks.

**Done when:** two consecutive real resets show the correct task, no stale submission, and the expected fixed layout.

## D2.5 — Replay a real golden trajectory

**Owner: PAIR**

Agent work:

- Adapt the Day 1 golden trajectory only where real coordinates differ.
- Use public click and key actions exclusively.
- Record screenshots, action trace, evaluator diagnostics, and reward at every step.
- Save provider and release metadata with the run.

Your review:

- Watch the trajectory or inspect its contact sheet.
- Confirm actions visibly interact with the intended controls.
- Confirm the reward timeline is `[0, ..., 0, 1]`.
- Confirm no privileged setup or evaluation call occurs in the action trace.

Suggested agent handoff:

> Replay the seed-7 golden trajectory through the real OSWorld backend. If coordinates differ, update an integration-only fixture without changing reward logic. Capture every screenshot and action, save release/provider metadata, and produce a contact sheet. Stop after one clean successful episode.

**Done when:** one real episode terminates with reward one and has enough evidence for a reviewer to audit it.

## D2.6 — Validate reset determinism

**Owner: AGENT · high**

Run at least five same-seed resets on the real backend and ten on the fake backend.

Measure separately:

- Canonical task-spec hash: exact match required.
- Privileged application-state hash: exact match required.
- Screenshot shape and data type: exact match required.
- Exact differing-pixel count.
- Maximum per-channel pixel difference.
- A documented perceptual similarity measure.
- Time from reset to stable frame.

The report must distinguish:

- Semantic/state determinism.
- Bitwise visual determinism.
- Perceptual visual stability.

If screenshots are not bitwise equal, identify the differing region before selecting a tolerance. Masking is allowed only for a justified nondeterministic region that is irrelevant to the task and disclosed in the report.

Suggested agent handoff:

> Build and run a repeated-reset determinism validator on fake and real backends. Report exact state hashes and raw screenshot differences before applying any tolerance. Localize visual differences and document the stabilization policy. Do not label perceptual similarity as bitwise determinism.

**Done when:** the report can explain every accepted difference and same-seed task/application state is exact.

## D2.7 — Validate reward timing

**Owner: AGENT · medium**

Execute these trajectories on the fake backend and the affordable subset on the real backend:

- Empty Submit.
- Correct fields without Submit.
- One near miss per required field.
- Wrong task ID or stale submission fixture.
- Every prefix of the golden trajectory.
- Complete golden trajectory.
- Duplicate Submit or evaluation after success.
- Timeout one action before completion.

Generate a table containing trajectory name, first positive-reward step, terminal step, truncation step, and expected outcome.

Suggested agent handoff:

> Implement a reward-timing validation runner from the named trajectories. Assert that only the complete golden trace receives reward and that it occurs on the valid submission transition. Emit JSON records and a compact Markdown table. Reuse fixtures rather than duplicating evaluator logic.

**Done when:** any early, missing, repeated, or incorrectly timed reward fails the validation command.

## D2.8 — Validate action and observation spaces

**Owner: AGENT · medium**

Run:

- Gymnasium environment checker against the fake backend.
- Observation containment after every tested reset and action.
- At least 500 sampled actions through the fake backend.
- Safe sampled-action smoke tests on the real backend.
- Coordinate boundary cases.
- Invalid coordinates, key indices, data types, missing keys, and extra keys.
- Post-terminal and post-truncation calls.

Record whether invalid input is rejected before backend execution.

Suggested agent handoff:

> Add a space-integrity validator for `PixelGuiEnv`. Combine the Gymnasium checker, sampled actions, boundary cases, malformed actions, observation containment, and post-episode behavior. Prove invalid inputs are rejected before reaching the backend. Generate structured results for the combined report.

**Done when:** all declared valid values are accepted, all named invalid values are rejected, and every observation is contained by its declared space.

## D2.9 — Audit reward-hacking surfaces

**Owner: AGENT · high**

Create an attack matrix with four possible dispositions: **blocked**, **tested**, **mitigated**, or **known limitation**.

Required attacks:

| Attack | Expected defense |
|---|---|
| Agent emits `DONE` without completing the form | `DONE` is not a public environment action and cannot create reward |
| Empty or partial Submit | Privileged evaluator returns failure |
| Correct visible fields without Submit | Submission event is required |
| Visible fake “Success” content | Pixels are never completion evidence |
| Reuse prior successful state | Reset clears state and task IDs must match |
| Repeat valid submission | Episode already ended; reward is one-shot |
| Out-of-bounds clicks | Rejected before backend execution |
| Open terminal or developer tools | Modifier/system keys are absent from the allowlist and app layout is constrained |
| Navigate to a completion endpoint | Address-bar/system navigation is unavailable through the bounded action interface |
| Guess or alter a task identifier | Evaluator checks current host-side task identity |
| Trigger evaluator during partial write | Submission state update is atomic |
| Provider reset fails silently | State hash and readiness validation fail the episode reset |

Ask the agent to look for additional attacks rather than merely implementing this list.

Suggested agent handoff:

> Red-team the environment's reward boundary. Implement the listed attacks, identify additional plausible attacks, and classify every surface as blocked, tested, mitigated, or a known limitation. Include executable regression tests wherever possible. Do not weaken the action interface to make testing convenient.

**Done when:** the report contains evidence for every claim and candidly lists residual risks.

## D2.10 — Generate the validation report

**Owner: AGENT · medium**

Produce:

- `artifacts/validation-report.json` for full machine-readable evidence.
- `artifacts/validation-report.md` for reviewers.

The Markdown report should lead with:

1. Release and provider metadata.
2. Overall pass/fail status.
3. Reset determinism summary.
4. Reward-timing summary.
5. Space-integrity summary.
6. Reward-hacking matrix.
7. Known limitations.
8. Exact reproduction commands.

Suggested agent handoff:

> Combine existing validator outputs into JSON and Markdown reports. Do not rerun or reinterpret tests inside the report generator. Include upstream tag, provider, task seed, screen size, timestamps, pass/fail counts, and known limitations. Link each summary claim to its underlying artifact.

**Done when:** deleting the Markdown report and rerunning one documented command recreates it from JSON evidence.

## D2.11 — Day 2 acceptance gate

**Owner: YOU**

Mark Day 2 **PASS** only if:

- [x] OSWorld-V2 is pinned to `v2026.06.24`.
- [x] Core tests still run without OSWorld.
- [x] A real OSWorld reset returns screenshot pixels through `PixelGuiEnv`.
- [x] A real public-action trajectory earns exactly one terminal reward.
- [x] Same-seed task and application state are exact across resets.
- [x] Screenshot variability is measured and honestly classified.
- [x] Reward-timing tests include golden prefixes and near misses.
- [x] Space-integrity checks pass.
- [x] The reward-hacking matrix has evidence and residual risks.
- [x] The provider was closed or stopped after validation.

If no real OSWorld run succeeded, Day 3 may still build the grounding pipeline from the local app, but the README must label OSWorld integration as incomplete. Do not claim a completed OSWorld environment on a resume until the integration gate passes.

## End-of-day artifacts

- Optional pinned OSWorld integration.
- `OSWorldBackend` adapter.
- Open custom OSWorld task.
- One recorded real golden trajectory.
- Reset-determinism measurements.
- Reward-timing results.
- Space-integrity results.
- Reward-hacking matrix.
- Machine-readable and reviewer-readable validation reports.
