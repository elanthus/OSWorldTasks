# Fake-backend throughput

Generated from stored evidence `fake-throughput.json`, measured `2026-11-12T18:03:29.679170+00:00` at revision `988b77d29a98785999fd8a249b537e5d28a61ca0` (worktree clean: yes).

Host: Apple M4 Pro, Darwin 27.0.0 (arm64); Python 3.12.15, NumPy 1.26.4, Pillow 11.0.0, Gymnasium 0.28.1.

Wall-clock measurements on one host, single process unless stated. They describe the in-process fake backend only, not the OSWorld or browser path.

Environment `PixelGym/VendorForm-v0`, observation 768x1024x3 uint8. Command: `scripts/measure_fake_throughput.py`.

## Steps per second (single environment)

3 repeats of 3000 steps each; median (min–max).

| Workload | Static-layer renderer | Single-pass reference | Speed-up (medians) |
| --- | ---: | ---: | ---: |
| Random policy (a third are NOOPs, which reuse the cached frame) | 1181 (1076–1208) | 207 (205–209) | 5.70x |
| Golden trajectory replay (renders almost every step) | 529 (524–531) | 125 (119–129) | 4.23x |

## Reset latency

300 resets over distinct seeds per repeat; values are the median across repeats of each statistic.

| Renderer | Median | p95 | Max |
| --- | ---: | ---: | ---: |
| Static-layer renderer | 7.08 ms | 7.71 ms | 10.61 ms |
| Single-pass reference | 7.02 ms | 7.49 ms | 7.95 ms |

## Vectorized (random policy, static-layer renderer)

| Vector env | Sub-environments | Env steps/s, median (min–max) |
| --- | ---: | ---: |
| `SyncVectorEnv` | 8 | 1078 (1073–1079) |
| `AsyncVectorEnv` (spawn) | 8 | 2715 (2633–2718) |

## Byte identity of the faster renderer

An untimed pass replays the same seeded action streams through both renderers and compares the SHA-256 of every frame, including each reset frame.

| Workload | Frames compared | Mismatched frames |
| --- | ---: | ---: |
| random | 3016 | 0 |
| golden | 3028 | 0 |
