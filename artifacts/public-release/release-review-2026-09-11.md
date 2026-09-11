# Public-release evidence refresh — 2026-09-11

This is a release-review record, not a publication decision or milestone verdict. It preserves the
owner's earlier decisions and limits without broadening them. The checked `origin/main` revision is
`695fc4c75149579dde962dd3cf2f817b2c9ffdda` (tree
`ff4b39b7877c8c25e7bcd43b55a62af6300618c7`), from a fresh, non-shallow GitHub clone of all
advertised branch heads and tags. Hidden PR refs were not fetched. Prerequisite PRs #189 and #190
were merged at `5f15e79f2ec27fad2308a1ff87aeed6ad7312054` and
`dde7983b46eecc8ff31aa547881ba7f24bf112e2` respectively.

The [structured record](release-review-2026-09-11.json) contains commands, exit statuses, counts,
timings, output hashes, the ref inventory, the historical fingerprint occurrence table, hosted
window, and remaining owner actions. Raw local command streams and provider journals are not
published.

## Repository findings

Before refresh, links and tracked-tree redaction exited 0, history exited 1 with the retained
findings, and inventory comparison exited 1 with 29 tracked-section differences. Inspection mapped
the drift to 21 added tracked files, one new acknowledged placeholder test vector, ordering shifts,
and file counts. It introduced zero tracked-tree review-required findings. No scanner rule or
allowlist changed. After refresh, the same four commands exited 0, 0, 1, and 0 respectively;
inventory comparison reported zero tracked-section differences, and its informational history
comparison also matched.

All 49 review-required history path records match the latest accepted baseline as an exact multiset
of blob, path, line, classification, fingerprint, and boundary commits. All 19 fingerprint
occurrence counts also match. No new or removed review-required history record was found. The
2026-09-07 owner acceptance remains limited to these exact records and seven ancestor boundaries;
it does not cover hosted content, commit metadata identities, or future disclosures. The history
command remains nonzero so the accepted findings stay visible.

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

The verification commands left no unstaged tracked-file change. The release record and refreshed
inventory were written after the measured candidate scan, so their commit and the PR/CI surfaces are
called out for the bounded post-submission delta rather than being implied to fall inside it.

## Remaining owner actions

The owner must still review the public wording and linked claims, review the exact accepted history
scope, accept or narrow the disclosed privacy limits, and review the bounded post-submission and
final read-only delta results. The final delta must be repeated if hosted state changes again before
publication. If those reviews are satisfactory, the owner may separately change visibility and
then verify the public repository from a logged-out browser. No visibility change, merge, history
rewrite, credential rotation, hosted-record removal, paid model call, or milestone decision was
performed here.
