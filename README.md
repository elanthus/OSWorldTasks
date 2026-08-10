# PixelGym-OSWorld

A three-day implementation plan for a validated, pixel-only GUI reinforcement-learning environment and grounding benchmark built on OSWorld-V2.

## Three-day plan

- [Day 1 — Environment core](plans/day-1-environment-core.md)
- [Day 2 — OSWorld integration and validation](plans/day-2-osworld-integration-and-validation.md) — **COMPLETE (PASS)**
- [Day 3 — Grounding experiment and portfolio package](plans/day-3-grounding-and-portfolio.md)

The plan assumes one constrained synthetic vendor-onboarding form as the core task. A file-upload variant is optional only after the core quality gates pass; a spreadsheet task is explicitly out of scope for this three-day sprint.

The project owner declared the Day 2 acceptance gate **PASS** and authorized
Day 3 on 2026-08-09 after reviewing the real golden trajectory, reset metrics,
reward timing, space integrity, reward-hacking audit, and residual risks.

## Recommended agent roster

These are roles, not five agents that must run simultaneously. Reuse a role sequentially when practical.

| Role | Thinking | Use for |
|---|---|---|
| Builder agent | Medium | Scaffolding, deterministic app code, fake backend, capture tools, report plumbing |
| Environment architect | High | Gymnasium contract, seeding, evaluator boundary, reward and episode semantics |
| OSWorld integration agent | High | Provider adapter, VM synchronization, real task setup, cleanup |
| Validation red-team agent | High | Determinism interpretation, reward-hacking probes, residual-risk analysis |
| Experiment analyst | High | Leakage review, paired statistics, error taxonomy, cautious interpretation |

Use medium thinking for bounded tasks with explicit tests and high thinking where a subtle error could invalidate the project claim. Each day document contains copy-ready handoff prompts and a human acceptance gate.

## Development

Requires Python 3.12. The core package and fast test suite never require OSWorld.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Fast unit tests (no VM, no network)
pytest tests/unit

# Full fast suite, including the wheel-packaging integration test
pytest tests/

# Format and lint
ruff format .
ruff check .

# Fake-backend demo: reward trace for a scripted interaction (no VM, no network)
python scripts/demo_fake_backend.py
python scripts/demo_fake_backend.py --seed 7 --screenshot artifacts/fake_frame.png

# Golden trajectory: verify the committed fixture and detect drift (writes nothing)
python scripts/golden_trajectory.py check
```

`pip install -e ".[dev]"` is self-contained: a bare `python3.12 -m venv .venv` has no `setuptools`, and the `dev` extra pins `setuptools>=68` so `tests/integration/test_wheel_packaging.py` (which builds a real wheel with `pip wheel --no-build-isolation`) works without any extra manual install.

Install the `osworld` extra (`pip install -e ".[osworld]"`) only for Day 2 integration work.

## Day 3 grounding workflow and providers

The browser-capture command needs the Chromium build pinned by Playwright. This is needed
only to regenerate the checked-in grounding images, not to run the fast suite or analyze
stored predictions.

```bash
python -m playwright install chromium
python scripts/capture_grounding_dataset.py
python scripts/generate_grounding_overlays.py
```

The frozen experiment uses the Codex CLI provider with `gpt-5.4-mini`. It uses the current
Codex login, runs non-interactively in a read-only sandbox, and requires an explicit cap on
new condition calls. Plan-only mode does not invoke a model:

```bash
python scripts/run_grounding_evaluation.py \
  --provider codex --pilot --max-new-calls 20 --plan-only
```

An OpenRouter alternative is available but is not used for the frozen Codex run. Store its
configuration only in the process environment; never put a real key or model setting in a
source file or checked-in `.env` file:

```bash
export OPENROUTER_API_KEY="..."
export OPENROUTER_MODEL="provider/model-id"

python scripts/run_grounding_evaluation.py \
  --provider openrouter --pilot --max-new-calls 20 --plan-only
```

`OPENROUTER_MODEL` must name an image-capable route with structured-output support. The
adapter sends the local PNG as base64, requires strict JSON Schema support from the routed
provider, and does not enable response healing or hidden retries. See OpenRouter's official
[image-input](https://openrouter.ai/docs/guides/overview/multimodal/image-understanding) and
[structured-output](https://openrouter.ai/docs/guides/features/structured-outputs)
documentation.

The deterministic mock exercises the identical parsing, caching, scoring, and immutable
prediction-file path without network or model calls:

```bash
python scripts/run_grounding_evaluation.py \
  --provider mock --full --max-new-calls 200 \
  --output /tmp/pixelgym-mock-predictions.jsonl
