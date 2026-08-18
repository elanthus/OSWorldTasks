"""Frozen JSON Schema loading and authoritative platform validation boundaries."""

from __future__ import annotations

import json
import math
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

from pixelgym.platform.contracts import GatePolicy, PolicyManifest
from pixelgym.platform.fingerprints import canonical_json_bytes, sha256_bytes

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
LEGACY_POLICY_SCHEMA_FILE = "platform-policy.legacy-v1.schema.json"


class ContractValidationError(ValueError):
    """A loaded or generated platform document violates its frozen contract."""


def _reject_nonstandard_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON numeric constant is forbidden: {value}")


def _load_json_object(path: Path | Traversable) -> dict[str, Any]:
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


def _validate_strict_json(value: object, *, label: str, path: str = "$") -> None:
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ContractValidationError(
                f"{label} contains a non-finite JSON number at {path}"
            )
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_strict_json(item, label=label, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractValidationError(
                    f"{label} contains a non-string JSON object key at {path}"
                )
            _validate_strict_json(item, label=label, path=f"{path}.{key}")
        return
    raise ContractValidationError(
        f"{label} contains a non-JSON {type(value).__name__} at {path}"
    )


class PlatformSchemas:
    """Load checked-in schemas once and validate exact contract-specific shapes."""

    def __init__(self, repository_root: Path | None = None) -> None:
        self.repository_root = repository_root.resolve() if repository_root else None
        if self.repository_root is not None:
            self.schema_root: Path | Traversable = self.repository_root / "config"
        else:
            packaged = resources.files("pixelgym.platform").joinpath("schemas")
            self.schema_root = (
                packaged
                if packaged.joinpath(REGISTRY_SCHEMA_FILE).is_file()
                else Path(__file__).resolve().parents[2] / "config"
            )
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
        for filename in {
            *CONTRACT_SCHEMA_FILES.values(),
            PRICE_CATALOG_SCHEMA_FILE,
            LEGACY_POLICY_SCHEMA_FILE,
        }:
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
        _validate_strict_json(value, label=contract)
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
        _validate_strict_json(value, label="price_catalog")
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

    def validate_legacy_policy(self, value: object) -> None:
        _validate_strict_json(value, label="legacy_policy_package")
        validator = Draft202012Validator(self._schema(LEGACY_POLICY_SCHEMA_FILE))
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
                "legacy_policy_package violates its frozen schema "
                f"at {error.json_path}: {error.message}"
            )


def load_policy_manifest(schemas: PlatformSchemas, value: object) -> PolicyManifest:
    """Decode current or formerly schema-valid v1 policy evidence without changing identity."""
    try:
        schemas.validate("policy_package", value)
        current = value
    except ContractValidationError as current_error:
        try:
            schemas.validate_legacy_policy(value)
        except ContractValidationError:
            raise current_error
        assert isinstance(value, dict)
        current = {
            **value,
            "code_state": "unverifiable",
            "source_tree_sha256": None,
            "source_provenance_verified": False,
            "source_provenance_failure_reason": "legacy_schema_missing_provenance",
        }
        schemas.validate("policy_package", current)
    assert isinstance(current, dict)
    try:
        policy = PolicyManifest(**current)
    except (TypeError, ValueError) as exc:
        raise ContractValidationError(
            "policy_package violates typed runtime invariants"
        ) from exc
    expected = "sha256:" + sha256_bytes(canonical_json_bytes(policy.identity_dict()))
    if policy.policy_id != expected:
        raise ContractValidationError("policy_package identity digest does not verify")
    return policy


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
