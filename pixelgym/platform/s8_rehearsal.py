"""S8 scripted serving rehearsal: prepare, run, inspect, and report one bounded lifecycle.

The rehearsal consumes the sealed S7 qualification and an owner-supplied approval document.
It never fabricates candidate approval or serving terms: ``prepare`` refuses an approval that
does not bind the exact sealed packages, reports, and demo caps, and ``run`` records the
approval digest as the authority for every control-plane transition it performs.

Every fake send (deployed traffic and contract smokes) is reserved durably in the campaign
ledger before the scripted transport is invoked, through the same ``S7SendBudget`` the
qualification used. Exceeding a phase or campaign cap stops the campaign. No real-provider
transport exists on this path.

``inspect_rehearsal`` and ``stored_report`` read stored results only. Replay of caller actions
is part of the explicit verifier, never of reporting.
"""

from __future__ import annotations

import base64
import io
import json
import shutil
import sqlite3
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from PIL import Image

from pixelgym.actions import ActionType
from pixelgym.env import PixelGuiEnv
from pixelgym.grounding.v5.backend import V5FakeBackend
from pixelgym.grounding.v5.contracts import (
    SCREEN_HEIGHT,
    SCREEN_WIDTH,
    content_digest,
    sha256_bytes,
)
from pixelgym.grounding.v5.generator import generate_task
from pixelgym.grounding.v5.journal import V5AttemptJournal
from pixelgym.grounding.v5.retention import _checkout, write_private
from pixelgym.grounding.v5.retention_validation import SEED
from pixelgym.platform import s7_runtime, s7_source
from pixelgym.platform.contracts import ArtifactRef
from pixelgym.platform.control_store import (
    ACTOR_VERIFICATION_SOURCE_KEY,
    ControlStore,
    TransitionError,
)
from pixelgym.platform.deployment import DeploymentCoordinator
from pixelgym.platform.deployment_smoke import DeploymentSmokeError
from pixelgym.platform.immutable_store import LocalImmutableStore
from pixelgym.platform.operational_log import MemoryOperationalLog
from pixelgym.platform.s7_budget import S7SendBudget
from pixelgym.platform.s7_evidence import (
    file_identity,
    gate_verifier,
    inventory,
    verify_packages,
    verify_seal,
)
from pixelgym.platform.s7_policy import (
    EVIDENCE_LABEL,
    POLICY_SHA256,
    QUALIFICATION_CAP,
    policy_document,
)
from pixelgym.platform.s7_qualification import read_json
from pixelgym.platform.s7_source import verify_runtime, write_json
from pixelgym.platform.service import PolicyRuntime, create_serving_app
from pixelgym.platform.stateful_contracts import SESSION_SCHEMA_VERSION
from pixelgym.platform.stateful_control import StatefulGateReport, stateful_package_from_dict
from pixelgym.platform.stateful_runtime import (
    ActiveDeploymentEpisodeRegistry,
    KindAwareRuntime,
    PreparedStatefulPolicy,
    StatefulCandidatePreparer,
    StatefulRuntime,
)
from pixelgym.platform.stateful_service import (
    ImmutableEpisodeOperationalLog,
    create_episode_router,
)

APPROVAL_SCHEMA = "pixelgym-s8-approval-v1"
PLAN_SCHEMA = "pixelgym-s8-rehearsal-plan-v1"
LIFECYCLE_SCHEMA = "pixelgym-s8-lifecycle-v1"
FAILURE_SCHEMA = "pixelgym-s8-failure-v1"
VERIFICATION_SCHEMA = "pixelgym-s8-verification-v1"
CHECKS_SCHEMA = "pixelgym-s8-acceptance-checks-v1"
REPORT_SCHEMA = "pixelgym-s8-review-report-v1"
INTERRUPTION_BOUNDARY = "canonical_response_persisted"
EXPECTED_SENDS = {"A": 2, "B": 24, "smoke": 6}
STEP_NAMES = (
    "register_and_refuse_unapproved_b",
    "approve_deploy_a_one_noop",
    "approve_deploy_b_interrupt_and_restore",
    "complete_b_golden_episode",
    "redeploy_b_and_exhaust_cap",
    "rollback_to_a_and_refuse_exhausted_b",
    "cleanup_and_final_pointer",
)
_PHASES = ("qualification", "A", "B", "smoke")


class RehearsalStopped(RuntimeError):
    """A frozen lifecycle expectation was not met; the campaign stops with its evidence."""


# Approval binding ---------------------------------------------------------------------------


def bind_approval(qualification: Path, document: Mapping[str, Any]) -> dict[str, Any]:
    """Verify an owner-supplied approval against the sealed qualification; never invent one."""
    required = {
        "schema_version",
        "actor",
        "decision",
        "approved_on",
        "qualification_plan_sha256",
        "gate_policy_sha256",
        "packages",
        "activation_approved",
        "publication_approved",
    }
    if not isinstance(document, Mapping) or set(document) != required:
        raise ValueError("S8 approval document fields differ from the approval schema")
    if document["schema_version"] != APPROVAL_SCHEMA:
        raise ValueError("unsupported S8 approval schema")
    if document["activation_approved"] is not True or document["publication_approved"] is not False:
        raise ValueError("S8 approval must state activation explicitly and never publication")
    for name in ("actor", "decision", "approved_on"):
        if not isinstance(document[name], str) or not document[name].strip():
            raise ValueError(f"S8 approval {name} is required")
    plan = read_json(qualification / "plan.json")
    if (
        document["qualification_plan_sha256"] != content_digest(plan)
        or document["gate_policy_sha256"] != POLICY_SHA256
        or plan.get("gate_policy_sha256") != POLICY_SHA256
    ):
        raise ValueError("S8 approval is not bound to this sealed qualification")
    packages = document["packages"]
    if not isinstance(packages, Mapping) or set(packages) != {"A", "B"}:
        raise ValueError("S8 approval must name exactly packages A and B")
    caps = policy_document()["deployment_attempt_caps"]
    for role in ("A", "B"):
        package = read_json(qualification / f"packages/{role}/package.json")
        report = read_json(qualification / f"packages/{role}/gate-report.json")
        expected = {
            "policy_id": package["policy_id"],
            "package_sha256": content_digest(package),
            "gate_report_sha256": content_digest(report),
            "deployment_attempt_cap": caps[role],
            "deployment_tier": "demo",
        }
        if packages[role] != expected:
            raise ValueError(
                f"S8 approval for package {role} differs from the sealed package, report, or terms"
            )
    return {
        "approval_sha256": content_digest(dict(document)),
        "actor": document["actor"],
        "approved_on": document["approved_on"],
    }


def backup_ledger(source: Path, target: Path) -> dict[str, Any]:
    """Copy the sealed qualification ledger with SQLite's backup API and verify the copy."""
    expected = file_identity(source)
    with (
        closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as origin,
        closing(sqlite3.connect(target)) as copy,
    ):
        origin.backup(copy)
    target.chmod(0o600)
    actual = file_identity(target)
    if actual != expected:
        raise ValueError("S8 ledger backup differs from the sealed qualification ledger")
    counts, campaign = ledger_state(target)
    if (
        counts != {"qualification": QUALIFICATION_CAP}
        or campaign["paused"] != 1
        or campaign["activation_approval"] is not None
    ):
        raise ValueError("S8 requires the paused, complete, unresumed qualification ledger")
    return {
        "schema_version": "pixelgym-s8-ledger-backup-v1",
        "source_identity": expected,
        "backup_identity": actual,
        "counts": counts,
        "paused": True,
    }


def ledger_state(path: Path) -> tuple[dict[str, int], dict[str, Any]]:
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
        counts = dict(db.execute("SELECT phase, count(*) FROM sends GROUP BY phase"))
        paused, approval = db.execute("SELECT paused, activation_approval FROM campaign").fetchone()
        keys = {
            phase: sorted(
                row[0]
                for row in db.execute("SELECT request_key FROM sends WHERE phase = ?", (phase,))
            )
            for phase in _PHASES
        }
    return counts, {"paused": paused, "activation_approval": approval, "keys": keys}


# Preparation --------------------------------------------------------------------------------


