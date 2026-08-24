from pixelgym.grounding.providers import OpenRouterProvider, QwenNormalizedCoordinateAdapter
from scripts.run_grounding_v4c_pilot import provider_for_name


def test_v4c_runner_selects_environment_configured_openrouter(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-secret")
    monkeypatch.setenv("OPENROUTER_MODEL", "qwen/qwen3.8-27b")

    provider = provider_for_name("openrouter")

    assert isinstance(provider, OpenRouterProvider)
    assert provider.name == "openrouter"
    assert provider.model == "qwen/qwen3.8-27b"


def test_v4c_runner_selects_qwen_1000x1000_adapter(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-secret")
    monkeypatch.setenv("OPENROUTER_MODEL", "qwen/qwen3.8-27b")

    provider = provider_for_name("openrouter-qwen-1000")

    assert isinstance(provider, QwenNormalizedCoordinateAdapter)
    assert provider.name == "openrouter-qwen-normalized-1000x1000"
    assert provider.model == "qwen/qwen3.8-27b"
    assert provider.parameters["coordinate_rescale"] == "qwen-1000x1000"
