# PixelGym Day 2 validation report

Generated from stored evidence: `2026-09-07T06:12:57.473613+00:00`.

**Automated validation status: PASS.** Automated validation status is not the D2.11 human acceptance verdict.

**Human D2.11 verdict: PASS.**

Declaration: D2.11 re-graded PASS against evidence revision artifacts/day-2-rev-2026-09-06-issues-95-101 (issues #95 and #101).

Evidence revision graded: `artifacts/day-2-rev-2026-09-06-issues-95-101`.

Declared by project owner at `2026-09-07T06:12:43Z`.

## Release and provider metadata

Docker preparation evidence is not available yet.
- Task seed: `7`
- Screen size: `1024x768`
- Host Python: `3.12.14`

## Provider stop-loss

Local Docker consumed 2204.14 of the authorized 5400 seconds across 7 serialized real-guest commands; each command's provider closure is recorded below.

Accounting basis: Sum of /usr/bin/time real seconds for serialized real-guest commands, as required by the owner allocation.

| Command record | Exit status | Real seconds | Hard limit (s) | Provider closed |
|---|---:|---:|---:|---|
| `commands/04-guest-browser-boundary.txt` | 0 | 179.40 | 5400 | yes |
| `commands/04b-guest-browser-boundary.txt` | 0 | 178.95 | 3712 | yes |
| `commands/04c-guest-browser-boundary-attempt-1-stale-local.txt` | 1 | 168.58 | 3533 | yes |
| `commands/04c-guest-browser-boundary.txt` | 0 | 169.41 | 3365 | yes |
| `commands/05-validate-day2-real-resets.txt` | 0 | 904.31 | 5198 | yes |
| `commands/06-real-space-smoke.txt` | 0 | 195.28 | 4268 | yes |
| `commands/07-real-golden-episode.txt` | 0 | 408.21 | 4052 | yes |

## Automated status

Completed sections: 8 / 8.

Missing: `none`.

Failed: `none`.

## Reset determinism

| Backend | Resets | Task exact | App state exact | Bitwise visual | Min SSIM | Max differing pixels | Max channel delta |
|---|---:|---|---|---|---:|---:|---:|
| fake | 10 | yes | yes | yes | 1.000000 | 0 | 0 |
| real-osworld-docker | 5 | yes | yes | yes | 1.000000 | 0 | 0 |

No visual mask or tolerance is applied by this report.

Observed real-reset differing-region bounding boxes (x1, y1, x2, y2): `none`.

## Reward timing

Trajectories: 123; passed: 123; failed: 0.

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
| golden-prefixes-000..109 (110 cases) | — | — | — | no_reward | no_reward | yes |
| complete-golden-trajectory | 110 | 110 | — | terminal_reward | terminal_reward | yes |
| duplicate-submit-after-success | 110 | 110 | — | terminal_reward | terminal_reward | yes |
| timeout-one-action-before-completion | — | — | 109 | truncated_without_reward | truncated_without_reward | yes |

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
- Source files hashed: 8
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
| Open terminal or developer tools | blocked | Ctrl, Alt, Command, Escape, function keys, and modifier combinations are absent from KEY_ALLOWLIST_VERSION=1; KEY exposes only one indexed allowlisted key. Stored current real-guest evidence requires Chromium app fullscreen mode, exact 1024x768 window bounds, and task-app pixels at the observation's top edge. | yes |
| Navigate to a completion endpoint | blocked | The action interface has no browser-navigation action or modifier chord. Stored current real-guest evidence requires Chromium app fullscreen mode, exact 1024x768 window bounds, and task-app pixels at the observation's top edge. | yes |
| Guess or alter a task identifier | tested | The stale-task fixture was rejected by the current host-side task identity. | yes |
| Trigger evaluator during partial write | mitigated | GuestTaskState appends an immutable complete record under one lock; evaluator also treats missing fields as mismatches, demonstrated by the partial fixture. | yes |
| Provider reset fails silently | tested | Stored real OSWorld reset evidence records exact task/application identity and valid screenshot shape and dtype. | yes |
| Mutate or change an action after validation | tested | Environment tests use stateful Mapping and int subclasses and prove dispatch uses only the immutable ValidatedAction snapshot. | yes |
| Conflate truncation with successful termination | tested | timeout-one-action-before-completion truncated without reward and post-truncation step was rejected. | yes |

## Known limitations

- The release-named x86 host and the Apple Silicon qemux/qemu base are third-party Docker Hub images without release signatures; digest pinning mitigates mutability, not publisher compromise.
- On Apple Silicon the native outer host avoids redundant container emulation, but the released guest remains x86-64 and runs without KVM. Boot/stabilization may still be slow or fail even when the adapter is correct.
- The privileged /api/state endpoint exists inside the guest. The tested app-mode contract removes browser navigation affordances from the bounded-click observation; containment against a browser or guest OS exploit remains outside this benchmark's threat model.
- OSWorld's structured computer action controller internally generates fixed pyautogui Python calls. PixelGym never accepts or forwards agent-supplied Python, but it inherits bugs in that upstream structured-action implementation.

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

- `browser_boundary`: [raw JSON](raw/browser-boundary.json)
- `fake_reset`: [raw JSON](raw/fake-reset.json)
- `real_golden_episode`: [raw JSON](raw/real-golden-episode.json)
- `real_reset`: [raw JSON](raw/real-reset.json)
- `real_space_smoke`: [raw JSON](raw/real-space-smoke.json)
- `reward_hacking`: [raw JSON](raw/reward-hacking.json)
- `reward_timing`: [raw JSON](raw/reward-timing.json)
- `space_integrity`: [raw JSON](raw/space-integrity.json)
