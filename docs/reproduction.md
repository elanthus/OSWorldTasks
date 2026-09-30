# Reproduction guide

Commands and historical timing moved from the README; start with the default setup below.

## Quick reproduction without OSWorld

Python 3.12 is required. The default development setup does not install OSWorld. The unit suite
does not need a VM, browser, socket, network, external service, or provider credential.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"

.venv/bin/ruff check .
.venv/bin/mypy pixelgym
.venv/bin/pytest -q -n auto tests/unit
.venv/bin/python scripts/golden_trajectory.py check
.venv/bin/python scripts/demo_fake_backend.py --seed 7
```

The documented fast-suite target uses the `pytest-xdist` dependency included in the `dev` extra to
run independent tests in parallel. Serial execution remains supported but is slower. The editable
install is sufficient for the fast suite, lint, and
strict static type check of the complete `pixelgym` package.

The public, response-free calibration supplement has a canonical verifier that checks its
snapshot hashes, plan binding, denominators, classifications, spend accounting, and generated
report without reading private journals or making provider calls:

```bash
.venv/bin/python -m scripts.publish_grounding_v5_calibration_supplement --verify
```

Three integration checks are kept outside the fast unit target and run as explicit steps in the pull-request
`Release integration` job. The first opens a loopback listener to
compare the FastAPI and OSWorld guest HTTP contracts. The second builds and installs the wheel in
temporary directories, then checks packaged application assets, schemas, license material, and
imports outside the source checkout. The third sends a large POST through system curl to a
temporary local TLS server, verifies its certificate, and checks exact body forwarding. These
commands need no OSWorld or provider access; the curl fixture uses the host's curl and OpenSSL tools:

```bash
.venv/bin/pytest -q -m local_http_integration \
  tests/integration/test_vendor_form_server_contract.py
.venv/bin/pytest -q tests/integration/test_wheel_packaging.py
.venv/bin/pytest -q tests/integration/test_grounding_v5_curl_wire.py
```

Pull-request CI also runs the fast suite with deterministic Hypothesis settings and branch coverage.
Real-loopback HTTP contract tests are not included in this coverage command. The branch-protected
`Fast suite` result aggregates the unit-coverage and `Release integration` job results, so either
failure blocks that required context.

The coverage gate threshold is configured in `pyproject.toml`. Coverage is measured across the
complete `pixelgym` package (`flows/` and `scripts/` are outside the installable package and out
of coverage scope for the same reason they are out of packaging and mypy scope, not because they are
hard to cover); optional OSWorld, browser, grounding, and platform modules remain included, along
with the project's 12 pre-existing `# pragma: no cover` lines. CI publishes the terminal report in
the job summary and uploads `coverage.xml` as the `fast-suite-coverage` artifact. Run the
identical coverage gate locally with:

```bash
.venv/bin/pytest -q -n 4 --dist worksteal --hypothesis-profile=ci \
  --cov=pixelgym --cov-report=term-missing --cov-report=xml --cov-fail-under=80 tests/unit
```

CI job timeouts are configured in the [CI workflow](../.github/workflows/ci.yml). The
[revision-pinned status source record](../artifacts/public-release/status-sources.json) captures the
configuration, visibility, and owner-gate sources at its recorded `source_revision`; it is historical
evidence, not an inventory of the current workflow.

## Release-hosted evidence images

The PNGs of the historical `day-2/` and `platform/` records and of the v3, v4, and v5 pilot and
calibration sets are assets of the `evidence-images-v1` GitHub release rather than tracked files.
The canonical v1 grounding images in `artifacts/grounding/` stay in the tree, so the v1 commands
below need no fetch. Before opening pilot or calibration images, or to run the unit tests that
open them rather than skip, fetch and verify them:

```bash
.venv/bin/python scripts/fetch_evidence_images.py
.venv/bin/python scripts/package_evidence_images.py verify --require-images
```

The fetch uses the GitHub CLI (`gh`) and needs network access. It verifies every file against the
checked-in `images.manifest.json` before writing and exits non-zero on any mismatch. The
[artifacts map](../artifacts/README.md#release-hosted-evidence-images) lists the sets.

## Local loopback HTTP contract

The vendor-form server contract group starts the bundled guest HTTP server on an ephemeral IPv4
loopback port and compares it with FastAPI's in-process test client. It makes no external request
and requires no browser, Docker service, OSWorld image, or optional dependency beyond the default
`dev` install. The host must permit binding a local `127.0.0.1` socket:

```bash
.venv/bin/python -m pytest -q -m local_http_integration \
  tests/integration/test_vendor_form_server_contract.py
```

This group is separate so a socket-restricted sandbox can run every unit test without weakening or
skipping the real-loopback assertions. Pull-request CI exercises it through the explicitly named
loopback step in the `Release integration` job; the required `Fast suite` result depends on that job
in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml).

Re-capturing the frozen browser dataset additionally requires Playwright's Chromium binary,
installed once with:

```bash
.venv/bin/python -m playwright install chromium
.venv/bin/python scripts/validate_vendor_form_browser_boundary.py \
  --output artifacts/local/browser-boundary.json
.venv/bin/python scripts/capture_grounding_dataset.py
```

To revalidate the checked-in dataset, candidate records, image hashes, allocation summary, and
known design limitations without launching a browser or rewriting capture assets, run:

```bash
.venv/bin/python scripts/capture_grounding_dataset.py --summary-only
```

To deterministically rebuild the balanced v2 metadata and audit sheets from the checked-in,
target-neutral v1 capture assets without any model calls, run:

```bash
.venv/bin/python scripts/build_grounding_benchmark_v2.py
```

The scripted incomplete-submit demo stays at reward `0.0`. The separate golden trajectory checks
that all preceding rewards are zero, the exact valid submission pays `1.0` once, and the episode
terminates rather than truncates.

## Real OSWorld-V2 integration

The integration is pinned to OSWorld-V2 tag `v2026.06.24` at commit
`2b9b7b4eb73243d557bdbf2998fe18d8e18e19c6`. The historical Day 2 run used Python 3.12.13 and
1920×1080 screenshots. The 2026-09-06 revision used Python 3.12.14 and 1024×768 observations; both
used a digest-pinned native ARM64 QEMU host around the release's unchanged x86-64 guest. Full
provider and image metadata are recorded in the historical
[validation artifact](../artifacts/validation-report.json) and the
[current revision](../artifacts/day-2-rev-2026-09-06-issues-95-101/validation-report.json).
Start with the [default development setup](#quick-reproduction-without-osworld), then install the
optional integration dependency and run:

```bash
.venv/bin/pip install -e ".[osworld]"
.venv/bin/python scripts/prepare_osworld_docker.py
.venv/bin/python scripts/validate_vendor_form_browser_boundary.py \
  --output artifacts/local/browser-boundary.json
.venv/bin/python scripts/smoke_osworld_reset.py
.venv/bin/python scripts/osworld_space_smoke.py
.venv/bin/python scripts/osworld_golden_trajectory.py check
.venv/bin/python scripts/validate_day2.py real-resets
.venv/bin/python scripts/validate_day2.py audit
.venv/bin/python scripts/validate_day2.py assemble
.venv/bin/python scripts/generate_validation_report.py
```

Preparation downloads the release's 14.2 GB compressed guest artifact. Apple Silicon still runs
the x86-64 guest without KVM, so the first expanded reset is slow
([validation evidence](../artifacts/validation-report.json)).
