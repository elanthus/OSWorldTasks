# Public release checklist

Status: owner review required. This checklist prepares a visibility change; it does not approve
README claims, declare a milestone gate, or change repository visibility.

Use this checklist from a clean clone with Python 3.12. The repository owner owns every approval
and visibility action below. Leave an item unticked until the owner has inspected its supporting
output directly.

## Public claim review

- [ ] Approve or edit each of the three README claims after opening its linked structured evidence.
- [ ] Confirm that each claim still names its workload, model or no-model condition, 1024×768 or
  historical 1920×1080 screen scope, and provider/host limits.
- [ ] Confirm that semantic determinism, bitwise visual determinism, and perceptual visual
  stability remain distinct. The historical run is semantic plus perceptual evidence; the
  2026-09-06 revision is bitwise evidence from one host.
- [ ] Inspect the README animation and other media for credentials, private UI, and identifying
  information before approving them for a public repository.
- [ ] Confirm the frozen v1 target/screen aliasing, moving model alias, synthetic platform metrics,
  optional OSWorld requirements, D2.11 re-grade, D4.12 verdict, and incomplete V5 work remain
  disclosed.
- [ ] Approve the README wording as a public claim. Merging the documentation PR records this
  wording approval; an automated check cannot supply it.

## Clean-clone inventory and license review

- [ ] Create the documented environment with `python3.12 -m venv .venv` and
  `.venv/bin/pip install -e ".[dev]"`.
- [ ] Run `.venv/bin/python scripts/inventory_public_release.py --mode links` and inspect the raw
  JSON. Every local README target must exist and be tracked; external links are inventoried but are
  not fetched by this offline check.
- [ ] Run `.venv/bin/python scripts/inventory_public_release.py --mode redaction` and inspect every
  category, including acknowledged synthetic test vectors and structured model outputs. This mode
  intentionally includes non-ignored, untracked working-tree files.
- [ ] Run `.venv/bin/python scripts/inventory_public_release.py --mode history` and inspect every
  fingerprinted finding from blobs reachable through all Git refs. Do not paste matched values into
  an issue, pull request, or release record.
- [ ] Run `.venv/bin/python scripts/inventory_public_release.py --mode check`. This rebuilds the
  inventory from tracked files only and compares it with
  `artifacts/public-release-inventory.json`; investigate any reported difference before release.
- [ ] Confirm no ignored/private evidence, restricted attempt journal, gated OSWorld task asset,
  raw private provider payload, operator path, personal e-mail address, credential, or token is
  tracked or linked from the README.
- [ ] Confirm `LICENSE`, `NOTICE`, and every bundled DejaVu `LICENSE-DejaVu.txt` accompany the clean
  clone and the built distribution.

## Verification and human gates

- [ ] Run `.venv/bin/ruff check .` and retain its raw exit status and output.
- [ ] Run `.venv/bin/mypy pixelgym` and retain its raw exit status and output.
- [ ] Run `.venv/bin/pytest -q -n auto tests/unit` and retain its raw counts and runtime.
- [ ] Review the pending D2.11 re-grade evidence. Record any new human verdict separately; do not
  infer it from automated status.
- [ ] Review the stored D4.12 raw evidence. Record any human milestone verdict separately; this
  release checklist does not declare one.
- [ ] Confirm the 2026-09-07 owner decision to accept the Git history without rewriting the seven
  ancestor commits whose earlier file versions retain fingerprinted absolute operator paths. Review
  the raw history inventory before ticking this item; the owner, not an automated check, records
  acceptance of those findings for publication.
- [ ] Change the GitHub repository visibility from private to public.
- [ ] From a logged-out browser, verify the README, local artifact links, license files, and release
  media are accessible in the public repository.
- [ ] Revoke or rotate any credential if the final Git history review discovers that it was ever
  committed; removing it from the current tree is not sufficient.
