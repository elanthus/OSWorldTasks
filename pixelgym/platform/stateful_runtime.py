"""Kind-aware serving runtime for stateful-v5 deployments (v5 serving stage S6).

The control plane keeps one active pointer. When it selects a grounding candidate, the v1
``PolicyRuntime`` serves ``/api/v1/ground`` and ``/api/v2`` refuses new episodes; when it selects
a ``stateful-v5`` candidate, the stateful runtime serves ``/api/v2/episodes`` and the v1 runtime
is unloaded. Each runtime refuses a prepared policy of the other kind.

Preparing a stateful candidate happens before the compare-and-swap and changes no traffic:

1. A no-cost contract smoke runs one fake-policy episode (create, act, close) through an isolated
   ``/api/v2`` app bound to the candidate's exact package and identity. The fake policy is the
   platform's scripted policy with a scripted transport; it never calls a provider.
2. The candidate's own policy code is loaded into the credential-free policy subprocess by an
   injected :class:`StatefulPolicyLoader`, and its ``reset`` is exercised. ``reset`` may not call a
   provider under the frozen v5 policy interface.
3. The per-version session store and attempt journal are opened. The deployment attempt cap is
   counted from that journal across every deployment of the same exact version, so redeploying
   or rolling back to a version never resets its spend.

The caller owns the environment. Nothing here executes an action or sees the task application.
"""

from __future__ import annotations

import base64
import io
import tempfile
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from fastapi.testclient import TestClient
from PIL import Image

from pixelgym.grounding.v5.contracts import SCREEN_HEIGHT, SCREEN_WIDTH, sha256_bytes
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.runner import (
    ProviderTransport,
    ScriptedStatefulPolicy,
    ScriptedTransport,
)
from pixelgym.platform.control_store import (
    CandidateKindMismatchError,
    DeploymentRecord,
    TransitionError,
)
from pixelgym.platform.deployment_smoke import DeploymentSmokeError
from pixelgym.platform.operational_log import MemoryOperationalLog
from pixelgym.platform.policy_subprocess import (
    PolicySubprocessError,
    PolicyWorkerLauncher,
    PolicyWorkerSpec,
    SandboxedPolicyProcess,
)
from pixelgym.platform.service import LoadedPolicy, PolicyRuntime, create_serving_app
from pixelgym.platform.serving_episode import (
    DeploymentChangedError,
    NoActiveStatefulDeploymentError,
    ServingEpisodeHost,
    SessionConflictError,
    SQLiteServingSessionStore,
)
from pixelgym.platform.stateful_contracts import (
    SESSION_SCHEMA_VERSION,
    ServingIdentity,
    StatefulPolicyPackage,
)
from pixelgym.platform.stateful_control import (
    PolicyKind,
    ServingTerms,
    StatefulCandidateRecord,
)
from pixelgym.platform.stateful_service import (
    MemoryEpisodeOperationalLog,
    MemoryEpisodeSessionRegistry,
    RegisteredEpisode,
    create_episode_router,
)

SMOKE_TASK_INSTRUCTION = "Deployment readiness smoke: no task application is attached."
_SMOKE_ACTION = {"action_type": 0, "x": 0, "y": 0, "key": 0}


class StatefulLoadError(DeploymentSmokeError):
    """A stateful candidate's policy could not be loaded into its isolated worker."""


@dataclass(frozen=True)
class LoadedStatefulPolicy:
    """The candidate's policy, running in its worker, and the serving-side transport."""

    policy: SandboxedPolicyProcess
    transport: ProviderTransport


class StatefulPolicyLoader(Protocol):
    def load(self, package: StatefulPolicyPackage) -> LoadedStatefulPolicy: ...


