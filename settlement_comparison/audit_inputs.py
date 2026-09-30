#!/usr/bin/env python3
"""Read-only audit of the city buffer and segment inputs."""

from __future__ import annotations

import json
import sqlite3

import geopandas as gpd
import pandas as pd
import pyogrio

from common import PIPELINE, load_config, normalize_text


def main() -> None:
    config = load_config()
    segment_path = (PIPELINE / config["segments"]).resolve()
    buffers = gpd.read_file(config["city_buffers"], layer=config["city_layer"])
    info = pyogrio.read_info(segment_path, layer=config["segments_layer"])
    with sqlite3.connect(segment_path) as connection:
        names = pd.read_sql_query(
            f'SELECT DISTINCT "{config["segment_city_field"]}" AS city_name '
            f'FROM "{config["segments_layer"]}" WHERE "{config["segment_city_field"]}" IS NOT NULL',
            connection,
        ).city_name.tolist()
    segment_names = {normalize_text(name) for name in names}
    aliases = {"m bour": "mbour"}
    unmatched = [
        name for name in buffers[config["city_fields"]["name"]]
        if aliases.get(normalize_text(name), normalize_text(name)) not in segment_names
    ]
    result = {
        "buffer_path": config["city_buffers"], "buffer_count": len(buffers),
        "buffer_crs": str(buffers.crs), "unique_buffer_ids": int(buffers[config["city_fields"]["id"]].nunique()),
        "segment_path": str(segment_path), "segment_layer": config["segments_layer"],
        "segment_count": int(info["features"]), "segment_crs": str(info["crs"]),
        "segment_city_name_count": len(names), "unmatched_buffer_city_names": unmatched,
    }
    print(json.dumps(result, indent=2))
    if len(buffers) != 93 or unmatched:
        raise RuntimeError("Input audit failed")


if __name__ == "__main__":
    main()
