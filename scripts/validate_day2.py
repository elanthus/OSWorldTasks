"""Run environment and OSWorld validators and store structured evidence.

This command does not declare the human-owned acceptance verdict. It runs named
automated checks, stores their raw records, and assembles
``artifacts/validation-report.json``. The project owner reads the gate command
output and declares the verdict separately.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pixelgym.backends.fake import FakeBackend
from pixelgym.backends.osworld import OSWorldBackend, OSWorldBackendConfig
from pixelgym.validation.audit import validate_reward_hacking
from pixelgym.validation.reset import validate_resets
from pixelgym.validation.reward import validate_reward_timing
from pixelgym.validation.spaces import validate_space_integrity

DEFAULT_RAW_DIR = Path("artifacts/day-2/raw")
DEFAULT_REPORT_JSON = Path("artifacts/validation-report.json")
DEFAULT_GOLDEN = Path("tests/unit/fixtures/golden_trajectory_seed7.json")
DEFAULT_PREPARATION = Path(".cache/osworld/preparation.json")
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_if_present(path: Path) -> Any | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _assemble(raw_dir: Path, report_json: Path, preparation_path: Path) -> dict[str, Any]:
    sections = {
        "fake_reset": _read_if_present(raw_dir / "fake-reset.json"),
        "real_reset": _read_if_present(raw_dir / "real-reset.json"),
        "reward_timing": _read_if_present(raw_dir / "reward-timing.json"),
        "space_integrity": _read_if_present(raw_dir / "space-integrity.json"),
        "browser_boundary": _read_if_present(raw_dir / "browser-boundary.json"),
        "real_space_smoke": _read_if_present(raw_dir / "real-space-smoke.json"),
        "reward_hacking": _read_if_present(raw_dir / "reward-hacking.json"),
        "real_golden_episode": _read_if_present(raw_dir / "real-golden-episode.json"),
    }
    required = tuple(sections)
    missing = [name for name in required if sections[name] is None]
    failed = [
        name
        for name, section in sections.items()
        if section is not None and not section.get("summary", {}).get("passed", False)
    ]
    status = "INCOMPLETE" if missing else "FAIL" if failed else "PASS"
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "gate_disclaimer": (
            "Automated validation status is not the D2.11 human acceptance verdict."
        ),
        "human_gate": _read_if_present(raw_dir / "human-gate.json"),
        "runtime": {
            "python_version": platform.python_version(),
            "host_system": platform.system(),
            "host_machine": platform.machine(),
            "preparation": _read_if_present(preparation_path),
            "provider_stop_loss": _read_if_present(raw_dir / "provider-stop-loss.json"),
            "utm_provider_diagnostic": _read_if_present(raw_dir / "utm-provider-diagnostic.json"),
            "native_arm64_provider_diagnostic": _read_if_present(
                raw_dir / "native-arm64-provider-diagnostic.json"
            ),
        },
        "automated_validation": {
            "status": status,
            "missing_sections": missing,
            "failed_sections": failed,
            "completed_section_count": len(required) - len(missing),
            "required_section_count": len(required),
        },
        "evidence": sections,
        "reproduction_commands": [
            "python scripts/prepare_osworld_docker.py",
            "python scripts/validate_vendor_form_browser_boundary.py",
            "python scripts/validate_day2.py fake",
            "python scripts/smoke_osworld_reset.py",
            "python scripts/osworld_space_smoke.py",
            "python scripts/osworld_golden_trajectory.py check",
            "python scripts/osworld_golden_trajectory.py record",
            "python scripts/validate_day2.py real-resets",
            "python scripts/validate_day2.py audit",
            "python scripts/validate_day2.py assemble",
            "python scripts/generate_validation_report.py",
        ],
    }
    _write(report_json, report)
    return report


def run_fake(raw_dir: Path, golden: Path, report_json: Path, preparation: Path) -> None:
    reset = validate_resets(
        FakeBackend,
        backend_name="fake",
        seed=7,
        reset_count=10,
        screenshot_dir=raw_dir.parent / "screenshots" / "fake-resets",
    )
    _write(raw_dir / "fake-reset.json", reset)
    reward = validate_reward_timing(golden)
    _write(raw_dir / "reward-timing.json", reward)
    spaces = validate_space_integrity(golden, sampled_action_count=500)
    _write(raw_dir / "space-integrity.json", spaces)
    browser_boundary = _read_if_present(raw_dir / "browser-boundary.json")
    audit = validate_reward_hacking(
        reward,
        spaces,
        browser_boundary=browser_boundary,
        repository_root=REPOSITORY_ROOT,
    )
    _write(raw_dir / "reward-hacking.json", audit)
    report = _assemble(raw_dir, report_json, preparation)
    print(
        json.dumps(
            {
                "fake": {
                    "reset": reset["summary"],
                    "reward": reward["summary"],
                    "spaces": spaces["summary"],
                    "audit": audit["summary"],
                },
                "automated_validation": report["automated_validation"],
            },
            indent=2,
            sort_keys=True,
        )
    )


def run_real_resets(
    raw_dir: Path,
    report_json: Path,
    preparation: Path,
    guest_image: Path,
) -> None:
    config = OSWorldBackendConfig(guest_image_path=guest_image)
    reset = validate_resets(
        lambda: OSWorldBackend(config),
        backend_name="real-osworld-docker",
        seed=7,
        reset_count=5,
        screenshot_dir=raw_dir.parent / "screenshots" / "real-resets",
    )
    _write(raw_dir / "real-reset.json", reset)
    reward = _read_if_present(raw_dir / "reward-timing.json")
    spaces = _read_if_present(raw_dir / "space-integrity.json")
    browser_boundary = _read_if_present(raw_dir / "browser-boundary.json")
    if reward is not None and spaces is not None:
        audit = validate_reward_hacking(
            reward,
            spaces,
            real_reset=reset if reset["summary"]["passed"] else None,
            browser_boundary=browser_boundary,
            repository_root=REPOSITORY_ROOT,
        )
        _write(raw_dir / "reward-hacking.json", audit)
    report = _assemble(raw_dir, report_json, preparation)
    print(
        json.dumps(
            {
                "real_reset": reset["summary"],
                "automated_validation": report["automated_validation"],
            },
            indent=2,
            sort_keys=True,
        )
    )


def run_audit(raw_dir: Path, report_json: Path, preparation: Path) -> None:
    """Refresh the reward-hacking audit strictly from checked-in structured evidence."""
    reward = _read_if_present(raw_dir / "reward-timing.json")
    spaces = _read_if_present(raw_dir / "space-integrity.json")
    browser_boundary = _read_if_present(raw_dir / "browser-boundary.json")
    real_reset = _read_if_present(raw_dir / "real-reset.json")
    missing = [
        name
        for name, value in (
            ("reward-timing.json", reward),
            ("space-integrity.json", spaces),
            ("browser-boundary.json", browser_boundary),
        )
        if value is None
    ]
    if missing:
        raise FileNotFoundError(f"missing stored audit evidence: {', '.join(missing)}")
    audit = validate_reward_hacking(
        reward,
        spaces,
        real_reset=(
            real_reset
            if real_reset is not None and real_reset["summary"].get("passed", False)
            else None
        ),
        browser_boundary=browser_boundary,
        repository_root=REPOSITORY_ROOT,
    )
    _write(raw_dir / "reward-hacking.json", audit)
    report = _assemble(raw_dir, report_json, preparation)
    print(
        json.dumps(
            {
                "audit": audit["summary"],
                "automated_validation": report["automated_validation"],
            },
            indent=2,
            sort_keys=True,
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("fake", "real-resets", "audit", "assemble"))
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--report-json", type=Path, default=DEFAULT_REPORT_JSON)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--preparation", type=Path, default=DEFAULT_PREPARATION)
    parser.add_argument(
        "--guest-image",
        type=Path,
        default=Path(".cache/osworld/osworld-v2-ubuntu-x86.qcow2"),
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "fake":
            run_fake(args.raw_dir, args.golden, args.report_json, args.preparation)
        elif args.command == "real-resets":
            run_real_resets(args.raw_dir, args.report_json, args.preparation, args.guest_image)
        elif args.command == "audit":
            run_audit(args.raw_dir, args.report_json, args.preparation)
        else:
            report = _assemble(args.raw_dir, args.report_json, args.preparation)
            print(json.dumps(report["automated_validation"], indent=2, sort_keys=True))
    except Exception as exc:  # noqa: BLE001 - CLI must report integration/provider failures
        print(f"Day 2 validation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
