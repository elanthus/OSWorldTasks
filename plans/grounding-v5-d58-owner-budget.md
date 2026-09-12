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

The running extension retains its frozen code and accounting. After it closes
with an idle transport, the reconciliation command verifies the exact closed
journal and appends the owner authorization and one budget waiver per existing
unknown hold. It preserves all earlier events and known charges. Reconciliation
can recover after interruption without duplicating waivers. It refuses to run
while a request remains active or the operator lock is held.

If untouched assignments remain at a closed budget/time boundary, a separately
frozen continuation uses the zero-hold ledger. Its model, route, prices, request
bounds, retries, task bank, order, prompts, focus cue and deferred correctness
remain unchanged. It preserves all started assignments, including any partial
episode at the boundary. The deadline remains six hours from the first reliable
continuation's original durable start, including setup time. No confirmatory
task or D5.8 human verdict is authorized by this accounting change.

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

After the active extension closes, the operator sequence is:

```bash
.venv/bin/python -m scripts.run_grounding_v5_owner_budget_continuation reconcile
PYTHONPATH=. .venv/bin/python artifacts/grounding-v5-d58-owner-budget-continuation/analyze.py --verify-reconciliation --journal .cache/d58-memory-calibration/aggregate.sqlite --write
.venv/bin/python -m scripts.run_grounding_v5_owner_budget_continuation prepare
```

The continuation requires a fresh matching price snapshot and curl identity,
committed runtime sources and the exact committed execution plan before paid
execution. The reconciliation and its verifier make no provider calls. The
closed continuation can later be verified at its recorded source revision with
`scripts.verify_d58_at_revision`.

Skip preparation of another paid phase if no untouched assignments remain. The
reconciliation records its own source revision and hashes, so it remains
independently reproducible in that case:

```bash
.venv/bin/python -m scripts.verify_d58_at_revision grounding-v5-d58-owner-budget --journal .cache/d58-memory-calibration/aggregate.sqlite
```

The additional source-provenance checks passed ten selected tests in 0.56
seconds; Ruff and mypy for the three affected runtime/verification files passed.
