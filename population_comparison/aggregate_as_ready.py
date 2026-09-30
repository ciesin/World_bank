#!/usr/bin/env python3
"""Aggregate city bundles as soon as all five local rasters are available."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time

import pandas as pd

from common import PIPELINE, load_config, raster_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    config = load_config()
    products = list(config["products"])
    prepared = sorted((PIPELINE / "data" / "prepared_segments").glob("*.parquet"))
    output_root = PIPELINE / "outputs" / "segment_population"
    output_root.mkdir(parents=True, exist_ok=True)

    while True:
        pending = [path for path in prepared if not (output_root / path.name).exists()]
        if not pending:
            print(f"All {len(prepared)} cities aggregated.", flush=True)
            return
        ready = [
            path for path in pending
            if all(raster_path(product, path.stem).exists() for product in products)
        ]
        for path in ready:
            city_name = str(pd.read_parquet(path, columns=["city_name"]).iloc[0].city_name)
            subprocess.run(
                [sys.executable, str(PIPELINE / "scripts" / "aggregate_population.py"),
                 "--city", city_name],
                cwd=PIPELINE,
                check=True,
            )
        completed = len(prepared) - len([
            path for path in prepared if not (output_root / path.name).exists()
        ])
        print(f"Aggregated {completed}/{len(prepared)} cities; waiting for {len(pending) - len(ready)} bundles", flush=True)
        if args.once:
            return
        time.sleep(max(5, args.poll_seconds))


if __name__ == "__main__":
    main()
