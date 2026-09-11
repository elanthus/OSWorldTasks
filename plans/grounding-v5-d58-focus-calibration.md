# D5.8 focus-repaired end-to-end calibration

The owner approved a fresh calibration after the completed focus/timeout diagnostic
on 2026-09-11. The assignment remains all 50 calibration tasks in both screenshot
modes, with the existing USD 20 aggregate cap. This approval does not authorize
confirmatory calls or decide the final D5.8 gate.

## Frozen execution

The successor starts every episode at reset. It inherits the exact task order,
seeds, task hashes, action limits, generator, delayed correctness feedback, prompts,
and screenshot policies from the previous Gemini 3.8 cohort. Only the admitted focus
renderer and request-local transport change. Historical executed source and evidence
remain intact; the new phase has separate trial IDs and results.

The model is `google/gemini-3.8-flash` through `google-vertex/global`, without fallback
or retries. A fresh public endpoint snapshot retains USD 0.75/million input tokens
and USD 3.75/million output tokens. The shared private journal carries USD 5.652835275
in known charges and USD 1.33419630 in unresolved request-sized holds. Holds are not
confirmed billing.

One transport lifecycle serves both modes sequentially. Any runner deadline that
abandons an active request durably retires the transport and stops the phase. The
driver also stops for five consecutive non-normal episodes, a provider identity or
price violation, the budget limit, or 90 minutes since the durable phase start.
The time limit is checked before every model action; a call already in progress may
finish after it. A five-second drain precedes publication; the journal is not closed
under an active worker. An interrupted episode is never resent, and a closed phase
cannot restart.

## Cost projection and completion limits

The previous cohort's five history episodes that reached success cost USD 1.45464795
in total: USD 0.29092959 each. Its fourteen stateless episodes that reached the action
limit cost USD 1.60283250: approximately USD 0.114488 each. Applying those respective
means to 50 episodes per mode gives **USD 20.2708813 in new charges**.

This is a small, selected sample and a planning estimate, not a bound. The focus
repair may change episode lengths. Scaling all 51 attempted episodes instead gives
USD 9.1472, but early infrastructure failures shorten many of those episodes and
understate the cost of completing all assignments. The roughly USD 13.013 remaining
under the aggregate cap may fund about 60–65 episodes at the completed-episode mean.
The driver must stop at the cap even if assignments remain.

## Evidence and decision

The execution plan binds source digests, admission, prices, all 100 assignments,
the prior 10,323-event journal prefix, aggregate call caps, and policy manifests.
Before calls, sources and the plan must be committed and the tracked tree clean.

```bash
.venv/bin/python -m scripts.run_grounding_v5_focus_calibration prepare
.venv/bin/python -m scripts.run_grounding_v5_focus_calibration execute --approved-plan-digest <execution_plan_digest>
.venv/bin/python -m scripts.run_grounding_v5_focus_calibration report
```

The report is generated from stored structured results. It retains failed and unrun
assignments and reports consumer exposure separately from first-attempt correctness
and terminal success. Proposed calibration criteria remain history exposure of at
least 40/50 and history terminal success between 20% and 80%; a positive or significant
memory effect is not required. Stateless exposure must be inspected to determine
whether a memory comparison is informative. Confirmatory seeds remain unused.

## Recorded outcome

The [stored report](../artifacts/grounding-v5-d58-focus-calibration/report.md) records
five attempted episodes: three history and two stateless. All ended in infrastructure
failure, triggering the five-consecutive-failure stop; 95 assignments remain unrun.
One stateless episode reached both consumers and made two valid first choices, one
correct. Neither mode recorded a terminal success. The small sample does not establish
end-to-end calibration rates or a memory effect.

The 53 wire calls produced 48 positive charges, one confirmed zero-charge empty
response, and four unresolved outcomes: three SSL errors and one URL error. Known
new charges were USD 0.278438250, with USD 0.18923100 in new unknown holds. Aggregate
known charges are USD 5.931273525 and unknown holds USD 1.52342730, with nothing in
flight. The budget was not the stopping condition. No runner deadline or transport
retirement occurred, and no calls were retried.

The [verification receipt](../artifacts/grounding-v5-d58-focus-calibration/verification.json)
binds the 10,904-event journal prefix, reconstructs all 53 submitted requests, remeasures
all five attempted episodes, and matches reservation and settlement bounds. It preserves
the earlier 10,323-event prefix. A negative check that changed a memory score was rejected
against the journal. The preceding diagnostic also still verifies after journal extension.

```bash
PYTHONPATH=. .venv/bin/python artifacts/grounding-v5-d58-focus-calibration/analyze.py
PYTHONPATH=. .venv/bin/python artifacts/grounding-v5-d58-focus-calibration/analyze.py --journal .cache/d58-memory-calibration/aggregate.sqlite
```

The [cost projection](../artifacts/grounding-v5-d58-focus-calibration/cost-projection.json)
retains the prior completed-episode samples and arithmetic. This stopped phase's small
charge must not be extrapolated as the price of 100 completed episodes. Provider
reliability still needs diagnosis before another approved calibration phase.
