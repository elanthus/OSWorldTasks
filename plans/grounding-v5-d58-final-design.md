# D5.8 — Final evaluation design decision package

**Status: proposed; human decision pending. No implementation or paid calls authorized.**

**Recommendation:** defer the final evaluation freeze. The current bank does not yet support a
credible test of memory. Approve a bounded generator and policy revision first, validate it without
provider calls, and obtain fresh calibration before signing the executable D5.8 manifest. The
[parent protocol](grounding-v5-agent-benchmark.md#delivery-sequence) assigns this decision to the
owner and requires a separate explicit approval for confirmatory calls.

This package records the evidence, proposed choices, and conditions needed to finish D5.8. It does
not record the owner's approval, declare a milestone verdict, or change historical results.

## Why the current bank is insufficient

The [published Qwen evidence](../artifacts/grounding-v5-calibration-supplement/qwen-pair.json)
contains 0/50 successes in each arm. Its stored diagnostic receipt records **zero entered deferred
consumers out of 100 declared per arm**, and zero entered critical decisions out of 220 per arm.
The comparison therefore never observed the behavior it was supposed to distinguish. The tested
intervention supplied previous actions and response digests; it retained neither earlier screenshots
nor semantic notes. Zero discordant successes cannot estimate sensitivity to a meaningful memory
intervention, and a degenerate bootstrap interval is not evidence of equivalence.

The [no-call audit](../artifacts/grounding-v5-d58-design/no-call-audit.json) adds structural evidence
from all 92 development and calibration seeds, including historical and replacement seeds:

- All 806 correct non-text controls in `base` and `twin_a` tasks occupy position zero; all 118 in
  `twin_b` occupy position one. This is a generator-wide positional shortcut, not evidence that a
  tested pixel-only policy exploited it.
- The later verification consumer labels the correct option “Use verification …” and its
  distractor “Use request …”. The current screenshot identifies the required source role.
- Six calibration seeds produce identical visible “Match row …” labels for distinct controls at
  the request consumer. These require a generator-level uniqueness rule.
- The existing `memoryless_deferred_fact` mutation deliberately chooses a known wrong control and
  then issues NOOPs. Its failure validates that trace; it does not show that a competent policy
  without memory must fail.

See [generator](../pixelgym/grounding/v5/generator.py),
[mutation construction](../pixelgym/grounding/v5/policies.py), and
[policy state and request construction](../pixelgym/grounding/v5/panel_policy.py).
No confirmatory task was generated, captured, or evaluated for this decision package.

Historical Gemini achieved 35/50 in the
[completed calibration report](../artifacts/grounding-v5-d56-completed-calibrations-report.md).
That supports trying it as the capable reference system; it does not establish a memory benefit.
The [supplement](../artifacts/grounding-v5-calibration-supplement/report.md) retains Mistral's 0/50
and both Qwen nulls. Different routes, versions, and harnesses must not be pooled into a controlled
model comparison.

## Proposed generator and admission freeze

Create a new generator version; preserve the current version and all historical evidence. Limit
changes to the following rules, applied across each family rather than to individual failed items:

1. Derive a per-stage permutation from a separate deterministic layout stream that does not read
   the requested target or correct control. Balance target positions in a no-call audit and test
   policies based on position and superficial labels.
2. Give all alternatives at a deferred consumer the same grammatical and source-role form.
   Use distinct candidate values and vary which earlier fact is correct. Remove duplicate labels.
3. Make each task require at least two earlier facts. A consumer's current pixels and instruction
   must be insufficient to determine the correct response. Validate this with development-only
   counterfactual pairs: identical consumer screenshots and instruction, different source facts,
   different correct actions. These are distinct from robustness twins, which preserve answers.
4. Retain the current 10/12/14-stage difficulty levels, short text entry, action schema, sparse
   reward, correction-slack formula, raw 1024×768 screenshots, and six workflow families. Do not
   add stages, larger text-entry burdens, or difficulty increases before the first revised pilot.
5. Add a no-call baseline that makes locally competent choices while losing deferred facts.
   Report its first consumer choices and any later recovery separately. A golden prefix is
   permitted only in a labelled construct-validation diagnostic, never a model benchmark score.

Repeat the existing admission, reset, replay, reward-hacking, coordinate, and human usability checks
on the revised development bank. Counterfactual consumer pixels must match bitwise while the
privileged answers differ. Do not require every memoryless trajectory to fail: guessing and
recovery are possible. Require evidence that positions, wording, and visible state cannot determine
the answer, and retain all first-attempt errors.

The design principle is consistent with memory evaluation under partial observability in
[POPGym, ICLR 2023](https://openreview.net/pdf?id=chDrutUTs0K). The counterfactual check above is a
proposed admission test for this repository, not a claimed result from that paper.

## Proposed policy candidates and calibration routing

| Role | Proposed candidate | Required change or qualification |
|---|---|---|
| Capable memory arm | Gemini 3.7 Flash through the previously calibrated OpenRouter/Vertex route | New policy retaining all screenshots and its own dispatched actions within an episode |
| Matched control | The same Gemini route and inference settings | Current screenshot only; empty history; otherwise the same prompt, parser, adapter, retry rule, and action budget |
| Lower-ability calibration reference | Qwen3-VL-8B-Instruct through the previously calibrated route | The same revised screenshot-history harness |
| Additional calibration reference | Mistral Small 4 through the previously calibrated route | The same revised screenshot-history harness |

These are candidate roles, not frozen executable policy IDs or current availability guarantees.
Freeze exact route, model alias or snapshot, inference parameters, dependency/runtime digests,
prompt, parser, normalized-coordinate adapter, context limit, state reducer, response schema,
retry classification and deadlines before any calibration approval. No silent fallback to another
model or provider is permitted. The reference harness retains all legitimately observed screenshots
up to the existing episode limit; it does not use evaluator annotations to select frames. If those
requests cannot fit the model context and spend bounds, stop and revise the design before calls.

First run a separately approved, bounded calibration pilot on revised development/calibration
seeds. Keep confirmatory seeds unused. Its initial paid slice remains limited to ten examples and
twenty condition calls; completing longer episodes or the four-policy calibration requires an
explicit further approval under the repository's paid-call rule.

After a complete approved calibration, report consumer exposure, first-attempt retention, paired
terminal outcomes, failure routes, discordance, and costs for the matched Gemini pair. Require
the capable arm to reach deferred consumers on at least 80% of assigned calibration episodes and
have a mixed terminal outcome range of 20–80%. These are proposed usability/discrimination
thresholds, not a requirement to obtain a statistically significant positive memory effect.
If both arms stay at the floor or fail before the consumers, stop; do not spend on confirmation.
If the matched comparison is informative but null or negative, retain it and let the owner decide
whether to freeze and test that null prospectively. Do not tune until memory appears beneficial.

## Proposed seeds and statistical target

Retain the existing reserved confirmatory seeds **6000–6095**. The
[current manifest](../artifacts/grounding-v5-manifests/v2/confirmatory.json) contains 96 episodes,
72 logical tasks, and 24 robustness pairs. Its present 24-episode ablation selects only 12 logical
tasks; its 12-episode reliability subset selects only six. Neither subset should be treated as that
many independent memory tests.

For a confirmatory memory comparison, propose **144 episodes and 120 logical tasks per arm**:
retain the original seed roles and add 48 singleton tasks, with seeds **6096–6143**, eight per
family in the existing family order. These additional seeds are proposals and are not installed in
the generator. Give each family two additional regression canaries, four frontier items, and two
ceiling probes. The total allocation becomes 30/84/30 (approximately 20%/60%/20%). Existing seed
roles remain unchanged; generator revisions necessarily create new canonical task IDs and hashes.

Run the matched Gemini arms on all 144 episodes. Designate `twin_a` for each robustness pair and
every singleton as the 120 independent representatives before outputs exist. Report exact episode
success over all 144 assignments per arm, retaining every failure and identifying missing tasks.
Use a family-stratified logical-cluster bootstrap for the all-episode absolute difference and
intervals. Report Wilson intervals as conventional descriptive intervals with their independence
limitation; do not use them alone for inference across twins.

The **single confirmatory memory hypothesis** is the paired terminal-success difference on the
120 designated representatives, tested with two-sided exact McNemar at **alpha 0.05**. Freeze a
**20-percentage-point minimum relevant absolute difference** and **at least 80% power**. The
primary benchmark score still covers all 144 episodes; the independent representative analysis
supplies the hypothesis test. Other model contrasts and per-family comparisons are descriptive or
exploratory. If the owner wants additional confirmatory hypotheses, freeze a multiplicity rule and
redo power before approving calls.

The audit sums prospective exact-test power over discordant counts, following the paired-proportion
power framework discussed in
[*Power and sample size evaluation for the McNemar test with application to matched case-control
studies*](https://pubmed.ncbi.nlm.nih.gov/1509223/). The following are **planning scenarios**, not
estimates from the Qwen nulls:

| Discordance assumption | 72 independent tasks | 96 independent tasks | 120 independent tasks |
|---|---:|---:|---:|
| 30% | 87.4% | 95.4% | 98.5% |
| 40% | 72.7% | 86.2% | 93.4% |
| 50% | 62.3% | 77.1% | 86.2% |

Recompute power using the informative revised calibration's discordance and a disclosed sensitivity
range before final approval. The 120-task proposal does not guarantee 80% power for arbitrary
discordance or for a smaller effect. If the target is not met, the owner must explicitly enlarge
the design or accept a lower sensitivity; no automatic resizing is allowed.

Freeze bootstrap seed **20260911**, 10,000 resamples, and 95% intervals. Freeze two additional
reliability trials on twelve distinct representative tasks, two per family selected by ascending
seed before execution. Keep repeats outside the primary denominator and budget them separately.
No confirmatory generator output or policy response may influence selection.

## Spend and the remaining approval boundary

**New authorized spend in this package: USD 0. Confirmatory execution cap: zero requests.**
The request to prepare D5.8 does not approve numeric paid-call caps or a revised provider package.
The owner has been asked for a total new-spend ceiling; no answer is assumed here.

USD 25 can be a proposed initial planning ceiling, but completion of the proposed design within it
has not been established. Historical per-episode spend does not price an all-screenshot history
policy. Before paid approval, calculate exact phase caps from the revised task and policy manifests
and a checked price catalog: environment actions, model attempts, provider control requests, and
total wire requests. Show conservative per-request reservations and projected phase costs. Count
unknown outcomes against the cap until reconciled; do not move unused budget between phases without
approval. If the bounded design cannot fit the owner's ceiling, present the sensitivity/cost
tradeoff before launching anything.

The final freeze must bind the actual generator and source digests, canonical tasks and seed lists,
admission evidence, exact candidate policy manifests, primary comparison, power calculation,
reliability subset, prices, per-phase dollar/request caps, and owner approval. Those executable
artifacts cannot honestly be signed before the bounded repairs and calibration exist. D5.8 remains
open; D5.9 must not start from this document.

## Reproduction

From the repository root:

```sh
.venv/bin/python -m artifacts.grounding-v5-d58-design.audit
.venv/bin/python -m scripts.publish_grounding_v5_calibration_supplement --verify
```

The audit reads committed response-free calibration receipts and current generator code, produces
the linked structured JSON, and runs five independent mathematical checks. It does not reread
restricted journals, rerun a model, or establish the unimplemented revised generator's validity.
