# PixelGym GUI Grounding Protocol v3 — Design (not built, not run)

**Status:** design only. No v3 capture exists, no v3 model output exists, and this document
does not authorize a paid or otherwise externally metered model call.
**Protocol identifiers (reserved):** `pixelgym-grounding-v3a` (primary),
`pixelgym-grounding-v3b` (fallback, entered only by the escalation rule in §6).
**Supersedes:** nothing. v1 and v2 artifacts, including
[`artifacts/grounding-dataset.jsonl`](grounding-dataset.jsonl),
[`artifacts/grounding-v2-dataset.jsonl`](grounding-v2-dataset.jsonl), and
[`artifacts/grounding-results.json`](grounding-results.json), remain frozen and unmodified.

---

## 1. Problem: the benchmark is saturated, and the cause is the parser

The stored v1 result reports set-of-marks accuracy of **100/100** against raw accuracy of
**56/100** ([`grounding-results.json`](grounding-results.json)). v2 reuses the same 100
screenshots and the same rendered marks and changes only the target allocation, so v2 has no
mechanism by which marks accuracy could fall off the ceiling either.

A metric pinned at 1.0 has zero variance, so:

- it cannot rank two candidate policies;
- the `minimum_accuracy` check in [`pixelgym/platform/gates.py`](../pixelgym/platform/gates.py)
  can never block a candidate on quality — only on missing evidence, cost, or latency; and
- the Milestone 4 demo lifecycle ("promotion blocked → revise → thresholds pass") has to lean
  on cost or latency to produce a blocked state, which is not what the accuracy gate is for.

### 1.1 Mechanical cause

This is not a subtle modelling artifact. In `pixelgym/grounding/evaluation.py`, the
marks-condition parser converts a selected `mark_id` into **the centre of that mark's box**,
which `score_point` then tests against the same box. A correct mark ID is therefore
*automatically* inside the target box. The marks condition never measured pointing; it measured
one 10-way multiple choice over uniquely-labelled, well-separated, mostly 464 × 30 px controls —
a solved task for a competent VLM.

The same collapse degrades the diagnostic value of `normalized_center_distance` for the marks
condition: whenever the selection is correct, the parsed point *is* the target box centre, so
the stored distance is exactly `0.0`. Under the v1/v2 parser the marks-condition distance
distribution is a step function — zero for correct selections, large for wrong ones — and a
distance-threshold sweep (§7) can add discrimination only to the **raw** condition.

### 1.2 Design consequence

Raw accuracy of 56% shows the *task* is not saturated for pointing. Only the marks *measurement*
is saturated, because the parser answers the pointing question on the model's behalf. v3
therefore fixes the measurement first (v3a) and hardens the stimulus only if the fixed
measurement still sits at the ceiling (v3b). Making the multiple choice harder without fixing
the parser would leave the benchmark measuring selection — a skill that is not what the
pixel-only environment deploys, since converting a `mark_id` to a click at serving time would
require candidate boxes that exist only at build time (AGENTS.md invariant 14).

---

## 2. Target operating point

v3 aims for **marks-condition accuracy in the 60–85% band** on the scored set of 100 examples —
15 to 40 errors. That band keeps the metric well away from both ceiling and floor, so a
promotion gate placed inside it can move in both directions.

The band is a design goal, not a promise. §6 preregisters the decision rule, and a value
outside the band after the permitted iterations is reported as the finding.

---

## 3. v3a — marks-aided pointing on the frozen v2 dataset

### 3.1 The one change

The marks-condition **response contract** changes from selecting a mark to producing a point:

| | v1 / v2 marks condition | v3a marks condition |
|---|---|---|
| Stimulus | marked screenshot (unchanged overlay pipeline) | **identical** — same frozen marked images |
| Response schema | `{"mark_id": <int>}` | `{"x": <int>, "y": <int>}` — the raw condition's existing schema |
| Parsed point | centre of the selected mark's box | the model's own coordinates, bounds-checked exactly as in raw |
| Scoring | `score_point`, point inside half-open target box | **unchanged** |
| What it measures | 10-way label selection | pointing, aided by marks |

