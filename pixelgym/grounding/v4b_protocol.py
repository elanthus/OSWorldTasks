"""Frozen constants and episode specifications for the v4b closed-loop pilot."""

from __future__ import annotations

import hashlib
from typing import Any

from pixelgym.serialization import canonical_json_bytes

V4B_PROTOCOL_VERSION = "pixelgym-grounding-v4b-pilot"
V4B_SEEDS = tuple(range(40, 50))
V4B_CONDITIONS = ("raw", "marks")
V4B_MAX_ACTIONS = 4
V4B_CALL_CAP = 80
V4B_WIDTH = 1024
V4B_HEIGHT = 768


def _stage(
    heading: str,
    instruction: str,
    facts: list[str],
    options: list[tuple[str, str]],
    target: str,
) -> dict[str, Any]:
    return {
        "heading": heading,
        "instruction": instruction,
        "facts": facts,
        "options": [{"semantic_id": semantic_id, "label": label} for semantic_id, label in options],
        "target": target,
    }


def _triage(seed: int, request: str, amount: str, risk: str, route: str) -> dict[str, Any]:
    requests = [
        ("open_northstar", "Open Northstar Paper — Pending — $18,200"),
        ("open_redwood", "Open Redwood Labs — Pending — $74,600"),
        ("open_kinetic", "Open Kinetic Freight — Approved — $91,300"),
        ("open_summit", "Open Summit Health — Pending — $42,900"),
    ]
    route_options = [
        ("route_standard", "Route: Standard"),
        ("route_finance", "Route: Finance"),
        ("route_compliance", "Route: Compliance"),
    ]
    return {
        "seed": seed,
        "family": "triage_route_confirm",
        "title": f"Request {request}",
        "stages": [
            _stage(
                "Triage the queue",
                "Open the pending request with the highest amount.",
                ["Ignore approved requests.", "Compare the visible pending amounts."],
                requests,
                "open_redwood",
            ),
            _stage(
                "Apply the approval policy",
                "Choose the required route for the opened request.",
                [
                    f"Opened: Redwood Labs · {amount} · {risk} risk",
                    "Policy: high risk → Compliance",
                    "Otherwise amount over $25,000 → Finance; all others → Standard",
                ],
                route_options,
                f"route_{route}",
            ),
            _stage(
                "Confirm the routing decision",
                f"Confirm the displayed {route.title()} route.",
                ["Request: Redwood Labs", f"Selected route: {route.title()}"],
                [
                    ("confirm_route", "Confirm route"),
                    ("return_to_queue", "Return to queue"),
                    ("escalate_request", "Escalate for review"),
                ],
                "confirm_route",
            ),
        ],
    }


def _reconcile(seed: int, record: str, inspect: str, resolution: str) -> dict[str, Any]:
    return {
        "seed": seed,
        "family": "reconcile_inspect_resolve",
        "title": f"Duplicate review {record}",
        "stages": [
            _stage(
                "Reconcile matching records",
                "Inspect the record whose tax ID matches the incoming request.",
                [
                    "Incoming: tax ID GB-77109 · bank ending 2044",
                    "Atlas Supply: GB-77109 · bank ending 2044",
                    "Atlas Services: GB-77019 · bank ending 1180",
                    "Atlassian Parts: IE-77109 · bank ending 6021",
                ],
                [
                    ("inspect_atlas_supply", "Inspect Atlas Supply"),
                    ("inspect_atlas_services", "Inspect Atlas Services"),
                    ("inspect_atlassian_parts", "Inspect Atlassian Parts"),
                ],
                "inspect_atlas_supply",
            ),
            _stage(
                "Inspect the conflict",
                "Use the evidence named by the conflict notice.",
                [
                    f"Selected: Atlas Supply · {record}",
                    f"Conflict notice: verify the {inspect.replace('_', ' ')} before resolving.",
                ],
                [
                    ("view_source_document", "View source document"),
                    ("view_audit_trail", "View audit trail"),
                    ("compare_fields", "Compare fields"),
                ],
                inspect,
            ),
            _stage(
                "Resolve the conflict",
                "Choose the safe resolution described by the inspected evidence.",
                [
                    f"Evidence: {resolution.replace('_', ' ')} is required.",
                    "No changes have been applied yet.",
                ],
                [
                    ("keep_current", "Keep current record"),
                    ("apply_requested", "Apply requested values"),
                    ("merge_verified", "Merge verified fields"),
                ],
                resolution,
            ),
        ],
    }


