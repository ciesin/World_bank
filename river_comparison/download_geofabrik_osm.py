#!/usr/bin/env python3
"""Download dated Geofabrik extracts and build city-level OSM water layers."""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import requests
import shapely
from shapely.geometry import box

from common import PIPELINE, city_output_stem, load_buffers, load_config, normalize_text


def download(url: str, output: Path) -> None:
    """Stream a PBF to a partial file and resume when the server supports it."""
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_suffix(output.suffix + ".partial")
    offset = partial.stat().st_size if partial.exists() else 0
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    with requests.get(url, headers=headers, stream=True, timeout=(30, 300)) as response:
        response.raise_for_status()
        if offset and response.status_code != 206:
            offset = 0
            mode = "wb"
        else:
            mode = "ab" if offset else "wb"
        total = int(response.headers.get("content-length", 0)) + offset
        next_report = offset + 100_000_000
        with partial.open(mode) as target:
            for block in response.iter_content(chunk_size=4 * 1024 * 1024):
                if not block:
                    continue
                target.write(block)
                offset += len(block)
                if offset >= next_report:
                    print(f"  downloaded {offset / 1e6:.0f}/{total / 1e6:.0f} MB", flush=True)
                    next_report += 100_000_000
    partial.replace(output)


def from_wkb(value):
    if isinstance(value, str):
        value = bytes.fromhex(value)
    return shapely.from_wkb(value, on_invalid="ignore")


def extract_country(pbf: Path, cities: gpd.GeoDataFrame, config: dict) -> tuple[list, list]:
    """Read a country PBF once (two passes for areas), retaining study-buffer hits."""
    import osmium

    waterways = set(config["osm"]["waterway_values"])
    natural_values = set(config["osm"]["natural_values"])
    allowed_water = set(config["osm"]["water_values"])
    study_area = shapely.union_all(cities.geometry.to_numpy())
    study_bbox = box(*study_area.bounds)
    factory = osmium.geom.WKBFactory()
    lines: list = []
    polygons: list = []
    processor = osmium.FileProcessor(str(pbf)).with_areas()
    examined = 0
    for obj in processor:
        examined += 1
        if examined % 5_000_000 == 0:
            print(f"  scanned {examined:,} OSM objects", flush=True)
        tags = obj.tags
        if obj.is_way():
            waterway = tags.get("waterway")
            natural = tags.get("natural")
            # Area=yes and natural water/wetland ways are represented by the
            # assembled Area object and must not be counted as linear water.
            if waterway not in waterways or tags.get("area") == "yes" or natural in natural_values:
                continue
            try:
                geometry = from_wkb(factory.create_linestring(obj))
            except (RuntimeError, ValueError):
                continue
            if geometry is not None and not geometry.is_empty and study_bbox.intersects(geometry) and study_area.intersects(geometry):
                lines.append(geometry)
        elif obj.is_area():
            waterway = tags.get("waterway")
            natural = tags.get("natural")
            water = tags.get("water")
            natural_water = natural == "water" and (water is None or water in allowed_water)
            if not (natural_water or natural == "wetland" or waterway in waterways):
                continue
            try:
                geometry = from_wkb(factory.create_multipolygon(obj))
            except (RuntimeError, ValueError):
                continue
            if geometry is not None and not geometry.is_empty and study_bbox.intersects(geometry) and study_area.intersects(geometry):
                polygons.append(geometry)
    print(f"  retained {len(lines):,} lines and {len(polygons):,} polygon features", flush=True)
    return lines, polygons


def write_cities(cities: gpd.GeoDataFrame, lines: list, polygons: list, config: dict, force: bool) -> None:
    fields = config["city_fields"]
    output_root = PIPELINE / "data" / "osm"
    output_root.mkdir(parents=True, exist_ok=True)
    line_frame = gpd.GeoDataFrame({"geometry": lines}, crs="EPSG:4326").to_crs(config["analysis_crs"])
    polygon_frame = gpd.GeoDataFrame({"geometry": polygons}, crs="EPSG:4326").to_crs(config["analysis_crs"])
    for city in cities.to_crs(config["analysis_crs"]).itertuples(index=False):
        stem = city_output_stem(city, fields)
        line_output = output_root / f"{stem}_osm_water_lines.parquet"
        polygon_output = output_root / f"{stem}_osm_water_polygons.parquet"
        if line_output.exists() and polygon_output.exists() and not force:
            print(f"Exists: {stem}", flush=True)
            continue
        city_lines = line_frame.loc[line_frame.intersects(city.geometry), "geometry"]
        city_polygons = polygon_frame.loc[polygon_frame.intersects(city.geometry), "geometry"]
        clipped_lines = [geometry.intersection(city.geometry) for geometry in city_lines]
        clipped_polygons = [geometry.intersection(city.geometry) for geometry in city_polygons]
        clipped_lines = [geometry for geometry in clipped_lines if not geometry.is_empty]
        clipped_polygons = [geometry for geometry in clipped_polygons if not geometry.is_empty]
        line_union = shapely.union_all(clipped_lines) if clipped_lines else None
        polygon_union = shapely.union_all(clipped_polygons) if clipped_polygons else None
        city_line_frame = gpd.GeoDataFrame(
            {"geometry": [] if line_union is None else [line_union]}, crs=config["analysis_crs"]
        ).explode(index_parts=False).reset_index(drop=True)
        city_polygon_frame = gpd.GeoDataFrame(
            {"geometry": [] if polygon_union is None else [polygon_union]}, crs=config["analysis_crs"]
        ).explode(index_parts=False).reset_index(drop=True)
        city_line_frame.to_parquet(line_output, index=False)
        city_polygon_frame.to_parquet(polygon_output, index=False)
        print(f"Wrote {stem}: {len(city_line_frame):,} line parts, {len(city_polygon_frame):,} polygon parts", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--country", action="append", help="Repeatable country-name filter")
    parser.add_argument("--force", action="store_true", help="Overwrite existing city OSM outputs")
    parser.add_argument("--keep-pbf", action="store_true", help="Retain downloaded country PBF files")
    args = parser.parse_args()
    config = load_config()
    fields = config["city_fields"]
    buffers = load_buffers(config).to_crs("EPSG:4326")
    extracts = config["osm"]["geofabrik_extracts"]
    if args.country:
        wanted = {normalize_text(value) for value in args.country}
        extracts = {country: slug for country, slug in extracts.items() if normalize_text(country) in wanted}
        if not extracts:
            raise ValueError("No configured country matches --country")
    pbf_root = PIPELINE / "data" / "osm" / "geofabrik"
    for country, slug in extracts.items():
        cities = buffers.loc[buffers[fields["country"]] == country].copy()
        if cities.empty:
            raise ValueError(f"No study cities found for configured country {country}")
        pbf = pbf_root / f"{slug}-latest.osm.pbf"
        url = f"{config['osm']['geofabrik_base_url']}/{slug}-latest.osm.pbf"
        if not pbf.exists():
            print(f"Downloading {country}: {url}", flush=True)
            download(url, pbf)
        print(f"Extracting {country} for {len(cities)} cities", flush=True)
        lines, polygons = extract_country(pbf, cities, config)
        write_cities(cities, lines, polygons, config, args.force)
        if not args.keep_pbf:
            pbf.unlink(missing_ok=True)
            print(f"Removed cached PBF after successful extraction: {pbf.name}", flush=True)


if __name__ == "__main__":
    main()
