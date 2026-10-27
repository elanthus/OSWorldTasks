# S7 successor qualification: local retention record

Projected from stored receipts and verifier outputs. No model was called, no fake send was made,
no activation or publication was performed, and no milestone verdict is declared.

Run `s7-20261026-successor`, qualified source revision `3b2865877aa784cfcb12066fcccc3f617b657ad7`.

| Copy | Files | Bytes | Manifest digest |
| --- | ---: | ---: | --- |
| local archive | 1984 | 388109403 | `sha256:79ec5f1c862b60d78f518e2454ad6f67a008590c712d2f3995cda2979f159595` |
| local restore | 1984 | 388109403 | `sha256:79ec5f1c862b60d78f518e2454ad6f67a008590c712d2f3995cda2979f159595` |
| remote restore (fresh Git/LFS cache) | 1984 | 388109403 | `sha256:79ec5f1c862b60d78f518e2454ad6f67a008590c712d2f3995cda2979f159595` |

Private LFS repository `elanthus/OSWorldTasks-evidence`, pinned commit `03d418ec138319e3dab7c9915f180607c855b64d`,
packed object 17613967 bytes. Budget observed at
2026-10-27T03:02:33.566500+00:00: $0, stop usage True.

| Verifier check | Exit | Fake sends | Cumulative | Report digest | Verification digest | Inspection unchanged |
| --- | ---: | --- | ---: | --- | --- | --- |
| original | 0 | {"qualification": 77} | 227 | `sha256:cb2eab8ba875…` | `sha256:8b7cd9464ebc…` | True |
| local-restore | 0 | {"qualification": 77} | 227 | `sha256:cb2eab8ba875…` | `sha256:8b7cd9464ebc…` | True |
| remote-restore | 0 | {"qualification": 77} | 227 | `sha256:cb2eab8ba875…` | `sha256:8b7cd9464ebc…` | True |

Digests identical across checks: True.

Each check ran the retained serving verifier in an isolated interpreter (`env -i`, `python -I -B`)
against the bundle's own hash-bound source snapshot. It verifies the seal, source binding, and
stored results, replays stored caller actions, regenerates the report from stored results only,
and confirms the inspection left the bundle unchanged.

Limitations:
- original run and local archive share one disk; the verified private LFS copy is the independent recovery path
- the budget observation is the owner's reading of the GitHub billing UI; it does not continuously verify the account setting
- qualification content (report, packages, gate observations) stays private pending separate publication authorization
- the verifier reads stored bytes and replays stored caller actions; it invokes no policy or transport
