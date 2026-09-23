# Haiku D5.8 successor power check

This response-free successor recomputes the D5.8 paired-power inputs from the fresh Haiku Claude Code CLI calibration. It uses the 44 representatives selected before the Haiku outcomes were observed; it does not treat all 50 seed pairs as independent.

## Independent calibration outcomes

The representatives contain 0 both-success, 25 history-only, 5 stateless-only, and 14 neither-success pairs. That is 30/44 discordant representatives (68.1818%).

At the unchanged 20-point minimum relevant difference, two-sided exact McNemar alpha 0.05, and 80% target power:

| Independent pairs | Episodes / arm with 24 twins | Power at observed discordance | Power at upper sensitivity | All-discordant stress |
|---:|---:|---:|---:|---:|
| 120 | 144 | 72.8% | 66.0% | 54.0% |
| 144 | 168 | 81.2% | 74.3% | 62.9% |
| 150 | 174 | 82.8% | 76.2% | 66.3% |
| 168 | 192 | 87.0% | 81.2% | 70.0% |
| 204 | 228 | 92.9% | 88.3% | 80.1% |

The conventional 95% Wilson planning range is 53.4431%–80.0007%. Wilson endpoints are a disclosed planning range, not a guarantee for the fixed family mix or a formal confidence bound on prospective power. No observed-effect power or calibration significance test is reported.

The prior 168-independent-pair choice remains an analytical candidate: it yields 87.0% power at observed discordance and 81.2% at the upper sensitivity endpoint. This recomputation does not carry forward the prior Gemini owner selection automatically.

## Remaining boundary

No provider call was made and no confirmatory task was generated. Owner selection remains unset. D5.9 is not ready to execute: the calibration manifests record that OS sandbox enforcement was not applied to either Claude Code CLI launch, and a Haiku successor still needs frozen admission, caps, runtime, and exact execution approval.

The structured analysis records every representative seed and source digest in [`analysis.json`](analysis.json).
