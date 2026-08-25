"""No-cost manifests used to exercise v5 without a provider or credentials."""

from __future__ import annotations

from pixelgym.grounding.v5.contracts import PolicyManifest
from pixelgym.grounding.v5.coordinates import IDENTITY_ADAPTER
from pixelgym.grounding.v5.sandbox import build_sandbox_manifest


def scripted_policy_manifest() -> PolicyManifest:
    sandbox = build_sandbox_manifest(
        runtime_digest="sha256:" + "0" * 64,
        provider_endpoint="http://127.0.0.1:8765",
    )
    return PolicyManifest.build(
        provider="in-process-fake",
        model="no-cost-scripted-policy-v1",
        exact_snapshot=True,
        harness_digest="sha256:" + "1" * 64,
        dependency_lock_digest="sha256:" + "2" * 64,
        system_prompt_digest="sha256:" + "3" * 64,
        task_renderer_version="pixelgym-agent-v5-task-renderer-v1",
        response_schema_version="pixelgym-agent-v5-canonical-response-v1",
        state_reducer_version="pixelgym-agent-v5-scripted-state-reducer-v1",
        parser_version="pixelgym-agent-v5-json-action-parser-v1",
        memory_policy_version="pixelgym-agent-v5-stateful-memory-v1",
        coordinate_adapter=IDENTITY_ADAPTER.name,
        coordinate_adapter_digest=IDENTITY_ADAPTER.source_digest,
        coordinate_input_convention="integer-pixel/1024x768",
        max_model_attempts_per_action=1,
        max_cancellation_requests_per_attempt=1,
        max_reconciliation_requests_per_attempt=1,
        request_deadline_seconds=5.0,
        cancellation_mode="cancel-once",
        reconciliation_deadline_seconds=1.0,
        sandbox=sandbox,
        code_revision="no-cost-fixture",
        dirty_worktree_policy="allowed-for-no-cost-tests-only",
        inference_parameters=(("temperature", "0"),),
        context_limit=4096,
    )
