"""OSWorld-V2 backend adapter for the PixelGym environment contract.

The optional dependency is loaded lazily so importing and testing the core
package never imports OSWorld.  Public PixelGym actions are translated only
to OSWorld's structured ``computer_13`` dictionaries; no caller-supplied
Python, shell command, text string, DONE, or browser-navigation action can
cross this adapter.
"""

from __future__ import annotations

import io
import json
import platform
import socket
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import numpy as np
from PIL import Image

from pixelgym.backends.base import Frame
from pixelgym.grounding.v5.contracts import EnvironmentResumeRecord, content_digest, sha256_bytes
from pixelgym.serialization import canonical_json_bytes
from pixelgym.task_spec import Submission
from pixelgym.tasks.vendor_form.osworld_task import APP_URL, create_osworld_task

OSWORLD_REPOSITORY = "https://github.com/xlang-ai/OSWorld-V2"
OSWORLD_TAG = "v2026.06.24"
OSWORLD_COMMIT = "2b9b7b4eb73243d557bdbf2998fe18d8e18e19c6"
OSWORLD_RELEASE = "osworld-v2-2026.06.24"
OSWORLD_RELEASE_MANIFEST_URL = (
    f"{OSWORLD_REPOSITORY}/blob/{OSWORLD_TAG}/benchmark_releases/{OSWORLD_RELEASE}.json"
)

RUNTIME_IMAGE_REPOSITORY = "docker.io/happysixd/osworld-docker"
RUNTIME_IMAGE_DIGEST = "sha256:0e6497a9295647cf05bf2b2af522fdd79bdeba2737595259cab310a3bcf6baa9"
RUNTIME_IMAGE_REFERENCE = f"{RUNTIME_IMAGE_REPOSITORY}@{RUNTIME_IMAGE_DIGEST}"

# The release-named host above is amd64-only. Docker Desktop on Apple Silicon
# otherwise emulates that outer container while it also emulates the x86 guest.
# This digest-pinned, MIT-licensed multi-architecture QEMU-Docker base removes
# the redundant outer emulation layer. The tiny local derivative lives in
# docker/osworld-arm64 and retains the release guest unchanged.
ARM64_RUNTIME_BASE_REPOSITORY = "docker.io/qemux/qemu"
ARM64_RUNTIME_BASE_DIGEST = (
    "sha256:b51ff8a5d69c10e57d3515c7a40dbbd47c410152b1491f849d12ede7607b80be"
)
ARM64_RUNTIME_BASE_REFERENCE = f"{ARM64_RUNTIME_BASE_REPOSITORY}@{ARM64_RUNTIME_BASE_DIGEST}"
ARM64_RUNTIME_SOURCE_COMMIT = "c698406b5a447234379b3fdede03d7f6e4e3fa6c"
ARM64_RUNTIME_IMAGE_REPOSITORY = "docker.io/pixelgym/osworld-qemu-arm64"
ARM64_RUNTIME_IMAGE_TAG = "qemux-6.18"
ARM64_RUNTIME_IMAGE_REFERENCE = f"{ARM64_RUNTIME_IMAGE_REPOSITORY}:{ARM64_RUNTIME_IMAGE_TAG}"

GUEST_ARTIFACT_REPOSITORY = "xlangai/v2-image"
GUEST_ARTIFACT_TAG = OSWORLD_TAG
GUEST_ARTIFACT_NAME = "osworld-v2-ubuntu-x86.qcow2.zip"
GUEST_ARTIFACT_SIZE = 14_189_763_267
GUEST_ARTIFACT_SHA256 = "eb737ae70b49849e24af407de6a518439a23de05a8497096a948334ce0a909aa"

_NAMED_KEY_MAP = {
    "Tab": "tab",
    "Enter": "enter",
    "Backspace": "backspace",
    "ArrowLeft": "left",
    "ArrowRight": "right",
    "ArrowUp": "up",
    "ArrowDown": "down",
}
_MAX_VALID_TCP_PORT = 65_535


def _default_runtime_image_reference() -> str:
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return ARM64_RUNTIME_IMAGE_REFERENCE
    return RUNTIME_IMAGE_REFERENCE


class OSWorldBackendError(RuntimeError):
    """OSWorld failed or returned data outside the PixelGym contract."""


