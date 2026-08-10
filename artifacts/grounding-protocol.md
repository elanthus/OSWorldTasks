# PixelGym GUI Grounding Protocol v1

**Status:** frozen on 2026-08-09 before benchmark capture or model output inspection  
**Owner approval:** 100 examples, 200 paired condition calls, Codex CLI with
`gpt-5.4-mini`  
**Protocol identifier:** `pixelgym-grounding-v1`

This protocol measures point grounding on the deterministic PixelGym vendor-onboarding
form. It compares a raw screenshot with a set-of-marks rendering of the identical
screenshot-target pair. A null or negative result is valid. No metric, exclusion, prompt,
or parser rule may change after a model output is inspected without incrementing the
protocol identifier and rerunning the pilot.

## Frozen provider and budget

- Today's provider is `codex-cli` version `0.147.0` using the exact CLI catalog identifier
  `gpt-5.4-mini` with low reasoning effort where supported.
- The installed CLI exposes the alias but not the dated API snapshot. Every response record
  therefore stores the model identifier, CLI version, run date, parameters, prompt hash,
  image hash, and cache key. Alias drift remains a disclosed reproducibility limitation.
- Codex runs are non-interactive, ephemeral, read-only, and receive exactly one image.
- OpenRouter is an implemented but unused alternative. It reads only
  `OPENROUTER_API_KEY` and `OPENROUTER_MODEL` from the process environment.
- The retained dataset has exactly 100 examples. Each example is evaluated once in each
  condition, for a hard cap of 200 condition calls.
- The pilot is a stratified ten-example subset evaluated in both conditions. Its 20 calls
  are included in the 200-call cap and cached responses are reused in the full run.
- A request that fails before any model response may be resumed. A wrong, invalid, or
  unparseable response is final and is never retried.