```

## Day 2 local Docker host

The integration is pinned to OSWorld-V2 `v2026.06.24` (commit
`2b9b7b4eb73243d557bdbf2998fe18d8e18e19c6`). It uses the Docker runtime
named by that release manifest, but resolves it by immutable digest rather
than the upstream provider's mutable `latest` reference. The matching Ubuntu
QCOW2 is downloaded from the release tag and checked against the release
manifest's size and SHA-256 before extraction.

The trust basis for x86-64 Linux is the [pinned upstream release manifest](https://github.com/xlang-ai/OSWorld-V2/blob/v2026.06.24/benchmark_releases/osworld-v2-2026.06.24.json),
which names `happysixd/osworld-docker`; there is no separately published
OSWorld-organization runtime image in that release. On Apple Silicon that
amd64-only host creates two emulation layers. Preparation instead builds the
small [`docker/osworld-arm64/Dockerfile`](docker/osworld-arm64/Dockerfile)
derivative from the MIT-licensed `qemux/qemu` ARM64 image pinned to digest
`sha256:b51ff8a5d69c10e57d3515c7a40dbbd47c410152b1491f849d12ede7607b80be`
(source revision `c698406b5a447234379b3fdede03d7f6e4e3fa6c`). The derivative only teaches
the host OSWorld's fixed `/System.qcow2` mount path and enables ephemeral QEMU
snapshot mode so the verified guest base remains read-only. The script records
the resolved local image ID and installs the compatibility tag hardcoded by
the pinned OSWorld provider.

```bash
source .venv/bin/activate
pip install -e ".[osworld]"
python scripts/prepare_osworld_docker.py
python scripts/validate_day2.py fake
python scripts/smoke_osworld_reset.py
python scripts/osworld_space_smoke.py
python scripts/osworld_golden_trajectory.py check
python scripts/osworld_golden_trajectory.py record
python scripts/validate_day2.py real-resets
python scripts/validate_day2.py assemble
python scripts/generate_validation_report.py
```

Preparation downloads a 14.2 GB compressed guest artifact into the ignored
`.cache/osworld/` directory. The extracted guest is larger. The runtime image
is `docker.io/happysixd/osworld-docker` pinned to digest
`sha256:0e6497a9295647cf05bf2b2af522fdd79bdeba2737595259cab310a3bcf6baa9`.
The guest archive must match
`sha256:eb737ae70b49849e24af407de6a518439a23de05a8497096a948334ce0a909aa`.

On Apple Silicon, the released guest remains x86-64 and Docker Desktop does
not expose KVM, so the VM still uses software emulation. The native ARM64 host
removes the unnecessary emulation of the outer Docker container; it does not
claim hardware acceleration. PixelGym requests a 50 GB guest volume; OSWorld
expands the root partition in its ephemeral snapshot while leaving the pinned
base image unchanged. The recorded expanded root was 48.5 GiB with 20.4 GiB
free, and the first measured expanded public reset returned stable 1920×1080
pixels in 183.23 seconds. Retain the 90-minute first-screenshot stop-loss on
other machines. `OSWorldBackend.close()` removes only the container it created
and leaves unrelated Docker workloads alone.

An Ubuntu VM in UTM is useful only if it materially improves the available
virtualization path. Before choosing it, check `uname -m`, `/dev/kvm`, memory,
disk, and Docker from inside the VM. An ARM64 Ubuntu guest without usable
nested acceleration still has to run the x86-64 OSWorld runtime and guest by
emulation, so it is not automatically faster than Docker Desktop.

The available running UTM VM was inspected through UTM's read-only
automation interface: UTM 4.7.5 reports QEMU `aarch64`, machine `virt`, 6144
MiB RAM, host hypervisor enabled. It does **not** qualify as the fallback for
this pinned x86-64 OSWorld release, and its QEMU guest agent is not installed,
so no Docker installation is needed in that VM for this project. The working
local provider is the native ARM64 Docker host above. Stored UTM evidence is
[`artifacts/day-2/raw/utm-provider-diagnostic.json`](artifacts/day-2/raw/utm-provider-diagnostic.json).

For any different candidate UTM VM or Linux host, collect this diagnostic
before changing providers:

```bash
uname -m
test -c /dev/kvm && ls -l /dev/kvm || echo "no /dev/kvm"
free -h
df -h /
docker version
docker info --format '{{.OSType}}/{{.Architecture}}'
```

Proceed with UTM only when the first command prints `x86_64` and `/dev/kvm`
exists. Otherwise the UTM guest would repeat the same x86-on-ARM software
emulation that blocked local Docker; use a remote x86-64 Linux host with KVM
instead. The recorded local diagnosis is
[`artifacts/day-2/raw/provider-stop-loss.json`](artifacts/day-2/raw/provider-stop-loss.json).

For a qualifying Ubuntu UTM guest, allocate at least 8 GB RAM, 4 vCPUs, and
60 GB of free disk, then install the host prerequisites. Docker's current
[official Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/)
use its signed apt repository:

```bash
sudo apt update
sudo apt install -y ca-certificates curl git python3.12 python3.12-venv
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER"
```

Log out of Ubuntu and back in so the Docker group membership takes effect,
then verify `docker run --rm hello-world` and rerun the diagnostic block above.
Copy the committed Day 2 checkout and evidence from the Mac so the UTM run
uses the exact adapter under review. From a Mac terminal, replace
`UTM_USER` and `UTM_IP`:

```bash
rsync -a --exclude .venv --exclude .cache/osworld \
  ./ UTM_USER@UTM_IP:~/OSWorldTasks/
