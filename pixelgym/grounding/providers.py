"""Provider adapters for frozen GUI-grounding evaluation prompts."""

from __future__ import annotations

import base64
import dataclasses
import http.client
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast

CODEX_MODEL = "gpt-5.4-mini"
CODEX_PARAMETERS: dict[str, Any] = {"reasoning_effort": "low", "temperature": None}
OPENROUTER_PARAMETERS: dict[str, Any] = {"temperature": 0, "seed": 20260809}
CLAUDE_MODEL = "claude-sonnet-5"
CLAUDE_PARAMETERS: dict[str, Any] = {"temperature": 0}


@dataclasses.dataclass(frozen=True)
class ProviderResponse:
    timestamp_utc: str
    latency_ms: float
    raw_response: str | None
    usage: dict[str, Any] | None
    provider_metadata: dict[str, Any]
    provider_trace: list[dict[str, Any]]
    request_failure: str | None = None

    def to_cache_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_cache_dict(cls, value: dict[str, Any]) -> ProviderResponse:
        return cls(**value)


class GroundingProvider(Protocol):
    name: str
    model: str
    parameters: dict[str, Any]

    def invoke(self, *, image_path: Path, prompt: str, schema: dict[str, Any]) -> ProviderResponse:
        """Make exactly one provider request and return its unmodified final text."""


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _parse_jsonl_trace(text: str) -> list[dict[str, Any]]:
    events = []
    for line in text.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def _find_usage(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(events):
        usage = event.get("usage")
        if isinstance(usage, dict):
            return usage
        item = event.get("item")
        if isinstance(item, dict) and isinstance(item.get("usage"), dict):
            return cast(dict[str, Any], item["usage"])
    return None


class CodexCLIProvider:
    name = "codex-cli"

    def __init__(
        self,
        *,
        model: str = CODEX_MODEL,
        executable: str | None = None,
        timeout_seconds: float = 180.0,
        command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.model = model
        self.parameters = dict(CODEX_PARAMETERS)
        self.executable = executable or shutil.which("codex") or "codex"
        self.timeout_seconds = timeout_seconds
        self._run = command_runner

    def _version(self) -> str:
        try:
            completed = self._run(
                [self.executable, "--version"],
                text=True,
                capture_output=True,
                timeout=10.0,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return "unknown"
        return completed.stdout.strip() or "unknown"

    def invoke(self, *, image_path: Path, prompt: str, schema: dict[str, Any]) -> ProviderResponse:
        started_at = _timestamp()
        start = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="pixelgym-codex-grounding-") as temporary:
            temporary_path = Path(temporary)
            schema_path = temporary_path / "schema.json"
            output_path = temporary_path / "response.json"
            schema_path.write_text(json.dumps(schema, sort_keys=True))
            command = [
                self.executable,
                "exec",
                "--ephemeral",
                "--ignore-user-config",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--model",
                self.model,
                "--image",
                str(image_path.resolve()),
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "--json",
                "-c",
                'model_reasoning_effort="low"',
                "-",
            ]
            try:
                completed = self._run(
                    command,
                    input=prompt,
                    text=True,
                    capture_output=True,
                    cwd=temporary,
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                return ProviderResponse(
                    timestamp_utc=started_at,
                    latency_ms=(time.monotonic() - start) * 1000,
                    raw_response=None,
                    usage=None,
                    provider_metadata={"cli_version": self._version(), "exit_code": None},
                    provider_trace=[],
                    request_failure=f"{type(exc).__name__}: provider process failed",
                )
            trace = _parse_jsonl_trace(completed.stdout)
            usage = _find_usage(trace)
            trace_event_types = sorted(
                {
                    event_type
                    for event in trace
                    if isinstance((event_type := event.get("type")), str)
                }
            )
            raw_response = output_path.read_text() if output_path.is_file() else None
            failure = None
            if completed.returncode != 0:
                # stderr may contain local paths or account metadata. Preserve only the
                # process status in published/cacheable records.
                failure = f"codex CLI exited with status {completed.returncode}"
            elif raw_response is None:
                failure = "codex CLI produced no final response file"
            return ProviderResponse(
                timestamp_utc=started_at,
                latency_ms=(time.monotonic() - start) * 1000,
                raw_response=raw_response,
                usage=usage,
                provider_metadata={
                    "cli_version": self._version(),
                    "exit_code": completed.returncode,
                    "trace_event_count": len(trace),
                    "trace_event_types": trace_event_types,
                },
                # Full CLI JSON events may contain local paths or account metadata.
                # Retain only aggregate event metadata and separately extracted usage.
                provider_trace=[],
                request_failure=failure,
            )


class ClaudeCodeCLIProvider:
    name = "claude-code-cli"

    def __init__(
        self,
        *,
        model: str = CLAUDE_MODEL,
        executable: str | None = None,
        timeout_seconds: float = 180.0,
        command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.model = model
        self.parameters = dict(CLAUDE_PARAMETERS)
        self.executable = executable or shutil.which("claude") or "claude"
        self.timeout_seconds = timeout_seconds
        self._run = command_runner

    def _version(self) -> str:
        try:
            completed = self._run(
                [self.executable, "--version"],
                text=True,
                capture_output=True,
                timeout=10.0,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return "unknown"
        return completed.stdout.strip() or "unknown"

    def invoke(self, *, image_path: Path, prompt: str, schema: dict[str, Any]) -> ProviderResponse:
        started_at = _timestamp()
        start = time.monotonic()
        full_prompt = (
            f"Use the Read tool to view the screenshot image at "
            f"{image_path.resolve()}. Then answer:\n\n{prompt}"
        )
        command = [
            self.executable,
            "--print",
            "--model",
            self.model,
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(schema, sort_keys=True),
            "--allowedTools",
            "Read",
            "--permission-mode",
            "dontAsk",
            "--setting-sources",
            "",
            full_prompt,
        ]
        try:
            with tempfile.TemporaryDirectory(prefix="pixelgym-claude-grounding-") as temporary:
                completed = self._run(
                    command,
                    text=True,
                    capture_output=True,
                    timeout=self.timeout_seconds,
                    check=False,
                    cwd=temporary,
                )
        except (OSError, subprocess.SubprocessError) as exc:
            return ProviderResponse(
                timestamp_utc=started_at,
                latency_ms=(time.monotonic() - start) * 1000,
                raw_response=None,
                usage=None,
                provider_metadata={"cli_version": self._version(), "exit_code": None},
                provider_trace=[],
                request_failure=f"{type(exc).__name__}: provider process failed",
            )
        if completed.returncode != 0:
            return ProviderResponse(
                timestamp_utc=started_at,
                latency_ms=(time.monotonic() - start) * 1000,
                raw_response=None,
                usage=None,
                provider_metadata={
                    "cli_version": self._version(),
                    "exit_code": completed.returncode,
                },
                provider_trace=[],
                request_failure=f"claude CLI exited with status {completed.returncode}",
            )
        try:
            envelope = json.loads(completed.stdout)
        except (json.JSONDecodeError, ValueError):
            return ProviderResponse(
                timestamp_utc=started_at,
                latency_ms=(time.monotonic() - start) * 1000,
                raw_response=None,
                usage=None,
                provider_metadata={
                    "cli_version": self._version(),
                    "exit_code": completed.returncode,
                },
                provider_trace=[],
                request_failure="claude CLI produced unparseable output",
            )
        is_error = envelope.get("is_error", False)
        raw_response = envelope.get("result") if not is_error else None
        failure = "claude CLI reported an error result" if is_error else None
        if not is_error and raw_response is None:
            failure = "claude CLI produced no result text"
        usage = envelope.get("usage")
        return ProviderResponse(
            timestamp_utc=started_at,
            latency_ms=(time.monotonic() - start) * 1000,
            raw_response=raw_response if isinstance(raw_response, str) else None,
            usage=usage if isinstance(usage, dict) else None,
            provider_metadata={
                "cli_version": self._version(),
                "exit_code": completed.returncode,
                "cost_usd": envelope.get("total_cost_usd"),
                "num_turns": envelope.get("num_turns"),
                "session_id": envelope.get("session_id"),
            },
            provider_trace=[],
            request_failure=failure,
        )


class OpenRouterProvider:
    name = "openrouter"
    endpoint = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(
        self,
        *,
        environment: Mapping[str, str] = os.environ,
        timeout_seconds: float = 180.0,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        api_key = environment.get("OPENROUTER_API_KEY")
        model = environment.get("OPENROUTER_MODEL")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set")
        if not model:
            raise RuntimeError("OPENROUTER_MODEL is not set")
        self._api_key = api_key
        self.model = model
        self.parameters = dict(OPENROUTER_PARAMETERS)
        self.timeout_seconds = timeout_seconds
        self._urlopen = urlopen

    def invoke(self, *, image_path: Path, prompt: str, schema: dict[str, Any]) -> ProviderResponse:
        started_at = _timestamp()
        start = time.monotonic()
        mime_type = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
        image_data = base64.b64encode(image_path.read_bytes()).decode("ascii")
        payload = {
            "model": self.model,
            **self.parameters,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{image_data}"},
                        },
                    ],
                }
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "grounding_prediction",
                    "strict": True,
                    "schema": schema,
                },
            },
            "provider": {"require_parameters": True},
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with self._urlopen(request, timeout=self.timeout_seconds) as response:
                response_body = json.load(response)
        except (
            OSError,
            TimeoutError,
            urllib.error.URLError,
            http.client.HTTPException,
            json.JSONDecodeError,
        ) as exc:
            return ProviderResponse(
                timestamp_utc=started_at,
                latency_ms=(time.monotonic() - start) * 1000,
                raw_response=None,
                usage=None,
                provider_metadata={"endpoint": self.endpoint},
                provider_trace=[],
                request_failure=f"{type(exc).__name__}: provider request failed",
            )
        try:
            raw_response = response_body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raw_response = None
        usage = response_body.get("usage") if isinstance(response_body, dict) else None
        failure = None if isinstance(raw_response, str) else "OpenRouter response contained no text"
        return ProviderResponse(
            timestamp_utc=started_at,
            latency_ms=(time.monotonic() - start) * 1000,
            raw_response=raw_response,
            usage=usage if isinstance(usage, dict) else None,
            provider_metadata={"endpoint": self.endpoint},
            provider_trace=[],
            request_failure=failure,
        )


class GeminiCoordinateAdapter:
    """Rescales coordinates from Gemini's internal grid to actual pixel dimensions.

    Gemini vision models emit x/y in a ~1000×1000 coordinate space regardless of
    the actual image dimensions stated in the prompt.  This wrapper rescales the
    response coordinates to match the true image size.
    """

    def __init__(self, inner: GroundingProvider, *, grid_size: int = 1000) -> None:
        self._inner = inner
        self._grid_size = grid_size
        self.name = inner.name
        self.model = inner.model
        self.parameters = {**inner.parameters, "coordinate_rescale": f"gemini-{grid_size}"}

    def invoke(self, *, image_path: Path, prompt: str, schema: dict[str, Any]) -> ProviderResponse:
        response = self._inner.invoke(image_path=image_path, prompt=prompt, schema=schema)
        if response.raw_response is None or response.request_failure is not None:
            return response
        try:
            parsed = json.loads(response.raw_response)
        except (json.JSONDecodeError, ValueError):
            return response
        if not isinstance(parsed, dict) or "x" not in parsed or "y" not in parsed:
            return response
        if not isinstance(parsed["x"], (int, float)) or isinstance(parsed["x"], bool):
            return response
        if not isinstance(parsed["y"], (int, float)) or isinstance(parsed["y"], bool):
            return response
        from PIL import Image

        with Image.open(image_path) as img:
            width, height = img.size
        rescaled = dict(parsed)
        rescaled["x"] = min(max(round(parsed["x"] * width / self._grid_size), 0), width - 1)
        rescaled["y"] = min(max(round(parsed["y"] * height / self._grid_size), 0), height - 1)
        return dataclasses.replace(
            response,
            raw_response=json.dumps(rescaled, separators=(",", ":")),
            provider_metadata={
                **response.provider_metadata,
                "coordinate_rescale": f"{self._grid_size}->{width}x{height}",
                "original_response": response.raw_response,
            },
        )


class MockProvider:
    name = "mock"
    model = "pixelgym-deterministic-mock-v1"

    def __init__(self) -> None:
        self.parameters = {"deterministic": True}
        self.call_count = 0

    def invoke(self, *, image_path: Path, prompt: str, schema: dict[str, Any]) -> ProviderResponse:
        self.call_count += 1
        raw_response = '{"mark_id":1}' if "mark_id" in schema["properties"] else '{"x":0,"y":0}'
        return ProviderResponse(
            timestamp_utc="2000-01-01T00:00:00+00:00",
            latency_ms=0.0,
            raw_response=raw_response,
            usage={"input_tokens": 0, "output_tokens": 0},
            provider_metadata={"mock_call_number": self.call_count},
            provider_trace=[],
        )
