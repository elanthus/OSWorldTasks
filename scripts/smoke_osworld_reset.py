"""Capture one real PixelGuiEnv reset through the local OSWorld Docker host."""

from __future__ import annotations

import argparse
import json
import signal
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

from pixelgym.backends.osworld import OSWorldBackend, OSWorldBackendConfig
from pixelgym.env import PixelGuiEnv
from pixelgym.validation.metrics import privileged_hashes

_MINIMUM_FREE_ROOT_BYTES = 5 * 1024**3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--guest-image",
        type=Path,
        default=Path(".cache/osworld/osworld-v2-ubuntu-x86.qcow2"),
    )
    parser.add_argument(
        "--screenshot",
        type=Path,
        default=Path("artifacts/day-2/first-real-reset.png"),
    )
    parser.add_argument(
        "--evidence",
        type=Path,
        default=Path("artifacts/day-2/raw/first-real-reset.json"),
    )
    parser.add_argument("--stop-loss-seconds", type=int, default=90 * 60)
    args = parser.parse_args(argv)

    if args.stop_loss_seconds <= 0:
        parser.error("--stop-loss-seconds must be positive")

    def stop_loss(_signum, _frame):
        raise TimeoutError(
            f"no completed first reset within {args.stop_loss_seconds} second stop-loss"
        )

    previous_handler = signal.signal(signal.SIGALRM, stop_loss)
    signal.alarm(args.stop_loss_seconds)
    backend = OSWorldBackend(OSWorldBackendConfig(guest_image_path=args.guest_image))
    env = PixelGuiEnv(backend)
    started = time.monotonic()
    evidence = {
        "schema_version": 1,
        "captured_at": datetime.now(UTC).isoformat(),
        "seed": 7,
        "summary": {
            "passed": False,
            "public_reset_returned_pixels": False,
            "task_identity_verified": False,
            "guest_root_has_at_least_5_gib_free": False,
            "provider_closed": False,
        },
    }
    error: Exception | None = None
    try:
        observation, info = env.reset(seed=7)
        elapsed = time.monotonic() - started
        state = backend.read_privileged_state()
        root_disk = backend.read_guest_root_disk()
        args.screenshot.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(observation).save(args.screenshot)
        evidence.update(
            {
                "task_id": info["task_id"],
                **privileged_hashes(state),
                "screenshot_path": str(args.screenshot),
                "screenshot_shape": list(observation.shape),
                "screenshot_dtype": str(observation.dtype),
                "reset_to_stable_frame_seconds": elapsed,
                "backend_metadata": backend.integration_metadata(),
                "guest_root_disk": root_disk,
                "summary": {
                    "passed": False,
                    "public_reset_returned_pixels": True,
                    "task_identity_verified": True,
                    "guest_root_has_at_least_5_gib_free": (
                        root_disk["available_bytes"] >= _MINIMUM_FREE_ROOT_BYTES
                    ),
                    "provider_closed": False,
                },
            }
        )
    except Exception as exc:  # noqa: BLE001 - persist the exact provider/reset blocker
        error = exc
        evidence["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": "".join(traceback.format_exception(exc)),
        }
    finally:
        # Give cleanup its own bounded interval even when the first-reset alarm
        # fired.  OSWorld also cleans a partially constructed provider inside
        # its constructor/reset paths; this outer close remains idempotent.
        signal.alarm(120)
        try:
            env.close()
        except Exception as exc:  # noqa: BLE001 - persist cleanup failure separately
            evidence["cleanup_error"] = {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": "".join(traceback.format_exception(exc)),
            }
            if error is None:
                error = exc
        else:
            evidence["summary"]["provider_closed"] = True
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous_handler)

    evidence["summary"]["passed"] = bool(
        evidence["summary"]["public_reset_returned_pixels"]
        and evidence["summary"]["task_identity_verified"]
        and evidence["summary"]["guest_root_has_at_least_5_gib_free"]
        and evidence["summary"]["provider_closed"]
        and error is None
    )

    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0 if evidence["summary"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
