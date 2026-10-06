#!/usr/bin/env python3
"""Reuse the audited 93-city segment partition from the water workflow."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from common import PIPELINE


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    source = PIPELINE.parents[1] / "river_analysis" / "water_93city_pipeline" / "data" / "prepared_segments"
    target = PIPELINE / "data" / "prepared_segments"
    target.mkdir(parents=True, exist_ok=True)
    files = sorted(source.glob("*.parquet"))
    if len(files) != 93:
        raise RuntimeError(f"Expected 93 audited water-workflow segment partitions; found {len(files)}")
    for path in files:
        output = target / path.name
        if output.exists() and not args.force:
            continue
        shutil.copy2(path, output)
    for name in ("city_name_crosswalk.csv", "partition_audit.csv"):
        if (source / name).exists():
            shutil.copy2(source / name, target / name)
    print(f"Prepared {len(files)} audited city segment partitions in {target}")


if __name__ == "__main__":
    main()
