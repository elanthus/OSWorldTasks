# PixelGym Day 2 validation report

Generated from stored evidence: `2026-08-14T23:48:24.046495+00:00`.

**Automated validation status: PASS.** Automated validation status is not the D2.11 human acceptance verdict.

**Human D2.11 verdict: PASS.** Day 3 authorized: yes.

Declared by project owner at `2026-08-10T05:52:03Z`.

## Release and provider metadata

- OSWorld release: `osworld-v2-2026.06.24` / `v2026.06.24`
- Upstream commit: `2b9b7b4eb73243d557bdbf2998fe18d8e18e19c6`
- Provider: `docker-local`
- Runtime image: `docker.io/pixelgym/osworld-qemu-arm64:qemux-6.18`
- Runtime base: `docker.io/qemux/qemu@sha256:b51ff8a5d69c10e57d3515c7a40dbbd47c410152b1491f849d12ede7607b80be`
- Runtime image ID / architecture: `sha256:60fe3501e5f5e16c5eaeb75b5b13b843c490891a1f24b86244b06c0c9d32d9f3` / `arm64`
- Docker engine: `linux/arm64` version `29.6.1`
- Docker allocation: `14` CPUs / `8320954368` bytes RAM
- Guest artifact: `xlangai/v2-image@v2026.06.24/osworld-v2-ubuntu-x86.qcow2.zip`
- Guest archive SHA-256: `sha256:eb737ae70b49849e24af407de6a518439a23de05a8497096a948334ce0a909aa`
- Preparation timestamp: `2026-08-10T00:43:16.468642+00:00`
- Task seed: `7`
- Screen size: `1920x1080`
- Host Python: `3.12.13`

## Provider stop-loss

Local Docker stopped after 4864.4 seconds (ceiling: 5400 seconds); the original amd64 outer-host path captured no real reset screenshot.

Final phase: `Chromium task-page launch`.

The pinned x86-64 guest boots on the arm64 Docker engine without KVM, but its desktop portal/browser startup is not viable enough to produce the required real screenshot within the bounded local investigation. The core Gym contract was not changed to accommodate this provider behavior.

The full diagnostic is stored in [`day-2/raw/provider-stop-loss.json`](day-2/raw/provider-stop-loss.json).

A later focused continuation checked the release-native browser path through `2026-08-09T21:20:11+00:00`. No screenshot; the additional paths converged on the same unaccelerated guest-browser blocker.

## Native ARM64 provider resolution

The digest-pinned `docker.io/pixelgym/osworld-qemu-arm64:qemux-6.18` host returned a real 1920x1080 public reset frame in 183.23 seconds.

The ephemeral guest root was expanded to 48.5 GiB with 20.4 GiB free.

The native ARM64 Docker host removes emulation of the outer amd64 container while leaving the pinned x86_64 OSWorld guest under QEMU software emulation. OSWorld expanded the guest root in an ephemeral snapshot to provide 20.4 GiB free without modifying the pinned base disk. It produced the required real public screenshot without changing the PixelGym observation, action, reward, or evaluator contracts.

The full diagnostic is stored in [`day-2/raw/native-arm64-provider-diagnostic.json`](day-2/raw/native-arm64-provider-diagnostic.json).

## UTM fallback diagnostic

UTM `4.7.5` reports VM `Linux` as `qemu/aarch64`, 6144 MiB RAM, hypervisor enabled.

The running UTM VM accelerates an aarch64 guest, while the pinned OSWorld Docker runtime and QCOW2 guest are x86-64. Architecture-specific ARM virtualization cannot supply x86 KVM acceleration to the nested OSWorld guest, so this VM would retain an emulated x86 path. Its configured 6 GiB RAM is also below the documented 8 GiB fallback recommendation.

The full read-only diagnostic is stored in [`day-2/raw/utm-provider-diagnostic.json`](day-2/raw/utm-provider-diagnostic.json).

## Automated status

