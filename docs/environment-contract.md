# Environment contract

This document is the authoritative statement of the fifteen invariants that define the PixelGym
environment and grounding claim. The numbered text below is unchanged from the version previously
kept in section 3 of `AGENTS.md`; code comments and tests that cite an invariant by number refer
to this list.

A change that would relax any invariant is a scope change. It is raised as a question and decided
by a person; it is not made to let a task pass.

## Invariants

These define the project's claim. Do not weaken one to make a task easier; stop and raise it instead.

**Observation and action**

1. The **screenshot is the only observation**. No accessibility tree, no DOM, no text extraction into the observation.
2. Actions are exactly `NOOP`, `CLICK` (bounded x/y), and `KEY` (index into a versioned allowlist: printable characters, Tab, Enter, Backspace, arrows).
3. No arbitrary Python, shell, browser-navigation, or `DONE` actions are exposed to the agent. Never pass agent-supplied Python through to OSWorld.
4. Invalid actions are **rejected before backend execution**. Never silently clip coordinates or coerce types.

**Reward**

5. Reward is `0.0` until an exact valid submission, then `1.0` **exactly once**.
6. Success comes only from the **privileged host-side evaluator**. Never from screenshot pixels, a success banner, focus position, action history, or an agent's self-declaration.
7. Success sets `terminated=True`. Hitting the step limit sets `truncated=True`. The two are never conflated.
8. Stepping after episode end raises a clear error.

**Determinism**

9. Same seed ⇒ same canonical task JSON, same task hash, same initial application state. Task IDs derive from a hash of the canonical spec.
10. The task app has no network calls, clocks, animation, transitions, blinking cursors in stable frames, or system-dependent fonts. Fixed layout and CSS dimensions.
11. Reset is idempotent for a given seed and clears all prior submissions.

**Boundaries**

12. `PixelGuiEnv` talks only to the backend protocol. **No OSWorld import in the core module.**
13. `info` may carry task ID and validation hashes. It must never carry expected answers or bounding boxes.
    *Note:* `PixelGuiEnv` puts only an opaque per-episode ID (`episode_id`, random at every reset) in `info`, never the task hash. The task ID is a pure function of the seed, so exposing it would let an agent regenerate the answers with the public generator; host-side code reads it from the `PixelGuiEnv.task_id` property.
14. Dataset-capture instrumentation (bounding boxes) is build-time only and must not be reachable from the evaluation adapter.
15. Set-of-marks candidates are generated **without consulting the requested target**. Marking only the ground-truth element leaks the answer.

## Where the invariants are enforced

| Invariants | Enforcing code | Tests |
| --- | --- | --- |
| 1–4 (observation and action) | [`pixelgym/env.py`](../pixelgym/env.py), [`pixelgym/actions.py`](../pixelgym/actions.py) | [`test_env.py`](../tests/unit/test_env.py), [`test_actions.py`](../tests/unit/test_actions.py), [`test_action_contract_properties.py`](../tests/unit/test_action_contract_properties.py) |
| 5–8 (reward) | [`pixelgym/env.py`](../pixelgym/env.py), [`pixelgym/evaluator.py`](../pixelgym/evaluator.py) | [`test_env.py`](../tests/unit/test_env.py), [`test_evaluator.py`](../tests/unit/test_evaluator.py), [`test_evaluator_contract_properties.py`](../tests/unit/test_evaluator_contract_properties.py) |
| 9–11 (determinism) | [`pixelgym/task_spec.py`](../pixelgym/task_spec.py), [`pixelgym/backends/base.py`](../pixelgym/backends/base.py), [`pixelgym/tasks/vendor_form/render.py`](../pixelgym/tasks/vendor_form/render.py) | [`test_fake_backend.py`](../tests/unit/test_fake_backend.py), [`test_golden_trajectory.py`](../tests/unit/test_golden_trajectory.py) |
| 12–13 (environment boundary) | [`pixelgym/env.py`](../pixelgym/env.py), [`pixelgym/backends/base.py`](../pixelgym/backends/base.py) | [`test_core_import_boundaries.py`](../tests/unit/test_core_import_boundaries.py), [`test_env.py`](../tests/unit/test_env.py) |
| 14–15 (grounding boundary) | [`pixelgym/grounding/capture.py`](../pixelgym/grounding/capture.py), [`pixelgym/grounding/evaluation.py`](../pixelgym/grounding/evaluation.py) | [`test_grounding_capture_instrumentation_boundary.py`](../tests/unit/test_grounding_capture_instrumentation_boundary.py), [`test_grounding_overlays.py`](../tests/unit/test_grounding_overlays.py) |

## Input contract and acknowledgement barrier

These mechanisms are not invariants. They are how the fake backend and the real page agree on
what an input does (invariants 1, 2, and 9–10) and how a browser backend knows an input has been
processed before reward is read (invariants 5–7).

