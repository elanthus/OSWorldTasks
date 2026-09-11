"""Workload-sized Gemini request reservations without changing policy observations."""

from __future__ import annotations

import base64
import copy
import io
import json
from dataclasses import replace
from decimal import Decimal
from typing import Any

from PIL import Image

from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.memory_calibration import MemoryCalibrationLedger
from pixelgym.grounding.v5.memory_plan import ScreenshotPriceConfig
from pixelgym.grounding.v5.panel_policy import MAX_OUTPUT_TOKENS, OpenRouterPanelTransport
from pixelgym.grounding.v5.runner import TransportOutcome

IMAGE_TOKEN_ALLOWANCE = 4096
FRAMING_TOKEN_ALLOWANCE = 8192
MAX_WORKLOAD_INPUT_TOKENS = 163840
BUDGET_VERSION = "gemini-png-request-budget-v1"


def request_bound(request: dict[str, Any], config: ScreenshotPriceConfig) -> dict[str, Any]:
    """Conservative estimate: padded media allowance, text bytes and framing slack.

    Google documents up to 2240 tokens/image for Gemini 3 ultra-high resolution.
    Reserve 4096 per unchanged 1024x768 PNG, plus one token per serialized UTF-8
    metadata byte and 8192 framing tokens. This is a disclosed workload assumption,
    not a provider tokenizer receipt; a priced response above it blocks the ledger.
    """
    if set(request) - {
        "model",
        "messages",
        "response_format",
        "provider",
        "seed",
        "max_tokens",
        "temperature",
        "reasoning",
    }:
        raise ValueError("unsupported request feature has no billing bound")
    if request.get("max_tokens") != MAX_OUTPUT_TOKENS:
        raise ValueError("output limit differs from the frozen request bound")
    value = copy.deepcopy(request)
    messages = value["messages"]
    if len(messages) != 2 or [m["role"] for m in messages] != ["system", "user"]:
        raise ValueError("request must have the frozen two-message structure")
    if not isinstance(messages[0]["content"], str) or not isinstance(messages[1]["content"], list):
        raise TypeError("unsupported message content")
    images = 0
    for part in messages[1]["content"]:
        if (
            part.get("type") == "text"
            and set(part) == {"type", "text"}
            and isinstance(part["text"], str)
        ):
            continue
        if (
            part.get("type") != "image_url"
            or set(part) != {"type", "image_url"}
            or set(part["image_url"]) != {"url"}
        ):
            raise ValueError("unsupported media content")
        url = part["image_url"]["url"]
        prefix = "data:image/png;base64,"
        if not isinstance(url, str) or not url.startswith(prefix) or len(url) > 5_000_000:
            raise ValueError("only bounded inline PNG observations are supported")
        with Image.open(io.BytesIO(base64.b64decode(url[len(prefix) :], validate=True))) as image:
            if image.format != "PNG" or image.size != (1024, 768):
                raise ValueError("observation dimensions differ from the admitted renderer")
            image.verify()
        images += 1
        part["image_url"]["url"] = ""
    if not 1 <= images <= 32:
        raise ValueError("image count exceeds the frozen policy")
    metadata_bytes = len(json.dumps(value, ensure_ascii=False, sort_keys=True).encode())
    inputs = images * IMAGE_TOKEN_ALLOWANCE + metadata_bytes + FRAMING_TOKEN_ALLOWANCE
    if inputs > MAX_WORKLOAD_INPUT_TOKENS:
        raise ValueError("request exceeds the admitted workload input allowance")
    maximum = (
        inputs * config.prompt_price_per_token_usd
        + MAX_OUTPUT_TOKENS * config.completion_price_per_token_usd
    )
    return {
        "version": BUDGET_VERSION,
        "request_digest": content_digest(request),
        "images": images,
        "metadata_bytes": metadata_bytes,
        "input_units_bound": inputs,
        "output_units_limit": MAX_OUTPUT_TOKENS,
        "request_maximum_usd": str(maximum),
    }


class ReboundedMemoryLedger(MemoryCalibrationLedger):
    """Replay append-only reductions of unknown holds, keeping them unknown."""

    def _replay_journal(self, journal: V5AttemptJournal) -> None:
        super()._replay_journal(journal)
        for event in journal.events():
            if event.kind != "spend_unknown_bound_revised":
                continue
            p = event.payload
            rid = p["reservation_id"]
            before, after = Decimal(p["old_bound_usd"]), Decimal(p["new_bound_usd"])
            self._validate_amount(after, name="revised unknown bound", allow_zero=False)
            if after > before or rid not in self._settlements:
                raise ValueError("invalid unknown bound revision")
            current = self._settlements[rid]
            if current[0] == "unknown":
                if current[1] != before:
                    raise ValueError("unknown bound revision has a different prior amount")
                self._settlements[rid] = ("unknown", after)
                self.unknown_reservation_usd += after - before

    def revise_unknown_bound(self, proof: dict[str, Any], *, plan_digest: str) -> None:
        if self.journal is None:
            raise ValueError("bound revision requires a durable journal")
        rid = proof["reservation_id"]
        payload = {**proof, "execution_plan_digest": plan_digest}
        key = f"spend/{rid}/revised-bound"
        with self._lock:
            existing = self.journal.event(key)
            if existing:
                if existing.payload != payload:
                    raise ValueError("existing bound revision has a different approval")
                return
            current = self._settlements.get(rid)
            after = Decimal(proof["new_bound_usd"])
            before = Decimal(proof["old_bound_usd"])
            if current != ("unknown", before) or not 0 < after <= before:
                raise ValueError("revision does not match an existing unknown hold")
            self.journal.append_event(
                event_key=key,
                kind="spend_unknown_bound_revised",
                trial_id="__spend_ledger__",
                step_index=0,
                payload=payload,
            )
            self._settlements[rid] = ("unknown", after)
            self.unknown_reservation_usd += after - before


class RequestBoundTransport(OpenRouterPanelTransport):
    def send(
        self, request: dict[str, Any], *, idempotency_key: str, deadline_seconds: float
    ) -> TransportOutcome:
        original = self.config
        if not isinstance(original, ScreenshotPriceConfig):
            raise TypeError("workload budgeting requires screenshot price config")
        try:
            proof = request_bound(request, original)
        except (ValueError, KeyError, TypeError) as exc:
            return TransportOutcome(
                "pre_send_failure", failure_code=f"request_bound_{type(exc).__name__}"
            )
        if self.ledger.journal is None:
            raise ValueError("request bounds require a durable journal")
        rid = content_digest(idempotency_key)
        self.ledger.journal.append_event(
            event_key=f"spend/{rid}/request-bound",
            kind="request_budget_bound",
            trial_id="__spend_ledger__",
            step_index=0,
            payload={**proof, "reservation_id": rid},
        )
        self.config = replace(original, upstream_context_length=proof["input_units_bound"])
        try:
            return super().send(
                request, idempotency_key=idempotency_key, deadline_seconds=deadline_seconds
            )
        finally:
            self.config = original

    def settle_unknown_spend(self, *, idempotency_key: str) -> None:
        if self.ledger.has_in_flight_reservation(idempotency_key):
            rid = content_digest(idempotency_key)
            event = (
                self.ledger.journal.event(f"spend/{rid}/request-bound")
                if self.ledger.journal
                else None
            )
            if event is None:
                raise ValueError("in-flight request lacks its recorded budget bound")
            self.ledger.reserve_unknown_charge(
                idempotency_key, Decimal(event.payload["request_maximum_usd"])
            )