Completed sections: 8 / 8.

Missing: `none`.

Failed: `none`.

## Reset determinism

| Backend | Resets | Task exact | App state exact | Bitwise visual | Min SSIM | Max differing pixels | Max channel delta |
|---|---:|---|---|---|---:|---:|---:|
| fake | 10 | yes | yes | yes | 1.000000 | 0 | 0 |
| real-osworld-docker | 5 | yes | yes | no | 0.999863 | 155 | 222 |

No visual mask or tolerance is applied by this report.

Observed real-reset differing-region bounding boxes (x1, y1, x2, y2): `[[1023, 8, 1039, 18], [1023, 8, 1039, 18], [1023, 8, 1031, 18], [1023, 8, 1039, 18]]`.

## Reward timing

Trajectories: 122; passed: 122; failed: 0.

| Trajectory | First reward | Terminal | Truncated | Expected | Observed | Passed |
|---|---:|---:|---:|---|---|---|
| empty-submit | — | — | — | no_reward | no_reward | yes |
| correct-fields-without-submit | — | — | — | no_reward | no_reward | yes |
| near-miss-company_name | — | — | — | no_reward | no_reward | yes |
| near-miss-contact_email | — | — | — | no_reward | no_reward | yes |
| near-miss-contact_phone | — | — | — | no_reward | no_reward | yes |
| near-miss-tax_id | — | — | — | no_reward | no_reward | yes |
| near-miss-country | — | — | — | no_reward | no_reward | yes |
| near-miss-payment_terms | — | — | — | no_reward | no_reward | yes |
| near-miss-expedited_onboarding | — | — | — | no_reward | no_reward | yes |
| wrong-or-stale-task-id-fixture | — | — | — | no_reward | no_reward | yes |
| golden-prefixes-000..108 (109 cases) | — | — | — | no_reward | no_reward | yes |
| complete-golden-trajectory | 109 | 109 | — | terminal_reward | terminal_reward | yes |
| duplicate-submit-after-success | 109 | 109 | — | terminal_reward | terminal_reward | yes |
| timeout-one-action-before-completion | — | — | 108 | truncated_without_reward | truncated_without_reward | yes |

## Space integrity

- Gymnasium checker: yes
- Sampled valid actions: 500
- All observations contained: yes
- Invalid inputs rejected before backend execution: yes
- Post-episode calls rejected: yes

### Real OSWorld smoke

- Safe public actions: 10
- All observations contained: yes
- Invalid inputs rejected before OSWorld execution: yes
- Empty Submit produced no reward: yes
- Provider closed: yes

### Real OSWorld golden episode

- Public actions: 112
- Positive reward count: 1
- First positive reward step: 112
- Terminal step: 112
- Provider closed: yes

| Real reward-timing case | Step | Reward | Terminal | Truncated | Passed |
|---|---:|---:|---|---|---|
| correct-fields-without-submit | 111 | 0.0 | no | no | yes |
| complete-golden-trajectory | 112 | 1.0 | yes | no | yes |

### Vendor-form browser submission boundary

- Browser: `chromium 151.0.7922.34`
- Source files hashed: 5
- Submit response: HTTP 200
- Privileged submissions recorded: 1
- Evaluator observed submission: yes
- Evaluator success: no
- Derived environment reward: 0.000000
- Checks: 10 / 10 passed

## Reward-hacking matrix