```

Inside Ubuntu, prepare and run the validation in the copied repository:

```bash
cd ~/OSWorldTasks
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,osworld]"
python scripts/prepare_osworld_docker.py
python scripts/smoke_osworld_reset.py
```

Stop at the smoke command if it does not write
`artifacts/day-2/first-real-reset.png`. If it succeeds, continue with the
remaining Day 2 commands listed above. Preparation must run inside Ubuntu so
the stored engine architecture and provider metadata describe the actual host.

The useful fallback is an x86-64 Ubuntu host with working `/dev/kvm`, at
least 8 GB RAM, and at least 60 GB free disk. On that host, install Docker
Engine from Docker's Ubuntu repository, add the login user to the `docker`
group, log out and back in, clone this repository, then run the same commands
above. Do not copy `.cache/osworld/preparation.json` from macOS: preparation
must run on the selected host so its engine and image metadata are truthful.

The generated automated-validation status alone is not the human Day 2
acceptance verdict. For the stored run, the project owner completed the D2.5
visual review and declared D2.11 PASS; the declaration is stored in
[`artifacts/day-2/raw/human-gate.json`](artifacts/day-2/raw/human-gate.json).

## The golden trajectory

`tests/unit/fixtures/golden_trajectory_seed7.json` is 109 literal `NOOP`/`CLICK`/`KEY` actions that solve the seed-7 vendor-onboarding task, plus the `(step, action_type, reward, terminated, truncated)` timeline they must produce. It is replayed **blind** — `tests/unit/test_golden_trajectory.py` never reads the task generator, the widget layout, or the backend's expected values, so the reward timeline it observes cannot be an artifact of the test knowing the answer.

The recorded timeline is checked two ways, and both are load-bearing: replay must equal what the fixture records (drift), *and* what the fixture records must independently be zeros-then-exactly-one (correctness). Only the first would turn the fixture into a snapshot of whatever the code currently does.

Evidence generated from the fixture lives in [artifacts/day-1/](artifacts/day-1/) — structured JSON, a human-readable trace, and the final frame. The Markdown trace is generated from the JSON, never maintained by hand.

Regenerating is deliberately a four-step workflow. `generate` refuses to write the committed fixture in place: a layout or generator bug that silently rewrote the golden oracle would make the whole suite pass by moving the goalposts.

```bash
python scripts/golden_trajectory.py generate --seed 7 --output /tmp/golden_trajectory_seed7.json
```

```bash
python scripts/golden_trajectory.py verify --fixture /tmp/golden_trajectory_seed7.json
```

```bash
diff -u tests/unit/fixtures/golden_trajectory_seed7.json /tmp/golden_trajectory_seed7.json
```

Accept the diff only if you can name its cause — a task-spec change, a layout change, a screen-size change, or an action-contract change. An unexplained coordinate or keystroke change should block acceptance. Then copy the candidate over the fixture, rerun `check`, and regenerate the artifacts:

```bash
python scripts/golden_trajectory.py artifacts --fixture tests/unit/fixtures/golden_trajectory_seed7.json --output artifacts/day-1
```

## Day 1 acceptance gate

The commands below produce the evidence for the D1.8 checklist in [plans/day-1-environment-core.md](plans/day-1-environment-core.md). Running them is the gate; **reading the raw output and declaring pass or fail is the human's call**, not the tooling's.

```bash
python3.12 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"
```

```bash
pytest tests/ -q
```

```bash
python scripts/golden_trajectory.py check
```

```bash
python scripts/demo_fake_backend.py --seed 7
```

| Checklist item | Where the evidence comes from |
|---|---|
| Package installs without OSWorld | the `pip install -e ".[dev]"` above; the `osworld` extra is not installed |
| Task app is deterministic for a fixed seed | `tests/unit/test_vendor_form_generator.py`, `tests/unit/test_vendor_form_app.py` |
| Screenshot is the only observation | `tests/unit/test_env.py`, `pixelgym/env.py` (`info` carries `task_id` only) |
| Action space is bounded clicks and allowlisted keys | `tests/unit/test_actions.py` |
| Modern five-value step result | Gymnasium `check_env`, run inside `tests/unit/test_env.py` |
| Reward fires exactly once, on valid submission | `golden_trajectory.py check`, `tests/unit/test_golden_trajectory.py` |
| Termination and truncation are distinct | `tests/unit/test_env.py` |
| Fast tests pass without network or VM | `pytest tests/` |
| Golden trajectory exists as a public-action fixture | `tests/unit/fixtures/golden_trajectory_seed7.json`, [artifacts/day-1/](artifacts/day-1/) |
