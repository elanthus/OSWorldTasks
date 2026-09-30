# PixelGym Grounding Experiment (report v2)

This is `pixelgym-grounding-report-v2`. It re-renders the frozen v1 evidence with prose derived from the stored data and with both bootstrap intervals. It makes no model or evaluation call. The frozen original report, [`artifacts/grounding-report.md`](grounding-report.md), is retained unchanged.

- Protocol: `pixelgym-grounding-v1`
- Prompt: `pixelgym-grounding-prompt-v1`
- Provider/model: `codex-cli` / `gpt-5.4-mini`
- Sample: 100 paired examples (10 target controls x 10 seeds), 200 condition records

## Main result

Raw-coordinate accuracy was 56/100 (56.0%). Set-of-marks accuracy was 100/100 (100.0%). The paired difference (marks minus raw) was +44.0 percentage points.

Two 95% percentile-bootstrap intervals for that difference are reported side by side. They use different units of analysis, and neither is the single result:

| Interval | Unit of analysis | n | 95% CI (pp) |
|---|---|---:|---|
| Example-level bootstrap | example | 100 | [+35.0, +54.0] |
| Target-clustered bootstrap | target control | 10 | [+21.0, +67.0] |

- Target-level exact sign test (unit: target control): 7 targets favour marks, 0 favour raw, 3 tied; two-sided p = 0.0156.
- Clopper-Pearson 95% CI for the proportion of examples with marks correct and raw incorrect (44/100; unit: example, assumes independence): [0.3408, 0.5428].
- Two-sided exact McNemar test (unit: example, assumes independence): p = 1.137e-13 over 44 discordant pairs.

The examples are target controls each repeated across seeds. Raw-condition success is strongly shared within a target, so examples of the same target are not independent. The example-level bootstrap treats every example as independent and therefore overstates precision; the target-clustered bootstrap resamples whole targets and reflects how many independent controls were actually tested. Both intervals are reported, each with its unit of analysis named; neither replaces the frozen v1 report.

This describes the paired difference from adding the frozen marks overlay for this model, task family, prompt, and capture setup. It does not establish a mechanism or generalize to other GUI tasks or models.

## Proposal coverage and the marks condition

Proposal coverage was 100/100 (100.0%). This holds by the retention rule, not as a measured property of the proposer: an example is retained only if the target semantic ID occurs exactly once among the independently collected candidates ([protocol, retention rule](grounding-protocol.md#retention-validation-and-exclusions)). The marks condition therefore measures selection among 10 labelled candidates on every example. Conditional selection accuracy was 100/100 (100.0%).

## Data integrity and exclusions

Invalid output counts were raw=0 and marks=0; request-failure counts were raw=0 and marks=0. Invalid outputs and request failures stay in the denominator and score as incorrect. 0 examples were excluded (n=100 of 100 retained), according to `collection.excluded_example_count` and the `per_example` rows in the stored results.

## Error review

The error-review artifact holds 44 error records (by condition: 44 `raw`; by `review_status`: 44 `manual_visual_review`). Recorded review method: "Manual visual inspection of all 44 annotated raw/marks error pairs across four contact sheets, with point and target-box geometry cross-checked against stored records." The review artifacts do not record a reviewer identity. The decisions file assigns records to 5 categories; categories are non-exclusive, so their counts can sum above the number of error records.

| Category | Error records |
|---|---:|
| coordinate scaling error | 7 |
| correct region but point just outside the box | 6 |
| crowded or overlapping controls | 14 |
| small target | 14 |
| wrong semantic element | 31 |

Category meanings as recorded in the decisions file:

- coordinate scaling error: Inference: horizontal alignment with the radio group plus substantial horizontal displacement is consistent with a coordinate-scaling error; the stored evidence does not establish the causal mechanism.
- correct region but point just outside the box: Observation: the point is visually aligned with the intended control but falls outside its frozen half-open target box.
- crowded or overlapping controls: Observation: the target is one of three closely adjacent radio controls; no controls geometrically overlap.
- small target: Observation: the frozen target-area rule assigns these radio controls to the small slice.
- wrong semantic element: Observation: the point lands on the matching read-only request-card row, another actionable control, or the vicinity of a different semantic field rather than the requested editable control.

## Harness context

The calls ran through the `codex-cli` harness. Mean input tokens per call (`usage.input_tokens`) were 11909.31 for raw (n=100) and 11915.26 for marks (n=100), from the clustered supplement. The harness prompt is not part of the recorded `pixelgym-grounding-prompt-v1`, and it is not recorded whether the harness prompt was identical across conditions.

## Limitations

- One synthetic vendor-onboarding task family, one resolution, and one model were used.
- Target identity is aliased with screen state in the frozen dataset, so per-control descriptive rates do not identify a control-type effect.
- `gpt-5.4-mini` is a moving provider alias rather than an immutable snapshot.
- The raw-coordinate gap is specific to `gpt-5.4-mini` with `pixelgym-grounding-prompt-v1`. With a revised prompt, `Claude Haiku 4.5` scored 100/100 raw on the same examples ([historical v3 report](grounding-v3-haiku-gemini-report.md), cited, not an input), so the marks-over-raw difference is specific to this model and prompt.
- Both bootstrap intervals describe uncertainty over this frozen example set, not over other applications or model versions.

## Method

Copied from the `method` block of the clustered supplement JSON:

- bootstrap_samples: 10000
- clopper_pearson: exact binomial interval for the proportion of examples with marks correct and raw incorrect
- cluster_level_draw_order: fresh random.Random(seed); per resample, k sequential rng.randrange(k) draws over targets in first-appearance order; pooled mean of drawn examples
- confidence_level: 0.95
- example_level_draw_order: per resample, n sequential rng.randrange(n) draws over per_example order; same as pixelgym.grounding.analysis.paired_bootstrap_interval
- percentile_convention: linear interpolation at position (n-1)*q, identical to pixelgym.grounding.analysis._percentile used for the frozen v1 interval
- rng: Python random.Random(20260809); rng.randrange draws
- sign_test: exact two-sided binomial, p=0.5, over targets; targets with zero net difference excluded

## Inputs and reproduction

Generated offline from:

- `artifacts/grounding-results.json`
- `artifacts/grounding-predictions.jsonl`
- `artifacts/grounding-clustered-analysis-v1.json`
- `artifacts/grounding-error-review.jsonl`
- `artifacts/grounding-error-review-decisions.json`
- `artifacts/grounding-overlays.jsonl`

No model or network call is made by these commands:

```bash
.venv/bin/python scripts/generate_grounding_report_v2.py
.venv/bin/python scripts/verify_grounding_report_v2.py
```

Input digests are recorded in [`artifacts/grounding-report-provenance-v2.json`](grounding-report-provenance-v2.json).
