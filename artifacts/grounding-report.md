# PixelGym Grounding Experiment

- Protocol: `pixelgym-grounding-v1`
- Prompt: `pixelgym-grounding-prompt-v2`
- Parser: `pixelgym-grounding-parser-v2`
- Scored models: Claude Haiku 4.5 (`claude-code-cli`), Gemini 3.7 Flash (`openrouter` + `GeminiCoordinateAdapter`)
- Collection window: 2026-08-21T05:07:20 to 2026-08-21T08:41:00 UTC
- Sample: 100 examples, 200 condition records per model, 0 exclusions

## Main result

**Claude Haiku 4.5:** Raw accuracy 100/100 (100.0%), marks accuracy 100/100 (100.0%). Paired delta 0.0pp, McNemar p = 1.0.

**Gemini 3.7 Flash (adapted):** Raw accuracy 91/100 (91.0%), marks accuracy 100/100 (100.0%). Paired delta +9.0pp, 95% CI [+4.0, +15.0]pp, McNemar p = 0.0039.

**Confound disclosure:** All 9 Flash raw failures are HTTP request errors (`request_failure`), not model output errors. They are temporally clustered in a single 10-second burst during the sequential raw pass, consistent with a transient OpenRouter outage. The marks pass ran separately and avoided the outage window. On successfully completed requests, Flash achieved 91/91 = 100% raw accuracy. The +9pp delta is an API-availability artifact, not a grounding-capability difference.

Both frontier VLMs ground at ceiling on this single-page form task when the provider is reliable.

## Proposal coverage and conditional selection

| Model | Proposal coverage | Conditional selection accuracy |
|---|---|---|
| Haiku 4.5 | 100/100 (100%) | 100/100 (100%) |
| Flash (adapted) | 100/100 (100%) | 100/100 (100%) |

The candidate generator proposed all 10 target elements in every screenshot. When proposed, both models selected the correct element 100% of the time under the marks condition. Coverage and selection are reported separately; selection accuracy does not absorb proposal failures.

## Data integrity and output failures

| Model | Raw invalid | Marks invalid | Raw request failures | Marks request failures |
|---|---|---|---|---|
| Haiku 4.5 | 0/100 | 0/100 | 0/100 | 0/100 |
| Flash (adapted) | 0/100 | 0/100 | 9/100 | 0/100 |

Invalid outputs and request failures remain in the denominator and score as incorrect. No examples were excluded. The 9 Flash request failures are the sole source of the raw/marks accuracy gap.

## Concordance tables

**Haiku 4.5:**

|  | Marks correct | Marks incorrect |
|---|---|---|
| Raw correct | 100 | 0 |
| Raw incorrect | 0 | 0 |

**Flash (adapted):**

|  | Marks correct | Marks incorrect |
|---|---|---|
| Raw correct | 91 | 0 |
| Raw incorrect | 9 | 0 |

No example was raw-correct/marks-incorrect for either model. No example was incorrect in both conditions for either model.

## Slices by element type

### Haiku 4.5

| Element type | n | Raw | Marks | Delta (pp) |
|---|---:|---:|---:|---:|
| text_input | 40 | 100% | 100% | 0.0 |
| radio | 30 | 100% | 100% | 0.0 |
| select | 10 | 100% | 100% | 0.0 |
| checkbox | 10 | 100% | 100% | 0.0 |
| button | 10 | 100% | 100% | 0.0 |

### Flash (adapted)

| Element type | n | Raw | Marks | Delta (pp) |
|---|---:|---:|---:|---:|
| text_input | 40 | 92.5% | 100% | +7.5 |
| radio | 30 | 90.0% | 100% | +10.0 |
| select | 10 | 90.0% | 100% | +10.0 |
| checkbox | 10 | 90.0% | 100% | +10.0 |
| button | 10 | 90.0% | 100% | +10.0 |

The Flash raw failure rate is approximately uniform across element types, consistent with the request-failure mechanism (API errors are independent of target type).

## Normalized center distance

For successfully parsed predictions, screenshot-diagonal-normalized center distance:

| Model | Condition | Mean | Median | P90 |
|---|---|---|---|---|
| Haiku 4.5 | raw | 0.0119 | 0.0078 | 0.0409 |
| Haiku 4.5 | marks | 0.0117 | 0.0078 | 0.0403 |
| Flash | raw | 0.0125 | 0.0008 | 0.0622 |
| Flash | marks | 0.0004 | 0.0006 | 0.0009 |

Flash marks achieves sub-pixel precision (mean 0.0004 normalized ≈ 0.5px) because the coordinate adapter rescales from a coarser grid and the marks overlay constrains the prediction space. Despite saturated accuracy, center distance provides residual discrimination between conditions.

## Error taxonomy

| Category | Haiku 4.5 | Flash (adapted) |
|---|---|---|
| invalid response format | 0 | 9 |
| All other categories | 0 | 0 |

All 9 Flash errors are `request_failure` (HTTP error before model output). No errors involve wrong semantic elements, near-miss coordinates, scaling errors, small targets, or ambiguous instructions. Review status: pending_visual_review (auto-classified; no model output exists to visually inspect).

## Calibration progression

