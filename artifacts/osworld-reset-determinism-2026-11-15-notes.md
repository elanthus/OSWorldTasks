# Guest reset determinism, 2026-11-15: provenance and observations

Hand-written on 2026-11-16. The numbers are in the [generated report](osworld-reset-determinism-2026-11-15-report.md),
built by `scripts/generate_osworld_reset_determinism_report.py` from the two `results.json` files.
Apart from the dated path redaction recorded at the end, nothing here changes those files. The **Observations** section is the author's reading of the
saved PNGs; it is not generated and is not a measured cause.

## How the runs were made

Approved by the project owner in chat on 2026-11-15. Commands, run one after the other from the
repository root on the PR B branch:

```bash
.venv/bin/python scripts/osworld_reset_determinism.py artifacts/osworld-reset-determinism-2026-11-15-a --guest-image .cache/osworld/osworld-v2-ubuntu-x86.qcow2 --seeds 0 7 11 23 --resets 5
```

```bash
.venv/bin/python scripts/osworld_reset_determinism.py artifacts/osworld-reset-determinism-2026-11-15-b --guest-image .cache/osworld/osworld-v2-ubuntu-x86.qcow2 --seeds 42 101 1000 10000 --resets 5
```

| Run | Revision (clean at start) | Started (UTC) | Wall time | Exit | Provider close |
| --- | --- | --- | ---: | ---: | --- |
| a | `93d7c24` | 2026-11-16T06:34:03Z | 5,040 s (84 min) | 0 | returned, no error, none suppressed |
| b | `074768b` (run a's evidence committed) | 2026-11-16T07:58:28Z | 4,422 s (74 min) | 0 | returned, no error, none suppressed |

- Both runs stayed under the 90-minute stop-loss; the driver's 85-minute guard skipped nothing.
- No Docker container was left running after either run (`docker ps -q` empty).
- The folders carry the local date (2026-11-15, US time); the recorded start times are UTC.
- Host: Apple Silicon Mac, provider `docker-local`, runtime image
  `docker.io/pixelgym/osworld-qemu-arm64:qemux-6.18`. The container log reports no KVM device, so
  the x86 guest ran under software emulation.
- During run a the host load average was about 55–60, mostly from unrelated short-lived `node`
  processes. Reset times in run a ranged 192–335 s against about 220 s in the 2026-11-12 check.
  This is context for the timings only.
- Seed 7's guest bundle digest, `300be223…`, equals the one the 2026-11-12 guest check recorded.

## Observations (author's reading of the saved PNGs)

Two seeds, both in run a, returned a frame whose hash differs from the seed's other resets. Task
and application-state hashes were identical on every reset of all 8 seeds.

1. **Seed 0, reset 3** (`seed-0-reset-3.png` vs `seed-0-reset-0.png`; 75,895 differing pixels,
   maximum per-channel delta 223, box x 253–770, y 0–147). The frame shows a GNOME desktop
   notification over the page: "Low Disk Space on 'Filesystem root' … only 983.0 MB disk space
   remaining". The form underneath matches the other resets. The guest's root filesystem appears
   to start each reset with under 1 GB free, and the notification appears when the desktop's disk
   check fires near capture time. This was not investigated further and is not claimed as the
   cause. No earlier stored artifact mentions this notification.
2. **Seed 7, reset 0** (`seed-7-reset-0.png` vs `seed-7-reset-1.png`; 111,455 differing pixels,
   maximum per-channel delta 227, box x 24–907, y 87–481). Reset 0's frame shows the page not yet
   fully drawn: the request card's values are empty and the payment-term radio buttons are absent.
   Resets 1–4 show the complete page with one hash. The reset still passed the page-ready check
   (two consecutive ready reads) and the frame-stability check (`stable_frame_count` identical
   screenshots), so an unfinished frame that holds still is accepted as the reset observation.
   Why the page reported ready before its content was on screen was not investigated.

## What this establishes and what it does not

- Establishes, for this guest image, host and the bundles listed in the report: semantic reset
  determinism on 8 of 8 seeds × 5 resets, and one frame hash on 6 of 8 seeds. On the other 2,
  one reset in five returned a different frame; the raw differences are in the report.
- Does not establish bitwise frame determinism of guest reset in general; 2 of 8 seeds are
  counterexamples in this run.
- Does not compare hosts or guests, does not compute perceptual similarity, and does not replace
  the 2026-09-06 five-reset record, which stays as it is.

## Open questions for the owner (not acted on)

- Whether guest reset should wait for a visually complete page (for example a page-side "painted"
  signal), since the frame-stability check accepts an unfinished page that holds still. This
  touches the reset path and the environment contract, so it was not changed.
- Whether to free space in the guest image or suppress desktop notifications. That would change the
  guest artifact, so it was not done.

## 2026-11-16 amendment: path redaction in the two `results.json` files

Both runs' `results.json` recorded `guest_image_path` (in `provenance` and in
`integration_metadata`) as the recording host's absolute path, which the checked-in-evidence guard
(`tests/unit/test_day3_release_evidence.py`) rejects. Those four strings were replaced with
`<repo>/.cache/osworld/osworld-v2-ubuntu-x86.qcow2`, using `pixelgym.evidence_redaction` with the
repository root mapped to `<repo>`. No other byte of either file changed, no measured value is
affected, and the guest report regenerates unchanged. The driver now applies the same redaction
(repository root, home directory, system temporary directory) whenever it writes `results.json`.
