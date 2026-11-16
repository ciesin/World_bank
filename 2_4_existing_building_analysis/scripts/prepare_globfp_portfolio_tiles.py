#!/usr/bin/env python3
"""Identify and download only the 3D-GloBFP tiles needed by the 93 cities."""

from __future__ import annotations

import json
import re
from pathlib import Path

import geopandas as gpd
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw/3d_globfp"
MANIFEST = ROOT / "data/cities/city_manifest.csv"


def file_catalog() -> pd.DataFrame:
    rows = []
    for path in sorted(RAW.glob("figshare_part*.json")):
        article = json.loads(path.read_text())
        for item in article["files"]:
            match = re.match(r"(\d+)_", item["name"])
            if not match:
                continue
            rows.append({
                "grid_ID": int(match.group(1)),
                "filename": item["name"],
                "download_url": item["download_url"],
                "bytes": int(item["size"]),
                "part": int(re.search(r"part(\d+)", path.stem).group(1)),
            })
    result = pd.DataFrame(rows).drop_duplicates("grid_ID")
    # Three world-grid cells contain no distributed building file.
    if len(result) < 2500:
        raise ValueError(f"Unexpectedly incomplete file catalog: {len(result):,}")
    return result


def main() -> None:
    grid = gpd.read_file(RAW / "world_grid/world_grid.shp").to_crs(4326)
    files = file_catalog()
    cities = pd.read_csv(MANIFEST)
    usage = []
    for row in cities.itertuples():
        aoi = gpd.read_file(
            ROOT / "data/cities" / row.city_slug / "inputs/aoi.geojson"
        ).geometry.union_all()
        indexes = grid.sindex.query(aoi, predicate="intersects")
        # Boundary-touching grid polygons are excluded unless intersection has area.
        selected = grid.iloc[indexes]
        selected = selected.loc[
            __import__("shapely").area(
                __import__("shapely").intersection(selected.geometry.to_numpy(), aoi)
            ) > 0
        ]
        for grid_id in selected.grid_ID:
            usage.append({"city_slug": row.city_slug, "grid_ID": int(grid_id)})
    usage = pd.DataFrame(usage).merge(files, on="grid_ID", how="left", validate="many_to_one")
    if usage.filename.isna().any():
        raise ValueError("Some required grid IDs have no file URL")
    usage.to_csv(ROOT / "data/cities/globfp_city_tile_usage.csv", index=False)
    unique = usage.drop_duplicates("grid_ID").sort_values("grid_ID").copy()
    unique["local_zip"] = unique.apply(
        lambda r: str(RAW / "portfolio_tiles" / r.filename), axis=1
    )
    unique["local_directory"] = unique.apply(
        lambda r: str(RAW / "portfolio_tiles" / f"tile_{int(r.grid_ID)}"), axis=1
    )
    unique.to_csv(ROOT / "data/cities/globfp_download_manifest.csv", index=False)
    summary = {
        "unique_tiles": int(len(unique)),
        "city_tile_uses": int(len(usage)),
        "download_gb": float(unique.bytes.sum() / 1e9),
        "maximum_tiles_per_city": int(usage.groupby("city_slug").size().max()),
    }
    (ROOT / "data/cities/globfp_tile_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
