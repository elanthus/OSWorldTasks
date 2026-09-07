from __future__ import annotations

import dataclasses
import io
import json
import subprocess
from pathlib import Path
from typing import ClassVar, Self

import pytest
from PIL import Image

from pixelgym.grounding.evaluation import RAW_SCHEMA, parse_prediction
from pixelgym.grounding.providers import (
    ClaudeCodeCLIProvider,
    CodexCLIProvider,
    GeminiCoordinateAdapter,
    MockProvider,
    OpenRouterProvider,
    ProviderResponse,
    QwenNormalizedCoordinateAdapter,
)


def test_codex_provider_uses_ephemeral_read_only_image_and_schema_flags(tmp_path: Path) -> None:
    image_path = tmp_path / "image.png"
    Image.new("RGB", (2, 2), "white").save(image_path)
    commands = []

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append((command, kwargs))
        if command[1:] == ["--version"]:
            return subprocess.CompletedProcess(command, 0, "codex-cli 0.test\n", "")
        output_index = command.index("--output-last-message") + 1
        Path(command[output_index]).write_text('{"x":1,"y":1}')
        return subprocess.CompletedProcess(command, 0, '{"type":"turn.completed"}\n', "")

    provider = CodexCLIProvider(executable="codex-test", command_runner=runner)
    response = provider.invoke(image_path=image_path, prompt="prompt", schema=RAW_SCHEMA)

    command = commands[0][0]
    assert command[:2] == ["codex-test", "exec"]
    assert "--ephemeral" in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert command[command.index("--model") + 1] == "gpt-5.4-mini"
    assert command[command.index("--image") + 1] == str(image_path.resolve())
    assert "--output-schema" in command
    assert commands[0][1]["input"] == "prompt"
    assert commands[0][1]["check"] is False
    assert response.raw_response == '{"x":1,"y":1}'
    assert response.provider_metadata["cli_version"] == "codex-cli 0.test"


def test_codex_provider_does_not_cache_unredacted_cli_stdout(tmp_path: Path) -> None:
    image_path = tmp_path / "image.png"
    Image.new("RGB", (2, 2), "white").save(image_path)

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[1:] == ["--version"]:
            return subprocess.CompletedProcess(command, 0, "codex-cli 0.test\n", "")
        output_index = command.index("--output-last-message") + 1
        Path(command[output_index]).write_text('{"x":1,"y":1}')
        stdout = "\n".join(
            (
                json.dumps(
                    {
                        "type": "item.completed",
                        "local_path": "/Users/private/project",
                        "account": "private@example.test",
                    }
                ),
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {"input_tokens": 3, "output_tokens": 1},
                    }
                ),
            )
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    response = CodexCLIProvider(
        executable="codex-test", command_runner=runner
    ).invoke(image_path=image_path, prompt="prompt", schema=RAW_SCHEMA)
    cached = json.dumps(response.to_cache_dict())

    assert response.usage == {"input_tokens": 3, "output_tokens": 1}
    assert response.provider_trace == []
    assert response.provider_metadata["trace_event_count"] == 2
    assert response.provider_metadata["trace_event_types"] == [
        "item.completed",
        "turn.completed",
    ]
    assert "/Users/private" not in cached
    assert "private@example.test" not in cached


def test_openrouter_requires_both_environment_variables() -> None:
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        OpenRouterProvider(environment={})
    with pytest.raises(RuntimeError, match="OPENROUTER_MODEL"):
        OpenRouterProvider(environment={"OPENROUTER_API_KEY": "secret"})


def test_openrouter_rejects_request_parameter_overrides_of_reserved_fields() -> None:
    with pytest.raises(ValueError, match="reserved fields"):
        OpenRouterProvider(
            environment={
                "OPENROUTER_API_KEY": "secret",
                "OPENROUTER_MODEL": "vendor/model",
            },
            request_parameters={"model": "different/model"},
        )


