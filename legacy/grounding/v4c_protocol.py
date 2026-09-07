"""Frozen constants and episode specifications for the v4c longer-horizon pilot."""

from __future__ import annotations

import hashlib
from typing import Any

from pixelgym.serialization import canonical_json_bytes

V4C_PROTOCOL_VERSION = "pixelgym-grounding-v4c-pilot"
V4C_SEEDS = tuple(range(60, 70))
V4C_CONDITIONS = ("raw", "marks")
V4C_RECOVERY_SLACK = 2
V4C_CALL_CAP = 178
V4C_WIDTH = 1024
V4C_HEIGHT = 768
V4C_PIN_ID = "pin_reference"
V4C_SKIP_ID = "continue_without_pin"

_FAMILY_DECISIONS = {
    "pin_traverse_apply": 6,
    "reconcile_deferred_evidence": 7,
    "constraint_repair_chain": 8,
}
_FAMILY_ALLOCATION = {
    "pin_traverse_apply": 4,
    "reconcile_deferred_evidence": 3,
    "constraint_repair_chain": 3,
}
# The carrier value must stay invisible for at least three whole decisions between
# the commit stage (its last at-source appearance) and the consumer stage.
_MIN_INTERVENING_DECISIONS = 3


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


def _reference_carry(
    seed: int, vendor: str, reference: str, order_refs: tuple[str, str, str], target_slot: str
) -> dict[str, Any]:
    slots = ("a", "b", "c")
    return {
        "seed": seed,
        "family": "pin_traverse_apply",
        "title": f"Transfer {reference}",
        "carrier": {"label": "Pinned reference", "value": reference},
        "commit_stage": 0,
        "consumer_stage": 4,
        "stages": [
            _stage(
                "Capture the incoming reference",
                "Pin the incoming order reference so it stays available for later matching.",
                [
                    f"Incoming transfer notice from {vendor}.",
                    f"Order reference: {reference}.",
                    "The source notice closes when you leave this panel.",
                ],
                [
                    (V4C_PIN_ID, f"Pin reference {reference}"),
                    (V4C_SKIP_ID, "Continue without pinning"),
                    ("dismiss_notice", "Dismiss the notice"),
                ],
                V4C_PIN_ID,
            ),
            _stage(
                "Open the vendor workspace",
                "Open the vendor records workspace to continue the transfer.",
                [
                    "The transfer requires the vendor's order list.",
                    "Queue position: 1 of 1.",
                ],
                [
                    ("open_vendor_workspace", "Open vendor workspace"),
                    ("open_billing_center", "Open billing center"),
                    ("open_audit_log", "Open audit log"),
                ],
                "open_vendor_workspace",
            ),
            _stage(
                "Acknowledge the handling notice",
                "Acknowledge the routine handling notice to unlock the order list.",
                [
                    "Handling notice: transfers must be matched to an open order.",
                    "No values from this notice are required later.",
                ],
                [
                    ("acknowledge_notice", "Acknowledge notice"),
                    ("snooze_notice", "Snooze notice"),
                    ("forward_notice", "Forward to supervisor"),
                ],
                "acknowledge_notice",
            ),
            _stage(
                "Open the order list",
                "Open the open-orders list for matching.",
                [
                    "Open orders are grouped under the orders tab.",
                    "Closed orders cannot receive transfers.",
                ],
                [
                    ("open_order_list", "Open open-orders list"),
                    ("open_closed_orders", "Open closed orders"),
                    ("open_draft_orders", "Open draft orders"),
                ],
                "open_order_list",
            ),
            _stage(
                "Match the pinned reference",
                "Select the open order that matches the pinned reference.",
                [
                    "Three open orders can receive this transfer.",
                    "Only the order matching the pinned reference may be selected.",
                ],
                [
                    (f"select_order_{slot}", f"Select order {ref}")
                    for slot, ref in zip(slots, order_refs)
                ],
                f"select_order_{target_slot}",
            ),
            _stage(
                "Confirm the transfer",
                "Confirm the matched transfer now.",
                [
                    "The matched order is staged.",
                    "Confirmation records the transfer for host review.",
                ],
                [
                    ("confirm_transfer", "Confirm transfer"),
                    ("discard_match", "Discard the match"),
                    ("restart_matching", "Restart matching"),
                ],
                "confirm_transfer",
            ),
        ],
    }


