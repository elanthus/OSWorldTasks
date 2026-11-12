# Fake versus Chromium: differential comparison and barrier controls

Generated from stored evidence `evidence.json`, recorded `2026-11-12T20:55:55Z` at revision `82adfd64c95efb1cd5582c36f5b8f8b5725f5763` (worktree clean: yes) by `scripts/run_browser_differential_evidence.py`.

Platform: Darwin 27.0.0 (arm64); Python 3.12.15; Chromium 151.0.7922.34 via Playwright 1.62.0; Hypothesis 6.167.1. Input contract `pixelgym-vendor-form-input-v3`, fake renderer `pixelgym-vendor-form-fake-renderer-v2`. Task seeds sampled from 7, 11, 23, 101.

## Differential runs

Each row is one Hypothesis run without shrinking. A divergence is the first action after which the fake's form state or submissions differ from Chromium's.

| Run | Hypothesis seed | Sequences | Actions | Divergent sequences | Seconds |
| --- | --- | ---: | ---: | ---: | ---: |
| derandomized | none (`derandomize=True`) | 120 | 656 | 0 | 23.2 |
| hypothesis-seed-1 | `1` | 120 | 655 | 0 | 23.8 |
| hypothesis-seed-2 | `2` | 120 | 978 | 0 | 32.6 |
| hypothesis-seed-3 | `3` | 120 | 576 | 0 | 21.4 |

## Divergences

Divergent sequences across all runs: 0.

## Acknowledgement barrier

Source: `tests/integration/test_vendor_form_input_barrier.py`. One click on Submit with the submission held at the server; the value is the number of submissions each in-step evaluator read returned.

| Control | Barrier | In-step reads | Timeline (reward, terminated, truncated) |
| --- | --- | --- | --- |
| Negative (held until after the read) | off | [0] | [[0.0, False, True]] |
| Positive (released after arrival) | on | [1] | [[0.0, False, True]] |

Golden trajectory `tests/unit/fixtures/golden_trajectory_seed7_v2.json` under submit latency:

| Submit delay (s) | Steps | Positive-reward steps | Terminated steps | Truncated steps | Last in-step read | Observation equals later frame |
| ---: | ---: | --- | --- | --- | ---: | --- |
| 0.05 | 109 | [109] | [109] | [] | 1 | True |
| 0.3 | 109 | [109] | [109] | [] | 1 | True |

Total run time: 123.6 s.
