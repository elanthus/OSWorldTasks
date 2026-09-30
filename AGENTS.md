# AGENTS.md — PixelGym-OSWorld

Operating instructions for any coding agent working in this repository. Read this before touching code.

## 1. What this project is

PixelGym-OSWorld is an implemented **pixel-only GUI reinforcement-learning environment** and
**GUI-grounding benchmark** built on OSWorld-V2. Sprints 1–3 deliver the environment, validation
evidence, and frozen grounding experiment. Milestone 4 adds a local-first grounding-evaluation and
policy-delivery platform around that frozen workload; its human milestone gate remains separate.

The deliverable is not "a working demo" — it is a working environment plus **evidence** that the
environment is correct: reset determinism, reward timing, space integrity, and a reward-hacking
audit.

The single core task is one deterministic synthetic **vendor-onboarding form**.

Design records and reference:

- [README.md](README.md) — project claim and current status
- [docs/environment-contract.md](docs/environment-contract.md) — the fifteen environment invariants and where they are enforced
- [docs/platform-design.md](docs/platform-design.md) — Milestone 4 evaluation, governance, serving, and rollback platform, and v5 stateful serving
- [docs/v5-benchmark-design.md](docs/v5-benchmark-design.md) — v5 agent benchmark contract, analysis plan, and status
- [docs/development-process.md](docs/development-process.md) — branch, review, and human-gate process
- [docs/glossary.md](docs/glossary.md) — D-numbers, grounding versions, and codenames

If this file and a design record disagree, the design record wins for task detail; this file wins for process and invariants.

## 2. Current repository state

The core environment, OSWorld adapter, validation suite, frozen grounding experiment, and
local-first platform components are implemented. The project owner recorded D4.12 `PASS` on 2026-09-09 against revision
`4c4a7fb2fcb983e81084542365abc0ec2d7df538` in `artifacts/platform/human-gate.json`.
That decision covers the reviewed scripted-provider governance and orchestration evidence; it is
not a model-quality or production-readiness claim and does not grade later changes. The v5 agent
benchmark and v5 serving work remain unfinished; agents must not infer a new milestone verdict.

Current repository layout includes the following core paths:

```text
pixelgym/
├── env.py            # PixelGuiEnv — Gymnasium contract only, no OSWorld imports
├── actions.py        # NOOP / CLICK / KEY, versioned key allowlist
├── task_spec.py      # typed task + submission contracts
├── evaluator.py      # privileged, host-side, structured EvaluationResult
├── backends/         # base.py protocol, fake.py, osworld.py
├── tasks/vendor_form/app/   # deterministic HTML/CSS/JS + FastAPI service
├── grounding/               # frozen dataset, provider, scoring, and report code
└── platform/                # evaluation, immutable evidence, gates, control plane, and serving API
flows/                       # Metaflow evaluation flow
tests/{unit,integration}/
artifacts/            # validation, grounding, and platform evidence
deploy/               # local Compose stack and platform runtime images
scripts/
```

The superseded v3/v4 calibration apps and v5 D5.6 experiment drivers formerly under
`legacy/grounding/` were removed from the tree (issue #170); the frozen evidence they
produced is verified by git-revision provenance sidecars and by fixtures under
`tests/unit/fixtures/`, and the code itself remains reproducible at git tag
`legacy-grounding-final` (`git worktree add <path> legacy-grounding-final`).

Python 3.12. Dependencies are declared in `pyproject.toml`; add one only when a task needs it. **OSWorld is an optional extra** — the fast unit path must install and run without it.

## 3. Non-negotiable invariants

The fifteen invariants in [docs/environment-contract.md](docs/environment-contract.md) define the project's claim. Do not weaken one to make a task easier; stop and raise it instead.

## 4. Human gates — stop and ask

Some decisions belong to a person, not an agent. An agent prepares work up to these points and then stops:

- **Scope changes.** If a feature does not improve the Gym contract, evaluator correctness, validation evidence, or the grounding experiment, defer it. Do not add it and ask later.
- **Milestone gates:** the Sprint 1, Sprint 2, and Sprint 3 acceptance gates (D1.8, D2.11, D3.11), the Milestone 4 platform gate (D4.12), and the v5 benchmark verdict (D5.10). Agents run the documented checks and report **raw results only**: command, exit status, counts, full output. Do not summarize a gate as passing, do not offer a provisional PASS/FAIL, and do not tick checklist boxes. A person reads the raw evidence and declares the verdict.
- **Provider choice and cloud spend,** including the 90-minute infrastructure stop-loss.
- **Any paid model call.** Each provider, model, policy package, phase, and call cap needs explicit approval. Run a pilot only after approval, stop at its cap, and do not continue to a full run without a second approval.
- **Public claims:** README wording, demo media, and any published summary of results.

## 5. Working agreements

- **One writing agent at a time** unless two tasks touch completely disjoint paths.
- **Preserve unrelated work.** Do not reformat, refactor, or "tidy" files outside the task.
- **Report the tests you actually ran**, with counts and runtime. Never describe a check you did not execute.
- Match reasoning effort to the task: medium for bounded implementation with an explicit test; high for environment semantics, evaluator boundaries, integration, determinism interpretation, and statistics.
- Prefer explicit fixtures over mocks that restate implementation details.
- Fast tests must not touch network, browser, OSWorld, or wall-clock sleeps.
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

1. The `Done when:` clause of the task (issue or work package) is literally satisfied.
2. Tests exist for the failure modes named in that section — not just the happy path.
3. Fast tests pass without OSWorld, network, or a VM.
4. No invariant in §3 was relaxed. If one was in the way, it is raised as a question, not worked around.
5. The report states files changed, tests run, and anything left incomplete.