def _docker_published_ports(provider: Any) -> set[int]:
    docker_ports: set[int] = set()
    for container in provider.client.containers.list():
        mappings = container.attrs.get("NetworkSettings", {}).get("Ports") or {}
        for published in mappings.values():
            if published:
                docker_ports.update(int(item["HostPort"]) for item in published)
    return docker_ports


def _can_bind_host_port(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("", port))
        except OSError:
            return False
    return True


def _portable_docker_available_port(
    provider: Any,
    start_port: int,
    *,
    published_port_source: Callable[[Any], set[int]] = _docker_published_ports,
    port_probe: Callable[[int], bool] = _can_bind_host_port,
) -> int:
    """Find an unused host port without macOS's privileged process scan."""

    if not 1 <= start_port <= _MAX_VALID_TCP_PORT:
        raise ValueError(
            f"start_port must be between 1 and {_MAX_VALID_TCP_PORT} inclusive"
        )

    docker_ports = published_port_source(provider)

    for port in range(start_port, _MAX_VALID_TCP_PORT + 1):
        if port in docker_ports:
            continue
        if port_probe(port):
            return port
    raise OSWorldBackendError(f"no available host port found starting at {start_port}")


def _install_darwin_docker_port_allocator() -> None:
    """Install the bounded macOS compatibility shim in this Python process.

    The pinned provider calls ``psutil.net_connections()``, which raises
    ``AccessDenied`` under normal macOS privacy controls before Docker starts.
    Binding a host socket checks the real allocation boundary; Docker's port
    map additionally covers ports published by existing containers.
    """

    if platform.system() != "Darwin":
        return
    from desktop_env.providers.docker.provider import DockerProvider

    if getattr(DockerProvider, "_pixelgym_port_allocator_installed", False):
        return
    DockerProvider._get_available_port = _portable_docker_available_port
    DockerProvider._pixelgym_port_allocator_installed = True


@dataclass(frozen=True)
class OSWorldBackendConfig:
    guest_image_path: Path
    cache_dir: Path = Path(".cache/osworld/pixelgym")
    width: int = 1920
    height: int = 1080
    client_password: str = "osworld-public-evaluation"
    stabilization_timeout: float = 12.0
    stabilization_poll_interval: float = 0.1
    stable_frame_count: int = 2
    noop_seconds: float = 0.1
    guest_volume_size_gb: int = 50
    runtime_image_reference: str = field(default_factory=_default_runtime_image_reference)

    def __post_init__(self) -> None:
        object.__setattr__(self, "guest_image_path", Path(self.guest_image_path))
        object.__setattr__(self, "cache_dir", Path(self.cache_dir))
        if self.width <= 0 or self.height <= 0:
            raise ValueError("OSWorld screen dimensions must be positive")
        if self.stabilization_timeout <= 0:
            raise ValueError("stabilization_timeout must be positive")
        if self.stabilization_poll_interval < 0:
            raise ValueError("stabilization_poll_interval must be non-negative")
        if self.stable_frame_count < 2:
            raise ValueError("stable_frame_count must be at least two")
        if self.noop_seconds < 0:
            raise ValueError("noop_seconds must be non-negative")
        if self.guest_volume_size_gb <= 0:
            raise ValueError("guest_volume_size_gb must be positive")
        if not self.runtime_image_reference:
            raise ValueError("runtime_image_reference must not be empty")


