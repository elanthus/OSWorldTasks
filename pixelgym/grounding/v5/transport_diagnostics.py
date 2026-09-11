"""Credential-free transport diagnostics without changing requests or retries."""

from __future__ import annotations

import re
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from pixelgym.grounding.v5.contracts import content_digest
from pixelgym.grounding.v5.journal import V5AttemptJournal

DIAGNOSTICS_VERSION = "request-error-enums-v1"


def safe_error_details(error: BaseException) -> dict[str, Any]:
    """Exclude exception messages, URLs, headers, credentials, and response text."""
    underlying = error.reason if isinstance(error, urllib.error.URLError) else error
    if not isinstance(underlying, BaseException):
        underlying = error
    result: dict[str, Any] = {
        "exception_class": type(error).__name__,
        "underlying_class": type(underlying).__name__,
    }
    for name in ("errno", "verify_code"):
        value = getattr(underlying, name, None)
        if type(value) is int:
            result[name] = value
    for name in ("library", "reason"):
        value = getattr(underlying, name, None)
        if (
            isinstance(underlying, ssl.SSLError)
            and isinstance(value, str)
            and re.fullmatch(r"[A-Z0-9_]{1,96}", value)
        ):
            result[f"ssl_{name}"] = value
    return result


class DiagnosticUrlopen:
    """Observe the existing urlopen call and response read; never resend either."""

    def __init__(
        self,
        journal: V5AttemptJournal,
        *,
        phase_id: str,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self.journal, self.phase_id, self.urlopen = journal, phase_id, urlopen

    def __call__(self, request: urllib.request.Request, **kwargs: Any) -> Any:
        key = request.get_header("Idempotency-key")
        if not isinstance(key, str) or not key:
            raise ValueError("diagnostic request lacks its idempotency identity")
        rid = content_digest(key)
        data = request.data
        body_bytes = len(data) if isinstance(data, (bytes, bytearray)) else None

        def record(error: BaseException, stage: str) -> None:
            self.journal.append_event(
                event_key=f"{self.phase_id}/transport/{rid}/{stage}",
                kind="transport_error_diagnostic",
                trial_id=self.phase_id,
                step_index=0,
                payload={
                    "diagnostics_version": DIAGNOSTICS_VERSION,
                    "reservation_id": rid,
                    "request_stage": stage,
                    "request_body_bytes": body_bytes,
                    **safe_error_details(error),
                },
            )

        try:
            response = self.urlopen(request, **kwargs)
        except (OSError, urllib.error.URLError) as error:
            record(error, "open_connect_or_send")
            raise

        class Response:
            def __enter__(self) -> Any:
                self.raw = response.__enter__()
                return self

            def __exit__(self, *args: object) -> Any:
                return response.__exit__(*args)

            def read(self, *args: Any, **read_kwargs: Any) -> Any:
                try:
                    return self.raw.read(*args, **read_kwargs)
                except (OSError, urllib.error.URLError) as error:
                    record(error, "read_response")
                    raise

        return Response()
