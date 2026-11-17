#!/usr/bin/env python3
"""v2 of the v5 stateless chance-level supplement: v1 unchanged plus a fixed label-order baseline.

v1 (``artifacts/grounding-v5-stateless-chance-ceiling-v1.*``) is kept as published. v2 repeats
every v1 value, computed by the v1 code, and adds one reference level that v1 did not store: the
best pair of fixed per-stage rules a stateless policy could apply to what the decision screen shows.

On each consumer stage the screen shows three "Match row <ref>" controls (for example ``G73``) and
no other evidence. A fixed rule picks one control from what is visible, without memory:

- by on-screen position: first, second, or third control; or
- by reference label order: the label that sorts first, second, or last as a string.

That is six rules per stage and 36 rule pairs. The supplement reports, for an otherwise-perfect
policy, the successes of every pair and the maximum. The maximum is selected on the same 192
frozen episodes it is evaluated on, so it is an optimistic, in-sample reference level, not a rule
any campaign policy used and not a proven upper bound. Standard library only; no randomness.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path
from typing import Any

from scripts import generate_v5_stateless_chance_ceiling as v1

SCHEMA_VERSION = "pixelgym-v5-stateless-chance-ceiling-v2"
JSON_OUTPUT = "artifacts/grounding-v5-stateless-chance-ceiling-v2.json"
MD_OUTPUT = "artifacts/grounding-v5-stateless-chance-ceiling-v2.md"
POSITION_RULES = ("position_0", "position_1", "position_2")
LABEL_RULES = ("label_first", "label_middle", "label_last")
RULES = POSITION_RULES + LABEL_RULES
WRITTEN_ON = "2026-11-15"


def consumer_labels(manifest: dict[str, Any]) -> dict[int, list[tuple[list[str], int]]]:
    """Per seed, per consumer stage: on-screen labels in order and the target's position."""

    out: dict[int, list[tuple[list[str], int]]] = {}
    for record in manifest["records"]:
        seed = record["seed_record"]["seed"]
        stages = []
        for dependency in v1.CONSUMER_DEPENDENCIES:
            (stage,) = [
                s
                for s in record["canonical_task"]["stages"]
                if s.get("critical")
                and s["kind"] == "click"
                and s.get("dependency_id") == dependency
            ]
            ids = [c["control_id"] for c in stage["controls"]]
            labels = [c["label"] for c in stage["controls"]]
            if len(set(labels)) != len(labels):
                raise v1.ChanceCeilingError(f"seed {seed}: {dependency} labels are not distinct")
            stages.append((labels, ids.index(stage["target_control_id"])))
        out[seed] = stages
    return out


def pick(rule: str, labels: list[str]) -> int:
    """On-screen position chosen by ``rule`` from the visible labels."""

    if rule in POSITION_RULES:
        return POSITION_RULES.index(rule)
    order = sorted(range(len(labels)), key=lambda i: labels[i])
    return order[LABEL_RULES.index(rule)]


def fixed_rule_pairs(manifest: dict[str, Any]) -> dict[str, Any]:
    stages_by_seed = consumer_labels(manifest)
    pairs = {}
    for first, second in itertools.product(RULES, repeat=2):
        pairs[f"{first}+{second}"] = sum(
            1
            for (s5_labels, s5_target), (s7_labels, s7_target) in stages_by_seed.values()
            if pick(first, s5_labels) == s5_target and pick(second, s7_labels) == s7_target
        )
    best = max(pairs.values())
    position_only = {
        k: v for k, v in pairs.items() if all(p in POSITION_RULES for p in k.split("+"))
    }
    return {
        "best_pair_successes_if_otherwise_perfect": best,
        "best_pairs": sorted(k for k, v in pairs.items() if v == best),
        "best_position_only_successes": max(position_only.values()),
        "best_pairs_using_a_label_rule": sorted(
            k for k, v in pairs.items() if v == best and k not in position_only
        ),
        "pair_count": len(pairs),
        "rules_per_stage": list(RULES),
        "selection": "maximum over all rule pairs, selected in-sample on the 192 frozen episodes",
        "successes_by_pair": pairs,
    }


