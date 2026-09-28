"""Prepare or verify the response-free, counted-network-retry D5.9 successor."""

import argparse
from pathlib import Path

from pixelgym.grounding.v5.d59_haiku_network_retry import OUTPUT_DIRECTORY, execution_plan
from pixelgym.serialization import canonical_json_bytes
from scripts.prepare_grounding_v5_d59_haiku_freeze import write_outputs

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    plan = execution_plan(ROOT, source_revision=args.source_revision)
    write_outputs(
        {"execution-plan.json": canonical_json_bytes(plan) + b"\n"},
        verify=args.verify,
        public=ROOT / OUTPUT_DIRECTORY,
    )
    print(plan["execution_plan_digest"])


if __name__ == "__main__":
    main()
