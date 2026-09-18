# D5.9 confirmatory freeze candidate

**Status:** the owner selected 168 independent representatives, 192 episodes per arm, and a
USD 120 aggregate planning cap on 2026-09-18. The response-free freeze candidate is built and
admitted. Execution remains disabled: the approved model-attempt and provider-wire caps are both
zero, the proposed 96-hour runtime window is unapproved, and a separate exact paid-execution
approval is still required.

This package closes the sample-size and planning-budget choices left open by D5.8. It does not run
D5.9, declare a v5 milestone verdict, or change the frozen calibration results.

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

## Evidence package

The checked-in package contains:

- [owner selection](../artifacts/grounding-v5-d59-freeze/owner-selection.json), preserving the
  distinction between planning selection and paid execution approval;
- [fresh route metadata](../artifacts/grounding-v5-d59-freeze/price-recheck.json) for the exact
  `google-vertex/global` route, with no fallback;
- [canonical task manifest](../artifacts/grounding-v5-d59-freeze/task-manifest.json), including all
  192 canonical task records and the source-revision binding;
- [no-cost admission evidence](../artifacts/grounding-v5-d59-freeze/admission.json), covering
  golden, recovery, mutation, stale-submission, deterministic replay, and random-floor checks for
  every selected task; and
- [execution-plan candidate](../artifacts/grounding-v5-d59-freeze/execution-plan.json), binding the
  policies, jobs, primary test, reliability subset, prices, source files, count caps, and dollar
  caps.

The count caps are conservative mechanical ceilings, not cost projections. The primary phase
allows at most 10,830 environment actions and 32,490 model/wire attempts. Reliability allows at
most 1,304 actions and 3,912 attempts. No provider control requests are allowed. These count caps
assume up to three bounded attempts for every environment action; the dollar guard can stop work
earlier.

Historical confirmed charges are USD 23.978227275. The candidate assigns USD 85.00 to primary
execution and USD 11.021772725 to reliability, totaling the selected USD 120 aggregate cap.
Request-sized reservations use the frozen `gemini-png-request-budget-v1` rule and the fresh route
prices. Full completion is not guaranteed within the dollar cap, and unused budget cannot move
between phases without approval.

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

## Reproduction

The builder performs no provider calls. From the repository root, substitute the source revision
recorded in the task manifest:

```sh
.venv/bin/python -m scripts.prepare_grounding_v5_d59_freeze \
  --source-revision 8a2d9c505bc64e03f00171eb8180a1ab334b3f6c --verify
```

The command reconstructs all canonical records and every admission replay, then compares the exact
bytes. It does not fetch prices or contact a model endpoint.
