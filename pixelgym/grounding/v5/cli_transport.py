"""Shared fail-closed subprocess lifecycle for distinct CLI provider adapters."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

from pixelgym.grounding.v5.contracts import (
    CliFault,
    CliFaultKind,
    classify_cli_process_fault,
    cli_pre_send_fault,
    cli_timeout_fault,
)
from pixelgym.grounding.v5.evidence import RedactedRawStdio, redact_raw_stdio


class RunningProcess(Protocol):
    pid: int
    returncode: int | None

    def communicate(
        self, input: str | None = None, timeout: float | None = None
    ) -> tuple[str, str]: ...

    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...


def start_process(command: Sequence[str], **kwargs: Any) -> RunningProcess:
    return cast(RunningProcess, subprocess.Popen(command, **kwargs))


@dataclass(frozen=True)
class StreamParseEnvelope[ParsedT]:
    """Provider parser output plus only the facts shared fault classification needs."""

    parsed: ParsedT
    stream_malformed: bool = False
    subscription_rate_limited: bool = False
    usage_observed: bool = False
    cost_observed: bool = False


@dataclass(frozen=True)
class CliExecutionEnvelope[ParsedT]:
    """Credential-safe result of one process; raw streams never leave this boundary."""

    process_id: int | None
    return_code: int | None
    stdout: RedactedRawStdio
    stderr: RedactedRawStdio
    parsed: ParsedT | None
    fault: CliFault | None
    error_type: str | None
    process_confirmed_stopped: bool


class CliProcessInterrupted(BaseException):
    """Carry sanitized process evidence while preserving interruption semantics."""

    def __init__(self, cause: BaseException, envelope: CliExecutionEnvelope[Any]) -> None:
        super().__init__(type(cause).__name__)
        self.cause = cause
        self.envelope = envelope


class CliSubprocessTransport[ParsedT]:
    """Own process start, deadline, cancellation, parsing, redaction, and faults."""

    def __init__(
        self,
        *,
        parser: Callable[[str], StreamParseEnvelope[ParsedT]],
        process_factory: Callable[..., RunningProcess] = start_process,
        terminate_grace_seconds: float = 2.0,
        kill_grace_seconds: float = 2.0,
    ) -> None:
        if terminate_grace_seconds <= 0 or kill_grace_seconds <= 0:
            raise ValueError("CLI process termination grace periods must be positive")
        self.parser = parser
        self.process_factory = process_factory
        self.terminate_grace_seconds = terminate_grace_seconds
        self.kill_grace_seconds = kill_grace_seconds
        self._active: dict[str, RunningProcess] = {}
        self._started: list[RunningProcess] = []
        self._lock = threading.Lock()
        self._closed = False

    @property
    def subprocesses_closed(self) -> bool:
        with self._lock:
            return all(process.poll() is not None for process in self._started)

    def run(
        self,
        *,
        invocation_id: str,
        command: Sequence[str],
        input_text: str,
        cwd: os.PathLike[str] | str,
        environment: Mapping[str, str],
        timeout_seconds: float,
        timeout_failure_code: str,
        on_started: Callable[[RunningProcess], None],
    ) -> CliExecutionEnvelope[ParsedT]:
        if self._closed:
            return self._empty(cli_pre_send_fault("transport_closed"))
        if timeout_seconds <= 0:
            return self._empty(cli_pre_send_fault("process_timeout_invalid"))
        try:
            process = self.process_factory(
                command,
                cwd=cwd,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except (OSError, ValueError) as exc:
            return self._empty(
                cli_pre_send_fault(
                    "process_start_failure", kind=CliFaultKind.PROCESS_START
                ),
                error_type=type(exc).__name__,
            )
        with self._lock:
            self._started.append(process)
            self._active[invocation_id] = process
        try:
            try:
                on_started(process)
            except BaseException as exc:
                raw_stdout, raw_stderr = self._terminate(process)
                fault = classify_cli_process_fault(
                    return_code=process.poll(),
                    stderr=raw_stderr,
                    stream_malformed=False,
                    error_type=type(exc).__name__,
                )
                if fault is None:
                    raise RuntimeError(
                        "CLI post-start callback failure was not classified"
                    ) from exc
                raise CliProcessInterrupted(
                    exc,
                    self._result(
                        process,
                        raw_stdout,
                        raw_stderr,
                        parsed=None,
                        fault=fault,
                        error_type=type(exc).__name__,
                    ),
                ) from exc
            try:
                raw_stdout, raw_stderr = process.communicate(
                    input_text, timeout=timeout_seconds
                )
            except subprocess.TimeoutExpired:
                raw_stdout, raw_stderr = self._terminate(process)
                return self._result(
                    process,
                    raw_stdout,
                    raw_stderr,
                    parsed=None,
                    fault=cli_timeout_fault(timeout_failure_code),
                )
            except OSError as exc:
                raw_stdout, raw_stderr = self._terminate(process)
                fault = classify_cli_process_fault(
                    return_code=process.poll(),
                    stderr=raw_stderr,
                    stream_malformed=False,
                    error_type=type(exc).__name__,
                )
                if fault is None:
                    raise RuntimeError("CLI process exception was not classified") from exc
                return self._result(
                    process,
                    raw_stdout,
                    raw_stderr,
                    parsed=None,
                    fault=fault,
                    error_type=type(exc).__name__,
                )
            except BaseException as exc:
                raw_stdout, raw_stderr = self._terminate(process)
                fault = classify_cli_process_fault(
                    return_code=process.poll(),
                    stderr=raw_stderr,
                    stream_malformed=False,
                    error_type=type(exc).__name__,
                )
                if fault is None:
                    raise RuntimeError("CLI process interruption was not classified") from exc
                raise CliProcessInterrupted(
                    exc,
                    self._result(
                        process,
                        raw_stdout,
                        raw_stderr,
                        parsed=None,
                        fault=fault,
                        error_type=type(exc).__name__,
                    ),
                ) from exc
            parsed = self.parser(raw_stdout)
            fault = classify_cli_process_fault(
                return_code=process.returncode,
                stderr=raw_stderr,
                stream_malformed=parsed.stream_malformed,
                subscription_rate_limited=parsed.subscription_rate_limited,
                usage_observed=parsed.usage_observed,
                cost_observed=parsed.cost_observed,
            )
            return self._result(
                process,
                raw_stdout,
                raw_stderr,
                parsed=parsed.parsed,
                fault=fault,
            )
        finally:
            with self._lock:
                self._active.pop(invocation_id, None)

    def cancel(self, invocation_id: str) -> bool:
        with self._lock:
            process = self._active.get(invocation_id)
        if process is None:
            return False
        self._terminate(process)
        return process.poll() is not None

    def close(self) -> None:
        with self._lock:
            active = list(self._active.values())
        for process in active:
            self._terminate(process)
        self._closed = True

    def _terminate(self, process: RunningProcess) -> tuple[str, str]:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                process.terminate()
        try:
            return process.communicate(timeout=self.terminate_grace_seconds)
        except subprocess.TimeoutExpired:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    process.kill()
            try:
                return process.communicate(timeout=self.kill_grace_seconds)
            except subprocess.TimeoutExpired:
                return "", ""

    @staticmethod
    def _empty(
        fault: CliFault, *, error_type: str | None = None
    ) -> CliExecutionEnvelope[ParsedT]:
        empty = redact_raw_stdio("")
        return CliExecutionEnvelope(
            None, None, empty, empty, None, fault, error_type, True
        )

    @staticmethod
    def _result(
        process: RunningProcess,
        raw_stdout: str,
        raw_stderr: str,
        *,
        parsed: ParsedT | None,
        fault: CliFault | None,
        error_type: str | None = None,
    ) -> CliExecutionEnvelope[ParsedT]:
        return CliExecutionEnvelope(
            process_id=process.pid,
            return_code=process.poll(),
            stdout=redact_raw_stdio(raw_stdout),
            stderr=redact_raw_stdio(raw_stderr),
            parsed=parsed,
            fault=fault,
            error_type=error_type,
            process_confirmed_stopped=process.poll() is not None,
        )
