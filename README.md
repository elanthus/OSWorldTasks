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

# Format and lint
ruff format .
ruff check .

# Fake-backend demo (available once D1.5/D1.6 land)
python scripts/demo_fake_backend.py
```

Install the `osworld` extra (`pip install -e ".[osworld]"`) only for Day 2 integration work.
