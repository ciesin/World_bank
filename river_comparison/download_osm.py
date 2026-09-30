#!/usr/bin/env python3
"""Download and cache OSM water features from Overpass, one city at a time."""

from __future__ import annotations

import argparse
import os
import time

import geopandas as gpd
import pandas as pd
import requests
import shapely

from common import PIPELINE, city_output_stem, load_buffers, load_config, normalize_text


os.environ.setdefault("MPLCONFIGDIR", "/tmp/wb-water-matplotlib")


LINE_TYPES = {"LineString", "MultiLineString"}
POLYGON_TYPES = {"Polygon", "MultiPolygon"}


def clean_features(features: gpd.GeoDataFrame, config: dict, city_polygon) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    if features.empty:
        empty = gpd.GeoDataFrame({"geometry": gpd.GeoSeries([], crs=config["analysis_crs"])})
        return empty.copy(), empty.copy()
    features = features.reset_index()
    features = features.loc[features.geometry.notna() & ~features.geometry.is_empty].copy()
    features = features.to_crs(config["analysis_crs"])
    features.geometry = features.geometry.intersection(city_polygon)
    features = features.loc[~features.geometry.is_empty].copy()
    waterways = set(config["osm"]["waterway_values"])
    allowed_water = set(config["osm"]["water_values"])
    waterway_values = features.get("waterway", pd.Series(index=features.index, dtype="object"))
    natural_values = features.get("natural", pd.Series(index=features.index, dtype="object"))
    water_values = features.get("water", pd.Series(index=features.index, dtype="object"))
    geom_types = features.geometry.geom_type
    lines = features.loc[waterway_values.isin(waterways) & geom_types.isin(LINE_TYPES)].copy()
    natural_water = (
        natural_values.eq("water") & (water_values.isna() | water_values.isin(allowed_water))
    )
    polygons = features.loc[
        geom_types.isin(POLYGON_TYPES)
        & (natural_water | natural_values.eq("wetland") | waterway_values.isin(waterways))
    ].copy()
    # Unioning removes duplicate member ways returned alongside tagged relations.
    if not lines.empty:
        lines = gpd.GeoDataFrame({"geometry": [shapely.union_all(lines.geometry.to_numpy())]}, crs=features.crs).explode(index_parts=False)
    if not polygons.empty:
        polygons = gpd.GeoDataFrame({"geometry": [shapely.union_all(polygons.geometry.to_numpy())]}, crs=features.crs).explode(index_parts=False)
    return lines.reset_index(drop=True), polygons.reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", action="append")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        import osmnx as ox
    except ImportError as exc:
        raise RuntimeError("Install requirements.txt in the project virtual environment before downloading OSM") from exc
    config = load_config()
    fields = config["city_fields"]
    buffers = load_buffers(config)
    if args.city:
        wanted = {normalize_text(name) for name in args.city}
        buffers = buffers.loc[buffers[fields["name"]].map(normalize_text).isin(wanted)]
    output_root = PIPELINE / "data" / "osm"
    output_root.mkdir(parents=True, exist_ok=True)
    ox.settings.requests_timeout = config["osm"]["request_timeout_seconds"]
    # Public mirrors expose different /status response formats. Explicit
    # request pacing and retry/backoff below replace OSMnx's status parser.
    ox.settings.overpass_rate_limit = False
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(PIPELINE / "data" / "osm" / "overpass_cache")
    tags = {"natural": config["osm"]["natural_values"], "waterway": config["osm"]["waterway_values"]}
    for city in buffers.itertuples(index=False):
        stem = city_output_stem(city, fields)
        line_output = output_root / f"{stem}_osm_water_lines.parquet"
        polygon_output = output_root / f"{stem}_osm_water_polygons.parquet"
        if line_output.exists() and polygon_output.exists() and not args.force:
            print(f"Exists: {line_output}", flush=True)
            continue
        city_metric = gpd.GeoDataFrame({"geometry": [city.geometry]}, crs=buffers.crs)
        query_polygon = city_metric.to_crs("EPSG:4326").geometry.iloc[0]
        features = None
        last_error = None
        endpoints = config["osm"]["overpass_endpoints"]
        for attempt in range(config["osm"]["max_attempts_per_city"]):
            endpoint = endpoints[attempt % len(endpoints)]
            ox.settings.overpass_url = endpoint
            try:
                features = ox.features_from_polygon(query_polygon, tags=tags)
                break
            except ox._errors.InsufficientResponseError:
                features = gpd.GeoDataFrame({"geometry": gpd.GeoSeries([], crs="EPSG:4326")})
                break
            except (requests.RequestException, ox._errors.ResponseStatusCodeError) as exc:
                last_error = exc
                wait = config["osm"]["retry_backoff_seconds"] * (attempt // len(endpoints) + 1)
                print(
                    f"Retry {attempt + 1}/{config['osm']['max_attempts_per_city']} for {stem} "
                    f"after {type(exc).__name__} from {endpoint}: {exc}; waiting {wait}s",
                    flush=True,
                )
                time.sleep(wait)
        if features is None:
            raise RuntimeError(f"All Overpass attempts failed for {stem}") from last_error
        lines, polygons = clean_features(features, config, city.geometry)
        lines.to_parquet(line_output, index=False)
        polygons.to_parquet(polygon_output, index=False)
        print(f"Wrote {line_output} ({len(lines):,} dissolved line parts; {len(polygons):,} polygon parts)", flush=True)
        time.sleep(max(0, config["osm"]["overpass_pause_seconds"]))


if __name__ == "__main__":
    main()
