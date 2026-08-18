"""Frozen JSON Schema loading and authoritative platform validation boundaries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

from pixelgym.platform.contracts import GatePolicy

CONTRACT_SCHEMA_FILES = {
    "run_manifest": "run-manifest.schema.json",
    "gate_policy": "promotion-gates.schema.json",
    "gate_report": "gate-report.schema.json",
    "policy_package": "platform-policy.schema.json",
    "raw_response": "raw-response.schema.json",
    "approval": "control-events.schema.json",
    "deployment": "control-events.schema.json",
    "audit_event": "control-events.schema.json",
}
CONTROL_EVENT_DEFINITIONS = {
    "approval": "approval",
    "deployment": "deployment",
    "audit_event": "audit",
}
PRICE_CATALOG_SCHEMA_FILE = "price-catalog.schema.json"
REGISTRY_SCHEMA_FILE = "platform-contracts.schema.json"


class ContractValidationError(ValueError):
    """A loaded or generated platform document violates its frozen contract."""


def _reject_nonstandard_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON numeric constant is forbidden: {value}")


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_nonstandard_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ContractValidationError(f"configuration is not strict JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise ContractValidationError(f"configuration must be a JSON object: {path.name}")
    return value


class PlatformSchemas:
    """Load checked-in schemas once and validate exact contract-specific shapes."""

    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()
        self.schema_root = self.repository_root / "config"
        self._schemas: dict[str, dict[str, Any]] = {}
        self._validators: dict[str, Draft202012Validator] = {}
        self._load_and_check_inventory()

    def _schema(self, filename: str) -> dict[str, Any]:
        if filename not in self._schemas:
            schema = _load_json_object(self.schema_root / filename)
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError as exc:
                raise ContractValidationError(f"invalid committed JSON Schema: {filename}") from exc
            self._schemas[filename] = schema
        return self._schemas[filename]

    def _load_and_check_inventory(self) -> None:
        registry = self._schema(REGISTRY_SCHEMA_FILE)
        try:
            versions = registry["properties"]["contracts"]["const"]
        except (KeyError, TypeError) as exc:
            raise ContractValidationError("platform contract registry has no frozen inventory") from exc
        if not isinstance(versions, dict) or set(versions) != set(CONTRACT_SCHEMA_FILES):
            raise ContractValidationError("platform contract registry and schema inventory differ")
        for filename in {*CONTRACT_SCHEMA_FILES.values(), PRICE_CATALOG_SCHEMA_FILE}:
            self._schema(filename)

    @property
    def versions(self) -> dict[str, str]:
        registry = self._schema(REGISTRY_SCHEMA_FILE)
        return dict(registry["properties"]["contracts"]["const"])

    def _validator(self, contract: str) -> Draft202012Validator:
        if contract not in CONTRACT_SCHEMA_FILES:
            raise KeyError(f"unknown platform contract: {contract}")
        if contract not in self._validators:
            root_schema = self._schema(CONTRACT_SCHEMA_FILES[contract])
            definition = CONTROL_EVENT_DEFINITIONS.get(contract)
            schema = root_schema if definition is None else root_schema["$defs"][definition]
            self._validators[contract] = Draft202012Validator(
                schema,
                format_checker=FormatChecker(),
            )
        return self._validators[contract]

    def validate(self, contract: str, value: object) -> None:
        errors = sorted(
            self._validator(contract).iter_errors(value),
            key=lambda error: (
                tuple(str(component) for component in error.absolute_path),
                error.message,
            ),
        )
        if errors:
            error = errors[0]
            location = error.json_path
            raise ContractValidationError(
                f"{contract} violates its frozen schema at {location}: {error.message}"
            )
        if contract == "audit_event":
            assert isinstance(value, dict)
            try:
                serialized_details = json.loads(
                    value["details_json"],
                    parse_constant=_reject_nonstandard_constant,
                )
            except (json.JSONDecodeError, ValueError) as exc:
                raise ContractValidationError(
                    "audit_event details_json is not strict JSON"
                ) from exc
            if serialized_details != value["details"]:
                raise ContractValidationError(
                    "audit_event details_json does not match parsed details"
                )

    def validate_price_catalog(self, value: object) -> None:
        validator = Draft202012Validator(
            self._schema(PRICE_CATALOG_SCHEMA_FILE),
            format_checker=FormatChecker(),
        )
        errors = sorted(
            validator.iter_errors(value),
            key=lambda error: (
                tuple(str(component) for component in error.absolute_path),
                error.message,
            ),
        )
        if errors:
            error = errors[0]
            raise ContractValidationError(
                f"price_catalog violates its frozen schema at {error.json_path}: {error.message}"
            )
        assert isinstance(value, dict)
        entries = value["entries"]
        identities = [(entry["provider"], entry["model"]) for entry in entries]
        if len(identities) != len(set(identities)):
            raise ContractValidationError("price_catalog contains duplicate provider/model entries")


def load_gate_policy(repository_root: Path, path: Path | None = None) -> GatePolicy:
    """Validate gate configuration before constructing its typed runtime contract."""
    target = path or repository_root / "config/promotion-gates.demo-v1.json"
    value = _load_json_object(target)
    PlatformSchemas(repository_root).validate("gate_policy", value)
    try:
        return GatePolicy(**value)
    except (TypeError, ValueError) as exc:
        raise ContractValidationError("gate_policy violates typed runtime invariants") from exc


def load_price_catalog(repository_root: Path, path: Path | None = None) -> dict[str, Any]:
    """Validate price configuration before provider or scoring work can start."""
    target = path or repository_root / "config/price-catalog.demo-v1.json"
    value = _load_json_object(target)
    PlatformSchemas(repository_root).validate_price_catalog(value)
    return value
