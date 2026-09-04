"""Public deterministic-browser settings shared by capture and validation."""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import socket
import threading
import time
import urllib.request
from collections.abc import Iterator
from enum import StrEnum

import uvicorn

from pixelgym.tasks.vendor_form.app.server import create_app

READY_SELECTOR = 'body[data-pixelgym-ready="true"]'

CHROMIUM_RENDERER_CONTRACT_VERSION = "pixelgym-chromium-renderer-v1"
CHROMIUM_RENDERER_ARGS = (
    "--disable-font-subpixel-positioning",
    "--disable-gpu",
    "--disable-lcd-text",
    "--disable-skia-runtime-opts",
    "--force-color-profile=srgb",
    "--force-device-scale-factor=1",
)

PLAYWRIGHT_PRESENTATION_ARGS = (
    "--hide-scrollbars",
)

GUEST_CHROMIUM_EXECUTABLE = "google-chrome"
GUEST_VENDOR_FORM_PORT = 3000
GUEST_VENDOR_FORM_URL = f"http://127.0.0.1:{GUEST_VENDOR_FORM_PORT}/"
GUEST_VIEWPORT_SIZE = (1024, 768)
GUEST_PRESENTATION_MODE = "app"
GUEST_PRESENTATION_MODE_FALLBACK_FROM = "kiosk"
GUEST_PRESENTATION_MODE_REASON = (
    "kiosk did not preserve the required full-frame 1024x768 task observation "
    "in the pinned OSWorld guest"
)
GUEST_PRESENTATION_SECURITY_ARGS = (
    "--user-data-dir=/dev/shm/pixelgym-chrome-profile",
    "--no-first-run",
    "--disable-default-apps",
    "--disable-session-crashed-bubble",
    "--disable-dev-shm-usage",
    f"--app={GUEST_VENDOR_FORM_URL}",
    "--start-fullscreen",
    "--window-position=0,0",
    f"--window-size={GUEST_VIEWPORT_SIZE[0]},{GUEST_VIEWPORT_SIZE[1]}",
)

_FIXED_ARG_PATTERN = re.compile(r"[A-Za-z0-9_./,:=+-]+")


class ChromiumLaunchPath(StrEnum):
    """The two fixed consumers of the canonical renderer contract."""

    PLAYWRIGHT = "playwright"
    OSWORLD_GUEST = "osworld-guest"


def _validate_fixed_argv(argv: tuple[str, ...]) -> None:
    if not argv or any(type(token) is not str or not token for token in argv):
        raise ValueError("Chromium argv must contain only non-empty strings")
    if len(argv) != len(set(argv)):
        raise ValueError("Chromium argv must not contain duplicate tokens")
    invalid = [token for token in argv if _FIXED_ARG_PATTERN.fullmatch(token) is None]
    if invalid:
        raise ValueError(f"Chromium argv contains unsafe fixed tokens: {invalid!r}")


def build_chromium_argv(path: ChromiumLaunchPath) -> tuple[str, ...]:
    """Compose and validate one of the two fixed Chromium launch contracts.

    The function intentionally accepts only an enum. In particular, callers cannot
    inject a URL, flag, executable, or shell fragment into the guest command.
    """

    if type(path) is not ChromiumLaunchPath:
        raise TypeError("Chromium launch path must be a ChromiumLaunchPath")
    argv: tuple[str, ...]
    if path is ChromiumLaunchPath.PLAYWRIGHT:
        argv = ("chromium", *CHROMIUM_RENDERER_ARGS, *PLAYWRIGHT_PRESENTATION_ARGS)
    else:
        argv = (
            GUEST_CHROMIUM_EXECUTABLE,
            *CHROMIUM_RENDERER_ARGS,
            *GUEST_PRESENTATION_SECURITY_ARGS,
        )
    _validate_fixed_argv(argv)
    return argv


def chromium_renderer_contract() -> dict[str, object]:
    """Return the content-derived renderer identity recorded by both launch paths."""

    body = json.dumps(
        {
            "version": CHROMIUM_RENDERER_CONTRACT_VERSION,
            "flags": CHROMIUM_RENDERER_ARGS,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return {
        "version": CHROMIUM_RENDERER_CONTRACT_VERSION,
        "identity": f"sha256:{hashlib.sha256(body).hexdigest()}",
        "flags": list(CHROMIUM_RENDERER_ARGS),
    }


# Backward-compatible public capture constant. New launch code calls the builder,
# while older frozen capture modules still receive the exact Playwright argument set.
BROWSER_ARGS = build_chromium_argv(ChromiumLaunchPath.PLAYWRIGHT)[1:]


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.contextmanager
def local_vendor_form_server() -> Iterator[str]:
    """Serve an isolated vendor form on loopback and always shut it down."""
    port = _free_local_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, name="vendor-form-browser-server", daemon=True)
    thread.start()
    deadline = time.monotonic() + 10.0
    health_url = f"http://127.0.0.1:{port}/"
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(health_url, timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.02)
    else:
        server.should_exit = True
        thread.join(timeout=2.0)
        raise RuntimeError("vendor-form browser server did not become ready")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)
        if thread.is_alive():
            raise RuntimeError("vendor-form browser server did not shut down")
