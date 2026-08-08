# Day 1 — Build the Environment Core

## Outcome

By the end of Day 1, the repository should contain a deterministic synthetic form task and a Gymnasium-compliant environment that runs against a fast fake desktop backend. No OSWorld VM is required for today's acceptance gate.

The environment must already have the final behavioral contract:

- Screenshot pixels are the only observation.
- Actions are bounded click coordinates or individual keystrokes.
- Reward is `0.0` until an exact submitted answer, then `1.0` once.
- Successful completion terminates the episode.
- Reaching the step limit truncates the episode.
- The same seed produces the same task specification and initial application state.

## Ownership legend

- **YOU** — A decision, credential, manual judgment, or scope-control task that should remain with you.
- **AGENT · medium** — Bounded implementation with an explicit interface and test. Use a general coding agent with medium reasoning.
- **AGENT · high** — Architecture or correctness work where subtle mistakes could undermine the project. Use a coding agent with high reasoning.
- **PAIR** — An agent prepares the work; you inspect the result and make the final decision.

Reasoning effort trades speed for reliability. Medium is appropriate for well-specified implementation; high is reserved here for environment semantics and evaluator boundaries. This follows [official OpenAI guidance on reasoning effort](https://developers.openai.com/tracks/building-agents#reasoning-vs-nonreasoning-models).

Run one writing agent at a time unless two tasks touch completely disjoint paths. Ask every agent to preserve unrelated work and to report tests actually run.

## Schedule and task list

| ID | Timebox | Owner | Task | Concrete output |
|---|---:|---|---|---|
| D1.1 | 30 min | **YOU** | Freeze scope and success criteria | Written scope decision in this document or an issue |
| D1.2 | 45 min | **AGENT · medium** | Scaffold the Python project | Installable package, test layout, development commands |
| D1.3 | 90 min | **AGENT · medium** | Build the deterministic form app | Local app with seeded request and exact submission state |
| D1.4 | 60 min | **AGENT · high** | Define task and evaluator contracts | Typed task specification and privileged evaluator boundary |
| D1.5 | 120 min | **AGENT · high** | Implement the Gymnasium environment | Valid spaces, reset, step, reward, termination, truncation |
| D1.6 | 75 min | **AGENT · medium** | Build the fake backend and unit tests | Fast tests requiring no VM or network |
| D1.7 | 45 min | **PAIR** | Create and replay a golden trajectory | Passing successful trace and recorded reward sequence |
| D1.8 | 30 min | **YOU** | Run the Day 1 gate | Explicit pass/fail and carryover list |

## D1.1 — Freeze the scope

**Owner: YOU**

Write down these decisions before implementation begins:

- Core task: synthetic vendor-onboarding form.
- Visible source: a request card rendered beside the form.
- Required controls: four text fields, one dropdown, one radio group, one checkbox, and Submit.
- Task variants: seeded values only; one fixed layout on Day 1.
- Observation: RGB screenshot array only.
- Allowed action types: `NOOP`, `CLICK`, and `KEY`.
- Printable characters plus Tab, Enter, Backspace, and arrow keys are allowed.
- No arbitrary Python commands, shell actions, browser navigation commands, or accessibility-tree observations.
- File upload is a stretch goal after Day 2 passes.
- Spreadsheet automation is not part of this sprint.

Decision rule: if a proposed feature does not improve the Gym contract, evaluator correctness, validation evidence, or grounding experiment, defer it.

**Done when:** these choices are accepted without an unresolved “maybe” that changes the action space or reward definition.

## D1.2 — Scaffold the project

**Owner: AGENT · medium**

Create a small Python 3.12 project with this initial structure:

```text
pixelgym/
├── __init__.py
├── env.py
├── actions.py
├── task_spec.py
├── evaluator.py
├── backends/
│   ├── __init__.py
│   ├── base.py
│   └── fake.py
└── tasks/vendor_form/
    ├── __init__.py
    └── app/
tests/
├── unit/
└── integration/
artifacts/
scripts/
```

Keep dependencies narrow: Gymnasium, NumPy, Pillow, FastAPI, Uvicorn, pytest, and only the formatting/type-checking tools actually used. Put OSWorld behind an optional integration dependency so fast tests do not install its heavy stack.

Suggested agent handoff:

> Scaffold a Python 3.12 package for PixelGym-OSWorld. Add the package and test layout from `plans/day-1-environment-core.md`. Keep OSWorld optional. Add commands for formatting, unit tests, and a fake-backend demo. Do not implement the environment yet. Run the smallest relevant checks and report the files changed.

**Done when:** a fresh environment can install the package and execute an empty unit-test suite without OSWorld.

## D1.3 — Build the deterministic task application

**Owner: AGENT · medium**

Implement a minimal local application:

1. A seeded task generator creates synthetic but obviously fictional values.
2. The request card displays the desired values visually.
3. The form collects normalized values.
4. Submit records an immutable submission event containing the task ID, seed, and submitted values.
5. A reset endpoint clears all prior state and installs the newly generated task.
6. A privileged state endpoint is available to the host-side evaluator but is not linked from the UI.

Determinism requirements:

- No external network resources.
- No clocks, random animation, transitions, blinking cursors in captured stable frames, or system-dependent fonts.
- Fixed layout and CSS dimensions.
- Seeded generator output is serializable to canonical JSON.
- Task IDs derive from a hash of the canonical task specification.
- Reset is idempotent for the same seed.

Suggested agent handoff:

> Implement the deterministic vendor-onboarding task app described in Day 1. Use static HTML/CSS/JavaScript and a minimal FastAPI service. All randomness must come from an explicit seed. Add tests proving that identical seeds produce byte-identical canonical task JSON, different seeds change the task, and reset removes prior submissions. Do not add external frontend dependencies.

**Done when:** the app can be reset twice with one seed and returns the same task hash and empty submission state both times.

## D1.4 — Define the task and evaluator contracts

**Owner: AGENT · high**

Define typed interfaces before connecting the environment:

### Task specification

At minimum:

- `task_id`
- `seed`
- `instruction`
- expected normalized field values
- maximum episode steps
- application URL or launch descriptor

### Submission state

At minimum:

- `task_id`
- submitted field values
- `submitted_at_step` or monotonically increasing submission number
- whether the submission is final

### Evaluator result

Return structured evidence, not just a float:

```python
EvaluationResult(
    success: bool,
    score: float,
    submitted: bool,
    mismatched_fields: tuple[str, ...],
    task_id_matches: bool,
)
```

The environment converts `success` into sparse reward. It must not infer success from screenshot text, a success banner, focus position, action history, or an agent-issued `DONE`.

Threat-model the evaluator boundary:

- Stale submission from a previous episode.
- Submission for a different task seed.
- Correct values present but never submitted.
- Duplicate submission.
- Agent-declared completion without valid state.
- Partially written state observed during submission.

Suggested agent handoff:

> Design and implement the task, submission, and evaluator contracts for PixelGym. Treat reward integrity as the primary concern. Add tests for stale task IDs, unsubmitted correct fields, single-field near misses, and duplicate evaluation. Return structured diagnostic evidence while keeping the environment reward strictly sparse.

**Done when:** the evaluator has explicit tests for every threat above and no evaluator function accepts screenshot pixels as completion evidence.

## D1.5 — Implement the Gymnasium environment

**Owner: AGENT · high**

Implement `PixelGuiEnv(gymnasium.Env)` over a backend protocol.

### Observation space

```python
spaces.Box(
    low=0,
    high=255,
    shape=(height, width, 3),
    dtype=np.uint8,
)
```

### Action space

Use a fixed `spaces.Dict` containing:

- `action_type`: discrete `NOOP`, `CLICK`, or `KEY`
- `x`: integer coordinate in `[0, width)`
- `y`: integer coordinate in `[0, height)`
- `key`: index into a versioned allowlist

Document which fields are active for each action type. Reject malformed or noncanonical actions; do not silently clip coordinates or coerce data types.

### Reset contract

`reset(seed=...)` must:

1. Call `super().reset(seed=seed)`.
2. Generate the task only from the environment RNG or explicit seed.
3. Clear reward, step, and terminal state.
4. Reset the backend application.
5. Wait for a stable frame.
6. Return `(observation, info)`.

The `info` dictionary may contain task ID and validation hashes, but not expected answers or bounding boxes.

### Step contract

`step(action)` must:

1. Validate the action before executing it.
2. Translate it into a backend click or key event.
3. Capture the next screenshot.
4. Query privileged evaluator state.
5. Return reward `1.0` only on the first transition into success.
6. Set `terminated=True` on success.
7. Set `truncated=True` only when the step limit is reached without success.
8. Require reset after termination or truncation.

Suggested agent handoff:

> Implement `PixelGuiEnv` as a modern Gymnasium environment using the contracts in Day 1. Pay special attention to seeding, post-terminal behavior, action containment, one-shot sparse reward, and the distinction between `terminated` and `truncated`. Use only the backend protocol; do not import OSWorld in the core module. Run Gymnasium's environment checker against the fake backend.

**Done when:** the official environment checker passes against the fake backend and the step result always contains five values of the documented types.

## D1.6 — Build the fake backend and unit-test matrix

**Owner: AGENT · medium**

The fake backend should emulate only the contract needed by the environment:

- Reset with a task specification.
- Return a deterministic RGB frame.
- Accept a validated click or key action.
- Expose privileged submission state.
- Allow tests to install precise state transitions.

Required unit tests:

- Same-seed task and initial observation.
- Different-seed task variation.
- Observation space containment.
- Action-space samples are accepted.
- Boundary clicks at `(0, 0)` and `(width - 1, height - 1)`.
- Out-of-range and malformed actions are rejected.
- Correct values without Submit receive zero.
- Every golden-trajectory prefix receives zero.
- Final valid Submit receives one and terminates.
- Reward cannot fire a second time.
- Step limit truncates without terminating.
- Stepping after episode end raises a clear error.

Suggested agent handoff:

> Implement a deterministic fake backend and the Day 1 unit-test matrix. Keep tests fast and independent of network, browser, OSWorld, and wall-clock sleeps. Prefer explicit fixtures over mocks that duplicate implementation details. Report test count and runtime.

**Done when:** all unit tests pass in well under one minute on a laptop.

## D1.7 — Create the golden trajectory

**Owner: PAIR**

Agent work:

1. Create a deterministic scripted trajectory for one fixed seed.
2. Express it only through public environment actions.
3. Store the action list as a test fixture.
4. Record `(step, action_type, reward, terminated, truncated)` for each step.

Your review:

- Confirm the trajectory does not use privileged expected answers directly at runtime.
- Confirm it resembles legitimate UI interaction.
- Confirm all rewards are zero except the final valid submission.
- Confirm the final screenshot and evaluator evidence match the intended task.

Suggested agent handoff:

> Add a public-action golden trajectory for seed 7. Replay it through the fake environment, save the expected reward timeline as a fixture, and create a concise text trace suitable for the validation report. Do not bypass the action interface.

**Done when:** the checked-in trajectory reproduces exactly and its expected reward vector is `[0, ..., 0, 1]`.

## D1.8 — Day 1 acceptance gate

**Owner: YOU**

Run the documented install, unit-test, environment-checker, and fake-demo commands.

Mark Day 1 **PASS** only if:

- [ ] The package installs without OSWorld.
- [ ] The task app is deterministic for a fixed seed.
- [ ] The screenshot is the only observation.
- [ ] The action space contains only bounded clicks and allowlisted keys.
- [ ] The environment returns the modern five-value step result.
- [ ] Reward fires exactly once, on valid submission.
- [ ] Termination and truncation are distinct and tested.
- [ ] All fast tests pass without network or VM access.
- [ ] A golden trajectory exists as a public-action fixture.

If the gate fails, carry only correctness work into Day 2. Do not start the file-upload variant.

## End-of-day artifacts

- Installable `pixelgym` package.
- Deterministic vendor-form application.
- Fake desktop backend.
- Gymnasium environment and declared spaces.
- Structured task evaluator.
- Unit-test suite and environment-checker result.
- Golden trajectory with sparse reward timeline.
- Short Day 1 decision log listing deferred features.