def _reconcile_deferred(
    seed: int, vendor: str, ending: str, record_endings: tuple[str, str], resolution: str
) -> dict[str, Any]:
    ending_a, ending_b = record_endings
    return {
        "seed": seed,
        "family": "reconcile_deferred_evidence",
        "title": f"Duplicate review {vendor}",
        "carrier": {"label": "Pinned account ending", "value": ending},
        "commit_stage": 1,
        "consumer_stage": 5,
        "stages": [
            _stage(
                "Review the incoming record",
                "Open the incoming vendor record for review.",
                [
                    f"Incoming vendor: {vendor}.",
                    f"Declared bank account ending: {ending}.",
                    "Two stored records claim this vendor.",
                ],
                [
                    ("open_incoming_record", "Open incoming record"),
                    ("open_stored_records", "Open stored records"),
                    ("defer_review", "Defer the review"),
                ],
                "open_incoming_record",
            ),
            _stage(
                "Keep the declared account available",
                "Pin the declared account ending before leaving the incoming record.",
                [
                    f"Declared account ending: {ending}.",
                    "The incoming record closes when review moves on.",
                ],
                [
                    (V4C_PIN_ID, f"Pin account ending {ending}"),
                    (V4C_SKIP_ID, "Continue without pinning"),
                    ("print_record", "Print the record"),
                ],
                V4C_PIN_ID,
            ),
            _stage(
                "Open the stored duplicates",
                "Open the stored duplicate list.",
                [
                    "Two stored records require comparison.",
                    "Duplicates are listed under stored records.",
                ],
                [
                    ("open_duplicate_list", "Open duplicate list"),
                    ("open_change_history", "Open change history"),
                    ("open_vendor_contacts", "Open vendor contacts"),
                ],
                "open_duplicate_list",
            ),
            _stage(
                "Inspect the first stored record",
                "Open stored record A for inspection.",
                [
                    "Record A must be inspected before record B.",
                    "Inspection order is fixed by the review policy.",
                ],
                [
                    ("inspect_record_a", "Inspect stored record A"),
                    ("inspect_record_b", "Inspect stored record B"),
                    ("skip_inspection", "Skip inspection"),
                ],
                "inspect_record_a",
            ),
            _stage(
                "Inspect the second stored record",
                "Open stored record B for inspection.",
                [
                    "Record A has been inspected.",
                    "Record B is the remaining stored record.",
                ],
                [
                    ("inspect_record_b", "Inspect stored record B"),
                    ("reopen_record_a", "Reopen stored record A"),
                    ("close_review", "Close the review"),
                ],
                "inspect_record_b",
            ),
            _stage(
                "Resolve against the pinned account",
                "Keep the stored record whose account ending matches the pinned value.",
                [
                    "Both stored records were inspected.",
                    "At most one stored record can match the pinned account ending.",
                ],
                [
                    ("keep_record_a", f"Keep record A — account ending {ending_a}"),
                    ("keep_record_b", f"Keep record B — account ending {ending_b}"),
                    ("keep_neither_record", "Keep neither record"),
                ],
                resolution,
            ),
            _stage(
                "Confirm the resolution",
                "Confirm the recorded resolution.",
                [
                    "The resolution is staged for host review.",
                    "No further inspection is required.",
                ],
                [
                    ("confirm_resolution", "Confirm resolution"),
                    ("reopen_review", "Reopen the review"),
                    ("escalate_review", "Escalate to registry"),
                ],
                "confirm_resolution",
            ),
        ],
    }


