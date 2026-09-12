# D5.8 prospective power and cost check

The retained calibration has 31 discordant outcomes among 44 designated independent representatives. At a 20-point difference and two-sided exact McNemar alpha 0.05, the original 120-pair proposal falls short of the 80% power target.

These calculations use the minimum relevant difference, not the observed calibration effect. They make no confirmatory call and generate no confirmatory task.

| Independent pairs | Episodes / arm | Power at observed discordance | Power at upper sensitivity | Primary projected USD | Aggregate including repeats USD |
|---:|---:|---:|---:|---:|---:|
| 120 | 144 | 71.4% | 64.9% | 56.46 | 89.85 |
| 144 | 168 | 79.8% | 73.3% | 65.88 | 99.26 |
| 150 | 174 | 81.4% | 75.2% | 68.23 | 101.62 |
| 168 | 192 | 85.9% | 80.3% | 75.29 | 108.67 |
| 204 | 228 | 92.1% | 87.5% | 89.40 | 122.79 |

Observed discordance is 70.4545%; the conventional 95% Wilson endpoints are 55.7798% and 81.8445%. Wilson endpoints are a disclosed planning range, not a guarantee for the fixed family mix or a formal confidence bound on prospective power. No observed-effect power or calibration significance test is reported.

The 168-pair option has 85.9% power at observed discordance and 80.3% at the upper endpoint. At 100% discordance it has only 70.0% power; the target is conditional on the planning assumptions. Keep failures in the primary denominator and report infrastructure causes separately.

Cost uses USD 17.645094000 across 90 newly attempted episodes, plus USD 23.978227275 already charged. Every option includes 48 additional reliability episodes: twelve distinct tasks, two additional trials, two arms. Linear projection at historical prices and observed task/arm mix; includes three shortened history episodes and unknown outcomes with owner-assigned zero weight. It is not a request bound, price guarantee, or execution authorization.

The owner must select a sample size and planning cap before the final design can be frozen. This report itself authorizes neither resizing nor paid execution.

[Structured calculation and input hashes](power.json). The calculation sums exact paired-binomial probabilities using the frozen [original audit](../grounding-v5-d58-design/audit.py), consistent with the paired-proportion power framework in [Lachin (1992)](https://onlinelibrary.wiley.com/doi/abs/10.1002/sim.4780110909).
