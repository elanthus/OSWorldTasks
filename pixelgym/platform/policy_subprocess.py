"""Credential-free subprocess boundary for stateful serving policy code.

The serving process retains the provider transport, credentials, attempt journal, and session
store.  Only the frozen policy protocol crosses this boundary, encoded as canonical JSON with
opaque byte strings represented as strict base64.  Production launchers must attest that the
worker is running under OS sandbox enforcement; the local launcher hook exists only so the fast
suite can exercise the RPC contract without depending on a host operating system.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import platform
import re
import select
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Protocol, cast
from urllib.parse import urlsplit

from pixelgym.grounding.v5.contracts import sha256_bytes
from pixelgym.grounding.v5.evidence import validate_credential_free
from pixelgym.grounding.v5.runner import PolicyVisibleResult
from pixelgym.platform.fingerprints import canonical_json_bytes

POLICY_WORKER_PROTOCOL_VERSION = "pixelgym-serving-policy-worker-v1"
DARWIN_SERVING_PROFILE_VERSION = "pixelgym-serving-policy-sandbox-v1"
MAX_POLICY_RPC_BYTES = 16 * 1024 * 1024
_IMPORT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_FIXED_WORKER_ENVIRONMENT = {
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/bin:/bin",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONIOENCODING": "utf-8",
}


class PolicySubprocessError(RuntimeError):
    """Base class for closed, stable policy-process failures."""


class PolicySubprocessUnavailableError(PolicySubprocessError):
    pass


class PolicySubprocessProtocolError(PolicySubprocessError):
    pass


@dataclass(frozen=True)
class PolicyWorkerSpec:
    """Exact worker factory and filesystem/network authority for one deployment."""

    factory_module: str
    factory_name: str
    factory_kwargs_json: bytes
    import_roots: tuple[Path, ...]
    provider_endpoint: str
    protected_paths: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        if not _IMPORT_NAME.fullmatch(self.factory_module):
            raise ValueError("policy worker factory module is malformed")
        if not _IMPORT_NAME.fullmatch(self.factory_name):
            raise ValueError("policy worker factory name is malformed")
        if not self.import_roots:
            raise ValueError("policy worker requires at least one import root")
        for root in self.import_roots:
            if not root.is_absolute() or not root.is_dir():
                raise ValueError("policy worker import roots must be existing absolute directories")
        for path in self.protected_paths:
            if not path.is_absolute():
                raise ValueError("policy worker protected paths must be absolute")
        value = _decode_canonical_object(self.factory_kwargs_json, "factory kwargs")
        validate_credential_free(value, field_class="policy_factory_kwargs")
        _provider_authority(self.provider_endpoint)

    @classmethod
    def build(
        cls,
        *,
        factory_module: str,
        factory_name: str,
        factory_kwargs: Mapping[str, Any],
        import_roots: Sequence[Path],
        provider_endpoint: str,
        protected_paths: Sequence[Path] = (),
    ) -> PolicyWorkerSpec:
        validate_credential_free(dict(factory_kwargs), field_class="policy_factory_kwargs")
        return cls(
            factory_module=factory_module,
            factory_name=factory_name,
            factory_kwargs_json=canonical_json_bytes(dict(factory_kwargs)),
            import_roots=tuple(_deduplicated_resolved(import_roots)),
            provider_endpoint=provider_endpoint,
            protected_paths=tuple(_deduplicated_resolved(protected_paths)),
        )

    def factory_payload(self) -> dict[str, Any]:
        return {
            "factory_module": self.factory_module,
            "factory_name": self.factory_name,
            "factory_kwargs": _decode_canonical_object(
                self.factory_kwargs_json, "factory kwargs"
            ),
        }


@dataclass(frozen=True)
class LaunchedPolicyWorker:
    process: subprocess.Popen[bytes]
    mechanism: str
    profile_digest: str | None
    os_sandbox_applied: bool


class PolicyWorkerLauncher(Protocol):
    def launch(self, *, spec: PolicyWorkerSpec, workspace: Path) -> LaunchedPolicyWorker: ...


def _deduplicated_resolved(paths: Sequence[Path]) -> tuple[Path, ...]:
    values: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved not in values:
            values.append(resolved)
    return tuple(values)


def _escaped_sbpl(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _provider_authority(endpoint: str) -> tuple[str, int]:
    parts = urlsplit(endpoint)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
    ):
        raise ValueError("policy provider endpoint must be an HTTP(S) origin without credentials")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    host = parts.hostname
    if not re.fullmatch(r"[A-Za-z0-9.-]+", host):
        raise ValueError("policy provider endpoint hostname is unsupported")
    return host, port


def darwin_serving_profile(
    *,
    provider_endpoint: str,
    runtime_root: Path,
    runtime_executable: Path,
    worker_path: Path,
    workspace: Path,
    import_roots: Sequence[Path],
    protected_paths: Sequence[Path],
) -> str:
    """Return the deny-by-default SBPL profile used by a serving policy worker."""

    host, port = _provider_authority(provider_endpoint)
    if host not in {"127.0.0.1", "localhost"}:
        raise PolicySubprocessUnavailableError(
            "Darwin sandbox-exec cannot enforce an exact non-loopback provider endpoint"
        )
    sandbox_host = "localhost"
    lines = [
        f";; {DARWIN_SERVING_PROFILE_VERSION}",
        "(version 1)",
        "(deny default)",
        '(import "system.sb")',
        "(allow process-info*)",
        (
            '(allow process-exec (literal "'
            + _escaped_sbpl(str(runtime_executable.resolve()))
            + '"))'
        ),
        (
            '(allow file-read* file-map-executable (subpath "'
            + _escaped_sbpl(str(runtime_root.resolve()))
            + '"))'
        ),
        '(allow file-read* (literal "'
        + _escaped_sbpl(str(worker_path.resolve()))
        + '"))',
    ]
    for root in _deduplicated_resolved(import_roots):
        lines.append(
            '(allow file-read* file-map-executable (subpath "'
            + _escaped_sbpl(str(root))
            + '"))'
        )
    lines.extend(
        (
            '(allow file-read* file-write* (subpath "'
            + _escaped_sbpl(str(workspace.resolve()))
            + '"))',
            "(deny network*)",
            f'(allow network-outbound (remote ip "{sandbox_host}:{port}"))',
            "(deny network-bind)",
        )
    )
    for path in _deduplicated_resolved(protected_paths):
        lines.append(
            '(deny file-read* file-write* (subpath "'
            + _escaped_sbpl(str(path))
            + '"))'
        )
    return "\n".join(lines)


def _resolve_darwin_runtime(python_executable: Path) -> tuple[Path, Path]:
    discovery = subprocess.run(
        [str(python_executable), "-I", "-B", "-c", "import sys; print(sys.executable)"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if discovery.returncode != 0:
        raise PolicySubprocessUnavailableError("policy runtime could not be resolved")
    resolved = Path(discovery.stdout.strip()).resolve()
    if not resolved.is_file():
        raise PolicySubprocessUnavailableError("policy runtime executable is unavailable")
    framework_root = next(
        (parent for parent in resolved.parents if parent.name.endswith(".framework")), None
    )
    if framework_root is None:
        return resolved, resolved.parent.parent
    version_root = next(
        (parent for parent in resolved.parents if parent.parent.name == "Versions"), None
    )
    if version_root is None:
        raise PolicySubprocessUnavailableError("policy framework runtime is unavailable")
    executable = version_root / "Resources/Python.app/Contents/MacOS/Python"
    if not executable.is_file():
        raise PolicySubprocessUnavailableError("policy framework executable is unavailable")
    return executable, framework_root


def policy_worker_command(
    *, runtime_executable: Path, worker_path: Path, import_roots: Sequence[Path]
) -> list[str]:
    command = [str(runtime_executable), "-I", "-B", str(worker_path.resolve())]
    for root in _deduplicated_resolved(import_roots):
        command.extend(("--import-root", str(root)))
    return command


def _bundled_worker_path() -> Path:
    """Resolve only the standalone sibling entrypoint, never an importable app module."""

    module_directory = Path(__file__).resolve().parent
    worker_path = module_directory / "policy_worker.py"
    if not worker_path.is_file() or worker_path.resolve().parent != module_directory:
        raise PolicySubprocessUnavailableError("policy worker entrypoint is unavailable")
    return worker_path.resolve()


class DarwinPolicyWorkerLauncher:
    """Launch a policy worker under a content-observable Darwin sandbox profile."""

    def __init__(self, *, python_executable: Path | None = None) -> None:
        self.python_executable = (python_executable or Path(sys.executable)).resolve()

    def launch(self, *, spec: PolicyWorkerSpec, workspace: Path) -> LaunchedPolicyWorker:
        if platform.system() != "Darwin" or not Path("/usr/bin/sandbox-exec").is_file():
            raise PolicySubprocessUnavailableError(
                "stateful policy serving requires Darwin sandbox-exec"
            )
        runtime_executable, runtime_root = _resolve_darwin_runtime(self.python_executable)
        worker_path = _bundled_worker_path()
        profile = darwin_serving_profile(
            provider_endpoint=spec.provider_endpoint,
            runtime_root=runtime_root,
            runtime_executable=runtime_executable,
            worker_path=worker_path,
            workspace=workspace,
            import_roots=spec.import_roots,
            protected_paths=spec.protected_paths,
        )
        command = [
            "/usr/bin/sandbox-exec",
            "-p",
            profile,
            *policy_worker_command(
                runtime_executable=runtime_executable,
                worker_path=worker_path,
                import_roots=spec.import_roots,
            ),
        ]
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=workspace,
            env={**_FIXED_WORKER_ENVIRONMENT, "TMPDIR": str(workspace)},
            start_new_session=True,
        )
        return LaunchedPolicyWorker(
            process=process,
            mechanism="darwin_sandbox_exec",
            profile_digest="sha256:" + sha256_bytes(profile.encode("utf-8")),
            os_sandbox_applied=True,
        )


def _read_response_line(stdout: BinaryIO, *, timeout_seconds: float) -> bytes:
    """Read exactly one bounded line without letting a partial write defeat the deadline."""

    deadline = time.monotonic() + timeout_seconds
    response = bytearray()
    descriptor = stdout.fileno()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PolicySubprocessUnavailableError("policy subprocess timed out")
        ready, _, _ = select.select([descriptor], [], [], remaining)
        if not ready:
            raise PolicySubprocessUnavailableError("policy subprocess timed out")
        chunk = os.read(descriptor, min(64 * 1024, MAX_POLICY_RPC_BYTES + 1 - len(response)))
        if not chunk:
            raise PolicySubprocessProtocolError("policy RPC response is unavailable")
        newline = chunk.find(b"\n")
        if newline >= 0:
            response.extend(chunk[:newline])
            if newline != len(chunk) - 1:
                raise PolicySubprocessProtocolError(
                    "policy RPC response contains trailing output"
                )
            return bytes(response)
        response.extend(chunk)
        if len(response) >= MAX_POLICY_RPC_BYTES:
            raise PolicySubprocessProtocolError("policy RPC response exceeds the byte limit")


class SandboxedPolicyProcess:
    """Implement the frozen stateful-policy protocol over one serialized worker."""

    def __init__(
        self,
        *,
        spec: PolicyWorkerSpec,
        launcher: PolicyWorkerLauncher | None = None,
        request_timeout_seconds: float = 10.0,
        require_os_sandbox: bool = True,
    ) -> None:
        if request_timeout_seconds <= 0:
            raise ValueError("policy subprocess timeout must be positive")
        self.spec = spec
        self.request_timeout_seconds = request_timeout_seconds
        self._workspace = tempfile.TemporaryDirectory(prefix="pixelgym-serving-policy-")
        self._lock = threading.RLock()
        self._next_request_id = 0
        self._closed = False
        selected_launcher = launcher or DarwinPolicyWorkerLauncher()
        try:
            launched = selected_launcher.launch(
                spec=spec, workspace=Path(self._workspace.name)
            )
            self._launched = launched
            if require_os_sandbox and not launched.os_sandbox_applied:
                raise PolicySubprocessUnavailableError(
                    "policy worker launcher did not apply an OS sandbox"
                )
            self._initialize()
        except BaseException:
            launched_value = getattr(self, "_launched", None)
            if isinstance(launched_value, LaunchedPolicyWorker):
                _stop_process(launched_value.process)
            self._workspace.cleanup()
            raise

    @property
    def sandbox_profile_digest(self) -> str | None:
        return self._launched.profile_digest

    @property
    def sandbox_mechanism(self) -> str:
        return self._launched.mechanism

    def _initialize(self) -> None:
        result = self._call("initialize", self.spec.factory_payload())
        if result != {"ready": True}:
            raise PolicySubprocessProtocolError("policy worker initialization response is invalid")

    def reset(self, task_instruction: str) -> bytes:
        return _decode_rpc_bytes(
            self._call("reset", {"task_instruction": task_instruction}), "policy state"
        )

    def build_request(self, state: bytes, screenshot: bytes) -> dict[str, Any]:
        result = self._call(
            "build_request",
            {"state": _encode_rpc_bytes(state), "screenshot": _encode_rpc_bytes(screenshot)},
        )
        if not isinstance(result, dict):
            raise PolicySubprocessProtocolError("policy request must be an object")
        validate_credential_free(result, field_class="policy_request")
        return cast(dict[str, Any], result)

    def reduce_state(self, state: bytes, canonical_response: bytes) -> bytes:
        return self._bytes_call(
            "reduce_state",
            state,
            canonical_response=_encode_rpc_bytes(canonical_response),
        )

    def failure_state(self, state: bytes, failure_code: str) -> bytes:
        return self._bytes_call("failure_state", state, failure_code=failure_code)

    def retryable_response_code(self, canonical_response: bytes) -> str | None:
        result = self._call(
            "retryable_response_code",
            {"canonical_response": _encode_rpc_bytes(canonical_response)},
        )
        if result is not None and not isinstance(result, str):
            raise PolicySubprocessProtocolError("retry code must be a string or null")
        return result

    def parse(self, canonical_response: bytes, state: bytes) -> dict[str, Any]:
        result = self._call(
            "parse",
            {
                "canonical_response": _encode_rpc_bytes(canonical_response),
                "state": _encode_rpc_bytes(state),
            },
        )
        if not isinstance(result, dict):
            raise PolicySubprocessProtocolError("parsed action must be an object")
        return cast(dict[str, Any], result)

    def post_parse_state(self, state: bytes, candidate: dict[str, Any]) -> bytes:
        return self._bytes_call("post_parse_state", state, candidate=candidate)

    def post_dispatch_state(
        self, state: bytes, action: dict[str, int], result: PolicyVisibleResult
    ) -> bytes:
        return self._bytes_call(
            "post_dispatch_state",
            state,
            action=action,
            result=result.to_dict(),
        )

    def _bytes_call(self, method: str, state: bytes, **values: Any) -> bytes:
        return _decode_rpc_bytes(
            self._call(method, {"state": _encode_rpc_bytes(state), **values}),
            "policy state",
        )

    def _call(self, method: str, payload: Mapping[str, Any]) -> Any:
        with self._lock:
            if self._closed:
                raise PolicySubprocessUnavailableError("policy subprocess is closed")
            process = self._launched.process
            stdin = cast(BinaryIO | None, process.stdin)
            stdout = cast(BinaryIO | None, process.stdout)
            if process.poll() is not None or stdin is None or stdout is None:
                raise PolicySubprocessUnavailableError("policy subprocess is unavailable")
            request_id = self._next_request_id
            self._next_request_id += 1
            request = {
                "protocol_version": POLICY_WORKER_PROTOCOL_VERSION,
                "request_id": request_id,
                "method": method,
                "payload": dict(payload),
            }
            encoded = canonical_json_bytes(request) + b"\n"
            if len(encoded) > MAX_POLICY_RPC_BYTES:
                raise PolicySubprocessProtocolError("policy RPC request exceeds the byte limit")
            deadline = time.monotonic() + self.request_timeout_seconds
            descriptor = stdin.fileno()
            was_blocking = os.get_blocking(descriptor)
            try:
                os.set_blocking(descriptor, False)
                pending = memoryview(encoded)
                while pending:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not select.select([], [descriptor], [], remaining)[1]:
                        raise PolicySubprocessUnavailableError("policy subprocess timed out")
                    try:
                        written = os.write(descriptor, pending)
                    except BlockingIOError:
                        continue
                    pending = pending[written:]
            except PolicySubprocessError:
                _stop_process(process)
                raise
            except (BrokenPipeError, OSError) as exc:
                _stop_process(process)
                raise PolicySubprocessUnavailableError(
                    "policy subprocess is unavailable"
                ) from exc
            finally:
                os.set_blocking(descriptor, was_blocking)
            try:
                response_bytes = _read_response_line(
                    stdout, timeout_seconds=deadline - time.monotonic()
                )
            except PolicySubprocessError:
                _stop_process(process)
                raise
            try:
                response = _decode_canonical_object(response_bytes, "RPC response")
                required = {"protocol_version", "request_id", "ok"}
                if (
                    response.get("protocol_version") != POLICY_WORKER_PROTOCOL_VERSION
                    or response.get("request_id") != request_id
                    or type(response.get("ok")) is not bool
                ):
                    raise PolicySubprocessProtocolError(
                        "policy RPC response identity is invalid"
                    )
                if response["ok"] is True:
                    if set(response) != required | {"result"}:
                        raise PolicySubprocessProtocolError(
                            "policy RPC response fields are invalid"
                        )
                    return response["result"]
                if set(response) != required | {"error_code"} or response.get(
                    "error_code"
                ) != "policy_error":
                    raise PolicySubprocessProtocolError(
                        "policy RPC failure fields are invalid"
                    )
            except PolicySubprocessProtocolError:
                _stop_process(process)
                raise
            _stop_process(process)
            raise PolicySubprocessError("policy subprocess rejected the request")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                if self._launched.process.poll() is None:
                    self._call("close", {})
            except PolicySubprocessError:
                pass
            finally:
                self._closed = True
                _stop_process(self._launched.process)
                self._workspace.cleanup()


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=1)


def _encode_rpc_bytes(value: bytes) -> str:
    if not isinstance(value, bytes):
        raise TypeError("policy RPC byte values must be bytes")
    return base64.b64encode(value).decode("ascii")


def _decode_rpc_bytes(value: object, field: str) -> bytes:
    if not isinstance(value, str):
        raise PolicySubprocessProtocolError(f"{field} must be base64 text")
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise PolicySubprocessProtocolError(f"{field} is not valid base64") from exc


def _decode_canonical_object(data: bytes, field: str) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON constant: {value}")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(
            data,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PolicySubprocessProtocolError(f"{field} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise PolicySubprocessProtocolError(f"{field} must be an object")
    if canonical_json_bytes(value) != data:
        raise PolicySubprocessProtocolError(f"{field} must use canonical JSON encoding")
    return cast(dict[str, Any], value)
