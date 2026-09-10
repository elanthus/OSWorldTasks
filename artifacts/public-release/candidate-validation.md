# Stacked release-candidate validation — issue 185

This evidence covers the candidate containing the status corrections (#187), README restructuring
(#188), and scoped privacy review (#186), plus the exact synthetic-vector acknowledgment (#185).
These changes are proposed in dependent review-ready PRs; they are not yet merged into main.
The owner must review and merge the stack in that order, then recheck any changed final candidate.

The [command records](candidate-command-records.json) retain source revision, clean-clone/ref scope,
Python version, commands, exit status, raw stdout/stderr, and elapsed time. Results are measurements,
not a human gate verdict. The evidence-recording commit necessarily follows the measured source
revision; it changes only the recorded outputs and tracked inventory, not the tested implementation.

[Inventory differences](inventory-changes.json) accounts for every tracked-tree difference from the
inventory at `06d695a327b02cb83e22f25cc47407d0b344bff5`. Inventory generation uses `--tracked-only`;
redaction separately includes non-ignored untracked files. Local plans, journals, or evaluation
artifacts from the user's working checkout are not copied into this candidate.

The history scan deliberately retains accepted operator-path findings and a nonzero exit status.
Read the separate [history/privacy review](privacy-review.md) for the matched ancestor acceptance,
new hosted disclosures, media gaps, and owner decisions. Synthetic scanner vectors are not actual
credentials; no rotation is indicated by the verified fixture. This candidate does not approve
publication, change visibility, execute models or a VM, or declare any milestone gate.
