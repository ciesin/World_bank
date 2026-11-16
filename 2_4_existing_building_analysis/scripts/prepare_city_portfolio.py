#!/usr/bin/env python3
"""Prepare per-city segments and AOIs from the 93-city combined GeoPackage."""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from pyproj import CRS, Transformer


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path("/Users/ggy1/Downloads/segments_hexbin_20260821.gpkg")
LAYER = "cities93_segments_hexbins"
SOURCE_CRS = "ESRI:54009"
PORTFOLIO = ROOT / "data/cities"
OUTPUT_ROOT = ROOT / "outputs/cities"


def slugify(value: str) -> str:
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()
    return text


def grouped_bounds(connection, legacy: bool) -> pd.DataFrame:
    condition = "IS NOT NULL" if legacy else "IS NULL"
    query = f"""
        SELECT c.UC_NM_MN AS name,
               c.ID_HDC_G0 AS city_id,
               MAX(c.CTR_MN_NM) AS country,
               MIN(r.minx) AS minx, MIN(r.miny) AS miny,
               MAX(r.maxx) AS maxx, MAX(r.maxy) AS maxy,
               COUNT(*) AS feature_count,
               SUM(c.Sqm_area) AS sqm_area
        FROM {LAYER} c
        JOIN rtree_{LAYER}_Shape r ON r.id = c.OBJECTID
        WHERE c.ID_HDC_G0 {condition}
        GROUP BY c.UC_NM_MN, c.ID_HDC_G0
    """
    return pd.read_sql_query(query, connection)


def bbox_intersection(a, b) -> float:
    return max(0.0, min(a.maxx, b.maxx) - max(a.minx, b.minx)) * max(
        0.0, min(a.maxy, b.maxy) - max(a.miny, b.miny)
    )


