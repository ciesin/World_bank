#!/usr/bin/env python3
"""Partition segment geometries into the 93 cities using audited name mapping."""

from __future__ import annotations

import argparse
import json
import sqlite3

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

from common import PIPELINE, city_output_stem, load_buffers, load_config, normalize_text


def compact_name(value) -> str:
    return normalize_text(value).replace(" ", "")


def city_name_crosswalk(config: dict, buffers: gpd.GeoDataFrame) -> tuple[dict[str, list[str]], list[dict]]:
    """Assign every raw segment city name to exactly one target buffer."""
    field = config["segment_fields"]["raw_city_name"]
    with sqlite3.connect(config["segments"]) as connection:
        raw_names = [
            row[0] for row in connection.execute(
                f'SELECT DISTINCT "{field}" FROM "{config["segments_layer"]}" '
                f'WHERE "{field}" IS NOT NULL'
            )
        ]
    city_field = config["city_fields"]["name"]
    target_by_key = {compact_name(name): name for name in buffers[city_field]}
    assigned: dict[str, list[str]] = {name: [] for name in buffers[city_field]}
    audit = []
    for raw_name in raw_names:
        target = target_by_key.get(compact_name(raw_name))
        method = "normalized_name"
        if target is None:
            escaped = raw_name.replace("'", "''")
            features = gpd.read_file(
                config["segments"], layer=config["segments_layer"],
                where=f'"{field}" = \'{escaped}\'', engine="pyogrio",
            ).to_crs(config["analysis_crs"])
            merged = features.geometry.union_all()
            overlap = buffers.geometry.intersection(merged).area
            target = str(buffers.loc[overlap.idxmax(), city_field])
            method = "largest_buffer_overlap"
        assigned[target].append(raw_name)
        audit.append({"raw_segment_city_name": raw_name, "target_city_name": target, "method": method})
    return assigned, audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", action="append", help="City name; may be repeated")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    config = load_config()
    fields = config["city_fields"]
    sf = config["segment_fields"]
    buffers = load_buffers(config)
    name_crosswalk, crosswalk_audit = city_name_crosswalk(config, buffers)
    if args.city:
        wanted = {name.casefold() for name in args.city}
        buffers = buffers.loc[buffers[fields["name"]].str.casefold().isin(wanted)]
    output_root = PIPELINE / "data" / "prepared_segments"
    output_root.mkdir(parents=True, exist_ok=True)
    audit = []
    pd.DataFrame(crosswalk_audit).sort_values(
        ["target_city_name", "raw_segment_city_name"]
    ).to_csv(output_root / "city_name_crosswalk.csv", index=False)

    for city in buffers.itertuples(index=False):
        stem = city_output_stem(city, fields)
        output = output_root / f"{stem}.parquet"
        if output.exists() and not args.force:
            print(f"Exists: {output}", flush=True)
            continue
        raw_names = name_crosswalk[str(getattr(city, fields["name"]))]
        quoted = ",".join("'" + name.replace("'", "''") + "'" for name in raw_names)
        segments = gpd.read_file(
            config["segments"],
            layer=config["segments_layer"],
            where=f'"{sf["raw_city_name"]}" IN ({quoted})',
            engine="pyogrio",
            fid_as_index=True,
        ).to_crs(config["analysis_crs"])
        # OBJECTID is the GeoPackage feature id, so pyogrio exposes it as the
        # index instead of a regular attribute column.
        segments[sf["object_id"]] = segments.index.to_numpy(dtype="int64")
        segments = segments.reset_index(drop=True)
        source_feature_count = len(segments)
        segments = segments.loc[
            segments.geometry.notna()
            & ~segments.geometry.is_empty
        ].copy()
        segments["segment_area_km2"] = shapely.area(segments.geometry.to_numpy()) / 1_000_000
        segments["city_id"] = int(getattr(city, fields["id"]))
        segments["city_name"] = str(getattr(city, fields["name"]))
        segments["country"] = str(getattr(city, fields["country"]))
        segments["city_population_2025"] = float(getattr(city, fields["population"]))
        segments["city_size_group"] = np.where(
            segments["city_population_2025"] < config["city_population_split"],
            "population_lt_300k",
            "population_ge_300k",
        )
        segments["source_zone"] = np.where(
            segments[sf["central_segment_id"]].notna(), "city_segment", "hexgrid"
        )
        segments["segment_id"] = (
            segments["city_id"].astype(str)
            + ":"
            + segments[sf["object_id"]].astype("int64").astype(str)
        )
        keep = [
            "segment_id", "city_id", "city_name", "country", "city_population_2025",
            "city_size_group", "source_zone", "segment_area_km2",
            sf["object_id"], sf["grid_id"], sf["raw_city_name"],
            sf["central_segment_id"], sf["merge_source"], "geometry",
        ]
        segments[keep].to_parquet(output, index=False)
        audit.append({
            "city_id": int(getattr(city, fields["id"])),
            "city_name": str(getattr(city, fields["name"])),
            "source_feature_count": int(source_feature_count),
            "segment_count": int(len(segments)),
            "central_segment_count": int((segments["source_zone"] == "city_segment").sum()),
            "hexgrid_count": int((segments["source_zone"] == "hexgrid").sum()),
            "raw_city_name_count": int(segments[sf["raw_city_name"]].nunique()),
            "raw_city_names": " | ".join(sorted(raw_names)),
            "output": str(output),
        })
        print(f"Wrote {output} ({len(segments):,} segments)", flush=True)

    if audit:
        pd.DataFrame(audit).to_csv(output_root / "partition_audit.csv", index=False)
        (output_root / "partition_audit.json").write_text(json.dumps(audit, indent=2) + "\n")


if __name__ == "__main__":
    main()
