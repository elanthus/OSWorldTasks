"""The power report is rendered from its structured inputs."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from scripts import prepare_grounding_v5_power_report as power

ROOT = Path(__file__).resolve().parents[2]


def test_power_report_follows_changed_structured_inputs():
    data = json.loads(power.SOURCE.read_text())
    changed = deepcopy(data)
    changed.update(alpha=0.01, minimum_relevant_absolute_difference=0.15, power_target=0.70)
    changed["options"] = [deepcopy(data["options"][0])]
    row = changed["options"][0]
    row.update(independent_pairs=99, episodes_per_arm=103, additional_reliability_episodes=7)
    row["power"].update(
        observed_discordance=0.76, upper_sensitivity=0.72, all_discordant_stress=0.61
    )
    report = power.render_report(changed)
    for expected in (
        "15-point",
        "alpha 0.01",
        "99-pair",
        "70% power target",
        "76.0%",
        "72.0%",
        "61.0%",
        "| 7 |",
    ):
        assert expected in report
    for stale in ("168-pair", "85.9%", "80.3%", "48 additional", "95%"):
        assert stale not in report
    row["power"]["upper_sensitivity"] = 0.69
    assert "No listed option meets" in power.render_report(changed)