def _constraint_repair(seed: int, submission: str, terms: str, target: str) -> dict[str, Any]:
    return {
        "seed": seed,
        "family": "constraint_repair_chain",
        "title": f"Submission repair {submission}",
        "carrier": {"label": "Pinned constraint", "value": terms},
        "commit_stage": 1,
        "consumer_stage": 6,
        "stages": [
            _stage(
                "Open the rejected submission",
                "Open the rejected vendor submission.",
                [
                    f"Submission {submission} was rejected by validation.",
                    f"Contract constraint: payment terms {terms}.",
                    "The constraint sheet is only visible on this panel.",
                ],
                [
                    ("open_submission", "Open rejected submission"),
                    ("open_templates", "Open templates"),
                    ("open_help_center", "Open help center"),
                ],
                "open_submission",
            ),
            _stage(
                "Keep the contract constraint available",
                "Pin the contract constraint before editing begins.",
                [
                    f"Contract constraint: payment terms {terms}.",
                    "Editing closes the constraint sheet.",
                ],
                [
                    (V4C_PIN_ID, f"Pin constraint {terms}"),
                    (V4C_SKIP_ID, "Continue without pinning"),
                    ("download_sheet", "Download the sheet"),
                ],
                V4C_PIN_ID,
            ),
            _stage(
                "Open the validation report",
                "Open the validation report for the rejection.",
                [
                    "The report lists one failing field.",
                    "Validation ran against the vendor contract.",
                ],
                [
                    ("open_validation_report", "Open validation report"),
                    ("open_activity_feed", "Open activity feed"),
                    ("open_attachments", "Open attachments"),
                ],
                "open_validation_report",
            ),
            _stage(
                "Select the failing field",
                "Select the field identified by the validation report.",
                [
                    "Failing field: payment terms.",
                    "All other fields passed validation.",
                ],
                [
                    ("select_payment_terms", "Payment terms"),
                    ("select_billing_email", "Billing email"),
                    ("select_bank_account", "Bank account"),
                ],
                "select_payment_terms",
            ),
            _stage(
                "Open the field editor",
                "Open the editor for the failing field.",
                [
                    "The failing field must be edited in the editor.",
                    "Direct list edits are disabled.",
                ],
                [
                    ("open_field_editor", "Open field editor"),
                    ("open_bulk_editor", "Open bulk editor"),
                    ("open_import_tool", "Open import tool"),
                ],
                "open_field_editor",
            ),
            _stage(
                "Unlock editing",
                "Unlock editing to change the field.",
                [
                    "The editor opens in read-only mode.",
                    "Unlocking applies only to the failing field.",
                ],
                [
                    ("unlock_editing", "Unlock editing"),
                    ("request_edit_access", "Request edit access"),
                    ("view_field_history", "View field history"),
                ],
                "unlock_editing",
            ),
            _stage(
                "Apply the pinned constraint",
                "Set the payment terms to the pinned contract constraint.",
                [
                    "Three term presets are offered.",
                    "Only the preset matching the pinned constraint passes validation.",
                ],
                [
                    ("set_terms_net15", "Set terms Net 15"),
                    ("set_terms_net30", "Set terms Net 30"),
                    ("set_terms_net45", "Set terms Net 45"),
                ],
                target,
            ),
            _stage(
                "Resubmit the repaired submission",
                "Send the repaired submission for validation now.",
                [
                    "The repaired submission is staged.",
                    "Resubmission sends it for host validation.",
                ],
                [
                    ("resubmit_submission", "Resubmit for validation"),
                    ("save_draft", "Save as draft"),
                    ("discard_changes", "Discard changes"),
                ],
                "resubmit_submission",
            ),
        ],
    }


V4C_EPISODES: tuple[dict[str, Any], ...] = (
    _reference_carry(60, "Harbor Optics", "PO-6084", ("PO-6084", "PO-6048", "PO-6804"), "a"),
    _reference_carry(61, "Cobalt Manufacturing", "PO-6137", ("PO-6173", "PO-6137", "PO-6317"), "b"),
    _reference_carry(62, "Juniper Freight", "PO-6259", ("PO-6295", "PO-6529", "PO-6259"), "c"),
    _reference_carry(63, "Meridian Paper", "PO-6320", ("PO-6302", "PO-6320", "PO-6230"), "b"),
    _reconcile_deferred(64, "Granite Tooling", "2044", ("2044", "1180"), "keep_record_a"),
    _reconcile_deferred(65, "Lakeshore Chemical", "5137", ("5731", "5137"), "keep_record_b"),
    _reconcile_deferred(66, "Pinnacle Logistics", "9402", ("9420", "4902"), "keep_neither_record"),
    _constraint_repair(67, "VS-4067", "Net 30", "set_terms_net30"),
    _constraint_repair(68, "VS-4068", "Net 45", "set_terms_net45"),
    _constraint_repair(69, "VS-4069", "Net 15", "set_terms_net15"),
)


def episode_for_seed(seed: int) -> dict[str, Any]:
    for episode in V4C_EPISODES:
        if episode["seed"] == seed:
            return episode
    raise ValueError("seed outside v4c pilot")


def episode_decisions(seed: int) -> int:
    return len(episode_for_seed(seed)["stages"])


def episode_max_actions(seed: int) -> int:
    return episode_decisions(seed) + V4C_RECOVERY_SLACK


def v4c_task_id(seed: int) -> str:
    episode = episode_for_seed(seed)
    return "v4c-" + hashlib.sha256(canonical_json_bytes(episode)).hexdigest()[:16]