class WorkerSpecPolicyLoader:
    """Load a package into the credential-free worker from an operator-registered spec.

    The registry maps a package's ``package_source_sha256`` to the exact worker factory that
    implements it. A package whose source digest is not registered, or whose frozen sandbox
    endpoint differs from the worker's egress authority, is refused before any process starts.
    The transport factory runs in the serving process, where the credential (if any) lives.
    """

    def __init__(
        self,
        *,
        worker_specs: Mapping[str, PolicyWorkerSpec],
        transport_factory: Callable[[StatefulPolicyPackage], ProviderTransport],
        launcher: PolicyWorkerLauncher | None = None,
        require_os_sandbox: bool = True,
        request_timeout_seconds: float = 10.0,
    ) -> None:
        self.worker_specs = dict(worker_specs)
        self.transport_factory = transport_factory
        self.launcher = launcher
        self.require_os_sandbox = require_os_sandbox
        self.request_timeout_seconds = request_timeout_seconds

    def load(self, package: StatefulPolicyPackage) -> LoadedStatefulPolicy:
        spec = self.worker_specs.get(package.package_source_sha256)
        if spec is None:
            raise StatefulLoadError("no policy worker is registered for the package source digest")
        if spec.provider_endpoint != package.v5_manifest().sandbox.provider_endpoint:
            raise StatefulLoadError(
                "policy worker egress authority does not match the frozen sandbox manifest"
            )
        try:
            policy = SandboxedPolicyProcess(
                spec=spec,
                launcher=self.launcher,
                request_timeout_seconds=self.request_timeout_seconds,
                require_os_sandbox=self.require_os_sandbox,
            )
        except PolicySubprocessError as exc:
            raise StatefulLoadError("policy worker did not start in its isolated process") from exc
        try:
            transport = self.transport_factory(package)
        except BaseException:
            policy.close()
            raise
        return LoadedStatefulPolicy(policy=policy, transport=transport)


@dataclass
class PreparedStatefulPolicy:
    """A verified, smoked, loaded stateful candidate that has not reached traffic yet."""

    candidate_id: str
    package: StatefulPolicyPackage
    terms: ServingTerms
    loaded: LoadedStatefulPolicy
    session_store: SQLiteServingSessionStore
    journal: V5AttemptJournal
    _closed: bool = False

    @property
    def kind(self) -> PolicyKind:
        return PolicyKind.STATEFUL_V5

    @property
    def policy_id(self) -> str:
        return self.package.policy_id

    def stop_policy(self) -> None:
        """Stop the policy worker; any later act on this version seals an infrastructure failure."""
        self.loaded.policy.close()

    def close(self) -> None:
        """Release every resource; used when the prepared candidate never reaches traffic."""
        if self._closed:
            return
        self._closed = True
        try:
            self.loaded.policy.close()
        finally:
            self.session_store.close()
            self.journal.close()


def _smoke_png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), "white").save(output, format="PNG")
    return output.getvalue()


def _screenshot_body(data: bytes) -> dict[str, str]:
    return {"image_base64": base64.b64encode(data).decode("ascii"), "media_type": "image/png"}


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise DeploymentSmokeError(message)


def _identity_headers_match(response: Any, identity: ServingIdentity) -> bool:
    return (
        response.headers.get("X-PixelGym-API-Version") == SESSION_SCHEMA_VERSION
        and response.headers.get("X-PixelGym-Policy-ID") == identity.policy_id
        and response.headers.get("X-PixelGym-Deployment-ID") == identity.deployment_id
        and response.headers.get("X-PixelGym-Exact-Policy-Version") == identity.exact_policy_version
        and response.headers.get("X-PixelGym-Evidence-Class") == identity.evidence_class.value
    )