| Attack | Disposition | Evidence | Evidence passed |
|---|---|---|---|
| Agent emits DONE without completing the form | tested | Action type 3 was rejected before backend execution; the public Discrete action space contains only NOOP=0, CLICK=1, KEY=2. | yes |
| Empty or partial Submit | tested | empty-submit trajectory produced no reward; a partial privileged submission was also evaluated without reward. Chromium 151.0.7922.34 returned HTTP 200, recorded exactly one empty privileged submission, preserved the settled validation message, and the host-side evaluator derived reward 0. Stored source hashes match the checked-out implementation. | yes |
| Correct visible fields without Submit | tested | correct-fields-without-submit public trajectory produced no reward. | yes |
| Visible fake Success content | tested | A fake Success status string with no submission produced reward 0. | yes |
| Reuse prior successful state | tested | A correct submission carrying vf-stale-task produced no reward. | yes |
| Repeat valid submission | tested | The complete trace emitted one terminal reward and the next step was rejected after episode termination. | yes |
| Out-of-bounds clicks | tested | All four coordinate boundary violations were rejected before backend calls. | yes |
| Open terminal or developer tools | blocked | Ctrl, Alt, Command, Escape, function keys, and modifier combinations are absent from KEY_ALLOWLIST_VERSION=1; KEY exposes only one indexed allowlisted key. | yes |
| Navigate to a completion endpoint | blocked | The action interface has no browser-navigation action and cannot express Ctrl+L; the privileged endpoint is not linked by the task UI. | yes |
| Guess or alter a task identifier | tested | The stale-task fixture was rejected by the current host-side task identity. | yes |
| Trigger evaluator during partial write | mitigated | GuestTaskState appends an immutable complete record under one lock; evaluator also treats missing fields as mismatches, demonstrated by the partial fixture. | yes |
| Provider reset fails silently | tested | Real reset task/application hashes were checked | yes |
| Mutate or change an action after validation | tested | Environment tests use stateful Mapping and int subclasses and prove dispatch uses only the immutable ValidatedAction snapshot. | yes |
| Conflate truncation with successful termination | tested | timeout-one-action-before-completion truncated without reward and post-truncation step was rejected. | yes |

## Known limitations

- The release-named x86 host and the Apple Silicon qemux/qemu base are third-party Docker Hub images without release signatures; digest pinning mitigates mutability, not publisher compromise.
- On Apple Silicon the native outer host avoids redundant container emulation, but the released guest remains x86-64 and runs without KVM. Boot/stabilization may still be slow or fail even when the adapter is correct.
- The privileged /api/state endpoint exists inside the guest. The bounded action interface cannot navigate to it, but containment against a browser or guest OS exploit is outside this benchmark's threat model.
- OSWorld's structured computer action controller internally generates fixed pyautogui Python calls. PixelGym never accepts or forwards agent-supplied Python, but it inherits bugs in that upstream structured-action implementation.
- The guest desktop's live top-panel clock is outside the deterministic task app. Across five real resets, at most 155 pixels changed and all differences were localized to clock-glyph boxes [[1023, 8, 1039, 18], [1023, 8, 1039, 18], [1023, 8, 1031, 18], [1023, 8, 1039, 18]]; minimum SSIM was 0.9998633416019792. No visual mask or tolerance was applied.

## Reproduction commands

```bash
python scripts/prepare_osworld_docker.py
python scripts/validate_vendor_form_browser_boundary.py
python scripts/validate_day2.py fake
python scripts/smoke_osworld_reset.py
python scripts/osworld_space_smoke.py
python scripts/osworld_golden_trajectory.py check
python scripts/osworld_golden_trajectory.py record
python scripts/validate_day2.py real-resets
python scripts/validate_day2.py audit
python scripts/validate_day2.py assemble
python scripts/generate_validation_report.py
```

## Underlying evidence

- `browser_boundary`: [raw JSON](day-2/raw/browser-boundary.json)
- `fake_reset`: [raw JSON](day-2/raw/fake-reset.json)
- `real_golden_episode`: [raw JSON](day-2/raw/real-golden-episode.json)
- `real_reset`: [raw JSON](day-2/raw/real-reset.json)
- `real_space_smoke`: [raw JSON](day-2/raw/real-space-smoke.json)
- `reward_hacking`: [raw JSON](day-2/raw/reward-hacking.json)
- `reward_timing`: [raw JSON](day-2/raw/reward-timing.json)
- `space_integrity`: [raw JSON](day-2/raw/space-integrity.json)
