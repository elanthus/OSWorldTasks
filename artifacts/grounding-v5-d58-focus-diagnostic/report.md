# D5.8 focus and timeout repair diagnostic

Scripted prefixes supplied every test state. These single-action checks do not measure end-to-end memory exposure or terminal success.

| Mode | Attempted / 10 | Desired text transition / 6 | Valid memory choices / 4 | Correct memory choices / 4 |
|---|---:|---:|---:|---:|
| history | 10 | 6 | 3 | 3 |
| stateless | 10 | 6 | 4 | 3 |

Stop: `all_assignments_completed`. New requests: 20. New known charges: USD 0.144682500.

Aggregate accounting: `{"blocked": false, "budget_accounted_spend_usd": "6.987031575", "in_flight_reservation_usd": "0", "spent_usd": "5.652835275", "unknown_charge_outcomes": 27, "unknown_reservation_usd": "1.33419630", "wire_requests_sent": 935}`. The shared ceiling remains USD 20; holds are not confirmed charges.

All failures and unrun assignments remain in [the summary](summary.json). [Execution plan](execution-plan.json), [historical diagnosis](diagnosis.json), [development renderer checks](admission.json), [before](focused-before.png), [after](focused-after.png).

Repeated development seeds and supplied prefixes make this a diagnostic, not an independent memory-effect estimate. The old stopped cohort is unchanged. Full calibration and final D5.8 approval remain open.
