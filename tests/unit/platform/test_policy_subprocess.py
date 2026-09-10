"""Fast contract tests for the S5 policy subprocess boundary."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import sysconfig
import time
from pathlib import Path

import pytest

from pixelgym.grounding.v5.evidence import CredentialValidationError
from pixelgym.grounding.v5.runner import PolicyVisibleResult
from pixelgym.platform.fingerprints import canonical_json_bytes
from pixelgym.platform.policy_subprocess import (
    DARWIN_SERVING_PROFILE_VERSION,
    LaunchedPolicyWorker,
    PolicySubprocessUnavailableError,
    PolicyWorkerSpec,
    SandboxedPolicyProcess,
    darwin_serving_profile,
    policy_worker_command,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
NOOP = {"action_type": 0, "x": 0, "y": 0, "key": 0}


class LocalWorkerLauncher:
    """Exercise the protocol only; production rejects this unenforced launcher by default."""

    def __init__(self) -> None:
        self.process: subprocess.Popen[bytes] | None = None

    def launch(self, *, spec: PolicyWorkerSpec, workspace: Path) -> LaunchedPolicyWorker:
        self.process = subprocess.Popen(
            policy_worker_command(
                runtime_executable=Path(sys.executable),
                worker_path=REPOSITORY_ROOT / "pixelgym/platform/policy_worker.py",
                import_roots=spec.import_roots,
            ),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=workspace,
            env={
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": "/usr/bin:/bin",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONIOENCODING": "utf-8",
                "TMPDIR": str(workspace),
            },
        )
        return LaunchedPolicyWorker(
            process=self.process,
            mechanism="unit_test_unenforced",
            profile_digest=None,
            os_sandbox_applied=False,
        )


class PartialLineLauncher:
    def launch(self, *, spec: PolicyWorkerSpec, workspace: Path) -> LaunchedPolicyWorker:
        del spec
        process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                (
                    "import sys,time;sys.stdin.buffer.readline();"
                    "sys.stdout.buffer.write(b'{');sys.stdout.buffer.flush();time.sleep(5)"
                ),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=workspace,
        )
        return LaunchedPolicyWorker(
            process=process,
            mechanism="partial_line_fixture",
            profile_digest=None,
            os_sandbox_applied=False,
        )


def _import_roots(*additional: Path) -> tuple[Path, ...]:
    purelib = Path(sysconfig.get_paths()["purelib"])
    return (REPOSITORY_ROOT, purelib, *additional)


def _scripted_spec() -> PolicyWorkerSpec:
    return PolicyWorkerSpec.build(
        factory_module="pixelgym.grounding.v5.runner",
        factory_name="ScriptedStatefulPolicy",
        factory_kwargs={"actions": [NOOP]},
        import_roots=_import_roots(),
        provider_endpoint="http://127.0.0.1:8765/",
    )


def test_worker_spec_rejects_credentials_and_non_origins() -> None:
    with pytest.raises(CredentialValidationError, match="credential-shaped"):
        PolicyWorkerSpec.build(
            factory_module="fixture",
            factory_name="Policy",
            factory_kwargs={"api_key": "not-allowed"},
            import_roots=_import_roots(),
            provider_endpoint="http://127.0.0.1:8765/",
        )
    with pytest.raises(ValueError, match="without credentials"):
        PolicyWorkerSpec.build(
            factory_module="fixture",
            factory_name="Policy",
            factory_kwargs={},
            import_roots=_import_roots(),
            provider_endpoint="http://user:password@127.0.0.1:8765/",
        )


def test_serving_profile_is_deny_by_default_and_scopes_authority(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    executable = runtime / "bin" / "python"
    source = tmp_path / "policy-source"
    workspace = tmp_path / "workspace"
    protected = source / "runner-private"
    for directory in (runtime, source, workspace):
        directory.mkdir(parents=True, exist_ok=True)

    profile = darwin_serving_profile(
        provider_endpoint="http://127.0.0.1:8765/",
        runtime_root=runtime,
        runtime_executable=executable,
        worker_path=REPOSITORY_ROOT / "pixelgym/platform/policy_worker.py",
        workspace=workspace,
        import_roots=(source,),
        protected_paths=(protected,),
    )

    assert profile.splitlines()[0] == f";; {DARWIN_SERVING_PROFILE_VERSION}"
    assert "(deny default)" in profile
    assert "(allow default)" not in profile
    assert '(allow network-outbound (remote ip "localhost:8765"))' in profile
    assert "(deny network*)" in profile
    assert "(deny network-bind)" in profile
    assert f'(deny file-read* file-write* (subpath "{protected}"))' in profile
    assert (
        f'(allow file-read* (literal "{REPOSITORY_ROOT}/pixelgym/platform/policy_worker.py"))'
        in profile
    )

    with pytest.raises(PolicySubprocessUnavailableError, match="non-loopback"):
        darwin_serving_profile(
            provider_endpoint="https://provider.example/v1",
            runtime_root=runtime,
            runtime_executable=executable,
            worker_path=REPOSITORY_ROOT / "pixelgym/platform/policy_worker.py",
            workspace=workspace,
            import_roots=(source,),
            protected_paths=(),
        )


def test_worker_bootstrap_has_no_pixelgym_application_imports() -> None:
    worker_path = REPOSITORY_ROOT / "pixelgym/platform/policy_worker.py"
    tree = ast.parse(worker_path.read_text(encoding="utf-8"))
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_modules.update(
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )

    assert all(not module.startswith("pixelgym") for module in imported_modules)


def test_unenforced_launcher_is_rejected_by_default() -> None:
    launcher = LocalWorkerLauncher()

    with pytest.raises(PolicySubprocessUnavailableError, match="did not apply"):
        SandboxedPolicyProcess(spec=_scripted_spec(), launcher=launcher)

    assert launcher.process is not None
    assert launcher.process.poll() is not None


def test_partial_worker_output_cannot_bypass_rpc_deadline() -> None:
    started = time.monotonic()
    with pytest.raises(PolicySubprocessUnavailableError, match="timed out"):
        SandboxedPolicyProcess(
            spec=_scripted_spec(),
            launcher=PartialLineLauncher(),
            require_os_sandbox=False,
            request_timeout_seconds=0.1,
        )
    assert time.monotonic() - started < 3.0


def test_policy_protocol_round_trips_only_canonical_values() -> None:
    launcher = LocalWorkerLauncher()
    policy = SandboxedPolicyProcess(
        spec=_scripted_spec(), launcher=launcher, require_os_sandbox=False
    )
    try:
        state = policy.reset("Complete the form")
        request = policy.build_request(state, b"screenshot")
        assert request == {
            "screenshot_digest": request["screenshot_digest"],
            "scripted_action": NOOP,
        }
        response = canonical_json_bytes(
            {
                "content": json.dumps(NOOP, sort_keys=True),
                "finish_reason": "stop",
                "model": "fixture",
                "response_id": "response-1",
                "usage": {"completion_tokens": 1, "prompt_tokens": 1},
            }
        )
        assert policy.retryable_response_code(response) is None
        failed = policy.failure_state(state, "fixture_failure")
        assert json.loads(failed)["history"] == [{"failure_code": "fixture_failure"}]
        reduced = policy.reduce_state(state, response)
        candidate = policy.parse(response, reduced)
        assert candidate == NOOP
        post_parse = policy.post_parse_state(reduced, candidate)
        dispatched = policy.post_dispatch_state(
            post_parse,
            NOOP,
            PolicyVisibleResult(
                screenshot_digest="sha256:" + "0" * 64,
                reward=0.0,
                terminated=False,
                truncated=False,
                step_index=0,
            ),
        )
        assert json.loads(dispatched)["action_index"] == 1
    finally:
        policy.close()

    assert launcher.process is not None
    assert launcher.process.poll() is not None


def test_worker_environment_does_not_inherit_parent_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "policy"
    source.mkdir()
    (source / "fixture_policy.py").write_text(
        "import json, os\n"
        "class EnvironmentPolicy:\n"
        "    def __init__(self, probe_variable): self.probe_variable = probe_variable\n"
        "    def reset(self, task_instruction):\n"
        "        return json.dumps({'instruction': task_instruction, "
        "'credential_visible': self.probe_variable in os.environ}, "
        "sort_keys=True, separators=(',', ':')).encode()\n"
        "    def close(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PIXELGYM_POLICY_TEST_KEY", "fixture-secret")
    spec = PolicyWorkerSpec.build(
        factory_module="fixture_policy",
        factory_name="EnvironmentPolicy",
        factory_kwargs={"probe_variable": "PIXELGYM_POLICY_TEST_KEY"},
        import_roots=_import_roots(source),
        provider_endpoint="http://127.0.0.1:8765/",
    )
    policy = SandboxedPolicyProcess(
        spec=spec, launcher=LocalWorkerLauncher(), require_os_sandbox=False
    )
    try:
        assert json.loads(policy.reset("Complete"))["credential_visible"] is False
    finally:
        policy.close()


def test_policy_request_cannot_carry_transport_credentials(tmp_path: Path) -> None:
    source = tmp_path / "policy"
    source.mkdir()
    (source / "leaking_policy.py").write_text(
        "class LeakingPolicy:\n"
        "    def reset(self, task_instruction): return b'{}'\n"
        "    def build_request(self, state, screenshot):\n"
        "        return {'authorization': 'Bearer fixture-credential-value'}\n"
        "    def close(self): pass\n",
        encoding="utf-8",
    )
    spec = PolicyWorkerSpec.build(
        factory_module="leaking_policy",
        factory_name="LeakingPolicy",
        factory_kwargs={},
        import_roots=_import_roots(source),
        provider_endpoint="http://127.0.0.1:8765/",
    )
    policy = SandboxedPolicyProcess(
        spec=spec, launcher=LocalWorkerLauncher(), require_os_sandbox=False
    )
    try:
        state = policy.reset("Complete")
        with pytest.raises(CredentialValidationError, match="authorization"):
            policy.build_request(state, b"screenshot")
    finally:
        policy.close()
