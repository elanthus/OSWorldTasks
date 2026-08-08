# AGENTS.md — PixelGym-OSWorld

Operating instructions for any coding agent working in this repository. Read this before touching code.

## 1. What this project is

PixelGym-OSWorld is a **pixel-only GUI reinforcement-learning environment** and a **GUI-grounding benchmark**, built on OSWorld-V2 and delivered as a three-day sprint. The deliverable is not "a working demo" — it is a working environment plus **evidence** that the environment is correct: reset determinism, reward timing, space integrity, and a reward-hacking audit.

The single core task is one deterministic synthetic **vendor-onboarding form**.

Source of truth for scope and sequencing:

- [README.md](README.md) — project claim and agent roster
- [plans/day-1-environment-core.md](plans/day-1-environment-core.md) — task app, contracts, Gymnasium env, fake backend, golden trajectory
- [plans/day-2-osworld-integration-and-validation.md](plans/day-2-osworld-integration-and-validation.md) — OSWorld adapter, custom task, validation suite, reward-hacking audit
- [plans/day-3-grounding-and-portfolio.md](plans/day-3-grounding-and-portfolio.md) — grounding dataset, set-of-marks experiment, analysis, portfolio package

If this file and a day plan disagree, the day plan wins for task detail; this file wins for process and invariants.

## 2. Current repository state

Planning documents only. There is **no code yet**. The first implementation task is D1.2 (project scaffold).

Target layout once scaffolded (from Day 1):

```text
pixelgym/
├── env.py            # PixelGuiEnv — Gymnasium contract only, no OSWorld imports
├── actions.py        # NOOP / CLICK / KEY, versioned key allowlist
├── task_spec.py      # typed task + submission contracts
├── evaluator.py      # privileged, host-side, structured EvaluationResult
├── backends/         # base.py protocol, fake.py, later osworld.py
└── tasks/vendor_form/app/   # deterministic HTML/CSS/JS + FastAPI service
tests/{unit,integration}/
artifacts/            # validation + grounding reports, generated evidence
scripts/
```

Python 3.12. Narrow dependencies: Gymnasium, NumPy, Pillow, FastAPI, Uvicorn, pytest, plus only the formatting/type-checking tools actually wired up. **OSWorld is an optional extra** — the fast unit path must install and run without it.

## 3. Non-negotiable invariants

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

## 4. Human gates — stop and ask

Some tasks in the plans are owned by **YOU** (the human), not by an agent. An agent must prepare work up to these points and then stop:

- **Scope changes** (D1.1). If a feature does not improve the Gym contract, evaluator correctness, validation evidence, or the grounding experiment — defer it. Do not add it and ask later.
- **Day gates** (D1.8, D2.11, D3.11). Agents run the documented checks and report **raw results only** — command, exit status, counts, full output. Do not summarize a gate as passing, do not offer a provisional PASS/FAIL, and do not tick the checklist boxes. The human reads the raw evidence and declares the verdict.
- **Provider choice and cloud spend** (D2.1), including the 90-minute infrastructure stop-loss.
- **Any paid model call** (D3.5, D3.6). Run the ten-example pilot only after explicit approval, stop at twenty condition calls, and do not continue to the full run without a second approval.
- **Public claims** — README wording, demo media, resume bullets (D3.9–D3.11).

Stretch work (file upload, extra VLM, LibreOffice, multi-resolution) is **blocked** until the core project is reproducible and public claims are approved. A spreadsheet task is out of scope for this sprint.

## 5. Working agreements

- **One writing agent at a time** unless two tasks touch completely disjoint paths.
- **Preserve unrelated work.** Do not reformat, refactor, or "tidy" files outside the task.
- **Report the tests you actually ran**, with counts and runtime. Never describe a check you did not execute.
- Match the reasoning effort the plan assigns: `AGENT · medium` for bounded implementation with an explicit test; `AGENT · high` for environment semantics, evaluator boundaries, integration, determinism interpretation, and statistics.
- Prefer explicit fixtures over mocks that restate implementation details.
- Fast tests must not touch network, browser, OSWorld, or wall-clock sleeps, and must finish well under a minute.
- Integration work goes behind the optional extra; it never changes the core environment contract to accommodate a provider quirk.
- **Dev setup must be self-contained.** `python3.12 -m venv .venv && pip install -e ".[dev]"` is the only setup step anyone (human or agent) should ever need to run the fast suite and lint. If a test needs a build tool (e.g. `setuptools` for `tests/integration/test_wheel_packaging.py`), add it to the `dev` extra in `pyproject.toml` — never document a manual `pip install <tool>` workaround instead.

## 6. Secrets and safety

- Never put credentials or API keys in a prompt, source file, committed artifact, captured terminal, or Git history. Use ignored environment variables only.
- Redact provider responses before anything is published.
- Do not fetch gated OSWorld benchmark tasks or assets. This repo ships its own open task.
- Pin `xlang-ai/OSWorld-V2` to `v2026.06.24`; never depend on `main`. Record upstream tag, provider, Python version, screen size, and image ID in every integration artifact.
- Close or stop the provider after any validation run, including on failure and interruption.

## 7. Evidence and reporting standards

The portfolio claim lives or dies on honesty here.

- Reports are **generated from stored structured evidence**. The report generator must not rerun or reinterpret tests.
- Distinguish **semantic/state determinism**, **bitwise visual determinism**, and **perceptual visual stability**. Never label a perceptual similarity score as bitwise determinism.
- Report raw screenshot differences (differing-pixel count, max per-channel delta) *before* applying any tolerance. Masking requires a localized, justified, disclosed region.
- Report set-of-marks **proposal coverage separately** from conditional mark-selection accuracy.
- Keep invalid/unparseable model outputs in the results. No hidden retries, no discarding a wrong answer.
- A null or negative set-of-marks result is a fine outcome. Do not shape the analysis toward an improvement.
- Every number in the README or a resume bullet must trace to a checked-in artifact. Leave a placeholder rather than inventing a metric.
- Classify every reward-hacking surface as **blocked**, **tested**, **mitigated**, or **known limitation**, with evidence, and state residual risks plainly.

## 8. Definition of done for an agent task

Before reporting a task complete:

1. The `Done when:` clause in the relevant day plan is literally satisfied.
2. Tests exist for the failure modes named in that section — not just the happy path.
3. Fast tests pass without OSWorld, network, or a VM.
4. No invariant in §3 was relaxed. If one was in the way, it is raised as a question, not worked around.
5. The report states files changed, tests run, and anything left incomplete.
