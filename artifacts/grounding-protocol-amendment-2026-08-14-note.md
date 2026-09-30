# Grounding protocol v1: amendment note

Supplements [grounding-protocol.md](grounding-protocol.md). That file is not edited because
`grounding-results.json` and `day-3/release/release-observations.json` record its SHA-256.

**Amended 2026-08-14: aliasing disclosure only; no metric changed (diff `901734b..f6a247b`).**

The amendment in commit `f6a247b` added a frozen-design limitation paragraph: each target appears
in exactly one screen state, so target identity and screen state are aliased. It also relabeled
the `element_type` secondary metric as descriptive only. The primary metric, the scoring rule,
the sample, and every other secondary metric are unchanged.

Reproduce with:

```bash
git diff 901734b f6a247b -- artifacts/grounding-protocol.md
```
