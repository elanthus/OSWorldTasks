import hashlib
import json
from pathlib import Path

from pixelgym.grounding.providers import ClaudeCodeCLIProvider, CodexCLIProvider, MockProvider
from scripts.run_grounding_v4b_pilot import provider_for_name

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_v4b_runner_selects_pinned_haiku_via_claude_code_cli() -> None:
    provider = provider_for_name("haiku")

    assert isinstance(provider, ClaudeCodeCLIProvider)
    assert provider.name == "claude-code-cli"
    assert provider.model == "claude-haiku-4-5-20251001"


def test_v4b_runner_preserves_existing_provider_selections() -> None:
    luna = provider_for_name("luna")
    mock = provider_for_name("mock")

    assert isinstance(luna, CodexCLIProvider)
    assert luna.model == "gpt-5.6-luna"
    assert isinstance(mock, MockProvider)


def test_committed_haiku_result_matches_immutable_evidence() -> None:
    artifacts = REPOSITORY_ROOT / "artifacts"
    result = json.loads(
        (artifacts / "grounding-v4b-pilot-results-haiku.json").read_text(
            encoding="utf-8"
        )
    )
    plan = json.loads(
        (artifacts / "grounding-v4b-pilot-plan-haiku.json").read_text(encoding="utf-8")
    )

    assert plan["upper_bound_calls"] == 80
    assert plan["cached_reachable_state_requests"] == 0
    assert result["model"] == "claude-haiku-4-5-20251001"
    assert result["collection"] == {
        "action_record_count": 60,
        "cache_hit_count": 0,
        "condition_record_count": 20,
        "cumulative_paid_call_count": 60,
        "episode_count": 10,
        "new_call_count": 60,
        "prior_paid_call_count": 0,
    }
    assert result["conditions"] == {
        "marks": {"episode_count": 10, "success_count": 10},
        "raw": {"episode_count": 10, "success_count": 10},
    }
    assert result["failures"] == {}
    assert result["routing"]["decision"] == "design_longer_horizon_successor"
    expected_evidence = {
        "predictions": {
            "path": "artifacts/grounding-v4b-pilot-predictions-haiku.jsonl",
            "sha256": "1fa6822d8595c730c2e5e81b3fced548547aa3686d62e754902ed63bdb8d1e80",
        },
        "condition_summaries": {
            "path": "artifacts/grounding-v4b-pilot-conditions-haiku.jsonl",
            "sha256": "4d936bbcb00f58b770cecea7b8d57dc74c8c7af71af27308dd893861ec131d99",
        },
    }
    for key, expected in expected_evidence.items():
        assert result[key] == expected
        evidence_path = REPOSITORY_ROOT / result[key]["path"]
        assert hashlib.sha256(evidence_path.read_bytes()).hexdigest() == result[key]["sha256"]
