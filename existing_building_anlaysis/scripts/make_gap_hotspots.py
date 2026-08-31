#!/usr/bin/env python3
"""Group contiguous 100 m source-gap cells into reviewable hotspots."""

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from scipy.ndimage import label


ROOT = Path(__file__).resolve().parents[1]
GRID_PATH = ROOT / "outputs/juba_comparison_grid.gpkg"

FIELDS = {
    "TEMPO": "tempo_gap",
    "Overture": "overture_gap",
    "Google 2.5D": "google25d_gap",
    "GlobalBuildingAtlas": "gba_gap",
    "3D-GloBFP": "globfp3d_gap",
    "WSF 3D v2": "wsf3d_gap",
}


def main():
    grid = gpd.read_file(GRID_PATH, layer="comparison_100m")
    height = int(grid.row.max()) + 1
    width = int(grid.col.max()) + 1
    structure = np.ones((3, 3), dtype="uint8")
    records = []
    geometries = []

    for source, field in FIELDS.items():
        mask = np.zeros((height, width), dtype=bool)
        mask[grid.row.to_numpy(), grid.col.to_numpy()] = grid[field].to_numpy().astype(bool)
        clusters, cluster_count = label(mask, structure=structure)
        cell_labels = clusters[grid.row.to_numpy(), grid.col.to_numpy()]
        for cluster_id in range(1, cluster_count + 1):
            indexes = np.flatnonzero(cell_labels == cluster_id)
            if len(indexes) < 3:
                continue
            subset = grid.iloc[indexes]
            geometry = shapely.union_all(subset.geometry.to_numpy())
            area_km2 = float((subset.aoi_fraction * 0.01).sum())
            records.append(
                {
                    "source": source,
                    "cluster_id": int(cluster_id),
                    "cell_count": int(len(indexes)),
                    "area_km2": area_km2,
                    "mean_family_count": float(subset.family_count.mean()),
                    "max_gap_count": int(subset.gap_count.max()),
                }
            )
            geometries.append(geometry)

    hotspots = gpd.GeoDataFrame(records, geometry=geometries, crs=grid.crs)
    hotspots["rank"] = hotspots.groupby("source")["area_km2"].rank(
        method="first", ascending=False
    ).astype(int)
    hotspots = hotspots.sort_values(["source", "rank"])
    hotspots.to_file(
        ROOT / "outputs/juba_gap_hotspots.gpkg", layer="gap_hotspots", driver="GPKG"
    )
    centroids = gpd.GeoSeries(hotspots.geometry.centroid, crs=hotspots.crs).to_crs(4326)
    table = pd.DataFrame(hotspots.drop(columns="geometry"))
    table["centroid_lon"] = centroids.x.to_numpy()
    table["centroid_lat"] = centroids.y.to_numpy()
    table[table["rank"] <= 20].to_csv(
        ROOT / "outputs/juba_gap_hotspots_top20.csv", index=False
    )
    print(table.groupby("source").agg(hotspots=("cluster_id", "count"), largest_km2=("area_km2", "max")))


if __name__ == "__main__":
    main()