def match_groups(hexes: pd.DataFrame, legacy: pd.DataFrame) -> pd.DataFrame:
    rows = []
    used = set()
    for _, hexrow in hexes.iterrows():
        scores = []
        hc_x = (hexrow.minx + hexrow.maxx) / 2
        hc_y = (hexrow.miny + hexrow.maxy) / 2
        for index, oldrow in legacy.iterrows():
            intersection = bbox_intersection(hexrow, oldrow)
            old_area = max(1.0, (oldrow.maxx - oldrow.minx) * (oldrow.maxy - oldrow.miny))
            oc_x = (oldrow.minx + oldrow.maxx) / 2
            oc_y = (oldrow.miny + oldrow.maxy) / 2
            distance = float(np.hypot(hc_x - oc_x, hc_y - oc_y))
            scores.append((intersection / old_area, -distance, index))
        scores.sort(reverse=True)
        overlap, neg_distance, index = scores[0]
        if index in used:
            raise ValueError(f"Legacy group matched twice: {legacy.loc[index, 'name']}")
        if overlap < 0.5:
            raise ValueError(
                f"Weak canonical/legacy match: {hexrow['name']} -> "
                f"{legacy.loc[index, 'name']} ({overlap:.3f})"
            )
        used.add(index)
        oldrow = legacy.loc[index]
        minx = min(hexrow.minx, oldrow.minx)
        miny = min(hexrow.miny, oldrow.miny)
        maxx = max(hexrow.maxx, oldrow.maxx)
        maxy = max(hexrow.maxy, oldrow.maxy)
        transformer = Transformer.from_crs(SOURCE_CRS, 4326, always_xy=True)
        west, south, east, north = transformer.transform_bounds(
            minx, miny, maxx, maxy, densify_pts=21
        )
        lon = (west + east) / 2
        lat = (south + north) / 2
        zone = int(np.floor((lon + 180) / 6) + 1)
        epsg = 32600 + zone if lat >= 0 else 32700 + zone
        rows.append({
            "city_slug": slugify(hexrow["name"]),
            "city_name": hexrow["name"],
            "legacy_name": oldrow["name"],
            "country": oldrow["country"],
            "city_id": int(oldrow["city_id"]),
            "hex_segments": int(hexrow.feature_count),
            "legacy_segments": int(oldrow.feature_count),
            "total_segments": int(hexrow.feature_count + oldrow.feature_count),
            "source_area_km2": float(
                (float(hexrow.sqm_area or 0) + float(oldrow.sqm_area or 0)) / 1e6
            ),
            "west": west, "south": south, "east": east, "north": north,
            "analysis_epsg": epsg,
            "bbox_match_legacy_coverage": overlap,
            "bbox_center_distance_m": -neg_distance,
        })
    if len(used) != len(legacy):
        missing = legacy.loc[~legacy.index.isin(used), "name"].tolist()
        raise ValueError(f"Unmatched legacy groups: {missing}")
    result = pd.DataFrame(rows).sort_values("city_slug").reset_index(drop=True)
    if result.city_slug.duplicated().any():
        raise ValueError("City slugs are not unique")
    return result


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def prepare_city(row) -> dict:
    city_dir = PORTFOLIO / row.city_slug
    input_dir = city_dir / "inputs"
    input_dir.mkdir(parents=True, exist_ok=True)
    (OUTPUT_ROOT / row.city_slug / "analysis").mkdir(parents=True, exist_ok=True)
    (OUTPUT_ROOT / row.city_slug / "integrated").mkdir(parents=True, exist_ok=True)
    names = sorted(set([row.city_name, row.legacy_name]))
    where = "UC_NM_MN IN (" + ",".join(sql_literal(v) for v in names) + ")"
    segments = gpd.read_file(
        SOURCE, layer=LAYER, where=where, engine="pyogrio", fid_as_index=True
    )
    expected = int(row.total_segments)
    if len(segments) != expected:
        raise ValueError(f"{row.city_name}: read {len(segments):,}, expected {expected:,}")
    segments = segments.loc[segments.geometry.notna() & ~segments.geometry.is_empty].copy()
    segments = segments.reset_index().rename(columns={"fid": "OBJECTID"})
    segments = segments.to_crs(int(row.analysis_epsg))
    if (~segments.geometry.is_valid).any():
        segments.geometry = shapely.make_valid(segments.geometry.to_numpy())
    segments = segments.sort_values("OBJECTID").reset_index(drop=True)
    segments.insert(0, "ANALYSIS_ID", np.arange(len(segments), dtype="int32"))
    segments["SEGMENT_UID"] = row.city_slug + "-" + segments.OBJECTID.astype(str)
    segments["CANONICAL_CITY"] = row.city_name
    segments["analysis_area_ha"] = shapely.area(segments.geometry.to_numpy()) / 10_000
    union = segments.geometry.union_all()
    overlap_m2 = float(segments.analysis_area_ha.sum() * 10_000 - union.area)
    keep = [
        "ANALYSIS_ID", "SEGMENT_UID", "OBJECTID", "GRID_ID", "ID_SEG",
        "UC_NM_MN", "CANONICAL_CITY", "MERGE_SRC", "analysis_area_ha", "geometry",
    ]
    segments[keep].to_parquet(input_dir / "segments.parquet", index=False)
    aoi = gpd.GeoDataFrame(
        {"city_slug": [row.city_slug], "city_name": [row.city_name],
         "country": [row.country], "analysis_epsg": [int(row.analysis_epsg)]},
        geometry=[union], crs=int(row.analysis_epsg),
    )
    aoi.to_parquet(input_dir / "aoi.parquet", index=False)
    aoi.to_crs(4326).to_file(input_dir / "aoi.geojson", driver="GeoJSON")
    metadata = {
        "city_slug": row.city_slug,
        "city_name": row.city_name,
        "legacy_name": row.legacy_name,
        "country": row.country,
        "city_id": int(row.city_id),
        "segments": int(len(segments)),
        "analysis_epsg": int(row.analysis_epsg),
        "aoi_area_km2": float(union.area / 1e6),
        "segment_overlap_m2": overlap_m2,
        "bbox_wgs84": list(aoi.to_crs(4326).total_bounds),
    }
    (input_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata


def main() -> None:
    PORTFOLIO.mkdir(parents=True, exist_ok=True)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(SOURCE) as connection:
        hexes = grouped_bounds(connection, legacy=False)
        legacy = grouped_bounds(connection, legacy=True)
    if len(hexes) != 93 or len(legacy) != 93:
        raise ValueError(f"Expected 93 canonical and legacy groups; got {len(hexes)}, {len(legacy)}")
    manifest = match_groups(hexes, legacy)
    manifest.to_csv(PORTFOLIO / "city_manifest.csv", index=False)
    print(manifest[["city_name", "legacy_name", "country", "total_segments"]].to_string(index=False))
    records = []
    for index, row in manifest.iterrows():
        print(f"[{index + 1:02d}/93] Preparing {row.city_name}", flush=True)
        records.append(prepare_city(row))
    (PORTFOLIO / "portfolio_preparation.json").write_text(
        json.dumps(records, indent=2) + "\n"
    )
    print(f"Prepared {len(records)} cities", flush=True)


if __name__ == "__main__":
    main()
