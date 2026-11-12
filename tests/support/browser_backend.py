"""Test-only `Backend` that drives the real vendor-form page in Playwright Chromium.

It exists so opt-in browser tests can run the *same* host contract the OSWorld backend runs --
dispatch an input, wait for the page's input acknowledgement, capture two bitwise-equal frames,
read submissions from the privileged server state -- against the real HTML/JS app on loopback,
with server latency injected where a test wants it. It is not part of the `pixelgym`
distribution, and nothing in `pixelgym` imports it.

`read_form_state()` is privileged test instrumentation (DOM reads) used only to compare against
the fake backend's model. It is never an observation.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import socket
import threading
import time
import urllib.request
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

import numpy as np
import uvicorn
from PIL import Image

from pixelgym.backends.base import Frame
from pixelgym.backends.settle import await_input_acknowledgement
from pixelgym.task_spec import Submission
from pixelgym.tasks.vendor_form.app.server import create_app
from pixelgym.tasks.vendor_form.browser_contract import BROWSER_ARGS, READY_SELECTOR
from pixelgym.tasks.vendor_form.ui import DESIGN_HEIGHT, DESIGN_WIDTH

_READ_FORM_STATE = """() => {
  const form = document.getElementById("vendor-form");
  const radios = Array.from(form.querySelectorAll('input[name="payment_terms"]'));
  const checked = radios.findIndex((radio) => radio.checked);
  const active = document.activeElement;
  const radioFocus = radios.indexOf(active);
  let focus = null;
  if (radioFocus !== -1) focus = "payment_terms";
  else if (active && active.id === "submit-button") focus = "submit";
  else if (active && active !== document.body && active.id) focus = active.id;
  return {
    values: {
      company_name: form.company_name.value,
      contact_email: form.contact_email.value,
      contact_phone: form.contact_phone.value,
      tax_id: form.tax_id.value,
      country: form.country.value,
      payment_terms: checked === -1 ? "" : radios[checked].value,
      expedited_onboarding: form.expedited_onboarding.checked,
    },
    focus: focus,
    payment_focus_index: radioFocus === -1 ? null : radioFocus,
    status: document.getElementById("submit-status").textContent,
  };
}"""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.contextmanager
def vendor_form_server(*, submit_delay: float = 0.0) -> Iterator[str]:
    """Serve the FastAPI task app on loopback, optionally delaying every ``POST /api/submit``.

    The delay is applied inside the server before the request is handled, so a submission is
    recorded only after it -- the shape of a slow guest service or a loaded VM.
    """

    app = create_app()

    if submit_delay:

        @app.middleware("http")
        async def _delay_submit(request: Any, call_next: Any) -> Any:
            if request.method == "POST" and request.url.path == "/api/submit":
                await asyncio.sleep(submit_delay)
            return await call_next(request)

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, name="vendor-form-test-server", daemon=True)
    thread.start()
    deadline = time.monotonic() + 10.0
    while not server.started:
        if time.monotonic() > deadline:
            server.should_exit = True
            raise RuntimeError("vendor-form test server did not start")
        time.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)


def json_request(url: str, payload: Mapping[str, Any] | None = None) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=10.0) as response:
        return json.load(response)


def launch_chromium(playwright: Any) -> Any:
    return playwright.chromium.launch(headless=True, args=list(BROWSER_ARGS))


class PlaywrightVendorFormBackend:
    """`Backend` over one Playwright page showing the task app at ``base_url``.

    With ``acknowledge_inputs=False`` the backend skips the input acknowledgement barrier and
    behaves like the pre-fix OSWorld backend (dispatch, stable frames, one read). Tests use that
    mode only to prove the latency regression test can fail.
    """

    def __init__(
        self,
        page: Any,
        base_url: str,
        *,
        acknowledge_inputs: bool = True,
        ack_timeout: float = 10.0,
    ) -> None:
        self._page = page
        self._base_url = base_url.rstrip("/")
        self._acknowledge_inputs = acknowledge_inputs
        self._ack_timeout = ack_timeout
        self._inputs_dispatched = 0
        self._active = False
        self.last_input_ack_reads: int | None = None

    @property
    def width(self) -> int:
        return DESIGN_WIDTH

    @property
    def height(self) -> int:
        return DESIGN_HEIGHT

    @property
    def app_url(self) -> str:
        return self._base_url + "/"

    def reset(self, seed: int) -> Mapping[str, Any]:
        json_request(f"{self._base_url}/api/reset", {"seed": seed})
        self._page.goto(self._base_url + "/")
        self._page.locator(READY_SELECTOR).wait_for(state="attached")
        self._inputs_dispatched = 0
        self._active = True
        if self._acknowledge_inputs:
            self._await_acknowledgements()
        return dict(json_request(f"{self._base_url}/api/state")["task"])

    def screenshot(self) -> Frame:
        self._require_active()
        previous: Frame | None = None
        for _attempt in range(200):
            png = self._page.screenshot(type="png", animations="disabled")
            with Image.open(io.BytesIO(png)) as image:
                current = np.asarray(image.convert("RGB"), dtype=np.uint8)
            if previous is not None and np.array_equal(previous, current):
                return current.copy()
            previous = current
        raise RuntimeError("browser frame did not become bitwise stable")

    def noop(self) -> None:
        self._require_active()

    def click(self, x: int, y: int) -> None:
        self._require_active()
        self._page.mouse.click(x, y)
        self._settle()

    def key(self, key: str) -> None:
        self._require_active()
        self._page.keyboard.press("Space" if key == " " else key)
        self._settle()

    def read_submissions(self) -> Sequence[Submission]:
        self._require_active()
        state = json_request(f"{self._base_url}/api/state")
        return [Submission.from_record(record) for record in state["submissions"]]

    def read_form_state(self) -> dict[str, Any]:
        """Privileged DOM read for differential comparison only; never an observation."""

        return dict(self._page.evaluate(_READ_FORM_STATE))

    def close(self) -> None:
        self._active = False

    def _settle(self) -> None:
        self._inputs_dispatched += 1
        if self._acknowledge_inputs:
            self._await_acknowledgements()

    def _await_acknowledgements(self) -> None:
        self.last_input_ack_reads = await_input_acknowledgement(
            lambda: int(json_request(f"{self._base_url}/api/input-ack")["acknowledged"]),
            self._inputs_dispatched,
            timeout=self._ack_timeout,
            poll_interval=0.002,
        )

    def _require_active(self) -> None:
        if not self._active:
            raise RuntimeError("PlaywrightVendorFormBackend has no active task; call reset(seed)")


__all__ = [
    "PlaywrightVendorFormBackend",
    "json_request",
    "launch_chromium",
    "vendor_form_server",
]
