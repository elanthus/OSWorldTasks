"""Standalone stdlib-only RPC entrypoint for a sandboxed serving policy.

This file intentionally imports no PixelGym application modules.  The sandbox launcher grants
read access to this exact file and to the approved policy/dependency roots, without making the
serving application's source tree readable merely to bootstrap the worker.
"""

from __future__ import annotations

import base64
import binascii
import importlib
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

POLICY_WORKER_PROTOCOL_VERSION = "pixelgym-serving-policy-worker-v1"
MAX_POLICY_RPC_BYTES = 16 * 1024 * 1024


class WorkerProtocolError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PolicyVisibleResult:
    screenshot_digest: str
    reward: float
    terminated: bool
    truncated: bool
    step_index: int

    def __post_init__(self) -> None:
        hexadecimal = self.screenshot_digest.removeprefix("sha256:")
        if (
            type(self.screenshot_digest) is not str
            or not self.screenshot_digest.startswith("sha256:")
            or len(hexadecimal) != 64
            or any(character not in "0123456789abcdef" for character in hexadecimal)
        ):
            raise ValueError("policy-visible screenshot digest is invalid")
        if type(self.reward) is not float:
            raise TypeError("policy-visible reward must be a float")
        if type(self.terminated) is not bool or type(self.truncated) is not bool:
            raise TypeError("policy-visible episode flags must be booleans")
        if type(self.step_index) is not int or self.step_index < 0:
            raise TypeError("policy-visible step index is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "screenshot_digest": self.screenshot_digest,
            "reward": self.reward,
            "terminated": self.terminated,
            "truncated": self.truncated,
            "step_index": self.step_index,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PolicyVisibleResult:
        expected = {
            "screenshot_digest",
            "reward",
            "terminated",
            "truncated",
            "step_index",
        }
        if set(value) != expected:
            raise ValueError("policy-visible result fields are invalid")
        return cls(
            screenshot_digest=value["screenshot_digest"],
            reward=value["reward"],
            terminated=value["terminated"],
            truncated=value["truncated"],
            step_index=value["step_index"],
        )


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _decode_object(data: bytes) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON constant: {value}")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    value = json.loads(
        data,
        parse_constant=reject_constant,
        object_pairs_hook=reject_duplicates,
    )
    if not isinstance(value, dict) or _canonical_json_bytes(value) != data:
        raise WorkerProtocolError("policy RPC object is not canonical")
    return value


def _decode_bytes(value: object) -> bytes:
    if not isinstance(value, str):
        raise TypeError("policy RPC byte value must be text")
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise WorkerProtocolError("policy RPC byte value is invalid") from exc


def _encode_bytes(value: bytes) -> str:
    if not isinstance(value, bytes):
        raise TypeError("policy method must return bytes")
    return base64.b64encode(value).decode("ascii")