def run_contract_smoke(candidate: StatefulCandidateRecord) -> None:
    """Serve one no-cost fake-policy episode through an isolated ``/api/v2`` app.

    The episode is bound to the candidate's exact package (screen, step limit, attempt limits,
    action-schema and key-allowlist versions) and to a smoke identity. Its state, journal, and
    records live in a temporary directory and never reach the deployment's stores.
    """

    package = candidate.package
    identity = ServingIdentity(
        policy_id=package.policy_id,
        deployment_id=f"smoke-{candidate.candidate_id}",
        exact_policy_version=candidate.candidate_id,
        evidence_class=package.evidence_class,
    )
    with tempfile.TemporaryDirectory(prefix="pixelgym-stateful-smoke-") as directory:
        root = Path(directory)
        session_store = SQLiteServingSessionStore(root / "sessions.sqlite")
        journal = V5AttemptJournal(root / "attempts.sqlite")
        try:
            host = ServingEpisodeHost(
                session_store=session_store,
                journal=journal,
                package=package,
                identity=identity,
                policy=ScriptedStatefulPolicy((_SMOKE_ACTION,)),
                transport=ScriptedTransport(),
                deployment_attempt_cap=1,
            )
            records = MemoryEpisodeOperationalLog()
            app = create_serving_app(
                PolicyRuntime(),
                operational_log=MemoryOperationalLog(),
                episode_router=create_episode_router(
                    host_factory=lambda: host,
                    session_registry=MemoryEpisodeSessionRegistry(),
                    operational_log=records,
                ),
            )
            screenshot = _smoke_png()
            screenshot_sha256 = "sha256:" + sha256_bytes(screenshot)
            with TestClient(app) as client:
                created = client.post(
                    "/api/v2/episodes",
                    json={
                        "schema_version": SESSION_SCHEMA_VERSION,
                        "task_instruction": SMOKE_TASK_INSTRUCTION,
                        "screen_width": SCREEN_WIDTH,
                        "screen_height": SCREEN_HEIGHT,
                        "client_episode_ref": "deployment-smoke",
                    },
                )
                _expect(created.status_code == 201, "stateful smoke could not open an episode")
                body = created.json()
                _expect(
                    body.get("identity") == identity.to_dict()
                    and _identity_headers_match(created, identity)
                    and body.get("max_steps") == package.max_steps
                    and body.get("action_schema_version") == package.action_schema_version
                    and body.get("key_allowlist_version") == package.key_allowlist_version,
                    "stateful smoke episode identity or caps do not match the package",
                )
                episode_id = body["episode_id"]
                acted = client.post(
                    f"/api/v2/episodes/{episode_id}/act",
                    json={
                        "schema_version": SESSION_SCHEMA_VERSION,
                        "screenshot": _screenshot_body(screenshot),
                        "previous_intent_id": None,
                        "previous_result": None,
                    },
                )
                _expect(acted.status_code == 200, "stateful smoke act was refused")
                act_body = acted.json()
                _expect(
                    act_body.get("sealed_failure") is None
                    and act_body.get("action")
                    == {"action_type": "NOOP", "x": None, "y": None, "key": None}
                    and act_body.get("identity") == identity.to_dict()
                    and _identity_headers_match(acted, identity),
                    "stateful smoke act did not return the validated fake action",
                )
                closed = client.post(
                    f"/api/v2/episodes/{episode_id}/close",
                    json={
                        "schema_version": SESSION_SCHEMA_VERSION,
                        "final_screenshot": _screenshot_body(screenshot),
                        "final_intent_id": act_body["intent_id"],
                        "final_result": {
                            "reward": 0.0,
                            "terminated": False,
                            "truncated": False,
                            "screenshot_sha256": screenshot_sha256,
                        },
                    },
                )
                _expect(closed.status_code == 200, "stateful smoke close was refused")
                close_body = closed.json()
                _expect(
                    close_body.get("terminal_classification") == "closed_by_caller"
                    and close_body.get("steps") == 1
                    and close_body.get("model_attempts") == 1
                    and close_body.get("final_screenshot_sha256") == screenshot_sha256
                    and _identity_headers_match(closed, identity),
                    "stateful smoke close summary does not match the episode",
                )
            _expect(
                len(records.records) == 3 and len(records.screenshots) == 1,
                "stateful smoke did not durably record its episode",
            )
        finally:
            session_store.close()
            journal.close()


