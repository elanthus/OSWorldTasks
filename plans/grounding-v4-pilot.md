# Grounding v4 Pilot — Compositional Calibration

**Status:** implementation and capture complete; no model evaluation authorized or run  
**Primary calibration model:** `gpt-5.6-luna`  
**Secondary ceiling check:** `claude-haiku-4-5-20251001`  
**Protocol identifier:** `pixelgym-grounding-v4-pilot`

## 1. Objective

Build a ten-example, capture-only GUI-grounding pilot that replaces geometric clutter as the
primary difficulty lever with visible relational reasoning, cross-panel rule application, and
recovery-state interpretation.

The pilot is successful as a calibration instrument only if a clean, transport-valid Luna run
lands in a useful improvement band:

- raw screenshot accuracy: **5–7 of 10**;
- candidate-independent set-of-marks accuracy: **6–8 of 10**;
- request failures: **0**;
- parse failures: **0**.

These are pilot routing thresholds, not public benchmark claims. A ten-example result has coarse
ten-point resolution and is not a final estimate.

## 2. Why v4 differs from v3

The v3b geometric levers and v3c repeated-row controls did not produce a middle band for the
stronger calibration models. V4 therefore holds the screenshot and action contracts fixed while
changing the capability under test.

The ten examples contain:

| Family | Count | Required capability |
|---|---:|---|
| Relational table selection | 4 | Compare multiple visible row attributes before choosing an action |
| Cross-panel policy application | 3 | Match a request card or apply a visible policy to choose a control |
| Recovery-state interpretation | 3 | Read validation or duplicate-state feedback and choose the repair action |

Every example remains one screenshot plus one requested click. This inexpensive probe determines
whether compositional grounding alone creates useful headroom before the project commits to a
multi-turn environment extension.

## 3. Frozen pilot design

- Canvas: 1024×768 CSS pixels, device scale factor 1.0.
- Seeds: 30 and 31, disjoint from the frozen v1 scored seeds and v3 calibration seeds.
- States: the existing five-state vocabulary (`initial`, `text_field_focused`,
  `validation_error`, `partially_completed`, `completed_review`).
- Allocation: two seeds × five states = ten examples; every seed/state cell appears exactly once.
- Output conditions: raw screenshot and candidate-independent set-of-marks overlay.
- Output contract: integer screenshot-pixel `{x, y}` for both conditions.
- Scoring: the predicted point must fall inside the frozen half-open target box.
- Reward boundary: this capture-only pilot does not alter `PixelGuiEnv`, its action space, or its
  privileged terminal reward.

Candidates are enumerated from every visible actionable control without consulting the requested
target. Target identity is joined only after the complete candidate set is captured.

## 4. Model protocol

### Luna primary

- Model ID: `gpt-5.6-luna`.
- Input: one PNG screenshot and one frozen text prompt.
- Reasoning effort: `low` for the initial calibration.
- Structured output: strict integer `x`/`y` schema.
- Conditions: ten raw and ten marked requests, exactly twenty condition calls.
- No hidden retry. Transport, refusal, and parsing failures remain incorrect records.

The implementation may plan and validate these calls without credentials. Making any paid request
requires explicit human approval. Execution must stop after the twenty-call pilot and requires a
second approval before any expansion or Haiku comparison.

### Haiku ceiling check

Haiku is not part of the first twenty calls. After separate approval, run the identical frozen
inputs and parser. A score above 85% routes the project toward short multi-step episodes; it does
not authorize modifying the frozen pilot after observing individual answers.

## 5. Escalation rules

- **Luna raw 50–70% and marks 60–80%:** freeze the pilot inputs and request approval for the Haiku
  ceiling check.
- **Either condition above 85%:** report saturation and design a separate v4b multi-step pilot.
- **Either condition below 50%:** perform a floor audit first. Check request/parse validity,
  coordinate frame, and semantic-nearest candidate before changing task difficulty.
- **Any request or parse failure:** do not interpret the result as model incapability.
- **Marks proposal coverage below 100%:** reject the capture; do not run a model.

## 6. Evidence and reproducibility

The capture command writes immutable JSONL inputs, raw and marked images, contact sheets, source
hashes, browser metadata, and a repeatability comparison. The second capture must be bitwise equal
to the first before a manifest can be emitted.

No report may call the pilot passed or failed. Agents report commands, exit statuses, counts, and
raw outputs; the human owns the verdict.

## 7. Done when

- [ ] The capture-only v4 application contains all ten target controls and no network, clock,
  animation, transition, or system-font dependency.
- [ ] The two-seed/five-state allocation validates exactly ten examples with a 4/3/3 family split.
- [ ] Candidate proposal generation is target-independent and has 100% target coverage.
- [ ] Two complete local capture passes are bitwise identical.
- [ ] Raw and marked datasets, contact sheets, capture evidence, and the unevaluated manifest are
  generated from the implementation.
- [ ] Unit tests cover wrong counts, wrong allocation, duplicate IDs, target leakage boundaries,
  missing candidates, and call-cap planning.
- [ ] Fast tests pass without network or paid model calls.
- [ ] No model call is made until the human explicitly approves paid evaluation.