def apply_v4c_click(
    episode: dict[str, Any], stage: int, pinned: bool, clicked_id: str | None
) -> tuple[int, bool, bool]:
    """The single frozen transition rule: (stage, pinned, recovery) after one click.

    Shared by the replay backend and the offline evidence replayer so the two can
    never drift apart.
    """
    stage_spec = episode["stages"][stage]
    if clicked_id == stage_spec["target"]:
        if stage == episode["commit_stage"]:
            pinned = True
        return stage + 1, pinned, False
    if stage == episode["commit_stage"] and clicked_id == V4C_SKIP_ID:
        # The preregistered progressing non-target: the workflow continues but
        # the carrier value never reaches the pinned chip.
        return stage + 1, pinned, False
    return stage, pinned, True


def _validate_deferred_dependency(episode: dict[str, Any]) -> None:
    commit = episode["commit_stage"]
    consumer = episode["consumer_stage"]
    value = episode["carrier"]["value"]
    stages = episode["stages"]
    if commit not in (0, 1):
        raise ValueError("v4c carrier must be visible at decision one or two")
    if consumer - commit < _MIN_INTERVENING_DECISIONS + 1 or consumer >= len(stages) - 1:
        raise ValueError("v4c consumer stage must follow at least three carrier-free decisions")
    commit_stage = stages[commit]
    if commit_stage["target"] != V4C_PIN_ID:
        raise ValueError("v4c commit stage target must be the pin control")
    commit_ids = {row["semantic_id"] for row in commit_stage["options"]}
    if V4C_SKIP_ID not in commit_ids:
        raise ValueError("v4c commit stage must offer the progressing skip control")
    if not any(value in fact for fact in commit_stage["facts"]):
        raise ValueError("v4c carrier value must appear in the commit stage facts")
    for index, stage in enumerate(stages):
        if index <= commit:
            continue
        if any(value in fact for fact in stage["facts"]):
            raise ValueError("v4c carrier value leaked into a post-commit stage's facts")
        if value in stage["heading"] or value in stage["instruction"]:
            raise ValueError("v4c carrier value leaked into a post-commit stage's text")
        if index != consumer and any(value in row["label"] for row in stage["options"]):
            raise ValueError("v4c carrier value leaked into a non-consumer stage's options")
        if index != commit and V4C_SKIP_ID in {row["semantic_id"] for row in stage["options"]}:
            raise ValueError("v4c skip control may exist only on the commit stage")
    consumer_stage = stages[consumer]
    if len(consumer_stage["options"]) < 3:
        raise ValueError("v4c consumer stage needs at least three plausible candidates")
    matches = [row for row in consumer_stage["options"] if value in row["label"]]
    if len(matches) > 1:
        raise ValueError("v4c consumer stage must contain at most one carrier match")
    if matches and matches[0]["semantic_id"] != consumer_stage["target"]:
        raise ValueError("v4c consumer target must be the option matching the carrier")
    if not matches:
        target_label = next(
            row["label"]
            for row in consumer_stage["options"]
            if row["semantic_id"] == consumer_stage["target"]
        )
        if any(character.isdigit() for character in target_label):
            raise ValueError("v4c no-match consumer target must be the value-free option")


def validate_v4c_protocol() -> dict[str, Any]:
    if len(V4C_EPISODES) != 10 or {row["seed"] for row in V4C_EPISODES} != set(V4C_SEEDS):
        raise ValueError("v4c requires exactly ten unique seeds 60-69")
    families: dict[str, int] = {}
    for episode in V4C_EPISODES:
        stages = episode.get("stages")
        expected_decisions = _FAMILY_DECISIONS.get(episode["family"])
        if expected_decisions is None:
            raise ValueError(f"unknown v4c family {episode['family']!r}")
        if not isinstance(stages, list) or len(stages) != expected_decisions:
            raise ValueError("v4c episode decision count does not match its family")
        families[episode["family"]] = families.get(episode["family"], 0) + 1
        for stage in stages:
            candidates = [row["semantic_id"] for row in stage["options"]]
            if len(candidates) != len(set(candidates)) or stage["target"] not in candidates:
                raise ValueError("each v4c stage needs unique candidates containing its target")
            if len(candidates) < 3:
                raise ValueError("each v4c stage needs at least three candidates")
        _validate_deferred_dependency(episode)
    if families != _FAMILY_ALLOCATION:
        raise ValueError("v4c family allocation must be exactly 4/3/3")
    per_condition = sum(len(row["stages"]) + V4C_RECOVERY_SLACK for row in V4C_EPISODES)
    if per_condition != 89 or per_condition * len(V4C_CONDITIONS) != V4C_CALL_CAP:
        raise ValueError("v4c call cap no longer equals 178")
    return {
        "episode_count": 10,
        "family_counts": families,
        "call_cap": V4C_CALL_CAP,
    }
