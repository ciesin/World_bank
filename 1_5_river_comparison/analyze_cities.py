#!/usr/bin/env python3
"""Create aligned presence grids and segment-level water statistics by city."""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import shapely
from exactextract import exact_extract
from exactextract.raster import RasterioRasterSource
from rasterio.features import rasterize, shapes

from common import (
    PIPELINE, city_output_stem, jrc_raster_path, load_buffers, load_config,
    normalize_text, prepared_segment_path, reporting_group,
)


COMBINATIONS = {
    1: "osm_only", 2: "gsw_only", 3: "osm_gsw", 4: "grwl_only",
    5: "osm_grwl", 6: "gsw_grwl", 7: "all_three",
}
LINE_TYPES = {"LineString", "MultiLineString"}


def read_parquet_or_empty(path: Path, crs: str) -> gpd.GeoDataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    frame = gpd.read_parquet(path)
    if frame.crs is None:
        frame = frame.set_crs(crs)
    return frame


def rasterize_presence(frame: gpd.GeoDataFrame, raster_crs, out_shape, transform, all_touched=True) -> np.ndarray:
    if frame.empty:
        return np.zeros(out_shape, dtype=bool)
    projected = frame.to_crs(raster_crs)
    geometries = [geom for geom in projected.geometry if geom is not None and not geom.is_empty]
    if not geometries:
        return np.zeros(out_shape, dtype=bool)
    return rasterize(
        ((geom, 1) for geom in geometries), out_shape=out_shape, transform=transform,
        fill=0, dtype="uint8", all_touched=all_touched,
    ).astype(bool)


def mask_polygon(mask: np.ndarray, transform, raster_crs, target_crs):
    geometries = [shapely.geometry.shape(geom) for geom, value in shapes(
        mask.astype("uint8"), mask=mask, transform=transform
    ) if int(value) == 1]
    if not geometries:
        return shapely.GeometryCollection()
    union = shapely.union_all(geometries)
    return gpd.GeoSeries([union], crs=raster_crs).to_crs(target_crs).iloc[0]


def line_lengths_by_segment(line_geometry, segments: gpd.GeoDataFrame) -> pd.Series:
    result = pd.Series(0.0, index=segments["segment_id"], dtype="float64")
    if line_geometry is None or line_geometry.is_empty:
        return result
    parts = gpd.GeoDataFrame({"geometry": [line_geometry]}, crs=segments.crs).explode(index_parts=False)
    parts = parts.loc[parts.geometry.geom_type.isin(LINE_TYPES)]
    if parts.empty:
        return result
    intersections = gpd.overlay(
        segments[["segment_id", "geometry"]], parts, how="intersection", keep_geom_type=False
    )
    intersections = intersections.loc[intersections.geometry.geom_type.isin(LINE_TYPES)]
    if intersections.empty:
        return result
    measured = intersections.assign(_length_km=intersections.geometry.length / 1000)
    totals = measured.groupby("segment_id")["_length_km"].sum()
    result.loc[totals.index] = totals
    return result


def raster_band_stats(source, band_index: int, segments: gpd.GeoDataFrame, prefix: str) -> pd.DataFrame:
    projected = segments[["segment_id", "geometry"]].to_crs(source.crs)
    result = exact_extract(
        RasterioRasterSource(source, band_idx=band_index), projected, ["mean", "sum"],
        include_cols=["segment_id"], output="pandas", strategy="raster-sequential",
    ).set_index("segment_id").reindex(segments["segment_id"])
    return result.rename(columns={"mean": f"{prefix}_mean", "sum": f"{prefix}_sum"})


