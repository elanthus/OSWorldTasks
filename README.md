# PixelGym-OSWorld

PixelGym-OSWorld combines a pixel-only Gymnasium environment with a GUI-grounding benchmark for
one deterministic synthetic vendor-onboarding form and an optional backend pinned to OSWorld-V2.
It is built to make environment contracts, reward timing, reset behavior, and grounding evidence
inspectable—not to claim broad desktop-agent performance.

## Problem

GUI-agent evaluations can look successful while leaking privileged state, accepting invalid
actions, paying reward at the wrong time, or hiding failures in aggregate metrics. Broad desktop
benchmarks also make those boundaries expensive to isolate. This project narrows the workload to
one fixed form so each behavior can be specified, tested, and tied to stored evidence.

The main questions are:

- Can an agent interact through screenshots and a small, validated action vocabulary while a
  separate host-side evaluator controls reward?
- Does the same seed reproduce the same task and application state?
- On this fixed layout, how does raw coordinate prediction compare with a deterministic
  set-of-marks condition when proposal provenance and scoring are disclosed?

## Architecture

```mermaid
flowchart LR
    A["Agent"] -->|"NOOP / bounded CLICK / allowlisted KEY"| E["PixelGuiEnv"]
    E --> B["Backend protocol"]
    B --> F["Deterministic fake backend"]
    B --> O["Pinned OSWorld-V2 adapter"]
    F --> S["RGB screenshot"]
    O --> S
    S --> A
    F --> V["Privileged host-side evaluator"]
    O --> V
    V -->|"0 until exact submission; then 1 once"| E
    D["Offline browser capture\nDOM-derived control boxes"] --> M["Target-agnostic marks"]
    M --> G["Frozen paired grounding experiment"]
```

At runtime, the environment exposes only an RGB screenshot. The agent can issue `NOOP`, bounded
integer `CLICK(x, y)`, or `KEY(index)` actions from a versioned allowlist. Expected answers,
bounding boxes, DOM data, and evaluator diagnostics do not enter the observation or `info`.
Invalid actions are rejected before backend execution, and the privileged evaluator is the only
source of success ([environment](pixelgym/env.py), [actions](pixelgym/actions.py),
[evaluator](pixelgym/evaluator.py)).

The grounding dataset has a separate, offline build path. Browser instrumentation records control
geometry while constructing frozen evaluation assets; that instrumentation is not reachable from
the environment adapter ([capture](pixelgym/grounding/capture.py),
[protocol](artifacts/grounding-protocol.md)).

## Completed result

### Pixel-only environment and validation

The implemented environment supports the fake backend for fast contract tests and the pinned
OSWorld-V2 adapter for real episodes. For seed 7, **five real OSWorld resets were semantically and
bitwise identical at 1024×768 on one local Apple Silicon Docker/QEMU host**
([reset evidence](artifacts/day-2-rev-2026-09-06-issues-95-101/raw/real-reset.json),
[revision environment](artifacts/day-2-rev-2026-09-06-issues-95-101/validation-report.json)). The
reward-hacking audit records a disposition and evidence for **14 tested surfaces**
([audit](artifacts/day-2-rev-2026-09-06-issues-95-101/raw/reward-hacking.json)). The historical
1920×1080 run established semantic task-state determinism and perceptual visual stability, not
bitwise equality ([historical reset evidence](artifacts/day-2/raw/real-reset.json)).

The environment contract is sparse and explicit: reward remains `0.0` until an exact valid
submission, becomes `1.0` exactly once, and terminates the episode; a step-limit ends by
truncation, and stepping after either ending raises an error
([validation report](artifacts/validation-report.json)).

![Real OSWorld episode](artifacts/day-3/review/real-osworld-episode.gif)

### Frozen grounding experiment

On **100 paired targets** from the same 1024×768 synthetic form, the Codex CLI provider using the
moving `gpt-5.4-mini` alias scored **56/100 with raw coordinates and 100/100 with marks**. The
paired difference was **+44.0 percentage points**, with a fixed-seed percentile-bootstrap **95% CI
of [+35.0, +54.0]** ([canonical report](artifacts/grounding-report.md),
[structured results](artifacts/grounding-results.json),
[provenance](artifacts/grounding-report-provenance-v1.json)). Proposal coverage was **100/100** and
conditional mark-selection accuracy was **100/100**; the two quantities are reported separately
in the same evidence.

