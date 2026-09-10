# Publication privacy review — issue 186

Prepared for owner review on 2026-09-10. This record does not approve publication, change visibility,
or extend the existing acceptance of historical paths to other surfaces. No actual credential was
established in the reviewed content; unreviewed surfaces remain explicitly unresolved.

## Git history

[History review](history-review.json) retains the exact source revision, fetched refs, command,
exit status, full fingerprint-only scanner output, and disposition. The ordinary full-history
GitHub clone was at `06d695a327b02cb83e22f25cc47407d0b344bff5`. It included the advertised heads
and fetched tag listed in that record, not local-only branches or hidden PR refs.

`python3.12 scripts/inventory_public_release.py --mode history` exited **1** with **49**
operator-path findings and **1** additional credential-shaped fixture finding. The path fingerprints
are a subset of the stored inventory and map to the same **seven ancestor boundary commits**.
The owner's 2026-09-07 acceptance recorded in `plans/public-release-checklist.md` is reused only
within that verified scope. These findings remain visible; the scanner was not changed to suppress
accepted historical paths. The earlier local all-ref scan's extra fixture blob is outside this
ordinary clone's ref scope, not an omitted finding.

The new fingerprint `sha256:9cc60315c6941fa80e3f712444dfb15039e3699777982d92d40a0d8eae4d0f1c`
comes from `test_policy_request_cannot_carry_transport_credentials` in
`tests/unit/platform/test_policy_subprocess.py`. Inspection shows a deliberately literal test value
returned by a temporary policy, and an assertion that the credential-bearing request is rejected
before transport. It is synthetic; rotation is not indicated. Issue 185 adds its exact fingerprint
to the existing test-path-scoped acknowledgments and reruns the final candidate checks. Previously
acknowledged synthetic vectors remain distinct from actual credential findings.

## GitHub-hosted surfaces

[Hosted review](hosted-review.json) records the API procedure, observation window, per-resource
identifiers and content fingerprints. [Path findings](hosted-path-findings.jsonl) retains every
matched fingerprint and its resource occurrence counts; its compact row schema and disposition
legend are in the hosted review. Sensitive matched values and payloads are not reproduced.

The paginated snapshot reviewed text from **187** issue/PR bodies, **318** discussion comments,
**440** PR review comments, **333** review summaries, and **395** available Actions log archives
(**1,730** text members). All **32** available Actions artifacts were downloaded and inspected as
`coverage.xml`; no other artifact member type was returned. Inventories and downloads exited 0.
No releases, release assets, or recognized GitHub attachment links were returned. No token-shaped
or non-synthetic email match was found in the retrieved bodies/comments/logs; artifact scans found
runner source paths and no token shape.

Original snapshot disposition, corrected by the follow-up inspection:

- **22,853 occurrences:** ordinary GitHub-hosted runner paths, including coverage source paths.
  They identify the hosted runtime, not an operator home directory; no removal proposed.
- **13 occurrences:** documentation examples or regular-expression fragments. Retained as examples;
  they are not evidence of a real operator directory.
- **5 occurrences in two discussion comments:** concrete operator-path disclosures, subsequently
  redacted with owner authorization. These hosted surfaces were **not** covered by the
  seven-ancestor acceptance. The original snapshot incorrectly classified the generic example
  in PR 134 as a sixth concrete disclosure; its original fingerprint remains in
  `owner_review_findings` for traceability.

The owner authorized neutral-placeholder replacements on 2026-09-10 in these exact comments:

- [Issue 44 comment 5313139648](https://github.com/elanthus/OSWorldTasks/issues/44#issuecomment-5313139648): four paths in pasted test warnings.
- [PR 134 comment 5532587952](https://github.com/elanthus/OSWorldTasks/pull/134#issuecomment-5532587952): one already generic abbreviated path example, also replaced.
- [PR 161 comment 5565199634](https://github.com/elanthus/OSWorldTasks/pull/161#issuecomment-5565199634): one operator-path prefix in a historical-scan example.

All three current comment bodies were edited and read back to verify the exact replacements
with surrounding technical text preserved; no comments were deleted. A fresh scan of those bodies
found zero private-path or credential-shape matches. The
[follow-up evidence](hosted-redaction-followup.json) records body hashes, timestamps, counts,
and the classification correction. Original snapshot files remain unchanged as historical evidence.
Editing current text cannot prove that historical edits, notifications, caches, or copies have
disappeared.

This is a time-window snapshot, not an atomic repository freeze. New PRs and runs from this release
stack require a release-time recheck. Deleted records, comment edit histories, hidden refs, commit
metadata identities, check annotations/summaries beyond logs, and content behind external links
were not exhaustively inspected. Attachment detection covers recognized GitHub attachment URLs;
zero matches is not proof that no externally linked media exists. There were no API-inaccessible
resources in the returned scope; undiscovered resources remain outside it.

## Visual review

[Media inventory](media-review.json) binds all **1,807** tracked image/media files to SHA-256 values.
Visual inspection covered **27 files**: every frame of the README animation (**113 frames**, viewed
in contact sheets with the final frame also inspected at full resolution), the README accuracy
figure, and **25** platform screenshots viewed in contact sheets. The animation shows the synthetic
vendor form in the historical guest desktop, a loopback address, and the guest clock. The platform
screenshots show scripted-demo identifiers and synthetic metrics. No credential, private account
UI, operator identity, or restricted provider payload was visible at the inspected scale.

The remaining **1,780 files** are inventoried but not visually reviewed. Historical media blobs
are also unreviewed. Contact sheets do not establish full-resolution inspection of every small
text field. These are open publication-review limits, not a claim that all repository media is safe.

The owner chose to leave media unchanged for now after spot checks and a separate retention audit.
No media was deleted, deduplicated, regenerated, or migrated to LFS. This retention decision does
not establish exhaustive visual review or publication approval.

## Publication decisions still required

The established actual-credential count is zero in the inspected scope. That is different from
certifying zero unreviewed actual credentials across every possible publication surface. Keep the
release checklist unticked until the owner disposes of the remaining publication-review limits
for media and other scope gaps, and public wording. The authorized current-comment redactions
are complete; they do not clear historical copies. Recheck the final candidate and current hosted state
before a visibility change. No credential rotation, history rewrite, record deletion, visibility
change, or milestone verdict was performed.