The marks become what set-of-marks overlays are for a pixel-only clicker: a **visual aid for
localization**, not an answer key. The paired question becomes "how much do marks improve a
model's ability to produce a correct click?" — the deployable skill.

### 3.2 What is reused unchanged

- **Dataset and pixels.** The scored set is exactly the 100 examples of
  [`grounding-v2-dataset.jsonl`](grounding-v2-dataset.jsonl) with its crossed
  `target_index = (task_seed + screen_state_index) mod 10` allocation, its frozen raw and marked
  screenshot bytes, and its candidate/overlay records. Nothing is recaptured and no frozen file
  is modified.
- **Pipeline.** `capture.py`, `overlays.py`, `score_point`, invalid-output retention, the
  response cache and deterministic request identity, paired bootstrap, exact McNemar, and
  proposal coverage reported separately from marks accuracy.
- **Invariant 15** is untouched by construction: candidate generation and mark rendering are the
  already-frozen target-agnostic v1 artifacts.

### 3.3 What changes, and how it is versioned

v3a is a **versioned policy change**, not a new capture surface:

- a new marks prompt under `PROMPT_VERSION = "pixelgym-grounding-prompt-v2"` (same target
  sentence and dimension sentence as v1; the marks paragraph asks for integer x/y coordinates of
  the requested control instead of a `mark_id`);
- the marks branch of `parse_prediction` validates the raw-condition point contract; the change
  is landed behind an explicit parser version so a v1/v2 replay stays byte-reproducible;
- `PREDICTION_SCHEMA_VERSION` is bumped so v3a prediction records can never be mixed into a
  v1/v2 analysis — the analyzer already rejects records with a foreign prediction schema
  version;
- the platform policy record carries the new `prompt_version` and `parser_version`
  ([`platform-policy.schema.json`](../pixelgym/platform/schemas/platform-policy.schema.json)
  already requires both), so a v3a policy is a distinct, auditable policy identity. The
  `scorer_version` and the v2 `dataset_fingerprint` are unchanged, so an existing gate policy's
  dataset and scorer pins remain valid.

Because `cache_key` includes `prompt_version` and the response schema, v1 cached responses
cannot collide with v3a requests.

### 3.4 Expected operating point, preregistered as a prediction

Selection on this dataset is at ceiling (100%) and unaided coordinate emission is at 56%.
Marks-aided pointing composes both skills: the badge anchors *which* control, and the model must
still emit *where*. The prediction is that v3a lands **between raw and the selection ceiling,
plausibly inside 60–85%**, because the badge and outline give a visual anchor for the
coordinate. This is a prediction, not a measurement; it exists so the calibration outcome can be
compared against it, including when it is wrong.

Two properties worth naming:

- **Difficulty is native.** The error rate comes from the model's own grounding, not from decoys
  that are never correct. There is no "distractors are never answers" shortcut for a policy to
  learn under repeated gate-driven promotion iterations.
- **The distance diagnostic becomes meaningful for both conditions** (§7): marks-condition
  points are now real model outputs with a continuous distance distribution.

### 3.5 Selection recovered as a free post-hoc diagnostic

