# Set-of-Marks Effect Across PixelGym Grounding Runs

- Scope: every stored paired raw/marks evaluation in this repository as of 2026-08-23
- Method: derived entirely from stored results and prediction artifacts; no new model calls,
  no rescoring, no exclusions beyond those already recorded in each source artifact
- Status of claims: the two 100-example runs under `pixelgym-grounding-v1` are scored
  experiment results. The v3c, v4, and v4b numbers are calibration and pilot instruments with
  routing thresholds fixed in advance; they are **not** public benchmark claims.

## Question

Does a candidate-independent set-of-marks overlay (every visible actionable control outlined
and numbered, generated without consulting the requested target) improve click accuracy over
the raw screenshot — and for which models?

## Results by run

| Run (protocol) | Model | n per condition | Raw | Marks | Paired delta | Discordant pairs | Source artifact |
|---|---|---:|---:|---:|---|---|---|
| v1 scored, prompt v1, 2026-08-10 | `gpt-5.4-mini` (low) | 100 | 56 | 100 | **+44.0pp**, 95% CI [+35, +54], McNemar p ≈ 1.1×10⁻¹³ | 44, all marks-only | `grounding-results.json` |
| v1 scored, prompt v2, 2026-08-21 | Claude Haiku 4.5 | 100 | 100 | 100 | 0.0pp, p = 1.0 | 0 | `grounding-v3a-analysis-claude-haiku-4.5.json` |
| v1 scored, prompt v2, 2026-08-21 | Gemini 3.7 Flash (adapted) | 100 | 91 | 100 | +9.0pp, p = 0.0039 — **transport confound, see below** | 9, all marks-only | `grounding-v3a-analysis-gemini-3.7-flash-adapted.json` |
| v3c calibration | Claude Haiku 4.5 | 20 | 19 | 20 | 1 discordant pair | 1 | `grounding-v3c-calibration-predictions-claude-haiku-4.5.jsonl` |
| v3c calibration | Gemini 3.7 Flash (adapted) | 20 | 20 | 20 | 0 | 0 | `grounding-v3c-calibration-predictions-gemini-3.7-flash-adapted.jsonl` |
| v3c calibration | gemma-3-4b / gemma-3-27b / qwen-2.5-vl-7b | 20 | 0 | 0 | none measurable at floor | 0 | per-model `grounding-v3c-calibration-predictions-*.jsonl` |
| v3c calibration | llama-4-scout | 20 | 1 | 0 | 1 raw-only pair | 1 | `grounding-v3c-calibration-predictions-llama-4-scout.jsonl` |
| v4 pilot (10 single-click examples) | `gpt-5.6-luna` (low) | 10 | 9 | 9 | 0 discordant; 1 example incorrect in both | 0 | `grounding-v4-pilot-results-luna.json` |
| v4b pilot (10 closed-loop episodes) | `gpt-5.6-luna` (low) | 10 | 9 | 10 | 1 discordant episode | 1 | `grounding-v4b-pilot-results-luna.json` |

All denominators keep request failures and invalid outputs as incorrect; no run in this table
excluded an example. Small-n rows (10–20) have coarse resolution and support no delta estimate.

**Flash confound (disclosed in `grounding-report.md`):** all 9 Flash raw failures are HTTP
request failures clustered in a single ~10-second burst during the sequential raw pass,
consistent with a transient provider outage; the marks pass ran outside that window. On
completed requests Flash scored 91/91 raw. The +9.0pp delta measures API availability, not a
grounding effect, and is excluded from the interpretation below.

## Proposal coverage, reported separately from selection

| Run | Proposal coverage | Conditional selection (marks) |
|---|---|---|
| v1 scored (both dates) | 100/100 targets proposed | 100/100 when proposed |
| v4 pilot | 10/10 | 9/10 |
| v4b pilot | 60/60 reachable actionable states | not separable from episode success at this n |

Coverage never absorbed a selection failure: in every run the candidate generator proposed the
target, so marks accuracy reflects selection, not proposal luck.

## Interpretation

The marks effect on this workload is **capability-band-dependent**, not uniformly null and not
uniformly positive:

1. **Mid-capability band: large, decisive effect.** For `gpt-5.4-mini`, marks rescued all 44
   raw failures (+44pp, p ≈ 10⁻¹³) — the one statistically strong marks result in the
   repository. The manual review of those 44 raw errors
   (`grounding-error-review-decisions.json`; categories overlap, so counts exceed 44) assigned:
   wrong semantic element 31, small target 14, crowded/overlapping controls 14, coordinate
   scaling 7, correct region but point just outside the box 6. These are exactly the error
   classes that numbered candidate boxes bypass: the model no longer has to produce precise
   coordinates, only pick a labeled region.
2. **Frontier band: no measurable headroom.** Haiku 4.5 is at ceiling in both conditions on
   the scored v1 dataset (delta 0.0pp on 100 paired examples). Luna shows no discordant pair
   in v4 and one discordant episode in v4b. That v4b episode's raw failure is a visible-policy
   application error with correct localization throughout (see
   `grounding-v4b-error-review.md`); marks do not target that failure class, and n = 1 supports
   no attribution.
3. **Floor band: no rescue.** Models at 0–1/20 on the v3c calibration (gemma-3-4b, gemma-3-27b,
   qwen-2.5-vl-7b, llama-4-scout) score 0–1/20 with marks as well. When a model cannot follow
   the coordinate output contract at all, converting localization into selection does not help.

A one-sentence summary consistent with all stored evidence: **on this workload, set-of-marks
overlays convert coordinate-production failures into candidate-selection successes for a model
in the middle capability band, and measurably change nothing at the frontier ceiling or the
open-model floor.**

## Limitations

- Single task family (deterministic vendor-onboarding form and its v4/v4b derivatives) at one
  resolution (1024×768). No cross-application generality is claimed.
- One model per band outside the frontier: the mid-band effect rests on `gpt-5.4-mini` alone.
- The 2026-08-10 mini run used prompt v1; the 2026-08-21 rescores used prompt v2. Each paired
  delta is within-run and unaffected, but cross-model accuracy comparisons between those runs
  carry a prompt-version difference.
- v3c, v4, and v4b use 10–20 examples per condition by design; they route decisions and cannot
  estimate effect sizes.
- Luna results are at reasoning effort `low` only.

## What would strengthen this

(Proposals; each paid step requires its own approval.)

- Evaluate one or two mid-size open-weight VLMs (for example Qwen2.5-VL at 32B/72B) on the
  frozen v1 and v3c inputs to test whether the mid-band marks effect replicates in a model
  that can be run locally at zero marginal cost.
- If a longer-horizon successor pilot is designed, keep the paired-condition structure so the
  band-dependence claim can be tested where frontier failures are semantic rather than
  geometric.
