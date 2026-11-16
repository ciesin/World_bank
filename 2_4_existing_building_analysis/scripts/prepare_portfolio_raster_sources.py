#!/usr/bin/env python3
"""Create shared download/usage manifests for portfolio raster sources."""

from __future__ import annotations

import json
import math
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely
from s2sphere import CellId, LatLng


ROOT = Path(__file__).resolve().parents[1]
CITIES = ROOT / "data/cities"
RAW = ROOT / "data/raw/portfolio"


def google_manifests(cities: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    usage = []
    for row in cities.itertuples():
        samples = [
            ((row.west + row.east) / 2, (row.south + row.north) / 2),
            (row.west, row.south), (row.west, row.north),
            (row.east, row.south), (row.east, row.north),
        ]
        tokens = {
            CellId.from_lat_lng(LatLng.from_degrees(lat, lon)).parent(2).to_token()
            for lon, lat in samples
        }
        for token in tokens:
            name = f"{token}_EPSG_{int(row.analysis_epsg)}_2023_06_30.json"
            usage.append({
                "city_slug": row.city_slug, "token": token,
                "analysis_epsg": int(row.analysis_epsg), "filename": name,
            })
    usage = pd.DataFrame(usage).drop_duplicates()
    unique = usage.drop_duplicates("filename").copy()
    unique["url"] = unique.filename.map(
        lambda n: f"https://storage.googleapis.com/open-buildings-temporal-data/v1/manifests/{n}"
    )
    unique["local_path"] = unique.filename.map(lambda n: str(RAW / "google_manifests" / n))
    return usage, unique


def wsf2019_tiles(cities: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    usage = []
    epsilon = 1e-9
    for row in cities.itertuples():
        x0 = int(math.floor(row.west / 2) * 2)
        x1 = int(math.floor((row.east - epsilon) / 2) * 2)
        y0 = int(math.floor(row.south / 2) * 2)
        y1 = int(math.floor((row.north - epsilon) / 2) * 2)
        for x in range(x0, x1 + 1, 2):
            for y in range(y0, y1 + 1, 2):
                filename = f"WSF2019_v1_{x}_{y}.tif"
                usage.append({"city_slug": row.city_slug, "filename": filename})
    usage = pd.DataFrame(usage).drop_duplicates()
    unique = usage.drop_duplicates("filename").copy()
    unique["url"] = unique.filename.map(
        lambda n: f"https://download.geoservice.dlr.de/WSF2019/files/{n}"
    )
    unique["local_path"] = unique.filename.map(lambda n: str(RAW / "wsf2019" / n))
    return usage, unique


def tempo_tiles(cities: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    index = gpd.read_file(ROOT / "data/raw/tempo_tile_index.gpkg").to_crs(4326)
    usage = []
    for row in cities.itertuples():
        aoi = gpd.read_parquet(CITIES / row.city_slug / "inputs/aoi.parquet").to_crs(4326).geometry.union_all()
        indexes = index.sindex.query(aoi, predicate="intersects")
        selected = index.iloc[indexes]
        selected = selected.loc[
            shapely.area(shapely.intersection(selected.geometry.to_numpy(), aoi)) > 0
        ]
        for item in selected.itertuples():
            usage.append({
                "city_slug": row.city_slug, "filename": item.filename,
                "url": item.data_2023q4,
            })
    usage = pd.DataFrame(usage).drop_duplicates()
    unique = usage.drop_duplicates("filename").copy()
    unique["local_path"] = unique.filename.map(lambda n: str(RAW / "tempo_2023q4" / n))
    return usage, unique


def wsf3d_subsets(cities: pd.DataFrame) -> pd.DataFrame:
    coverages = {
        "fraction": "land__WSF3D_V02_BUILDINGFRACTION",
        "height": "land__WSF3D_V02_BUILDINGHEIGHT",
    }
    rows = []
    for city in cities.itertuples():
        # Small padding ensures the containing native 90 m cell is returned.
        west, south, east, north = city.west - .002, city.south - .002, city.east + .002, city.north + .002
        for variable, coverage in coverages.items():
            url = (
                "https://geoservice.dlr.de/eoc/land/wcs?service=WCS&version=2.0.1"
                f"&request=GetCoverage&coverageId={coverage}"
                f"&subset=Lat({south},{north})&subset=Long({west},{east})&format=image/tiff"
            )
            rows.append({
                "city_slug": city.city_slug, "variable": variable, "url": url,
                "local_path": str(RAW / "wsf3d" / city.city_slug / f"{variable}.tif"),
            })
    return pd.DataFrame(rows)


def write_pair(name, pair) -> None:
    usage, download = pair
    usage.to_csv(CITIES / f"{name}_city_usage.csv", index=False)
    download.to_csv(CITIES / f"{name}_download_manifest.csv", index=False)


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    cities = pd.read_csv(CITIES / "city_manifest.csv")
    google = google_manifests(cities)
    wsf = wsf2019_tiles(cities)
    tempo = tempo_tiles(cities)
    write_pair("google_manifest", google)
    write_pair("wsf2019", wsf)
    write_pair("tempo", tempo)
    wsf3d = wsf3d_subsets(cities)
    wsf3d.to_csv(CITIES / "wsf3d_download_manifest.csv", index=False)
    summary = {
        "google_manifests": int(len(google[1])),
        "wsf2019_tiles": int(len(wsf[1])),
        "tempo_tiles": int(len(tempo[1])),
        "wsf3d_subsets": int(len(wsf3d)),
    }
    (CITIES / "raster_source_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