| Mechanism | What it is | Enforced in | Tests |
| --- | --- | --- | --- |
| Input contract `pixelgym-vendor-form-input-v3` | The shared rule for what each CLICK and KEY does. Tab walks a fixed order, and Tab from Submit leaves the form. Text inputs keep one caret pinned to the end of the value, and arrows are inert in them. The focused text input draws a static caret after its last character, so a trailing space is visible (v3). The country select moves by ArrowUp/ArrowDown and first-letter search; Enter and Space are inert on it (v3). Radios follow standard group semantics. | [`ui.py`](../pixelgym/tasks/vendor_form/ui.py) (`INPUT_CONTRACT_VERSION`, `FormState`), [`app.js`](../pixelgym/tasks/vendor_form/app/static/app.js) (`installInputContract`, `updateCaret`), [`render.py`](../pixelgym/tasks/vendor_form/render.py) (`RENDERER_VERSION`) | [`test_fake_backend.py`](../tests/unit/test_fake_backend.py), [`test_frame_determines_oracle_action.py`](../tests/unit/test_frame_determines_oracle_action.py); opt-in [`test_vendor_form_differential.py`](../tests/integration/test_vendor_form_differential.py), [`test_vendor_form_frame_determines_action.py`](../tests/integration/test_vendor_form_frame_determines_action.py) |
| Acknowledgement barrier | The page acknowledges each trusted agent input (one primary mouseup per CLICK, one non-modifier keyup per KEY). It does so only after the input task has run, any submission it started has settled, and the next frame is committed. After its Nth input the host waits for exactly N acknowledgements before capturing the observation and reading submissions. Correctness rests on the count; the deadline only fails closed. | [`app.js`](../pixelgym/tasks/vendor_form/app/static/app.js) (`acknowledgeInput`), [`settle.py`](../pixelgym/backends/settle.py), [`osworld.py`](../pixelgym/backends/osworld.py) (`_settle_input`) | [`test_backend_settle.py`](../tests/unit/test_backend_settle.py), [`test_osworld_input_barrier.py`](../tests/unit/test_osworld_input_barrier.py); opt-in [`test_vendor_form_input_barrier.py`](../tests/integration/test_vendor_form_input_barrier.py) |
| `POST /api/input-ack`, `GET /api/input-ack` | The page posts `{task_id, page, count}` for its Nth acknowledgement. The server accepts it only for the active task, the current page token, and `count` equal to the previous count plus one. The GET returns the count. Acknowledgements never enter privileged submission state. | [`server.py`](../pixelgym/tasks/vendor_form/app/server.py), [`guest_server.py`](../pixelgym/tasks/vendor_form/app/guest_server.py) (`acknowledge_input`); the guest read is [`guest_programs/input_ack.py`](../pixelgym/tasks/vendor_form/guest_programs/input_ack.py) | [`test_vendor_form_app.py`](../tests/unit/test_vendor_form_app.py), [`test_vendor_form_guest_server.py`](../tests/unit/test_vendor_form_guest_server.py) |
| `POST /api/page-ready`, `GET /api/page-ready` | After the task is fetched, rendered, and the bundled fonts load, the page reports ready for the active task. The server refuses a stale task, sets the acknowledgement count to zero, and returns a fresh page token. Guest setup waits for two consecutive ready reads before the first observation. | [`app.js`](../pixelgym/tasks/vendor_form/app/static/app.js) (`initializePage`), both servers (`mark_page_ready`), [`osworld_task.py`](../pixelgym/tasks/vendor_form/osworld_task.py) (setup, `guest_programs/page_ready.py`) | [`test_vendor_form_app.py`](../tests/unit/test_vendor_form_app.py), [`test_osworld_task.py`](../tests/unit/test_osworld_task.py) |
| Page token | An integer the server bumps on every reset and page-ready, never random. A late acknowledgement from an earlier page carries an old token and is refused, even after a same-seed reset that keeps the task ID. | both servers (`page_generation`), [`app.js`](../pixelgym/tasks/vendor_form/app/static/app.js) (`pageToken`) | [`test_vendor_form_app.py`](../tests/unit/test_vendor_form_app.py) (`test_late_acknowledgement_from_the_previous_page_is_refused_after_same_seed_reset`) |
| Fixed setup acknowledgement count | Guest setup sends the page no acknowledged input; the pointer is parked with a move only, and setup checks the reported position. `OSWorldBackend.reset` requires exactly `SETUP_ACKNOWLEDGED_INPUTS` (0) and never adopts the page's count. | [`osworld_task.py`](../pixelgym/tasks/vendor_form/osworld_task.py) (`SETUP_ACKNOWLEDGED_INPUTS`, `_park_pointer`), [`osworld.py`](../pixelgym/backends/osworld.py) (`reset`) | [`test_osworld_task.py`](../tests/unit/test_osworld_task.py), [`test_osworld_input_barrier.py`](../tests/unit/test_osworld_input_barrier.py), model in [`osworld_setup_model.py`](../tests/support/osworld_setup_model.py) |

The reward-hacking classification of these endpoints is in the
[2026-11 addendum](../artifacts/reward-hacking-addendum-2026-11.json).

**Known limitations.**

- Tab order, the country select's keyboard behaviour, and the caret are defined by the task
  app's JavaScript, not by native browser behaviour. The page pins them so the fake can model
  them and so they do not depend on the platform, a timer, or state that is invisible in pixels.
  An agent trained here therefore learns this app's contract. It does not learn native Chromium
  behaviour, for example a timed multi-key type-ahead, a select popup, or a caret that clicks can
  place.
- Pixel parity between the fake renderer and Chromium is not claimed. The claim is that each
  backend's frame is a function of the visible form state; the frame/action tests check that the
  oracle never acts differently on the same frame.
- `gym.make("PixelGym/VendorForm-v0", max_episode_steps=N)` does not set the environment's own
  step limit. Gymnasium adds a separate `TimeLimit(N)` on top of it. Use `episode_step_limit=N`
  ([`registration.py`](../pixelgym/registration.py),
  [`test_registration_vector.py`](../tests/unit/test_registration_vector.py)).

The validation evidence for these invariants is indexed in the
[evidence index](evidence-index.md). The reward-hacking audit classifies each attack surface as
blocked, tested, mitigated, or a known limitation.
