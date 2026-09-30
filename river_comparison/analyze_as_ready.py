#!/usr/bin/env python3
"""Run city analysis incrementally as OSM and JRC inputs become available."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time

from common import PIPELINE, city_output_stem, jrc_raster_path, load_buffers, load_config, prepared_segment_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--poll-seconds", type=int, default=30)
    args = parser.parse_args()
    config = load_config()
    fields = config["city_fields"]
    buffers = load_buffers(config)
    script = PIPELINE / "scripts" / "analyze_cities.py"
    while True:
        for city in buffers.itertuples(index=False):
            stem = city_output_stem(city, fields)
            output = PIPELINE / "outputs" / "segment_water" / f"{stem}_segment_water.parquet"
            if output.exists():
                continue
            required = [
                prepared_segment_path(stem), jrc_raster_path(stem),
                PIPELINE / "data" / "osm" / f"{stem}_osm_water_lines.parquet",
                PIPELINE / "data" / "osm" / f"{stem}_osm_water_polygons.parquet",
                PIPELINE / "data" / "grwl" / f"{stem}_grwl_river_polygon.parquet",
            ]
            if all(path.exists() for path in required):
                subprocess.run(
                    [sys.executable, str(script), "--city", str(getattr(city, fields["name"]))],
                    check=True,
                )
        completed = len(list((PIPELINE / "outputs" / "segment_water").glob("*_segment_water.parquet")))
        print(f"Analyzed {completed}/93 cities", flush=True)
        if completed == len(buffers):
            return
        time.sleep(max(5, args.poll_seconds))


if __name__ == "__main__":
    main()
