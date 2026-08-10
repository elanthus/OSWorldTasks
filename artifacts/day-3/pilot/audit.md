# Grounding Pilot Audit

- Protocol: `pixelgym-grounding-v1`
- Provider/model: `codex-cli` / `gpt-5.4-mini`
- Calls: 20 (10 paired examples)
- Parse failures: 0
- Request failures: 0
- Raw accuracy: 5/10 (50%)
- Marks accuracy: 10/10 (100%)
- Proposal coverage: 10/10 (100%)
- Same target text in both prompts: True
- Raw coordinate bounds valid: True
- Selected mark IDs exist: True
- Credential marker scan clean: True

Blue boxes in the review images are frozen ground truth and were added only after
model collection. Green points are correct; red points are incorrect. The first
three raw misses land on the visually similar read-only request card, which must be
considered during the human ambiguity review rather than silently excluded.
