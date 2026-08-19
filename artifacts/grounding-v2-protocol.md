# PixelGym GUI Grounding Protocol v2

**Status:** dataset design frozen on 2026-08-18; evaluation not run
**Protocol identifier:** `pixelgym-grounding-v2`

V2 repairs the target-identity/screen-state aliasing discovered in the frozen v1 benchmark.
It preserves v1 and its reported paired result unchanged. No v2 model output exists, and this
protocol does not authorize a paid or otherwise externally metered model call.

## Crossed allocation

The benchmark reuses the 100 deterministic screenshots already captured for seeds 0–19 in five
stable screen states. It assigns exactly one target instruction to each screenshot using the
preregistered rule

```text
target_index = (task_seed + screen_state_index) mod 10
```

This yields a balanced incomplete-block design with:

- 100 examples and 100 distinct screenshots;
- all 10 targets represented in all 5 screen states;
- exactly 2 distinct seed replicates in every target-by-state cell;
- 10 examples per target, 20 per state, and 5 per seed; and
- 200 condition calls if every example is later evaluated once raw and once with marks.

Target identity and screen state are therefore crossed rather than aliased. Target and
control-type slices may be reported independently of screen-state composition. The two replicates
per cell still limit interaction estimates, and results remain specific to one synthetic form,
layout, resolution, and seed range.

## Reused capture and target-neutral proposals

`scripts/build_grounding_benchmark_v2.py` derives v2 metadata from the checked-in v1 dataset,
candidate records, and overlays. The raw screenshot and marked screenshot bytes are reused because
the candidate generator and marks rendering were target-neutral: every actionable candidate was
collected and marked before the requested target was joined. V2 changes the target instruction,
target box, and target mark ID for each seed/state cell; it does not recapture or alter pixels.

The builder validates the one-to-one joins, image references, coordinate transforms, proposal
coverage, exact versions, and full target-by-state allocation. It writes:

- `artifacts/grounding-v2-dataset.jsonl`
- `artifacts/grounding-v2-candidates.jsonl`
- `artifacts/grounding-v2-overlays.jsonl`
- `artifacts/grounding-v2-manifest.json`
- `artifacts/grounding-v2/contact-sheet.png`
- `artifacts/grounding-v2/marks-contact-sheet.png`

The manifest records hashes of all v1 inputs and v2 metadata outputs. Build-time target boxes and
candidate records remain evaluation labels, never model observations.

## Paired conditions and analysis

The raw and set-of-marks conditions, parser, point-inside-half-open-box scoring rule, invalid-output
retention, proposal-coverage metric, conditional mark-selection metric, paired bootstrap interval,
and exact McNemar test remain as specified by v1. Any future run must freeze the provider, exact
model identifier, prompt versions, parameters, pilot, call cap, and approval before the first
model call. V2 results must be reported separately from v1 and must not be presented as a rerun or
replacement of the stored v1 result.