def prepare(root: Path, qualification: Path, state: Path, *, approval_path: Path) -> dict[str, Any]:
    """Create the isolated state directory, verified ledger backup, and approval binding."""
    qualification, state = qualification.resolve(), state.resolve()
    if state.exists() or _checkout(state) is not None:
        raise ValueError("new private S8 state directory outside Git is required")
    if _checkout(qualification) is not None:
        raise ValueError("sealed S7 qualification must live outside Git")
    if state == qualification or state in qualification.parents or qualification in state.parents:
        raise ValueError("S8 state and the qualification must be disjoint")
    s7_source.verify_host_source(root)
    verify_seal(qualification)
    verify_packages(qualification)
    document = json.loads(approval_path.read_bytes())
    binding = bind_approval(qualification, document)
    plan_q = read_json(qualification / "plan.json")
    source = read_json(qualification / "source-binding.json")
    lock = read_json(qualification / "runtime-lock.json")
    policy = policy_document()
    state.mkdir(mode=0o700, parents=True)
    try:
        write_json(state / "approval.json", document)
        receipt = backup_ledger(qualification / "send-budget.sqlite", state / "send-budget.sqlite")
        write_json(state / "ledger-backup.json", receipt)
        packages = {}
        for role in ("A", "B"):
            package = read_json(qualification / f"packages/{role}/package.json")
            packages[role] = {
                "policy_id": package["policy_id"],
                "package_sha256": content_digest(package),
                "gate_report_sha256": content_digest(
                    read_json(qualification / f"packages/{role}/gate-report.json")
                ),
                "max_steps": package["max_steps"],
                "deployment_attempt_cap": policy["deployment_attempt_caps"][role],
                "deployment_tier": "demo",
            }
        plan = {
            "schema_version": PLAN_SCHEMA,
            "evidence_label": EVIDENCE_LABEL,
            "qualification_path": str(qualification),
            "qualification_seal_sha256": content_digest(read_json(qualification / "seal.json")),
            "qualification_plan_sha256": content_digest(plan_q),
            "run_reference": plan_q["run_reference"] + "/s8-rehearsal",
            "approval_sha256": binding["approval_sha256"],
            "actor": binding["actor"],
            "source_revision": source["revision"],
            "source_binding_sha256": content_digest(source),
            "runtime_lock_sha256": content_digest(lock),
            "gate_policy_sha256": POLICY_SHA256,
            "packages": packages,
            "seed": SEED,
            "golden_actions_sha256": content_digest(plan_q["actions"]["B"]),
            "golden_action_count": len(plan_q["actions"]["B"]),
            "phase_caps": policy["phase_caps"],
            "campaign_cap": policy["campaign_cap"],
            "expected_sends": EXPECTED_SENDS,
            "interruption_boundary": INTERRUPTION_BOUNDARY,
            "lifecycle_steps": list(STEP_NAMES),
            "activation_approved": True,
            "publication_approved": False,
            "real_provider_cap": 0,
        }
        write_json(state / "plan.json", plan)
    except BaseException:
        # The directory did not exist before this call. A failure after creating it, for
        # example the ledger pause check, must not leave a partial directory that a retry
        # refuses as "already exists".
        shutil.rmtree(state, ignore_errors=True)
        raise
    return plan


# Caller side --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Reply:
    status: int
    body: dict[str, Any]
    headers: dict[str, str]

    def code(self) -> str | None:
        detail = self.body.get("detail")
        return detail.get("code") if isinstance(detail, dict) else None


class Caller(Protocol):
    kind: str

    def post(self, path: str, body: Mapping[str, Any]) -> Reply: ...

    def get(self, path: str) -> Reply: ...

    def close(self) -> None: ...


class AsgiCaller:
    """In-process ASGI caller; opens no socket."""

    kind = "in-process-asgi"

    def __init__(self, app: Any) -> None:
        from fastapi.testclient import TestClient

        self.client = TestClient(app)
        self.client.__enter__()

    def post(self, path: str, body: Mapping[str, Any]) -> Reply:
        response = self.client.post(path, json=dict(body))
        return Reply(response.status_code, response.json(), dict(response.headers))

    def get(self, path: str) -> Reply:
        response = self.client.get(path)
        return Reply(response.status_code, response.json(), dict(response.headers))

    def close(self) -> None:
        self.client.__exit__(None, None, None)


