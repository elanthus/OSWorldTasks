# D5.8: run the remaining 90 calibration assignments

The owner approved this continuation with “please run the remaining 90” after the
curl diagnostic completed all ten supplied-state actions. The
[execution plan](../artifacts/grounding-v5-d58-reliable-continuation/execution-plan.json)
preserves the ten recorded infrastructure failures and assigns only the 90
untouched episodes, 45 per mode, in their existing order. Every new episode starts
at reset and uses model actions. The task generator, seeds, focus cue, delayed
correctness feedback, prompts and screenshot intervention remain unchanged.

The continuation uses the [repaired transport](grounding-v5-d58-reliable-transport.md):
an initial send and at most two observable transient retries of the same request,
full server cooldowns, and one cancellable curl process per send. It records
exhausted retries as episode failures and proceeds to the next untouched
assignment. The old five-consecutive-episode-failure stop is removed. Provider
identity or price violations, transport retirement, and the spend or time limits
still stop the phase. Started episodes and closed phases cannot restart.

The aggregate ceiling remains USD 28, including all earlier D5.8 charges and
unknown holds. The starting ledger accounts for USD 8.026698825, leaving
USD 19.973301175 available. Each send checks its actual request-sized bound; the
diagnostic's USD 1 and 20-call limits do not apply to this continuation. Its
2,588 available environment actions permit at most 7,764 wire attempts, including
retries, subject to the dollar ceiling and a 90-minute phase deadline. Neither
limit guarantees that all assignments finish.

During execution, the owner approved “Allow up to 6 hours; keep $28 cap” after
the first pair took about five minutes. The
[runtime amendment](../artifacts/grounding-v5-d58-runtime-amendment/approval.json)
permits additional phases for untouched assignments. It leaves this phase's
frozen 90-minute deadline intact. The extension driver measures six hours from
this phase's original durable start, including intervening setup time, and
refuses to start after that absolute deadline. It preserves every started
episode, including a partial episode at the first phase's time limit. It may
continue only after a clean time-limit closure, not after a provider/transport
safety stop or budget exhaustion.

Runtime sources are frozen at `75c29ca38e8e57bacf1546c98f5a7f8f089b6141`, and the
plan is `sha256:61c029d3266c5f9cdfd68e5d989afa476ba4dd32e39c6ebcca3bdf1aba5aefed`.
The preceding diagnostic's runtime files remain byte-identical. The plan records
the later `pyproject.toml` test-marker and frozen-analyzer style changes, and binds
the new full-episode wrapper and driver. Prior executed source revisions and
evidence remain intact.

The first phase closed at its 90-minute limit with an idle transport and no
in-flight reservations. Its [report](../artifacts/grounding-v5-d58-reliable-continuation/report.md)
records 22 newly attempted episodes: 21 ended normally and one history episode
was cut short by the phase deadline, stored as `request_failure`. Of the new
assignments, history succeeded on 10/11 and stateless on 1/11; respectively 10/11
and 11/11 reached both memory consumers. These counts exclude the ten preserved
infrastructure failures. Sixty-eight assignments remain untouched.

The phase made 570 wire requests, with USD 4.232186775 confirmed and
USD 1.39394100 added as unresolved holds. The aggregate is USD 10.565320050
confirmed plus USD 3.08750655 held, leaving USD 14.347173400 under the USD 28
ceiling. The [private verification receipt](../artifacts/grounding-v5-d58-reliable-continuation/verification.json)
reconstructs 571 request bodies, verifies all 570 reservations and settlements,
and remeasures all 32 attempted episodes. Twenty-five logical actions were
retried; 24 recovered and the last was stopped by the phase deadline. A public
verification and a separate altered-wire-count rejection check also completed
without provider calls.

Analysis reports the complete 100-assignment cohort and the 90 assignments using
the repaired transport separately. It preserves infrastructure failures and
explicitly unrun assignments. Consumer exposure, first-choice correctness and
terminal success are separate measures; supplied-state diagnostics add no
episodes. This continuation includes no confirmatory tasks or final D5.8 verdict.

The offline continuation tests cover exact assignment retention, reset and
first-choice scoring, budget/time stops, interruption without replay, phase-level
limits, and progression through all 90 simulated failed episodes. They passed
11 tests in 10.54 seconds. Mypy checked 127 source files and Ruff passed before
execution. Existing transport fixtures cover the bounded retry mechanics.

After closure, the read-only verifier reconstructs each new request from stored
screenshots and policy state, checks identical requests within retries, remeasures
episode outcomes, reconciles individual settlements and holds, and checks the
preserved journal prefix. The revision helper makes verification possible after
later source changes:

```bash
.venv/bin/python -m scripts.verify_d58_at_revision grounding-v5-d58-reliable-continuation --journal .cache/d58-memory-calibration/aggregate.sqlite
```

This command requires the closed summary and verification files. It makes no
provider calls and leaves the checkout unchanged.

During the extension, the owner checked OpenRouter activity and directed that
unresolved holds count as USD 0. The
[owner accounting amendment](grounding-v5-d58-owner-budget.md) records that
decision and prepares an append-only adjustment after the active phase closes.
The ceiling remains USD 28 and the original six-hour deadline remains in force.
Historical phase summaries above retain their original accounting.
