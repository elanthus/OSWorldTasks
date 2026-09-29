"""No-call checks of the new D5.9 retry plan and execution boundary.

The plan's policy inputs (all of ``pixelgym/``, ``pyproject.toml``, and the
platform lock) are byte-verified against the plan's recorded source revision
using an archive of that revision, not the live checkout. This keeps the frozen
artifact verified while allowing ``pixelgym/`` edits after D5.9.
"""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from pixelgym.grounding.v5 import claude_code_policy as claude
from pixelgym.grounding.v5 import d59_haiku_network_retry as successor
from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.d59_haiku_execution import validate_assignments
from pixelgym.grounding.v5.d59_haiku_retry_successor import CALIBRATION_PATH, POLICY_INPUT_ROOTS
from scripts import run_grounding_v5_d59_haiku_network_retry as execution
from tests.support.recorded_revision import archive_recorded_root

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / successor.OUTPUT_DIRECTORY / "execution-plan.json"


def plan():
    return json.loads(PUBLIC.read_text())


@pytest.fixture
def recorded_root(tmp_path, monkeypatch):
    """Policy inputs and plan sources as they were at the recorded revision.

    The frozen executor's ``validate_plan`` rebuilds the plan from its module
    ``ROOT``; point it at the archive so it also verifies against the revision.
    """
    root = archive_recorded_root(
        tmp_path,
        monkeypatch,
        plan()["source_binding"]["source_revision"],
        (
            *POLICY_INPUT_ROOTS,
            *successor.SOURCE_FILES,
            successor.PREDECESSOR_PATH,
            CALIBRATION_PATH,
        ),
    )
    monkeypatch.setattr(execution, "ROOT", root)
    return root


def approval(value):
    return {
        "execution_plan_digest": value["execution_plan_digest"],
        "approved_caps": value["phase_caps"]["aggregate"],
        "approved_runtime_window_hours": value["runtime"]["proposed_aggregate_window_hours"],
        "subscription_execution_authorized": True,
        "incremental_experiment_charge_cap_usd": "0.00",
    }


def test_network_successor_is_reproducible_and_preserves_cohort_and_caps(recorded_root):
    value = plan()
    source_revision = value["source_binding"]["source_revision"]
    assert successor.execution_plan(recorded_root, source_revision=source_revision) == value
    previous = json.loads((ROOT / successor.PREDECESSOR_PATH).read_text())
    for field in ("phase_caps", "primary_comparison", "allocation", "runtime", "security_boundary"):
        assert value[field] == previous[field]
    assert value["execution_enabled"] is False
    assert value["subscription_execution_authorized"] is False
    assert value["approved_model_attempt_cap"] == 0
    jobs = validate_assignments(value)
    assert len(jobs) == 432
    assert all(job["trial_id"].startswith("d59-haiku-r3-") for job in jobs)
    assert not {job["trial_id"] for job in jobs}.intersection(
        job["trial_id"] for job in previous["primary_jobs"] + previous["reliability_jobs"]
    )
    identity = claude.ClaudeRuntimeIdentity(**value["runtime_identity"])
    manifests = execution.manifests(ROOT, value, identity)
    for manifest in manifests.values():
        assert manifest.max_model_attempts_per_action == 2
        assert dict(manifest.inference_parameters)["cli_api_retry_limit"] == "0"
        assert manifest.transport_retry_rule == "claude-one-stopped-timeout-or-connection-retry-v1"
    execution.validate_plan(value, approval(value))


@pytest.mark.parametrize(
    "field,value",
    [
        ("execution_plan_digest", "sha256:wrong"),
        ("approved_runtime_window_hours", 169),
        ("subscription_execution_authorized", False),
        ("incremental_experiment_charge_cap_usd", "1.00"),
        ("approved_caps", {}),
    ],
)
def test_execution_rejects_approval_drift(field, value):
    frozen = plan()
    changed = approval(frozen)
    changed[field] = value
    with pytest.raises(ValueError, match="approval must bind"):
        successor.validate_approval(frozen, changed)


def test_execution_rejects_modified_plan_even_with_updated_digest(recorded_root):
    value = plan()
    value["primary_jobs"][0]["seed"] += 1
    value["execution_plan_digest"] = content_digest(
        {k: v for k, v in value.items() if k != "execution_plan_digest"}
    )
    with pytest.raises(ValueError, match="differs from the committed candidate"):
        execution.validate_plan(value, approval(value))


def test_live_manifest_rejects_retry_rule_drift():
    value = deepcopy(plan())
    value["policy_manifests"]["history"]["transport_retry_rule"] = "unlimited"
    identity = claude.ClaudeRuntimeIdentity(**value["runtime_identity"])
    with pytest.raises(ValueError, match="live policies differ"):
        execution.manifests(ROOT, value, identity)


def test_preparation_rejects_changed_executor(recorded_root, monkeypatch):
    value = plan()
    original = Path.read_bytes

    def changed(path):
        if path == recorded_root / "scripts/run_grounding_v5_d59_haiku_network_retry.py":
            return b"changed"
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", changed)
    with pytest.raises(ValueError, match="execution source differs"):
        successor.execution_plan(
            recorded_root, source_revision=value["source_binding"]["source_revision"]
        )


def test_policy_input_mutation_at_recorded_revision_is_rejected(recorded_root):
    target = recorded_root / "pixelgym/env.py"
    target.write_bytes(target.read_bytes() + b"# mutated\n")
    with pytest.raises(ValueError, match="policy input differs from source revision"):
        successor.execution_plan(
            recorded_root, source_revision=plan()["source_binding"]["source_revision"]
        )
