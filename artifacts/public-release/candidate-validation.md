# Release-candidate validation — issue 185 follow-up

PRs #189 and #191 are on main, including the README changes originally proposed in #190.
#192 merged into a retired stack branch; this follow-up brings its exact synthetic-fixture
acknowledgment and refreshed inventory to main. The hosted-redaction follow-up is included.
Media remains unchanged. No model, browser, OSWorld, or VM was used.

The [command records](candidate-command-records.json) retain the measured source revision, tree,
clone/ref scope, commands, full output with disclosed path placeholders, original output hashes,
exit statuses, and timing. This was a fresh non-shallow local Git clone using `--no-local`,
with the candidate branch, origin/main, and explicit provenance tag; no untracked files were copied.
Dependency installation used network access. Validation commands ran locally.
The initial attempt omitted origin/main and failed one provenance-reproduction test (1 failed,
1,785 passed). Adding the required ref resolved it without changing source; the initial output
record hash and diagnosis are retained in the command records.

Measured source revision: `b4668ba4276c8b4251a7dd99293b0a2382820e2c`; base main: `14a55a041b8cfa769fd9eefbca84db7dbc94ae62`.
The evidence-recording commit follows that source and changes only these records. It does not
change the tested implementation. Final preflight validates the resulting branch.

[Inventory differences](inventory-changes.json) accounts for all tracked-section differences
against the inventory on main. Generation uses `--tracked-only`; redaction separately considers
non-ignored untracked files. Both initial and final clean-clone Git status were empty.

| Command | Exit status | Elapsed seconds |
|---|---:|---:|
| `python3.12 -m venv .venv` | 0 | 0.813 |
| `.venv/bin/pip install -e .[dev]` | 0 | 1.838 |
| `.venv/bin/ruff check .` | 0 | 0.042 |
| `.venv/bin/mypy pixelgym` | 0 | 0.232 |
| `.venv/bin/pytest -q -n auto tests/unit` | 0 | 53.234 |
| `.venv/bin/python scripts/golden_trajectory.py check` | 0 | 2.55 |
| `.venv/bin/python scripts/inventory_public_release.py --mode links` | 0 | 0.096 |
| `.venv/bin/python scripts/inventory_public_release.py --mode redaction` | 0 | 2.532 |
| `.venv/bin/python scripts/inventory_public_release.py --mode history` | 1 | 8.505 |
| `.venv/bin/python scripts/inventory_public_release.py --mode check` | 0 | 11.017 |

The test output records `1786 passed, 56 warnings in 52.75s`. Subprocess timing includes startup and teardown.
History retains accepted operator-path findings and exits 1; this is not a clean history verdict.
See the [privacy review](privacy-review.md) for scope, completed current-comment redactions,
historical-copy limits, and remaining media-review gaps. Results are measurements, not a human
gate verdict or publication approval. Recheck a changed final candidate before publication.