class StatefulCandidatePreparer:
    """``stateful_load_and_smoke`` for the deployment coordinator."""

    def __init__(
        self,
        *,
        loader: StatefulPolicyLoader,
        state_root: Path,
        require_os_sandbox: bool = True,
    ) -> None:
        self.loader = loader
        self.state_root = state_root
        self.require_os_sandbox = require_os_sandbox

    def __call__(
        self, candidate: StatefulCandidateRecord, terms: ServingTerms
    ) -> PreparedStatefulPolicy:
        if not isinstance(candidate, StatefulCandidateRecord):
            raise CandidateKindMismatchError("stateful preparation received a non-stateful policy")
        if terms.candidate_id != candidate.candidate_id or terms.policy_id != candidate.policy_id:
            raise TransitionError("serving terms do not belong to the prepared candidate")
        run_contract_smoke(candidate)
        loaded = self.loader.load(candidate.package)
        session_store: SQLiteServingSessionStore | None = None
        journal: V5AttemptJournal | None = None
        try:
            policy = loaded.policy
            if not isinstance(policy, SandboxedPolicyProcess):
                raise StatefulLoadError("stateful policy code must run in the policy subprocess")
            if self.require_os_sandbox and not policy.os_sandbox_applied:
                raise StatefulLoadError("policy worker lacks OS sandbox enforcement")
            try:
                state = policy.reset(SMOKE_TASK_INSTRUCTION)
            except PolicySubprocessError as exc:
                raise StatefulLoadError("policy worker failed its readiness reset") from exc
            if not isinstance(state, bytes) or not state:
                raise StatefulLoadError("stateful policy worker returned no reset state")
            directory = self.state_root / "packages" / candidate.candidate_id
            session_store = SQLiteServingSessionStore(directory / "sessions.sqlite")
            journal = V5AttemptJournal(directory / "attempts.sqlite")
            if journal.call_counts()[0] >= terms.deployment_attempt_cap:
                raise StatefulLoadError(
                    "the deployment attempt cap for this exact version is already exhausted"
                )
        except BaseException:
            loaded.policy.close()
            if session_store is not None:
                session_store.close()
            if journal is not None:
                journal.close()
            raise
        return PreparedStatefulPolicy(
            candidate_id=candidate.candidate_id,
            package=candidate.package,
            terms=terms,
            loaded=loaded,
            session_store=session_store,
            journal=journal,
        )


@dataclass(frozen=True)
class ActiveStatefulDeployment:
    identity: ServingIdentity
    host: ServingEpisodeHost
    prepared: PreparedStatefulPolicy