class OSWorldBackend:
    """Backend protocol implementation over one OSWorld ``DesktopEnv``."""

    def __init__(
        self,
        config: OSWorldBackendConfig,
        *,
        desktop_env_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.config = config
        self._desktop_env_factory = desktop_env_factory
        self._env: Any | None = None
        self._task: Any | None = None
        self._task_record: Mapping[str, Any] | None = None
        self._latest_raw_screenshot: bytes | None = None
        self._last_submissions: tuple[Submission, ...] = ()
        self._structured_action_count = 0
        self._closed = False
        self._last_stabilization_seconds: float | None = None

    @property
    def width(self) -> int:
        return self.config.width

    @property
    def height(self) -> int:
        return self.config.height

    @property
    def app_url(self) -> str:
        return APP_URL

    @property
    def last_stabilization_seconds(self) -> float | None:
        return self._last_stabilization_seconds

    def reset(self, seed: int) -> Mapping[str, Any]:
        if type(seed) is not int:
            raise TypeError(f"OSWorldBackend seed must be an int, got {type(seed).__name__}")
        self._closed = False
        self.config.cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            env = self._ensure_env()
            task, record = create_osworld_task(seed, cache_dir=self.config.cache_dir)
            observation = env.reset(task_config=task, seed=seed)
            self._task = task
            self._task_record = record
            self._last_submissions = ()
            self._structured_action_count = 0
            task.read_privileged_state(env)
            self._latest_raw_screenshot = self._raw_from_observation(observation)
            # Validate the public observation path during reset, not later on
            # the first action.  PixelGuiEnv.reset() must receive real pixels.
            self.screenshot()
            return record
        except BaseException:
            self._close_after_failure()
            raise

    def screenshot(self) -> Frame:
        env = self._require_active()
        started = time.monotonic()
        deadline = started + self.config.stabilization_timeout
        previous = (
            self._decode_screenshot(self._latest_raw_screenshot)
            if self._latest_raw_screenshot is not None
            else None
        )
        equal_run = 1 if previous is not None else 0
        last_difference: tuple[int, int] | None = None

        while time.monotonic() <= deadline:
            raw = env.controller.get_screenshot()
            current = self._decode_screenshot(raw)
            self._latest_raw_screenshot = raw
            if previous is not None and np.array_equal(previous, current):
                equal_run += 1
                if equal_run >= self.config.stable_frame_count:
                    self._last_stabilization_seconds = time.monotonic() - started
                    return current.copy()
            else:
                if previous is not None:
                    delta = np.abs(current.astype(np.int16) - previous.astype(np.int16))
                    last_difference = (
                        int(np.count_nonzero(np.any(delta != 0, axis=2))),
                        int(delta.max(initial=0)),
                    )
                equal_run = 1
            previous = current
            if self.config.stabilization_poll_interval:
                time.sleep(self.config.stabilization_poll_interval)

        detail = "unknown"
        if last_difference is not None:
            detail = (
                f"{last_difference[0]} differing pixels, maximum channel delta {last_difference[1]}"
            )
        raise OSWorldBackendError(
            "OSWorld frame did not become bitwise stable within "
            f"{self.config.stabilization_timeout:.1f}s; last comparison: {detail}"
        )

    def noop(self) -> None:
        # DesktopEnv handles WAIT specially and never forwards it to an
        # executable controller action.  ``pause`` is split across its two
        # wait sites, hence half here for the requested total duration.
        self._step({"action_type": "WAIT"}, pause=self.config.noop_seconds / 2)

    def click(self, x: int, y: int) -> None:
        self._step(
            {"action_type": "CLICK", "parameters": {"button": "left", "x": x, "y": y}},
            pause=0,
        )

    def key(self, key: str) -> None:
        if key in _NAMED_KEY_MAP:
            action = {
                "action_type": "PRESS",
                "parameters": {"key": _NAMED_KEY_MAP[key]},
            }
        elif isinstance(key, str) and len(key) == 1 and key.isprintable():
            # TYPING receives exactly one character selected from PixelGym's
            # fixed allowlist.  It is not an arbitrary text action.
            action = {"action_type": "TYPING", "parameters": {"text": key}}
        else:
            raise OSWorldBackendError(f"key {key!r} is not a supported PixelGym key")
        self._step(action, pause=0)

    def read_submissions(self) -> Sequence[Submission]:
        env = self._require_active()
        if self._task is None:
            raise OSWorldBackendError("OSWorld task is not installed")
        self._last_submissions = tuple(self._task.read_submissions(env))
        return list(self._last_submissions)

    @property
    def last_submissions(self) -> tuple[Submission, ...]:
        """Validation-only snapshot from the most recent evaluator read."""

        return self._last_submissions

    @property
    def structured_action_count(self) -> int:
        """Validation-only count of public actions forwarded to OSWorld."""

        return self._structured_action_count

    def read_privileged_state(self) -> dict[str, Any]:
        """Validation-only host evidence; never exposed in environment info."""

        env = self._require_active()
        if self._task is None:
            raise OSWorldBackendError("OSWorld task is not installed")
        return cast(dict[str, Any], self._task.read_privileged_state(env))

    def read_guest_root_disk(self) -> dict[str, int]:
        """Validation-only root-volume evidence; never exposed to the agent."""

        env = self._require_active()
        result = env.controller.run_bash_script(
            "df -B1 --output=size,avail,pcent / | tail -n 1", timeout=15
        )
        if not isinstance(result, dict) or result.get("returncode") != 0:
            raise OSWorldBackendError(f"guest root-volume query failed: {result!r}")
        parts = str(result.get("output", "")).split()
        if len(parts) != 3 or not parts[2].endswith("%"):
            raise OSWorldBackendError(f"guest root-volume query was malformed: {result!r}")
        try:
            return {
                "size_bytes": int(parts[0]),
                "available_bytes": int(parts[1]),
                "used_percent": int(parts[2][:-1]),
            }
        except ValueError as exc:
            raise OSWorldBackendError(f"guest root-volume query was malformed: {result!r}") from exc

    def integration_metadata(self) -> dict[str, Any]:
        return {
            "upstream_repository": OSWORLD_REPOSITORY,
            "upstream_tag": OSWORLD_TAG,
            "upstream_commit": OSWORLD_COMMIT,
            "release": OSWORLD_RELEASE,
            "release_manifest_url": OSWORLD_RELEASE_MANIFEST_URL,
            "provider": "docker-local",
            "python_version": platform.python_version(),
            "screen_size": [self.width, self.height],
            "runtime_image": self.config.runtime_image_reference,
            "runtime_image_base": (
                ARM64_RUNTIME_BASE_REFERENCE
                if self.config.runtime_image_reference == ARM64_RUNTIME_IMAGE_REFERENCE
                else None
            ),
            "runtime_source_commit": (
                ARM64_RUNTIME_SOURCE_COMMIT
                if self.config.runtime_image_reference == ARM64_RUNTIME_IMAGE_REFERENCE
                else None
            ),
            "guest_artifact_repository": GUEST_ARTIFACT_REPOSITORY,
            "guest_artifact_tag": GUEST_ARTIFACT_TAG,
            "guest_artifact_name": GUEST_ARTIFACT_NAME,
            "guest_artifact_sha256": f"sha256:{GUEST_ARTIFACT_SHA256}",
            "guest_image_path": str(self.config.guest_image_path),
            "guest_volume_size_gb": self.config.guest_volume_size_gb,
            "task_bundle_sha256": (self._task.bundle_sha256 if self._task is not None else None),
            "host_port_allocator": (
                "socket-bind-plus-docker-port-map"
                if platform.system() == "Darwin"
                else "upstream-psutil"
            ),
        }

    def checkpoint(self) -> bytes:
        """Seal the live-session reconnect state used by the v5 runner.

        OSWorld does not expose a content-addressed VM snapshot per action, so
        this checkpoint supports only a proven live reconnect to this exact
        session.  A process restart without the live session fails closed.
        """

        if self._task_record is None:
            raise OSWorldBackendError("OSWorld task is not installed")
        screenshot = self.screenshot()
        privileged = self.read_privileged_state()
        return canonical_json_bytes(
            {
                "schema_version": "pixelgym-osworld-live-reconnect-v1",
                "task_id": self._task_record["task_id"],
                "seed": self._task_record["seed"],
                "backend_identity": self._v5_backend_identity(),
                "structured_action_count": self._structured_action_count,
                "screenshot_digest": "sha256:" + sha256_bytes(screenshot.tobytes()),
                "application_state_digest": content_digest(privileged),
            }
        )

    def restore(self, checkpoint: bytes) -> None:
        """Verify reconnect to the exact still-live OSWorld session."""

        try:
            value = json.loads(checkpoint)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OSWorldBackendError("invalid OSWorld reconnect checkpoint") from exc
        if not isinstance(value, dict):
            raise OSWorldBackendError("OSWorld reconnect checkpoint must be an object")
        if value.get("schema_version") != "pixelgym-osworld-live-reconnect-v1":
            raise OSWorldBackendError("unsupported OSWorld reconnect checkpoint schema")
        current = json.loads(self.checkpoint())
        if current != value:
            raise OSWorldBackendError("OSWorld live session cannot prove the sealed state")

    def environment_resume_record(self, *, step_count: int) -> EnvironmentResumeRecord:
        checkpoint = self.checkpoint()
        value = json.loads(checkpoint)
        return EnvironmentResumeRecord(
            task_id=value["task_id"],
            backend_identity=value["backend_identity"],
            step_count=step_count,
            screenshot_digest=value["screenshot_digest"],
            application_state_digest=value["application_state_digest"],
            mechanism="live_reconnect",
            checkpoint_digest="sha256:" + sha256_bytes(checkpoint),
        )

    def verify_resume_record(
        self, resume_record: EnvironmentResumeRecord, *, step_count: int
    ) -> None:
        if self.environment_resume_record(step_count=step_count) != resume_record:
            raise OSWorldBackendError("OSWorld reconnect binding mismatch")

    def _v5_backend_identity(self) -> str:
        if self._task_record is None:
            raise OSWorldBackendError("OSWorld task is not installed")
        return content_digest(
            {
                "runtime_image": self.config.runtime_image_reference,
                "guest_image": str(self.config.guest_image_path.resolve()),
                "task_id": self._task_record["task_id"],
                "seed": self._task_record["seed"],
            }
        )

    def close(self) -> None:
        env, self._env = self._env, None
        self._task = None
        self._task_record = None
        self._latest_raw_screenshot = None
        self._last_submissions = ()
        self._structured_action_count = 0
        self._closed = True
        if env is not None:
            try:
                env.close()
            except Exception as exc:
                raise OSWorldBackendError(f"failed to close OSWorld provider: {exc}") from exc

    def _ensure_env(self) -> Any:
        if self._env is not None:
            return self._env
        if not self.config.guest_image_path.is_file():
            raise OSWorldBackendError(
                f"OSWorld guest image does not exist: {self.config.guest_image_path}"
            )
        factory = self._desktop_env_factory
        if factory is None:
            try:
                _install_darwin_docker_port_allocator()
                from desktop_env.desktop_env import DesktopEnv
            except ImportError as exc:  # pragma: no cover - optional install
                raise OSWorldBackendError(
                    "OSWorld-V2 is not installed; install PixelGym's 'osworld' extra"
                ) from exc
            factory = DesktopEnv
        try:
            self._env = factory(
                provider_name="docker",
                path_to_vm=str(self.config.guest_image_path),
                snapshot_name="init_state",
                action_space="computer_13",
                cache_dir=str(self.config.cache_dir / "desktop-env"),
                screen_size=(self.width, self.height),
                headless=True,
                require_a11y_tree=False,
                require_terminal=False,
                os_type="Ubuntu",
                enable_proxy=False,
                client_password=self.config.client_password,
                force_disable_recording=True,
                volume_size=self.config.guest_volume_size_gb,
            )
        except Exception as exc:
            raise OSWorldBackendError(f"failed to construct OSWorld DesktopEnv: {exc}") from exc
        return self._env

    def _step(self, action: dict[str, Any], *, pause: float) -> None:
        env = self._require_active()
        try:
            result = env.step(action, pause=pause)
        except Exception as exc:
            raise OSWorldBackendError(
                f"OSWorld structured action {action['action_type']} failed: {exc}"
            ) from exc
        if not isinstance(result, tuple) or len(result) != 4:
            raise OSWorldBackendError(
                f"OSWorld step returned an unexpected result shape: {result!r}"
            )
        observation = result[0]
        self._latest_raw_screenshot = self._raw_from_observation(observation)
        self._structured_action_count += 1

    def _require_active(self) -> Any:
        if self._env is None or self._task is None:
            raise OSWorldBackendError("OSWorldBackend has no active task; call reset(seed) first")
        if self._closed:
            raise OSWorldBackendError(
                "OSWorldBackend is closed; call reset(seed) to create a new host"
            )
        return self._env

    @staticmethod
    def _raw_from_observation(observation: Any) -> bytes:
        if not isinstance(observation, Mapping):
            raise OSWorldBackendError("OSWorld reset/step observation is not a mapping")
        raw = observation.get("screenshot")
        if not isinstance(raw, bytes):
            raise OSWorldBackendError(f"OSWorld screenshot must be bytes, got {type(raw).__name__}")
        return raw

    def _decode_screenshot(self, raw: Any) -> Frame:
        if not isinstance(raw, bytes):
            raise OSWorldBackendError(
                f"OSWorld controller screenshot must be bytes, got {type(raw).__name__}"
            )
        try:
            with Image.open(io.BytesIO(raw)) as image:
                frame = np.asarray(image.convert("RGB"), dtype=np.uint8)
        except Exception as exc:
            raise OSWorldBackendError(f"could not decode OSWorld screenshot: {exc}") from exc
        expected = (self.height, self.width, 3)
        if frame.shape != expected:
            raise OSWorldBackendError(
                f"OSWorld screenshot shape {frame.shape} does not match configured {expected}"
            )
        return frame

    def _close_after_failure(self) -> None:
        try:
            self.close()
        except OSWorldBackendError:
            # Preserve the original integration failure while still making a
            # best-effort cleanup attempt.  The provider's own constructor and
            # reset paths also clean up partial containers.
            return
