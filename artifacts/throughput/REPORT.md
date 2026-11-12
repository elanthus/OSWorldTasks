# Fake-backend throughput

Generated from stored evidence `fake-throughput.json`, measured `2026-11-12T02:24:45.809438+00:00` at revision `a1b585c53fdb5d744628a3bced8ab909ac854ef1` (worktree clean: yes).

Host: Apple M4 Pro, Darwin 27.0.0 (arm64); Python 3.12.15, NumPy 1.26.4, Pillow 11.0.0, Gymnasium 0.28.1.

Wall-clock measurements on one host, single process unless stated. They describe the in-process fake backend only, not the OSWorld or browser path.

Environment `PixelGym/VendorForm-v0`, observation 768x1024x3 uint8. Command: `scripts/measure_fake_throughput.py`.

## Steps per second (single environment)

3 repeats of 3000 steps each; median (min–max).

| Workload | Static-layer renderer | Single-pass reference | Speed-up (medians) |
| --- | ---: | ---: | ---: |
| Random policy (a third are NOOPs, which reuse the cached frame) | 1168 (1146–1183) | 206 (198–208) | 5.66x |
| Golden trajectory replay (renders almost every step) | 349 (340–351) | 116 (114–116) | 3.02x |

## Reset latency

300 resets over distinct seeds per repeat; values are the median across repeats of each statistic.

| Renderer | Median | p95 | Max |
| --- | ---: | ---: | ---: |
| Static-layer renderer | 7.02 ms | 7.45 ms | 8.53 ms |
| Single-pass reference | 6.89 ms | 7.27 ms | 11.53 ms |

## Vectorized (random policy, static-layer renderer)

| Vector env | Sub-environments | Env steps/s, median (min–max) |
| --- | ---: | ---: |
| `SyncVectorEnv` | 8 | 1100 (1096–1102) |
| `AsyncVectorEnv` (spawn) | 8 | 2873 (2868–2886) |

## Byte identity of the faster renderer

An untimed pass replays the same seeded action streams through both renderers and compares the SHA-256 of every frame, including each reset frame.

| Workload | Frames compared | Mismatched frames |
| --- | ---: | ---: |
| random | 3001 | 0 |
| golden | 3001 | 0 |
