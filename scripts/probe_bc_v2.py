#!/usr/bin/env python3
"""Three small probes behind the BC v2 diagnosis. Each stores its full configuration and result.

None selects anything on test seeds. Each is meant to finish in under 30 minutes on one host.

``legibility``
    Can one character of the read-only request card be read from a crop at the policy's input
    resolution (grayscale, 2x2 area-downsampled, 384x512), and at full resolution? A small CNN is
    trained to name the centre character of a fixed window around each card character. Crops come
    from reset frames of the standard split's training seeds (train) and validation seeds (held
    out). Character positions are computed from the renderer's own font metrics, so labels are
    exact. Separates "the text is unreadable after downsampling" from "the model does not use
    readable text".

``convergence``
    Continue ``standard-n256-seed0-steps6000`` from its stored weights (accepted only if their
    SHA-256 equals the digest in ``results.json``) on the same 256 training demonstrations, with
    the v2 loss and learning rate, evaluating validation step accuracy every 500 steps. It stops
    when validation accuracy has not improved for 3 evaluations, at 30,000 total steps (6,000 of
    them already done), or at the wall-clock cap. The optimiser state was not stored, so AdamW
    restarts from zero moments. The best validation checkpoint is then scored once on the test
    frames (teacher-forced and closed loop) for reporting only.

``copy``
    Can the key head reproduce one training episode's keystrokes from its frames? Two variants on
    the key frames of standard training seed 0: (a) the whole network from scratch with the key
    loss only; (b) the stored ``standard-n256-seed0`` weights with everything except the key
    branch frozen. Reports training-set key accuracy over steps.

    .venv/bin/python scripts/probe_bc_v2.py legibility
    .venv/bin/python scripts/probe_bc_v2.py convergence --weights-dir <dir>
    .venv/bin/python scripts/probe_bc_v2.py copy --weights-dir <dir>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pixelgym.actions import KEY_ALLOWLIST, ActionType
from scripts import run_bc_experiment_v2 as bc

SCHEMA_VERSION = "pixelgym-bc-probe-v1"
OUTPUT_DIR = Path("artifacts/bc-diagnosis-v1/probes")
RUN = "standard-n256-seed0-steps6000"


def _provenance(torch: Any, dev: Any) -> dict[str, Any]:
    return {
        "git_revision": bc._git("rev-parse", "HEAD").strip(),
        "worktree_clean": bc._git("status", "--porcelain") == "",
        "git_status_porcelain": bc._git("status", "--porcelain"),
        "recorded_at_unix": int(time.time()),
        "host": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "python": platform.python_version(),
        "torch": torch.__version__,
        "device": str(dev),
    }


def _stored_weights(torch: Any, dev: Any, weights_dir: Path) -> tuple[Any, dict[str, Any]]:
    results = json.loads((REPO_ROOT / bc.OUTPUT_DIR / "results.json").read_text("utf-8"))
    (run,) = [r for r in results["runs"] if r["name"] == RUN]
    path = weights_dir / f"{RUN}.pt"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != run["weights"]["sha256"]:
        raise SystemExit(f"{path}: SHA-256 {digest} differs from results.json")
    model = bc.build_model(torch).to(dev)
    model.load_state_dict(torch.load(path, map_location=dev))
    return model, run


# -- legibility --------------------------------------------------------------------------------

CROP_HALF_WIDTH = 8  # full-resolution pixels each side of the character centre
CROP_HALF_HEIGHT = 12


def card_character_crops(seeds: list[int]) -> dict[str, Any]:
    from PIL import Image

    from pixelgym.backends.fake import FakeBackend
    from pixelgym.tasks.vendor_form import render
    from pixelgym.tasks.vendor_form.ui import REQUEST_CARD_FIELDS

    full, low, labels, fields = [], [], [], []
    for seed in seeds:
        backend = FakeBackend()
        backend.reset(seed)
        frame = backend.screenshot()
        gray_full = (
            frame[..., 0].astype(np.uint32) * 299
            + frame[..., 1].astype(np.uint32) * 587
            + frame[..., 2].astype(np.uint32) * 114
        ) // 1000
        gray_low = bc.preprocess(frame)
        layout = backend.layout
        painter = render._Painter(Image.new("RGB", (8, 8)), layout)
        values = backend.current_fields()
        for field in REQUEST_CARD_FIELDS:
            value = values[field]
            if not isinstance(value, str):
                continue
            rect = layout.request_values[field]
            shown = render._fit(
                value, painter.body, max(1, rect.width - 2 * painter.inset), keep_tail=False
            )
            _, cy = rect.center
            for i, ch in enumerate(shown):
                if ch == " " or ch not in KEY_ALLOWLIST:
                    continue
                left = rect.x + painter.inset + painter.body.getlength(shown[:i])
                cx = round(left + painter.body.getlength(ch) / 2)
                y0, x0 = cy - CROP_HALF_HEIGHT, cx - CROP_HALF_WIDTH
                crop = gray_full[y0 : y0 + 2 * CROP_HALF_HEIGHT, x0 : x0 + 2 * CROP_HALF_WIDTH]
                ly0, lx0 = y0 // 2, x0 // 2
                small = gray_low[ly0 : ly0 + CROP_HALF_HEIGHT, lx0 : lx0 + CROP_HALF_WIDTH]
                if crop.shape != (2 * CROP_HALF_HEIGHT, 2 * CROP_HALF_WIDTH) or small.shape != (
                    CROP_HALF_HEIGHT,
                    CROP_HALF_WIDTH,
                ):
                    continue
                full.append(crop.astype(np.uint8))
                low.append(small.astype(np.uint8))
                labels.append(KEY_ALLOWLIST.index(ch))
                fields.append(field)
    return {
        "full": np.stack(full),
        "low": np.stack(low),
        "label": np.asarray(labels),
        "field": np.asarray(fields),
    }


def _classifier(torch: Any, height: int, width: int, classes: int) -> Any:
    nn = torch.nn
    return nn.Sequential(
        nn.Conv2d(1, 32, 3, padding=1),
        nn.ReLU(),
        nn.Conv2d(32, 64, 3, padding=1),
        nn.ReLU(),
        nn.Flatten(),
        nn.Linear(64 * height * width, 128),
        nn.ReLU(),
        nn.Linear(128, classes),
    )


def train_classifier(
    torch: Any, dev: Any, train_x: np.ndarray, train_y: np.ndarray, test_x: np.ndarray, *, steps
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    torch.manual_seed(0)
    rng = np.random.default_rng(0)
    h, w = train_x.shape[1:]
    model = _classifier(torch, h, w, len(KEY_ALLOWLIST)).to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)
    loss_fn = torch.nn.CrossEntropyLoss()
    xs = torch.from_numpy(train_x).float().div(255).unsqueeze(1).to(dev)
    ys = torch.from_numpy(train_y).to(dev)
    curve = []
    for step in range(1, steps + 1):
        idx = torch.from_numpy(rng.integers(0, len(train_x), size=256)).to(dev)
        loss = loss_fn(model(xs[idx]), ys[idx])
        opt.zero_grad(set_to_none=True)
        loss.backward()
        opt.step()
        if step % 500 == 0:
            curve.append({"step": step, "loss": float(loss.item())})
    model.eval()
    with torch.no_grad():
        tx = torch.from_numpy(test_x).float().div(255).unsqueeze(1).to(dev)
        pred = torch.cat([model(tx[i : i + 1024]).argmax(1) for i in range(0, len(tx), 1024)])
    return pred.cpu().numpy(), curve


def probe_legibility(torch: Any, dev: Any, args: argparse.Namespace) -> dict[str, Any]:
    seeds = bc.split_seeds("standard")
    train = card_character_crops(list(seeds["train"]))
    held = card_character_crops(list(seeds["validation"]))
    majority = int(np.bincount(train["label"]).argmax())
    out: dict[str, Any] = {
        "probe": "legibility",
        "config": {
            "train_seeds": "standard training seeds 0-255 (reset frames)",
            "held_out_seeds": "standard validation seeds 256-287 (reset frames)",
            "crop_full_resolution": [2 * CROP_HALF_HEIGHT, 2 * CROP_HALF_WIDTH],
            "crop_policy_resolution": [CROP_HALF_HEIGHT, CROP_HALF_WIDTH],
            "policy_preprocessing": "Rec. 601 luma, 2x2 area mean (bc.preprocess)",
            "label": "centre character of the crop, from the renderer's font metrics",
            "classifier": "conv3x3(32)-conv3x3(64)-fc128, AdamW lr 1e-3, batch 256",
            "steps": args.steps,
            "classes": len(KEY_ALLOWLIST),
        },
        "train_crops": len(train["label"]),
        "held_out_crops": len(held["label"]),
        "held_out_majority_class_accuracy": float((held["label"] == majority).mean()),
        "resolutions": {},
    }
    for name in ("low", "full"):
        pred, curve = train_classifier(
            torch, dev, train[name], train["label"], held[name], steps=args.steps
        )
        ok = pred == held["label"]
        out["resolutions"][name] = {
            "held_out_accuracy": float(ok.mean()),
            "by_field": {
                f: {
                    "crops": int((held["field"] == f).sum()),
                    "accuracy": float(ok[held["field"] == f].mean()),
                }
                for f in sorted(set(held["field"]))
            },
            "curve": curve,
        }
    return out


# -- convergence -------------------------------------------------------------------------------


def probe_convergence(torch: Any, dev: Any, args: argparse.Namespace) -> dict[str, Any]:
    model, run = _stored_weights(torch, dev, args.weights_dir)
    seeds = bc.split_seeds("standard")
    train = bc.generate_demos(seeds["train"], processes=args.processes)
    validation = bc.generate_demos(seeds["validation"], processes=args.processes)
    test = bc.generate_demos(seeds["test"], processes=args.processes)
    targets = bc.cell_targets(bc.layout())
    cfg = bc.CONFIG
    torch.manual_seed(args.seed)
    rng = np.random.default_rng(args.seed)
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=cfg.learning_rate, weight_decay=cfg.weight_decay
    )
    masks = bc.click_label_masks(train, targets)
    label = torch.from_numpy(
        (masks / np.maximum(masks.sum(1, keepdims=True), 1)).astype(np.float32)
    )
    label = label.to(dev)
    types_t = torch.from_numpy(train.action_type).to(dev)
    keys_t = torch.from_numpy(train.key).to(dev)
    ce = torch.nn.CrossEntropyLoss()

    def evaluate() -> dict[str, Any]:
        return bc.step_metrics(
            validation, *bc.predict(torch, model, validation.frames, dev), targets
        )

    start_metrics = evaluate()
    curve = [{"step": run["steps"], **start_metrics}]
    best = (start_metrics["step_accuracy"], run["steps"])
    best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
    since_best = 0
    started = time.perf_counter()
    step = run["steps"]
    stop = "max_steps"
    while step < args.max_total_steps:
        for _ in range(cfg.eval_every):
            step += 1
            index = rng.integers(0, len(train), size=cfg.batch_size)
            idx = torch.from_numpy(index).to(dev)
            type_logits, click_logits, key_logits = model(
                bc._batch(torch, train.frames[index], dev)
            )
            types = types_t[idx]
            loss = ce(type_logits, types)
            is_click = types == int(ActionType.CLICK)
            if is_click.any():
                logp = torch.log_softmax(click_logits[is_click], dim=1)
                loss = loss - (label[idx][is_click] * logp).sum(1).mean()
            is_key = types == int(ActionType.KEY)
            if is_key.any():
                loss = loss + ce(key_logits[is_key], keys_t[idx][is_key])
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            optimizer.step()
        metrics = evaluate()
        curve.append({"step": step, "loss": float(loss.item()), **metrics})
        print(f"step {step} validation {metrics['step_accuracy']:.4f}", flush=True)
        if metrics["step_accuracy"] > best[0]:
            best = (metrics["step_accuracy"], step)
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
            since_best = 0
        else:
            since_best += 1
        if since_best >= args.patience:
            stop = f"no validation improvement in {args.patience} evaluations"
            break
        if time.perf_counter() - started > args.max_minutes * 60:
            stop = f"wall-clock cap {args.max_minutes} min"
            break
    model.load_state_dict(best_state)
    tf = bc.step_metrics(test, *bc.predict(torch, model, test.frames, dev), targets)
    closed = bc.closed_loop(bc.model_policy(torch, model, dev), list(seeds["test"]))
    return {
        "probe": "convergence",
        "config": {
            "run": RUN,
            "start_weights_sha256": run["weights"]["sha256"],
            "start_step": run["steps"],
            "max_total_steps": args.max_total_steps,
            "eval_every": cfg.eval_every,
            "patience_evaluations": args.patience,
            "max_minutes": args.max_minutes,
            "learning_rate": cfg.learning_rate,
            "weight_decay": cfg.weight_decay,
            "batch_size": cfg.batch_size,
            "rng_seed": args.seed,
            "optimizer_state": "AdamW restarted with zero moments (v2 stored no optimizer state)",
            "selection": "best validation step accuracy; test scored once afterwards",
        },
        "stop_reason": stop,
        "train_seconds": round(time.perf_counter() - started, 1),
        "validation_at_start": start_metrics,
        "best_validation_step": best[1],
        "best_validation_step_accuracy": best[0],
        "validation_curve": curve,
        "test_teacher_forced_at_best": tf,
        "test_closed_loop_at_best": {k: closed[k] for k in closed if k != "per_episode"},
    }


# -- copy --------------------------------------------------------------------------------------


def _overfit(torch: Any, dev: Any, model: Any, frames: Any, keys: Any, params: Any, steps: int):
    opt = torch.optim.AdamW(params, lr=3e-4, weight_decay=0.0)
    ce = torch.nn.CrossEntropyLoss()
    curve = []
    for step in range(1, steps + 1):
        _t, _c, key_logits = model(frames)
        loss = ce(key_logits, keys)
        opt.zero_grad(set_to_none=True)
        loss.backward()
        opt.step()
        if step % 100 == 0 or step == steps:
            with torch.no_grad():
                acc = float((model(frames)[2].argmax(1) == keys).float().mean())
            curve.append({"step": step, "loss": float(loss.item()), "train_key_accuracy": acc})
    return curve


def probe_copy(torch: Any, dev: Any, args: argparse.Namespace) -> dict[str, Any]:
    demos = bc.generate_demos([0], processes=1)
    is_key = demos.action_type == ActionType.KEY
    frames = bc._batch(torch, demos.frames[is_key], dev)
    keys = torch.from_numpy(demos.key[is_key]).to(dev)
    torch.manual_seed(0)
    scratch = bc.build_model(torch).to(dev)
    started = time.perf_counter()
    curve_scratch = _overfit(torch, dev, scratch, frames, keys, scratch.parameters(), args.steps)
    t_scratch = time.perf_counter() - started
    stored, run = _stored_weights(torch, dev, args.weights_dir)
    branch = ("key_context", "key_attention", "key_values", "key_head")
    for name, param in stored.named_parameters():
        param.requires_grad = name.split(".")[0] in branch
    trainable = [p for p in stored.parameters() if p.requires_grad]
    started = time.perf_counter()
    curve_head = _overfit(torch, dev, stored, frames, keys, trainable, args.steps)
    return {
        "probe": "copy",
        "config": {
            "episode": "standard training seed 0",
            "key_frames": int(is_key.sum()),
            "distinct_keys": len(set(demos.key[is_key].tolist())),
            "steps": args.steps,
            "optimizer": "AdamW lr 3e-4, no weight decay, full batch",
            "variants": {
                "scratch_full_network": "build_model from torch seed 0, key loss only",
                "stored_weights_key_branch_only": (
                    f"{RUN} weights; only {', '.join(branch)} trained"
                ),
            },
            "stored_weights_sha256": run["weights"]["sha256"],
        },
        "scratch_full_network": {"curve": curve_scratch, "seconds": round(t_scratch, 1)},
        "stored_weights_key_branch_only": {
            "curve": curve_head,
            "seconds": round(time.perf_counter() - started, 1),
            "trainable_parameters": int(sum(p.numel() for p in trainable)),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="probe", required=True)
    leg = sub.add_parser("legibility")
    leg.add_argument("--steps", type=int, default=3000)
    conv = sub.add_parser("convergence")
    conv.add_argument("--weights-dir", type=Path, required=True)
    conv.add_argument("--max-total-steps", type=int, default=30_000)
    conv.add_argument("--patience", type=int, default=3)
    conv.add_argument("--max-minutes", type=float, default=25.0)
    conv.add_argument("--seed", type=int, default=0)
    conv.add_argument("--processes", type=int, default=6)
    cp = sub.add_parser("copy")
    cp.add_argument("--weights-dir", type=Path, required=True)
    cp.add_argument("--steps", type=int, default=1500)
    args = parser.parse_args(argv)
    torch = bc._torch()
    dev = bc.device(torch)
    provenance = _provenance(torch, dev)
    started = time.perf_counter()
    probe = {"legibility": probe_legibility, "convergence": probe_convergence, "copy": probe_copy}
    result = probe[args.probe](torch, dev, args)
    result["schema_version"] = SCHEMA_VERSION
    result["provenance"] = provenance
    result["command"] = " ".join(["scripts/probe_bc_v2.py", *(argv or sys.argv[1:])])
    result["total_seconds"] = round(time.perf_counter() - started, 1)
    out = REPO_ROOT / OUTPUT_DIR / f"{args.probe}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n", "utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