def test_openrouter_reads_model_from_environment_and_never_serializes_key(tmp_path: Path) -> None:
    image_path = tmp_path / "image.png"
    Image.new("RGB", (2, 2), "white").save(image_path)
    captured = {}

    class Response(io.BytesIO):
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            self.close()

    def urlopen(request: object, *, timeout: float) -> Response:
        captured["request"] = request
        captured["timeout"] = timeout
        return Response(
            json.dumps(
                {
                    "choices": [{"message": {"content": '{"x":1,"y":1}'}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                }
            ).encode("utf-8")
        )

    provider = OpenRouterProvider(
        environment={"OPENROUTER_API_KEY": "secret-value", "OPENROUTER_MODEL": "vendor/model"},
        urlopen=urlopen,
    )
    response = provider.invoke(image_path=image_path, prompt="prompt", schema=RAW_SCHEMA)

    request = captured["request"]
    body = json.loads(request.data)
    assert provider.model == "vendor/model"
    assert body["model"] == "vendor/model"
    assert body["temperature"] == 0
    assert body["seed"] == 20260809
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["provider"]["require_parameters"] is True
    assert b"secret-value" not in request.data
    assert request.headers["Authorization"] == "Bearer secret-value"
    assert response.raw_response == '{"x":1,"y":1}'


def test_openrouter_binds_custom_parameters_routing_and_response_identity(tmp_path: Path) -> None:
    image_path = tmp_path / "image.png"
    Image.new("RGB", (2, 2), "white").save(image_path)
    captured = {}

    class Response(io.BytesIO):
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            self.close()

    def urlopen(request: object, *, timeout: float) -> Response:
        del timeout
        captured["body"] = json.loads(request.data)
        return Response(
            json.dumps(
                {
                    "id": "generation-1",
                    "model": "vendor/model",
                    "provider": "Vendor",
                    "choices": [{"message": {"content": '{"x":1,"y":1}'}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0.001},
                }
            ).encode("utf-8")
        )

    provider = OpenRouterProvider(
        environment={"OPENROUTER_API_KEY": "secret", "OPENROUTER_MODEL": "vendor/model"},
        request_parameters={"max_tokens": 128},
        provider_routing={"only": ["vendor"], "allow_fallbacks": False},
        urlopen=urlopen,
    )
    response = provider.invoke(image_path=image_path, prompt="prompt", schema=RAW_SCHEMA)

    assert captured["body"]["max_tokens"] == 128
    assert captured["body"]["provider"] == {
        "require_parameters": True,
        "only": ["vendor"],
        "allow_fallbacks": False,
    }
    assert provider.parameters["provider_routing"] == captured["body"]["provider"]
    assert response.provider_metadata == {
        "endpoint": OpenRouterProvider.endpoint,
        "response_id": "generation-1",
        "response_model": "vendor/model",
        "upstream_provider": "Vendor",
    }


def test_openrouter_does_not_cache_transport_exception_details(tmp_path: Path) -> None:
    image_path = tmp_path / "image.png"
    Image.new("RGB", (2, 2), "white").save(image_path)

    def urlopen(request: object, *, timeout: float) -> object:
        del request, timeout
        raise OSError("proxy at /Users/private/account.sock failed")

    response = OpenRouterProvider(
        environment={"OPENROUTER_API_KEY": "secret", "OPENROUTER_MODEL": "vendor/model"},
        urlopen=urlopen,
    ).invoke(image_path=image_path, prompt="prompt", schema=RAW_SCHEMA)

    cached = json.dumps(response.to_cache_dict())
    assert response.request_failure == "OSError: provider request failed"
    assert "/Users/private" not in cached


# ── ClaudeCodeCLIProvider ──


def _claude_json_result(text: str, *, cost: float = 0.01) -> str:
    return json.dumps(
        {
            "type": "result",
            "result": text,
            "duration_ms": 1234,
            "duration_api_ms": 1000,
            "num_turns": 1,
            "is_error": False,
            "session_id": "test-session",
            "total_cost_usd": cost,
            "usage": {"input_tokens": 12, "output_tokens": 4},
        }
    )


def test_claude_provider_passes_print_model_schema_and_reads_image(tmp_path: Path) -> None:
    image_path = tmp_path / "image.png"
    Image.new("RGB", (2, 2), "white").save(image_path)
    commands: list[tuple[list[str], dict[str, object]]] = []

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append((command, kwargs))
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, "2.1.224 (Claude Code)\n", "")
        return subprocess.CompletedProcess(
            command, 0, _claude_json_result('{"x":42,"y":99}'), ""
        )

    provider = ClaudeCodeCLIProvider(executable="claude-test", command_runner=runner)
    response = provider.invoke(image_path=image_path, prompt="Locate the target.", schema=RAW_SCHEMA)

    command = commands[0][0]
    assert command[0] == "claude-test"
    assert "--print" in command
    assert "--bare" not in command
    assert command[command.index("--model") + 1] == "claude-sonnet-5"
    assert "--output-format" in command
    assert command[command.index("--output-format") + 1] == "json"
    assert "--json-schema" in command
    assert "--dangerously-skip-permissions" not in command
    assert command[command.index("--permission-mode") + 1] == "dontAsk"
    assert command[command.index("--setting-sources") + 1] == ""
    assert Path(commands[0][1]["cwd"]).name.startswith("pixelgym-claude-grounding-")
    assert str(image_path.resolve()) in " ".join(command)
    assert response.raw_response == '{"x":42,"y":99}'
    assert response.request_failure is None
    assert response.provider_metadata["cli_version"] == "2.1.224 (Claude Code)"


def test_claude_provider_extracts_usage_from_json_envelope(tmp_path: Path) -> None:
    image_path = tmp_path / "image.png"
    Image.new("RGB", (2, 2), "white").save(image_path)

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, "2.1.224\n", "")
        return subprocess.CompletedProcess(
            command, 0, _claude_json_result('{"x":1,"y":1}', cost=0.005), ""
        )

    response = ClaudeCodeCLIProvider(
        executable="claude-test", command_runner=runner
    ).invoke(image_path=image_path, prompt="p", schema=RAW_SCHEMA)

    assert response.provider_metadata["cost_usd"] == 0.005
    assert response.provider_metadata["num_turns"] == 1
    assert response.usage == {"input_tokens": 12, "output_tokens": 4}


def test_claude_provider_handles_nonzero_exit(tmp_path: Path) -> None:
    image_path = tmp_path / "image.png"
    Image.new("RGB", (2, 2), "white").save(image_path)

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, "2.1.224\n", "")
        return subprocess.CompletedProcess(command, 1, "", "error details at /Users/private")

    response = ClaudeCodeCLIProvider(
        executable="claude-test", command_runner=runner
    ).invoke(image_path=image_path, prompt="p", schema=RAW_SCHEMA)

    assert response.request_failure == "claude CLI exited with status 1"
    assert response.raw_response is None
    cached = json.dumps(response.to_cache_dict())
    assert "/Users/private" not in cached


