"""Explicit S7 preparer wiring for a later, separately authorized activation.

Nothing imports this from the default bootstrap or configures approval automatically.
"""

from __future__ import annotations

from pathlib import Path

from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.platform.policy_subprocess import PolicyWorkerSpec
from pixelgym.platform.s7_budget import BudgetedScriptedTransport, S7SendBudget
from pixelgym.platform.s7_evidence import verify_packages, verify_seal
from pixelgym.platform.s7_qualification import read_json
from pixelgym.platform.s7_source import verify_worker_binding, worker_spec
from pixelgym.platform.stateful_contracts import StatefulPolicyPackage
from pixelgym.platform.stateful_control import StatefulCandidateRecord
from pixelgym.platform.stateful_runtime import (
    StatefulCandidatePreparer,
    StatefulLoadError,
    WorkerSpecPolicyLoader,
    run_contract_smoke,
)


def preparer(
    *, root: Path, qualification: Path, state_root: Path, budget: S7SendBudget
) -> StatefulCandidatePreparer:
    """Build guarded wiring; approval, serving terms, and CAS remain coordinator duties."""
    verify_seal(qualification)
    verify_packages(qualification)
    plan = read_json(qualification / "plan.json")
    source = read_json(qualification / "source-binding.json")
    lock = read_json(qualification / "runtime-lock.json")
    packages = {
        role: read_json(qualification / f"packages/{role}/package.json") for role in ("A", "B")
    }
    by_source = {
        content_digest(spec["worker"])[7:]: worker_spec(root, state_root, spec["worker"])
        for spec in plan["packages"].values()
    }

    def role_for(package: StatefulPolicyPackage) -> str:
        for role, expected in packages.items():
            if expected == package.to_dict():
                return role
        raise StatefulLoadError("S7 loader refuses a package outside the qualified exact bundle")

    def verify_source(package: StatefulPolicyPackage, spec: PolicyWorkerSpec) -> None:
        role = role_for(package)
        binding = plan["packages"][role]["worker"]
        verify_worker_binding(root, source, lock, binding)
        if spec != worker_spec(root, state_root, binding):
            raise StatefulLoadError("S7 worker specification differs from its source binding")

    def smoke(candidate: StatefulCandidateRecord) -> None:
        role = role_for(candidate.package)
        binding = plan["packages"][role]["worker"]
        verify_source(candidate.package, worker_spec(root, state_root, binding))
        run_contract_smoke(candidate, transport=BudgetedScriptedTransport(budget, phase="smoke"))

    loader = WorkerSpecPolicyLoader(
        worker_specs=by_source,
        transport_factory=lambda package: BudgetedScriptedTransport(
            budget, phase=role_for(package)
        ),
        source_verifier=verify_source,
        require_os_sandbox=True,
    )
    return StatefulCandidatePreparer(
        loader=loader, state_root=state_root, require_os_sandbox=True, contract_smoke=smoke
    )
