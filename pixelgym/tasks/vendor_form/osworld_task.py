"""Open OSWorld task wrapper for the deterministic vendor form.

This module intentionally has no import-time dependency on OSWorld.  The
class returned by :func:`create_osworld_task` is created only when the
optional integration is used and derives from that installed release's
public ``desktop_env.task_base.BaseTask``.

Task setup and privileged evaluation are distinct from agent actions.  Setup
uploads a deterministic, dependency-free application bundle and runs only
fixed commands.  Evaluation reads ``/api/state`` through the trusted OSWorld
controller and feeds immutable ``Submission`` values into PixelGym's existing
host-side evaluator.  Neither path is exposed through ``PixelGuiEnv``'s
NOOP/CLICK/KEY action space.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any, cast

from pixelgym.env import DEFAULT_INSTRUCTION, DEFAULT_MAX_EPISODE_STEPS
from pixelgym.evaluator import evaluate
from pixelgym.task_spec import Submission, TaskSpec
from pixelgym.tasks.vendor_form import generator

APP_PORT = 3000
APP_URL = f"http://127.0.0.1:{APP_PORT}/"
GUEST_ROOT = Path("/tmp/pixelgym-vendor-form")
GUEST_BUNDLE = Path("/tmp/pixelgym-vendor-form.zip")
GUEST_SERVER_LOG = Path("/tmp/pixelgym-vendor-form-server.log")
GUEST_CHROME_LOG = Path("/tmp/pixelgym-vendor-form-chrome.log")

_APP_DIR = Path(__file__).parent / "app"
_BUNDLE_FILES = (
    Path("guest_server.py"),
    Path("static/app.js"),
    Path("static/index.html"),
    Path("static/style.css"),
    Path("static/fonts/DejaVuSans.ttf"),
    Path("static/fonts/DejaVuSans-Bold.ttf"),
    Path("static/fonts/LICENSE-DejaVu.txt"),
)
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


class OSWorldTaskError(RuntimeError):
    """The custom task could not be installed or its state was invalid."""


def build_guest_bundle(task_record: dict[str, Any], destination: Path) -> str:
    """Build a byte-reproducible guest bundle and return its SHA-256.

    ZIP member order, timestamps, permissions, and JSON serialization are all
    fixed.  The task record itself is generated on the host and included as
    ``task.json``; the guest service never regenerates or silently alters it.
    """

    destination.parent.mkdir(parents=True, exist_ok=True)
    members: list[tuple[str, bytes]] = []
    for relative in _BUNDLE_FILES:
        members.append((relative.as_posix(), (_APP_DIR / relative).read_bytes()))
    members.append(
        (
            "task.json",
            generator.canonical_json(task_record).encode("utf-8"),
        )
    )

    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for name, body in sorted(members):
            info = zipfile.ZipInfo(name, date_time=_ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            zf.writestr(info, body, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return hashlib.sha256(destination.read_bytes()).hexdigest()


class _VendorFormTaskSupport:
    """Implementation mixed into the installed release's ``BaseTask``."""

    def __init__(
        self,
        *,
        record: dict[str, Any],
        bundle_path: Path,
        bundle_sha256: str,
        instruction: str,
        max_episode_steps: int,
    ) -> None:
        super().__init__(  # type: ignore[call-arg]  # optional BaseTask is loaded dynamically
            id=record["task_id"],
            instruction=instruction,
            source="pixelgym-open-vendor-form",
            platform="linux",
            proxy=False,
            disable_recording=True,
            intermediate_eval_safe=True,
        )
        self._record = json.loads(generator.canonical_json(record))
        self.instruction = instruction
        self._bundle_path = Path(bundle_path)
        self._bundle_sha256 = bundle_sha256
        self._max_episode_steps = max_episode_steps

    @property
    def bundle_sha256(self) -> str:
        return self._bundle_sha256

    def setup(self, setup_controller: Any, use_proxy: bool = False) -> None:
        if use_proxy:
            raise OSWorldTaskError("the open vendor task does not use a proxy")

        setup_controller.download([{"url": str(self._bundle_path), "path": str(GUEST_BUNDLE)}])

        # These are trusted, fixed setup commands.  No agent value can enter
        # this path.  Every destructive target is the explicit task-owned
        # /tmp directory inside the disposable OSWorld guest.
        setup_controller.execute(
            [
                "bash",
                "-lc",
                (
                    "pkill -f '/tmp/pixelgym-vendor-form/[g]uest_server.py' "
                    "2>/dev/null || true; "
                    "pkill -f '[g]oogle-chrome.*pixelgym-chrome-profile' "
                    "2>/dev/null || true; "
                    "rm -rf /tmp/pixelgym-vendor-form; "
                    "rm -rf /tmp/pixelgym-chrome-profile; "
                    "mkdir -p /tmp/pixelgym-vendor-form; "
                    "python3 -m zipfile -e /tmp/pixelgym-vendor-form.zip "
                    "/tmp/pixelgym-vendor-form"
                ),
            ],
            quiet=True,
            timeout=60,
        )
        setup_controller.launch(
            [
                "bash",
                "-lc",
                (
                    f"exec python3 {GUEST_ROOT / 'guest_server.py'} --host 127.0.0.1 "
                    f"--port {APP_PORT} --task-json {GUEST_ROOT / 'task.json'} "
                    f">{GUEST_SERVER_LOG} 2>&1"
                ),
            ]
        )

        ready_name = "pixelgym-vendor-form-ready.txt"
        ready_path = Path(setup_controller.cache_dir) / ready_name
        ready_path.unlink(missing_ok=True)
        ready_script = (
            "import time,urllib.request\n"
            "last_error = None\n"
            "for attempt in range(240):\n"
            " try:\n"
            f"  body=urllib.request.urlopen('{APP_URL}healthz',timeout=1).read()\n"
            "  print('READY')\n"
            "  break\n"
            " except Exception as exc:\n"
            "  last_error = repr(exc)\n"
            "  if attempt == 239: raise RuntimeError(last_error)\n"
            "  time.sleep(0.25)\n"
        )
        ready_error_name = "pixelgym-vendor-form-ready-error.txt"
        ready_error_path = Path(setup_controller.cache_dir) / ready_error_name
        ready_error_path.unlink(missing_ok=True)
        setup_controller.execute(
            ["python3", "-c", ready_script],
            stdout=ready_name,
            stderr=ready_error_name,
            quiet=True,
            timeout=75,
        )
        if not ready_path.is_file() or ready_path.read_text(encoding="utf-8").strip() != "READY":
            server_log_name = "pixelgym-vendor-form-server-log.txt"
            server_log_path = Path(setup_controller.cache_dir) / server_log_name
            server_log_path.unlink(missing_ok=True)
            setup_controller.execute(
                ["bash", "-lc", f"cat {GUEST_SERVER_LOG} 2>/dev/null || true"],
                stdout=server_log_name,
                quiet=True,
                timeout=15,
            )
            ready_error = (
                ready_error_path.read_text(encoding="utf-8").strip()
                if ready_error_path.is_file()
                else "missing"
            )
            server_log = (
                server_log_path.read_text(encoding="utf-8").strip()
                if server_log_path.is_file()
                else "missing"
            )
            raise OSWorldTaskError(
                "vendor-form service did not reach its readiness endpoint; "
                f"probe_error={ready_error[-2000:]!r}; server_log={server_log[-4000:]!r}"
            )

        reset_script = (
            "import json,urllib.request;"
            "req=urllib.request.Request("
            f"'{APP_URL}api/reset',"
            f"data=json.dumps({{'seed':{self._record['seed']}}}).encode(),"
            "headers={'Content-Type':'application/json'},method='POST');"
            "print(urllib.request.urlopen(req,timeout=5).read().decode())"
        )
        reset_name = "pixelgym-vendor-form-reset.json"
        reset_path = Path(setup_controller.cache_dir) / reset_name
        reset_path.unlink(missing_ok=True)
        setup_controller.execute(
            ["python3", "-c", reset_script],
            stdout=reset_name,
            quiet=True,
            timeout=15,
        )
        try:
            reset_result = json.loads(reset_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise OSWorldTaskError("guest reset did not return valid JSON") from exc
        expected_identity = {
            "task_id": self._record["task_id"],
            "seed": self._record["seed"],
        }
        if reset_result != expected_identity:
            raise OSWorldTaskError(
                f"guest reset identity mismatch: expected {expected_identity}, got {reset_result}"
            )

        desktop_ready_name = "pixelgym-vendor-form-desktop-ready.txt"
        desktop_ready_path = Path(setup_controller.cache_dir) / desktop_ready_name
        desktop_ready_path.unlink(missing_ok=True)
        desktop_ready_script = (
            "import os,subprocess,time\n"
            "env=os.environ.copy()\n"
            "env['DISPLAY']=':0'\n"
            "last='not attempted'\n"
            "for attempt in range(480):\n"
            " try:\n"
            "  result=subprocess.run(['wmctrl','-m'],env=env,capture_output=True,text=True,"
            "timeout=2)\n"
            "  last=(result.stdout+result.stderr).strip()\n"
            "  if result.returncode == 0:\n"
            "   print('DESKTOP_READY')\n"
            "   break\n"
            " except Exception as exc:\n"
            "  last=repr(exc)\n"
            " if attempt == 479: raise RuntimeError(last)\n"
            " time.sleep(0.25)\n"
        )
        setup_controller.execute(
            ["python3", "-c", desktop_ready_script],
            stdout=desktop_ready_name,
            quiet=True,
            timeout=135,
        )
        if (
            not desktop_ready_path.is_file()
            or desktop_ready_path.read_text(encoding="utf-8").strip() != "DESKTOP_READY"
        ):
            raise OSWorldTaskError("guest window manager/session bus did not become ready")

        # Launch directly into the task through the guest's real graphical
        # session.  This is the normal path for an accelerated OSWorld host;
        # the page reports its own task-bound render-complete marker below.
        setup_controller.launch(
            [
                "bash",
                "-lc",
                (
                    "export DISPLAY=:0; "
                    "export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus; "
                    "exec google-chrome --user-data-dir=/tmp/pixelgym-chrome-profile "
                    "--no-first-run --disable-default-apps "
                    "--disable-session-crashed-bubble --disable-gpu "
                    "--disable-dev-shm-usage --start-maximized "
                    f"{APP_URL} >{GUEST_CHROME_LOG} 2>&1"
                ),
            ]
        )
        page_ready_name = "pixelgym-vendor-form-page-ready.txt"
        page_ready_path = Path(setup_controller.cache_dir) / page_ready_name
        page_ready_path.unlink(missing_ok=True)
        page_ready_script = (
            "import json,time,urllib.request\n"
            "last = None\n"
            "for attempt in range(240):\n"
            " try:\n"
            f"  value=json.load(urllib.request.urlopen('{APP_URL}api/page-ready',timeout=1))\n"
            "  if value == {'ready': True}:\n"
            "   print('PAGE_READY')\n"
            "   break\n"
            " except Exception as exc:\n"
            "  last = repr(exc)\n"
            " if attempt == 239: raise RuntimeError(last or 'page did not report ready')\n"
            " time.sleep(0.25)\n"
        )
        setup_controller.execute(
            ["python3", "-c", page_ready_script],
            stdout=page_ready_name,
            quiet=True,
            timeout=75,
        )
        if (
            not page_ready_path.is_file()
            or page_ready_path.read_text(encoding="utf-8").strip() != "PAGE_READY"
        ):
            diagnostic_name = "pixelgym-vendor-form-chrome-diagnostic.txt"
            diagnostic_path = Path(setup_controller.cache_dir) / diagnostic_name
            diagnostic_path.unlink(missing_ok=True)
            setup_controller.execute(
                [
                    "bash",
                    "-lc",
                    (
                        f"cat {GUEST_CHROME_LOG} 2>&1 || true; "
                        "wmctrl -l 2>&1 || true; "
                        "pgrep -af 'chrome|chromium' 2>&1 || true"
                    ),
                ],
                stdout=diagnostic_name,
                quiet=True,
                timeout=15,
            )
            diagnostic = (
                diagnostic_path.read_text(encoding="utf-8").strip()
                if diagnostic_path.is_file()
                else "missing"
            )
            raise OSWorldTaskError(
                "managed Chromium task page did not report render completion; "
                f"guest_diagnostic={diagnostic[-6000:]!r}"
            )
        setup_controller.execute(
            [
                "bash",
                "-lc",
                (
                    "wmctrl -a 'Vendor Onboarding' 2>/dev/null || true; "
                    "wmctrl -r 'Vendor Onboarding' -b add,maximized_vert,maximized_horz "
                    "2>/dev/null || true"
                ),
            ],
            quiet=True,
            timeout=15,
        )
        setup_controller.execute(
            [
                "python3",
                "-c",
                (
                    "import pyautogui; "
                    "pyautogui.click(100,150); "
                    f"pyautogui.moveTo({setup_controller.screen_width - 10},"
                    f"{setup_controller.screen_height - 10},duration=0)"
                ),
            ],
            quiet=True,
            timeout=15,
        )

    def read_privileged_state(self, env: Any) -> dict[str, Any]:
        script = (
            "python3 - <<'PY'\n"
            "import urllib.request\n"
            f"print(urllib.request.urlopen('{APP_URL}api/state',timeout=5).read().decode())\n"
            "PY\n"
        )
        result = env.controller.run_bash_script(script, timeout=15)
        if not isinstance(result, dict) or result.get("returncode") != 0:
            raise OSWorldTaskError(f"privileged state query failed: {result!r}")
        try:
            state = json.loads(result.get("output", "").strip())
        except json.JSONDecodeError as exc:
            raise OSWorldTaskError("privileged state query returned invalid JSON") from exc
        if state.get("task") != self._record:
            raise OSWorldTaskError("privileged task state does not match the active host task")
        if not isinstance(state.get("submissions"), list):
            raise OSWorldTaskError("privileged submission state is not a list")
        return cast(dict[str, Any], state)

    def read_submissions(self, env: Any) -> list[Submission]:
        state = self.read_privileged_state(env)
        return [Submission.from_record(record) for record in state["submissions"]]

    def evaluate(self, env: Any) -> dict[str, Any]:
        """Evaluate the final privileged history using the core latest-submission rule.

        Native OSWorld calls this at episode end rather than after every action. Therefore a later
        invalid submission supersedes an earlier valid one here; unlike ``PixelGuiEnv``, the native
        harness does not terminate immediately when the earlier valid event is recorded.
        """
        task = TaskSpec.from_generated(
            self._record,
            instruction=self.instruction,
            app_url=APP_URL,
            max_episode_steps=self._max_episode_steps,
        )
        return dataclasses.asdict(evaluate(task, self.read_submissions(env)))


def create_osworld_task(
    seed: int,
    *,
    cache_dir: Path,
    instruction: str = DEFAULT_INSTRUCTION,
    max_episode_steps: int = DEFAULT_MAX_EPISODE_STEPS,
) -> tuple[Any, dict[str, Any]]:
    """Return ``(BaseTask instance, generated task record)`` for ``seed``."""

    try:
        from desktop_env.task_base import BaseTask
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise RuntimeError(
            "OSWorld-V2 is not installed; install PixelGym's 'osworld' extra"
        ) from exc

    class VendorFormOSWorldTask(_VendorFormTaskSupport, BaseTask):  # type: ignore[misc]
        """Release-native custom task created against OSWorld's public API."""

    VendorFormOSWorldTask.__name__ = "VendorFormOSWorldTask"
    record = generator.generate_task(seed)
    candidate_path = cache_dir / f"vendor-form-{record['task_id']}.zip"
    bundle_sha256 = build_guest_bundle(record, candidate_path)
    # SetupController caches downloads by source URL.  Content-address the
    # filename so a task-app code change cannot silently reuse an older guest
    # bundle for the same deterministic task ID.
    bundle_path = cache_dir / f"vendor-form-{record['task_id']}-{bundle_sha256[:16]}.zip"
    candidate_path.replace(bundle_path)
    task = VendorFormOSWorldTask(
        record=record,
        bundle_path=bundle_path,
        bundle_sha256=bundle_sha256,
        instruction=instruction,
        max_episode_steps=max_episode_steps,
    )
    return task, record
