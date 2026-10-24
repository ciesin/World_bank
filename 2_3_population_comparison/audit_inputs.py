#!/usr/bin/env python3
"""Audit local study inputs without downloading data or running analysis."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pyogrio
import rasterio

from common import PIPELINE, load_buffers, load_config


def table_count(path: str, layer: str) -> int:
    with sqlite3.connect(path) as connection:
        return int(connection.execute(f'SELECT COUNT(*) FROM "{layer}"').fetchone()[0])


def main() -> None:
    config = load_config()
    fields = config["city_fields"]
    buffers = load_buffers(config)
    segment_info = pyogrio.read_info(config["segments"], layer=config["segments_layer"])
    with sqlite3.connect(config["segments"]) as connection:
        layer = config["segments_layer"]
        summary = connection.execute(
            f'''SELECT COUNT(*), COUNT(DISTINCT "UC_NM_MN"),
                       SUM("ID_SEG" IS NOT NULL), SUM("MERGE_SRC" IS NOT NULL),
                       COUNT(DISTINCT "UC_NM_MN" || '|' || "GRID_ID")
                FROM "{layer}"'''
        ).fetchone()
    night = Path(config["local_nighttime_raster"])
    with rasterio.open(night) as source:
        night_info = {
            "path": str(night),
            "crs": str(source.crs),
            "shape": [source.height, source.width],
            "resolution": list(source.res),
            "nodata": source.nodata,
            "description": source.descriptions[0],
            "year": source.tags().get("YEAR"),
            "units": source.tags().get("UNITS"),
        }
    result = {
        "city_count": int(len(buffers)),
        "unique_city_names": int(buffers[fields["name"]].nunique()),
        "countries": sorted(buffers[fields["country"]].dropna().unique().tolist()),
        "city_population_lt_300k": int((buffers[fields["population"]] < config["city_population_split"]).sum()),
        "city_population_ge_300k": int((buffers[fields["population"]] >= config["city_population_split"]).sum()),
        "segment_count": int(summary[0]),
        "raw_segment_city_name_count": int(summary[1]),
        "central_segment_count": int(summary[2]),
        "hexgrid_count": int(summary[3]),
        "unique_city_grid_keys": int(summary[4]),
        "segment_crs": str(segment_info["crs"]),
        "nighttime_raster": night_info,
    }
    output = PIPELINE / "data" / "input_audit.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
