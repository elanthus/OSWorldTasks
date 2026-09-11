# Public-release evidence refresh — 2026-09-11

This is a release-review record, not a publication decision or milestone verdict. It preserves the
owner's earlier decisions and limits without broadening them. The checked `origin/main` revision is
`d3c13af1b12cf031cde1ab8021f385b804248bd8` (tree
`04f232d367c4a29a135b4589ee58e80da3aaa2f8`), fetched and merged without rewriting the history of
the fresh, non-shallow clone. Hidden PR refs were not fetched. Prerequisite PRs #189 and #190 were
merged at `5f15e79f2ec27fad2308a1ff87aeed6ad7312054` and
`dde7983b46eecc8ff31aa547881ba7f24bf112e2`; PR #197 subsequently merged at
`d3c13af1b12cf031cde1ab8021f385b804248bd8` and is included in this refresh.

The [structured record](release-review-2026-09-11.json) contains commands, exit statuses, counts,
timings, output hashes, the ref inventory, the historical fingerprint occurrence table, hosted
window, and remaining owner actions. Raw local command streams and provider journals are not
published.

## Repository findings

The initial refresh correctly omitted two blobs that were never reachable from GitHub refs, but it
failed to disclose the provenance correction. Blob `848ef0c2…`, reachable only from `refs/stash` in
the earlier working clone, contributed eight acknowledged placeholder-path rows and two
acknowledged synthetic-email rows. Blob `417d8c21…`, reachable only from an agent checkpoint ref,
contributed one acknowledged bearer-token test-vector row. Published history added one placeholder
row and two synthetic-email rows relative to the stale baseline, producing net category changes of
-7 private paths, 0 e-mail addresses, and -1 credential shape. No review-required row was added or
removed.

The scanner now enumerates only `origin` remote-tracking branches and tags, emits that exact scope,
and fails closed when it is unavailable. Local-only stash and agent refs are structurally excluded.
Each history row has a stable identity over category, blob, line, classification, value fingerprint,
and token shape; all observed paths and boundary commits remain separate provenance. This also
corrects the first refresh's unstable one-path map, which re-pathed nine otherwise stable
synthetic-email rows and changed two boundary-commit lists. No allowlist changed.

All 49 review-required history rows match the latest accepted baseline by the stable identity, and
all 19 fingerprint occurrence counts match. The 2026-09-07 owner acceptance remains limited to
those exact records and seven ancestor boundaries; it does not cover hosted content, commit
metadata identities, or future disclosures. The history command remains nonzero so the accepted
findings stay visible.

After incorporating PR #197, the final commands exited 0, 0, 1, and 0 for links, tracked redaction,
history, and comparison. They reported 73 links (70 local), 2,765 files (956 text), zero tracked-tree
review-required findings, 49 unchanged review-required history rows, and zero inventory difference.

## Hosted delta

The initial delta window ran from the latest recorded completion at
2026-09-10 19:42:02 UTC through 2026-09-11 20:26:20 UTC. Exact hashes reused 1,291 unchanged texts;
43 new or changed bodies, comments, review comments, and review summaries were scanned. Matching
run identity/head/update/attempt reused 407 log archives; 19 new or changed log archives were
downloaded. Immutable IDs reused 39 artifacts; 14 new artifact archives were downloaded. The 33
archives contained 116 UTF-8 text members and no non-text member. The delta contained 1,352
ordinary GitHub-hosted runner-path occurrences, no unresolved finding, and no established actual
credential. The three previously redacted current comments still match their verified hashes.

No release, release asset, or recognized GitHub attachment link was returned. Hidden refs, deleted
records, edit histories, commit metadata identities, check annotations and summaries beyond logs,
external linked content, and unchanged historical media remain outside exhaustive coverage. The
earlier owner acceptance of those limits is preserved; it does not relabel uninspected content as
inspected.

PR #198 was then opened at head `84203268b8491f0e99c30ffcf51d96e663a382db`. Its Lint, Type
check, Fast suite, and preflight review jobs completed successfully; the fast suite took 14m45s
and uploaded its coverage artifact. CodeRabbit returned a successful status with a rate-limit note
and no review content. A second hosted delta through 2026-09-11 21:01:41 UTC scanned 49 new or
changed texts, 23 new or changed log archives, 16 new artifacts, and 199 UTF-8 archive members. It
reused 1,291 unchanged text hashes, 407 unchanged run identities, and 39 immutable artifact IDs.
Only 1,786 ordinary GitHub-hosted runner-path occurrences were found; there was no unresolved
finding, established actual credential, collection failure, non-text archive member, release,
release asset, or recognized GitHub attachment link. Raw bodies and archives were not committed.

The evidence-update commit and CI for the resulting PR head necessarily postdate this committed
snapshot. A final read-only delta must cover that terminal head, and must be repeated if the PR or
hosted state changes again before publication.

## Verification

Python 3.12.14 created the documented virtual environment and installed `.[dev]`. Ruff exited 0;
mypy reported no issues in 108 source files; the socket-free unit target reported 1,877 passed and
56 dependency warnings; the canonical response-free evidence verifier checked three policies with
zero provider calls; the relocated loopback HTTP suite reported 10 passed and one dependency
warning; the installed-wheel smoke suite reported 8 passed; and the golden trajectory and fake
backend demo both exited 0. The loopback suite required the unchanged command to be rerun outside a
restricted sandbox that denied local bind. Full command-level timing and hashes are in the
structured record.

After current main and the review corrections were incorporated, a second fresh environment was
created. Its first install attempt exited 1 because the restricted sandbox denied PyPI DNS; the
unchanged command exited 0 with network permission. In that environment Ruff exited 0, mypy found
no issues in 109 source files, 1,885 unit tests passed with 56 dependency warnings, both frozen
evidence verifiers exited 0, and the new combined release-integration command reported 18 passed
with one dependency warning. Pull-request CI now runs that combined loopback-contract and wheel
smoke command as a dedicated `Release integration` job.

The two release-review files existed as drafts and were included in the measured file and text
counts. Measured outputs were then inserted into those drafts and the generated inventory was
mechanically replaced. Those final content edits, their commit, and the PR/CI surfaces therefore
postdate the measured candidate tree and remain called out for the bounded post-submission delta.

## Remaining owner actions

The owner must still review the public wording and linked claims, review the exact accepted history
identities and path provenance, accept or narrow the disclosed privacy limits, and review the
bounded post-submission and final read-only delta results. The final delta must be repeated if
hosted state changes again before publication. If those reviews are satisfactory, the owner may
separately change visibility and then verify the public repository from a logged-out browser. No
visibility change, merge, history rewrite, credential rotation, hosted-record removal, paid model
call, or milestone decision was performed here.
