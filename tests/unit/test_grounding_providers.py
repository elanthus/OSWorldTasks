from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from typing import Self

import pytest
from PIL import Image

from pixelgym.grounding.evaluation import RAW_SCHEMA
from pixelgym.grounding.providers import CodexCLIProvider, OpenRouterProvider


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


def test_openrouter_requires_both_environment_variables() -> None:
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        OpenRouterProvider(environment={})
    with pytest.raises(RuntimeError, match="OPENROUTER_MODEL"):
        OpenRouterProvider(environment={"OPENROUTER_API_KEY": "secret"})


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
