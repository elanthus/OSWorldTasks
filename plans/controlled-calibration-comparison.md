# Complete the calibration panel: matched memory comparison

Status: the matched Qwen v2 pair completed all 100 assignments with zero successes;
Mistral Slot C subsequently completed all 50 assignments with zero successes. The
[calibration supplement](../artifacts/grounding-v5-calibration-supplement/report.md) preserves
these distinct runs, their per-task outcomes, spend, and local audit receipts.

Reader: the project owner reviewing D5.6/D5.7 evidence. This runbook retains the preparation
procedures used for those runs; it does not authorize repeating consumed paid approvals.
The historical Gemini and Qwen stateful results remain in the
[earlier report](../artifacts/grounding-v5-d56-completed-calibrations-report.md).
The matched Qwen pair is inconclusive about memory benefit because both arms scored zero.
Panel admission and D5.8 design decisions remain subject to owner review; no human gate is declared.

## Frozen intervention

Both arms use `qwen/qwen3-vl-8b-instruct` through OpenRouter's Alibaba route with fallbacks disabled.
They share system prompt, initial user prompt, response schema, coordinate adapter, model seed,
temperature, retry limits, deadlines, code revision, runtime, and ordered task assignments.
The comparison validator rejects manifest differences except policy ID, memory policy, and state
reducer. It also rejects unequal task order or action caps before a controlled adapter executes.

The stateful arm retains its own visible-action history. The stateless arm sends the same history
heading with an empty list on every request. Neither retains earlier screenshot content or
semantic notes. Consequently this tests **visible-action history**, not every form of memory.
The first request is identical in both arms. Every episode resets state. Paired assignments are
adjacent, and the arm executed first alternates across tasks. The provider model is an alias;
identical request controls do not prove an immutable upstream model or deterministic responses.

## Prepare the smoke

Commit the implementation and use a clean tracked checkout. The preparation command performs no
provider calls and refuses to overwrite a plan file. Output paths below must be unused; replace
them together when preparing another package.

```bash
.venv/bin/python scripts/prepare_grounding_v5_comparison.py plan \
  --phase smoke --maximum-spend-usd 0.34 \
  --run-output artifacts/grounding-v5-controlled-qwen-smoke-run \
  --output artifacts/grounding-v5-controlled-qwen-smoke-plan.json
.venv/bin/python scripts/run_grounding_v5_calibration.py --validate-only \
  --plan artifacts/grounding-v5-controlled-qwen-smoke-plan.json
```

