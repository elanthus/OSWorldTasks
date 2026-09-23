"""Prepare or execute the exactly approved Haiku D5.9 retry successor."""

from __future__ import annotations

from scripts import d59_haiku_retry_execution as authorization
from scripts import run_grounding_v5_d59_haiku as runner


def main() -> None:
    """Run the shared fail-closed executor with the successor authorization contract."""

    runner.APPROVED_CAPS = authorization.APPROVED_CAPS
    runner.APPROVED_RUNTIME_HOURS = authorization.APPROVED_RUNTIME_HOURS
    runner.EXECUTION_PLAN_DIGEST = authorization.EXECUTION_PLAN_DIGEST
    runner.EXECUTION_PLAN_PATH = authorization.EXECUTION_PLAN_PATH
    runner.OWNER_APPROVAL_PATH = authorization.OWNER_APPROVAL_PATH
    runner.SUMMARY_SCHEMA_VERSION = "pixelgym-agent-v5-d59-haiku-execution-summary-v2"
    runner.CLI_API_RETRY_LIMIT = 0
    runner.execution_binding = authorization.execution_binding
    runner.validate_assignments = authorization.validate_assignments
    runner.validate_execution_authorization = (
        authorization.validate_execution_authorization
    )
    runner.validated_live_manifests = authorization.validated_live_manifests
    runner.main()


if __name__ == "__main__":
    main()
