# Release-candidate validation — issue 185 follow-up

This evidence covers the candidate containing the status corrections (#187), README restructuring
(#188), and scoped privacy review (#186), plus the exact synthetic-vector acknowledgment (#185).
PRs #189 and #191 are on main, including the README changes originally proposed in #190.
PR #192 merged into a retired stack branch; this follow-up brings its scanner acknowledgment and
refreshed inventory to main. Media remains unchanged. The hosted-redaction follow-up is included.

The [command records](candidate-command-records.json) retain source revision, clean-clone/ref scope,
Python version, commands, exit status, stdout/stderr with disclosed path placeholders and original stream hashes, and elapsed time. Results are measurements,
not a human gate verdict. The evidence-recording commit necessarily follows the measured source
revision; it changes only the recorded outputs and tracked inventory, not the tested implementation.

[Inventory differences](inventory-changes.json) accounts for every tracked-tree difference from the
inventory at `06d695a327b02cb83e22f25cc47407d0b344bff5`. Inventory generation uses `--tracked-only`;
redaction separately includes non-ignored untracked files. Local plans, journals, or evaluation
artifacts from the user's working checkout are not copied into this candidate.

The history scan deliberately retains accepted operator-path findings and a nonzero exit status.
Read the separate [history/privacy review](privacy-review.md) for the matched ancestor acceptance,
completed hosted redactions, media gaps, and owner decisions. Synthetic scanner vectors are not actual
credentials; no rotation is indicated by the verified fixture. This candidate does not approve
publication, change visibility, execute models or a VM, or declare any milestone gate.

## Recorded commands

Source revision: `79ef2a03f40a256d94f080fcc76570898b868453`. Full output with path placeholders is in the linked command records.

| Command | Exit status | Elapsed seconds |
|---|---:|---:|
| `python3.12 -m venv .venv` | 0 | 0.831 |
| `.venv/bin/pip install -e .[dev]` | 0 | 1.809 |
| `.venv/bin/ruff check .` | 0 | 0.617 |
| `.venv/bin/mypy pixelgym` | 0 | 6.559 |
| `.venv/bin/pytest -q -n auto tests/unit` | 0 | 66.827 |
| `.venv/bin/python scripts/golden_trajectory.py check` | 0 | 2.628 |
| `.venv/bin/python scripts/inventory_public_release.py --mode links` | 0 | 0.083 |
| `.venv/bin/python scripts/inventory_public_release.py --mode redaction` | 0 | 2.496 |
| `.venv/bin/python scripts/inventory_public_release.py --mode history` | 1 | 8.374 |
| `.venv/bin/python scripts/inventory_public_release.py --mode check` | 0 | 10.381 |

The fast-suite stdout records `1786 passed, 56 warnings in 61.15s`; subprocess elapsed time
includes startup and teardown. Mypy records 105 source files. Golden replay records 110 actions,
reward zero for the preceding 109 actions, then reward one at the exact valid submission.
Both initial and final tracked/untracked status were empty. No OSWorld extra, VM, browser, or
provider call was used. Dependency installation required network access; validation was local.
