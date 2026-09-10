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
import importlib
import json
import platform
import re
import select
import subprocess
import sys
import tempfile
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Protocol, cast
from urllib.parse import urlsplit

from pixelgym.grounding.v5.contracts import PolicyVisibleResult, sha256_bytes
from pixelgym.grounding.v5.evidence import validate_credential_free
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
    *, runtime_executable: Path, import_roots: Sequence[Path]
) -> list[str]:
    roots = [str(path.resolve()) for path in _deduplicated_resolved(import_roots)]
    bootstrap = (
        "import runpy,sys;"
        f"sys.path[:0]={roots!r};"
        "runpy.run_module('pixelgym.platform.policy_subprocess',run_name='__main__')"
    )
    return [str(runtime_executable), "-I", "-B", "-c", bootstrap]


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
        profile = darwin_serving_profile(
            provider_endpoint=spec.provider_endpoint,
            runtime_root=runtime_root,
            runtime_executable=runtime_executable,
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
            try:
                stdin.write(encoded)
                stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise PolicySubprocessUnavailableError(
                    "policy subprocess is unavailable"
                ) from exc
            ready, _, _ = select.select(
                [stdout], [], [], self.request_timeout_seconds
            )
            if not ready:
                _stop_process(process)
                raise PolicySubprocessUnavailableError("policy subprocess timed out")
            response_bytes = stdout.readline(MAX_POLICY_RPC_BYTES + 1)
            if not response_bytes or len(response_bytes) > MAX_POLICY_RPC_BYTES:
                _stop_process(process)
                raise PolicySubprocessProtocolError(
                    "policy RPC response is unavailable or oversized"
                )
            try:
                response = _decode_canonical_object(
                    response_bytes.rstrip(b"\n"), "RPC response"
                )
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


def _require_payload(payload: object, expected: set[str]) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError("policy RPC payload fields are invalid")
    return cast(dict[str, Any], payload)


def _resolve_factory(module_name: object, factory_name: object) -> Any:
    if (
        not isinstance(module_name, str)
        or not isinstance(factory_name, str)
        or not _IMPORT_NAME.fullmatch(module_name)
        or not _IMPORT_NAME.fullmatch(factory_name)
    ):
        raise ValueError("policy factory identity is malformed")
    target: Any = importlib.import_module(module_name)
    for component in factory_name.split("."):
        if component.startswith("_"):
            raise ValueError("private policy factories are forbidden")
        target = getattr(target, component)
    if not callable(target):
        raise TypeError("policy factory is not callable")
    return target


def _worker_invoke(policy: Any, method: str, payload: object) -> tuple[Any, bool]:
    if method == "reset":
        values = _require_payload(payload, {"task_instruction"})
        if not isinstance(values["task_instruction"], str):
            raise TypeError("task instruction must be text")
        return _encode_rpc_bytes(policy.reset(values["task_instruction"])), False
    if method == "build_request":
        values = _require_payload(payload, {"state", "screenshot"})
        result = policy.build_request(
            _decode_rpc_bytes(values["state"], "policy state"),
            _decode_rpc_bytes(values["screenshot"], "screenshot"),
        )
        if not isinstance(result, dict):
            raise TypeError("policy request must be an object")
        return result, False
    if method == "reduce_state":
        values = _require_payload(payload, {"state", "canonical_response"})
        result = policy.reduce_state(
            _decode_rpc_bytes(values["state"], "policy state"),
            _decode_rpc_bytes(values["canonical_response"], "canonical response"),
        )
        return _encode_rpc_bytes(result), False
    if method == "failure_state":
        values = _require_payload(payload, {"state", "failure_code"})
        if not isinstance(values["failure_code"], str):
            raise TypeError("failure code must be text")
        result = policy.failure_state(
            _decode_rpc_bytes(values["state"], "policy state"), values["failure_code"]
        )
        return _encode_rpc_bytes(result), False
    if method == "retryable_response_code":
        values = _require_payload(payload, {"canonical_response"})
        result = policy.retryable_response_code(
            _decode_rpc_bytes(values["canonical_response"], "canonical response")
        )
        if result is not None and not isinstance(result, str):
            raise TypeError("retry code must be text or null")
        return result, False
    if method == "parse":
        values = _require_payload(payload, {"canonical_response", "state"})
        result = policy.parse(
            _decode_rpc_bytes(values["canonical_response"], "canonical response"),
            _decode_rpc_bytes(values["state"], "policy state"),
        )
        if not isinstance(result, dict):
            raise TypeError("parsed action must be an object")
        return result, False
    if method == "post_parse_state":
        values = _require_payload(payload, {"state", "candidate"})
        if not isinstance(values["candidate"], dict):
            raise TypeError("action candidate must be an object")
        result = policy.post_parse_state(
            _decode_rpc_bytes(values["state"], "policy state"), values["candidate"]
        )
        return _encode_rpc_bytes(result), False
    if method == "post_dispatch_state":
        values = _require_payload(payload, {"state", "action", "result"})
        if not isinstance(values["action"], dict) or not isinstance(values["result"], dict):
            raise TypeError("post-dispatch values must be objects")
        result = policy.post_dispatch_state(
            _decode_rpc_bytes(values["state"], "policy state"),
            values["action"],
            PolicyVisibleResult.from_dict(values["result"]),
        )
        return _encode_rpc_bytes(result), False
    if method == "close":
        _require_payload(payload, set())
        policy.close()
        return None, True
    raise ValueError("unsupported policy RPC method")


def _worker_response(request_id: int, *, result: Any = None, error: bool = False) -> bytes:
    value = {
        "protocol_version": POLICY_WORKER_PROTOCOL_VERSION,
        "request_id": request_id,
        "ok": not error,
        **({"error_code": "policy_error"} if error else {"result": result}),
    }
    return canonical_json_bytes(value) + b"\n"


def _worker_main() -> int:
    policy: Any | None = None
    for line in sys.stdin.buffer:
        request_id: int | None = None
        if len(line) > MAX_POLICY_RPC_BYTES or not line.endswith(b"\n"):
            return 2
        try:
            request = _decode_canonical_object(line[:-1], "RPC request")
            if set(request) != {"protocol_version", "request_id", "method", "payload"}:
                raise ValueError("policy RPC request fields are invalid")
            if request["protocol_version"] != POLICY_WORKER_PROTOCOL_VERSION:
                raise ValueError("policy RPC version is invalid")
            request_id_value = request["request_id"]
            method = request["method"]
            if (
                type(request_id_value) is not int
                or request_id_value < 0
                or not isinstance(method, str)
            ):
                raise ValueError("policy RPC request identity is invalid")
            request_id = request_id_value
            if method == "initialize":
                if policy is not None:
                    raise ValueError("policy worker is already initialized")
                values = _require_payload(
                    request["payload"],
                    {"factory_module", "factory_name", "factory_kwargs"},
                )
                kwargs = values["factory_kwargs"]
                if not isinstance(kwargs, dict):
                    raise TypeError("policy factory kwargs must be an object")
                validate_credential_free(kwargs, field_class="policy_factory_kwargs")
                factory = _resolve_factory(values["factory_module"], values["factory_name"])
                policy = factory(**kwargs)
                result, should_close = {"ready": True}, False
            else:
                if policy is None:
                    raise ValueError("policy worker is not initialized")
                result, should_close = _worker_invoke(
                    policy, method, request["payload"]
                )
        except Exception:  # noqa: BLE001 - worker returns only a stable error code.
            if request_id is not None:
                sys.stdout.buffer.write(_worker_response(request_id, error=True))
                sys.stdout.buffer.flush()
            return 1
        sys.stdout.buffer.write(_worker_response(request_id, result=result))
        sys.stdout.buffer.flush()
        if should_close:
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(_worker_main())