The marks condition is not an end-to-end pixel-only proposal system. During offline dataset
construction, Playwright evaluates `getBoundingClientRect()` for every actionable control and
stores those DOM-derived locations before the requested target is joined. The overlay generator
marks all candidates without consulting the target. The model sees the marked screenshot and
instruction, returns a `mark_id`, and the evaluation adapter supplies the center coordinates of
that candidate's stored box ([capture implementation](pixelgym/grounding/capture.py),
[adapter implementation](pixelgym/grounding/evaluation.py),
[leakage controls](artifacts/grounding-protocol.md#set-of-marks-leakage-controls)). This offline
proposal provenance is distinct from the screenshot-only environment interface described above.

![Raw-coordinate versus set-of-marks accuracy](artifacts/grounding/figures/raw-vs-marks-accuracy.png)

### Supporting platform work and status

A local-first evaluation and policy-delivery layer rehearses resumable execution, evidence
retention, mechanical promotion gates, explicit approval, exact-version serving, and rollback on
the frozen workload. Its measurements use a deterministic `scripted-demo` provider and are
synthetic orchestration fixtures—not model-quality, production-throughput, or deployment evidence
([platform architecture](artifacts/platform/architecture.md),
[platform evidence](artifacts/platform/seed-policy-fanout-evidence-v1.json)).

The owner-recorded [D1.8](artifacts/day-1/human-gate.json),
[D2.11](artifacts/day-2-rev-2026-09-06-issues-95-101/raw/human-gate.json),
[D3.11](artifacts/day-3/raw/human-gate.json), and
[D4.12](artifacts/platform/human-gate.json) decisions remain in the audit trail. D4.12 applies only
to its reviewed revision and scripted-provider scope. The v5 confirmatory benchmark and v5
milestone gate remain incomplete; retained calibration, negative results, and unfinished work are
documented without being promoted to a headline result
([v5 plan](plans/grounding-v5-agent-benchmark.md), [evidence index](docs/evidence-index.md)).

## Reproduction

Python 3.12 is required. The default development install excludes OSWorld. From the repository
root:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/ruff check .
.venv/bin/mypy pixelgym
.venv/bin/pytest -q -n auto tests/unit
.venv/bin/python scripts/golden_trajectory.py check
.venv/bin/python scripts/verify_grounding_report.py
```

The unit suite is offline: it does not bind sockets or require a browser, Docker, OSWorld, provider
credentials, or external services. The grounding verifier reads frozen evidence, recomputes the
headline, verifies stored hashes, and leaves tracked files unchanged. Numerical recomputation is
supported; cross-platform byte-for-byte PNG regeneration is not claimed
([verification guide](docs/grounding-verification.md)).

The real-loopback HTTP contract tests are a separate local integration group. Optional OSWorld,
browser capture, local Metaflow/MLflow runtime, and Docker/Playwright lifecycle commands are also
documented separately with their prerequisites ([reproduction guide](docs/reproduction.md),
[deployment guide](deploy/README.md#test-suite-boundaries)). Pull-request CI runs the offline unit
suite and the loopback HTTP group as separate jobs. The manually dispatched platform workflow runs
only the Metaflow/MLflow runtime tests; it does **not** run the Docker/Playwright lifecycle suite
([CI](.github/workflows/ci.yml), [manual workflow](.github/workflows/platform-integration.yml)).

## Limitations

- The environment and grounding result cover one deterministic synthetic form at one 1024×768
  layout. They do not establish broad GUI or cross-application generalization
  ([grounding report](artifacts/grounding-report.md#limitations)).
- `gpt-5.4-mini` was a moving provider alias, not an immutable model snapshot. The result is bound
  to the recorded provider, prompt, data, and collection window
  ([provenance](artifacts/grounding-report-provenance-v1.json)).
- The frozen v1 allocation perfectly aliases target identity with screen state. Control-type
  slices are descriptive; the crossed v2 allocation has not been run against a model
  ([v2 protocol](artifacts/grounding-v2-protocol.md),
  [v2 manifest](artifacts/grounding-v2-manifest.json)).
- Marks rely on target-agnostic but DOM-derived offline proposals and fixed-layout center mapping.
  The result measures selection given that proposal mechanism, not proposal generation from pixels
  or end-to-end task completion ([protocol](artifacts/grounding-protocol.md)).
- Bitwise visual repeatability was observed on one host. Cross-host portability is not established;
  the historical run supports only semantic determinism and perceptual visual stability
  ([visual differences](artifacts/day-2-rev-2026-09-06-issues-95-101/raw/renderer-screenshot-differences.json)).
- The privileged state endpoint exists inside the guest. App-mode navigation containment was
  tested, while browser and guest-OS exploits remain outside the threat model. Digest pinning does
  not remove third-party publisher risk ([reward-hacking audit](artifacts/day-2-rev-2026-09-06-issues-95-101/raw/reward-hacking.json)).
- Platform metrics are synthetic, local scripted-provider measurements. V5 calibration is
  descriptive, the confirmatory benchmark is unfinished, and no v5 gate is declared
  ([evidence index](docs/evidence-index.md)).

## Related work and demonstrated contribution

[OSWorld](https://proceedings.neurips.cc/paper_files/paper/2024/hash/5d413e48f84dc61244b6be550f1cd8f5-Abstract-Datasets_and_Benchmarks_Track.html)
introduced a real-computer environment with task setup and execution-based evaluation across
desktop operating systems. This project uses the pinned OSWorld-V2 release as an optional backend;
it does not claim to originate OSWorld or reproduce the breadth of its benchmark. Its demonstrated
environment contribution is a narrow Gymnasium-compatible, screenshot-only interface, a custom
deterministic task, a privileged evaluator boundary, and stored validation evidence for the
contracts above.

[Set-of-Mark Prompting](https://arxiv.org/abs/2310.11441) introduced the visual-prompting pattern of
overlaying regions with referable marks. This project adapts that pattern to a fixed synthetic GUI
using offline DOM-derived control boxes instead of the paper's segmentation-based proposal path.
Its demonstrated research-engineering contribution is the frozen paired comparison, explicit
target-agnostic proposal controls, separate proposal/selection metrics, retained failures, and a
non-mutating evidence verifier. It is not a new foundation model, a general GUI-proposal method, or
a broad agent benchmark.

Repository work used a bounded agent-assisted branch-and-PR process: agents could implement,
test, and prepare evidence, while deterministic checks, independent review, and owner-held scope,
spend, scientific, and publication gates remained separate. The
[workflow ADR](plans/adr-agent-assisted-workflow.md) records both useful findings and defects that
escaped automation; it is a process audit, not an authorship claim or a substitute for human
review.

## Evidence and project history

- [Evidence index](docs/evidence-index.md) — canonical claims, historical records, withdrawals,
  and public-verification limits.
- [Reproduction guide](docs/reproduction.md) — offline, loopback, browser, and OSWorld commands.
- [Sprint 1 environment plan](plans/sprint-1-environment-core.md),
  [Sprint 2 integration plan](plans/sprint-2-osworld-integration-and-validation.md), and
  [Sprint 3 grounding plan](plans/sprint-3-grounding-and-portfolio.md) — completed core sequence.
- [Platform milestone plan](plans/grounding-evaluation-platform.md) and
  [v5 benchmark plan](plans/grounding-v5-agent-benchmark.md) — supporting platform detail and
  unfinished work.
- [Qualified resume-bullet review candidate](artifacts/resume-bullets-v2-review.md) — proposed
  wording with the current scope qualifications. The digest-bound historical approved artifact is
  retained unchanged at [artifacts/resume-bullets.md](artifacts/resume-bullets.md).
- [Public release checklist](plans/public-release-checklist.md) — unticked human release gate; its
  inventory refresh remains separate from this change.

## Contributing

Read [AGENTS.md](AGENTS.md) before changing code or evidence. Preserve the screenshot-only,
host-evaluated reward, determinism, and build-time-instrumentation boundaries; keep OSWorld optional
for the fast path; version corrections to frozen evidence instead of silently rewriting history;
and report only checks actually run. Open review-ready pull requests by default. Human-owned gates
remain human decisions.

## License

Copyright 2026 Michael Swailes. Licensed under the Apache License, Version 2.0. See
[`LICENSE`](LICENSE) and [`NOTICE`](NOTICE). Bundled DejaVu fonts retain their separate notices and
license terms listed in `NOTICE`.
