"""PR 1 check on the pinned OSWorld guest: golden trajectory plus review cases vs. fake.

Usage: ``driver_subset.py OUT [--no-golden] [--cases NAME ...]``. With no options it runs
everything, exactly as ``driver.py`` (the 2026-11-11 driver) does. ``--cases`` runs only the named
review cases and ``--no-golden`` skips the golden replay; both exist to retry steps whose VM did
not boot. Writes raw results under OUT. Always closes the provider.
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

from pixelgym.actions import KEY_ALLOWLIST, ActionType
from pixelgym.backends.fake import FakeBackend
from pixelgym.backends.osworld import OSWorldBackend, OSWorldBackendConfig
from pixelgym.env import PixelGuiEnv
from scripts.osworld_golden_trajectory import DEFAULT_FIXTURE, record_real

OUT = Path(sys.argv[1])
_ARGS = sys.argv[2:]
RUN_GOLDEN = "--no-golden" not in _ARGS
SELECTED = _ARGS[_ARGS.index("--cases") + 1 :] if "--cases" in _ARGS else None
SELECTED = [name for name in SELECTED if not name.startswith("--")] if SELECTED else SELECTED
GUEST = Path(".cache/osworld/osworld-v2-ubuntu-x86.qcow2")
STARTED = time.monotonic()
BUDGET = 75 * 60
KEY = {k: i for i, k in enumerate(KEY_ALLOWLIST)}

SUBMIT = ("click", (578, 486))
CASES = {
    "caret ArrowLeft": [("click", (768, 94)), *[("key", k) for k in "ab"], ("key", "ArrowLeft"),
                        ("key", "c"), SUBMIT],
    "caret ArrowUp": [("click", (768, 94)), *[("key", k) for k in "ab"], ("key", "ArrowUp"),
                      ("key", "c"), SUBMIT],
    "caret click near left edge": [("click", (768, 94)), *[("key", k) for k in "abc"],
                                   ("click", (768, 280)), ("click", (540, 94)), ("key", "X"),
                                   SUBMIT],
    "label click focuses": [("click", (768, 280)), ("click", (768, 67)), ("key", "Z"), SUBMIT],
    "Tab into radios then ArrowDown": [("key", "Tab")] * 6 + [("key", "ArrowDown"), SUBMIT],
    "Tab into radios then Space": [("key", "Tab")] * 6 + [("key", " "), SUBMIT],
    "select ArrowDown twice": [("click", (768, 343)), ("key", "ArrowDown"), ("key", "ArrowDown"),
                               SUBMIT],
}


def to_action(item):
    kind, arg = item
    if kind == "click":
        return {"action_type": int(ActionType.CLICK), "x": arg[0], "y": arg[1], "key": 0}
    return {"action_type": int(ActionType.KEY), "x": 0, "y": 0, "key": KEY[arg]}


def main() -> int:
    unknown = sorted(set(SELECTED or ()) - set(CASES))
    if unknown:
        raise SystemExit(f"unknown case names: {unknown}")
    OUT.mkdir(parents=True, exist_ok=True)
    results: dict = {
        "started_unix": time.time(),
        "selection": {"golden": RUN_GOLDEN, "cases": SELECTED or "all"},
    }
    t0 = time.monotonic()
    if not RUN_GOLDEN:
        results["golden"] = {"skipped": "not selected"}
    else:
        try:
            golden = record_real(DEFAULT_FIXTURE, GUEST, OUT / "real-golden")
            results["golden"] = {
                "summary": golden["summary"],
                "seconds": time.monotonic() - t0,
                "backend_metadata": golden["backend_metadata"],
                "rewards": [s["reward"] for s in golden["trace"]],
            }
        except Exception:
            results["golden"] = {"error": traceback.format_exc(), "seconds": time.monotonic() - t0}
    (OUT / "results.json").write_text(json.dumps(results, indent=2, default=str))

    backend = OSWorldBackend(OSWorldBackendConfig(guest_image_path=GUEST))
    env = PixelGuiEnv(backend)
    results["cases"] = {}
    try:
        for name, items in CASES.items():
            if SELECTED is not None and name not in SELECTED:
                continue
            if time.monotonic() - STARTED > BUDGET:
                results["cases"][name] = {"skipped": "time budget"}
                continue
            c0 = time.monotonic()
            try:
                env.reset(seed=7)
                fake = FakeBackend()
                fake_env = PixelGuiEnv(fake)
                fake_env.reset(seed=7)
                steps = []
                for item in items:
                    s0 = time.monotonic()
                    _o, r, term, trunc, _i = env.step(to_action(item))
                    fake_env.step(to_action(item))
                    steps.append({
                        "action": list(item), "reward": r, "terminated": term,
                        "seconds": round(time.monotonic() - s0, 2),
                        "ack_reads": backend.last_input_ack_reads,
                        "stabilization_seconds": backend.last_stabilization_seconds,
                    })
                    if term:
                        break
                real_subs = [dict(s.values) for s in backend.read_submissions()]
                fake_subs = [dict(s.values) for s in fake.read_submissions()]
                results["cases"][name] = {
                    "match": real_subs == fake_subs,
                    "guest_submissions": real_subs,
                    "fake_submissions": fake_subs,
                    "steps": steps,
                    "seconds": round(time.monotonic() - c0, 1),
                }
            except Exception:
                results["cases"][name] = {"error": traceback.format_exc(),
                                          "seconds": round(time.monotonic() - c0, 1)}
            (OUT / "results.json").write_text(json.dumps(results, indent=2, default=str))
    finally:
        env.close()
        results["provider_closed"] = True
        results["total_seconds"] = time.monotonic() - STARTED
        (OUT / "results.json").write_text(json.dumps(results, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
