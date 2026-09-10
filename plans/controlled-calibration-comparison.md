# Complete the calibration panel: matched memory comparison

Status: Qwen comparison implementation prepared; new provider calls require exact owner approval.
Reader: the project owner reviewing the remaining D5.6/D5.7 work and the operator executing it.

The [completed calibration report](../artifacts/grounding-v5-d56-completed-calibrations-report.md)
contains completed Gemini and Qwen stateful runs. The original four-slot panel still lacks completed
Slot C and Qwen stateless evidence. Running only a stateless successor against historical Qwen
would confound memory with prompt, runtime, revision, and retry changes. Prepare and run both Qwen
arms together on the current revision. Preserve every historical run.

This implementation prepares that pair. It does **not** complete Slot C, certify a four-system
panel, or declare D5.6, D5.7, or a human milestone gate complete. Slot C still needs a current route
and price check, an exact successor approval, and a completed run. A replacement model requires
the owner's panel decision under the [benchmark plan](grounding-v5-agent-benchmark.md).

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