Before the scored evaluation, three page variants of increasing difficulty were calibrated on seeds 20–23 (4 seeds × 5 states = 20 examples each):

| Variant | Page | Candidates | Key difficulty lever |
|---|---|---|---|
| v3a | Vendor form | 10 | Baseline |
| v3b | Dense form | 38 | Near-duplicate labels, 18px controls, header/sidebar occlusion |
| v3c | Data table | 48 | 8 identical Edit/Delete buttons requiring row-context disambiguation |

### Frontier model calibration results

| Variant | Haiku raw | Haiku marks | Flash raw | Flash marks |
|---|---|---|---|---|
| v3a | 20/20 (100%) | 20/20 (100%) | 20/20 (100%) | 20/20 (100%) |
| v3b | 20/20 (100%) | 20/20 (100%) | 20/20 (100%) | 20/20 (100%) |
| v3c | 19/20 (95%) | 20/20 (100%) | 20/20 (100%) | 20/20 (100%) |

### Weaker model calibration results

| Model | Parameters | v3c raw | v3c marks | v3a raw | v3a marks |
|---|---|---|---|---|---|
| Llama 4 Scout | 17B active (MoE) | 1/20 (5%) | 0/20 (0%) | — | — |
| Gemma 3 27B | 27B | 0/20 (0%) | 0/20 (0%) | 3/20 (15%) | 2/20 (10%) |

The calibration shows a binary capability cliff: frontier VLMs saturate at 95–100% across all variants, while mid-tier models floor at 0–15%. No model occupies the 60–85% escalation band. The difficulty progression from v3a to v3c created measurable degradation for Gemma 27B (15% → 0% raw) but not for frontier models.

## Total model calls

| Category | Calls |
|---|---|
| v3a calibration (4 models) | 160 |
| v3b calibration (2 models) | 80 |
| v3c calibration (4 models) | 160 |
| Scored evaluation — Haiku | 200 |
| Scored evaluation — Flash | 200 |
| **Total** | **800** |

Excludes failed HTTP requests from models that do not support structured output (Qwen 2.5 VL 7B, Gemma 3 4B), which returned zero usable predictions.

## Limitations

1. **Single task family.** Results are from one deterministic vendor-onboarding form at one resolution. They do not generalize to multi-page workflows, dynamic content, or other GUI layouts.
2. **Two scored models.** The scored evaluation covers only Haiku 4.5 and Flash 3.7. Mid-tier models were tested in calibration only.
3. **Confounded Flash delta.** The +9pp SoM lift for Flash is attributable to transient API failures, not grounding capability. The experiment cannot distinguish SoM benefit from provider reliability.
4. **No retry logic.** Request failures score as incorrect per protocol. A production system with retries would likely achieve 100%/100% for both models in both conditions.
5. **Saturated metric.** Both models are at or near ceiling. The benchmark cannot discriminate fine-grained grounding quality differences between frontier VLMs.
6. **Temporal ordering.** Conditions were evaluated sequentially (all raw, then all marks), not interleaved. This design enables temporal confounds.
7. **Target-state aliasing.** Target identity is aliased with screen state in the frozen capture grid. Control-type slices are descriptive compositions; differences cannot be attributed independently to control type.

## Offline reproduction

Analysis regenerates from stored predictions without model or network calls:

```bash
python -c "
from pathlib import Path
from pixelgym.serialization import load_jsonl
from pixelgym.grounding.analysis import analyze_predictions, build_error_review_template
import json

root = Path('.')
examples = load_jsonl(root / 'artifacts/grounding-dataset.jsonl')
for tag in ['claude-haiku-4.5', 'gemini-3.7-flash-adapted']:
    preds = load_jsonl(root / f'artifacts/grounding-v3a-scored-predictions-{tag}.jsonl')
    reviews = build_error_review_template(examples, preds)
    analysis = analyze_predictions(examples=examples, predictions=preds, error_reviews=reviews)
    (root / f'artifacts/grounding-v3a-analysis-{tag}.json').write_text(
        json.dumps(analysis, indent=2, sort_keys=True) + '\n')
"
```

## Artifact inventory

| File | Description |
|---|---|
| `artifacts/grounding-dataset.jsonl` | 100 scored examples (20 seeds × 5 states) |
| `artifacts/grounding-overlays.jsonl` | Set-of-marks overlay metadata |
| `artifacts/grounding-v3a-scored-predictions-claude-haiku-4.5.jsonl` | Haiku scored predictions (200 records) |
| `artifacts/grounding-v3a-scored-predictions-gemini-3.7-flash-adapted.jsonl` | Flash scored predictions (200 records) |
| `artifacts/grounding-v3a-analysis-claude-haiku-4.5.json` | Haiku full analysis |
| `artifacts/grounding-v3a-analysis-gemini-3.7-flash-adapted.json` | Flash full analysis |
| `artifacts/grounding-v3a-manifest.json` | Experiment manifest with decision history |
| `artifacts/grounding-v3b-manifest.json` | v3b calibration manifest |
| `artifacts/grounding-v3c-manifest.json` | v3c calibration manifest |
| `artifacts/grounding-v3c-calibration-predictions-*.jsonl` | v3c calibration predictions (4 models) |
