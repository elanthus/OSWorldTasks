"""Approved real-provider policies for the frozen grounding evaluation flow.

The evaluation flow defaults to the no-cost scripted replay provider. This module adds the
only other path it accepts: a *preconfigured, allowlisted* provider policy read from a
checked-in registry, resolved by an opaque reference, and executed only when the launcher
supplies the exact SHA-256 approval digest of that registry record.

Design boundaries, in the order they matter:

* Registry values are data, never code. A record names a transport from a fixed table,
  a model string, scalar request parameters, and the *name* of the credential environment
  variable. Nothing in a record is interpreted as a path, module, command, flow name, or
  shell fragment, and unknown keys are rejected.
* Approval is content-bound. ``ApprovedProviderPolicy.approval_sha256`` digests the canonical
  record, so editing any field (model, cap, prices, routing, prompt) invalidates a previous
  approval. The flow refuses to start on a digest mismatch.
* Calls are capped before they are sent. The adapter counts attempts under a lock and raises
  before the request leaves the process once the cap is reached; it never retries.
* Raw before score. The adapter forwards the provider's response text byte-for-byte to the
  runner's immutable raw-response envelope. Coordinate rescaling adapters rewrite that text,
  so only ``coordinate_adapter: "none"`` is admitted by this version.
* Credentials never enter evidence. The adapter reads the credential at construction time
  from the process environment and the manifest records only the variable name.

``kind`` is fixed to ``"grounding"`` here. It exists so a later, separately approved version
can register stateful (v5) policy kinds without changing the registry's shape.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pixelgym.grounding.providers import GroundingProvider, OpenRouterProvider
from pixelgym.platform.evaluation import PlatformProviderResponse
from pixelgym.platform.fingerprints import canonical_json_bytes, sha256_bytes

REGISTRY_SCHEMA_VERSION = "pixelgym-approved-providers-v1"
REGISTRY_RELATIVE_PATH = Path("config/approved-providers.json")
SUPPORTED_KINDS = frozenset({"grounding"})
SUPPORTED_TRANSPORTS = frozenset({"openrouter"})
SUPPORTED_COORDINATE_ADAPTERS = frozenset({"none"})
SUPPORTED_CONDITIONS = frozenset({"raw"})

_REFERENCE_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$")
_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_CATALOG_VERSION_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$")
_PARAMETER_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_USAGE_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_RESERVED_REQUEST_PARAMETERS = frozenset({"model", "messages", "response_format", "provider"})
_RECORD_FIELDS = frozenset(
    {
        "reference",
        "kind",
        "transport",
        "provider",
        "model",
        "model_alias_disclosure",
        "prompt_version",
        "condition",
        "call_cap",
        "max_concurrency",
        "price_catalog_version",
        "credential_env",
        "request_parameters",
        "provider_routing",
        "coordinate_adapter",
    }
)
_OPTIONAL_RECORD_FIELDS = frozenset(
    {"model_alias_disclosure", "request_parameters", "provider_routing"}
)


class ApprovedProviderError(ValueError):
    """The registry, a launch request, or a credential fails an approved-provider rule."""


def _scalar(value: object) -> bool:
    return isinstance(value, (str, int, float, bool))


def _validate_scalar_mapping(
    value: object, *, label: str, reserved: frozenset[str]
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ApprovedProviderError(f"{label} must be a JSON object")
    result: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not _PARAMETER_KEY_RE.fullmatch(key):
            raise ApprovedProviderError(f"{label} contains a non-identifier key")
        if key in reserved:
            raise ApprovedProviderError(f"{label} cannot set reserved field {key!r}")
        if isinstance(item, list):
            if not all(isinstance(entry, str) and _MODEL_RE.fullmatch(entry) for entry in item):
                raise ApprovedProviderError(f"{label}.{key} list entries must be plain identifiers")
        elif not _scalar(item):
            raise ApprovedProviderError(f"{label}.{key} must be a scalar or list of identifiers")
        result[key] = item
    return result


@dataclass(frozen=True)
class ApprovedProviderPolicy:
    """One preconfigured provider/model/prompt policy the flow may select by reference."""

    reference: str
    kind: str
    transport: str
    provider: str
    model: str
    prompt_version: int
    condition: str
    call_cap: int
    max_concurrency: int
    price_catalog_version: str
    credential_env: str
    coordinate_adapter: str
    model_alias_disclosure: str | None = None
    request_parameters: dict[str, Any] = field(default_factory=dict)
    provider_routing: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _REFERENCE_RE.fullmatch(self.reference):
            raise ApprovedProviderError("reference must be a short lowercase identifier")
        if self.kind not in SUPPORTED_KINDS:
            raise ApprovedProviderError(f"unsupported policy kind {self.kind!r}")
        if self.transport not in SUPPORTED_TRANSPORTS:
            raise ApprovedProviderError(f"unsupported transport {self.transport!r}")
        if self.provider == "scripted-demo":
            raise ApprovedProviderError("the scripted demo provider is not an approved provider")
        if not _MODEL_RE.fullmatch(self.provider) or not _MODEL_RE.fullmatch(self.model):
            raise ApprovedProviderError("provider and model must be plain identifiers")
        if not isinstance(self.prompt_version, int) or isinstance(self.prompt_version, bool):
            raise ApprovedProviderError("prompt_version must be an integer")
        if self.prompt_version <= 0:
            raise ApprovedProviderError("prompt_version must be positive")
        if self.condition not in SUPPORTED_CONDITIONS:
            raise ApprovedProviderError(f"unsupported condition {self.condition!r}")
        for name in ("call_cap", "max_concurrency"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ApprovedProviderError(f"{name} must be a positive integer")
        if self.max_concurrency > self.call_cap:
            raise ApprovedProviderError("max_concurrency cannot exceed call_cap")
        if not _CATALOG_VERSION_RE.fullmatch(self.price_catalog_version):
            raise ApprovedProviderError("price_catalog_version must be a short identifier")
        if self.price_catalog_version == "pixelgym-demo-prices-v1":
            raise ApprovedProviderError("an approved provider cannot bill against the demo catalog")
        if not _ENV_NAME_RE.fullmatch(self.credential_env):
            raise ApprovedProviderError("credential_env must name an environment variable")
        if self.coordinate_adapter not in SUPPORTED_COORDINATE_ADAPTERS:
            raise ApprovedProviderError(
                "coordinate adapters rewrite raw responses before storage; "
                f"only {sorted(SUPPORTED_COORDINATE_ADAPTERS)} are admitted"
            )
        if self.model_alias_disclosure is not None and not isinstance(
            self.model_alias_disclosure, str
        ):
            raise ApprovedProviderError("model_alias_disclosure must be a string or null")
        object.__setattr__(
            self,
            "request_parameters",
            _validate_scalar_mapping(
                self.request_parameters,
                label="request_parameters",
                reserved=_RESERVED_REQUEST_PARAMETERS,
            ),
        )
        object.__setattr__(
            self,
            "provider_routing",
            _validate_scalar_mapping(
                self.provider_routing, label="provider_routing", reserved=frozenset()
            ),
        )

    @classmethod
    def from_record(cls, record: object) -> ApprovedProviderPolicy:
        if not isinstance(record, dict):
            raise ApprovedProviderError("approved provider record must be a JSON object")
        unknown = set(record) - _RECORD_FIELDS
        if unknown:
            raise ApprovedProviderError(
                f"approved provider record has unknown fields: {sorted(unknown)}"
            )
        missing = _RECORD_FIELDS - _OPTIONAL_RECORD_FIELDS - set(record)
        if missing:
            raise ApprovedProviderError(
                f"approved provider record is missing fields: {sorted(missing)}"
            )
        for name in (
            "reference",
            "kind",
            "transport",
            "provider",
            "model",
            "condition",
            "price_catalog_version",
            "credential_env",
            "coordinate_adapter",
        ):
            if not isinstance(record[name], str):
                raise ApprovedProviderError(f"{name} must be a string")
        return cls(**record)

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "schema_version": REGISTRY_SCHEMA_VERSION,
            "reference": self.reference,
            "kind": self.kind,
            "transport": self.transport,
            "provider": self.provider,
            "model": self.model,
            "model_alias_disclosure": self.model_alias_disclosure,
            "prompt_version": self.prompt_version,
            "condition": self.condition,
            "call_cap": self.call_cap,
            "max_concurrency": self.max_concurrency,
            "price_catalog_version": self.price_catalog_version,
            "credential_env": self.credential_env,
            "coordinate_adapter": self.coordinate_adapter,
            "request_parameters": dict(self.request_parameters),
            "provider_routing": dict(self.provider_routing),
        }

    @property
    def approval_sha256(self) -> str:
        """Content digest a human approves; any field change produces a new digest."""
        return "sha256:" + sha256_bytes(canonical_json_bytes(self.canonical_dict()))

    def manifest_parameters(self) -> dict[str, Any]:
        """Policy-manifest parameters: identity-bearing, credential-free, retry-free."""
        parameters: dict[str, Any] = {
            "approved_provider_reference": self.reference,
            "approved_provider_sha256": self.approval_sha256,
            "transport": self.transport,
            "credential_env": self.credential_env,
            "call_cap": self.call_cap,
            "coordinate_adapter": self.coordinate_adapter,
            "deterministic": False,
            "hidden_retries": 0,
            "request_parameters": dict(self.request_parameters),
            "provider_routing": dict(self.provider_routing),
        }
        # Surface the inference knobs the tracking contract reads by name.
        for name in ("temperature", "seed", "max_output_tokens", "reasoning"):
            if name in self.request_parameters:
                parameters[name] = self.request_parameters[name]
        if "max_tokens" in self.request_parameters and "max_output_tokens" not in parameters:
            parameters["max_output_tokens"] = self.request_parameters["max_tokens"]
        return parameters

    def credential_present(self, environment: Mapping[str, str]) -> bool:
        """Report whether the named credential is set without exposing its value."""
        return bool(environment.get(self.credential_env))


def load_approved_providers(
    repository_root: Path, path: Path | None = None
) -> dict[str, ApprovedProviderPolicy]:
    """Load and strictly validate the checked-in registry; an empty registry is valid."""
    target = path or repository_root / REGISTRY_RELATIVE_PATH
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ApprovedProviderError(f"registry is not readable strict JSON: {target.name}") from exc
    if not isinstance(document, dict):
        raise ApprovedProviderError("registry must be a JSON object")
    if set(document) != {"schema_version", "entries"}:
        raise ApprovedProviderError("registry must contain exactly schema_version and entries")
    if document["schema_version"] != REGISTRY_SCHEMA_VERSION:
        raise ApprovedProviderError("registry schema_version is not supported")
    if not isinstance(document["entries"], list):
        raise ApprovedProviderError("registry entries must be a list")
    registry: dict[str, ApprovedProviderPolicy] = {}
    for record in document["entries"]:
        policy = ApprovedProviderPolicy.from_record(record)
        if policy.reference in registry:
            raise ApprovedProviderError(
                f"duplicate approved provider reference {policy.reference!r}"
            )
        registry[policy.reference] = policy
    return registry


def resolve_approved_provider(
    registry: Mapping[str, ApprovedProviderPolicy],
    reference: str,
    *,
    approved_sha256: str,
    model: str,
    prompt_version: int,
    maximum_calls: int,
    provider_concurrency: int,
) -> ApprovedProviderPolicy:
    """Return the policy only when every launch parameter restates the approved record."""
    policy = registry.get(reference)
    if policy is None:
        raise ApprovedProviderError(f"{reference!r} is not an approved provider reference")
    if approved_sha256 != policy.approval_sha256:
        raise ApprovedProviderError(
            f"approval digest does not match the registry record for {reference!r}"
        )
    mismatches = [
        name
        for name, expected, actual in (
            ("model", policy.model, model),
            ("prompt_version", policy.prompt_version, prompt_version),
            ("maximum_calls", policy.call_cap, maximum_calls),
        )
        if expected != actual
    ]
    if mismatches:
        raise ApprovedProviderError(
            f"launch parameters must restate the approved record exactly; mismatched: {mismatches}"
        )
    if provider_concurrency > policy.max_concurrency:
        raise ApprovedProviderError("provider concurrency exceeds the approved maximum")
    return policy


def price_entry_for(
    price_catalog: Mapping[str, Any], policy: ApprovedProviderPolicy
) -> dict[str, float]:
    """Select the frozen price record the approved policy bills against."""
    if price_catalog.get("catalog_version") != policy.price_catalog_version:
        raise ApprovedProviderError("price catalog version does not match the approved record")
    for entry in price_catalog["entries"]:
        if entry["provider"] == policy.provider and entry["model"] == policy.model:
            return {
                "input_usd_per_million_tokens": float(entry["input_usd_per_million_tokens"]),
                "output_usd_per_million_tokens": float(entry["output_usd_per_million_tokens"]),
            }
    raise ApprovedProviderError("price catalog has no entry for the approved provider/model")


class ApprovedCallLedger:
    """Durable, run-wide attempt ledger shared by every adapter instance of one run.

    Metaflow executes each shard in its own task process and constructs a fresh adapter
    there, so an in-process counter alone would enforce the approved cap per shard rather
    than per run. This ledger lives in SQLite keyed by submission and reserves one row per
    attempt inside an immediate transaction *before* the request is sent. A reservation is
    never released: an attempt with an unknown outcome still counts, matching the v5 rule.
    """

    def __init__(self, path: Path, *, submission_id: str, call_cap: int) -> None:
        if call_cap <= 0:
            raise ApprovedProviderError("call_cap must be positive")
        self.path = path
        self.submission_id = submission_id
        self.call_cap = call_cap
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS attempts ("
                " submission_id TEXT NOT NULL,"
                " attempt_index INTEGER NOT NULL,"
                " request_id TEXT NOT NULL,"
                " reserved_at_utc TEXT NOT NULL,"
                " PRIMARY KEY (submission_id, attempt_index))"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def reserved(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM attempts WHERE submission_id = ?", (self.submission_id,)
            ).fetchone()
        return int(row[0])

    def reserve(self, request_id: str) -> int:
        """Atomically reserve the next attempt index or raise before any request is sent."""
        from datetime import UTC, datetime

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT COALESCE(MAX(attempt_index), -1) + 1 FROM attempts"
                    " WHERE submission_id = ?",
                    (self.submission_id,),
                ).fetchone()
                index = int(row[0])
                if index >= self.call_cap:
                    raise ApprovedProviderError(
                        "approved call cap reached for this run; refusing to send another request"
                    )
                connection.execute(
                    "INSERT INTO attempts VALUES (?, ?, ?, ?)",
                    (
                        self.submission_id,
                        index,
                        request_id,
                        datetime.now(UTC).isoformat(),
                    ),
                )
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
        return index


class ApprovedGroundingProviderAdapter:
    """Platform provider around a Day 3 grounding provider with a pre-send call cap."""

    synthetic = False

    def __init__(
        self,
        policy: ApprovedProviderPolicy,
        *,
        inner: GroundingProvider,
        price_entry: Mapping[str, float],
        ledger: ApprovedCallLedger | None = None,
    ) -> None:
        if policy.model != inner.model:
            raise ApprovedProviderError("inner provider model does not match the approved record")
        if ledger is not None and ledger.call_cap != policy.call_cap:
            raise ApprovedProviderError("ledger call cap does not match the approved record")
        self.policy = policy
        self.ledger = ledger
        self.name = policy.provider
        self.model = policy.model
        self._inner = inner
        self._price_entry = dict(price_entry)
        self._lock = threading.Lock()
        self.attempts = 0

    def _cost(self, usage: Mapping[str, float]) -> float | None:
        prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
        completion = usage.get("completion_tokens", usage.get("output_tokens"))
        if prompt is None or completion is None:
            return None
        return (
            prompt * self._price_entry["input_usd_per_million_tokens"]
            + completion * self._price_entry["output_usd_per_million_tokens"]
        ) / 1_000_000

    def invoke(
        self,
        *,
        request_id: str,
        example_id: str,
        condition: str,
        image_path: Path,
        prompt: str,
        schema: dict[str, Any],
    ) -> PlatformProviderResponse:
        del example_id
        if condition != self.policy.condition:
            raise ApprovedProviderError("condition differs from the approved record")
        with self._lock:
            if self.attempts >= self.policy.call_cap:
                raise ApprovedProviderError(
                    "approved call cap reached; refusing to send another request"
                )
            # The durable reservation is the run-wide guard; the in-process counter is the
            # per-instance backstop. Both happen before the request leaves the process.
            if self.ledger is not None:
                self.ledger.reserve(request_id)
            self.attempts += 1
        response = self._inner.invoke(image_path=image_path, prompt=prompt, schema=schema)
        usage: dict[str, float] | None = None
        if response.usage is not None:
            usage = {
                key: value
                for key, value in response.usage.items()
                if isinstance(key, str)
                and _USAGE_KEY_RE.fullmatch(key)
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
                and value >= 0
            }
        return PlatformProviderResponse(
            raw_response=response.raw_response,
            latency_ms=response.latency_ms,
            usage=usage,
            cost_usd=self._cost(usage) if usage is not None else None,
            request_failure=response.request_failure,
        )


def _build_openrouter(
    policy: ApprovedProviderPolicy,
    environment: Mapping[str, str],
    urlopen: Callable[..., Any] | None,
) -> GroundingProvider:
    scoped = {
        "OPENROUTER_API_KEY": environment[policy.credential_env],
        "OPENROUTER_MODEL": policy.model,
    }
    extra: dict[str, Any] = {} if urlopen is None else {"urlopen": urlopen}
    return OpenRouterProvider(
        environment=scoped,
        request_parameters=policy.request_parameters,
        provider_routing=policy.provider_routing or None,
        **extra,
    )


_TRANSPORT_BUILDERS: dict[
    str,
    Callable[
        [ApprovedProviderPolicy, Mapping[str, str], Callable[..., Any] | None], GroundingProvider
    ],
] = {"openrouter": _build_openrouter}


def build_platform_provider(
    policy: ApprovedProviderPolicy,
    *,
    price_catalog: Mapping[str, Any],
    environment: Mapping[str, str],
    urlopen: Callable[..., Any] | None = None,
    ledger: ApprovedCallLedger | None = None,
) -> ApprovedGroundingProviderAdapter:
    """Construct the capped adapter; fails closed when the credential is absent."""
    if not policy.credential_present(environment):
        raise ApprovedProviderError(
            f"credential environment variable {policy.credential_env} is not set"
        )
    price_entry = price_entry_for(price_catalog, policy)
    inner = _TRANSPORT_BUILDERS[policy.transport](policy, environment, urlopen)
    return ApprovedGroundingProviderAdapter(
        policy, inner=inner, price_entry=price_entry, ledger=ledger
    )
