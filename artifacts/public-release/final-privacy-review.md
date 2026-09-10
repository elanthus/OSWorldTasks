# Final privacy refresh — issue 186

The owner accepted the documented coverage limits on 2026-09-10: “I'll accept the current coverage
limits.” This includes the remaining media and historical/hidden/edit-history review gaps in the
[original review](privacy-review.md). Acceptance does not label uninspected content as inspected,
clear new findings automatically, or authorize a repository visibility change.

The refresh of merged main `81c7acdaba723978904407c5bb42665d3690e1f4` found no new unresolved
findings within the reviewed scope. All 49 review-required historical path records match the prior
accepted records, including their occurrence counts, and map to the same seven ancestor boundary
commits. The current bodies of all three redacted comments still match their verified follow-up
hashes. Three new credential-shaped occurrences in two review comments are quotations of the
already verified synthetic test fixture, not established live credentials.

## Repository scan

[Repository records](final-repository-scan.json) contain the source commit/tree, all fetched refs,
Python version, command arguments, complete fingerprint-only stdout/stderr, exit statuses, output
hashes, and elapsed times. The snapshot was a fresh, non-shallow GitHub clone of advertised branches
and tags. Hidden PR refs remain outside the accepted scope. Initial and final Git status were clean.

| Command | Exit status | Result |
|---|---:|---|
| `python3.12 scripts/inventory_public_release.py --mode links` | 0 | No link failures. |
| `python3.12 scripts/inventory_public_release.py --mode redaction` | 0 | No current-tree review-required findings or license failures. |
| `python3.12 scripts/inventory_public_release.py --mode history` | 1 | The same 49 accepted path records remain visible. All 13 credential-shaped history findings are acknowledged synthetic test vectors. |
| `python3.12 scripts/inventory_public_release.py --mode check` | 0 | No tracked-inventory differences. History comparison remains informational because ref scope differs. |

The historical path comparison uses a multiset of blob OID, repository-relative path, line, and
fingerprint; duplicate occurrences are retained. There are zero new review-required path records.
The previous seven-ancestor acceptance has not been broadened. Synthetic fixture classification
uses the exact fingerprint and verified test-source context; scanner patterns were not changed.

## GitHub-hosted refresh

[Hosted records](final-hosted-review.json) bind the prior snapshot by file hash and completion time,
then record every currently inventoried resource and its review disposition. The refresh window was
2026-09-10 19:41:33–19:42:02 UTC; the baseline completed at 17:16:11 UTC.

| Surface | Current inventory | Refresh |
|---|---:|---|
| Issue/PR bodies | 192 | Across bodies, comments, and review summaries: 20 new or changed texts scanned; 1,273 unchanged body hashes reuse baseline coverage. |
| Issue/PR discussion comments | 328 | Included in the text comparison above. |
| PR review comments | 440 | Included in the text comparison above. |
| PR review summaries | 333 | Included in the text comparison above. |
| Actions log archives | 407 | 12 new archives downloaded; 395 unchanged runs reuse baseline scans. |
| Actions artifacts | 39 | 7 new archives downloaded; 32 immutable IDs reuse prior downloaded-and-scanned coverage. |
| Releases/assets and recognized attachment links | 0 | None returned or detected. External linked content remains outside exhaustive coverage. |

Unchanged logs require both a matching run/head and an update timestamp no later than baseline
completion. The 19 downloaded archives contained 117 text members; none required binary visual
inspection. All inventory/download requests succeeded; no returned resource was inaccessible.
New/changed content contained 889 ordinary GitHub runner-path occurrences and three quotations of
the exact synthetic fixture fingerprint in PR #192 comment 5622871496 and PR #193 comment 5624155685.
The surrounding comments explicitly discuss the fixture and its rejection-before-transport test.
Only fingerprints and resource identifiers are retained; matched values and comment bodies are not
published. No rotation, comment deletion, or additional redaction was indicated by these findings.

## Disposition and remaining limits

No actual credential was established in the reviewed scope. This is different from certifying
zero credentials in every undiscovered or unreviewed surface. The owner accepts the existing limits:
27 media files were visually inspected by the agent, 1,780 were inventoried but not visually reviewed,
and historical media, small text beyond contact-sheet resolution, hidden refs, deleted records,
comment edit histories, commit identities, annotations beyond logs, and external content were not
exhaustively inspected. Media remains unchanged; the owner's spot checks were not converted into
an invented file-by-file review count.

The review deliverable is complete for this recorded snapshot and accepted scope. Publication
approval, visibility changes, and milestone verdicts remain separate owner actions. New or changed
content after this window, including this evidence PR and its CI outputs, requires a delta recheck
before a visibility change. Original review snapshots and their disclosed limitations remain intact.
