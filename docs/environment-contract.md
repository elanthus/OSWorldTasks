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

The validation evidence for these invariants is indexed in the
[evidence index](evidence-index.md). The reward-hacking audit classifies each attack surface as
blocked, tested, mitigated, or a known limitation.
