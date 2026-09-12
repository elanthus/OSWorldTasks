# D5.8: owner-authorized zero unresolved budget holds

The owner instructed: “we can count unresolved holds as $0. I checked the
openrouter activity, and they're not getting billed.” The
[approval record](../artifacts/grounding-v5-d58-owner-budget/approval.json) keeps
the aggregate ceiling at USD 28 and the original six-hour deadline. It supersedes
positive budget weights for unresolved outcomes. The proposed USD 35 increase
is unnecessary under this instruction.

Confirmed charges and full request-sized in-flight reservations continue to
count against the ceiling. Unknown outcomes retain their request bounds and
failure records, but carry zero budget weight. The owner's activity check is the
billing evidence for this decision; the adjustment does not create a
provider-reported zero-cost receipt. A later actual charge is still counted and
can block further sends.

The extension retained its frozen code and accounting. It closed idle at its
reservation guard after 64 newly attempted assignments. The
[reconciliation](../artifacts/grounding-v5-d58-owner-budget/reconciliation.json)
then appended the owner authorization and 90 budget waivers totaling USD
4.64703030. Its [private verification](../artifacts/grounding-v5-d58-owner-budget/verification.json)
confirmed unchanged charges, wire counts, failed-outcome counts and journal
prefix, with no provider calls. Reconciliation supports recovery after
interruption without duplicate waivers and refuses an active request or held
operator lock.

The continuation also binds the preceding transport's last schedule and carries
its cooldown deadline and consecutive-failure count into the new lifecycle.
Changing accounting cannot shorten a provider wait or reset the backoff streak.
If that wait exceeds the remaining authorized time, no new episode starts.
The earlier handoff into the active extension occurred after its predecessor's
stored cooldown had expired, as checked against the original start event.

Four untouched assignments remained. A separately
[frozen continuation](../artifacts/grounding-v5-d58-owner-budget-continuation/execution-plan.json)
used the zero-hold ledger. Its model, route, prices, request
bounds, retries, task bank, order, prompts, focus cue and deferred correctness
remained unchanged. It preserved all started assignments, including the partial
episode at the reservation boundary. The deadline remained six hours from the first reliable
continuation's original durable start, including setup time. No confirmatory
task or D5.8 human verdict was authorized by this accounting change.

The [final report](../artifacts/grounding-v5-d58-owner-budget-continuation/report.md)
records all four assignments, including the final history episode cut short at
the six-hour deadline. That phase made 106 wire requests and added USD 0.702578250
in confirmed charges. Aggregate charges closed at USD 23.978227275, with zero
in-flight reservations and zero unresolved budget holds. Five further unpriced
outcomes remain recorded with zero budget weight. The [audit](../artifacts/grounding-v5-d58-owner-budget-continuation/verification.json)
remeasured all 100 cohort assignments, preserved the preceding 96 results,
reconstructed 107 planned requests, and verified identical retry bodies and
the inherited cooldown. The public verifier also completed with exit status zero.

The implementation is in
[`owner_budget.py`](../pixelgym/grounding/v5/owner_budget.py) and
[`run_grounding_v5_owner_budget_continuation.py`](../scripts/run_grounding_v5_owner_budget_continuation.py).
The read-only analyzer checks the preserved prefix, unchanged charges and wire
counts, every appended waiver, and the replayed balances. Continuation analysis
also reconstructs the unchanged request bodies, verifies retry identity, and
retains unpriced responses with zero budget weight.

Before freezing this repair, the owner-accounting and continuation suite passed
27 tests in 11.59 seconds. The tests cover approval binding, replay after an
interrupted waiver, active-request protection, later actual charges, rejection
of changed accounting evidence, the original absolute deadline, and progression
through simulated failed episodes. Ruff passed for the repository; mypy passed
for the two new runtime files. These checks made no provider calls.

The executed operator sequence, before freezing and executing the final plan, was:

```bash
.venv/bin/python -m scripts.run_grounding_v5_owner_budget_continuation reconcile
PYTHONPATH=. .venv/bin/python artifacts/grounding-v5-d58-owner-budget-continuation/analyze.py --verify-reconciliation --journal .cache/d58-memory-calibration/aggregate.sqlite --write
.venv/bin/python -m scripts.run_grounding_v5_owner_budget_continuation prepare
```

The continuation required a fresh matching price snapshot and curl identity,
committed runtime sources and the exact committed execution plan before paid
execution. The reconciliation and its verifier make no provider calls. The
closed continuation can later be verified at its recorded source revision with
`scripts.verify_d58_at_revision`.

All assignments are now recorded; neither paid phase can restart. The
reconciliation records its own source revision and hashes, so it remains
independently reproducible in that case:

```bash
.venv/bin/python -m scripts.verify_d58_at_revision grounding-v5-d58-owner-budget --journal .cache/d58-memory-calibration/aggregate.sqlite
```

The additional source-provenance checks passed ten selected tests in 0.56
seconds; Ruff and mypy for the three affected runtime/verification files passed.
The cooldown-handoff checks passed six selected tests in 1.91 seconds, including
an active wait, an expired wait, insufficient remaining time, and the driver
loop. Their clock is simulated; they make no network calls or wall-clock sleeps.

Before the final paid phase, the full offline unit command
`.venv/bin/pytest -q -n 4 --dist worksteal tests/unit` completed with exit status
zero: 2,042 passed and 16 deprecation warnings in 95.55 seconds.