def analyze(manifest: dict[str, Any], reports: dict[str, dict[str, Any]]) -> dict[str, Any]:
    base = v1.analyze(manifest, reports)
    rules = fixed_rule_pairs(manifest)
    if (
        rules["best_position_only_successes"]
        != base["best_fixed_position"]["successes_if_otherwise_perfect"]
    ):
        raise v1.ChanceCeilingError("position-only rules disagree with v1's best fixed position")
    return {
        "fixed_rule_pairs": rules,
        "post_hoc": True,
        "schema_version": SCHEMA_VERSION,
        "supersedes_nothing": (
            "v1 is unchanged; every v1 field below is recomputed by the v1 code and must equal it"
        ),
        "v1": base,
        "written_on": WRITTEN_ON,
    }


def render_markdown(result: dict[str, Any]) -> str:
    base, rules = result["v1"], result["fixed_rule_pairs"]
    n = base["episodes_per_arm"]
    v1_text = v1.render_markdown(base)
    body = v1_text[v1_text.index("## Why there is a chance level") :]
    scope = body.index("## Scope")
    label_pairs = ", ".join(f"`{p}`" for p in rules["best_pairs_using_a_label_rule"])
    added = [
        "## Fixed label-order rules (added in v2)",
        "",
        (
            'Each consumer stage shows three "Match row <ref>" controls. Six fixed rules pick one '
            "from what is visible: by on-screen position (first, second, third) or by the "
            "reference label's string order (first, middle, last). Over all "
            f"{rules['pair_count']} rule pairs for the two stages, an otherwise-perfect policy "
            f"succeeds at most {rules['best_pair_successes_if_otherwise_perfect']} of {n} times; "
            f"position-only pairs reach {rules['best_position_only_successes']} of {n}, as in v1."
        ),
        "",
        (
            f"Pairs reaching {rules['best_pair_successes_if_otherwise_perfect']} "
            f"that use a label rule: {label_pairs}."
        ),
        "",
        (
            "This maximum is chosen on the same 192 frozen episodes it is scored on, so it is an "
            "optimistic in-sample reference level. No campaign policy used a fixed rule, and it "
            "is not a proven upper bound for every stateless policy. Only these six rules per "
            "stage are evaluated; other fixed rules over the visible labels, such as a preference "
            "order over the labels, are not."
        ),
        "",
    ]
    lines = [
        "# v5 stateless arm against its chance level (v2)",
        "",
        (
            f"**Post hoc.** Written on {result['written_on']}, after all three campaigns' "
            "outcomes were known; the label-order rules were chosen then."
        ),
        "",
        (
            "Generated by `scripts/generate_v5_stateless_chance_ceiling_v2.py` from "
            + ", ".join(f"`{path}`" for path in base["inputs"])
            + f". Structured source: `{JSON_OUTPUT}`. v2 repeats "
            "[v1](grounding-v5-stateless-chance-ceiling-v1.md) unchanged and adds the fixed "
            "label-order section. It supplements, and does not replace, the frozen campaign "
            "reports."
        ),
        "",
    ]
    return "\n".join(lines) + "\n" + body[:scope] + "\n".join(added) + "\n" + body[scope:]


def serialize(result: dict[str, Any]) -> str:
    return json.dumps(result, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path, default=None, help="default: <root>/artifacts")
    args = parser.parse_args(argv)
    result = analyze(*v1.load_inputs(args.repository_root))
    out = args.output_dir or args.repository_root / "artifacts"
    out.mkdir(parents=True, exist_ok=True)
    (out / Path(JSON_OUTPUT).name).write_text(serialize(result), encoding="utf-8")
    (out / Path(MD_OUTPUT).name).write_text(render_markdown(result), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