The smoke uses ten development tasks spanning all six families, one action per task per arm,
twenty model attempts and wire requests total, no retries, and zero provider control requests.
It tests transport and dispatch compatibility; it does not measure memory effects.
The frozen Qwen request guard is `$0.016719872`, so twenty requests reserve at most `$0.334397440`.
The proposed `$0.34` ceiling is **new incremental spend**, separate from all consumed approvals.
Verify current route pricing before approval; the
[Alibaba endpoint record](https://openrouter.ai/api/v1/models/qwen/qwen3-vl-8b-instruct/endpoints)
was checked on 2026-09-10 and listed prompt/completion prices of `$0.000000117`/`$0.000000455`
per token, matching the frozen config.

The owner must approve the printed exact plan digest before execution:

```bash
.venv/bin/python scripts/run_grounding_v5_calibration.py --execute \
  --plan artifacts/grounding-v5-controlled-qwen-smoke-plan.json \
  --approved-plan-sha256 'sha256:EXACT_OWNER_APPROVED_DIGEST'
```

Execution requires the approved revision, a clean tracked worktree, the existing OpenRouter
credential configuration, and the supported policy runtime. Raw attempt journals and screenshots
remain restricted local evidence. Do not commit the run directory or raw summary merely because
the runner produced it. Review and redact publication evidence separately.

## Prepare full calibration after smoke review

The following is an unexecuted proposal and requires a second exact approval:

```bash
.venv/bin/python scripts/prepare_grounding_v5_comparison.py plan \
  --phase calibration --maximum-spend-usd 10.00 \
  --run-output artifacts/grounding-v5-controlled-qwen-calibration-run \
  --output artifacts/grounding-v5-controlled-qwen-calibration-plan.json
```

The calibration uses the frozen v2 `calibration-d56.json`: fifty tasks per arm, 100 assignments,
2,862 environment actions, at most 11,448 model attempts/wire requests, and no provider control
requests. Both arms permit the same bounded retry budget (three retries, four attempts per action).
The proposed fresh `$10` ledger is the binding cap; the conservative unrestricted request maximum
is `$191.409094656`. This ceiling cannot guarantee completion. Prior approvals authorize no new
spend. Do not use historical outcomes to replace a missing new assignment.

The runner retains invalid outputs without retry, stops after three consecutive failures, and
hard-stops on infrastructure failures, request failures, policy violations, or a blocked spend
ledger. An interrupted or stopped run remains incomplete; output reuse is forbidden. Review the
stored failure before preparing any successor. Human review of smoke is procedural: the generic
runner verifies the exact full-plan approval but does not automatically verify a predecessor smoke.

## Generate descriptive paired diagnostics

After an approved execution, derive diagnostics entirely from its stored summary:

```bash
.venv/bin/python scripts/prepare_grounding_v5_comparison.py report \
  --plan artifacts/grounding-v5-controlled-qwen-calibration-plan.json \
  --summary artifacts/grounding-v5-controlled-qwen-calibration-run/summary.json \
  --output artifacts/grounding-v5-controlled-qwen-comparison.json
```

The derivative checks plan/revision/trial bindings, rejects duplicate or unassigned results and
inconsistent success labels, retains terminal classifications, and lists outcomes by task and
family. Missing assignments remain null. An incomplete comparison has no overall success-rate
difference; its observed-pair table is explicitly descriptive. Negative and null results remain
unchanged. The derivative binds its source summary digest but does not independently audit the
restricted journal. Journal verification and the other item/family diagnostics required by D5.6
remain necessary before publishing the completed panel. No significance test treats robustness
twins as independent, and no benchmark or milestone verdict is generated.

## A rate-limited successor

If a run stops at `rate_limit_retry_exhausted`, first audit the frozen summary and journal without
making another call. Preserve that run as incomplete. The optional `--generation v2` configuration
raises the fallback backoff base from two to fifteen seconds in **both** arms. Its three retries
therefore wait fifteen, thirty, and sixty seconds when the provider supplies no usable retry hint.
This shared backoff also applies to retryable transport faults. The existing sixty-second ceiling,
four-attempt limit, request deadlines, tasks, prompts, adapters, parsing, and stop rules remain in
place. Valid `Retry-After` hints retain their existing precedence and bound.

Longer backoff is a proposed response to transient rate limits, not a guarantee of completion.
OpenRouter documents [exponential backoff and provider-side capacity limits](
https://openrouter.ai/docs/api_reference/limits). Provider fallbacks remain disabled to preserve the
controlled route. The original configuration remains the default; existing plan bytes and results
are not changed by selecting the new generation.

After committing and reviewing the successor implementation, prepare a fresh full comparison:

```bash
.venv/bin/python scripts/prepare_grounding_v5_comparison.py plan \
  --phase calibration --generation v2 --maximum-spend-usd 10.00 \
  --run-output artifacts/grounding-v5-controlled-qwen-calibration-v2-run \
  --output artifacts/grounding-v5-controlled-qwen-calibration-v2-plan.json
```

This proposes a new 100-assignment run with its own $10 ceiling and unchanged call caps. It starts
both arms afresh on the same fifty tasks and does not fill missing predecessor rows, pool outcomes,
or reuse the predecessor's paid-call approval. Review and approve the new exact digest before any
execution. Selecting `--generation v2 --phase smoke` also supports an optional no-retry smoke with
the existing twenty-call bound; that is a separate package and is not executed by the planner.

## Offline panel diagnostics and a Llama retry successor

`pixelgym.grounding.v5.calibration_diagnostics` scores stored host-side stage diagnostics without
executing the environment or a provider. It reports first decisions at critical stages, consumer
entry and resolution, visible recovery entries, irreversible commits, and action overhead. Missing
episodes and unentered decisions stay separate from observed incorrect decisions. A correctly
chosen action that intentionally enters a declared repair state counts as a correct first decision.

The owner selected both exploratory dependency-retention readings: resolution before any visible
error and resolution before the consumer enters its declared repair state. Both use entered
consumers as their conditional denominator and disclose unentered consumers. These diagnostics do
not change frozen terminal success. The benchmark plan does not supply a concrete frozen loop
rule; an exploratory repeated screenshot/action signature must not be labeled its protocol loop rate.

If the original `C-llama-stateful` run exhausts rate-limit retries, the registered
`C-llama-stateful-v2` configuration supports a separately approved successor. It preserves the
model, DeepInfra FP8 route, no-fallback routing, prompts, coordinate adapter, state reducer, and
180-second request deadline. It changes only the slot identity and bounded retry controls: four
attempts per action, three shared retries, and a fifteen-second fallback backoff base. The existing
sixty-second ceiling gives fallback waits of fifteen, thirty, and sixty seconds. Valid provider
retry hints retain precedence. The original two-attempt configuration remains available unchanged.

A successor must freeze a fresh plan at its committed revision, recheck route prices, and receive
exact owner approval. Fifty fresh calibration assignments imply 1,431 environment actions and at
most 5,724 model attempts/wire requests, with zero control requests. A proposed fresh $2 ceiling
remains binding; longer backoff does not guarantee completion. Preserve stopped predecessor rows
as incomplete evidence, never fill them with a successor, and never reuse their paid-call approval.

The owner initially selected a fixed Google Vertex route for Llama Scout. See the
[Slot C Vertex successor procedure](slot-c-vertex-successor.md) for its separate smoke and
calibration plans. That selection does not authorize new paid calls or retire the frozen
DeepInfra evidence.

After both Vertex probes stopped on HTTP 404, the owner selected Mistral Small 4 on 2026-09-10.
Use the [Mistral successor procedure](slot-c-mistral-successor.md) for the current preparation.
Its smoke and any full calibration each require fresh exact approval.