Because the frozen candidate boxes are build-time evaluation labels, the analysis can record,
per marks-condition prediction, **which candidate box (if any) contains the predicted point**.
That recovers a selection-equivalent metric ("did the model point at the right control, even if
the click missed the box?") without any contract change, and separates two error modes:

- wrong control chosen (point inside a different candidate box);
- right control, bad coordinates (point inside no candidate box, or near the target box but
  outside it — visible on the distance curve).

This diagnostic never touches the model-facing contract and uses only stored artifacts.

---

## 4. Calibration set: seeds 20–23, captured with the unchanged pipeline

Tuning any decision against the 100 scored examples would fit the benchmark to its own pilot.
v3a therefore captures **4 calibration seeds (20–23) × 5 screen states = 20 calibration
examples**, outside the frozen `TASK_SEEDS = range(20)`:

- captured by the **existing, unchanged** `capture.py` against the **existing, unchanged** task
  app — no new page, no new server, no Sprint 1–2 source file touched, so every existing
  determinism artifact and `capture_source_hashes` entry stays byte-identical;
- the capture-repeatability check (two independent captures, bitwise identical) is rerun for the
  calibration captures before any evaluation;
- targets assigned by the same crossed allocation rule;
- overlays rendered by the unchanged target-agnostic `overlays.py`;
- calibration examples are never part of the scored 100, never enter the headline result, and
  every calibration output is labelled `CALIBRATION`.

---

## 5. Stages — nothing paid runs without approval

**Every stage past Stage 0 is a human gate under AGENTS.md §4. An agent prepares and stops.**

| Stage | Cost | Owner | Content |
|---|---|---|---|
| 0 | free | AGENT | Implement the versioned prompt/parser change with unit tests; capture and verify the calibration set (§4); build the calibration manifest and contact sheets. Human visually reviews the contact sheets. |
| 1 | ≤ 40 calls | **YOU** | Calibration pilot: 20 calibration examples × 2 conditions (raw, marks-aided pointing). Provider, exact model, parameters, prompt versions, price catalog, and a hard call cap are frozen before the first call. |
| 2 | — | **YOU** | Escalation decision per the §6 rule: freeze v3a, or approve entry into v3b (which carries its own build and pilot budget, §8). |
| 3 | 200 calls | **YOU** (second, separate approval) | Scored run: 100 v2 examples × 2 conditions. |

Worst case if v3b is never entered: **240 calls**. v3b, if entered, adds its own budget (§8).

### 5.1 Evidence rules for calibration

- Every pilot is retained and published, including superseded ones, with raw responses, parse
  statuses, and the decision it produced.
- No hidden retries. An invalid or unparseable calibration response is final and scored
  incorrect.
- Each decision is written to the manifest **before** the next build step, so the sequence
  observed → decided → rebuilt is auditable.
- Calibration results must never be quoted as a v3 result.

---

## 6. Preregistered escalation rule (fixed before any pilot output is seen)

Let `m` be calibration-set marks-aided-pointing accuracy.

| Observed | Action |
|---|---|
| **0.60 ≤ m ≤ 0.85** | **Freeze v3a.** Request Stage 3 approval. |
| `m` > 0.85 | v3a is reported as achieved (an honest finding: marks-aided pointing near ceiling on this layout). Request approval to enter **v3b** (§8), which layers stimulus difficulty on top of the v3a contract. |
| `m` < 0.60 | v3a has no difficulty dial to loosen — the layout is frozen. Run the §6.1 floor check; absent a defect, the achieved value is reported honestly. A below-band metric still discriminates (it is noisier and has less headroom above the gate threshold); whether it is acceptable for gating is the human's call. |

v3a itself has **zero dial iterations** — there is nothing to tune except by escalating. The
two-iteration cap applies inside v3b if entered (§8). There is no per-example tuning at any
stage.

### 6.1 Floor and defect checks

If calibration **raw** accuracy falls below 0.10, or marks-aided pointing falls below raw by
more than 10 points, treat the pilot as evidence of a defect (for example a corrupted prompt,
an illegible overlay, or a parsing bug) rather than a difficulty reading: inspect the contact
sheets and raw responses, fix the defect, and re-pilot. A defect fix is not a dial iteration,
and the defective pilot is retained and published like any other.

---

## 7. Analysis extension (implemented in this change)

`pixelgym/grounding/analysis.py` now emits a `distance_threshold_accuracy` section, and the
analysis schema version is bumped to `pixelgym-grounding-results-v3`.

### 7.1 Accuracy as a function of the distance threshold

`distance_threshold_curve(records, thresholds=...)` reports, at each normalized-center-distance
threshold, the fraction of records whose stored distance is **≤** the threshold. Defaults:

```text
0.0, 0.005, 0.01, 0.02, 0.03, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3, 0.5
```

(normalized by the screenshot diagonal, 1280 px at 1024 × 768).

Semantics, stated so the number cannot be misread:

- **It is not the scored benchmark metric.** `correct` remains point-inside-a-half-open-box. A
  near miss stays a miss. The section carries `"is_scored_benchmark_metric": false`.
- **It fails closed.** A record with no measurable distance — an invalid output or a request
  failure — stays in the denominator and is never counted as within-threshold. The curve
  therefore cannot reach 1.0 while any output is unparseable.
- **Equality passes**, matching the gate convention that `observed == threshold` passes.
- Comparison uses the stored full-precision distances; rounding is for display only.
- The distance data already existed on every prediction record and was computed but unused.
  This change only reports it. `gates.py` is unchanged and still does not consume distance;
  wiring a distance-based gate would be a new gate policy version and a human decision (D4.1).

**Scope of validity by parser version.** Under the v1/v2 marks parser the marks-condition curve
is degenerate (§1.1): distance is exactly `0.0` whenever the selection is correct, so the sweep
adds information only for the **raw** condition on stored v1/v2 records. Under the v3a contract
both conditions produce real points, and the curve becomes meaningful for both — including
distinguishing, among saturated policies, one that clicks box centres from one that clips box
edges. Any report over v1/v2 records must not present the marks-condition curve as evidence of
pointing precision.

### 7.2 Per-target-size breakdown

`distance_threshold_report(pairs, ...)` splits the same curves by the frozen target-size bucket
and adds, per bucket:

- `example_count`;
- `target_area_ratio` min / median / max — the continuous quantity that keeps a compressed
  bucket visible rather than silently uninformative;
- per condition: `correct_count`, `record_count`, `strict_accuracy` (agreeing with the existing
  `slices.target_size` values), and the threshold curve.

`per_example` records now also carry `target_area_ratio`. The frozen `small`/`medium`/`large`
thresholds are not moved — that would break comparability with the v1/v2 slices.

### 7.3 The stored v1 result is not regenerated

[`artifacts/grounding-results.json`](grounding-results.json) stays exactly as published, at
`schema_version: pixelgym-grounding-results-v2`. It was produced by the v2 analyzer, and the
report generator reads stored evidence without reinterpreting it. Every number in the README
continues to trace to that unchanged file.

---

## 8. v3b — fallback: harden the stimulus (entered only by the §6 rule)

If v3a calibration lands above 0.85, difficulty must come from the layout. v3b is the
harder-capture design summarized here; it inherits **the v3a point contract** — it never reverts
to `mark_id`, so the parser circularity cannot return.

- **A build-time-only variant page** (`grounding_v3/` static assets plus a capture-only server
  in a new `pixelgym/grounding/v3_server.py`), never mounted by the task app that `PixelGuiEnv`
  or the OSWorld adapter use, and never linked from `/`. `browser_contract.py` is not touched:
  it is hash-pinned by `SOURCE_PATHS` in
  [`pixelgym/validation/browser_boundary.py`](../pixelgym/validation/browser_boundary.py), and
  modifying it would invalidate Sprint 2 browser-boundary evidence. The variant page obeys
  invariant 10 unchanged: no network, no clock, no animation, fixed CSS dimensions, bundled
  fonts, 1024 × 768 at device scale 1.0, and bitwise capture repeatability verified.
- **Difficulty levers**, applied in preregistered tiers with a hard cap of **two dial
  iterations** against the disjoint calibration seeds: candidate density (10 → ~40 marked
  candidates), near-duplicate label families, layout-fixed partial occlusion (a static drawer
  and sticky header occupying fixed viewport rectangles, independent of the target), and
  smaller controls (30 → 18 px height). Badge overlap, label confusability, occluded fractions,
  and target area ratios are measured at build time and reported in the manifest before any
  provider call.
- **Invariant 15 checks, automated:** candidates and marks are asserted to be a pure function of
  `(task_seed, screen_state)` — recomputed under all 10 possible target assignments and required
  byte-identical; mark style uniformity across all marks in a frame; occluder rectangles
  identical across screenshots sharing a screen state; a preregistered minimum unoccluded badge
  area applied uniformly to all badges. Distractor labels come from the seeded generator, never
  from reading `TARGET_SPECS`.
- **Scope and spend:** the variant page is a scope decision (AGENTS.md §4, D1.1) and carries its
  own staged budget: Stage 0 build (free), ≤ 40-call calibration pilot per iteration, and the
  same separate 200-call scored-run approval.
- **Known trade, stated up front:** keeping the 10-target pool means most v3b candidates can
  never be requested, so part of its difficulty rests on rejecting never-correct distractors —
  a shortcut that repeated gate-driven iteration could learn. This is the main reason v3b is the
  fallback rather than the primary design.

---

## 9. Known limitations, stated plainly

1. **v3a conflates selection and coordinate emission in one number.** A marks-condition error
   does not by itself say whether the model chose the wrong control or mis-emitted the
   coordinates of the right one. The §3.5 post-hoc diagnostic and the §7 distance curve
   separate the two modes in analysis, but the headline metric is the composite — deliberately,
   because the composite is the deployable skill.
2. **v3a marks numbers are not comparable with v1/v2 marks numbers.** Same pixels, same
   allocation, different response contract: v1/v2 measured selection, v3a measures aided
   pointing. Reports must never chart them on one axis. Under platform invariant 7 the control
   plane must refuse to compare across these policy identities, and a v3a candidate cannot
   inherit a v1/v2 gate result.
3. **Two replicates per target-by-state cell** still limit interaction estimates, unchanged
   from v2.
4. **One synthetic form, one resolution, one seed range.** Nothing here generalizes to real
   application GUIs, and no such claim will be made. Harder domains (grid- or canvas-shaped
   apps) are a v4 scope question and, per AGENTS.md §4, a human decision.
5. **§3.4 is a prediction, not a measurement**, preregistered so the outcome can be compared
   against it — including when it is wrong.
6. **A null result is acceptable.** If v3a lands at ceiling and v3b is declined, or v3b lands
   out of band after its two iterations, the achieved value is the reported finding. The
   analysis will not be reshaped to manufacture a mid-band number.

---

## 10. Artifacts v3a will produce

| Path | Contents |
|---|---|
| `artifacts/grounding-v3a-calibration-dataset.jsonl` | 20 calibration examples (seeds 20–23) |
| `artifacts/grounding-v3a-calibration-candidates.jsonl` | target-agnostic candidates for the calibration captures |
| `artifacts/grounding-v3a-calibration-overlays.jsonl` | marks, badge boxes, proposal coverage |
| `artifacts/grounding-v3a-capture.json` | browser/version/source hashes, repeatability evidence for the calibration captures |
| `artifacts/grounding-v3a-manifest.json` | prompt/parser versions, allocation, escalation-rule text, decision history |
| `artifacts/grounding-v3a/contact-sheet.png` | raw visual audit sheet (calibration) |
| `artifacts/grounding-v3a/marks-contact-sheet.png` | marked visual audit sheet (calibration) |
| `artifacts/grounding-v3a-pilot-*.json` | every calibration pilot, retained including superseded ones |

The scored set needs no new dataset artifact — it is the frozen v2 dataset. No v3 prediction or
result artifact is written until the Stage 3 approval in §5. v3b artifacts (its own dataset,
manifest, and capture evidence) exist only if v3b is entered.

---

## 11. Open questions for the human gate

1. Approve v3a Stage 0: the versioned prompt/parser change and the calibration capture of seeds
   20–23 with the unchanged pipeline. (No new page, no Sprint 1–2 file changes; scope impact is
   minimal but the approval is yours.)
2. Approve provider, exact model, and the Stage 1 spend cap of 40 calls.
3. Approve or amend the §6 escalation rule **before** any pilot runs; it must be frozen while
   it is still uninformed by outputs.
4. Decide whether a future gate policy version should consume the §7.1 distance curve. It is
   diagnostic only today, and under the v1/v2 parser it is informative only for the raw
   condition.