def _payload(value: object, expected: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise WorkerProtocolError("policy RPC payload fields are invalid")
    return value


def _factory(module_name: object, factory_name: object) -> Any:
    if not isinstance(module_name, str) or not isinstance(factory_name, str):
        raise TypeError("policy factory identity must be text")
    target: Any = importlib.import_module(module_name)
    for component in factory_name.split("."):
        if not component or component.startswith("_"):
            raise ValueError("private policy factories are forbidden")
        target = getattr(target, component)
    if not callable(target):
        raise TypeError("policy factory is not callable")
    return target


def _invoke(policy: Any, method: str, payload: object) -> tuple[Any, bool]:
    if method == "reset":
        values = _payload(payload, {"task_instruction"})
        if not isinstance(values["task_instruction"], str):
            raise TypeError("task instruction must be text")
        return _encode_bytes(policy.reset(values["task_instruction"])), False
    if method == "build_request":
        values = _payload(payload, {"state", "screenshot"})
        result = policy.build_request(
            _decode_bytes(values["state"]), _decode_bytes(values["screenshot"])
        )
        if not isinstance(result, dict):
            raise TypeError("policy request must be an object")
        return result, False
    if method == "reduce_state":
        values = _payload(payload, {"state", "canonical_response"})
        return (
            _encode_bytes(
                policy.reduce_state(
                    _decode_bytes(values["state"]),
                    _decode_bytes(values["canonical_response"]),
                )
            ),
            False,
        )
    if method == "failure_state":
        values = _payload(payload, {"state", "failure_code"})
        if not isinstance(values["failure_code"], str):
            raise TypeError("failure code must be text")
        return (
            _encode_bytes(
                policy.failure_state(
                    _decode_bytes(values["state"]), values["failure_code"]
                )
            ),
            False,
        )
    if method == "retryable_response_code":
        values = _payload(payload, {"canonical_response"})
        result = policy.retryable_response_code(
            _decode_bytes(values["canonical_response"])
        )
        if result is not None and not isinstance(result, str):
            raise TypeError("retry code must be text or null")
        return result, False
    if method == "parse":
        values = _payload(payload, {"canonical_response", "state"})
        result = policy.parse(
            _decode_bytes(values["canonical_response"]),
            _decode_bytes(values["state"]),
        )
        if not isinstance(result, dict):
            raise TypeError("parsed action must be an object")
        return result, False
    if method == "post_parse_state":
        values = _payload(payload, {"state", "candidate"})
        if not isinstance(values["candidate"], dict):
            raise TypeError("action candidate must be an object")
        return (
            _encode_bytes(
                policy.post_parse_state(
                    _decode_bytes(values["state"]), values["candidate"]
                )
            ),
            False,
        )
    if method == "post_dispatch_state":
        values = _payload(payload, {"state", "action", "result"})
        if not isinstance(values["action"], dict) or not isinstance(
            values["result"], dict
        ):
            raise TypeError("post-dispatch values must be objects")
        return (
            _encode_bytes(
                policy.post_dispatch_state(
                    _decode_bytes(values["state"]),
                    values["action"],
                    PolicyVisibleResult.from_dict(values["result"]),
                )
            ),
            False,
        )
    if method == "close":
        _payload(payload, set())
        policy.close()
        return None, True
    raise ValueError("unsupported policy RPC method")


def _response(request_id: int, *, result: Any = None, error: bool = False) -> bytes:
    value = {
        "protocol_version": POLICY_WORKER_PROTOCOL_VERSION,
        "request_id": request_id,
        "ok": not error,
        **({"error_code": "policy_error"} if error else {"result": result}),
    }
    return _canonical_json_bytes(value) + b"\n"


def _import_roots(arguments: list[str]) -> tuple[str, ...]:
    if len(arguments) % 2 != 0:
        raise ValueError("policy worker arguments are malformed")
    roots: list[str] = []
    for index in range(0, len(arguments), 2):
        if arguments[index] != "--import-root" or not arguments[index + 1]:
            raise ValueError("policy worker arguments are malformed")
        roots.append(arguments[index + 1])
    return tuple(roots)


def main() -> int:
    try:
        sys.path[:0] = list(_import_roots(sys.argv[1:]))
    except Exception:  # noqa: BLE001 - malformed startup input exits without diagnostics.
        return 2
    policy: Any | None = None
    while True:
        line = sys.stdin.buffer.readline(MAX_POLICY_RPC_BYTES + 1)
        if not line:
            return 0
        request_id: int | None = None
        if len(line) > MAX_POLICY_RPC_BYTES or not line.endswith(b"\n"):
            return 2
        try:
            request = _decode_object(line[:-1])
            if set(request) != {"protocol_version", "request_id", "method", "payload"}:
                raise WorkerProtocolError("policy RPC request fields are invalid")
            if request["protocol_version"] != POLICY_WORKER_PROTOCOL_VERSION:
                raise WorkerProtocolError("policy RPC version is invalid")
            request_id_value = request["request_id"]
            method = request["method"]
            if (
                type(request_id_value) is not int
                or request_id_value < 0
                or not isinstance(method, str)
            ):
                raise WorkerProtocolError("policy RPC request identity is invalid")
            request_id = request_id_value
            if method == "initialize":
                if policy is not None:
                    raise WorkerProtocolError("policy worker is already initialized")
                values = _payload(
                    request["payload"],
                    {"factory_module", "factory_name", "factory_kwargs"},
                )
                kwargs = values["factory_kwargs"]
                if not isinstance(kwargs, dict):
                    raise TypeError("policy factory kwargs must be an object")
                policy = _factory(values["factory_module"], values["factory_name"])(
                    **kwargs
                )
                result, should_close = {"ready": True}, False
            else:
                if policy is None:
                    raise WorkerProtocolError("policy worker is not initialized")
                result, should_close = _invoke(policy, method, request["payload"])
        except Exception:  # noqa: BLE001 - the worker returns only a stable error code.
            if request_id is not None:
                sys.stdout.buffer.write(_response(request_id, error=True))
                sys.stdout.buffer.flush()
            return 1
        sys.stdout.buffer.write(_response(request_id, result=result))
        sys.stdout.buffer.flush()
        if should_close:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
