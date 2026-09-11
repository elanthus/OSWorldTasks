# D5.8 focus and timeout repair diagnostic

The owner approved the repair, diagnosis, and a small diagnostic on 2026-09-11.
This successor preserves the closed Gemini 3.8 cohort and uses the existing USD 20
aggregate ledger. It authorizes 20 new model calls, with no retries, provider control
calls, full calibration, or confirmatory execution.

## What the stored traces show

The [read-only diagnosis](../artifacts/grounding-v5-d58-focus-diagnostic/diagnosis.json)
examines all fourteen stateless action-limit truncations. Their 377 actions at the
first text-entry stage include 363 clicks on an already-focused empty input and
zero KEY actions. The old empty placeholder continues to say “Click, then enter
the short code” after focus; only the input fill changes. A clearer visible focus
cue is a plausible repair, whose effectiveness remains to be tested.

## Repair and preservation

`FocusMemoryBackend` adds a blue border, “Ready to type” placeholder, static caret
for entered text, and “INPUT ACTIVE” label. These depend only on current visible
input state. Continue remains the submission control. The generator, policy prompts,
coordinates, action budget, task answers, and deferred correctness feedback are unchanged.
The old backend and its executed wrapper remain in place for reproduction.

The [development checks](../artifacts/grounding-v5-d58-focus-diagnostic/admission.json)
compare 72 golden task replays against the predecessor. They record exact checkpoint
and submission equality, raw pixel differences, and confinement of changed pixels
to the focused input. Consumer pixels are identical to the previously admitted
renderer. These checks do not claim browser integration or human usability approval.

`IsolatedRequestBoundTransport` gives every request its own fixed configuration,
carrying sequential cooldown state forward without sharing mutable request budgets.
Overlapping sends are rejected before transmission. A runner deadline while a send
is still active records durable transport retirement, blocks further sends in that
lifecycle, and retains that request's bound. Late receipts can replace its unknown
hold with a known charge once; their actions never re-enter the episode.

The underlying HTTP call is not remotely cancelled. The diagnostic stops on a
retirement, allows a five-second drain, and closes the journal only after the worker
has finished. If the worker remains outstanding, process exit ends the local daemon;
its upstream billing remains unknown. Retirement persists across restart. There is
no automatic creation of a replacement transport or restart of a closed phase.

## Diagnostic design

Ten fixed development states are tested once in each mode, alternating condition
order. Six states test text entry: two unfocused, two focused and empty, one partially
typed, and one complete awaiting Continue. Four states test deferred choices, two
at each memory consumer. Seeds 5000, 5004, 5008, and 5012 are reused across states;
these are not independent statistical samples.

Scripted prefixes supply each state. History receives their chronological screenshots
and actions; stateless receives only the current screenshot. Only the model's next
action is scored. Desired text transitions are focusing the field, entering the next
correct character, or pressing Continue with the complete code. Valid and correct
memory choices are recorded separately. Failed and unrun conditions remain explicit.

The [execution plan](../artifacts/grounding-v5-d58-focus-diagnostic/execution-plan.json)
binds sources, admission, prices, cases, policy manifests, and the prior ledger prefix.
The route remains Gemini 3.8 Flash through `google-vertex/global`; the fresh public
price snapshot retains USD 0.75/million input and USD 3.75/million output. At most
USD 2.7648 in new request reservations fits beneath the existing USD 20 total ceiling,
including carried charges and unknown holds. This is a bound, not predicted billing.

Commands, from the repository root:

```bash
.venv/bin/python -m scripts.run_grounding_v5_focus_diagnostic diagnose
.venv/bin/python -m scripts.run_grounding_v5_focus_diagnostic admission
.venv/bin/python -m scripts.run_grounding_v5_focus_diagnostic prepare
.venv/bin/python -m scripts.run_grounding_v5_focus_diagnostic execute --approved-plan-digest <execution_plan_digest>
.venv/bin/python -m scripts.run_grounding_v5_focus_diagnostic report
```

Prepare refuses to overwrite a different frozen plan; execute requires committed
sources and plan, an unchanged source binding, a price snapshot less than 24 hours
old, and the original shared ledger. The report command renders stored summary
evidence without model calls.

## Limits and next decision

The [completed diagnostic](../artifacts/grounding-v5-d58-focus-diagnostic/report.md)
attempted all twenty assigned calls. Both modes made the desired text transition
in all six assigned states. History made three valid memory choices, all correct;
its fourth call returned an empty provider response with a confirmed zero charge.
Stateless made four valid memory choices, three correct. The failure remains in
the history denominator: both modes score 3/4 on the assigned memory checks.

New known spend was USD 0.144682500, bringing aggregate known spend to
USD 5.652835275. Unknown holds remained USD 1.33419630; no new unknown hold or
in-flight reservation remained. No runner deadline occurred in this diagnostic.
Timeout retirement and late-response behavior were exercised in the offline fixtures.

The [journal verification](../artifacts/grounding-v5-d58-focus-diagnostic/verification.json)
reconstructed all twenty submitted requests and nineteen dispatched actions, matched
every request's reservation and settlement bound, and verified that the preceding
10,044-event cohort prefix is unchanged. It made zero provider calls. To verify:

```bash
PYTHONPATH=. .venv/bin/python artifacts/grounding-v5-d58-focus-diagnostic/analyze.py
PYTHONPATH=. .venv/bin/python artifacts/grounding-v5-d58-focus-diagnostic/analyze.py --journal .cache/d58-memory-calibration/aggregate.sqlite
```

The supplied-state results support trying a fresh end-to-end calibration with the
revised focus cue. They do not establish end-to-end memory exposure, terminal success
rates, an isolated memory effect, or confirmatory power. Those remain requirements
for the separate full calibration and the owner's final D5.8 decision.
