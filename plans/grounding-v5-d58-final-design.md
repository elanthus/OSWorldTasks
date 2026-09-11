# D5.8 — Final evaluation design decision package

**Status: approved full calibration stopped at its aggregate budget guard after 5/100 episodes. Known spend USD 0.84307725; unknown-charge reservations USD 2.88645120. Final evaluation freeze pending.**

**Decision so far:** repair the original bank before a final evaluation. Its Qwen comparison is
inconclusive, and its answer shortcuts undermine memory validity. The approved successor implements
two deferred facts and a matched screenshot-history intervention. Its full calibration is incomplete;
the available evidence cannot support signing the executable D5.8 manifest. The
[parent protocol](grounding-v5-agent-benchmark.md#delivery-sequence) assigns this decision to the
owner and requires a separate explicit approval for confirmatory calls.

The owner approved the bounded repair scope with the response “approved, 5 dollars”, then selected
deferred correctness feedback until final submission. The
[approval record](../artifacts/grounding-v5-d58-design/repair-approval.json) preserves both decisions.
That initial approval covered implementation and a USD 5 aggregate planning ceiling. The owner
subsequently approved the presented ten-example pilot with “OK, unit tests passed. Lets do the
calbration”; its [execution record](../artifacts/grounding-v5-d58-calibration-pilot/execution-plan.json)
binds the exact cases, policy manifests, driver revision and caps. The owner then approved the full
matched calibration with “ok, please proceed with the full run”, within the same aggregate ceiling.
The [full execution plan](../artifacts/grounding-v5-d58-full-calibration/execution-plan.json) records
that approval. The final confirmatory freeze remains pending. These approvals declare no milestone
verdict and do not change historical results.

## Approved full calibration outcome

The [full report](../artifacts/grounding-v5-d58-full-calibration/report.md) retains all 100
assignments: fifty repaired tasks in each matched Gemini condition, representing 44 logical tasks
per arm. The fixed order cycles through families and alternates the first condition. Both arms
start from reset and use only model-selected actions, with no scripted prefix.

Only **five episodes were attempted** before the shared budget guard stopped execution:

| Condition | Attempted / assigned | Terminal successes | Reached both memory consumers | Correct first memory attempts / attempted |
|---|---:|---:|---:|---:|
| Screenshot history | 3 / 50 | 1 | 1 | 3 / 3 |
| Current screenshot only | 2 / 50 | 0 | 0 | 0 / 0 |

The history arm has two infrastructure failures. Both stateless episodes exhausted their action
limits at stage 1, before either memory consumer. The remaining **95 assignments were not run**;
they are missing coverage, not observed model failures. The [descriptive analysis](../artifacts/grounding-v5-d58-full-calibration/analysis.json)
contains two completed pairs: one history-only terminal success and one pair with neither arm
successful. Forty-eight pairs are incomplete. No significance test or confirmatory power estimate
is computed from this stopped campaign. One successful episode demonstrates end-to-end feasibility;
it does not establish the proposed exposure threshold, difficulty range, or a memory benefit.

The full phase issued **106 new requests** with **USD 0.65298975 in known charges**. Including the
twenty-call pilot, aggregate known spend is **USD 0.84307725**. Two requests have unknown charges,
each held at the full **USD 1.44322560** bound. Accounted spend is therefore **USD 3.72952845**,
leaving **USD 1.27047155** unreserved. Another full request reservation would exceed USD 5, so the
driver closed the phase. There are no in-flight requests, retries, provider control requests, or
confirmatory calls. Unknown-charge holds remain in the original durable ledger.

Before execution, the full-run ledger wrapper corrected an inherited behavior that reduced unknown
holds after observing cheap responses. This phase always retains the full approved request bound;
the completed pilot had no unknown charges and its stored results remain unchanged. The first
full-run process then encountered a bookkeeping error in its post-episode response-object audit.
The [interruption snapshot](../artifacts/grounding-v5-d58-full-calibration/interruption-1-summary.json)
and [execution amendment](../artifacts/grounding-v5-d58-full-calibration/execution-amendment-1.json)
preserve that history. The correction changed only the execution wrapper; every existing request,
failure, charge and reservation was retained. Continuation skipped the recorded first episode and
made no retry. Generator, tasks, policies, provider settings, seed order and dollar cap stayed frozen.

The [verification receipt](../artifacts/grounding-v5-d58-full-calibration/verification.json) reconstructs
the summary from the closed aggregate journal and verifies the original pilot prefix, frozen source
and policy bindings, provider identity, request counts, and budget stop. Keep the current design
frozen. Completing calibration now requires a separately approved budget or execution-plan revision;
confirmation remains disabled. The original Qwen floor result and this incomplete successor run
cannot justify final D5.8 approval.

## Completed bounded calibration pilot

The [stored report](../artifacts/grounding-v5-d58-calibration-pilot/report.md) records **9/10 correct
first choices with screenshot history and 6/10 with the current screenshot alone**. All twenty
assigned requests completed. One history click missed the consumer controls and remains a failure;
the other nineteen actions selected a consumer choice. There were no retries, provider control
requests, unknown charges or in-flight reservations. Total new spend was **USD 0.19008750**, leaving
**USD 4.80991250** within the existing aggregate ceiling at pilot completion. The full phase above
subsequently drew on that same balance.

The [paired analysis](../artifacts/grounding-v5-d58-calibration-pilot/analysis.json), generated only
from the stored summary, records five pairs correct in both conditions, four correct only with
history, and one correct only without history. Its exploratory two-sided exact McNemar p-value is
0.375. These ten diagnostic pairs do not establish a memory benefit. They do show that the policy
can select the repaired memory choices when supplied with the relevant screenshots.

Keep the admitted generator, policies and seed allocation frozen. Scripted prefixes supplied the
lead-in to every tested consumer, so this pilot cannot establish end-to-end reachability, terminal
success, item difficulty bands, or the power of the proposed final terminal-success comparison.
The subsequently approved full phase above used that same aggregate ledger. Its incomplete results
do not close those evidence gaps. No confirmatory task was evaluated.

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

## Approved generator repair scope

The opt-in `pixelgym-agent-v5-generator-memory-v2` implements the following rules across all six
families. Historical generator, contracts, and seed files remain unchanged. The default backend
still uses the historical generator; successor evaluation explicitly requires `MemoryBackend`.

1. Derive a per-stage permutation from a separate deterministic layout stream that does not read
   the requested target or correct control. Audit target positions without selecting favorable seeds, and test
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
6. Record each deferred choice and advance without revealing its correctness. Check both recorded
   choices at final submission. Wrong choices cannot be tried again on the same consumer, and a
   wrong deferred choice cannot earn success by selecting the correct final commit control.
   Any declared visible recovery branch must behave identically for correct and incorrect choices.
   This closes the immediate-feedback shortcut: trying every option previously cost at most four
   extra actions, within the existing six-action minimum correction allowance.

Repeat the existing admission, reset, replay, reward-hacking, coordinate, and human usability checks
on the revised development bank. Counterfactual consumer pixels must match bitwise while the
privileged answers differ. Do not require every memoryless trajectory to fail: guessing and
recovery are possible. Require evidence that positions, wording, and visible state cannot determine
the answer, and retain all first-attempt errors.

The design principle is consistent with memory evaluation under partial observability in
[POPGym, ICLR 2023](https://openreview.net/pdf?id=chDrutUTs0K). The counterfactual check above is a
repository-specific admission test, not a claimed result from that paper.

## Implemented repair and review evidence

The [development report](../artifacts/grounding-v5-d58-design/memory-repair/report.md) and
[source binding](../artifacts/grounding-v5-d58-design/memory-repair/sources.json) identify the
reviewable successor. Source or generator changes after this binding require a new version and
fresh admission evidence before paid execution. No change may be selected using confirmatory results.

The new policy retains every observed lossless PNG and its own dispatched actions within one
episode, up to 32 observations. It adds the current screenshot through a pure reducer before the
existing runner persists the pre-call checkpoint. A new process can therefore replay the same
history without a hidden image cache. The matched control gets only the current screenshot.
Both arms share the system prompt, parser, coordinate adapter, provider settings and retry rules.
Neither gets private diagnostics, reward metadata, expected answers, or target annotations as
policy context. Interruption tests cover both the first and a later action boundary.

The schema and backend are versioned separately from historical evidence. The declared recovery
at the verification consumer asks every choice to be confirmed; it never reports whether the
reference was correct. An incorrect recorded choice seals an unsuccessful submission at final
commit and cannot earn evaluator reward. The environment still truncates unsuccessful episodes
at the action limit and terminates only on exact success.

This implementation uses the deterministic in-process pixel renderer. The linked source and
consumer PNGs are available for owner usability review. No browser/OSWorld integration or human
usability verdict is claimed by the automated checks.

## Proposed policy candidates and calibration routing

| Role | Proposed candidate | Required change or qualification |
|---|---|---|
| Capable memory arm | Gemini 3.7 Flash through the previously calibrated OpenRouter/Vertex route | New policy retaining all screenshots and its own dispatched actions within an episode |
| Matched control | The same Gemini route and inference settings | Current screenshot only; empty history; otherwise the same prompt, parser, adapter, retry rule, and action budget |
| Lower-ability calibration reference | Qwen3-VL-8B-Instruct through the previously calibrated route | The same revised screenshot-history harness |
| Additional calibration reference | Mistral Small 4 through the previously calibrated route | The same revised screenshot-history harness |

These remain candidate roles, not approved final executable policy IDs or availability guarantees.
Freeze exact route, model alias or snapshot, inference parameters, dependency/runtime digests,
prompt, parser, normalized-coordinate adapter, context limit, state reducer, response schema,
retry classification and deadlines before any calibration approval. No silent fallback to another
model or provider is permitted. The reference harness retains all legitimately observed screenshots
up to the existing episode limit; it does not use evaluator annotations to select frames. If those
requests cannot fit the model context and spend bounds, stop and revise the design before calls.

The approved pilot completed ten examples and twenty condition calls on development seeds. The
subsequently approved full Gemini phase stopped at its budget guard. Keep confirmatory seeds unused;
Qwen, Mistral, reliability and confirmatory runs remain outside the executable allowance.

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
family in the existing family order. The successor implements this reserved seed metadata; its
confirmatory tasks have not been generated or inspected. Give each family two additional regression canaries, four frontier items, and two
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

**Approved total new-spend ceiling: USD 5.00 across all successor phases.** This includes actual
charges and reservations for unknown outcomes. The approval is not a separate USD 5 allowance per
policy or per run. Confirmatory execution remains disabled until its exact plan is approved.

Completion of the proposed design within USD 5 has not been established. Historical per-episode spend does not price an all-screenshot history
policy. Before paid approval, calculate exact phase caps from the revised task and policy manifests
and a checked price catalog: environment actions, model attempts, provider control requests, and
total wire requests. Show conservative per-request reservations and projected phase costs. Count
unknown outcomes against the cap until reconciled; do not move unused budget between phases without
approval. If the bounded design cannot fit the owner's ceiling, present the sensitivity/cost
tradeoff before launching anything.

The [diagnostic plan](../artifacts/grounding-v5-d58-design/memory-repair/pilot-plan.json) binds two
candidate Gemini policy manifests to ten development cases. It permits at most **20 model attempts,
zero provider control requests, and 20 wire requests**, with no retries. Its **340 environment-action
cap** includes 320 scripted prefix actions and 20 model-selected consumer actions. The same prefix
is supplied chronologically to the history arm and omitted from the stateless arm. This deliberately
isolates first-attempt recall; it cannot supply end-to-end calibration success rates or paired power.
That pilot plan supplies no allowance for other phases. The separate full plan added only the
matched Gemini end-to-end phase: at most 2,862 model/wire requests and 2,862 environment actions,
zero control requests, and no retries. Its aggregate caps carry the pilot forward to 2,882
model/wire requests and 3,202 environment actions, still within the same USD 5 ceiling. The dollar
guard stopped execution before those count caps were reached. Qwen, Mistral, reliability and
confirmation remain disabled.

The public [OpenRouter endpoint snapshot](../artifacts/grounding-v5-d58-design/gemini-price-snapshot.json)
records the matching Vertex routes, model display name and prices. The provider uses an alias, not
an immutable snapshot. Planning reserves its full 1,048,576-token input bound plus 4,096 output tokens
at the highest matching route rates: **USD 1.44322560 per request**, or **USD 28.86451200** if all
twenty requests incurred that maximum. These are conservative bounds, not predicted charges.
The USD 5 ceiling can accommodate three such unresolved reservations; a fourth must stop.
Actual settled costs may release enough room for more requests within the approved phase caps.

The historical candidate plan remains explicitly non-executable. The separately approved
[execution plan](../artifacts/grounding-v5-d58-calibration-pilot/execution-plan.json) enabled only
the now-completed twenty-call pilot. Its driver binds both arms to one durable aggregate ledger,
rejects a missing ledger, and never resends an uncertain request. Recovery and shared-budget tests
cover interruption boundaries and unknown charges surviving a fresh process. The
[endpoint recheck](../artifacts/grounding-v5-d58-calibration-pilot/price-recheck.json) matched the
candidate bounds before execution. Preserve the ignored authoritative ledger at
`.cache/d58-memory-calibration/aggregate.sqlite`; a subsequent approved phase must carry forward
its settled spend and reservations. Any changed manifest requires the owner's exact-plan approval.

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
.venv/bin/python -m scripts.prepare_grounding_v5_memory --verify
.venv/bin/python -m scripts.run_grounding_v5_memory_pilot report
.venv/bin/python -m artifacts.grounding-v5-d58-calibration-pilot.analyze
.venv/bin/python -m artifacts.grounding-v5-d58-full-calibration.verify
.venv/bin/python -m scripts.run_grounding_v5_memory_calibration report
.venv/bin/python -m artifacts.grounding-v5-d58-full-calibration.analyze
```

The full evidence verifier checks the public file hashes and stored report without a provider call.
Add `--journal` only on the original machine with the preserved ignored aggregate journal to repeat
the deeper provenance audit. The report and analysis commands use stored structured results only.

The audit reads committed response-free calibration receipts and current generator code, produces
the linked structured JSON, and runs five independent mathematical checks. It does not reread
restricted journals or rerun a model. The successor command verifies artifact bytes, current source
digests, and the report against stored structured evidence. Its build mode refuses to overwrite an
existing evidence directory. To reproduce a fresh build, use a separate checkout at the recorded
code revision before the evidence commit and run it without `--verify`.
