#!/usr/bin/env python3
"""Create enriched WSF hotspot outputs and a compact expanded-run manifest."""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"


def records(frame, columns):
    clean = frame[columns].replace({np.nan: None})
    return clean.to_dict(orient="records")


def main():
    segments = gpd.read_parquet(ROOT / "data/processed/juba_segments_20260821.parquet")
    wsf = pd.read_csv(OUT / "juba_segment_wsf2019_summary.csv")
    lookup = segments[["ANALYSIS_ID", "GRID_ID", "SOURCE_ID_SEG", "geometry"]].rename(
        columns={"ANALYSIS_ID": "ID_SEG"}
    )
    enriched = wsf.drop(columns=["GRID_ID", "SOURCE_ID_SEG", "unit_type"], errors="ignore").merge(
        lookup.drop(columns="geometry"), on="ID_SEG", how="left"
    )
    enriched["unit_type"] = np.where(
        enriched.SOURCE_ID_SEG.notna(), "legacy segment", "new hex"
    )
    first = ["ID_SEG", "GRID_ID", "SOURCE_ID_SEG", "unit_type"]
    enriched = enriched[first + [c for c in enriched.columns if c not in first]]
    enriched.to_csv(OUT / "juba_segment_wsf2019_summary.csv", index=False)

    ranked = enriched.sort_values(
        "wsf2019_no_footprint_settled_area_ha", ascending=False
    )
    ranked.head(100).to_csv(
        OUT / "juba_segment_wsf2019_gap_hotspots_top100.csv", index=False
    )
    ranked[ranked.unit_type == "new hex"].head(100).to_csv(
        OUT / "juba_hex_wsf2019_gap_hotspots_top100.csv", index=False
    )
    ranked[ranked.unit_type == "legacy segment"].head(100).to_csv(
        OUT / "juba_legacy_segment_wsf2019_gap_hotspots_top100.csv", index=False
    )

    hotspot_rows = enriched[enriched.wsf2019_no_footprint_settled_area_ha > 0]
    hotspots = lookup.merge(hotspot_rows.drop(columns=["GRID_ID", "SOURCE_ID_SEG"]), on="ID_SEG")
    hotspots["unit_type"] = np.where(
        hotspots.SOURCE_ID_SEG.notna(), "legacy segment", "new hex"
    )
    hotspots.to_file(
        OUT / "juba_segment_wsf2019_gap_hotspots.gpkg",
        layer="wsf2019_no_footprint",
        driver="GPKG",
    )

    raster_meta = json.loads((OUT / "juba_30m_height_analysis_metadata.json").read_text())
    source30 = pd.read_csv(OUT / "juba_30m_source_summary.csv")
    height = pd.read_csv(OUT / "juba_height_source_summary.csv")
    height_sensitivity = pd.read_csv(OUT / "juba_height_sensitivity.csv")
    geometry = pd.read_csv(OUT / "juba_geometry_overall_summary.csv")
    positive_hotspots = enriched[enriched.wsf2019_no_footprint_settled_area_ha > 0]
    positive_hexes = positive_hotspots[positive_hotspots.unit_type == "new hex"]
    manifest = {
        "analysis": "Expanded Juba building consistency and WSF 2019 settlement-gap screen",
        "segment_source": "/Users/ggy1/Downloads/segments_hexbin_20260821.gpkg",
        "reporting_id": "GRID_ID",
        "aoi_area_km2": raster_meta["aoi_area_km2"],
        "segment_count": int(len(enriched)),
        "new_hex_count": int((enriched.unit_type == "new hex").sum()),
        "legacy_segment_count": int((enriched.unit_type == "legacy segment").sum()),
        "wsf2019_method": {
            "product": "World Settlement Footprint 2019",
            "native_resolution_m": 10,
            "analysis_resolution_m": 30,
            "positive_threshold_fraction": 0.10,
            "consensus_role": "Independent screen; excluded from footprint consensus",
            "no_footprint_rule": "WSF-positive cell and zero positive footprint sources at the 25 m2 threshold",
        },
        "wsf2019_results": {
            "settlement_cells": raster_meta["wsf2019_settlement_cells"],
            "settlement_area_km2": raster_meta["wsf2019_settlement_area_km2"],
            "no_footprint_cells": raster_meta["wsf2019_no_footprint_cells"],
            "no_footprint_settled_area_km2": raster_meta["wsf2019_no_footprint_settled_area_km2"],
            "no_footprint_pct_of_settlement": 100
            * raster_meta["wsf2019_no_footprint_settled_area_km2"]
            / raster_meta["wsf2019_settlement_area_km2"],
            "segments_with_no_footprint_settlement": int(len(positive_hotspots)),
            "new_hexes_with_no_footprint_settlement": int(len(positive_hexes)),
        },
        "footprint_30m": records(
            source30,
            ["source", "positive_30m_cells", "consensus_recall_proxy_pct", "estimated_built_area_km2"],
        ),
        "height_100m": records(
            height[height.grid == "100m"],
            ["source", "height_coverage_of_aoi_pct", "median_height_m", "p90_height_m"],
        ),
        "height_sensitivity_100m": records(
            height_sensitivity,
            ["scenario", "comparable_100m_cells", "median_inter_product_range_m", "p90_inter_product_range_m"],
        ),
        "gba_height_dependency": raster_meta["gba_dependency"],
        "height_interpretation": "Inter-product consistency only; no independent reference-height dataset.",
        "geometry": records(
            geometry,
            ["source_a", "source_b", "median_neighborhood_union_iou", "median_one_to_one_iou"],
        ),
        "key_outputs": [
            "juba_30m_comparison.tif",
            "juba_100m_comparison.tif",
            "juba_100m_height_comparison.tif",
            "juba_height_sensitivity.csv",
            "juba_height_hotspots_top100.gpkg",
            "juba_neighborhood_consistency.gpkg",
            "juba_geometry_neighborhood_summary.gpkg",
            "juba_segment_wsf2019_gap_hotspots.gpkg",
            "juba_30m_wsf2019_settlement_gaps.png",
            "juba_segment_wsf2019_settlement_gaps.png",
        ],
    }
    (OUT / "juba_expanded_run_summary.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest["wsf2019_results"], indent=2), flush=True)


if __name__ == "__main__":
    main()