class StatefulRuntime:
    """Holds at most one active stateful deployment and builds its episode host."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._active: ActiveStatefulDeployment | None = None

    @property
    def active(self) -> ActiveStatefulDeployment | None:
        with self._lock:
            return self._active

    def activate(self, deployment: DeploymentRecord, prepared: object) -> None:
        if not isinstance(prepared, PreparedStatefulPolicy):
            raise CandidateKindMismatchError(
                "the stateful runtime accepts only prepared stateful-v5 packages"
            )
        if (
            deployment.candidate_id != prepared.candidate_id
            or deployment.policy_id != prepared.package.policy_id
        ):
            raise TransitionError("prepared package does not match the activated deployment")
        identity = ServingIdentity(
            policy_id=prepared.package.policy_id,
            deployment_id=deployment.deployment_id,
            exact_policy_version=prepared.candidate_id,
            evidence_class=prepared.package.evidence_class,
        )
        host = ServingEpisodeHost(
            session_store=prepared.session_store,
            journal=prepared.journal,
            package=prepared.package,
            identity=identity,
            policy=prepared.loaded.policy,
            transport=prepared.loaded.transport,
            deployment_attempt_cap=prepared.terms.deployment_attempt_cap,
        )
        with self._lock:
            previous = self._active
            self._active = ActiveStatefulDeployment(identity=identity, host=host, prepared=prepared)
        if previous is not None and previous.prepared is not prepared:
            previous.prepared.stop_policy()

    def deactivate(self) -> None:
        with self._lock:
            previous = self._active
            self._active = None
        if previous is not None:
            # An act already in flight on the retired version fails closed: the host seals it
            # as an infrastructure failure under the retired identity.
            previous.prepared.stop_policy()

    def host_factory(self) -> ServingEpisodeHost:
        active = self.active
        if active is None:
            raise NoActiveStatefulDeploymentError("no approved stateful policy is active")
        return active.host

    def readiness(self) -> dict[str, str] | None:
        active = self.active
        if active is None:
            return None
        return {
            "status": "ready",
            "policy_id": active.identity.policy_id,
            "kind": PolicyKind.STATEFUL_V5.value,
        }


class ActiveDeploymentEpisodeRegistry:
    """Episode registry that only resolves episodes of the active stateful deployment.

    An episode opened under a deployment that no longer holds the pointer is refused with
    ``DeploymentChangedError`` rather than served by a retired version. After a process restart
    the active deployment's durable session store recovers episodes the in-memory map lost.
    """

    def __init__(self, runtime: StatefulRuntime) -> None:
        self.runtime = runtime
        self._episodes: dict[str, RegisteredEpisode] = {}
        self._lock = threading.RLock()

    def register(self, episode_id: str, registration: RegisteredEpisode) -> None:
        with self._lock:
            active = self.runtime.active
            if active is None or registration.identity != active.identity:
                raise DeploymentChangedError("the episode's deployment is no longer active")
            if episode_id in self._episodes:
                raise SessionConflictError("episode is already registered")
            self._episodes[episode_id] = registration

    def get(self, episode_id: str) -> RegisteredEpisode | None:
        with self._lock:
            active = self.runtime.active
            registration = self._episodes.get(episode_id)
            if registration is not None:
                if active is None or registration.identity != active.identity:
                    raise DeploymentChangedError("the episode's deployment is no longer active")
                return registration
            if active is None:
                return None
            state = active.host.session_store.get(episode_id)
            if state is None:
                return None
            if state.identity != active.identity:
                raise DeploymentChangedError("the episode's deployment is no longer active")
            registration = RegisteredEpisode(
                host=active.host, identity=state.identity, screen=dict(state.screen)
            )
            self._episodes[episode_id] = registration
            return registration


class KindAwareRuntime:
    """Route one activated deployment to the runtime of its policy kind.

    Exactly one kind serves at a time. The other kind is unloaded first, so traffic never
    reaches a version that no longer holds the active pointer; if activation then fails,
    nothing serves rather than the wrong version.
    """

    def __init__(self, grounding: PolicyRuntime, stateful: StatefulRuntime) -> None:
        self.grounding = grounding
        self.stateful = stateful

    @property
    def active_kind(self) -> PolicyKind | None:
        if self.grounding.loaded is not None:
            return PolicyKind.GROUNDING
        if self.stateful.active is not None:
            return PolicyKind.STATEFUL_V5
        return None

    def activate(self, deployment: DeploymentRecord, prepared: object) -> None:
        if isinstance(prepared, LoadedPolicy):
            if prepared.manifest.policy_id != deployment.policy_id:
                raise TransitionError("loaded policy does not match the activated deployment")
            self.stateful.deactivate()
            # The only mutation of the traffic runtime happens after the database CAS succeeds.
            self.grounding.activate(replace(prepared, deployment_id=deployment.deployment_id))
            return
        if isinstance(prepared, PreparedStatefulPolicy):
            self.grounding.deactivate()
            self.stateful.activate(deployment, prepared)
            return
        raise CandidateKindMismatchError("activation received a policy of an unknown kind")
