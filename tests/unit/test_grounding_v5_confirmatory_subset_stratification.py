"""Confirmatory subsets are stratified by family."""

from __future__ import annotations

from pixelgym.grounding.v5.contracts import Partition
from pixelgym.grounding.v5.manifests import partition_manifest
from pixelgym.grounding.v5.planning import _family_stratified_subset


def test_confirmatory_subsets_are_family_stratified() -> None:
    confirmatory = tuple(partition_manifest(Partition.CONFIRMATORY)["records"])
    for per_family in (2, 4):
        subset = _family_stratified_subset(confirmatory, per_family=per_family)
        counts = {
            family: sum(record["seed_record"]["family"] == family for record in subset)
            for family in {record["seed_record"]["family"] for record in confirmatory}
        }
        assert set(counts.values()) == {per_family}
