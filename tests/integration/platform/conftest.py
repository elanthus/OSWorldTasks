"""Isolated, opt-in Docker Compose fixture for platform service acceptance tests."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import pytest

OPT_IN_ENV = "PIXELGYM_RUN_COMPOSE_TESTS"
FIXTURE_ENVIRONMENT = {
    "PIXELGYM_POSTGRES_USER": "pixelgym_demo",
    "PIXELGYM_POSTGRES_PASSWORD": "local_demo_postgres_only",
    "PIXELGYM_MINIO_USER": "pixelgym_demo",
    "PIXELGYM_MINIO_PASSWORD": "local_demo_minio_only",
    "PIXELGYM_REVIEWER_ID": "local-reviewer",
    "PIXELGYM_CSRF_SECRET": "local-demo-csrf-secret-change-before-any-shared-use",
}
LOCAL_DEMO_SECRETS = (
    FIXTURE_ENVIRONMENT["PIXELGYM_POSTGRES_PASSWORD"],
    FIXTURE_ENVIRONMENT["PIXELGYM_MINIO_PASSWORD"],
    FIXTURE_ENVIRONMENT["PIXELGYM_CSRF_SECRET"],
)


def _free_ports(count: int) -> list[int]:
    """Reserve distinct ephemeral ports until the full set has been selected."""
    if count <= 0:
        raise ValueError("port count must be positive")
    listeners: list[socket.socket] = []
    try:
        for _ in range(count):
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.bind(("127.0.0.1", 0))
            listeners.append(listener)
        return [int(listener.getsockname()[1]) for listener in listeners]
    finally:
        for listener in listeners:
            listener.close()


@dataclass(frozen=True)
class ComposeStack:
    repository_root: Path
    project: str
    environment: dict[str, str]
    platform_port: int
    mlflow_port: int
    postgres_port: int
    minio_port: int

    @property
    def platform_url(self) -> str:
        return f"http://127.0.0.1:{self.platform_port}"

    @property
    def mlflow_url(self) -> str:
        return f"http://127.0.0.1:{self.mlflow_port}"

    @property
    def minio_url(self) -> str:
        return f"http://127.0.0.1:{self.minio_port}"

    def redact(self, output: str) -> str:
        redacted = output.replace(str(self.repository_root), "<repository>")
        for secret in LOCAL_DEMO_SECRETS:
            redacted = redacted.replace(secret, "<redacted-local-demo-secret>")
        home = str(Path.home())
        return redacted.replace(home, "<home>")

    def _command(self, *arguments: str) -> list[str]:
        return [
            sys.executable,
            str(self.repository_root / "scripts/platform_compose.py"),
            "--project-name",
            self.project,
            *arguments,
        ]

    def _invoke(self, *arguments: str, timeout: float) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self._command(*arguments),
            cwd=self.repository_root,
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )

    @staticmethod
    def _timeout_output(exc: subprocess.TimeoutExpired) -> str:
        output = exc.stdout or ""
        return output.decode(errors="replace") if isinstance(output, bytes) else output

    def diagnostics(self) -> str:
        chunks: list[str] = []
        for arguments in (("ps", "--all"), ("logs", "--no-color", "--tail", "200")):
            try:
                chunks.append(self._invoke(*arguments, timeout=30).stdout)
            except subprocess.TimeoutExpired as exc:
                chunks.append(
                    f"diagnostic command timed out: {' '.join(arguments)}\n"
                    f"{self._timeout_output(exc)}"
                )
        return "\n".join(chunks)

    def compose(
        self,
        *arguments: str,
        timeout: float = 300,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        try:
            completed = self._invoke(*arguments, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            pytest.fail(
                self.redact(
                    f"Compose command timed out after {timeout:g}s: "
                    f"{' '.join(arguments)}\n{self._timeout_output(exc)}\n{self.diagnostics()}"
                )
            )
        if check and completed.returncode:
            pytest.fail(
                self.redact(
                    f"Compose command failed with exit {completed.returncode}: "
                    f"{' '.join(arguments)}\n{completed.stdout}\n{self.diagnostics()}"
                )
            )
        return completed

    def wait_http(self, path: str, *, timeout: float = 120) -> None:
        deadline = time.monotonic() + timeout
        last_error = "no response"
        while time.monotonic() < deadline:
            try:
                with urlopen(f"{self.platform_url}{path}", timeout=2) as response:
                    if response.status < 500:
                        return
            except HTTPError as exc:
                if exc.code < 500:
                    return
                last_error = f"HTTP {exc.code}"
            except (OSError, URLError) as exc:
                last_error = type(exc).__name__
            time.sleep(0.25)
        pytest.fail(self.redact(f"platform health timeout ({last_error})\n{self.diagnostics()}"))


@pytest.fixture(scope="session")
def compose_stack() -> Iterator[ComposeStack]:
    if os.environ.get(OPT_IN_ENV) != "1":
        pytest.skip(f"set {OPT_IN_ENV}=1 to run fresh-stack integration tests")
    repository_root = Path(__file__).parents[3]
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=repository_root,
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    if status:
        pytest.fail("fresh-stack integration tests require a clean committed worktree")
    ports = _free_ports(4)
    environment = os.environ.copy()
    environment.update(FIXTURE_ENVIRONMENT)
    environment.update(
        {
            "PIXELGYM_PLATFORM_PORT": str(ports[0]),
            "PIXELGYM_MLFLOW_PORT": str(ports[1]),
            "PIXELGYM_POSTGRES_PORT": str(ports[2]),
            "PIXELGYM_MINIO_PORT": str(ports[3]),
        }
    )
    stack = ComposeStack(
        repository_root=repository_root,
        project=f"pixelgym-it-{uuid.uuid4().hex[:12]}",
        environment=environment,
        platform_port=ports[0],
        mlflow_port=ports[1],
        postgres_port=ports[2],
        minio_port=ports[3],
    )
    try:
        stack.compose("up", "--build", "--wait", "--wait-timeout", "240", timeout=600)
        stack.wait_http("/health/live")
        yield stack
    finally:
        completed = stack.compose(
            "down", "--volumes", "--remove-orphans", "--rmi", "local", timeout=180, check=False
        )
        if completed.returncode:
            pytest.fail(
                stack.redact(
                    f"isolated Compose cleanup failed with exit {completed.returncode}\n"
                    f"{completed.stdout}"
                )
            )
