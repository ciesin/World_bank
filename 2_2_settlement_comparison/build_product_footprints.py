#!/usr/bin/env python3
"""Create one clipped city footprint for each vector and raster product."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import rasterio
import shapely
from exactextract import exact_extract
from pyproj import CRS
from shapely.geometry import GeometryCollection

from common import (
    PIPELINE, first_existing, load_config, normalize_text, polygonal,
    read_vector, slugify, vector_layers,
)


VECTOR_PRODUCTS = [
    "ghs_ucdb_2024", "ghs_fua", "grid3_settlements_2024",
    "africapolis_2020", "city_adm_2026",
]
RASTER_PRODUCTS = ["wsf_tracker_2025", "dynamic_world_2025", "google_2_5d_2023"]


def segment_name_lookup(path: Path, layer: str) -> dict[str, str]:
    with sqlite3.connect(path) as connection:
        values = pd.read_sql_query(
            f'SELECT DISTINCT "UC_NM_MN" AS city_name FROM "{layer}" WHERE "UC_NM_MN" IS NOT NULL',
            connection,
        ).city_name
    return {normalize_text(value): value for value in values}


def city_segments(config: dict, city_name: str, lookup: dict[str, str]) -> gpd.GeoDataFrame:
    stored_name = lookup.get(normalize_text(city_name))
    aliases = {"pointe noire": "pointe-noire", "m bour": "mbour"}
    if stored_name is None:
        stored_name = lookup.get(aliases.get(normalize_text(city_name), ""))
    if stored_name is None:
        raise ValueError(f"No segment city matches {city_name!r}")
    escaped = stored_name.replace("'", "''")
    return gpd.read_file(
        config["segments"], layer=config["segments_layer"],
        where=f'"{config["segment_city_field"]}" = \'{escaped}\'', engine="pyogrio",
    )


def raster_path(city_id: int, slug: str, product: str) -> Path | None:
    roots = [PIPELINE / "data/rasters" / product, PIPELINE / "data/rasters/earth_engine"]
    pattern = f"{city_id}_{slug}_{product}*.tif"
    matches = [path for root in roots if root.exists() for path in root.glob(pattern)]
    return sorted(matches)[0] if matches else None


def vector_candidates(path: Path, layer, buffer, analysis_crs) -> gpd.GeoDataFrame:
    if path.suffix.lower() in {".parquet", ".geoparquet"}:
        data = read_vector(path, layer)
    else:
        info = pyogrio.read_info(path, layer=layer)
        source_crs = CRS.from_user_input(info["crs"])
        bbox = gpd.GeoSeries([buffer], crs=analysis_crs).to_crs(source_crs).total_bounds
        data = read_vector(path, layer, bbox=tuple(bbox))
    if data.crs is None:
        raise ValueError(f"Source has no CRS: {path} layer={layer}")
    data = polygonal(data.to_crs(analysis_crs))
    if data.empty:
        return data
    return data.loc[data.intersects(buffer)].copy()


def select_vector(candidates, specification, city_id, city_name, buffer):
    if candidates.empty:
        return None, None
    id_field = first_existing(candidates.columns, specification.get("id_fields", []))
    name_field = first_existing(candidates.columns, specification.get("name_fields", []))
    exact_id = np.zeros(len(candidates), dtype=bool)
    if id_field:
        exact_id = candidates[id_field].astype(str).eq(str(city_id)).to_numpy()
    name_match = np.zeros(len(candidates), dtype=bool)
    if name_field:
        target = normalize_text(city_name)
        accepted_names = {target}
        for alias in specification.get("name_aliases", {}).get(city_name, []):
            accepted_names.add(normalize_text(alias))
        names = candidates[name_field].map(normalize_text)
        name_match = names.map(
            lambda value: bool(value) and any(
                value == accepted or value in accepted or accepted in value
                for accepted in accepted_names
            )
        ).to_numpy()
    if specification.get("require_name_match") and not np.any(exact_id | name_match):
        return None, None
    anchor = buffer.representative_point()
    contains_anchor = shapely.covers(candidates.geometry.to_numpy(), anchor)
    intersection_area = shapely.area(shapely.intersection(candidates.geometry.to_numpy(), buffer))
    ranking = pd.DataFrame({
        "position": np.arange(len(candidates)), "exact_id": exact_id.astype(int),
        "name_match": name_match.astype(int), "contains_anchor": contains_anchor.astype(int),
        "intersection_area": intersection_area,
    }).sort_values(
        ["exact_id", "name_match", "contains_anchor", "intersection_area"],
        ascending=[False, False, False, False],
    )
    position = int(ranking.iloc[0].position)
    reason = ranking.iloc[0].to_dict()
    return candidates.iloc[position], reason


def raster_footprint(path, segments, buffer, threshold_m2):
    with rasterio.open(path) as source:
        if source.crs is None or not source.crs.is_projected:
            raise ValueError(f"Raster must use a projected CRS: {path}")
        transform = source.transform
        pixel_area = abs(transform.a * transform.e - transform.b * transform.d)
        projected = polygonal(segments.to_crs(source.crs))
        statistics = exact_extract(
            source,
            projected[["geometry"]].reset_index(drop=True),
            "sum",
            output="pandas",
            strategy="raster-sequential",
        )
    # The rasters are binary. exact_extract's sum weights each built pixel (1)
    # by the fraction of its area covered by the segment. Multiplying by pixel
    # area therefore gives built-up area without a centre-cell approximation.
    pixel_equivalents = pd.to_numeric(statistics["sum"], errors="coerce").fillna(0.0).to_numpy()
    area = pixel_equivalents * pixel_area
    # Coverage fractions can carry single-precision noise (for example, an
    # exact 40 m2 overlap may evaluate as 40.000001). Treat numerically equal
    # values as equal so that the configured strict-greater-than rule holds.
    keep = (area > threshold_m2) & ~np.isclose(
        area, threshold_m2, rtol=1e-7, atol=1e-6,
    )
    selected = projected.loc[keep]
    geometry = selected.geometry.union_all() if len(selected) else None
    if geometry is not None:
        geometry = shapely.intersection(geometry, gpd.GeoSeries([buffer], crs=segments.crs).to_crs(projected.crs).iloc[0])
        geometry = gpd.GeoSeries([geometry], crs=projected.crs).to_crs(segments.crs).iloc[0]
        geometry = shapely.make_valid(geometry, method="structure", keep_collapsed=False)
    segment_result = pd.DataFrame({
        "segment_row": np.arange(len(projected), dtype="int64"),
        "built_pixel_equivalents": pixel_equivalents,
        "built_up_area_m2": area,
        "built_flag": keep,
    })
    return geometry, segment_result, pixel_area


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", action="append")
    parser.add_argument("--allow-missing", action="store_true")
    args = parser.parse_args()
    config = load_config()
    config["segments"] = str((PIPELINE / config["segments"]).resolve())
    fields = config["city_fields"]
    analysis_crs = config["analysis_crs"]
    buffers = gpd.read_file(config["city_buffers"], layer=config["city_layer"]).to_crs(analysis_crs)
    if args.city:
        wanted = {slugify(value) for value in args.city}
        buffers = buffers.loc[buffers[fields["name"]].map(slugify).isin(wanted)]
    manifest_path = PIPELINE / "data/vector_source_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError("Run acquire_vector_sources.py first")
    manifest = json.loads(manifest_path.read_text())["records"]
    available = {
        source: [record for record in manifest if record["source"] == source and record.get("path")]
        for source in VECTOR_PRODUCTS
    }
    lookup = segment_name_lookup(Path(config["segments"]), config["segments_layer"])
    footprint_rows = []
    audit_rows = []
    segment_output = PIPELINE / "outputs/segment_flags"
    segment_output.mkdir(parents=True, exist_ok=True)
    raster_config = config["raster_processing"]

    for city in buffers.itertuples(index=False):
        city_id = int(getattr(city, fields["id"]))
        city_name = str(getattr(city, fields["name"]))
        country = str(getattr(city, fields["country"]))
        population = float(getattr(city, config["population_field"]))
        slug = slugify(city_name)
        buffer = city.geometry
        buffer_area = float(buffer.area)
        segments = city_segments(config, city_name, lookup).to_crs(analysis_crs)
        segments = segments.loc[segments.intersects(buffer)].copy()
        segments.geometry = segments.geometry.intersection(buffer)

        for product in VECTOR_PRODUCTS:
            specification = config["sources"][product]
            pieces = []
            for record in available[product]:
                if record.get("country") and record["country"] != country:
                    continue
                path = Path(record["path"])
                for layer in vector_layers(path, specification.get("layer_hint", "")):
                    part = vector_candidates(path, layer, buffer, analysis_crs)
                    if len(part):
                        part["_source_path"] = str(path)
                        part["_source_layer"] = layer
                        pieces.append(part)
            candidates = gpd.GeoDataFrame(
                pd.concat(pieces, ignore_index=True), geometry="geometry", crs=analysis_crs
            ) if pieces else gpd.GeoDataFrame(geometry=[], crs=analysis_crs)
            selected, reason = select_vector(candidates, specification, city_id, city_name, buffer)
            if selected is None:
                audit_rows.append({"city_id": city_id, "city_name": city_name, "product": product, "status": "missing", "candidate_count": 0})
                continue
            geometry = shapely.make_valid(
                shapely.intersection(selected.geometry, buffer),
                method="structure", keep_collapsed=False,
            )
            area = float(geometry.area)
            footprint_rows.append({
                "city_id": city_id, "city_name": city_name, "country": country,
                "population_2025": population, "product": product, "product_type": "vector",
                "buffer_area_m2": buffer_area, "footprint_area_m2": area,
                "coverage_pct": 100 * area / buffer_area, "geometry": geometry,
            })
            audit_rows.append({
                "city_id": city_id, "city_name": city_name, "product": product,
                "status": "selected", "candidate_count": len(candidates), **reason,
                "source_path": selected.get("_source_path"), "source_layer": selected.get("_source_layer"),
            })

        flag_table = pd.DataFrame({"segment_row": np.arange(len(segments), dtype="int64")})
        for id_field in config.get("segment_id_fields", []):
            if id_field in segments:
                flag_table[id_field] = segments[id_field].to_numpy()
        for product in RASTER_PRODUCTS:
            path = raster_path(city_id, slug, product)
            if path is None:
                audit_rows.append({"city_id": city_id, "city_name": city_name, "product": product, "status": "raster_missing"})
                continue
            geometry, flags, pixel_area = raster_footprint(
                path, segments, buffer, raster_config["built_up_area_threshold_m2"],
            )
            if (
                raster_config.get(product, {}).get("all_zero_raster_is_missing")
                and not np.any(flags.built_pixel_equivalents.to_numpy() > 0)
            ):
                audit_rows.append({
                    "city_id": city_id, "city_name": city_name, "product": product,
                    "status": "all_zero_raster_assumed_missing", "raster_path": str(path),
                    "pixel_area_m2": pixel_area, "segments": len(segments),
                })
                continue
            flag_table[f"{product}_built_pixel_equivalents"] = flags.built_pixel_equivalents
            flag_table[f"{product}_built_up_area_m2"] = flags.built_up_area_m2
            flag_table[f"{product}_built_flag"] = flags.built_flag
            area = float(geometry.area) if geometry is not None and not geometry.is_empty else 0.0
            footprint_rows.append({
                "city_id": city_id, "city_name": city_name, "country": country,
                "population_2025": population, "product": product, "product_type": "raster",
                "buffer_area_m2": buffer_area, "footprint_area_m2": area,
                "coverage_pct": 100 * area / buffer_area,
                "geometry": geometry if geometry is not None else GeometryCollection(),
            })
            audit_rows.append({
                "city_id": city_id, "city_name": city_name, "product": product,
                "status": "created", "raster_path": str(path), "pixel_area_m2": pixel_area,
                "segments": len(segments), "built_segments": int(flags.built_flag.sum()),
            })
        flag_table.to_parquet(segment_output / f"{city_id}_{slug}.parquet", index=False)

    footprints = gpd.GeoDataFrame(footprint_rows, geometry="geometry", crs=analysis_crs)
    output = PIPELINE / "outputs"
    output.mkdir(parents=True, exist_ok=True)
    footprints.to_parquet(output / "city_product_footprints.parquet", index=False)
    footprints.to_file(output / "city_product_footprints.gpkg", layer="footprints", driver="GPKG")
    pd.DataFrame(audit_rows).to_csv(output / "footprint_build_audit.csv", index=False)
    missing = pd.DataFrame(audit_rows).status.astype(str).str.contains("missing").sum()
    if missing and not args.allow_missing:
        raise RuntimeError(f"Built available footprints but {missing} city-product inputs are missing; inspect footprint_build_audit.csv")
    print(f"Wrote {len(footprints)} city-product footprints; missing inputs: {missing}")


if __name__ == "__main__":
    main()
