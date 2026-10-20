# D5.10 historical-verification observations

Verdict: `null`. The project owner retains the D5.10 decision.

Generated from stored JSON only; this renderer does not execute verifiers.

The [original package](../grounding-v5-d510/b0f00f8dc0a5f588e62ce4846cf7ba9fab037f61/REPORT.md) is unchanged. Its exit-one observations remain historical observations. The manifest beside this report binds that package's manifest and every file in this supplement.

## Initial investigation

[investigation.json](investigation.json) preserves the earlier investigation byte for byte, including exact differences, historical source hashes, commands, outputs, reproduction helpers, and setup failures. Its source comparisons refer to the original evidence revision, not a new benchmark execution.

| Original record | Historical revision | Exit | Command seconds |
| --- | --- | ---: | ---: |
| 27 | `09c5b4a19794842c1cf0c0a8a74b1cc7c8f59fb5` | 0 | 0.58189 |
| 42 | `9446b445d392ed29e7643ec6195d0143ffc70330` | 0 | 0.506668 |
| 43 | `1e5d9c0d19acf51505919deefe0d155c2ab22b26` | 0 | 0.420855 |
| 44 | `dec1fa9d0f060ebe789919c50a7c2335e00f0347` | 0 | 0.632088 |
| 46 | `f40157d7f2fc70b5b3d3b659f5cd3168be8e19b3` | 0 | 410.103141 |
| 48 | `ca1c98333cffb5f04beef1d4f56ed09f9840f538` | 0 | 0.508709 |
| 49 | `1201df8a773add79733d392f876b579752f797c5` | 0 | 7.216681 |
| 50 | `b3167ff49576aec4001444ba3166d37cfc8dfda4` | 0 | 6.340972 |

## Entry-point run

[entry-point-run.json](entry-point-run.json) records a separate run through `python -m scripts.verify_v5_at_revision all`. It includes the launcher's file hash, dependency versions, full child-command outputs, timeouts, and artifact-change lists.

The entry point reads tracked artifacts from `b0f00f8dc0a5f588e62ce4846cf7ba9fab037f61` and source from each selected historical revision. It does not verify later working-tree edits. Record 46 runs full admission; it does not use `--skip-admission`.

### Record 27

Original observation: [27-verify-d58-memory-admission.json](../grounding-v5-d510/b0f00f8dc0a5f588e62ce4846cf7ba9fab037f61/commands/27-verify-d58-memory-admission.json), exit `1`.

Source revision: `09c5b4a19794842c1cf0c0a8a74b1cc7c8f59fb5`.

Command: `<path-2>/bin/python -m scripts.prepare_grounding_v5_memory --verify`

Exit: `0`. Command seconds: `0.285539`. Artifact files checked: `1174`.

Verification errors: `[]`. Artifact changes: `[]`.

Full output:

```text
verified artifact hashes, current source binding, and evidence-only report; provider calls: 0
```

### Record 42

Original observation: [42-verify-d58-review-corrections.json](../grounding-v5-d510/b0f00f8dc0a5f588e62ce4846cf7ba9fab037f61/commands/42-verify-d58-review-corrections.json), exit `1`.

Source revision: `9446b445d392ed29e7643ec6195d0143ffc70330`.

Command: `<path-2>/bin/python -m scripts.prepare_grounding_v5_review_corrections --verify`

Exit: `0`. Command seconds: `0.161405`. Artifact files checked: `1174`.

Verification errors: `[]`. Artifact changes: `[]`.

Full output:

```text
Verified corrections from stored evidence; provider calls: 0
```

### Record 43

Original observation: [43-verify-d58-power-report.json](../grounding-v5-d510/b0f00f8dc0a5f588e62ce4846cf7ba9fab037f61/commands/43-verify-d58-power-report.json), exit `1`.

Source revision: `1e5d9c0d19acf51505919deefe0d155c2ab22b26`.

Command: `<path-2>/bin/python -m scripts.prepare_grounding_v5_power_report --verify`

Exit: `0`. Command seconds: `0.151282`. Artifact files checked: `1174`.

Verification errors: `[]`. Artifact changes: `[]`.

Full output:

```text
Verified corrected report from stored evidence; provider calls: 0
```

### Record 44

