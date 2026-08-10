"""Render Markdown from stored Day 2 JSON evidence without rerunning checks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

DEFAULT_INPUT = Path("artifacts/validation-report.json")
DEFAULT_OUTPUT = Path("artifacts/validation-report.md")


def _value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def render(report: dict[str, Any]) -> str:
    automated = report["automated_validation"]
    evidence = report["evidence"]
    lines = [
        "# PixelGym Day 2 validation report",
        "",
        f"Generated from stored evidence: `{report['generated_at']}`.",
        "",
        (f"**Automated validation status: {automated['status']}.** {report['gate_disclaimer']}"),
        "",
    ]
    human_gate = report.get("human_gate")
    if human_gate is not None:
        lines.extend(
            [
                (
                    f"**Human D2.11 verdict: {human_gate['verdict']}.** "
                    f"Day 3 authorized: {_value(human_gate['day3_authorized'])}."
                ),
                "",
                f"Declared by {human_gate['declared_by']} at `{human_gate['declared_at']}`.",
                "",
            ]
        )
    lines.extend(["## Release and provider metadata", ""])
    preparation = report["runtime"].get("preparation")
    if preparation is None:
        lines.append("Docker preparation evidence is not available yet.")
    else:
        runtime = preparation["runtime"]
        guest = preparation["guest_artifact"]
        lines.extend(
            [
                f"- OSWorld release: `{preparation['release']}` / `{preparation['upstream_tag']}`",
                f"- Upstream commit: `{preparation['upstream_commit']}`",
                f"- Provider: `{preparation['provider']}`",
                f"- Runtime image: `{runtime['reference']}`",
                (
                    f"- Runtime image ID / architecture: `{runtime['local_image_id']}` / "
                    f"`{runtime['image_architecture']}`"
                ),
                (
                    f"- Docker engine: `{runtime['engine_os']}/{runtime['engine_arch']}` "
                    f"version `{runtime['engine_version']}`"
                ),
                (
                    f"- Docker allocation: `{runtime['engine_cpus']}` CPUs / "
                    f"`{runtime['engine_memory_bytes']}` bytes RAM"
                ),
                f"- Guest artifact: `{guest['repository']}@{guest['tag']}/{Path(guest['archive']).name}`",
                f"- Guest archive SHA-256: `{guest['archive_sha256']}`",
                f"- Preparation timestamp: `{preparation['prepared_at']}`",
            ]
        )
        if runtime.get("base_reference"):
            lines.insert(
                lines.index(f"- Runtime image: `{runtime['reference']}`") + 1,
                f"- Runtime base: `{runtime['base_reference']}`",
            )
    integration_record = evidence.get("real_golden_episode") or evidence.get("real_reset")
    if integration_record is not None:
        screen_size = integration_record["backend_metadata"]["screen_size"]
        lines.extend(
            [
                f"- Task seed: `{integration_record['seed']}`",
                f"- Screen size: `{screen_size[0]}x{screen_size[1]}`",
            ]
        )
    lines.extend([f"- Host Python: `{report['runtime']['python_version']}`", ""])
    stop_loss = report["runtime"].get("provider_stop_loss")
    if stop_loss is not None:
        blocker = stop_loss["final_blocker"]
        lines.extend(
            [
                "## Provider stop-loss",
                "",
                (
                    f"Local Docker stopped after {stop_loss['elapsed_seconds']:.1f} seconds "
                    f"(ceiling: {stop_loss['stop_loss_seconds']} seconds); the original "
                    "amd64 outer-host path captured no real reset screenshot."
                ),
                "",
                f"Final phase: `{blocker['phase']}`.",
                "",
                blocker["interpretation"],
                "",
                (
                    "The full diagnostic is stored in "
                    "[`day-2/raw/provider-stop-loss.json`](day-2/raw/provider-stop-loss.json)."
                ),
                "",
            ]
        )
        continuation = stop_loss.get("post_stop_loss_continuation")
        if continuation is not None:
            lines.extend(
                [
                    (
                        "A later focused continuation checked the release-native browser path "
                        f"through `{continuation['checked_until']}`. {continuation['result']}"
                    ),
                    "",
                ]
            )
    native = report["runtime"].get("native_arm64_provider_diagnostic")
    if native is not None:
        runtime = native["runtime"]
        probe = native["probe"]
        guest = native["guest"]
        lines.extend(
            [
                "## Native ARM64 provider resolution",
                "",
                (
                    f"The digest-pinned `{runtime['image']}` host returned a real "
                    f"{probe['public_adapter_screenshot_shape'][1]}x"
                    f"{probe['public_adapter_screenshot_shape'][0]} public reset frame in "
                    f"{probe['public_adapter_reset_to_stable_frame_seconds']:.2f} seconds."
                ),
                "",
                (
                    f"The ephemeral guest root was expanded to "
                    f"{guest['expanded_root_size_bytes'] / (1024**3):.1f} GiB with "
                    f"{guest['expanded_root_available_bytes'] / (1024**3):.1f} GiB free."
                ),
                "",
                native["interpretation"],
                "",
                (
                    "The full diagnostic is stored in "
                    "[`day-2/raw/native-arm64-provider-diagnostic.json`]"
                    "(day-2/raw/native-arm64-provider-diagnostic.json)."
                ),
                "",
            ]
        )
    utm = report["runtime"].get("utm_provider_diagnostic")
    if utm is not None:
        config = utm["utm"]
        lines.extend(
            [
                "## UTM fallback diagnostic",
                "",
                (
                    f"UTM `{config['version']}` reports VM `{config['vm_name']}` as "
                    f"`{config['backend']}/{config['architecture']}`, "
                    f"{config['memory_mib']} MiB RAM, hypervisor enabled."
                ),
                "",
                utm["interpretation"],
                "",
                (
                    "The full read-only diagnostic is stored in "
                    "[`day-2/raw/utm-provider-diagnostic.json`]"
                    "(day-2/raw/utm-provider-diagnostic.json)."
                ),
                "",
            ]
        )
    lines.extend(
        [
            "## Automated status",
            "",
            (
                f"Completed sections: {automated['completed_section_count']} / "
                f"{automated['required_section_count']}."
            ),
            "",
            f"Missing: `{', '.join(automated['missing_sections']) or 'none'}`.",
            "",
            f"Failed: `{', '.join(automated['failed_sections']) or 'none'}`.",
            "",
            "## Reset determinism",
            "",
            (
                "| Backend | Resets | Task exact | App state exact | Bitwise visual | Min SSIM | "
                "Max differing pixels | Max channel delta |"
            ),
            "|---|---:|---|---|---|---:|---:|---:|",
        ]
    )
    for name in ("fake_reset", "real_reset"):
        section = evidence.get(name)
        if section is None:
            continue
        summary = section["summary"]
        lines.append(
            f"| {section['backend']} | {section['reset_count']} | "
            f"{_value(summary['semantic_task_state_exact'])} | "
            f"{_value(summary['privileged_application_state_exact'])} | "
            f"{_value(summary['bitwise_visual_determinism'])} | "
            f"{_value(summary['minimum_ssim'])} | "
            f"{summary['maximum_differing_pixel_count']} | "
            f"{summary['maximum_per_channel_delta']} |"
        )
    lines.extend(["", "No visual mask or tolerance is applied by this report.", ""])
    real_reset = evidence.get("real_reset")
    if real_reset is not None:
        changed_regions = [
            record["differing_pixel_bbox_xyxy"]
            for record in real_reset["records"]
            if record["differing_pixel_bbox_xyxy"] is not None
        ]
        lines.append(
            "Observed real-reset differing-region bounding boxes (x1, y1, x2, y2): "
            f"`{changed_regions or 'none'}`."
        )
        lines.append("")

    reward = evidence.get("reward_timing")
    lines.extend(["## Reward timing", ""])
    if reward is None:
        lines.append("Reward-timing evidence is not available.")
    else:
        lines.extend(
            [
                (
                    f"Trajectories: {reward['summary']['trajectory_count']}; "
                    f"passed: {reward['summary']['passed_count']}; "
                    f"failed: {reward['summary']['failed_count']}."
                ),
                "",
                "| Trajectory | First reward | Terminal | Truncated | Expected | Observed | Passed |",
                "|---|---:|---:|---:|---|---|---|",
            ]
        )
        prefix_records = [
            record for record in reward["records"] if record["name"].startswith("golden-prefix-")
        ]
        display_records = [
            record
            for record in reward["records"]
            if not record["name"].startswith("golden-prefix-")
        ]
        if prefix_records:
            display_records.insert(
                10,
                {
                    "name": (
                        f"golden-prefixes-000..{len(prefix_records) - 1:03d} "
                        f"({len(prefix_records)} cases)"
                    ),
                    "first_positive_reward_step": None,
                    "terminal_step": None,
                    "truncation_step": None,
                    "expected_outcome": "no_reward",
                    "observed_outcome": (
                        "no_reward"
                        if all(record["passed"] for record in prefix_records)
                        else "mixed"
                    ),
                    "passed": all(record["passed"] for record in prefix_records),
                },
            )
        for record in display_records:
            lines.append(
                f"| {record['name']} | {_value(record['first_positive_reward_step'])} | "
                f"{_value(record['terminal_step'])} | {_value(record['truncation_step'])} | "
                f"{record['expected_outcome']} | {record['observed_outcome']} | "
                f"{_value(record['passed'])} |"
            )
    lines.extend(["", "## Space integrity", ""])
    spaces = evidence.get("space_integrity")
    if spaces is None:
        lines.append("Space-integrity evidence is not available.")
    else:
        summary = spaces["summary"]
        lines.extend(
            [
                f"- Gymnasium checker: {_value(spaces['gymnasium_checker_passed'])}",
                f"- Sampled valid actions: {spaces['sampled_action_count']}",
                f"- All observations contained: {_value(summary['observations_contained'])}",
                (
                    "- Invalid inputs rejected before backend execution: "
                    f"{_value(summary['invalid_inputs_rejected_before_backend'])}"
                ),
                f"- Post-episode calls rejected: {_value(summary['post_episode_calls_rejected'])}",
            ]
        )
    real_spaces = evidence.get("real_space_smoke")
    lines.extend(["", "### Real OSWorld smoke", ""])
    if real_spaces is None:
        lines.append("Real-backend space smoke evidence is not available.")
    else:
        summary = real_spaces["summary"]
        lines.extend(
            [
                f"- Safe public actions: {real_spaces['valid_action_count']}",
                f"- All observations contained: {_value(summary['observations_contained'])}",
                (
                    "- Invalid inputs rejected before OSWorld execution: "
                    f"{_value(summary['invalid_inputs_rejected_before_backend'])}"
                ),
                f"- Empty Submit produced no reward: {_value(summary['empty_submit_no_reward'])}",
                f"- Provider closed: {_value(summary['provider_closed'])}",
            ]
        )

    real_golden = evidence.get("real_golden_episode")
    lines.extend(["", "### Real OSWorld golden episode", ""])
    if real_golden is None:
        lines.append("Real golden-episode evidence is not available.")
    else:
        summary = real_golden["summary"]
        lines.extend(
            [
                f"- Public actions: {real_golden['action_count']}",
                f"- Positive reward count: {summary['positive_reward_count']}",
                f"- First positive reward step: {_value(summary['first_positive_reward_step'])}",
                f"- Terminal step: {_value(summary['terminal_step'])}",
                f"- Provider closed: {_value(summary['provider_closed'])}",
            ]
        )
        lines.extend(
            [
                "",
                "| Real reward-timing case | Step | Reward | Terminal | Truncated | Passed |",
                "|---|---:|---:|---|---|---|",
            ]
        )
        for record in real_golden["real_reward_timing_subset"]:
            lines.append(
                f"| {record['name']} | {record['step']} | {record['reward']} | "
                f"{_value(record['terminated'])} | {_value(record['truncated'])} | "
                f"{_value(record['passed'])} |"
            )

    audit = evidence.get("reward_hacking")
    lines.extend(["", "## Reward-hacking matrix", ""])
    if audit is None:
        lines.append("Reward-hacking evidence is not available.")
    else:
        lines.extend(
            [
                "| Attack | Disposition | Evidence | Evidence passed |",
                "|---|---|---|---|",
            ]
        )
        for attack in audit["attacks"]:
            lines.append(
                f"| {attack['attack']} | {attack['disposition']} | {attack['evidence']} | "
                f"{_value(attack['evidence_passed'])} |"
            )
        lines.extend(["", "## Known limitations", ""])
        lines.extend(f"- {limitation}" for limitation in audit["known_limitations"])

    lines.extend(["", "## Reproduction commands", "", "```bash"])
    lines.extend(report["reproduction_commands"])
    lines.extend(["```", "", "## Underlying evidence", ""])
    evidence_paths = {
        "fake_reset": "day-2/raw/fake-reset.json",
        "real_reset": "day-2/raw/real-reset.json",
        "reward_timing": "day-2/raw/reward-timing.json",
        "space_integrity": "day-2/raw/space-integrity.json",
        "real_space_smoke": "day-2/raw/real-space-smoke.json",
        "reward_hacking": "day-2/raw/reward-hacking.json",
        "real_golden_episode": "day-2/raw/real-golden-episode.json",
    }
    for name, section in evidence.items():
        status = f"[raw JSON]({evidence_paths[name]})" if section is not None else "missing"
        lines.append(f"- `{name}`: {status}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    report = json.loads(args.input.read_text(encoding="utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(report), encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
