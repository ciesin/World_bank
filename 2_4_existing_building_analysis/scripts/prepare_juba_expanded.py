#!/usr/bin/env python3
"""Prepare the expanded Juba AOI, segment layer, and locally available sources."""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely


ROOT = Path(__file__).resolve().parents[1]
SEGMENTS_SOURCE = Path("/Users/ggy1/Downloads/segments_hexbin_20260821.gpkg")
SEGMENTS_LAYER = "cities93_segments_hexbins"
CRS = "EPSG:32636"


def prepare_segments() -> tuple[gpd.GeoDataFrame, object]:
    segments = gpd.read_file(
        SEGMENTS_SOURCE,
        layer=SEGMENTS_LAYER,
        where="UC_NM_MN = 'Juba'",
    ).to_crs(CRS)
    segments = segments[segments.geometry.notna() & ~segments.geometry.is_empty].copy()
    if segments.GRID_ID.isna().any() or segments.GRID_ID.duplicated().any():
        raise ValueError("GRID_ID must be non-null and unique within Juba")
    if not segments.geometry.is_valid.all():
        segments.geometry = shapely.make_valid(segments.geometry.to_numpy())
    segments = segments.sort_values("GRID_ID").reset_index(drop=True)
    segments.rename(columns={"ID_SEG": "SOURCE_ID_SEG"}, inplace=True)
    segments.insert(0, "ANALYSIS_ID", np.arange(len(segments), dtype="int32"))
    segments["analysis_area_ha"] = segments.geometry.area / 10_000
    union = segments.geometry.union_all()

    sum_area = float(segments.geometry.area.sum())
    overlap_area = sum_area - float(union.area)
    # Sub-metre boundary slivers can arise from reprojection; material overlap cannot.
    if overlap_area > 10.0:
        raise ValueError(f"Expanded segment polygons overlap by {overlap_area:.2f} m2")

    processed = ROOT / "data/processed"
    processed.mkdir(parents=True, exist_ok=True)
    keep = [
        "ANALYSIS_ID",
        "GRID_ID",
        "SOURCE_ID_SEG",
        "UC_NM_MN",
        "MERGE_SRC",
        "analysis_area_ha",
        "geometry",
    ]
    segments[keep].to_parquet(processed / "juba_segments_20260821.parquet", index=False)
    segments[keep].to_file(
        processed / "juba_segments_20260821.gpkg",
        layer="juba_segments",
        driver="GPKG",
    )
    aoi = gpd.GeoDataFrame({"name": ["Juba expanded segment union"]}, geometry=[union], crs=CRS)
    aoi.to_crs(4326).to_file(ROOT / "data/aoi/juba_expanded.geojson", driver="GeoJSON")
    return segments[keep], union


def google_urls(aoi) -> list[str]:
    manifest_path = ROOT / "data/raw/google_2_5d/17_EPSG_32636_2023_06_30.json"
    manifest = json.loads(manifest_path.read_text())
    xmin, ymin, xmax, ymax = aoi.bounds
    urls = []
    for tileset in manifest["tilesets"]:
        for source in tileset["sources"]:
            transform = source["affineTransform"]
            dimensions = source["dimensions"]
            left = float(transform["translateX"])
            top = float(transform["translateY"])
            right = left + float(transform["scaleX"]) * int(dimensions["width"])
            bottom = top + float(transform["scaleY"]) * int(dimensions["height"])
            tile_xmin, tile_xmax = sorted((left, right))
            tile_ymin, tile_ymax = sorted((bottom, top))
            if tile_xmax <= xmin or tile_xmin >= xmax or tile_ymax <= ymin or tile_ymin >= ymax:
                continue
            uri = source["uris"][0]
            gs_path = manifest["uriPrefix"] + uri
            if not gs_path.startswith("gs://"):
                raise ValueError(f"Unexpected Google URI: {gs_path}")
            bucket_path = gs_path[5:]
            bucket, object_path = bucket_path.split("/", 1)
            urls.append(f"https://storage.googleapis.com/{bucket}/{object_path}")
    urls = sorted(set(urls))
    (ROOT / "data/raw/google_2_5d/urls_expanded.txt").write_text("\n".join(urls) + "\n")
    return urls


def clip_overture(aoi):
    source = ROOT / "data/raw/overture_expanded/juba_buildings_expanded.parquet"
    data = gpd.read_parquet(source).to_crs(CRS)
    indexes = data.sindex.query(aoi, predicate="intersects")
    data = data.iloc[indexes].copy()
    data.geometry = shapely.intersection(data.geometry.to_numpy(), aoi)
    data = data[data.geometry.notna() & ~data.geometry.is_empty].copy()
    data.to_parquet(ROOT / "data/processed/overture_juba_expanded.parquet", index=False)
    return len(data)


def clip_globfp(aoi):
    source = ROOT / "data/raw/3d_globfp/tile_1362/1362_31.25_3.75_32.5_5.0_OD_UG.shp"
    bbox_wgs84 = gpd.GeoSeries([aoi], crs=CRS).to_crs(4326).total_bounds
    data = gpd.read_file(source, bbox=tuple(bbox_wgs84)).to_crs(CRS)
    indexes = data.sindex.query(aoi, predicate="intersects")
    data = data.iloc[indexes].copy()
    data.geometry = shapely.intersection(data.geometry.to_numpy(), aoi)
    data = data[data.geometry.notna() & ~data.geometry.is_empty].copy()
    data.to_parquet(ROOT / "data/processed/3d_globfp_juba_expanded.parquet", index=False)
    return len(data)


def combine_gba():
    polygon = gpd.read_parquet(ROOT / "data/processed/gba_polygon_juba_expanded.parquet")
    polygon["component"] = "Polygon"
    odbl = gpd.read_parquet(ROOT / "data/processed/gba_odbl_juba_expanded.parquet")
    odbl["component"] = "ODbLPolygon"
    combined = gpd.GeoDataFrame(
        pd.concat([polygon, odbl], ignore_index=True), geometry="geometry", crs=polygon.crs
    ).to_crs(CRS)
    combined.to_parquet(
        ROOT / "data/processed/global_building_atlas_juba_expanded.parquet", index=False
    )
    return len(combined)


def main():
    segments, aoi = prepare_segments()
    urls = google_urls(aoi)
    overture_count = clip_overture(aoi)
    globfp_count = clip_globfp(aoi)
    gba_count = combine_gba()
    summary = {
        "segments": int(len(segments)),
        "area_km2": float(aoi.area / 1e6),
        "google_tiles": len(urls),
        "overture_features": overture_count,
        "globfp_features": globfp_count,
        "gba_features": gba_count,
    }
    (ROOT / "data/processed/juba_expanded_preparation.json").write_text(
        json.dumps(summary, indent=2) + "\n"
    )
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
