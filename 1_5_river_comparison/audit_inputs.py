#!/usr/bin/env python3
"""Audit local inputs and write a machine-readable setup report."""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pyogrio

from common import PIPELINE, load_buffers, load_config


def main() -> None:
    config = load_config()
    buffers = load_buffers(config)
    shps = sorted(Path(config["grwl_directory"]).glob("*.shp"))
    if not shps:
        raise FileNotFoundError(f"No GRWL shapefiles in {config['grwl_directory']}")
    sample = pyogrio.read_info(shps[0])
    report = {
        "city_count": int(len(buffers)),
        "city_names_unique": bool(buffers[config["city_fields"]["name"]].is_unique),
        "segment_layer": config["segments_layer"],
        "grwl_tile_count": len(shps),
        "grwl_geometry_type": sample["geometry_type"],
        "grwl_fields": list(sample["fields"]),
        "jrc_latest_complete_year": config["jrc_gsw"]["recent_end_year"],
        "example_cities_found": sorted(set(config["example_cities"]) & set(buffers[config["city_fields"]["name"]])),
    }
    if report["city_count"] != 93:
        raise ValueError(f"Expected 93 cities, found {report['city_count']}")
    required = {"width_m", "lakeFlag", "segmentID", "segmentInd"}
    if not required.issubset(report["grwl_fields"]):
        raise ValueError(f"GRWL fields missing: {sorted(required - set(report['grwl_fields']))}")
    output = PIPELINE / "data" / "input_audit.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
