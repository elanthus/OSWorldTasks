# Fake-backend throughput

Generated from stored evidence `fake-throughput.json`, measured `2026-11-13T06:19:59.356052+00:00` at revision `ce0b022a37af8bc63cd02ccd9baa171e68c5cef1` (worktree clean: yes).

Host: Apple M4 Pro, Darwin 27.0.0 (arm64); Python 3.12.15, NumPy 1.26.4, Pillow 11.0.0, Gymnasium 0.28.1.

Wall-clock measurements on one host, single process unless stated. They describe the in-process fake backend only, not the OSWorld or browser path.

Environment `PixelGym/VendorForm-v0`, observation 768x1024x3 uint8. Command: `scripts/measure_fake_throughput.py --output artifacts/throughput-v2/fake-throughput.json`.

## Steps per second (single environment)

3 repeats of 3000 steps each; median (min–max).

| Workload | NOOP share | Static-layer renderer | Single-pass reference | Speed-up (medians) |
| --- | ---: | ---: | ---: | ---: |
| Random policy (NOOPs reuse the cached frame) | 34.2% (1025/3000) | 1165 (1125–1166) | 212 (210–212) | 5.51x |
| Golden trajectory replay | 0.9% (28/3000) | 528 (526–529) | 129 (129–130) | 4.10x |

## Reset latency

300 resets over distinct seeds per repeat; values are the median across repeats of each statistic.

| Renderer | Median | p95 | Max |
| --- | ---: | ---: | ---: |
| Static-layer renderer | 7.09 ms | 7.78 ms | 8.27 ms |
| Single-pass reference | 6.89 ms | 7.55 ms | 8.22 ms |

## Vectorized (random policy, static-layer renderer)

| Vector env | Sub-environments | Env steps/s, median (min–max) |
| --- | ---: | ---: |
| `SyncVectorEnv` | 8 | 1022 (1015–1027) |
| `AsyncVectorEnv` (spawn) | 8 | 2521 (2419–2543) |

## Byte identity of the faster renderer

An untimed pass replays the same seeded action streams through both renderers and compares the SHA-256 of every frame, including each reset frame.

| Workload | Frames compared | Mismatched frames |
| --- | ---: | ---: |
| random | 3016 | 0 |
| golden | 3028 | 0 |