class LoopbackCaller:
    """Real HTTP listener bound to 127.0.0.1 only; exercised by the opt-in integration test."""

    def __init__(self, app: Any, *, port: int) -> None:
        import threading

        import uvicorn

        self.kind = f"loopback-listener:127.0.0.1:{port}"
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline or not self.thread.is_alive():
                raise RuntimeError("loopback listener did not start")
            time.sleep(0.01)
        self.port = port

    def _request(self, method: str, path: str, body: bytes | None) -> Reply:
        import http.client

        # One connection per request: uvicorn closes idle keep-alive connections, and a
        # stale reused connection would end the one-shot campaign with a transport error.
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            connection.request(
                method, path, body=body, headers={"Content-Type": "application/json"}
            )
            response = connection.getresponse()
            data = response.read()
            return Reply(response.status, json.loads(data), dict(response.getheaders()))
        finally:
            connection.close()

    def post(self, path: str, body: Mapping[str, Any]) -> Reply:
        return self._request("POST", path, json.dumps(dict(body)).encode())

    def get(self, path: str) -> Reply:
        return self._request("GET", path, None)

    def close(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            raise RuntimeError("loopback listener did not stop")


CallerFactory = Callable[[Any], Caller]
PreparerFactory = Callable[[Path, Path, Path, S7SendBudget], StatefulCandidatePreparer]


def default_preparer(
    root: Path, qualification: Path, state_root: Path, budget: S7SendBudget
) -> StatefulCandidatePreparer:
    """The S7 preparer: exact-bundle source verification, real sandbox, budgeted smokes."""
    return s7_runtime.preparer(
        root=root, qualification=qualification, state_root=state_root, budget=budget
    )


def _png(observation: Any) -> bytes:
    output = io.BytesIO()
    Image.fromarray(observation, "RGB").save(output, format="PNG")
    return output.getvalue()


def _digest(data: bytes) -> str:
    return "sha256:" + sha256_bytes(data)


def environment_action(served: Mapping[str, Any]) -> dict[str, int]:
    """Translate the API's symbolic action into the PixelGym action contract."""
    kind = ActionType[str(served["action_type"])]
    return {
        "action_type": int(kind),
        "x": int(served["x"] or 0),
        "y": int(served["y"] or 0),
        "key": int(served["key"] or 0),
    }


class CallerEnvironment:
    """The caller-owned environment: retains every dispatch, screenshot, and checkpoint."""

    def __init__(self, state: Path, *, episode_ref: str) -> None:
        self.state = state
        self.episode_ref = episode_ref
        self.task = generate_task(SEED)
        self.backend = V5FakeBackend()
        self.env = PixelGuiEnv(
            self.backend,
            instruction=self.task.instruction,
            max_episode_steps=self.task.max_episode_steps,
        )
        observation, _ = self.env.reset(seed=SEED)
        self.screenshot = _png(observation)
        self._store(self.screenshot, "png")
        self.initial_checkpoint = _digest(self.backend.checkpoint())
        self._store(self.backend.checkpoint(), "ckpt")
        self.dispatches: list[dict[str, Any]] = []
        self.closed = False

    def _store(self, data: bytes, suffix: str) -> str:
        digest = _digest(data)
        path = self.state / "caller" / "objects" / f"{digest[7:]}.{suffix}"
        if not path.exists():
            write_private(path, data)
        return digest

    def current_checkpoint(self) -> str:
        return _digest(self.backend.checkpoint())

    def result(self) -> dict[str, Any]:
        """The previous result as the caller reports it with the current screenshot."""
        if not self.dispatches:
            raise RehearsalStopped("no dispatch has been made yet")
        last = self.dispatches[-1]
        return {
            "reward": last["reward"],
            "terminated": last["terminated"],
            "truncated": last["truncated"],
            "screenshot_sha256": last["screenshot_after_sha256"],
        }

    def dispatch(self, intent_id: str, served: Mapping[str, Any]) -> dict[str, Any]:
        action = environment_action(served)
        observation, reward, terminated, truncated, _ = self.env.step(action)
        after = _png(observation)
        record = {
            "step_index": len(self.dispatches),
            "intent_id": intent_id,
            "screenshot_before_sha256": _digest(self.screenshot),
            "served_action": dict(served),
            "environment_action": action,
            "reward": float(reward),
            "terminated": bool(terminated),
            "truncated": bool(truncated),
            "screenshot_after_sha256": self._store(after, "png"),
            "backend_checkpoint_sha256": self._store(self.backend.checkpoint(), "ckpt"),
        }
        self.dispatches.append(record)
        self.screenshot = after
        return record

    def save(self, episode_id: str) -> dict[str, Any]:
        ledger = {
            "schema_version": "pixelgym-s8-caller-ledger-v1",
            "episode_id": episode_id,
            "client_episode_ref": self.episode_ref,
            "seed": SEED,
            "task_id": self.task.task_id,
            "initial_screenshot_sha256": self.dispatches[0]["screenshot_before_sha256"]
            if self.dispatches
            else _digest(self.screenshot),
            "initial_checkpoint_sha256": self.initial_checkpoint,
            "dispatches": self.dispatches,
            "environment_actions": self.backend.action_count,
        }
        write_json(self.state / "caller" / "episodes" / f"{episode_id}.json", ledger)
        return ledger

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.env.close()


# Serving stack ------------------------------------------------------------------------------


@dataclass
class Stack:
    control: ControlStore
    stateful: StatefulRuntime
    coordinator: DeploymentCoordinator[Any]
    caller: Caller
    prepared: list[PreparedStatefulPolicy] = field(default_factory=list)

    def close(self) -> None:
        """Stop workers, close service writers and stores; safe to call more than once."""
        try:
            self.stateful.deactivate()
        finally:
            try:
                for item in self.prepared:
                    item.close()
            finally:
                try:
                    self.caller.close()
                finally:
                    self.control.connection.close()


def _refuse_grounding(candidate: Any) -> bool:
    raise TransitionError("the S8 rehearsal serves no grounding candidate")


def build_stack(
    *,
    root: Path,
    state: Path,
    qualification: Path,
    budget: S7SendBudget,
    actor: str,
    preparer_factory: PreparerFactory,
    caller_factory: CallerFactory,
) -> Stack:
    """Build control plane, runtime, coordinator, and API over the persistent state stores."""
    control = ControlStore(
        state / "control.sqlite",
        reviewer_identity=actor,
        approved_stateful_gate_policy_sha256s=(POLICY_SHA256,),
        stateful_gate_verifiers={
            POLICY_SHA256: gate_verifier(
                LocalImmutableStore(qualification / "immutable"), qualification_root=qualification
            )
        },
    )
    try:
        control.migrate()
        stateful = StatefulRuntime()
        kinds = KindAwareRuntime(PolicyRuntime(), stateful)
        preparer = preparer_factory(root, qualification, state, budget)
        prepared: list[PreparedStatefulPolicy] = []

        def recording_preparer(candidate: Any, terms: Any) -> PreparedStatefulPolicy:
            result = preparer(candidate, terms)
            prepared.append(result)
            return result

        coordinator: DeploymentCoordinator[Any] = DeploymentCoordinator(
            control=control,
            store=LocalImmutableStore(qualification / "immutable"),
            load_and_smoke=_refuse_grounding,
            on_activated=kinds.activate,
            validate_activation=kinds.check_activatable,
            stateful_load_and_smoke=recording_preparer,
        )
        app = create_serving_app(
            kinds.grounding,
            operational_log=MemoryOperationalLog(),
            episode_router=create_episode_router(
                host_factory=stateful.host_factory,
                session_registry=ActiveDeploymentEpisodeRegistry(stateful),
                operational_log=ImmutableEpisodeOperationalLog(
                    LocalImmutableStore(state / "immutable")
                ),
            ),
            stateful_readiness=stateful.readiness,
        )
        caller = caller_factory(app)
    except BaseException:
        control.connection.close()
        raise
    return Stack(control, stateful, coordinator, caller, prepared)


# Lifecycle ----------------------------------------------------------------------------------


def _screenshot_body(data: bytes) -> dict[str, str]:
    return {"image_base64": base64.b64encode(data).decode("ascii"), "media_type": "image/png"}


def _error(exc: BaseException) -> dict[str, Any]:
    # Control-plane messages are fixed strings; other exception text may hold private paths.
    return {
        "error_class": type(exc).__name__,
        "message": str(exc) if isinstance(exc, (TransitionError, DeploymentSmokeError)) else None,
    }


class Rehearsal:
    """The seven ordered lifecycle steps over one persistent state directory."""

    def __init__(
        self,
        root: Path,
        state: Path,
        *,
        preparer_factory: PreparerFactory = default_preparer,
        caller_factory: CallerFactory = AsgiCaller,
        require_os_sandbox: bool = True,
    ) -> None:
        self.root, self.state = root.resolve(), state.resolve()
        self.require_os_sandbox = require_os_sandbox
        self.worker_proofs: list[dict[str, Any]] = []
        self.caller_kinds: list[str] = []
        self.plan = read_json(self.state / "plan.json")
        self.qualification = Path(self.plan["qualification_path"])
        self.preparer_factory = preparer_factory
        self.caller_factory = caller_factory
        self.actor: str = self.plan["actor"]
        self.budget = S7SendBudget(self.state / "send-budget.sqlite")
        self.stack: Stack | None = None
        self.environments: list[CallerEnvironment] = []
        self.steps: list[dict[str, Any]] = []
        self.candidates: dict[str, str] = {}
        self.deployments: dict[str, str] = {}
        self.episodes: dict[str, str] = {}
        self.current_step: str | None = None
        self.pending: tuple[CallerEnvironment, str, dict[str, Any]] | None = None
        self.stopped_step: dict[str, Any] | None = None

    # -- helpers --

    def expect(self, condition: bool, message: str) -> None:
        if not condition:
            raise RehearsalStopped(message)

    def counts(self) -> dict[str, int]:
        return self.budget.counts()

    def version_attempts(self) -> dict[str, int]:
        result = {}
        for role, candidate_id in self.candidates.items():
            path = self.state / "packages" / candidate_id / "attempts.sqlite"
            if not path.exists():
                result[role] = 0
                continue
            with closing(V5AttemptJournal(path, read_only=True)) as journal:
                result[role] = journal.call_counts()[0]
        return result

    def active_pointer(self) -> dict[str, Any] | None:
        """The ledger's active pointer; after cleanup, read from the closed control store."""
        if self.stack is None:
            with closing(_read_only(self.state / "control.sqlite")) as db:
                row = db.execute(
                    "SELECT d.deployment_id, d.candidate_id, d.action, p.generation "
                    "FROM active_pointer p JOIN deployments d ON d.deployment_id = p.deployment_id "
                    "WHERE p.singleton = 1"
                ).fetchone()
            if row is None:
                return None
            return dict(zip(("deployment_id", "candidate_id", "action", "generation"), row))
        current, generation = self.stack.control.active()
        if current is None:
            return None
        return {
            "deployment_id": current.deployment_id,
            "candidate_id": current.candidate_id,
            "action": current.action,
            "generation": generation,
        }

    def last_audit(self) -> dict[str, Any]:
        assert self.stack is not None
        event = self.stack.control.audit_events(limit=1, newest_first=True)[0]
        return {
            "event_type": event["event_type"],
            "subject_id": event["subject_id"],
            "stage": event["details"].get("stage"),
            "error_class": event["details"].get("error_class"),
            "candidate_id": event["details"].get("candidate_id"),
        }

    def journal_step_kinds(self, role: str, episode_id: str, step_index: int) -> list[str]:
        path = self.state / "packages" / self.candidates[role] / "attempts.sqlite"
        with closing(V5AttemptJournal(path, read_only=True)) as journal:
            return [
                e.kind
                for e in journal.events(episode_id)
                if e.step_index == step_index and e.kind != "act_received"
            ]

    @contextmanager
    def step(self, name: str) -> Iterator[dict[str, Any]]:
        self.expect(name == STEP_NAMES[len(self.steps)], "lifecycle steps must run in order")
        self.current_step = name
        started = time.monotonic()
        record: dict[str, Any] = {
            "step": len(self.steps) + 1,
            "name": name,
            "fake_sends_before": self.counts(),
            "observations": {},
        }
        try:
            yield record["observations"]
        except BaseException:
            record["elapsed_seconds"] = round(time.monotonic() - started, 6)
            self.stopped_step = record
            raise
        record["fake_sends_after"] = self.counts()
        record["version_attempts_after"] = self.version_attempts()
        record["active_pointer_after"] = self.active_pointer()
        record["elapsed_seconds"] = round(time.monotonic() - started, 6)
        self.steps.append(record)
        self.current_step = None

    def new_environment(self, ref: str) -> CallerEnvironment:
        env = CallerEnvironment(self.state, episode_ref=ref)
        self.environments.append(env)
        return env

    def create(self, env: CallerEnvironment, ref: str | None = None) -> Reply:
        assert self.stack is not None
        return self.stack.caller.post(
            "/api/v2/episodes",
            {
                "schema_version": SESSION_SCHEMA_VERSION,
                "task_instruction": env.task.instruction,
                "screen_width": SCREEN_WIDTH,
                "screen_height": SCREEN_HEIGHT,
                "client_episode_ref": ref or env.episode_ref,
            },
        )

    def act(
        self,
        episode_id: str,
        screenshot: bytes,
        *,
        previous_intent_id: str | None = None,
        previous_result: Mapping[str, Any] | None = None,
    ) -> Reply:
        assert self.stack is not None
        return self.stack.caller.post(
            f"/api/v2/episodes/{episode_id}/act",
            {
                "schema_version": SESSION_SCHEMA_VERSION,
                "screenshot": _screenshot_body(screenshot),
                "previous_intent_id": previous_intent_id,
                "previous_result": None if previous_result is None else dict(previous_result),
            },
        )

    def close_episode(
        self,
        episode_id: str,
        *,
        screenshot: bytes | None,
        final_intent_id: str | None,
        final_result: Mapping[str, Any] | None,
    ) -> Reply:
        assert self.stack is not None
        return self.stack.caller.post(
            f"/api/v2/episodes/{episode_id}/close",
            {
                "schema_version": SESSION_SCHEMA_VERSION,
                "final_screenshot": None if screenshot is None else _screenshot_body(screenshot),
                "final_intent_id": final_intent_id,
                "final_result": None if final_result is None else dict(final_result),
            },
        )

    def approve(self, role: str) -> dict[str, Any]:
        assert self.stack is not None
        control = self.stack.control
        candidate_id = self.candidates[role]
        candidate = control.get_stateful_candidate(candidate_id)
        reason = f"S8 owner approval {self.plan['approval_sha256']} package {role}"
        approval = control.approve(
            candidate_id,
            actor=self.actor,
            reason=reason,
            gate_report_sha256=candidate.gate_report_sha256,
        )
        terms = control.approve_stateful_serving_terms(
            candidate_id,
            actor=self.actor,
            reason=reason,
            deployment_attempt_cap=self.plan["packages"][role]["deployment_attempt_cap"],
            deployment_tier=self.plan["packages"][role]["deployment_tier"],
        )
        return {
            "approval_id": approval["approval_id"],
            "terms_id": terms.terms_id,
            "deployment_attempt_cap": terms.deployment_attempt_cap,
            "deployment_tier": terms.deployment_tier.value,
            "state": control.get_stateful_candidate(candidate_id).state.value,
        }

    def worker_proof(self, event: str) -> dict[str, Any]:
        """Retain the active worker's OS-enforcement proof; refuse an unenforced worker."""
        assert self.stack is not None
        active = self.stack.stateful.active
        self.expect(active is not None, "no active stateful deployment holds a worker")
        assert active is not None
        policy = active.prepared.loaded.policy
        proof = {
            "event": event,
            "deployment_id": active.identity.deployment_id,
            "os_sandbox_applied": bool(policy.os_sandbox_applied),
            "mechanism": policy.sandbox_mechanism,
            "profile_sha256": policy.sandbox_profile_digest,
        }
        self.worker_proofs.append(proof)
        self.expect(
            not self.require_os_sandbox or proof["os_sandbox_applied"] is True,
            "the serving worker lacks OS sandbox enforcement",
        )
        return proof

    def deploy(self, role: str, reason: str) -> dict[str, Any]:
        assert self.stack is not None
        deployment = self.stack.coordinator.deploy(
            self.candidates[role], actor=self.actor, reason=reason
        )
        return {
            "deployment_id": deployment.deployment_id,
            "candidate_id": deployment.candidate_id,
            "generation": deployment.generation,
            "worker": self.worker_proof(f"deploy {role}"),
        }

    def restart(self) -> None:
        """Recreate the app and runtime over the same stores; the ledger is the authority."""
        assert self.stack is not None
        self.stack.close()
        self.stack = None
        self.stack = self.make_stack()

    def make_stack(self) -> Stack:
        stack = build_stack(
            root=self.root,
            state=self.state,
            qualification=self.qualification,
            budget=self.budget,
            actor=self.actor,
            preparer_factory=self.preparer_factory,
            caller_factory=self.caller_factory,
        )
        self.caller_kinds.append(stack.caller.kind)
        return stack

    def noop_episode(self, observations: dict[str, Any], ref: str) -> None:
        """One A episode: create, act, dispatch the NOOP, close with the observed result."""
        env = self.new_environment(ref)
        created = self.create(env)
        self.expect(created.status == 201, "A episode could not be opened")
        episode_id = created.body["episode_id"]
        self.episodes[ref] = episode_id
        acted = self.act(episode_id, env.screenshot)
        self.expect(
            acted.status == 200 and acted.body["sealed_failure"] is None,
            "A act was not served",
        )
        self.expect(acted.body["action"]["action_type"] == "NOOP", "A must serve one NOOP")
        dispatch = env.dispatch(acted.body["intent_id"], acted.body["action"])
        closed = self.close_episode(
            episode_id,
            screenshot=env.screenshot,
            final_intent_id=acted.body["intent_id"],
            final_result=env.result(),
        )
        self.expect(closed.status == 200, "A close was refused")
        env.save(episode_id)
        env.close()
        observations[ref] = {
            "episode_id": episode_id,
            "identity": created.body["identity"],
            "intent_id": acted.body["intent_id"],
            "served_action": acted.body["action"],
            "reward": dispatch["reward"],
            "terminated": dispatch["terminated"],
            "truncated": dispatch["truncated"],
            "close": closed.body,
        }

    # -- steps --

    def step_1(self) -> None:
        assert self.stack is not None
        control = self.stack.control
        with self.step(STEP_NAMES[0]) as observations:
            refs = [
                ArtifactRef(**v)
                for v in read_json(self.qualification / "artifact-references.json")["artifacts"]
            ]
            for role in ("A", "B"):
                package = stateful_package_from_dict(
                    read_json(self.qualification / f"packages/{role}/package.json")
                )
                report = StatefulGateReport.from_dict(
                    read_json(self.qualification / f"packages/{role}/gate-report.json")
                )
                self.expect(
                    content_digest(package.to_dict())
                    == self.plan["packages"][role]["package_sha256"]
                    and content_digest(report.to_dict())
                    == self.plan["packages"][role]["gate_report_sha256"],
                    f"package {role} differs from the approved plan",
                )
                candidate = control.register_stateful_candidate(
                    package=package, gate_report=report, artifacts=refs
                )
                self.candidates[role] = candidate.candidate_id
                observations[f"candidate_{role}"] = {
                    "candidate_id": candidate.candidate_id,
                    "policy_id": candidate.policy_id,
                    "state": candidate.state.value,
                    "gate_policy_sha256": candidate.gate_report.gate_policy_sha256,
                    "gate_overall_passed": candidate.gate_report.overall_passed,
                    "bound_artifacts": len(candidate.artifacts),
                }
            before = self.active_pointer()
            counts_before = self.counts()
            refusal: dict[str, Any] | None = None
            try:
                self.deploy("B", "S8 step 1: unapproved B must be refused")
            except Exception as exc:  # noqa: BLE001 - the refusal itself is the evidence.
                refusal = _error(exc)
            self.expect(refusal is not None, "unapproved B was deployed")
            observations["unapproved_b_deploy"] = {
                "refusal": refusal,
                "active_pointer_before": before,
                "active_pointer_after": self.active_pointer(),
                "fake_sends_unchanged": self.counts() == counts_before,
                "b_attempts": self.version_attempts()["B"],
                "audit": self.last_audit(),
            }
            self.expect(
                self.active_pointer() is None and self.counts() == counts_before,
                "unapproved refusal changed the pointer or the counts",
            )

    def step_2(self) -> None:
        with self.step(STEP_NAMES[1]) as observations:
            observations["approval_A"] = self.approve("A")
            observations["deploy_A"] = self.deploy("A", "S8 step 2: deploy approved A")
            self.deployments["A"] = observations["deploy_A"]["deployment_id"]
            self.noop_episode(observations, "s8-a-noop-1")
            self.expect(self.version_attempts()["A"] == 1, "A must have consumed one of four")

    def step_3(self) -> None:
        with self.step(STEP_NAMES[2]) as observations:
            assert self.stack is not None
            observations["approval_B"] = self.approve("B")
            observations["deploy_B"] = self.deploy("B", "S8 step 3: deploy approved B")
            self.deployments["B"] = observations["deploy_B"]["deployment_id"]
            env = self.new_environment("s8-b-golden")
            created = self.create(env)
            self.expect(created.status == 201, "B golden episode could not be opened")
            episode_id = created.body["episode_id"]
            self.episodes["s8-b-golden"] = episode_id
            active = self.stack.stateful.active
            self.expect(active is not None, "B is not active")
            assert active is not None
            active.host.interrupt_after = INTERRUPTION_BOUNDARY
            counts_before = self.counts()
            interrupted = self.act(episode_id, env.screenshot)
            kinds = self.journal_step_kinds("B", episode_id, 0)
            observations["interruption"] = {
                "boundary": INTERRUPTION_BOUNDARY,
                "status": interrupted.status,
                "code": interrupted.code(),
                "journal_step_kinds": kinds,
                "fake_sends_after_interruption": self.counts(),
                "caller_checkpoint_unchanged": env.current_checkpoint() == env.initial_checkpoint,
                "caller_dispatches": len(env.dispatches),
            }
            self.expect(
                interrupted.status == 503
                and "canonical_response_persisted" in kinds
                and "intent_issued" not in kinds
                and self.counts()["B"] == counts_before.get("B", 0) + 1,
                "interruption did not stop after the canonical response was persisted",
            )
            altered = np.array(Image.open(io.BytesIO(env.screenshot)).convert("RGB"))
            altered[0, 0] ^= 1
            before_probe = self.counts()
            probe = self.act(episode_id, _png(altered))
            observations["changed_context_probe"] = {
                "status": probe.status,
                "code": probe.code(),
                "fake_sends_unchanged": self.counts() == before_probe,
                "b_attempts": self.version_attempts()["B"],
                "caller_dispatches": len(env.dispatches),
            }
            self.expect(
                probe.status == 409
                and probe.code() == "intent_reference_invalid"
                and self.counts() == before_probe
                and not env.dispatches,
                "changed screenshot context was not refused",
            )
            self.restart()
            assert self.stack is not None
            restored = self.stack.coordinator.restore_active()
            self.expect(
                restored is not None and restored.deployment_id == self.deployments["B"],
                "restore_active did not restore the same B deployment",
            )
            assert restored is not None
            observations["restore"] = {
                "deployment_id": restored.deployment_id,
                "same_deployment": restored.deployment_id == self.deployments["B"],
                "fake_sends_after_restore": self.counts(),
                "worker": self.worker_proof("restore_active B"),
            }
            before_recovery = self.counts()
            recovered = self.act(episode_id, env.screenshot)
            self.expect(
                recovered.status == 200 and recovered.body["sealed_failure"] is None,
                "recovered act was not served",
            )
            observations["recovery"] = {
                "status": recovered.status,
                "intent_id": recovered.body["intent_id"],
                "served_action": recovered.body["action"],
                "attempt_count": recovered.body["attempt_count"],
                "identity": recovered.body["identity"],
                "fake_sends_before": before_recovery,
                "fake_sends_after": self.counts(),
                "additional_sends": self.counts()["B"] - before_recovery["B"],
                "journal_step_kinds": self.journal_step_kinds("B", episode_id, 0),
            }
            self.expect(
                self.counts() == before_recovery
                and recovered.body["identity"]["deployment_id"] == self.deployments["B"],
                "recovery consumed a send or changed identity",
            )
            self.pending = (env, episode_id, recovered.body)

    def step_4(self) -> None:
        with self.step(STEP_NAMES[3]) as observations:
            self.expect(self.pending is not None, "B golden episode is not pending")
            assert self.pending is not None
            env, episode_id, reply = self.pending
            rewards = []
            closed: Reply | None = None
            for _ in range(self.plan["golden_action_count"]):
                dispatch = env.dispatch(reply["intent_id"], reply["action"])
                rewards.append(dispatch["reward"])
                if dispatch["terminated"] or dispatch["truncated"]:
                    closed = self.close_episode(
                        episode_id,
                        screenshot=env.screenshot,
                        final_intent_id=reply["intent_id"],
                        final_result=env.result(),
                    )
                    break
                acted = self.act(
                    episode_id,
                    env.screenshot,
                    previous_intent_id=reply["intent_id"],
                    previous_result=env.result(),
                )
                self.expect(
                    acted.status == 200 and acted.body["sealed_failure"] is None,
                    "B golden act was not served",
                )
                reply = acted.body
            self.expect(closed is not None and closed.status == 200, "B golden close failed")
            assert closed is not None
            repeated = self.close_episode(
                episode_id,
                screenshot=env.screenshot,
                final_intent_id=reply["intent_id"],
                final_result=env.result(),
            )
            env.save(episode_id)
            env.close()
            observations["golden_episode"] = {
                "episode_id": episode_id,
                "dispatches": len(env.dispatches),
                "rewards": rewards,
                "reward_one_count": rewards.count(1.0),
                "terminated": env.dispatches[-1]["terminated"],
                "truncated": env.dispatches[-1]["truncated"],
                "close": closed.body,
                "repeated_close_status": repeated.status,
                "repeated_close_identical": repeated.body == closed.body,
                "fake_sends_after_repeat": self.counts(),
            }
            self.expect(
                rewards.count(1.0) == 1
                and rewards[-1] == 1.0
                and len(rewards) == self.plan["golden_action_count"]
                and repeated.body == closed.body
                and self.version_attempts()["B"] == self.plan["golden_action_count"],
                "golden episode did not complete with exactly one terminal reward",
            )

    def step_5(self) -> None:
        with self.step(STEP_NAMES[4]) as observations:
            golden_episode = self.episodes["s8-b-golden"]
            before = self.version_attempts()["B"]
            observations["redeploy_B"] = self.deploy("B", "S8 step 5: redeploy the same B version")
            self.deployments["B-redeploy"] = observations["redeploy_B"]["deployment_id"]
            cap = self.plan["packages"]["B"]["deployment_attempt_cap"]
            golden_count = self.plan["golden_action_count"]
            self.expect(
                self.deployments["B-redeploy"] != self.deployments["B"]
                and self.version_attempts()["B"] == before == golden_count,
                "redeploy changed B's count or reused the deployment id",
            )
            env = self.new_environment("s8-b-cap-probe")
            stale = self.act(golden_episode, env.screenshot)
            observations["old_deployment_episode"] = {
                "episode_id": golden_episode,
                "status": stale.status,
                "code": stale.code(),
            }
            self.expect(stale.code() == "deployment_changed", "old-deployment episode served")
            created = self.create(env)
            self.expect(created.status == 201, "cap-probe episode could not be opened")
            episode_id = created.body["episode_id"]
            self.episodes["s8-b-cap-probe"] = episode_id
            reply = self.act(episode_id, env.screenshot)
            self.expect(reply.status == 200 and reply.body["sealed_failure"] is None, "probe act")
            sends = [self.version_attempts()["B"]]
            # Probe up to the cap: the acts after the golden episode's sends, less the
            # one already issued above, then one more act that must seal without a send.
            for _ in range(cap - golden_count - 1):
                env.dispatch(reply.body["intent_id"], reply.body["action"])
                reply = self.act(
                    episode_id,
                    env.screenshot,
                    previous_intent_id=reply.body["intent_id"],
                    previous_result=env.result(),
                )
                self.expect(
                    reply.status == 200 and reply.body["sealed_failure"] is None, "probe act"
                )
                sends.append(self.version_attempts()["B"])
            env.dispatch(reply.body["intent_id"], reply.body["action"])
            sealed = self.act(
                episode_id,
                env.screenshot,
                previous_intent_id=reply.body["intent_id"],
                previous_result=env.result(),
            )
            after_seal = self.version_attempts()["B"]
            refused = [self.create(env, ref=f"s8-b-after-cap-{i}") for i in range(2)]
            closed = self.close_episode(
                episode_id, screenshot=None, final_intent_id=None, final_result=None
            )
            env.save(episode_id)
            observations["cap_probe"] = {
                "episode_id": episode_id,
                "b_attempts_after_each_act": sends,
                "fourth_result_act": {
                    "status": sealed.status,
                    "sealed_failure": sealed.body.get("sealed_failure"),
                    "intent_id": sealed.body.get("intent_id"),
                },
                "b_attempts_after_seal": after_seal,
                "fake_sends_after_seal": self.counts(),
                "create_after_cap": [{"status": r.status, "code": r.code()} for r in refused],
                "fake_sends_after_refusals": self.counts(),
                "b_attempts_after_refusals": self.version_attempts()["B"],
                "close": closed.body,
            }
            self.expect(
                sends == list(range(golden_count + 1, cap + 1))
                and sealed.status == 200
                and sealed.body.get("sealed_failure") == "cap_reached"
                and after_seal == cap
                and all(r.status == 429 for r in refused)
                and all(r.code() == "deployment_attempt_cap_reached" for r in refused)
                and self.version_attempts()["B"] == cap
                and self.counts()["B"] == cap,
                f"B cap did not seal at exactly {cap} before a further send",
            )

    def step_6(self) -> None:
        with self.step(STEP_NAMES[5]) as observations:
            assert self.stack is not None
            a_before = self.version_attempts()["A"]
            rolled = self.stack.coordinator.rollback(
                actor=self.actor, reason="S8 step 6: roll back to approved A"
            )
            self.deployments["A-rollback"] = rolled.deployment_id
            observations["rollback_to_A"] = {
                "deployment_id": rolled.deployment_id,
                "candidate_id": rolled.candidate_id,
                "action": rolled.action,
                "worker": self.worker_proof("rollback A"),
                "new_deployment_id": rolled.deployment_id != self.deployments["A"],
                "a_attempts_before": a_before,
                "a_attempts_after": self.version_attempts()["A"],
            }
            self.expect(
                rolled.candidate_id == self.candidates["A"]
                and rolled.deployment_id != self.deployments["A"]
                and self.version_attempts()["A"] == a_before == 1,
                "rollback did not restore A with its existing count of one",
            )
            probe_env = self.new_environment("s8-stale-probe")
            stale = self.act(self.episodes["s8-b-cap-probe"], probe_env.screenshot)
            probe_env.close()
            observations["old_b_episode"] = {"status": stale.status, "code": stale.code()}
            self.expect(stale.code() == "deployment_changed", "old B episode was served")
            self.noop_episode(observations, "s8-a-noop-2")
            self.expect(self.version_attempts()["A"] == 2, "A must have consumed two of four")
            pointer = self.active_pointer()
            counts = self.counts()
            for name in ("rollback_to_exhausted_B", "redeploy_exhausted_B"):
                refusal = self.refused_return_to_b(name)
                self.expect(refusal is not None, f"{name} was not refused")
                observations[name] = {
                    "refusal": refusal,
                    "audit": self.last_audit(),
                    "active_pointer_after": self.active_pointer(),
                    "pointer_preserved": self.active_pointer() == pointer,
                    "b_attempts": self.version_attempts()["B"],
                    "fake_sends": self.counts(),
                }
                self.expect(
                    self.active_pointer() == pointer and self.version_attempts()["B"] == 24,
                    f"{name} changed the pointer or B's count",
                )
            observations["smoke_sends_consumed_by_refusals"] = self.counts().get(
                "smoke", 0
            ) - counts.get("smoke", 0)

    def refused_return_to_b(self, name: str) -> dict[str, Any] | None:
        assert self.stack is not None
        try:
            if name == "rollback_to_exhausted_B":
                self.stack.coordinator.rollback(
                    actor=self.actor, reason="S8 step 6: rollback to exhausted B"
                )
            else:
                self.deploy("B", "S8 step 6: redeploy exhausted B")
        except Exception as exc:  # noqa: BLE001 - the refusal itself is the evidence.
            return _error(exc)
        return None

    def step_7(self) -> None:
        with self.step(STEP_NAMES[6]) as observations:
            assert self.stack is not None
            final = self.active_pointer()
            observations["final_active_deployment"] = final
            observations["last_selected_candidate"] = self.candidates["A"]
            self.expect(
                final is not None and final["candidate_id"] == self.candidates["A"],
                "final local state must select A",
            )
            observations["deployment_history"] = [
                {k: row[k] for k in ("deployment_id", "candidate_id", "action", "generation")}
                for row in self.stack.control.deployment_history()
            ]
            self.cleanup()
            observations["workers_stopped"] = True
            observations["environments_closed"] = all(e.closed for e in self.environments)

    def cleanup(self) -> None:
        """Stop workers, caller environments, service writers, and stores."""
        try:
            for env in self.environments:
                env.close()
        finally:
            if self.stack is not None:
                stack, self.stack = self.stack, None
                stack.close()

    def run(self) -> dict[str, Any]:
        """Execute the ordered lifecycle once; retain evidence on success, failure, and stop."""
        self.expect(
            not (self.state / "lifecycle.json").exists()
            and not (self.state / "failure.json").exists()
            and not (self.state / "seal.json").exists(),
            "S8 rehearsal already executed; never rerun or reset its ledger",
        )
        s7_source.verify_host_source(self.root)
        verify_seal(self.qualification)
        self.expect(
            content_digest(read_json(self.qualification / "seal.json"))
            == self.plan["qualification_seal_sha256"]
            and content_digest(read_json(self.state / "approval.json"))
            == self.plan["approval_sha256"]
            and self.plan["gate_policy_sha256"] == POLICY_SHA256
            and self.plan["activation_approved"] is True
            and self.plan["publication_approved"] is False,
            "S8 plan bindings or approval boundary changed",
        )
        verify_runtime(read_json(self.qualification / "runtime-lock.json"))
        self.budget.resume_after_owner_review(self.plan["approval_sha256"])
        started = time.monotonic()
        try:
            self.stack = self.make_stack()
            self.step_1()
            self.step_2()
            self.step_3()
            self.step_4()
            self.step_5()
            self.step_6()
            self.step_7()
        except BaseException as exc:
            try:
                self.cleanup()
            finally:
                self.record_failure(exc, started)
            raise
        lifecycle = self.lifecycle(started)
        write_json(self.state / "lifecycle.json", lifecycle)
        return lifecycle

    def accounting(self) -> dict[str, Any]:
        return {
            "fake_sends": self.counts(),
            "version_attempts": self.version_attempts(),
            "transport": "BudgetedScriptedTransport",
            "real_provider_requests": 0,
            "provider_control_requests": 0,
            "monetary_spend_usd": 0,
            "activation_performed": bool(self.deployments),
            "publication_performed": False,
        }

    def lifecycle(self, started: float) -> dict[str, Any]:
        return {
            "schema_version": LIFECYCLE_SCHEMA,
            "evidence_label": EVIDENCE_LABEL,
            "plan_sha256": content_digest(self.plan),
            "approval_sha256": self.plan["approval_sha256"],
            "actor": self.actor,
            "actor_verification_source": self.actor_verification_source(),
            "os_sandbox_required": self.require_os_sandbox,
            "worker_proofs": self.worker_proofs,
            "caller_kinds": self.caller_kinds,
            "candidates": self.candidates,
            "deployments": self.deployments,
            "episodes": self.episodes,
            "interruption_boundary": INTERRUPTION_BOUNDARY,
            "steps": self.steps,
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "milestone_verdict": None,
            **self.accounting(),
        }

    def actor_verification_source(self) -> str | None:
        """How the control ledger attributed the recorded actor; read from the closed ledger."""
        if not (self.state / "control.sqlite").exists():
            return None
        with closing(_read_only(self.state / "control.sqlite")) as db:
            row = db.execute(
                "SELECT details_json FROM audit_events WHERE event_type = 'candidate.approved' "
                "ORDER BY rowid LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        value = json.loads(row[0]).get(ACTOR_VERIFICATION_SOURCE_KEY)
        return str(value) if value is not None else None

    def record_failure(self, cause: BaseException, started: float) -> None:
        """Retain a sealed terminal failure after writers stop; never reset its ledger."""
        write_json(
            self.state / "failure.json",
            {
                "schema_version": FAILURE_SCHEMA,
                "lifecycle_complete": False,
                "stage": self.current_step,
                "cause": type(cause).__name__,
                "message": str(cause) if isinstance(cause, RehearsalStopped) else None,
                "completed_steps": self.steps,
                "stopped_step": self.stopped_step,
                "os_sandbox_required": self.require_os_sandbox,
                "worker_proofs": self.worker_proofs,
                "caller_kinds": self.caller_kinds,
                "candidates": self.candidates,
                "deployments": self.deployments,
                "episodes": self.episodes,
                "elapsed_seconds": round(time.monotonic() - started, 6),
                "milestone_verdict": None,
                **self.accounting(),
            },
        )
        try:
            seal(self.state)
        except Exception as sealing:  # noqa: BLE001 - the original failure must surface.
            cause.add_note(f"S8 failure evidence was not sealed: {type(sealing).__name__}")


def seal(state: Path) -> dict[str, Any]:
    """Seal the state directory; every writer must be closed first."""
    value = inventory(state)
    if (state / "seal.json").exists():
        (state / "seal.json").unlink()
    write_json(state / "seal.json", value)
    return value


# Semantic verification (read-only) ----------------------------------------------------------


def _read_only(path: Path) -> sqlite3.Connection:
    query = "?mode=ro" if path.with_name(path.name + "-wal").exists() else "?mode=ro&immutable=1"
    return sqlite3.connect(path.resolve().as_uri() + query, uri=True)


def replay_caller_episode(state: Path, ledger: Mapping[str, Any]) -> dict[str, Any]:
    """Replay stored caller actions from the seed; never construct a policy or transport."""
    task = generate_task(ledger["seed"])
    backend = V5FakeBackend()
    env = PixelGuiEnv(
        backend, instruction=task.instruction, max_episode_steps=task.max_episode_steps
    )
    objects = state / "caller" / "objects"
    differences, rewards = [], []
    try:
        observation, _ = env.reset(seed=ledger["seed"])
        if _digest(_png(observation)) != ledger["initial_screenshot_sha256"]:
            raise ValueError("S8 initial caller screenshot differs from replay")
        if _digest(backend.checkpoint()) != ledger["initial_checkpoint_sha256"]:
            raise ValueError("S8 initial backend checkpoint differs from replay")
        for dispatch in ledger["dispatches"]:
            stored_png = (objects / (dispatch["screenshot_after_sha256"][7:] + ".png")).read_bytes()
            if _digest(stored_png) != dispatch["screenshot_after_sha256"]:
                raise ValueError("S8 stored caller screenshot bytes do not match their digest")
            observation, reward, terminated, truncated, _ = env.step(dispatch["environment_action"])
            stored = np.array(Image.open(io.BytesIO(stored_png)).convert("RGB"))
            delta = np.abs(observation.astype(np.int16) - stored.astype(np.int16))
            comparison = {
                "step_index": dispatch["step_index"],
                "differing_pixels": int(np.count_nonzero(np.any(delta != 0, axis=2))),
                "max_channel_delta": int(delta.max()),
            }
            differences.append(comparison)
            if (
                comparison["differing_pixels"]
                or float(reward) != dispatch["reward"]
                or bool(terminated) != dispatch["terminated"]
                or bool(truncated) != dispatch["truncated"]
                or _digest(backend.checkpoint()) != dispatch["backend_checkpoint_sha256"]
                or environment_action(dispatch["served_action"]) != dispatch["environment_action"]
            ):
                raise ValueError(f"S8 caller replay mismatch: {comparison}")
            rewards.append(float(reward))
        return {
            "episode_id": ledger["episode_id"],
            "client_episode_ref": ledger["client_episode_ref"],
            "replayed_dispatches": len(rewards),
            "rewards": rewards,
            "reward_one_count": rewards.count(1.0),
            "screenshot_differences": differences,
            "environment_actions": backend.action_count,
        }
    finally:
        env.close()


def _session_records(path: Path, episode_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with closing(_read_only(path)) as db:
        row = db.execute(
            "SELECT payload FROM serving_sessions WHERE episode_id = ?", (episode_id,)
        ).fetchone()
        if row is None:
            raise ValueError("S8 session store lacks a lifecycle episode")
        session = json.loads(bytes(row[0]))
        records = [
            json.loads(bytes(r[0]))
            for r in db.execute(
                "SELECT payload FROM serving_step_records WHERE episode_id = ? ORDER BY step_index",
                (episode_id,),
            )
        ]
    return session, records


def inspect_rehearsal(state: Path) -> dict[str, Any]:
    """Derive the semantic verification from stored evidence; raise on any inconsistency."""
    state = state.resolve()
    plan, lifecycle = read_json(state / "plan.json"), read_json(state / "lifecycle.json")
    approval = read_json(state / "approval.json")
    if (
        lifecycle["plan_sha256"] != content_digest(plan)
        or lifecycle["approval_sha256"] != content_digest(approval)
        or plan["approval_sha256"] != content_digest(approval)
        or approval["activation_approved"] is not True
        or approval["publication_approved"] is not False
        or lifecycle["publication_performed"] is not False
        or lifecycle["real_provider_requests"] != 0
        or lifecycle["provider_control_requests"] != 0
        or lifecycle["monetary_spend_usd"] != 0
        or lifecycle["transport"] != "BudgetedScriptedTransport"
        or lifecycle["milestone_verdict"] is not None
        or [s["name"] for s in lifecycle["steps"]] != list(STEP_NAMES)
    ):
        raise ValueError("S8 lifecycle bindings, authority, or accounting differ")
    read_json(state / "ledger-backup.json")
    counts, campaign = ledger_state(state / "send-budget.sqlite")
    caps = policy_document()["phase_caps"]
    if (
        counts != lifecycle["fake_sends"]
        or set(counts) - set(_PHASES)
        or any(counts.get(phase, 0) > caps[phase] for phase in _PHASES)
        or sum(counts.values()) > policy_document()["campaign_cap"]
        or counts["qualification"] != QUALIFICATION_CAP
        or campaign["activation_approval"] != plan["approval_sha256"]
    ):
        raise ValueError("S8 durable fake-send ledger differs from the lifecycle or its caps")
    candidates, deployments = lifecycle["candidates"], lifecycle["deployments"]
    with closing(_read_only(state / "control.sqlite")) as db:
        db.row_factory = sqlite3.Row
        history = [
            {k: row[k] for k in ("deployment_id", "candidate_id", "action", "generation")}
            for row in db.execute("SELECT * FROM deployments ORDER BY generation")
        ]
        pointer = db.execute(
            "SELECT deployment_id FROM active_pointer WHERE singleton = 1"
        ).fetchone()
        audit = [
            {
                "event_type": row["event_type"],
                "subject_id": row["subject_id"],
                "details": json.loads(row["details_json"]),
            }
            for row in db.execute("SELECT * FROM audit_events ORDER BY rowid")
        ]
        terms = {
            row["candidate_id"]: dict(row)
            for row in db.execute("SELECT * FROM stateful_serving_terms")
        }
    expected_history = [
        (deployments["A"], candidates["A"], "deploy"),
        (deployments["B"], candidates["B"], "deploy"),
        (deployments["B-redeploy"], candidates["B"], "deploy"),
        (deployments["A-rollback"], candidates["A"], "rollback"),
    ]
    if [(h["deployment_id"], h["candidate_id"], h["action"]) for h in history] != expected_history:
        raise ValueError("S8 deployment history differs from the lifecycle")
    if pointer is None or pointer[0] != deployments["A-rollback"]:
        raise ValueError("S8 final active pointer is not the rollback to A")
    failures = [
        {
            "event_type": e["event_type"],
            "stage": e["details"]["stage"],
            "candidate_id": e["details"]["candidate_id"],
            "error_class": e["details"]["error_class"],
        }
        for e in audit
        if e["event_type"] in {"deployment.deploy_failed", "deployment.rollback_failed"}
    ]
    if [(f["event_type"], f["stage"]) for f in failures] != [
        ("deployment.deploy_failed", "preactivation"),
        ("deployment.rollback_failed", "preactivation"),
        ("deployment.deploy_failed", "preactivation"),
    ]:
        raise ValueError("S8 refused transitions are not recorded as preactivation failures")
    if {
        role: terms[candidates[role]]["deployment_attempt_cap"] for role in ("A", "B")
    } != policy_document()["deployment_attempt_caps"] or any(
        t["deployment_tier"] != "demo" for t in terms.values()
    ):
        raise ValueError("S8 recorded serving terms differ from the approved demo caps")
    attempts: dict[str, int] = {}
    reserved_keys: set[str] = set()
    integrity: dict[str, dict[str, Any]] = {}
    for role in ("A", "B"):
        path = state / "packages" / candidates[role] / "attempts.sqlite"
        with closing(V5AttemptJournal(path, read_only=True)) as journal:
            attempts[role] = journal.call_counts()[0]
            events = journal.events()
            reserved_keys.update(
                e.payload["idempotency_key"] for e in events if e.kind == "attempt_started"
            )
            integrity[role] = journal.integrity_report()
    proofs = lifecycle["worker_proofs"]
    enforcement = {
        "os_sandbox_required": lifecycle["os_sandbox_required"],
        "worker_count": len(proofs),
        "all_enforced": bool(proofs)
        and all(
            p["os_sandbox_applied"] is True
            and p["mechanism"] == "darwin_sandbox_exec"
            and isinstance(p["profile_sha256"], str)
            and p["profile_sha256"].startswith("sha256:")
            for p in proofs
        ),
        "mechanisms": sorted({p["mechanism"] for p in proofs}),
        "caller_kinds": lifecycle["caller_kinds"],
    }
    if [p["event"] for p in proofs] != [
        "deploy A",
        "deploy B",
        "restore_active B",
        "deploy B",
        "rollback A",
    ] or (lifecycle["os_sandbox_required"] and not enforcement["all_enforced"]):
        raise ValueError("S8 worker enforcement proofs are missing or unenforced")
    if attempts != lifecycle["version_attempts"] or attempts != {
        "A": EXPECTED_SENDS["A"],
        "B": EXPECTED_SENDS["B"],
    }:
        raise ValueError("S8 per-version attempt counts differ from the lifecycle")
    if reserved_keys != set(campaign["keys"]["A"]) | set(campaign["keys"]["B"]):
        raise ValueError("S8 deployed-traffic reservations are not the recorded runner requests")
    golden = lifecycle["episodes"]["s8-b-golden"]
    journal_path = state / "packages" / candidates["B"] / "attempts.sqlite"
    with closing(V5AttemptJournal(journal_path, read_only=True)) as journal:
        first_step = [
            e.kind for e in journal.events(golden) if e.step_index == 0 and e.kind != "act_received"
        ]
        cap_probe = lifecycle["episodes"]["s8-b-cap-probe"]
        sealed = journal.event(f"{cap_probe}/episode_sealed")
    # Journal-derived only. The lifecycle's own ``additional_sends`` and ``same_deployment``
    # observations are self-reports; the single attempt and intent at step 0 of the golden
    # episode, together with the reservation and deployment-history checks above, are the
    # independent evidence that recovery sent nothing extra on the same deployment.
    recovery = {
        "attempt_started": first_step.count("attempt_started"),
        "canonical_response_persisted": first_step.count("canonical_response_persisted"),
        "intent_issued": first_step.count("intent_issued"),
    }
    if recovery != {
        "attempt_started": 1,
        "canonical_response_persisted": 1,
        "intent_issued": 1,
    }:
        raise ValueError("S8 recovery evidence shows a duplicate send")
    if sealed is None or sealed.payload.get("sealed_failure") != "cap_reached":
        raise ValueError("S8 cap probe was not sealed as cap_reached")
    replays, server_steps = [], {}
    for path in sorted((state / "caller" / "episodes").glob("*.json")):
        ledger = read_json(path)
        replays.append(replay_caller_episode(state, ledger))
        role = "B" if ledger["episode_id"] in {golden, cap_probe} else "A"
        session, records = _session_records(
            state / "packages" / candidates[role] / "sessions.sqlite", ledger["episode_id"]
        )
        served = [
            (r["screenshot_sha256"], r["action"]) for r in records if r["intent_id"] is not None
        ]
        dispatched = [
            (d["screenshot_before_sha256"], d["served_action"]) for d in ledger["dispatches"]
        ]
        if served != dispatched:
            raise ValueError("S8 server step records differ from the caller dispatch ledger")
        server_steps[ledger["episode_id"]] = {
            "resume_phase": session["resume_phase"],
            "terminal_classification": session["terminal_classification"],
            "step_records": len(records),
            "model_attempts": session["model_attempts"],
        }
    golden_replay = next(r for r in replays if r["episode_id"] == golden)
    if (
        golden_replay["reward_one_count"] != 1
        or golden_replay["rewards"][-1] != 1.0
        or golden_replay["replayed_dispatches"] != plan["golden_action_count"]
        or server_steps[golden]["terminal_classification"] != "terminated"
        or server_steps[cap_probe]["terminal_classification"] != "cap_reached"
    ):
        raise ValueError("S8 golden or cap-probe outcomes differ from the plan")
    store = LocalImmutableStore(state / "immutable")
    operational = {}
    for episode_id in lifecycle["episodes"].values():
        prefix = f"serving-episode-records/{episode_id}"
        present = {
            name: store.get_reference(f"{prefix}/{name}") is not None
            for name in ("opened.json", "closed.json")
        }
        steps = 0
        while store.get_reference(f"{prefix}/steps/{steps:04d}.json") is not None:
            steps += 1
        if not all(present.values()):
            raise ValueError("S8 immutable operational records are incomplete")
        operational[episode_id] = {**present, "steps": steps}
    return {
        "schema_version": VERIFICATION_SCHEMA,
        "lifecycle_sha256": content_digest(lifecycle),
        "plan_sha256": content_digest(plan),
        "approval_sha256": content_digest(approval),
        "fake_sends": counts,
        "campaign_total": sum(counts.values()),
        "version_attempts": attempts,
        "worker_enforcement": enforcement,
        "journal_integrity": integrity,
        "deployment_history": history,
        "final_active_deployment_id": pointer[0],
        "refused_transitions": failures,
        "recovery": recovery,
        "replays": replays,
        "server_episodes": server_steps,
        "operational_records": operational,
        "real_provider_requests": 0,
        "activation_performed": True,
        "publication_performed": False,
    }


# Stored-results-only reporting --------------------------------------------------------------


def stored_report(state: Path) -> dict[str, Any]:
    """Read stored lifecycle, verification, and acceptance records; execute nothing."""
    state = state.resolve()
    verify_seal(state)
    plan = read_json(state / "plan.json")
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA,
        "evidence_label": EVIDENCE_LABEL,
        "plan_sha256": content_digest(plan),
        "approval_sha256": plan["approval_sha256"],
        "source_revision": plan["source_revision"],
        "gate_policy_sha256": plan["gate_policy_sha256"],
        "qualification_plan_sha256": plan["qualification_plan_sha256"],
        "expected_sends": plan["expected_sends"],
        "phase_caps": plan["phase_caps"],
        "campaign_cap": plan["campaign_cap"],
        "milestone_verdict": None,
    }
    if (state / "failure.json").exists():
        failure = read_json(state / "failure.json")
        counts, _ = ledger_state(state / "send-budget.sqlite")
        if counts != failure["fake_sends"]:
            raise ValueError("S8 stopped lifecycle accounting differs from its durable ledger")
        return {
            **report,
            "lifecycle_complete": bool(failure["lifecycle_complete"]),
            "failure": failure,
            "fake_sends": counts,
        }
    lifecycle = read_json(state / "lifecycle.json")
    verification = (
        read_json(state / "verification.json") if (state / "verification.json").exists() else None
    )
    checks = (
        read_json(state / "acceptance-checks.json")
        if (state / "acceptance-checks.json").exists()
        else None
    )
    if verification is not None and verification["lifecycle_sha256"] != content_digest(lifecycle):
        raise ValueError("S8 stored verification is not bound to the stored lifecycle")
    return {
        **report,
        "lifecycle_complete": True,
        "lifecycle": lifecycle,
        "verification": verification,
        "acceptance_checks": checks,
        "fake_sends": lifecycle["fake_sends"],
    }


def _fence(text: str) -> list[str]:
    body = text if text.endswith("\n") else text + "\n"
    return ["```text", *body.rstrip("\n").split("\n"), "```", ""]


def _observation_lines(value: Any, indent: int = 0) -> list[str]:
    prefix = "  " * indent
    if isinstance(value, Mapping):
        lines = []
        for key, item in value.items():
            if isinstance(item, Mapping):
                lines.append(f"{prefix}- {key}:")
                lines.extend(_observation_lines(item, indent + 1))
            else:
                lines.append(f"{prefix}- {key}: `{json.dumps(item, sort_keys=True)}`")
        return lines
    return [f"{prefix}- `{json.dumps(value, sort_keys=True)}`"]


def render_report(report: Mapping[str, Any]) -> str:
    lines = [
        "# S8 scripted serving rehearsal review",
        "",
        (
            "Generated from stored evidence only. No policy, transport, server, backend, or "
            "network client was constructed. No milestone verdict: the human grades S8."
        ),
        "",
        f"Rehearsal plan: `{report['plan_sha256']}`",
        "",
        f"Owner approval record: `{report['approval_sha256']}`",
        "",
        f"Source revision: `{report['source_revision']}`",
        "",
        f"Gate policy: `{report['gate_policy_sha256']}`",
        "",
        (
            f"Phase caps: {json.dumps(report['phase_caps'], sort_keys=True)}; campaign cap "
            f"{report['campaign_cap']}; expected sends "
            f"{json.dumps(report['expected_sends'], sort_keys=True)}."
        ),
        "",
    ]
    if "failure" in report:
        failure = report["failure"]
        lines.extend(
            [
                (
                    "Lifecycle completed; a later stage stopped. The state directory retains its "
                    "evidence and consumed sends."
                    if report["lifecycle_complete"]
                    else "Lifecycle stopped; the state directory retains its evidence and "
                    "consumed sends."
                ),
                "",
                f"Stopped at: `{failure['stage']}`. Cause: {failure['cause']}.",
                "",
                f"Durable fake sends: {json.dumps(report['fake_sends'], sort_keys=True)}.",
                "",
                (
                    f"Activation performed: {failure['activation_performed']}. "
                    f"Real-provider requests: {failure['real_provider_requests']}."
                ),
                "",
                "| Completed step | Elapsed (s) | Fake sends after |",
                "| --- | ---: | --- |",
            ]
        )
        for step in failure["completed_steps"]:
            lines.append(
                f"| {step['step']} {step['name']} | {step['elapsed_seconds']} | "
                f"{json.dumps(step['fake_sends_after'], sort_keys=True)} |"
            )
        lines.append("")
        return "\n".join(lines)
    lifecycle = report["lifecycle"]
    lines.extend(
        [
            (
                f"Durable fake sends: {json.dumps(lifecycle['fake_sends'], sort_keys=True)} "
                f"(total {sum(lifecycle['fake_sends'].values())}). Per-version attempts: "
                f"{json.dumps(lifecycle['version_attempts'], sort_keys=True)}."
            ),
            "",
            (
                f"Real-provider requests: {lifecycle['real_provider_requests']}. Provider control "
                f"requests: {lifecycle['provider_control_requests']}. Recorded spending (USD): "
                f"{lifecycle['monetary_spend_usd']}. These are declared constants of the "
                "fixture-only transport boundary, supported by the durable ledger, not "
                "independent network or billing measurements."
            ),
            "",
            (
                f"Actor recorded: `{lifecycle['actor']}` "
                f"(verification source: {lifecycle['actor_verification_source']}; a local "
                "audit actor is not authenticated human identity; the owner approval record "
                "is the authority)."
            ),
            "",
            (
                f"Worker OS sandbox required: {lifecycle['os_sandbox_required']}. Worker proofs: "
                + "; ".join(
                    f"{p['event']} {p['mechanism']} applied={p['os_sandbox_applied']} "
                    f"profile={p['profile_sha256']}"
                    for p in lifecycle["worker_proofs"]
                )
                + f". Caller: {', '.join(lifecycle['caller_kinds'])}."
            ),
            "",
            f"Lifecycle elapsed: {lifecycle['elapsed_seconds']} s.",
            "",
            "| Step | Name | Elapsed (s) | Fake sends after | Attempts after | Active pointer |",
            "| ---: | --- | ---: | --- | --- | --- |",
        ]
    )
    for step in lifecycle["steps"]:
        pointer = step["active_pointer_after"]
        lines.append(
            f"| {step['step']} | {step['name']} | {step['elapsed_seconds']} | "
            f"{json.dumps(step['fake_sends_after'], sort_keys=True)} | "
            f"{json.dumps(step['version_attempts_after'], sort_keys=True)} | "
            f"{'none' if pointer is None else pointer['deployment_id']} |"
        )
    lines.append("")
    for step in lifecycle["steps"]:
        lines.extend([f"## Step {step['step']}: {step['name']}", ""])
        lines.extend(_observation_lines(step["observations"]))
        lines.append("")
    verification = report["verification"]
    lines.extend(["## Stored semantic verification", ""])
    if verification is None:
        lines.extend(["No stored verification record.", ""])
    else:
        lines.extend(
            [
                f"Verification bound to lifecycle `{verification['lifecycle_sha256']}`.",
                "",
                f"Deployment history: {json.dumps(verification['deployment_history'])}",
                "",
                f"Refused transitions: {json.dumps(verification['refused_transitions'])}",
                "",
                f"Recovery: {json.dumps(verification['recovery'], sort_keys=True)}",
                "",
                (
                    "| Replayed episode | Dispatches | Reward 1 count | Max differing pixels | "
                    "Max channel delta |"
                ),
                "| --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for replay in verification["replays"]:
            diffs = replay["screenshot_differences"]
            lines.append(
                f"| {replay['client_episode_ref']} | {replay['replayed_dispatches']} | "
                f"{replay['reward_one_count']} | "
                f"{max((d['differing_pixels'] for d in diffs), default=0)} | "
                f"{max((d['max_channel_delta'] for d in diffs), default=0)} |"
            )
        lines.append("")
    lines.extend(["## Acceptance checks", ""])
    checks = report["acceptance_checks"]
    if checks is None:
        lines.extend(["No acceptance command records stored.", ""])
    else:
        for check in checks["checks"]:
            lines.extend(
                [
                    f"### {check['name']}",
                    "",
                    f"Command: `{' '.join(check['command'])}`",
                    "",
                    f"Exit status: {check['exit_status']}. Elapsed: {check['elapsed_seconds']} s.",
                    "",
                    "stdout:",
                    "",
                    *_fence(check["stdout"]),
                    "stderr:",
                    "",
                    *_fence(check["stderr"]),
                ]
            )
    lines.extend(
        [
            (
                "The golden script is privileged fixture data and the caller is trusted for its "
                "dispatch reports; stored caller actions are independently replayed by the "
                "verifier. Calibration is a schema compatibility label. These results measure "
                "serving controls, not a model, availability, or storage-enforced retention."
            ),
            "",
        ]
    )
    return "\n".join(lines)
