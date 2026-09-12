# Release inventory and privacy refresh — 2026-09-12

This refresh covers the merged release-readiness work through `d40ecf0904609f9950ba876cc6f44b936ddc40df`.
The generated [inventory](../public-release-inventory.json) is refreshed; the previous dated reviews
remain unchanged. This record does not authorize publication or declare a milestone verdict.
The repository remains private, and the main-push CI change in PR #200 remains open with auto-merge
disabled.

## Repository findings

The [repository record](repository-refresh-2026-09-12.json) identifies the clean, non-shallow GitHub
clone, source tree, advertised origin branches and tags, candidate scanner hash, command results,
and the comparison with the previous inventory. Hidden PR refs and local-only refs were not scanned.
The clean-clone comparison initially reported seven tracked-inventory differences after the README,
evidence-map, demo-media, and script-name changes. The refreshed candidate inventories
72 README links (67 local and 5 external, not fetched),
2,771 tracked files, and 962 text files.
There are no current-tree review-required findings, link failures, or license failures.

All 49 previously accepted historical operator-path records match by stable identity, fingerprint
occurrence count, repository paths, and boundary provenance. The same seven ancestor commits remain.
The history command still exits 1 so these findings remain visible; the owner's earlier acceptance
has not been broadened.

Two newly flagged occurrences at lines 54 and 91 of historical blob
`2328db232ab70c096d4a5fbcee7d499a741a9fea`, in
`tests/integration/test_grounding_v5_curl_wire.py`, are the same synthetic token. The test asserts and
sends that value to a loopback TLS server, replacing the production endpoint and using a generated
local test CA. Independent source review confirmed that context. Only its exact fingerprint,
`sha256:be14d3c75c6571ab2f0474ab8a80c9e4ac8e3700ec2cd202712ee50a07d25a36`, was added to the
existing test-vector acknowledgment. Token patterns and tests-only restrictions are unchanged.
All 16 historical credential-shaped occurrences are now classified as synthetic test vectors.

## Hosted findings and correction

The [hosted record](hosted-refresh-2026-09-12.json) retains current resource identities, fingerprints,
reuse metadata, archive members, and review dispositions. Its reusable baseline is the complete
September 10 fingerprint ledger; September 11's summary-only delta is retained as prior evidence,
not used to infer unchanged resource identities. Downloads began at 04:43:04 UTC; the final inventory
began at 04:47:50 UTC, with targeted redaction verification completed at
04:49:51 UTC on September 12. Per-archive windows and executed scanner hashes distinguish the
first-pass downloads from the final inventory.

| Surface | Current inventory | Reviewed delta or reuse |
|---|---:|---|
| Issue/PR bodies, comments, review comments, and review summaries | 1,360 | 69 new or changed texts; 1,291 matching baseline hashes |
| Actions log archives | 454 | 47 new or changed records; 407 matching run identities |
| Actions artifacts | 72 | 33 new immutable IDs; 39 baseline IDs reused |
| Releases, assets, recognized attachment links | 0 | None returned or detected |

The 80 downloaded archives contained 575 scanned UTF-8 members and no skipped binary or large
members. All requests succeeded. New content contained 4,267 ordinary GitHub runner-path
occurrences and nine local validation-path occurrences in dependency warnings in PR descriptions
#201, #202, and #204. Those paths were introduced by this release-readiness work and have been
replaced with a disclosed redaction marker. Warning text, test counts, exit statuses, and timings
are preserved. Exact before/after body hashes and removed-path fingerprints are retained; all three
saved descriptions were fetched again and verified. This correction covers current bodies only.

After correction there are no unresolved findings in the scanned delta and no established actual
credential. No new hosted token-shape match occurred. The three comments redacted in the earlier
review still match their verified follow-up hashes. These results do not certify undiscovered or
uninspected content as credential-free.

## Verification and limits

The scanner regression target reported **9 passed in 2.69 seconds**. It checks that the exact
synthetic value is acknowledged only in test paths, while altered values and non-test locations
remain flagged in both the current tree and history. Ruff also exited 0 for the changed Python files.
Final candidate scan commands, raw fingerprint-only outputs, timings, and hashes are stored in the
repository record. The inventory checks content classifications and counts; it is not a digest of
every file. Final report content, its commit, and subsequent PR/CI activity necessarily postdate the
measured source snapshot and require a bounded recheck before publication.

The previously accepted privacy limits still apply: 27 media files were visually reviewed, 1,780
were inventoried without visual review, and media retention is unchanged. Historical media, small
text beyond contact-sheet resolution, hidden refs, deleted records, comment/PR edit histories,
commit identities, check annotations and summaries beyond logs, and external linked content were
not exhaustively inspected. Recognized attachments and release assets were inventoried; no content
was fetched because none was returned. No repository visibility change, history rewrite, credential
rotation, paid model call, or new milestone decision was performed.

The owner retains publication approval and any decision to broaden the accepted scope. Repeat the
bounded delta if repository or hosted content changes before that decision.