Original observation: [44-verify-d58-haiku-successor.json](../grounding-v5-d510/b0f00f8dc0a5f588e62ce4846cf7ba9fab037f61/commands/44-verify-d58-haiku-successor.json), exit `1`.

Source revision: `dec1fa9d0f060ebe789919c50a7c2335e00f0347`.

Command: `<path-2>/bin/python -m scripts.prepare_grounding_v5_d58_haiku_successor --verify`

Exit: `0`. Command seconds: `0.19755`. Artifact files checked: `1174`.

Verification errors: `[]`. Artifact changes: `[]`.

Full output:

```text
Verified Haiku D5.8 successor power evidence; provider calls: 0
```

### Record 46

Original observation: [46-verify-d59-freeze.json](../grounding-v5-d510/b0f00f8dc0a5f588e62ce4846cf7ba9fab037f61/commands/46-verify-d59-freeze.json), exit `1`.

Source revision: `f40157d7f2fc70b5b3d3b659f5cd3168be8e19b3`.

Command: `<path-2>/bin/python -m scripts.prepare_grounding_v5_d59_freeze --verify --source-revision f40157d7f2fc70b5b3d3b659f5cd3168be8e19b3`

Exit: `0`. Command seconds: `345.517307`. Artifact files checked: `1174`.

Verification errors: `[]`. Artifact changes: `[]`.

Full output:

```text
{"files": ["admission.json", "execution-plan.json", "task-manifest.json"], "provider_calls_made": 0, "verified": true}
```

### Record 48

Original observation: [48-verify-d59-haiku-freeze.json](../grounding-v5-d510/b0f00f8dc0a5f588e62ce4846cf7ba9fab037f61/commands/48-verify-d59-haiku-freeze.json), exit `1`.

Source revision: `ca1c98333cffb5f04beef1d4f56ed09f9840f538`.

Command: `<path-2>/bin/python -m scripts.prepare_grounding_v5_d59_haiku_freeze --verify --source-revision ca1c98333cffb5f04beef1d4f56ed09f9840f538`

Exit: `0`. Command seconds: `0.216782`. Artifact files checked: `1174`.

Verification errors: `[]`. Artifact changes: `[]`.

Full output:

```text
{"files": ["execution-plan.json", "owner-exception.json"], "provider_calls_made": 0, "verified": true}
```

### Record 49

Original observation: [49-verify-d59-haiku-retry-successor.json](../grounding-v5-d510/b0f00f8dc0a5f588e62ce4846cf7ba9fab037f61/commands/49-verify-d59-haiku-retry-successor.json), exit `1`.

Source revision: `1201df8a773add79733d392f876b579752f797c5`.

Command: `<path-2>/bin/python -m scripts.prepare_grounding_v5_d59_haiku_retry_successor --verify --source-revision 1201df8a773add79733d392f876b579752f797c5`

Exit: `0`. Command seconds: `5.133025`. Artifact files checked: `1174`.

Verification errors: `[]`. Artifact changes: `[]`.

Full output:

```text
{"execution_enabled": false, "execution_plan_digest": "sha256:c123aad69824e2ec352fd1751fafd762e43a0f2697cdd00e4521efe6509d5cf7", "history_policy_id": "policy-6788af9cc24b21e0b89f", "provider_calls_made": 0, "stateless_policy_id": "policy-9ce79eec1338f6426873"}
```

### Record 50

Original observation: [50-verify-d59-haiku-network-retry.json](../grounding-v5-d510/b0f00f8dc0a5f588e62ce4846cf7ba9fab037f61/commands/50-verify-d59-haiku-network-retry.json), exit `1`.

Source revision: `b3167ff49576aec4001444ba3166d37cfc8dfda4`.

Command: `<path-2>/bin/python -m scripts.prepare_grounding_v5_d59_haiku_network_retry --verify --source-revision b3167ff49576aec4001444ba3166d37cfc8dfda4`

Exit: `0`. Command seconds: `5.410268`. Artifact files checked: `1174`.

Verification errors: `[]`. Artifact changes: `[]`.

Full output:

```text
sha256:3f50d15f72244cfc8fd422cd16ab8dc6ab8edd873f6c9a92375472671fdcd092
```

## Limits

These are historical artifact-verification observations. They do not recover the deleted D5.9 private journals, establish an OS-enforced policy boundary, approve public wording, or declare a milestone verdict. The initial investigation and the entry-point run are separate observations; neither replaces the original failures.