def test_claude_provider_handles_process_exception(tmp_path: Path) -> None:
    image_path = tmp_path / "image.png"
    Image.new("RGB", (2, 2), "white").save(image_path)

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, "2.1.224\n", "")
        raise OSError("cannot launch /Users/private/claude")

    response = ClaudeCodeCLIProvider(
        executable="claude-test", command_runner=runner
    ).invoke(image_path=image_path, prompt="p", schema=RAW_SCHEMA)

    assert response.request_failure == "OSError: provider process failed"
    cached = json.dumps(response.to_cache_dict())
    assert "/Users/private" not in cached


def test_claude_provider_handles_is_error_response(tmp_path: Path) -> None:
    image_path = tmp_path / "image.png"
    Image.new("RGB", (2, 2), "white").save(image_path)

    error_result = json.dumps(
        {
            "type": "result",
            "result": "Rate limited",
            "is_error": True,
            "cost_usd": 0,
            "duration_ms": 100,
            "num_turns": 0,
            "session_id": "s",
            "total_cost_usd": 0,
        }
    )

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, "2.1.224\n", "")
        return subprocess.CompletedProcess(command, 0, error_result, "")

    response = ClaudeCodeCLIProvider(
        executable="claude-test", command_runner=runner
    ).invoke(image_path=image_path, prompt="p", schema=RAW_SCHEMA)

    assert response.request_failure == "claude CLI reported an error result"
    assert response.raw_response is None


@pytest.mark.parametrize("stdout", ["[]", '"scalar"', "42", "null"])
def test_claude_provider_records_non_object_json_as_failure(
    tmp_path: Path, stdout: str
) -> None:
    image_path = tmp_path / "image.png"
    Image.new("RGB", (2, 2), "white").save(image_path)

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, "2.1.224\n", "")
        return subprocess.CompletedProcess(command, 0, stdout, "")

    response = ClaudeCodeCLIProvider(
        executable="claude-test", command_runner=runner
    ).invoke(image_path=image_path, prompt="p", schema=RAW_SCHEMA)

    assert response.request_failure == "claude CLI produced non-object JSON output"
    assert response.raw_response is None


def test_claude_provider_does_not_leak_private_paths_in_cache(tmp_path: Path) -> None:
    image_path = tmp_path / "image.png"
    Image.new("RGB", (2, 2), "white").save(image_path)

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, "2.1.224\n", "")
        return subprocess.CompletedProcess(
            command, 0, _claude_json_result('{"x":1,"y":1}'), ""
        )

    response = ClaudeCodeCLIProvider(
        executable="claude-test", command_runner=runner
    ).invoke(image_path=image_path, prompt="p", schema=RAW_SCHEMA)

    cached = json.dumps(response.to_cache_dict())
    assert response.provider_trace == []
    assert str(tmp_path) not in cached


