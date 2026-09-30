#!/usr/bin/env python3
"""Clip GRWL centerlines, apply measured half-width buffers, and dissolve by city."""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio
import shapely
from shapely.geometry import box

from common import PIPELINE, city_output_stem, load_buffers, load_config, normalize_text


def tile_index(root: Path) -> gpd.GeoDataFrame:
    cache = PIPELINE / "data" / "grwl" / "tile_index.parquet"
    if cache.exists():
        return gpd.read_parquet(cache)
    rows = []
    for path in sorted(root.glob("*.shp")):
        info = pyogrio.read_info(path)
        rows.append({"path": str(path), "tile": path.stem, "geometry": box(*info["total_bounds"])})
    result = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    cache.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(cache, index=False)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", action="append")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    config = load_config()
    fields = config["city_fields"]
    buffers = load_buffers(config)
    if args.city:
        wanted = {normalize_text(name) for name in args.city}
        buffers = buffers.loc[buffers[fields["name"]].map(normalize_text).isin(wanted)]
    root = Path(config["grwl_directory"])
    index = tile_index(root)
    output_root = PIPELINE / "data" / "grwl"
    output_root.mkdir(parents=True, exist_ok=True)
    include_flags = set(config["grwl"]["include_lake_flags"])

    for city in buffers.itertuples(index=False):
        stem = city_output_stem(city, fields)
        polygon_output = output_root / f"{stem}_grwl_river_polygon.parquet"
        line_output = output_root / f"{stem}_grwl_centerlines.parquet"
        if polygon_output.exists() and line_output.exists() and not args.force:
            print(f"Exists: {polygon_output}", flush=True)
            continue
        city_frame = gpd.GeoDataFrame({"geometry": [city.geometry]}, crs=buffers.crs)
        city_wgs84 = city_frame.to_crs("EPSG:4326")
        query_geom = city_wgs84.geometry.iloc[0]
        candidates = index.loc[index.intersects(query_geom)]
        pieces = []
        for item in candidates.itertuples(index=False):
            data = gpd.read_file(item.path, bbox=query_geom.bounds, engine="pyogrio")
            if data.empty:
                continue
            data["grwl_tile"] = item.tile
            pieces.append(data)
        if pieces:
            lines = gpd.GeoDataFrame(pd.concat(pieces, ignore_index=True), crs="EPSG:4326")
            flag_field = config["grwl"]["lake_flag_field"]
            width_field = config["grwl"]["width_field"]
            lines = lines.loc[
                lines[flag_field].isin(include_flags)
                & pd.to_numeric(lines[width_field], errors="coerce").gt(0)
                & lines.geometry.notna() & ~lines.geometry.is_empty
            ].copy()
            if not lines.empty:
                metric_crs = city_wgs84.estimate_utm_crs()
                metric_city = city_wgs84.to_crs(metric_crs).geometry.iloc[0]
                lines = lines.to_crs(metric_crs)
                lines.geometry = lines.geometry.intersection(metric_city)
                lines = lines.loc[~lines.geometry.is_empty].copy()
                widths = pd.to_numeric(lines[width_field], errors="coerce").to_numpy(dtype="float64")
                buffers_array = shapely.buffer(
                    lines.geometry.to_numpy(), widths / 2, cap_style="round", join_style="round"
                )
                dissolved = shapely.union_all(buffers_array)
                polygon = gpd.GeoDataFrame(
                    {"city_id": [int(getattr(city, fields["id"]))],
                     "city_name": [str(getattr(city, fields["name"]))],
                     "geometry": [dissolved]}, crs=metric_crs,
                ).to_crs(config["analysis_crs"])
                lines = lines.to_crs(config["analysis_crs"])
            else:
                polygon = gpd.GeoDataFrame(
                    {"city_id": pd.Series(dtype="int64"), "city_name": pd.Series(dtype="string"),
                     "geometry": gpd.GeoSeries([], crs=config["analysis_crs"])},
                    crs=config["analysis_crs"],
                )
                lines = gpd.GeoDataFrame(lines, geometry="geometry", crs=config["analysis_crs"])
        else:
            lines = gpd.GeoDataFrame({"geometry": gpd.GeoSeries([], crs=config["analysis_crs"])})
            polygon = gpd.GeoDataFrame({"geometry": gpd.GeoSeries([], crs=config["analysis_crs"])})
        lines.to_parquet(line_output, index=False)
        polygon.to_parquet(polygon_output, index=False)
        print(f"Wrote {polygon_output} ({len(lines):,} centerline sections)", flush=True)


if __name__ == "__main__":
    main()
