"""Prepare or verify the no-call D5.9 replication freeze candidate.

Without ``--verify`` the installed CLI is bound to the replication route with
``claude --version`` and ``claude --help`` only; no login and no provider call. With
``--verify`` the committed candidate is rebuilt from its own recorded identity and compared
byte for byte.

Generation defaults to the v1 rule. A future v2 successor requires an explicit version,
failure-streak scope, and policy-violation disposition. These options prepare a candidate;
they do not approve execution. Verification always reads the stored candidate's choices.
"""

import argparse
import json
from pathlib import Path

from pixelgym.grounding.v5 import claude_code_policy as claude
from pixelgym.grounding.v5.d59_replication import (
    OUTPUT_DIRECTORY,
    STOP_RULE_VERSION,
    execution_plan,
    stop_rule_options_for_plan,
)
from pixelgym.grounding.v5.replication_stop_rules import (
    PROPOSED_STOP_RULE_VERSION,
    ProposedStopRule,
)
from pixelgym.serialization import canonical_json_bytes
from scripts.prepare_grounding_v5_d59_haiku_freeze import write_outputs

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument(
        "--stop-rule-version", choices=(STOP_RULE_VERSION, PROPOSED_STOP_RULE_VERSION)
    )
    parser.add_argument("--failure-streak-scope", choices=("per_worker", "non_overlapping"))
    parser.add_argument("--policy-violation-action", choices=("stop", "continue"))
    parser.add_argument("--consecutive-request-failure-limit", type=int)
    parser.add_argument("--rate-limit-backoff-seconds", type=float)
    parser.add_argument("--maximum-rate-limit-backoff-seconds", type=float)
    args = parser.parse_args()
    option_names = (
        "failure_streak_scope",
        "policy_violation_action",
        "consecutive_request_failure_limit",
        "rate_limit_backoff_seconds",
        "maximum_rate_limit_backoff_seconds",
    )
    selected_options = {
        name: getattr(args, name) for name in option_names if getattr(args, name) is not None
    }
    if args.verify and (args.stop_rule_version is not None or selected_options):
        parser.error(
            "--verify infers the stop rule from the stored candidate; omit stop-rule options"
        )
    if args.stop_rule_version != PROPOSED_STOP_RULE_VERSION and selected_options:
        parser.error("amendment options require --stop-rule-version " + PROPOSED_STOP_RULE_VERSION)
    stop_options = None
    if args.stop_rule_version == PROPOSED_STOP_RULE_VERSION:
        if args.failure_streak_scope is None or args.policy_violation_action is None:
            parser.error(
                "v2 requires explicit --failure-streak-scope and --policy-violation-action"
            )
        try:
            stop_options = ProposedStopRule(**selected_options)
        except ValueError as exc:
            parser.error(str(exc))
    public = ROOT / OUTPUT_DIRECTORY
    if args.verify:
        stored = json.loads((public / "execution-plan.json").read_text(encoding="utf-8"))
        identity = claude.ClaudeRuntimeIdentity(**stored["runtime_identity"])
        stop_options = stop_rule_options_for_plan(stored)
    else:
        identity = claude.probe_claude_route(claude.REPLICATION_ROUTE)
    plan = execution_plan(
        ROOT,
        source_revision=args.source_revision,
        runtime_identity=identity,
        stop_rule_options=stop_options,
    )
    write_outputs(
        {"execution-plan.json": canonical_json_bytes(plan) + b"\n"},
        verify=args.verify,
        public=public,
    )
    print(
        json.dumps(
            {
                "execution_plan_digest": plan["execution_plan_digest"],
                "history_policy_id": plan["policy_manifests"]["history"]["policy_id"],
                "stateless_policy_id": plan["policy_manifests"]["stateless"]["policy_id"],
                "pilot_assignments": len(plan["pilot"]["jobs"]),
                "full_assignments": len(plan["primary_jobs"]) + len(plan["reliability_jobs"]),
                "execution_enabled": plan["execution_enabled"],
                "provider_calls_made": 0,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
