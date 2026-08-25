"""Explicit, disjoint seed and parameter records frozen for v5 generation."""

from __future__ import annotations

from typing import Literal

from pixelgym.grounding.v5.contracts import (
    DifficultyBand,
    Partition,
    SeedRecord,
    WorkflowFamily,
)

# These values are intentionally written out rather than derived from a runtime
# RNG.  Changing any member changes the partition manifest identity.
DEVELOPMENT_SEEDS = (
    5000, 5001, 5002, 5003, 5004, 5005, 5006, 5007,
    5008, 5009, 5010, 5011, 5012, 5013, 5014, 5015,
    5016, 5017, 5018, 5019, 5020, 5021, 5022, 5023,
)
CALIBRATION_SEEDS = (
    5100, 5101, 5102, 5103, 5104, 5105, 5106, 5107, 5108, 5109,
    5110, 5111, 5112, 5113, 5114, 5115, 5116, 5117, 5118, 5119,
    5120, 5121, 5122, 5123, 5124, 5125, 5126, 5127, 5128, 5129,
    5130, 5131, 5132, 5133, 5134, 5135, 5136, 5137, 5138, 5139,
    5140, 5141, 5142, 5143, 5144, 5145, 5146, 5147, 5148, 5149,
    5150, 5151, 5152, 5153, 5154, 5155, 5156, 5157, 5158, 5159,
)
CONFIRMATORY_SEEDS = (
    6000, 6001, 6002, 6003, 6004, 6005, 6006, 6007,
    6008, 6009, 6010, 6011, 6012, 6013, 6014, 6015,
    6016, 6017, 6018, 6019, 6020, 6021, 6022, 6023,
    6024, 6025, 6026, 6027, 6028, 6029, 6030, 6031,
    6032, 6033, 6034, 6035, 6036, 6037, 6038, 6039,
    6040, 6041, 6042, 6043, 6044, 6045, 6046, 6047,
    6048, 6049, 6050, 6051, 6052, 6053, 6054, 6055,
    6056, 6057, 6058, 6059, 6060, 6061, 6062, 6063,
    6064, 6065, 6066, 6067, 6068, 6069, 6070, 6071,
    6072, 6073, 6074, 6075, 6076, 6077, 6078, 6079,
    6080, 6081, 6082, 6083, 6084, 6085, 6086, 6087,
    6088, 6089, 6090, 6091, 6092, 6093, 6094, 6095,
)


def _band(partition: Partition, logical_index: int) -> DifficultyBand:
    # Logical robustness twins have weight two while unpaired items have
    # weight one. These partition-specific assignments account for that
    # multiplicity and freeze 20%/60%/20% across the complete 180 episodes.
    if partition is Partition.DEVELOPMENT:
        if logical_index == 0:
            return DifficultyBand.REGRESSION
        if logical_index == 3:
            return DifficultyBand.CEILING
    elif partition is Partition.CALIBRATION:
        if logical_index == 0:
            return DifficultyBand.REGRESSION
        if logical_index in {6, 7}:
            return DifficultyBand.CEILING
    else:
        if logical_index in {0, 4}:
            return DifficultyBand.REGRESSION
        if logical_index in {3, 11}:
            return DifficultyBand.CEILING
    return DifficultyBand.FRONTIER


def _partition_records(
    partition: Partition,
    seeds: tuple[int, ...],
    *,
    per_family: int,
    pair_count: int,
) -> tuple[SeedRecord, ...]:
    records: list[SeedRecord] = []
    families = tuple(WorkflowFamily)
    for family_position, family in enumerate(families):
        family_seeds = seeds[family_position * per_family : (family_position + 1) * per_family]
        paired_episode_count = pair_count * 2
        for local_index, seed in enumerate(family_seeds):
            if local_index < paired_episode_count:
                logical_index = local_index // 2
                variant: Literal["base", "twin_a", "twin_b"] = (
                    "twin_a" if local_index % 2 == 0 else "twin_b"
                )
            else:
                logical_index = pair_count + local_index - paired_episode_count
                variant = "base"
            records.append(
                SeedRecord(
                    seed=seed,
                    partition=partition,
                    family=family,
                    family_index=local_index,
                    logical_id=(
                        f"{partition.value}-{family.value}-logical-{logical_index:02d}"
                    ),
                    variant=variant,
                    difficulty_band=_band(partition, logical_index),
                )
            )
    return tuple(records)


SEED_RECORDS = (
    *_partition_records(
        Partition.DEVELOPMENT, DEVELOPMENT_SEEDS, per_family=4, pair_count=0
    ),
    *_partition_records(
        Partition.CALIBRATION, CALIBRATION_SEEDS, per_family=10, pair_count=2
    ),
    *_partition_records(
        Partition.CONFIRMATORY, CONFIRMATORY_SEEDS, per_family=16, pair_count=4
    ),
)
SEED_RECORD_BY_SEED = {record.seed: record for record in SEED_RECORDS}


def validate_seed_contract() -> dict[str, object]:
    expected = {
        Partition.DEVELOPMENT: 24,
        Partition.CALIBRATION: 60,
        Partition.CONFIRMATORY: 96,
    }
    if len(SEED_RECORD_BY_SEED) != len(SEED_RECORDS):
        raise ValueError("v5 seed partitions overlap")
    family_counts: dict[str, dict[str, int]] = {}
    pair_episodes: dict[str, int] = {}
    for partition, count in expected.items():
        selected = [record for record in SEED_RECORDS if record.partition is partition]
        if len(selected) != count:
            raise ValueError(f"{partition.value} requires {count} seed records")
        family_counts[partition.value] = {
            family.value: sum(record.family is family for record in selected)
            for family in WorkflowFamily
        }
        expected_per_family = count // len(WorkflowFamily)
        if set(family_counts[partition.value].values()) != {expected_per_family}:
            raise ValueError(f"{partition.value} family allocation is not balanced")
        pair_episodes[partition.value] = sum(record.robustness_pair for record in selected)
    if pair_episodes != {"development": 0, "calibration": 24, "confirmatory": 48}:
        raise ValueError("v5 robustness-pair allocation changed")
    band_counts = {
        band.value: sum(record.difficulty_band is band for record in SEED_RECORDS)
        for band in DifficultyBand
    }
    if band_counts != {
        "regression_canary": 36,
        "frontier": 108,
        "ceiling_probe": 36,
    }:
        raise ValueError("v5 complete-set difficulty-band allocation changed")
    if any(
        not any(record.family is family and record.difficulty_band is band for record in SEED_RECORDS)
        for family in WorkflowFamily
        for band in DifficultyBand
    ):
        raise ValueError("every v5 family must be represented in every difficulty band")
    return {
        "partition_counts": {key.value: value for key, value in expected.items()},
        "family_counts": family_counts,
        "robustness_pair_episode_counts": pair_episodes,
        "difficulty_band_counts": band_counts,
    }
