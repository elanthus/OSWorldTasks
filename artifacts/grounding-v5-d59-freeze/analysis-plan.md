# D5.9 pre-registration record

This file preserves the D5.9 analysis plan beside its evidence after the planning directory was
retired. The sections below are verbatim copies, not paraphrases. Each copy sits in a fenced text
block so that its original relative links are kept byte-for-byte without being rendered from this
directory. A later statistics change must cite this record rather than edit it.

## Source record

- Copied from commit: `f0bcbd6f3e379f880baeca233d4b7e05528bcfc5`. This is the last commit that contains the retired planning
  directory. Both source files are unchanged between their last modifying commit and this commit,
  so the recorded digests are the digests at both.
- Digest method: `git show <commit>:<path> | shasum -a 256`.

| Source path | Copied lines | sha256 of the source file at the commit |
| --- | --- | --- |
| `plans/grounding-v5-d59-confirmatory-freeze.md` | 12–31, 62–75 | `3cd250bb77c908c760a51f231331cb72c165f7efa6eb7ff9655ec89b4d5de292` |
| `plans/grounding-v5-d59-haiku-freeze.md` | 13–23, 63–74 | `1c672e05b87cdb0a5d48e1aeb2dd7c32b3d8643584cbad8f18c365b7114f0ec9` |

## Gemini confirmatory freeze: selected design

Source: `plans/grounding-v5-d59-confirmatory-freeze.md`, lines 12–31. Contains the primary hypothesis, alpha, minimum relevant difference,
target power, stratified logical-cluster bootstrap (seed 20260911, 10,000 resamples, 95%
intervals), and reliability schedule.

```text
## Selected design

- The matched conditions remain Gemini 3.8 Flash screenshot history and the corresponding
  current-frame-only control. Their frozen policy IDs are
  `policy-f3643833caa8b2e9a526` and `policy-1c80069711f0cf37e792`.
- Seeds 6000–6191 supply 192 episodes per arm in six balanced workflow families.
- The allocation contains 168 predesignated independent representatives and 24 robustness pairs.
  Every singleton and each pair's `twin_a` is a primary representative.
- Difficulty bands contain 42 regression canaries, 108 frontier episodes, and 42 ceiling probes.
- The single primary hypothesis remains a two-sided exact McNemar comparison at alpha 0.05, with
  a 20-point minimum relevant absolute difference and 80% target power.
- The family-stratified logical-cluster bootstrap keeps seed 20260911, 10,000 resamples, and 95%
  intervals.
- Reliability uses the first two representatives by ascending seed in each family. Each condition
  receives two additional trials on those twelve tasks; repeats stay outside the primary score.

The memory generator and task schema are versioned as
`pixelgym-agent-v5-generator-memory-v3` and `pixelgym-agent-v5-task-memory-v3`. Version 3 preserves
the v2 task mechanics and adds the second eight-seed-per-family singleton block. Existing D5.8
evidence and its v2 schema remain unchanged and bound to their historical revision.
```

## Gemini confirmatory freeze: stop rules

Source: `plans/grounding-v5-d59-confirmatory-freeze.md`, lines 62–75.

```text
## Remaining human boundary

D5.9 must not start from this PR. Before execution, the owner must inspect the exact plan and
explicitly approve:

1. the exact execution-plan digest;
2. nonzero model-attempt and provider-wire caps no larger than the frozen candidate caps;
3. the proposed 96-hour aggregate runtime window or a replacement window; and
4. the named USD phase caps and the unchanged zero-weight rule for unresolved historical outcomes.

Any generator, policy, route, price, prompt, parser, adapter, cap, reliability subset, or source
change requires a new versioned freeze and admission check. A price change requires a fresh
snapshot and a new plan digest. No post-result tuning or rerun is permitted outside the frozen
reliability schedule.
```

## Haiku freeze successor: frozen comparison

Source: `plans/grounding-v5-d59-haiku-freeze.md`, lines 13–23. The Haiku successor keeps the primary test, alpha, minimum relevant
difference, and target power unchanged, and does not restate the bootstrap definition.

```text
## Frozen comparison

- Model: `claude-haiku-4-5-20251001` through Claude Code CLI 2.1.267 and the `claude.ai` Max
  subscription route.
- Screenshot-history policy: `policy-79441db33362e00a1ac6`.
- Current-frame-only policy: `policy-e1d746cd6a83ffb12a20`.
- Sample: 168 independent representatives and 24 robustness twins, or 192 episodes per arm.
- Primary test: two-sided exact McNemar at alpha 0.05, with the unchanged 20-point minimum
  relevant absolute difference and 80% target power.
- Reliability schedule: two additional trials per arm on the first two independent
  representatives in each workflow family.
```

## Haiku freeze successor: stop rules

Source: `plans/grounding-v5-d59-haiku-freeze.md`, lines 63–74.

```text
## Remaining human boundary

The [execution-plan candidate](../artifacts/grounding-v5-d59-haiku-freeze/execution-plan.json) is
non-executable. Before any confirmatory call, the owner must separately approve:

1. the exact execution-plan digest;
2. nonzero model-attempt and provider-wire caps no larger than the candidate ceilings; and
3. the proposed 168-hour aggregate runtime window or a replacement window.

Any policy, CLI version, provider route, prompt, parser, task, admission, retry, cap, reliability
subset, or security-boundary change requires another versioned successor. The D5.10 verdict and
all public model-quality or security wording remain human-owned.
```
