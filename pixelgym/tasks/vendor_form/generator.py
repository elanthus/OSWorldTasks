"""Seeded generator for the deterministic vendor-onboarding task (D1.3).

All output derives only from an integer seed via ``random.Random(seed)``, so
identical seeds always produce byte-identical canonical JSON. Values are
deliberately, obviously fictional (``.example`` domains, the reserved ``555``
telephone exchange) so nothing here resembles a real vendor.
"""

from __future__ import annotations

import hashlib
import json
import random
from typing import Any

FIELD_NAMES: tuple[str, ...] = (
    "company_name",
    "contact_email",
    "contact_phone",
    "tax_id",
    "country",
    "payment_terms",
    "expedited_onboarding",
)

_SCHEMA_VERSION = 1

_COMPANY_PREFIXES: tuple[str, ...] = (
    "Blue Harbor",
    "North Ridge",
    "Silver Creek",
    "Amber Field",
    "Copper Vale",
    "Granite Bay",
    "Cedar Hollow",
    "Marble Point",
)
_COMPANY_SUFFIXES: tuple[str, ...] = (
    "Supply Co.",
    "Logistics Group",
    "Manufacturing Ltd.",
    "Trading Partners",
    "Industries Inc.",
    "Distribution LLC",
)
_COUNTRY_OPTIONS: tuple[str, ...] = (
    "Australia",
    "Brazil",
    "Canada",
    "Germany",
    "Japan",
    "Kenya",
)
_PAYMENT_TERMS_OPTIONS: tuple[str, ...] = ("Net 15", "Net 30", "Net 45")


def _slugify(name: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in name).strip("-")


def _spec_body(seed: int) -> dict[str, Any]:
    rng = random.Random(seed)

    company_name = f"{rng.choice(_COMPANY_PREFIXES)} {rng.choice(_COMPANY_SUFFIXES)}"
    domain = f"{_slugify(company_name)}.example"
    contact_phone = f"+1-555-{rng.randint(0, 999):03d}-{rng.randint(0, 9999):04d}"
    tax_id = "TAX-" + "".join(rng.choices("0123456789ABCDEFGHJKLMNPQRSTUVWXYZ", k=8))

    fields = {
        "company_name": company_name,
        "contact_email": f"onboarding@{domain}",
        "contact_phone": contact_phone,
        "tax_id": tax_id,
        "country": rng.choice(_COUNTRY_OPTIONS),
        "payment_terms": rng.choice(_PAYMENT_TERMS_OPTIONS),
        "expedited_onboarding": rng.choice([True, False]),
    }

    return {
        "schema_version": _SCHEMA_VERSION,
        "seed": seed,
        "fields": fields,
        "options": {
            "country": list(_COUNTRY_OPTIONS),
            "payment_terms": list(_PAYMENT_TERMS_OPTIONS),
        },
    }


def canonical_json(spec_body: dict[str, Any]) -> str:
    """Canonical, order-independent JSON serialization used for hashing and diffing."""
    return json.dumps(spec_body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def task_id_for(spec_body: dict[str, Any]) -> str:
    digest = hashlib.sha256(canonical_json(spec_body).encode("utf-8")).hexdigest()
    return f"vf-{digest[:16]}"


def generate_task(seed: int) -> dict[str, Any]:
    """Generate the full task record (canonical spec body plus its derived task_id)."""
    body = _spec_body(seed)
    return {"task_id": task_id_for(body), **body}