def _diagnose(seed: int, field: str, repair: str) -> dict[str, Any]:
    labels = {
        "select_billing_email": "Billing email",
        "select_vat_number": "VAT number",
        "select_bank_account": "Bank account",
    }
    return {
        "seed": seed,
        "family": "diagnose_repair_resubmit",
        "title": f"Submission repair {seed}",
        "stages": [
            _stage(
                "Diagnose the failed submission",
                "Select the field identified by the validation message.",
                [f"Validation failed: {labels[field]} has an invalid format."],
                [
                    ("select_billing_email", "Billing email"),
                    ("select_vat_number", "VAT number"),
                    ("select_bank_account", "Bank account"),
                ],
                field,
            ),
            _stage(
                "Apply the offered repair",
                "Choose the repair explicitly recommended below.",
                [f"Recommended repair: {repair.replace('_', ' ')}."],
                [
                    ("replace_invalid_value", "Replace invalid value"),
                    ("clear_field", "Clear the field"),
                    ("request_exemption", "Request an exemption"),
                ],
                repair,
            ),
            _stage(
                "Submit the repaired request",
                "Send the repaired request for validation now.",
                ["Repair preview: valid", "No other validation messages remain."],
                [
                    ("resubmit_request", "Resubmit request"),
                    ("save_draft", "Save draft"),
                    ("cancel_changes", "Cancel changes"),
                ],
                "resubmit_request",
            ),
        ],
    }


V4B_EPISODES: tuple[dict[str, Any], ...] = (
    _triage(40, "VR-3040", "$74,600", "medium", "finance"),
    _triage(41, "VR-3041", "$74,600", "high", "compliance"),
    _triage(42, "VR-3042", "$24,600", "low", "standard"),
    _triage(43, "VR-3043", "$54,600", "medium", "finance"),
    _reconcile(44, "bank conflict", "view_audit_trail", "keep_current"),
    _reconcile(45, "address conflict", "view_source_document", "apply_requested"),
    _reconcile(46, "mixed conflict", "compare_fields", "merge_verified"),
    _diagnose(47, "select_billing_email", "replace_invalid_value"),
    _diagnose(48, "select_vat_number", "clear_field"),
    _diagnose(49, "select_bank_account", "request_exemption"),
)


def episode_for_seed(seed: int) -> dict[str, Any]:
    for episode in V4B_EPISODES:
        if episode["seed"] == seed:
            return episode
    raise ValueError("seed outside v4b pilot")


def v4b_task_id(seed: int) -> str:
    episode = episode_for_seed(seed)
    return "v4b-" + hashlib.sha256(canonical_json_bytes(episode)).hexdigest()[:16]


def validate_v4b_protocol() -> dict[str, Any]:
    if len(V4B_EPISODES) != 10 or {row["seed"] for row in V4B_EPISODES} != set(V4B_SEEDS):
        raise ValueError("v4b requires exactly ten unique seeds 40-49")
    families: dict[str, int] = {}
    for episode in V4B_EPISODES:
        stages = episode.get("stages")
        if not isinstance(stages, list) or len(stages) != 3:
            raise ValueError("every v4b episode must contain exactly three stages")
        families[episode["family"]] = families.get(episode["family"], 0) + 1
        for stage in stages:
            candidates = [row["semantic_id"] for row in stage["options"]]
            if len(candidates) != len(set(candidates)) or stage["target"] not in candidates:
                raise ValueError("each v4b stage needs unique candidates containing its target")
    expected = {
        "triage_route_confirm": 4,
        "reconcile_inspect_resolve": 3,
        "diagnose_repair_resubmit": 3,
    }
    if families != expected:
        raise ValueError("v4b family allocation must be exactly 4/3/3")
    if len(V4B_SEEDS) * len(V4B_CONDITIONS) * V4B_MAX_ACTIONS != V4B_CALL_CAP:
        raise ValueError("v4b call cap no longer equals 80")
    return {"episode_count": 10, "family_counts": families, "call_cap": V4B_CALL_CAP}
