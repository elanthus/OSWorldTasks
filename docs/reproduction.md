# Reproduction guide

Commands and historical timing moved from the README; start with the default setup below.

## Quick reproduction without OSWorld

Python 3.12 is required. The default development setup does not install OSWorld and the fast suite
does not need a VM, browser, network, or provider credentials.

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
run independent tests in parallel; targeting well under a minute under typical load (see the measured
ranges below for observed run-to-run variance). Serial execution remains supported but is slower and
not the parallel timing target. The editable install is sufficient for the fast suite, lint, and
strict static type check of the complete `pixelgym` package.

The public, response-free calibration supplement has a canonical verifier that checks its
snapshot hashes, plan binding, denominators, classifications, spend accounting, and generated
report without reading private journals or making provider calls:

```bash
.venv/bin/python -m scripts.publish_grounding_v5_calibration_supplement --verify
```

Three integration checks are kept outside the fast unit target and run together in the pull-request
`Release integration` job. The first opens a loopback listener to
compare the FastAPI and OSWorld guest HTTP contracts. The second builds and installs the wheel in
temporary directories, then checks packaged application assets, schemas, license material, and
imports outside the source checkout. The third sends a large POST through system curl to a
temporary local TLS server, verifies its certificate, and checks exact body forwarding. These
commands need no OSWorld or provider access; the curl fixture uses the host's curl and OpenSSL tools:

```bash
.venv/bin/pytest -q tests/integration/test_vendor_form_server_contract.py
.venv/bin/pytest -q tests/integration/test_wheel_packaging.py
.venv/bin/pytest -q tests/integration/test_grounding_v5_curl_wire.py
```

Pull-request CI also runs the fast suite with deterministic Hypothesis settings and branch coverage.
The 80% threshold comes from the pre-property-test measurement of 80.337% across the complete
`pixelgym` package (`flows/` and `scripts/` are outside the installable package and out
of coverage scope for the same reason they are out of packaging and mypy scope, not because they are
hard to cover); optional OSWorld, browser, grounding, and platform modules remain included, along
with the project's 12 pre-existing `# pragma: no cover` lines. CI publishes the terminal report in
the job summary and uploads `coverage.xml` as the `fast-suite-coverage` artifact. The measured report
that produced the 80% baseline is checked in at
[`artifacts/ci-coverage-baseline-issue-114.md`](../artifacts/ci-coverage-baseline-issue-114.md). Run the
identical coverage gate locally with:

```bash
.venv/bin/pytest -q -n 4 --dist worksteal --hypothesis-profile=ci \
  --cov=pixelgym --cov-report=term-missing --cov-report=xml --cov-fail-under=80 tests/unit
```

The [CI workflow](../.github/workflows/ci.yml) gives the fast-suite job a **20-minute timeout**;
lint and type checking each have a 10-minute timeout. Hosted coverage runtime includes runner,
setup, instrumentation, and reporting costs and is a different measurement from a local plain
suite. Historical local measurements in the README at revision `06d695a` ranged 54.6–69.5s for
the plain suite and 66.9–82.2s with coverage. These are historical observations, not current timing
guarantees or evidence that a hosted job should finish in a minute. See the
[status source record](../artifacts/public-release/status-sources.json) for the checked configuration,
visibility, and owner-gate sources.

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
the x86-64 guest without KVM; the recorded first expanded reset took 183.23 seconds
([validation evidence](../artifacts/validation-report.json)).