def aggregate_outputs() -> None:
    segment_files = sorted((PIPELINE / "outputs" / "segment_water").glob("*.parquet"))
    cell_files = sorted((PIPELINE / "outputs" / "city_cell_counts").glob("*.csv"))
    if not segment_files or not cell_files:
        return
    cell_data = pd.concat((pd.read_csv(path) for path in cell_files), ignore_index=True)
    grouped = cell_data.groupby(["reporting_group", "combination"], as_index=False)["cell_count"].sum()
    totals = grouped.groupby("reporting_group")["cell_count"].transform("sum")
    grouped["proportion_percent"] = 100 * grouped["cell_count"] / totals
    analysis_root = PIPELINE / "outputs" / "analysis"
    analysis_root.mkdir(parents=True, exist_ok=True)
    grouped.to_csv(analysis_root / "figure_4_1_presence_combinations.csv", index=False)

    columns = [
        "city_id", "city_name", "country", "city_size_group", "source_zone",
        "osm_all_total_km", "osm_not_jrc_gsw_all_km",
        "osm_not_jrc_gsw_permanent_km", "osm_not_grwl_km",
    ]
    segments = pd.concat((pd.read_parquet(path, columns=columns) for path in segment_files), ignore_index=True)
    rows = []
    groups = [
        ("all_cities", None, None),
        ("cities_lt_300k_urban_center", "population_lt_300k", "city_segment"),
        ("cities_lt_300k_peri_urban", "population_lt_300k", "hexgrid"),
        ("cities_ge_300k_urban_center", "population_ge_300k", "city_segment"),
        ("cities_ge_300k_peri_urban", "population_ge_300k", "hexgrid"),
    ]
    value_columns = columns[5:]
    for label, size, zone in groups:
        subset = segments if size is None else segments.loc[
            (segments.city_size_group == size) & (segments.source_zone == zone)
        ]
        row = {"reporting_group": label, "segment_count": len(subset), "city_count": subset.city_id.nunique()}
        row.update({column: subset[column].sum() for column in value_columns})
        rows.append(row)
    pd.DataFrame(rows).to_csv(analysis_root / "table_4_2_osm_unmatched_length_km.csv", index=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", action="append")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-incomplete", action="store_true")
    args = parser.parse_args()
    config = load_config()
    fields = config["city_fields"]
    buffers = load_buffers(config)
    if args.city:
        wanted = {normalize_text(name) for name in args.city}
        buffers = buffers.loc[buffers[fields["name"]].map(normalize_text).isin(wanted)]
    segment_root = PIPELINE / "outputs" / "segment_water"
    cell_root = PIPELINE / "outputs" / "city_cell_counts"
    combo_root = PIPELINE / "outputs" / "presence_grids"
    for root in (segment_root, cell_root, combo_root):
        root.mkdir(parents=True, exist_ok=True)

    for city in buffers.itertuples(index=False):
        stem = city_output_stem(city, fields)
        output = segment_root / f"{stem}_segment_water.parquet"
        required = {
            "segments": prepared_segment_path(stem),
            "jrc": jrc_raster_path(stem),
            "osm_lines": PIPELINE / "data" / "osm" / f"{stem}_osm_water_lines.parquet",
            "osm_polygons": PIPELINE / "data" / "osm" / f"{stem}_osm_water_polygons.parquet",
            "grwl": PIPELINE / "data" / "grwl" / f"{stem}_grwl_river_polygon.parquet",
        }
        missing = [key for key, path in required.items() if not path.exists()]
        if missing and args.skip_incomplete:
            print(f"Not ready: {stem} ({', '.join(missing)})", flush=True)
            continue
        if missing:
            raise FileNotFoundError(f"Missing inputs for {stem}: {missing}")
        if output.exists() and not args.force:
            print(f"Exists: {output}", flush=True)
            continue
        segments = gpd.read_parquet(required["segments"])
        osm_lines = read_parquet_or_empty(required["osm_lines"], config["analysis_crs"])
        osm_polygons = read_parquet_or_empty(required["osm_polygons"], config["analysis_crs"])
        grwl = read_parquet_or_empty(required["grwl"], config["analysis_crs"])
        with rasterio.open(required["jrc"]) as source:
            descriptions = {name: index for index, name in enumerate(source.descriptions, start=1)}
            needed = {"recent_any_water", "recent_permanent_water"}
            needed.update(f"decade_{key}_occurrence_pct" for key in config["jrc_gsw"]["decades"])
            if missing_bands := needed - set(descriptions):
                raise ValueError(f"JRC raster bands missing for {stem}: {sorted(missing_bands)}")
            shape = (source.height, source.width)
            transform = source.transform
            raster_crs = source.crs
            any_data = source.read(descriptions["recent_any_water"], masked=True)
            permanent_data = source.read(descriptions["recent_permanent_water"], masked=True)
            # Earth Engine can write masked floating-point cells as NaN even
            # when the GeoTIFF advertises a numeric nodata value. Exclude both
            # representations from the valid-observation mask.
            valid = ~np.ma.getmaskarray(any_data) & np.isfinite(any_data.filled(np.nan))
            gsw_any = valid & (any_data.filled(0) >= 0.5)
            gsw_permanent = valid & (permanent_data.filled(0) >= 0.5)
            osm_presence = rasterize_presence(osm_lines, raster_crs, shape, transform) | rasterize_presence(
                osm_polygons, raster_crs, shape, transform
            )
            grwl_presence = rasterize_presence(grwl, raster_crs, shape, transform)
            city_mask = rasterize_presence(
                gpd.GeoDataFrame({"geometry": [city.geometry]}, crs=buffers.crs),
                raster_crs, shape, transform, all_touched=False,
            )
            combination = (
                osm_presence.astype("uint8") + 2 * gsw_permanent.astype("uint8")
                + 4 * grwl_presence.astype("uint8")
            )
            combination[~city_mask] = 255
            profile = source.profile.copy()
            profile.update(count=1, dtype="uint8", nodata=255, compress="zstd")
            combo_path = combo_root / f"{stem}_presence_combinations.tif"
            with rasterio.open(combo_path, "w", **profile) as target:
                target.write(combination, 1)
                target.set_band_description(1, "presence_combination_code")

            zones = segments[["source_zone", "geometry"]].dissolve(by="source_zone").reset_index()
            zone_raster = np.zeros(shape, dtype="uint8")
            for code, zone_name in ((1, "city_segment"), (2, "hexgrid")):
                zone_raster[rasterize_presence(
                    zones.loc[zones.source_zone == zone_name], raster_crs, shape, transform,
                    all_touched=False,
                )] = code
            rows = []
            city_size = str(segments.city_size_group.iloc[0])
            for group_name, selector in (
                ("all_cities", city_mask),
                (reporting_group(city_size, "city_segment"), zone_raster == 1),
                (reporting_group(city_size, "hexgrid"), zone_raster == 2),
            ):
                for code, label in COMBINATIONS.items():
                    rows.append({
                        "city_id": int(segments.city_id.iloc[0]), "city_name": str(segments.city_name.iloc[0]),
                        "reporting_group": group_name, "combination": label,
                        "combination_code": code, "cell_count": int(np.sum(selector & (combination == code))),
                    })
            pd.DataFrame(rows).to_csv(cell_root / f"{stem}_cell_counts.csv", index=False)

            for key in config["jrc_gsw"]["decades"]:
                prefix = f"jrc_occurrence_{key}"
                stats = raster_band_stats(source, descriptions[f"decade_{key}_occurrence_pct"], segments, prefix)
                segments[f"{prefix}_mean_pct"] = stats[f"{prefix}_mean"].to_numpy()
            recent_stats = raster_band_stats(source, descriptions["recent_permanent_water"], segments, "jrc_permanent")
            segments["jrc_permanent_cell_equivalent"] = recent_stats["jrc_permanent_sum"].to_numpy()

        osm_geometry = shapely.union_all(osm_lines.geometry.to_numpy()) if not osm_lines.empty else shapely.GeometryCollection()
        gsw_all_polygon = mask_polygon(gsw_any & city_mask, transform, raster_crs, segments.crs)
        gsw_permanent_polygon = mask_polygon(gsw_permanent & city_mask, transform, raster_crs, segments.crs)
        grwl_polygon = shapely.union_all(grwl.geometry.to_numpy()) if not grwl.empty else shapely.GeometryCollection()
        variants = {
            "osm_all_total_km": osm_geometry,
            "osm_not_jrc_gsw_all_km": shapely.difference(osm_geometry, gsw_all_polygon),
            "osm_not_jrc_gsw_permanent_km": shapely.difference(osm_geometry, gsw_permanent_polygon),
            "osm_not_grwl_km": shapely.difference(osm_geometry, grwl_polygon),
        }
        for column, geometry in variants.items():
            segments[column] = line_lengths_by_segment(geometry, segments).to_numpy()
        segments.to_parquet(output, index=False)
        print(f"Wrote {output} ({len(segments):,} segments)", flush=True)
    aggregate_outputs()


if __name__ == "__main__":
    main()
