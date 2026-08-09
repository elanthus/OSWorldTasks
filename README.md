# PixelGym-OSWorld

A three-day implementation plan for a validated, pixel-only GUI reinforcement-learning environment and grounding benchmark built on OSWorld-V2.

## Three-day plan

- [Day 1 — Environment core](plans/day-1-environment-core.md)
- [Day 2 — OSWorld integration and validation](plans/day-2-osworld-integration-and-validation.md)
- [Day 3 — Grounding experiment and portfolio package](plans/day-3-grounding-and-portfolio.md)

The plan assumes one constrained synthetic vendor-onboarding form as the core task. A file-upload variant is optional only after the core quality gates pass; a spreadsheet task is explicitly out of scope for this three-day sprint.

## Recommended agent roster

These are roles, not five agents that must run simultaneously. Reuse a role sequentially when practical.

| Role | Thinking | Use for |
|---|---|---|
| Builder agent | Medium | Scaffolding, deterministic app code, fake backend, capture tools, report plumbing |
| Environment architect | High | Gymnasium contract, seeding, evaluator boundary, reward and episode semantics |
| OSWorld integration agent | High | Provider adapter, VM synchronization, real task setup, cleanup |
| Validation red-team agent | High | Determinism interpretation, reward-hacking probes, residual-risk analysis |
| Experiment analyst | High | Leakage review, paired statistics, error taxonomy, cautious interpretation |

Use medium thinking for bounded tasks with explicit tests and high thinking where a subtle error could invalidate the project claim. Each day document contains copy-ready handoff prompts and a human acceptance gate.

## Development

Requires Python 3.12. The core package and fast test suite never require OSWorld.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Fast unit tests (no VM, no network)
pytest tests/unit

# Full fast suite, including the wheel-packaging integration test
pytest tests/

# Format and lint
ruff format .
ruff check .

# Fake-backend demo: reward trace for a scripted interaction (no VM, no network)
python scripts/demo_fake_backend.py
python scripts/demo_fake_backend.py --seed 7 --screenshot artifacts/fake_frame.png

# Golden trajectory: verify the committed fixture and detect drift (writes nothing)
python scripts/golden_trajectory.py check
```

`pip install -e ".[dev]"` is self-contained: a bare `python3.12 -m venv .venv` has no `setuptools`, and the `dev` extra pins `setuptools>=68` so `tests/integration/test_wheel_packaging.py` (which builds a real wheel with `pip wheel --no-build-isolation`) works without any extra manual install.

Install the `osworld` extra (`pip install -e ".[osworld]"`) only for Day 2 integration work.

## The golden trajectory

`tests/unit/fixtures/golden_trajectory_seed7.json` is 109 literal `NOOP`/`CLICK`/`KEY` actions that solve the seed-7 vendor-onboarding task, plus the `(step, action_type, reward, terminated, truncated)` timeline they must produce. It is replayed **blind** — `tests/unit/test_golden_trajectory.py` never reads the task generator, the widget layout, or the backend's expected values, so the reward timeline it observes cannot be an artifact of the test knowing the answer.

The recorded timeline is checked two ways, and both are load-bearing: replay must equal what the fixture records (drift), *and* what the fixture records must independently be zeros-then-exactly-one (correctness). Only the first would turn the fixture into a snapshot of whatever the code currently does.

Evidence generated from the fixture lives in [artifacts/day-1/](artifacts/day-1/) — structured JSON, a human-readable trace, and the final frame. The Markdown trace is generated from the JSON, never maintained by hand.

Regenerating is deliberately a four-step workflow. `generate` refuses to write the committed fixture in place: a layout or generator bug that silently rewrote the golden oracle would make the whole suite pass by moving the goalposts.

```bash
python scripts/golden_trajectory.py generate --seed 7 --output /tmp/golden_trajectory_seed7.json
```

```bash
python scripts/golden_trajectory.py verify --fixture /tmp/golden_trajectory_seed7.json
```

```bash
diff -u tests/unit/fixtures/golden_trajectory_seed7.json /tmp/golden_trajectory_seed7.json
```

Accept the diff only if you can name its cause — a task-spec change, a layout change, a screen-size change, or an action-contract change. An unexplained coordinate or keystroke change should block acceptance. Then copy the candidate over the fixture, rerun `check`, and regenerate the artifacts:

```bash
python scripts/golden_trajectory.py artifacts --fixture tests/unit/fixtures/golden_trajectory_seed7.json --output artifacts/day-1
```

## Day 1 acceptance gate

The commands below produce the evidence for the D1.8 checklist in [plans/day-1-environment-core.md](plans/day-1-environment-core.md). Running them is the gate; **reading the raw output and declaring pass or fail is the human's call**, not the tooling's.

```bash
python3.12 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"
```

```bash
pytest tests/ -q
```

```bash
python scripts/golden_trajectory.py check
```

```bash
python scripts/demo_fake_backend.py --seed 7
```

| Checklist item | Where the evidence comes from |
|---|---|
| Package installs without OSWorld | the `pip install -e ".[dev]"` above; the `osworld` extra is not installed |
| Task app is deterministic for a fixed seed | `tests/unit/test_vendor_form_generator.py`, `tests/unit/test_vendor_form_app.py` |
| Screenshot is the only observation | `tests/unit/test_env.py`, `pixelgym/env.py` (`info` carries `task_id` only) |
| Action space is bounded clicks and allowlisted keys | `tests/unit/test_actions.py` |
| Modern five-value step result | Gymnasium `check_env`, run inside `tests/unit/test_env.py` |
| Reward fires exactly once, on valid submission | `golden_trajectory.py check`, `tests/unit/test_golden_trajectory.py` |
| Termination and truncation are distinct | `tests/unit/test_env.py` |
| Fast tests pass without network or VM | `pytest tests/` |
| Golden trajectory exists as a public-action fixture | `tests/unit/fixtures/golden_trajectory_seed7.json`, [artifacts/day-1/](artifacts/day-1/) |
