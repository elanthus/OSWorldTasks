# D5.8 memory repair — development evidence

Generator: `pixelgym-agent-v5-generator-memory-v2`. Provider calls: **0**. New spend: **USD 0**.

72 development tasks completed deterministic golden and recovery replays, six mutation replays each, and the random-action floor check. No confirmatory task was generated or evaluated.

All 48 source interventions changed the required answer while retaining exactly identical consumer pixels and task instruction. Every source image changed. Consumer differing-pixel count and maximum channel delta are both zero, with no masking or tolerance.

Correct memory-choice positions in the 24 base development tasks (48 choices): `{"0": 19, "1": 17, "2": 12}`. The layout uses a target-independent deterministic permutation; finite samples need not balance exactly.

## Scripted construct diagnostics

These traces use privileged golden actions outside the two memory consumers. They are not pixel-only model scores. Each consumer uses only a fixed position or lexical ordering of the current labels. No retry or correctness feedback is available.

| Consumer rule | First choices correct / 48 | Terminal successes / 24 |
|---|---:|---:|
| label-max | 19 | 4 |
| label-min | 11 | 0 |
| position-0 | 19 | 4 |
| position-1 | 17 | 4 |
| position-2 | 12 | 0 |

The counterfactual pairs establish that the current image cannot uniquely determine the answer. They do not establish model ability, the size of a memory benefit, or confirmatory power. Human usability review and fresh end-to-end calibration remain open.

## Review artifacts

[Stored evidence](admission.json), [source binding](sources.json), [exact diagnostic call plan](pilot-plan.json). The call plan is non-executable, requires exact execution approval, and has zero confirmatory calls.

The approved USD 5 ceiling is shared across all successor phases. The conservative full-context reservation does not guarantee completion of twenty requests under that ceiling. An execution driver enforcing the shared durable ledger is required before any call.

The successor currently uses the deterministic in-process pixel renderer. No browser/OSWorld integration or human usability verdict is claimed.

Source images: [base](base-source.png), [changed fact](counterfactual-source.png). Consumer images: [base](base-consumer.png), [changed fact](counterfactual-consumer.png).
