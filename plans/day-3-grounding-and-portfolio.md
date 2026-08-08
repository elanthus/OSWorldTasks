# Day 3 — Run the Grounding Experiment and Package the Portfolio Project

## Outcome

By the end of Day 3, the repository should contain a versioned GUI-grounding dataset derived from the task environment, a paired raw-screenshot versus set-of-marks experiment, statistically honest results, and a concise portfolio presentation.

The goal is an experiment with a measured result, not a predetermined improvement. A null or negative set-of-marks result is acceptable if the benchmark, controls, analysis, and error investigation are sound.

## Ownership legend

- **YOU** — Approve API/provider spending, choose the final model, inspect samples, record the demo, and approve public claims.
- **AGENT · medium** — Dataset generation, overlay implementation, model adapters, report plumbing, and documentation drafts.
- **AGENT · high** — Experimental design, leakage audit, statistical analysis, and interpretation.
- **PAIR** — Agent prepares a batch or draft; you visually verify it before scale-up or publication.

Never place an API key in a prompt, source file, captured terminal, artifact, or Git history. Use an ignored environment variable and redact provider responses before publication.

## Schedule and task list

| ID | Timebox | Owner | Task | Concrete output |
|---|---:|---|---|---|
| D3.1 | 45 min | **PAIR** | Freeze the grounding protocol | Versioned schema, conditions, metrics, and sample budget |
| D3.2 | 75 min | **AGENT · medium** | Capture the benchmark | Screenshots and validated target bounding boxes |
| D3.3 | 60 min | **AGENT · medium** | Generate set-of-marks images | Deterministic overlays and proposal metadata |
| D3.4 | 60 min | **AGENT · medium** | Implement the VLM evaluation adapter | Strict structured predictions and cached raw responses |
| D3.5 | 30 min | **PAIR** | Run a small pilot | Ten paired examples with visual and parser review |
| D3.6 | 15–60 min | **YOU + AGENT · medium** | Approve and run the full experiment | Complete paired prediction file |
| D3.7 | 75 min | **AGENT · high** | Analyze results and errors | Confidence interval, paired test, slices, error taxonomy |
| D3.8 | 60 min | **AGENT · medium** | Create the results package | Tables, figures, examples, reproducible report |
| D3.9 | 60 min | **PAIR** | Finish README and demo | Reviewer-first project page and short episode video |
| D3.10 | 30 min | **PAIR** | Draft resume bullets | Evidence-backed bullets with real numbers only |
| D3.11 | 30 min | **YOU** | Run the final release gate | Public-claim and reproducibility approval |

## D3.1 — Freeze the grounding protocol

**Owner: PAIR**

Agent proposal:

- Target 100 examples; 60 is the minimum viable complete dataset, 150 is the stretch cap.
- Use identical screenshot-target pairs in both conditions.
- Condition A: raw screenshot, model returns one `(x, y)` point.
- Condition B: set-of-marks screenshot, model returns one mark ID.
- Use one pinned VLM and deterministic inference settings where supported.
- Cache every raw request metadata record and response so analysis never recalls the model.

Each example should include:

```json
{
  "example_id": "vendor-form-0001-country",
  "image_path": "screenshots/vendor-form-0001.png",
  "target": "Click the Country dropdown",
  "bbox": [812, 351, 1084, 397],
  "element_type": "select",
  "task_seed": 17,
  "screen_state": "initial",
  "screen_width": 1920,
  "screen_height": 1080
}
```

Primary metric:

- Point-inside-ground-truth-box accuracy.

Secondary metrics:

- Normalized point-to-box-center distance.
- Invalid or unparseable output rate.
- Accuracy by element type.
- Accuracy by target box area.
- Latency and API usage if available.
- Set-of-marks proposal coverage.
- Selection accuracy conditional on proposal coverage.

