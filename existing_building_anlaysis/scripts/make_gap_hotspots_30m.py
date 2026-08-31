#!/usr/bin/env python3
"""Group contiguous 30 m source-gap cells into reviewable hotspots."""

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from scipy.ndimage import label


ROOT = Path(__file__).resolve().parents[1]
GRID_PATH = ROOT / "outputs/juba_30m_comparison_grid.parquet"
FIELDS = {
    "Overture": "overture_gap",
    "Google 2.5D": "google25d_gap",
    "GlobalBuildingAtlas": "gba_gap",
    "3D-GloBFP": "globfp3d_gap",
}
MIN_CELLS = 10  # At least 0.009 km2 before accounting for partial AOI cells.


def main():
    grid = gpd.read_parquet(GRID_PATH)
    height = int(grid.row.max()) + 1
    width = int(grid.col.max()) + 1
    structure = np.ones((3, 3), dtype="uint8")
    records, geometries = [], []
    rows = grid.row.to_numpy(dtype=int)
    cols = grid.col.to_numpy(dtype=int)

    for source, field in FIELDS.items():
        mask = np.zeros((height, width), dtype=bool)
        mask[rows, cols] = grid[field].to_numpy().astype(bool)
        clusters, cluster_count = label(mask, structure=structure)
        cell_labels = clusters[rows, cols]
        for cluster_id in range(1, cluster_count + 1):
            indexes = np.flatnonzero(cell_labels == cluster_id)
            if len(indexes) < MIN_CELLS:
                continue
            subset = grid.iloc[indexes]
            geometries.append(shapely.union_all(subset.geometry.to_numpy()))
            records.append({
                "source": source,
                "cluster_id": int(cluster_id),
                "cell_count": int(len(indexes)),
                "area_km2": float((subset.aoi_fraction * 0.0009).sum()),
                "mean_source_count": float(subset.source_count.mean()),
                "max_gap_count": int(subset.gap_count.max()),
            })

    hotspots = gpd.GeoDataFrame(records, geometry=geometries, crs=grid.crs)
    hotspots["rank"] = hotspots.groupby("source")["area_km2"].rank(
        method="first", ascending=False
    ).astype(int)
    hotspots = hotspots.sort_values(["source", "rank"])
    hotspots.to_file(ROOT / "outputs/juba_30m_gap_hotspots.gpkg", layer="gap_hotspots_30m", driver="GPKG")
    centroids = gpd.GeoSeries(hotspots.geometry.centroid, crs=hotspots.crs).to_crs(4326)
    table = pd.DataFrame(hotspots.drop(columns="geometry"))
    table["centroid_lon"] = centroids.x.to_numpy()
    table["centroid_lat"] = centroids.y.to_numpy()
    table[table["rank"] <= 20].to_csv(ROOT / "outputs/juba_30m_gap_hotspots_top20.csv", index=False)
    print(table.groupby("source").agg(hotspots=("cluster_id", "count"), largest_km2=("area_km2", "max")))


if __name__ == "__main__":
    main()
