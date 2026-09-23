# D5.8 Haiku successor decision package

**Status:** proposed response-free successor. The fresh Haiku calibration is complete, and the independent-representative power calculation is checked in. The previous Gemini decision remains historical. No Haiku candidate selection, D5.9 freeze, provider-call cap, runtime window, or execution approval is recorded here.

## Outcome

This package performs the next D5.8 decision step with the fresh Haiku Claude Code CLI evidence from [PR #210](https://github.com/elanthus/OSWorldTasks/pull/210). It replaces neither the historical Gemini evidence nor its owner decision. It creates a versioned analytical successor so a later owner choice can name Haiku history versus Haiku stateless as the primary pair without rewriting prior records.

The calculation uses the 44 representatives already designated by the Gemini calibration's logical-cluster analysis. That seed selection predates the Haiku outcomes. Six robustness twins are excluded from the independent denominator; all 50 paired outcomes remain in the calibration report.

## Recomputed independent outcomes

The [structured analysis](../artifacts/grounding-v5-d58-haiku-successor/analysis.json) and [generated report](../artifacts/grounding-v5-d58-haiku-successor/report.md) record:

| Outcome among 44 representatives | Count |
|---|---:|
| Both succeed | 0 |
| History only succeeds | 25 |
| Stateless only succeeds | 5 |
| Neither succeeds | 14 |
| Discordant | 30 |

The discordance estimate is 30/44, or 68.1818%. Its conventional 95% Wilson planning range is 53.4431%–80.0007%. These are planning inputs, not a significance test or a claim that the fixed confirmatory family mix is random.

The successor preserves the existing two-sided exact McNemar alpha of 0.05, 20-point minimum relevant absolute difference, and 80% target power. It does not substitute the observed Haiku effect for the minimum relevant effect.

| Independent pairs | Episodes per arm with 24 twins | Power at observed discordance | Power at upper sensitivity |
|---:|---:|---:|---:|
| 120 | 144 | 72.8% | 66.0% |
| 144 | 168 | 81.2% | 74.3% |
| 150 | 174 | 82.8% | 76.2% |
| 168 | 192 | 87.0% | 81.2% |
| 204 | 228 | 92.9% | 88.3% |

The prior 168-independent-pair design still clears the target at observed discordance and at the upper sensitivity endpoint. It remains an analytical candidate rather than an automatically inherited owner selection.

## Bound policy identities

The calibration evidence binds exact model `claude-haiku-4-5-20251001` through Claude Code CLI 2.1.267 and the `claude.ai` Max subscription route. The candidate policy IDs are:

- screenshot history: `policy-79441db33362e00a1ac6`;
- stateless current frame: `policy-e1d746cd6a83ffb12a20`.

The calculation reads only the checked-in response-free snapshot and the pre-existing representative allocation. It makes zero provider calls and generates zero confirmatory tasks.

## Remaining decisions and blockers

The owner still must decide whether to carry forward the 168-independent-pair design and formally replace the Gemini candidate pair with the two Haiku policies.

D5.9 is not executable from this package. The calibration manifests record `os_sandbox_applied: false` for both Claude Code CLI launches, so they do not yet satisfy the confirmatory policy-sandbox boundary. A later versioned freeze must also bind admitted tasks, exact source and runtime digests, count caps, a runtime window, the subscription execution boundary, and separate exact approval. No prior Gemini or Haiku calibration approval carries forward.

## Reproduction

This command rebuilds the analysis and report from checked-in evidence without contacting a provider:

```sh
.venv/bin/python -m scripts.prepare_grounding_v5_d58_haiku_successor --verify
```