def test_claude_provider_protocol_attributes() -> None:
    provider = ClaudeCodeCLIProvider(executable="claude-test")
    assert provider.name == "claude-code-cli"
    assert provider.model == "claude-sonnet-5"
    assert isinstance(provider.parameters, dict)


# ── GeminiCoordinateAdapter ──


def test_gemini_adapter_rescales_1000_grid_to_actual_pixels(tmp_path: Path) -> None:
    image_path = tmp_path / "screenshot.png"
    Image.new("RGB", (1024, 768), "white").save(image_path)

    inner = MockProvider()
    inner.model = "gemini-test"
    adapter = GeminiCoordinateAdapter(inner)

    response = adapter.invoke(
        image_path=image_path,
        prompt="Locate target.",
        schema=RAW_SCHEMA,
    )
    parsed = json.loads(response.raw_response)
    assert parsed == {"x": 0, "y": 0}
    assert response.provider_metadata["coordinate_rescale"] == "1000->1024x768"
    assert response.provider_metadata["original_response"] == '{"x":0,"y":0}'


def test_gemini_adapter_rescales_nonzero_coordinates(tmp_path: Path) -> None:
    image_path = tmp_path / "screenshot.png"
    Image.new("RGB", (1024, 768), "white").save(image_path)

    inner = MockProvider()
    original_invoke = inner.invoke

    def fixed_invoke(*, image_path: Path, prompt: str, schema: dict) -> ProviderResponse:
        resp = original_invoke(image_path=image_path, prompt=prompt, schema=schema)
        return ProviderResponse(
            timestamp_utc=resp.timestamp_utc,
            latency_ms=resp.latency_ms,
            raw_response='{"x":750,"y":500}',
            usage=resp.usage,
            provider_metadata=resp.provider_metadata,
            provider_trace=resp.provider_trace,
        )

    inner.invoke = fixed_invoke  # type: ignore[assignment]
    adapter = GeminiCoordinateAdapter(inner)

    response = adapter.invoke(image_path=image_path, prompt="p", schema=RAW_SCHEMA)
    parsed = json.loads(response.raw_response)
    assert parsed["x"] == round(750 * 1024 / 1000)
    assert parsed["y"] == round(500 * 768 / 1000)


def test_gemini_adapter_passes_through_failures(tmp_path: Path) -> None:
    image_path = tmp_path / "screenshot.png"
    Image.new("RGB", (1024, 768), "white").save(image_path)

    failure = ProviderResponse(
        timestamp_utc="2000-01-01T00:00:00+00:00",
        latency_ms=0.0,
        raw_response=None,
        usage=None,
        provider_metadata={},
        provider_trace=[],
        request_failure="network error",
    )

    class FailProvider:
        name = "fail"
        model = "fail-model"
        parameters: ClassVar[dict[str, object]] = {}

        def invoke(self, **kwargs: object) -> ProviderResponse:
            return failure

    adapter = GeminiCoordinateAdapter(FailProvider())
    result = adapter.invoke(image_path=image_path, prompt="p", schema=RAW_SCHEMA)
    assert result.request_failure == "network error"
    assert result.raw_response is None


def test_gemini_adapter_passes_through_mark_id_responses(tmp_path: Path) -> None:
    image_path = tmp_path / "screenshot.png"
    Image.new("RGB", (1024, 768), "white").save(image_path)

    mark_response = ProviderResponse(
        timestamp_utc="2000-01-01T00:00:00+00:00",
        latency_ms=0.0,
        raw_response='{"mark_id":3}',
        usage=None,
        provider_metadata={},
        provider_trace=[],
    )

    class MarkProvider:
        name = "mark"
        model = "mark-model"
        parameters: ClassVar[dict[str, object]] = {}

        def invoke(self, **kwargs: object) -> ProviderResponse:
            return mark_response

    adapter = GeminiCoordinateAdapter(MarkProvider())
    result = adapter.invoke(image_path=image_path, prompt="p", schema=RAW_SCHEMA)
    assert json.loads(result.raw_response) == {"mark_id": 3}


def test_gemini_adapter_passes_through_non_numeric_coordinates(tmp_path: Path) -> None:
    image_path = tmp_path / "screenshot.png"
    Image.new("RGB", (1024, 768), "white").save(image_path)

    inner = MockProvider()
    original_invoke = inner.invoke

    def fixed_invoke(*, image_path: Path, prompt: str, schema: dict) -> ProviderResponse:
        response = original_invoke(image_path=image_path, prompt=prompt, schema=schema)
        return dataclasses.replace(response, raw_response='{"x":"640","y":null}')

    inner.invoke = fixed_invoke  # type: ignore[assignment]
    result = GeminiCoordinateAdapter(inner).invoke(
        image_path=image_path, prompt="p", schema=RAW_SCHEMA
    )
    assert json.loads(result.raw_response) == {"x": "640", "y": None}