The selected model supports image input and structured outputs according to the
[official model documentation](https://developers.openai.com/api/docs/models/gpt-5.4-mini).
The Codex invocation uses the documented non-interactive `exec` image and output-schema
flags from the
[official CLI reference](https://learn.chatgpt.com/docs/developer-commands?surface=cli).

## Dataset construction

Capture uses the real deterministic browser task app at a 1024 x 768 CSS-pixel viewport
and device scale factor 1. Build-time browser instrumentation collects all actionable
elements before the target is consulted. It is not imported by `PixelGuiEnv`, either
backend, the evaluator, or the model evaluation adapter.

The fixed example grid is seeds 0 through 19 crossed with these five stable states:

1. `initial`: empty form with no focused control.
2. `text_field_focused`: empty form with Company name focused and no visible caret.
3. `validation_error`: empty form after a rejected submit, with the deterministic inline
   validation message visible.
4. `partially_completed`: Company name and Contact email filled, Country selected, no
   submission.
5. `completed_review`: every field filled with the visible request-card values, no
   submission.

The unstable native open-select popup is excluded because browser/OS popup rendering is not
part of the page bitmap. No screenshot is cropped around the requested target.

The target cycle repeats ten times across the 100-example grid, yielding ten examples for
each actionable semantic target:

1. Company name text input.
2. Contact email text input.
3. Contact phone text input.
4. Tax ID text input.
5. Country select.
6. Net 15 payment-terms radio choice.
7. Net 30 payment-terms radio choice.
8. Net 45 payment-terms radio choice.
9. Expedited-onboarding checkbox.
10. Submit button.

Instructions use the fixed natural form `Click the <visible control description>`. Radio
instructions include their visible option text. Each instruction uniquely identifies one
intended actionable element.

## Versioned example schema

`artifacts/grounding-dataset.jsonl` contains one JSON object per line with exactly these
fields:

```json
{
  "schema_version": "pixelgym-grounding-example-v1",
  "protocol_version": "pixelgym-grounding-v1",
  "example_id": "vendor-form-0001-company-name",
  "image_path": "artifacts/grounding/images/raw/vendor-form-0001.png",
  "image_sha256": "<64 lowercase hex characters>",
  "target_id": "company_name",
  "target": "Click the Company name field",
  "bbox": [536, 78, 1000, 108],
  "css_bbox": [536.0, 78.0, 1000.0, 108.0],
  "element_type": "text_input",
  "task_seed": 0,
  "task_id": "vf-<hash>",
  "screen_state": "initial",
  "css_width": 1024,
  "css_height": 768,
  "screen_width": 1024,
  "screen_height": 768,
  "device_scale_factor": 1.0,
  "capture_version": "pixelgym-browser-capture-v1"
}
```

Bounding boxes use screenshot-pixel `[x_min, y_min, x_max, y_max]` half-open bounds.
`css_bbox` stores the browser's floating-point CSS bounds. The capture tool asserts the
CSS-to-screenshot transformation against the decoded PNG dimensions.

Candidate metadata is stored independently by `example_id`. Every candidate contains a
stable semantic ID, element type, visible label, CSS box, and screenshot-pixel box. The
dataset target is joined to this already-collected candidate set only after capture.

## Paired conditions

Both conditions receive the exact same target string and full screenshot dimensions.
Prompt metadata contains no bounding box, expected point, target element ID, candidate
index, form answer, DOM, or accessibility data.

### Condition A: raw screenshot

Input is the untouched screenshot. The model must return only:

```json
{"x": 742, "y": 93}
```

Coordinates are integer screenshot pixels with origin at the upper-left. Values outside
the image bounds are invalid; values are never clipped.

### Condition B: set of marks

Input is the full screenshot with all independently generated candidates marked. Stable
mark IDs are assigned top-to-bottom, then left-to-right, with semantic ID as the final
tie-breaker. The model must return only:

```json
{"mark_id": 5}
```

The adapter converts a valid mark ID to the center of that candidate's screenshot-pixel
box. Unknown or malformed mark IDs are invalid and are never repaired.

## Set-of-marks leakage controls

- Candidate generation never accepts or reads the requested target, target ID, or target
  box.
- Every actionable element is marked, including all three radio choices.
- Candidate order depends only on candidate geometry and semantic ID.
- Mark styling and placement do not vary with the requested target.
- The target-to-candidate join happens only after the overlay and mapping are complete.
- Proposal coverage is measured by an exact semantic-ID match and is reported separately
  from conditional mark-selection accuracy.
- The raw screenshot is not cropped, highlighted, resized, or otherwise target-conditioned.

## Retention, validation, and exclusions

An example is retained only if all automatic checks pass:

- decoded image dimensions match recorded screenshot dimensions;
- target and candidate boxes are within image bounds and have nonzero area;
- CSS-to-screenshot coordinate transformation matches the recorded pixel box;
- the target semantic ID occurs exactly once in the independently collected candidates;
- the target box overlaps the intended rendered candidate;
- `(image_sha256, target, bbox)` is unique;
- the screenshot contains neither `/api/state` output nor evaluator-only data;
- no duplicate example ID, mark ID, or candidate semantic ID exists.

Capture or instrumentation failures are exclusions and are reported by seed/state before
any model call. Ambiguous instructions discovered during the pre-pilot contact-sheet review
require a protocol revision; they are not silently removed after model outputs exist. No
post-response performance-based exclusion is allowed.

## Frozen scoring and analysis

The primary metric is point-inside-ground-truth-box accuracy using half-open bounds.

Secondary metrics are:

- normalized Euclidean distance from predicted point to target-box center, divided by the
  screenshot diagonal;
- invalid or unparseable output rate by condition;
- accuracy by `element_type`;
- accuracy by target-area slice, where target area divided by screen area is `small` below
  0.005, `medium` from 0.005 up to 0.02, and `large` at or above 0.02;
- request latency and provider-reported usage when available;
- set-of-marks proposal coverage;
- mark-selection accuracy conditional on proposal coverage.

The paired effect is marks accuracy minus raw accuracy in percentage points. Its 95%
confidence interval uses a paired nonparametric bootstrap over examples with 10,000
resamples and random seed `20260809`. McNemar's exact two-sided test uses the discordant
paired correct/incorrect counts. Invalid predictions score as incorrect and remain in all
denominators.

Error review uses these fixed, non-exclusive inspectable labels: wrong semantic element;
correct region but point just outside the box; coordinate scaling error; small target;
crowded or overlapping controls; ambiguous instruction; mark omitted or illegible; correct
proposal but wrong mark selection; invalid response format. The report distinguishes
observed geometry/outcomes from reviewer inference and makes no claim beyond this one
model, task family, screen size, and paired intervention.

## Response record and cache contract

Each final condition record stores: protocol and prompt versions, example ID, condition,
provider, exact model identifier, parameters, UTC timestamp, latency, usage when available,
image and prompt hashes, cache key, raw final response, parsed prediction, common pixel
point or null, parse/request status, correctness, normalized center distance, and mark ID
when applicable. Provider traces are redacted before publication. Credentials are never
written to prompts, source files, artifacts, subprocess arguments, or Git history.

The cache key is SHA-256 over canonical JSON containing provider, model, condition, prompt
version, prompt text, image SHA-256, structured-output schema, and parameters. Cache hits
produce no provider call. The immutable full prediction JSONL contains exactly one final
record per example and condition, including explicit request-failure records.

## Human gates

1. Before D3.5, the owner must explicitly authorize the 20-call pilot. The agent then stops
   after the pilot contact sheet, raw-response/parser audit, and ten paired records.
2. Before D3.6, the owner must inspect and approve the pilot and explicitly authorize the
   remaining cached-or-new calls up to the 200-call total cap.
3. README claims, demo media, and resume bullets remain drafts until the owner approves the
   public-claims gate.

