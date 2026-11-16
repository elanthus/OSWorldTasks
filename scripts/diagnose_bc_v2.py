#!/usr/bin/env python3
"""Diagnose the BC v2 null result from stored predictions and stored weights; no training.

Inputs (all stored): ``artifacts/bc-experiment-v2/results.json``, ``test-frames-<split>.json``,
``predictions/*.json``, and each run's weights, located under ``--weights-dir`` and accepted only
if their SHA-256 equals the digest ``results.json`` recorded. Outputs
``artifacts/bc-diagnosis-v1/diagnosis.json``; the report is generated from it by
``scripts/generate_bc_diagnosis_report.py``.

What it computes:

1. Keys. For every test KEY frame, the field being typed (the control the oracle last clicked),
   the position in the string (keys typed since that click), the oracle key, and the card-blind
   baseline's guess (the most frequent training continuation of what is already typed, the v2
   definition). Per run, key accuracy by field, by position bucket, and by whether the correct
   key equals the baseline's guess, with the four-way agreement table (both right, only the
   policy, only the baseline, neither).
2. Clicks. Per run, click-target errors by the oracle's target widget and by click number within
   the episode, with the widget the policy chose instead.
3. First wrong action. Each run's closed loop is replayed with its stored weights on the test
   seeds and compared step by step with the scripted oracle's plan
   (``build_dynamic_solve_actions`` right after reset); the first step where the policy's action
   fails the v2 step rule is recorded with its kind, widget, field and position. Before
   replaying, the loaded weights must reproduce the stored teacher-forced predictions exactly, or
   the run is refused.

Regenerated test demonstrations must equal ``test-frames-<split>.json`` exactly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pixelgym.actions import KEY_ALLOWLIST, ActionType
from scripts import run_bc_experiment_v2 as bc

SCHEMA_VERSION = "pixelgym-bc-diagnosis-v1"
SOURCE = Path("artifacts/bc-experiment-v2")
OUTPUT = Path("artifacts/bc-diagnosis-v1/diagnosis.json")
POSITION_BUCKETS = ((0, 0), (1, 1), (2, 3), (4, 7), (8, 15), (16, 10_000))


class DiagnosisError(RuntimeError):
    pass


def position_bucket(position: int) -> str:
    for low, high in POSITION_BUCKETS:
        if low <= position <= high:
            return f"{low}" if low == high else (f"{low}+" if high >= 10_000 else f"{low}-{high}")
    raise ValueError(position)


def widget_name(target: Any) -> str:
    if target is None:
        return "empty space"
    widget, option = target
    return widget.value if option is None else f"{widget.value}[{option}]"


def key_name(index: int) -> str:
    return KEY_ALLOWLIST[index] if 0 <= index < len(KEY_ALLOWLIST) else f"#{index}"


def agreement(policy_ok: np.ndarray, baseline_ok: np.ndarray) -> dict[str, int]:
    return {
        "frames": int(policy_ok.size),
        "policy_correct": int(policy_ok.sum()),
        "baseline_correct": int(baseline_ok.sum()),
        "both_correct": int((policy_ok & baseline_ok).sum()),
        "policy_only": int((policy_ok & ~baseline_ok).sum()),
        "baseline_only": int((~policy_ok & baseline_ok).sum()),
        "neither": int((~policy_ok & ~baseline_ok).sum()),
    }


def key_context(train: bc.Demos, test: bc.Demos, targets: list[Any]) -> dict[str, np.ndarray]:
    form_layout = bc.layout()
    contexts = bc._typing_contexts(test, form_layout)
    _types, _cells, baseline = bc.card_blind_predictions(train, test, targets)
    is_key = test.action_type == ActionType.KEY
    field = np.array([widget_name(c[0]) if c else "" for c in contexts], dtype=object)
    position = np.array([len(c[1]) if c else -1 for c in contexts])
    return {"is_key": is_key, "field": field, "position": position, "baseline": baseline}


def key_breakdown(test: bc.Demos, ctx: dict[str, np.ndarray], predicted_key: np.ndarray) -> dict:
    is_key = ctx["is_key"]
    policy_ok = (predicted_key == test.key)[is_key]
    baseline_ok = (ctx["baseline"] == test.key)[is_key]
    field = ctx["field"][is_key]
    buckets = np.array([position_bucket(int(p)) for p in ctx["position"][is_key]], dtype=object)
    out: dict[str, Any] = {"overall": agreement(policy_ok, baseline_ok)}
    out["by_field"] = {
        f: agreement(policy_ok[field == f], baseline_ok[field == f]) for f in sorted(set(field))
    }
    out["by_position"] = {
        b: agreement(policy_ok[buckets == b], baseline_ok[buckets == b])
        for b in [position_bucket(low) for low, _ in POSITION_BUCKETS]
        if (buckets == b).any()
    }
    out["by_baseline_guess"] = {
        "correct_key_is_baseline_guess": agreement(
            policy_ok[baseline_ok], baseline_ok[baseline_ok]
        ),
        "correct_key_is_not_baseline_guess": agreement(
            policy_ok[~baseline_ok], baseline_ok[~baseline_ok]
        ),
    }
    return out


def click_breakdown(test: bc.Demos, predicted_cell: np.ndarray, targets: list[Any]) -> dict:
    form_layout = bc.layout()
    by_widget: dict[str, Counter[str]] = defaultdict(Counter)
    by_number: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for episode in bc._episodes(test):
        number = 0
        for index in episode:
            if test.action_type[index] != ActionType.CLICK:
                continue
            number += 1
            oracle = form_layout.hit_test(*(int(v) for v in test.click_xy[index]))
            chosen = targets[int(predicted_cell[index])]
            ok = chosen == oracle
            by_widget[widget_name(oracle)]["correct" if ok else f"-> {widget_name(chosen)}"] += 1
            by_number[number][0] += 1
            by_number[number][1] += int(ok)
    return {
        "by_target_widget": {
            w: {
                "clicks": sum(c.values()),
                "correct": c.get("correct", 0),
                "wrong_choices": dict(sorted((k[3:], v) for k, v in c.items() if k != "correct")),
            }
            for w, c in sorted(by_widget.items())
        },
        "by_click_number": {
            str(n): {"clicks": v[0], "correct": v[1]} for n, v in sorted(by_number.items())
        },
    }


def first_wrong_actions(
    torch: Any, model: Any, dev: Any, test: bc.Demos, targets: list[Any]
) -> list[dict[str, Any]]:
    """Replay each test episode closed-loop; stop at the first action that fails the v2 step rule.

    The oracle's plan is fixed by the task (``build_dynamic_solve_actions`` right after reset), so
    step ``t`` is compared with ``plan[t]``. A click on another cell of the right control passes
    the rule but can leave a different state (for example the caret), so the closed-loop frame is
    also compared with the oracle's stored frame at each step before the first wrong action.
    """

    from pixelgym.backends.fake import FakeBackend
    from pixelgym.env import PixelGuiEnv
    from tests.support.golden_solver import build_dynamic_solve_actions

    policy = bc.model_policy(torch, model, dev)
    form_layout = bc.layout()
    records = []
    for episode in bc._episodes(test):
        seed = int(test.seed[episode[0]])
        backend = FakeBackend()
        env = PixelGuiEnv(backend, max_episode_steps=bc.CONFIG.max_episode_steps)
        observation, _ = env.reset(seed=seed)
        plan = [{k: int(v) for k, v in a.items()} for a in build_dynamic_solve_actions(backend)]
        if len(plan) != len(episode):
            raise DiagnosisError(f"seed {seed}: oracle plan length differs from stored demo")
        record: dict[str, Any] = {"seed": seed, "first_wrong": None, "frames_differing_before": 0}
        field: Any = None
        typed = 0
        for step, oracle in enumerate(plan):
            frame = bc.preprocess(observation)
            if not np.array_equal(frame, test.frames[episode[step]]):
                record["frames_differing_before"] += 1
            p_type, p_cell, p_key = policy(frame)
            is_click = oracle["action_type"] == ActionType.CLICK
            is_key = oracle["action_type"] == ActionType.KEY
            want = form_layout.hit_test(oracle["x"], oracle["y"]) if is_click else None
            kind = None
            if p_type != oracle["action_type"]:
                kind = "action_type"
            elif is_click and targets[p_cell] != want:
                kind = "click_target"
            elif is_key and p_key != oracle["key"]:
                kind = "key"
            if kind is not None:
                record["first_wrong"] = {
                    "step": step,
                    "kind": kind,
                    "oracle_action_type": ActionType(oracle["action_type"]).name,
                    "policy_action_type": ActionType(p_type).name,
                    "oracle_click_widget": widget_name(want) if is_click else None,
                    "policy_click_widget": (
                        widget_name(targets[p_cell]) if p_type == ActionType.CLICK else None
                    ),
                    "field": widget_name(field) if is_key and field else None,
                    "position": typed if is_key else None,
                    "oracle_key": key_name(oracle["key"]) if is_key else None,
                    "policy_key": key_name(p_key) if p_type == ActionType.KEY else None,
                }
                break
            if is_click:
                field, typed = want, 0
            elif is_key:
                typed += 1
            observation, _reward, terminated, truncated, _ = env.step(
                bc.decode_action(p_type, p_cell, p_key)
            )
            if terminated or truncated:
                record["ended_before_plan_finished_at_step"] = step
                break
        records.append(record)
    return records


def summarize_first_wrong(records: list[dict[str, Any]]) -> dict[str, Any]:
    wrong = [r["first_wrong"] for r in records if r["first_wrong"]]
    steps = sorted(w["step"] for w in wrong)
    return {
        "episodes": len(records),
        "with_wrong_action": len(wrong),
        "by_kind": dict(Counter(w["kind"] for w in wrong)),
        "by_field_for_keys": dict(Counter(w["field"] for w in wrong if w["kind"] == "key")),
        "by_position_for_keys": dict(
            Counter(position_bucket(w["position"]) for w in wrong if w["kind"] == "key")
        ),
        "by_oracle_click_widget": dict(
            Counter(w["oracle_click_widget"] for w in wrong if w["kind"] == "click_target")
        ),
        "step_min": steps[0] if steps else None,
        "step_median": steps[len(steps) // 2] if steps else None,
        "step_max": steps[-1] if steps else None,
        "episodes_with_frame_drift_before_first_wrong": sum(
            1 for r in records if r["frames_differing_before"]
        ),
    }


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--weights-dir", type=Path, required=True)
    parser.add_argument("--processes", type=int, default=6)
    parser.add_argument("--output", type=Path, default=REPO_ROOT / OUTPUT)
    args = parser.parse_args(argv)
    started = time.time()
    results = json.loads((REPO_ROOT / SOURCE / "results.json").read_text(encoding="utf-8"))
    torch = bc._torch()
    dev = bc.device(torch)
    targets = bc.cell_targets(bc.layout())
    out: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "source_results_revision": results["git_revision"],
        "git_revision": bc._git("rev-parse", "HEAD").strip(),
        "worktree_clean": bc._git("status", "--porcelain") == "",
        "command": " ".join(["scripts/diagnose_bc_v2.py", *(argv or sys.argv[1:])]),
        "inputs": [str(SOURCE / "results.json")],
        "host": {
            "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
            "python": platform.python_version(),
            "torch": torch.__version__,
            "device": str(dev),
        },
        "splits": {},
        "runs": {},
    }
    for split in bc.SPLITS:
        seeds = bc.split_seeds(split)
        stored = json.loads((REPO_ROOT / SOURCE / f"test-frames-{split}.json").read_text("utf-8"))
        test = bc.generate_demos(seeds["test"], processes=args.processes)
        for name in ("seed", "action_type", "cell", "key", "click_xy"):
            if not np.array_equal(getattr(test, name), np.asarray(stored[name])):
                raise DiagnosisError(f"{split}: regenerated test {name} differs from stored")
        train = bc.generate_demos(seeds["train"], processes=args.processes)
        ctx = key_context(train, test, targets)
        out["inputs"].append(str(SOURCE / f"test-frames-{split}.json"))
        out["splits"][split] = {
            "test_frames": len(test),
            "key_frames": int(ctx["is_key"].sum()),
            "baseline_key_accuracy": float((ctx["baseline"] == test.key)[ctx["is_key"]].mean()),
        }
        for run in (r for r in results["runs"] if r["split"] == split):
            name = run["name"]
            pred_path = SOURCE / "predictions" / f"{name}.json"
            pred = json.loads((REPO_ROOT / pred_path).read_text(encoding="utf-8"))
            out["inputs"].append(str(pred_path))
            p_type, p_cell, p_key = (np.asarray(pred[k]) for k in ("action_type", "cell", "key"))
            weights = args.weights_dir / f"{name}.pt"
            if sha256_file(weights) != run["weights"]["sha256"]:
                raise DiagnosisError(f"{name}: weights digest differs from results.json")
            model = bc.build_model(torch).to(dev)
            model.load_state_dict(torch.load(weights, map_location=dev))
            r_type, r_cell, r_key = bc.predict(torch, model, test.frames, dev)
            reproduced = {
                "action_type": int((r_type != p_type).sum()),
                "cell": int((r_cell != p_cell).sum()),
                "key": int((r_key != p_key).sum()),
            }
            if any(reproduced.values()):
                raise DiagnosisError(f"{name}: weights do not reproduce predictions {reproduced}")
            records = first_wrong_actions(torch, model, dev, test, targets)
            out["runs"][name] = {
                "split": split,
                "demo_episodes": run["demo_episodes"],
                "training_seed": run["training_seed"],
                "weights_sha256": run["weights"]["sha256"],
                "predictions_reproduced_mismatches": reproduced,
                "keys": key_breakdown(test, ctx, p_key),
                "clicks": click_breakdown(test, p_cell, targets),
                "first_wrong_action": summarize_first_wrong(records),
                "first_wrong_action_per_episode": records,
            }
            print(name, "done", round(time.time() - started), flush=True)
    out["seconds"] = round(time.time() - started, 1)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