def test_gemini_adapter_clamps_boundary_coordinates(tmp_path: Path) -> None:
    image_path = tmp_path / "screenshot.png"
    Image.new("RGB", (1024, 768), "white").save(image_path)

    inner = MockProvider()
    original_invoke = inner.invoke

    def fixed_invoke(*, image_path: Path, prompt: str, schema: dict) -> ProviderResponse:
        response = original_invoke(image_path=image_path, prompt=prompt, schema=schema)
        return dataclasses.replace(response, raw_response='{"x":1000,"y":1000}')

    inner.invoke = fixed_invoke  # type: ignore[assignment]
    result = GeminiCoordinateAdapter(inner).invoke(
        image_path=image_path, prompt="p", schema=RAW_SCHEMA
    )
    assert json.loads(result.raw_response) == {"x": 1023, "y": 767}


def test_gemini_adapter_exposes_inner_identity() -> None:
    inner = MockProvider()
    adapter = GeminiCoordinateAdapter(inner)
    assert adapter.name == "mock"
    assert adapter.model == inner.model
    assert "coordinate_rescale" in adapter.parameters
    assert adapter.parameters["coordinate_rescale"] == "gemini-1000"


def test_qwen_adapter_rescales_1000x1000_and_uses_distinct_identity(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "screenshot.png"
    Image.new("RGB", (1024, 768), "white").save(image_path)
    inner = MockProvider()
    original_invoke = inner.invoke

    def fixed_invoke(*, image_path: Path, prompt: str, schema: dict) -> ProviderResponse:
        response = original_invoke(image_path=image_path, prompt=prompt, schema=schema)
        return dataclasses.replace(
            response,
            raw_response='{"action_type":1,"x":750,"y":500,"key":0}',
        )

    inner.invoke = fixed_invoke  # type: ignore[assignment]
    adapter = QwenNormalizedCoordinateAdapter(inner)

    response = adapter.invoke(image_path=image_path, prompt="p", schema=RAW_SCHEMA)

    assert json.loads(response.raw_response) == {
        "action_type": 1,
        "x": round(750 * 1024 / 1000),
        "y": round(500 * 768 / 1000),
        "key": 0,
    }
    assert adapter.name == "mock-qwen-normalized-1000x1000"
    assert adapter.model == inner.model
    assert adapter.parameters["coordinate_rescale"] == "qwen-1000x1000"
    assert response.provider_metadata["coordinate_rescale"] == "1000->1024x768"
    assert response.provider_metadata["original_response"] == (
        '{"action_type":1,"x":750,"y":500,"key":0}'
    )


def test_qwen_adapter_rejects_nonpositive_grid_size() -> None:
    with pytest.raises(ValueError, match="grid_size must be positive"):
        QwenNormalizedCoordinateAdapter(MockProvider(), grid_size=0)


@pytest.mark.parametrize(
    ("raw_response", "expected_error"),
    (
        # x=1001 is outside the 0..1000 grid, so the adapter rescales it linearly
        # (1001 * 1024 / 1000 = 1025) instead of clamping it into range; 1025 is
        # then rejected as outside the 1024-wide screenshot.
        ('{"x":1001,"y":500}', "point lies outside screenshot"),
        ('{"x":-1,"y":500}', "point lies outside screenshot"),
        ('{"x":1.5,"y":500}', "x and y must be integers"),
    ),
)
def test_qwen_adapter_keeps_invalid_grid_coordinates_rejectable(
    tmp_path: Path, raw_response: str, expected_error: str
) -> None:
    image_path = tmp_path / "screenshot.png"
    Image.new("RGB", (1024, 768), "white").save(image_path)
    inner = MockProvider()
    original_invoke = inner.invoke

    def fixed_invoke(*, image_path: Path, prompt: str, schema: dict) -> ProviderResponse:
        response = original_invoke(image_path=image_path, prompt=prompt, schema=schema)
        return dataclasses.replace(response, raw_response=raw_response)

    inner.invoke = fixed_invoke  # type: ignore[assignment]

    response = QwenNormalizedCoordinateAdapter(inner).invoke(
        image_path=image_path, prompt="p", schema=RAW_SCHEMA
    )
    parsed = parse_prediction(
        response.raw_response, condition="raw", width=1024, height=768, marks=[]
    )

    assert parsed.status == "invalid"
    assert parsed.error == expected_error
    assert response.provider_metadata["original_response"] == raw_response