ScreenSpot uses screenshot, instruction, and actionable-element bounding-box annotations as a GUI-grounding formulation; it was introduced in the peer-reviewed [SeeClick paper at ACL 2024](https://aclanthology.org/2024.acl-long.505/). Set-of-marks should be treated as the experimental condition described in the [Set-of-Mark paper](https://arxiv.org/abs/2310.11441), not as a guaranteed gain.

Your review:

- Confirm targets are natural, unambiguous instructions.
- Confirm both conditions receive equivalent task information.
- Approve the sample cap before any paid model run.
- Choose and record the exact model identifier available to you.

Suggested agent handoff:

> Write a versioned grounding-benchmark protocol for the vendor-form environment. Define the JSONL schema, paired raw and set-of-marks conditions, metrics, exclusions, and sample cap. Include proposal coverage separately from mark-selection accuracy. Audit the design for label leakage and do not assume set-of-marks will improve accuracy.

**Done when:** the protocol is committed before model outputs are inspected and no metric can be changed silently after the run.

## D3.2 — Capture and validate the benchmark

**Owner: AGENT · medium**

Generate examples from multiple seeds and meaningful screen states:

- Initial empty form.
- One text field focused.
- Dropdown open, if stable.
- Validation error displayed.
- Partially completed form.

Balance targets across:

- Text inputs.
- Dropdown/select controls.
- Radio choices.
- Checkbox.
- Submit button.
- Small text or icon controls only if their purpose is unambiguous.

Collect boxes from browser instrumentation during dataset construction. Store CSS-pixel and screenshot-pixel dimensions, then assert the coordinate transformation. Do not use the target box to crop the model's raw screenshot.

Quality checks:

- Box lies within image bounds.
- Box has nonzero area.
- Box overlaps the rendered intended element.
- Target text uniquely identifies the intended element.
- No duplicate `(image, target, bbox)` records.
- Screenshot contains no privileged expected-answer endpoint or evaluator data.

Suggested agent handoff:

> Build a deterministic capture tool that resets seeded vendor-form tasks, records stable screenshots, and collects actionable-element bounding boxes through build-time browser instrumentation. Produce versioned JSONL plus a contact sheet with boxes and target labels. Add schema and coordinate-transform tests. Do not expose instrumentation to the evaluation adapter.

**Done when:** every example passes automatic checks and a contact sheet makes annotation mistakes visually obvious.

## D3.3 — Generate set-of-marks overlays

**Owner: AGENT · medium**

For each screenshot:

1. Obtain the full candidate set independently of the requested target.
2. Assign stable mark IDs using a documented ordering, such as top-to-bottom then left-to-right.
3. Draw thin, high-contrast boxes and readable labels.
4. Avoid covering control text where possible.
5. Save the candidate-to-box mapping.
6. Record whether the target box has a matching candidate.

Do not create marks only for the ground-truth target. That would leak the answer.

Required tests:

- Deterministic overlay bytes for the same input.
- Stable candidate ordering.
- Every mark maps to exactly one candidate.
- Mark IDs are unique and parseable.
- Proposal-coverage calculation is correct.
- Overlay dimensions equal raw image dimensions.

Suggested agent handoff:

> Implement deterministic set-of-marks overlays for the grounding dataset. Generate candidates without consulting the requested target, use a stable spatial ordering, preserve the full image, and save proposal metadata. Add tests for ordering, coverage, unique IDs, dimensions, and byte-level determinism.

**Done when:** paired raw and marked images exist for every retained example and target proposal coverage is reported rather than assumed.

## D3.4 — Implement the VLM evaluation adapter

**Owner: AGENT · medium**

The adapter must:

- Accept one example and one experimental condition.
- Construct a versioned prompt.
- Request a strict structured response.
- Store model identifier, parameters, prompt version, timestamp, latency, usage, raw response, and parsed prediction.
- Normalize raw point predictions to screenshot pixels.
- Convert a mark ID to the selected candidate's center point for common scoring.
- Mark parse failures explicitly instead of retrying until correct.
- Cache responses by a hash of model, prompt, image, and parameters.
- Support a dry-run/mock provider for tests.

Prompt contracts should ask for only the needed structure, for example:

```json
{"x": 1042, "y": 613}
```

or:

```json
{"mark_id": 17}
```

Suggested agent handoff:

> Implement a provider-neutral VLM evaluation adapter for paired raw-coordinate and set-of-marks conditions. Require strict structured output, cache raw responses and metadata, and score parse failures without hidden retries. Add a mock provider and unit tests. Do not run a paid model yet.

**Done when:** the mock provider can complete the whole pipeline and rerunning it produces no duplicate calls.

## D3.5 — Run a ten-example pilot

**Owner: PAIR**

You approve the limited API use before the agent runs it.

Agent work:

- Sample ten examples spanning all major control types.
- Run both conditions once.
- Produce raw responses, parsed predictions, and annotated result images.
- Stop after twenty total condition calls.

Your inspection:

- Are raw coordinates interpreted in the correct coordinate system?
- Are mark IDs visible and legible?
- Does the model receive the same target in both conditions?
- Are parse failures scored rather than silently repaired?
- Are outputs and credentials safely redacted?
- Are any targets genuinely ambiguous?

If the protocol or parser changes after the pilot, increment its version and rerun the pilot before the full experiment.

Suggested agent handoff:

> After explicit approval for API use, run exactly ten benchmark examples in both conditions. Produce annotated result images and a parser/protocol audit. Stop after the pilot and do not run the remaining dataset until the user approves it.

**Done when:** you sign off on the images, coordinate mapping, prompts, and output parser.

## D3.6 — Run the full paired experiment

**Owner: YOU + AGENT · medium**

Your task:

- Approve the exact number of remaining calls and any associated cost.
- Confirm the provider environment variable is set outside the repository.
- Start or authorize the run.

Agent task:

- Execute every retained example under both conditions.
- Preserve paired ordering.
- Resume from cache after interruption.
- Never discard or rerun an incorrect answer unless the request itself failed before a model response.
- Produce one immutable predictions JSONL file.

Suggested agent handoff:

> Run the approved grounding experiment on all retained examples under both versioned conditions. Resume from the response cache, retain invalid predictions, and produce an immutable paired predictions file. Do not analyze results or alter the protocol during collection.

**Done when:** every example has exactly one final record for each condition or an explicit request-failure record.

## D3.7 — Analyze results and errors

**Owner: AGENT · high**

Compute:

- Raw accuracy and set-of-marks accuracy.
- Paired accuracy delta in percentage points.
- A paired bootstrap 95% confidence interval for the delta.
- McNemar's exact test on paired correct/incorrect outcomes.
- Invalid-output rates.
- Normalized center-distance summaries.
- Proposal coverage and conditional mark-selection accuracy.
- Accuracy slices by element type and target size.

Use a fixed random seed for bootstrapping and store the analysis configuration.

Create an error taxonomy by manually inspectable categories:

- Wrong semantic element.
- Correct region but point just outside the box.
- Coordinate scaling error.
- Small target.
- Crowded or overlapping controls.
- Ambiguous instruction.
- Mark omitted or illegible.
- Correct proposal, wrong mark selection.
- Invalid response format.

The narrative must distinguish observation from inference. Do not claim causality beyond the paired intervention, and do not generalize one model/task family to all GUI grounding.

Suggested agent handoff:

> Analyze the frozen paired prediction file. Compute the named metrics, paired bootstrap interval, and McNemar exact test with a fixed seed. Separate proposal coverage from conditional selection. Produce an error taxonomy with linked examples and write a cautious interpretation that includes null or negative outcomes honestly.

**Done when:** the analysis reruns without model calls and every reported number is traceable to an example-level record.

## D3.8 — Create the results package

**Owner: AGENT · medium**

Produce:

- `artifacts/grounding-protocol.md`
- `artifacts/grounding-dataset.jsonl`
- `artifacts/grounding-predictions.jsonl`
- `artifacts/grounding-results.json`
- `artifacts/grounding-report.md`
- A raw-versus-marks accuracy figure with confidence interval.
- A breakdown by control type.
- A small gallery of representative wins, losses, and unchanged cases.

The report should include model/date, prompt versions, sample count, exclusions, proposal coverage, uncertainty, latency/usage, limitations, and exact reproduction commands.

Suggested agent handoff:

> Turn the frozen protocol, dataset, predictions, and analysis into a reproducible grounding report. Create only figures supported by the stored results. Include representative wins, losses, and unchanged pairs. Make the report regenerable without network or model calls.

**Done when:** one offline command recreates every table and figure from checked-in or release-attached result data.

## D3.9 — Finish the README and demo

**Owner: PAIR**

Agent draft order for the README:

1. One-sentence project claim.
2. Short episode GIF or video thumbnail.
3. Validation-results table.
4. Grounding experiment result.
5. Architecture diagram.
6. Quick fake-backend reproduction.
7. Real OSWorld integration instructions.
8. Environment contract.
9. Reward-hacking audit.
10. Limitations and future work.

Your tasks:

- Record or approve a 30–60 second clean episode.
- Verify no credentials, account identifiers, or private UI appear.
- Remove exaggerated language.
- Confirm a reviewer can find the main result within two minutes.

Suggested agent handoff:

> Rewrite the project README for a technical hiring reviewer using the final artifacts. Lead with the working environment, validation evidence, and measured grounding result. Include concise reproduction paths for fake and real backends. Do not fabricate metrics, hide failed checks, or call the environment deterministic without the validator's exact qualification.

**Done when:** the README is understandable without reading source code and every quantitative claim links to an artifact.

## D3.10 — Draft resume bullets

**Owner: PAIR**

Agent drafts bullets using actual values only:

> Built a Gymnasium-compliant, pixel-only GUI environment on OSWorld-V2 with bounded coordinate/keystroke actions, execution-based sparse rewards, and deterministic seeded task generation.

> Developed an RL-environment validation pipeline covering **N** repeated resets, **M** reward-timing trajectories, action/observation space integrity, and **K** adversarial reward-hacking surfaces.

> Created a **D**-example GUI-grounding benchmark and measured a **Δ-point** raw-versus-set-of-marks accuracy difference with a paired 95% confidence interval of **[L, U]**.

Your review:

- Replace placeholders only from final artifacts.
- Prefer precise scope over “production-grade” or “state of the art.”
- Ensure “OSWorld-V2” means a real integration run passed.
- Keep the most differentiated bullet first: environment validation.

Suggested agent handoff:

> Draft three one-line resume bullets from the final validation and grounding artifacts. Use only measured numbers, name OSWorld-V2 and Gymnasium accurately, and prioritize environment-validation impact. Leave a placeholder rather than inventing any missing metric.

**Done when:** every noun and number can be defended in a technical interview.

## D3.11 — Final release gate

**Owner: YOU**

Mark the sprint **COMPLETE** only if:

- [ ] Day 1 fast tests and environment checker pass from a clean install.
- [ ] Day 2 has one successful real OSWorld episode.
- [ ] The upstream tag and provider metadata are recorded.
- [ ] Validation reports are generated from structured evidence.
- [ ] The reward-hacking report includes known limitations.
- [ ] The grounding protocol was frozen before the full run.
- [ ] Every benchmark example has paired condition records.
- [ ] Statistics and figures reproduce without API calls.
- [ ] Set-of-marks proposal coverage is reported separately.
- [ ] README claims match the artifacts.
- [ ] Demo media contains no secrets or private data.
- [ ] Resume bullets contain no fabricated or ambiguous metrics.

If any box fails, publish the completed subset with the limitation stated plainly and schedule the missing item before using the stronger resume claim.

## Optional stretch work — only after the gate

1. Add a file-upload variant with three synthetic decoy files.
2. Add a native file-picker grounding slice.
3. Add a second VLM as a replication condition.
4. Add one small LibreOffice task as a separate milestone.
5. Run the validators across multiple screen resolutions or providers.

Do not start stretch work until the core project is reproducible and the public claims are approved.

## End-of-day artifacts

- Frozen grounding protocol.
- Versioned screenshots, targets, and bounding boxes.
- Deterministic set-of-marks overlays.
- Cached paired model responses and predictions.
- Statistical analysis and error taxonomy.
- Reproducible report, figures, and example gallery.
- Reviewer-first README and short demo.
- Evidence-backed resume bullets.
