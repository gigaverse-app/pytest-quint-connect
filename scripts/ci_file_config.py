"""Derive file selection from the same graph and execution inputs as lane admission."""

import sys
from pathlib import Path

from pytest_selective.ci import LaneConfig
from pytest_selective.impact_models import ImpactConfig


def file_config(lanes: LaneConfig) -> ImpactConfig:
    if lanes.global_inputs is None:
        raise ValueError("This repository must declare its shared CI execution inputs")
    return lanes.graph.model_copy(
        update={"global_inputs": sorted(set(lanes.graph.global_inputs) | set(lanes.global_inputs))}
    )


if __name__ == "__main__":
    lanes = LaneConfig.model_validate_json(Path(".github/test-lanes.json").read_text(encoding="utf-8"))
    Path(sys.argv[1]).write_text(file_config(lanes).model_dump_json(), encoding="utf-8")
