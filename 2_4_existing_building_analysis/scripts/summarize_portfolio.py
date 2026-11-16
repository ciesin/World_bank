#!/usr/bin/env python3
"""Validate and summarize the city portfolio outputs."""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/mpl_portfolio_summary")

import matplotlib
import matplotlib.pyplot as plt
import pandas as pd
import rasterio


matplotlib.use("Agg")
ROOT = Path(__file__).resolve().parents[1]
CITIES = ROOT / "data/cities"
OUTPUTS = ROOT / "outputs/cities"
WSF = ROOT / "data/raw/portfolio/wsf2019"


def valid_raster(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 1024:
        return False
    try:
        with rasterio.open(path) as src:
            return src.width > 0 and src.height > 0 and src.count > 0
    except Exception:
        return False


def main() -> None:
    manifest = pd.read_csv(CITIES / "city_manifest.csv")
    usage = pd.read_csv(CITIES / "wsf2019_city_usage.csv")
    rows = []
    validation = []
    for city in manifest.itertuples(index=False):
        base = OUTPUTS / city.city_slug
        vector_path = base / "integrated/summary.json"
        raster_path = base / "analysis/raster_summary.json"
        vector = json.loads(vector_path.read_text()) if vector_path.exists() else {}
        raster = json.loads(raster_path.read_text()) if raster_path.exists() else {}
        height_summary_path = base / "analysis/height_source_summary.csv"
        sensitivity_path = base / "analysis/height_sensitivity.csv"
        height_summary = pd.read_csv(height_summary_path) if height_summary_path.exists() else pd.DataFrame()
        sensitivity = pd.read_csv(sensitivity_path) if sensitivity_path.exists() else pd.DataFrame()
        gba_row = height_summary.loc[height_summary.source.eq("GBA.Height")] if not height_summary.empty else pd.DataFrame()
        excluded = sensitivity.loc[sensitivity.scenario.eq("GBA excluded")] if not sensitivity.empty else pd.DataFrame()
        included = sensitivity.loc[sensitivity.scenario.eq("GBA included")] if not sensitivity.empty else pd.DataFrame()
        counts = vector.get("geometry_source_counts", {})
        total = int(vector.get("integrated_buildings", 0))
        required = usage.loc[usage.city_slug.eq(city.city_slug), "filename"].tolist()
        valid_wsf = sum(valid_raster(WSF / name) for name in required)
        wsf_complete = bool(required) and valid_wsf == len(required)
        expected = {
            "integrated_footprints": base / "integrated/best_available_footprints.parquet",
            "lineage": base / "integrated/lineage.parquet",
            "vector_segments": base / "analysis/segment_vector_summary.gpkg",
            "comparison_30m": base / "analysis/comparison_30m.tif",
            "height_100m": base / "analysis/height_comparison_100m.tif",
            "segment_analysis": base / "analysis/segment_analysis.gpkg",
            "overview": base / "analysis/overview.png",
        }
        missing = [name for name, path in expected.items() if not path.exists()]
        rows.append({
            "city_slug": city.city_slug,
            "city_name": city.city_name,
            "country": city.country,
            "segments": int(city.total_segments),
            "aoi_area_km2": float(city.source_area_km2),
            "integrated_buildings": total,
            "osm_buildings": int(counts.get("OpenStreetMap", 0)),
            "overture_non_osm_buildings": int(counts.get("Overture_nonOSM", 0)),
            "globfp_gapfill_buildings": int(counts.get("3D-GloBFP_gapfill", 0)),
            "osm_share_pct": 100 * counts.get("OpenStreetMap", 0) / total if total else 0,
            "height_available_buildings": int(raster.get("height_available_buildings", 0)),
            "height_available_pct": float(raster.get("height_available_pct", 0)),
            "gba_height_tiles": int(raster.get("gba_height_tiles", 0)),
            "gba_height_available_100m_cells": int(raster.get("gba_height_available_100m_cells", 0)),
            "gba_height_coverage_pct_of_aoi": float(gba_row.coverage_pct_of_aoi.iloc[0]) if len(gba_row) else 0,
            "gba_height_median_m": float(gba_row.median_height_m.iloc[0]) if len(gba_row) else float("nan"),
            "height_range_median_gba_excluded": float(excluded.median_inter_product_range_m.iloc[0]) if len(excluded) else float("nan"),
            "height_range_median_gba_included": float(included.median_inter_product_range_m.iloc[0]) if len(included) else float("nan"),
            "consensus_cells_30m": int(raster.get("consensus_cells", 0)),
            "wsf_settlement_cells_30m": int(raster.get("wsf_settlement_cells", 0)),
            "wsf_no_footprint_cells_30m": int(raster.get("wsf_no_footprint_cells", 0)),
            "wsf_no_footprint_settled_area_km2": float(raster.get("wsf_no_footprint_settled_area_km2", 0)),
            "wsf2019_tiles_required": len(required),
            "wsf2019_tiles_valid": valid_wsf,
            "wsf2019_complete": wsf_complete,
            "wsf3d_portfolio_available": False,
            "vector_complete": vector_path.exists(),
            "raster_complete": raster_path.exists(),
            "gba_height_complete": (base / "analysis/height_sensitivity.csv").exists(),
            "missing_outputs": ";".join(missing),
        })
        validation.append({
            "city_slug": city.city_slug,
            "missing_outputs": missing,
            "invalid_geometries": vector.get("invalid_geometries"),
            "unassigned_segments": vector.get("unassigned_segments"),
            "geometry_count_matches": sum(counts.values()) == total if vector else False,
            "wsf2019_complete": wsf_complete,
        })

    summary = pd.DataFrame(rows).sort_values(["country", "city_name"])
    summary.to_csv(OUTPUTS / "portfolio_summary.csv", index=False)
    (OUTPUTS / "portfolio_summary.json").write_text(
        json.dumps(summary.to_dict(orient="records"), indent=2) + "\n"
    )
    (OUTPUTS / "portfolio_validation.json").write_text(
        json.dumps(validation, indent=2) + "\n"
    )

    fig, axes = plt.subplots(1, 3, figsize=(16, 7))
    top = summary.nlargest(20, "integrated_buildings").sort_values("integrated_buildings")
    axes[0].barh(top.city_name, top.integrated_buildings / 1e6, color="#3973ac")
    axes[0].set_title("Largest integrated datasets")
    axes[0].set_xlabel("million buildings")
    axes[1].hist(summary.height_available_pct, bins=20, color="#5a9f68", edgecolor="white")
    axes[1].set_title("Height availability across cities")
    axes[1].set_xlabel("integrated buildings with height (%)")
    gaps = summary.nlargest(20, "wsf_no_footprint_settled_area_km2").sort_values(
        "wsf_no_footprint_settled_area_km2"
    )
    colors = ["#d8654f" if ok else "#aaaaaa" for ok in gaps.wsf2019_complete]
    axes[2].barh(gaps.city_name, gaps.wsf_no_footprint_settled_area_km2, color=colors)
    axes[2].set_title("Largest WSF-screened footprint gaps")
    axes[2].set_xlabel("settled area without footprint evidence (km²)")
    fig.suptitle("Building-footprint portfolio: 93-city processing summary", fontsize=15)
    fig.tight_layout()
    fig.savefig(OUTPUTS / "portfolio_overview.png", dpi=180, bbox_inches="tight")
    plt.close(fig)

    complete = int(summary.raster_complete.sum())
    wsf_complete = int(summary.wsf2019_complete.sum())
    readme = f"""# 93-city building-footprint portfolio

This directory contains one folder per city. Each city folder is split into:

- `analysis/`: 30 m footprint/WSF comparison, 100 m height comparison, segment-level tables, source summary, and overview figure.
- `integrated/`: best-available footprint geometries, lineage, summary, and selection overview.

## Selection method

Geometry priority is OpenStreetMap-derived Overture, then other Overture geometries, then non-duplicating 3D-GloBFP gap-fill geometries. Candidate 3D-GloBFP features are suppressed when they substantially overlap or closely duplicate a higher-priority footprint. Every selected record retains source and confidence lineage.

Height priority is native geometry height, OSM levels converted at 3 m per level, 3D-GloBFP vector height, Google 2.5D at 30 m, optional WSF3D at 100 m, then TEMPO at 100 m. GBA.Height is retained as an additional modeled comparison product rather than changing that priority. It is excluded from independent-source counts because it shares PlanetScope imagery with TEMPO and its footprint/model lineage overlaps existing products.

## Raster bands

`comparison_30m.tif`: Overture fraction; Google 2.5D fraction; 3D-GloBFP fraction; source agreement count; consensus; WSF2019 settlement fraction; WSF presence; any footprint presence; WSF settlement with no footprints; Google height; GBA.Height; GBA.Height valid-building fraction; 3D-GloBFP height.

`height_comparison_100m.tif`: TEMPO, Google 2.5D, GBA.Height, 3D-GloBFP, and WSF3D heights; GBA valid-building fraction; GBA-excluded count/range; and GBA-included count/range. GBA values are restricted to the available 3D-GloBFP proxy footprint mask at 5 m and building-area weighted to 30 m and 100 m. This differs from Juba, where the official GBA footprint layer is locally available.

## Completion and caveats

- Raster-complete cities: {complete}/93.
- WSF2019 source-tile complete cities: {wsf_complete}/93. Grey bars in the WSF-gap panel indicate incomplete WSF2019 source coverage; do not compare their gap totals as complete-city estimates.
- Portfolio WSF3D retrieval was unavailable during this run. The WSF3D band is retained as NoData for consistency; other height sources remain populated.
- GBA.Height is CC BY-NC 4.0 and cannot be used commercially without further permission. The portfolio comparison has no independent reference-height dataset; all agreement measures are inter-product consistency, not accuracy.
- OSM provenance/version metadata is used as a preference and review signal, not proof that every footprint was manually reviewed.
- 3D-GloBFP is used as the GBA-family geometry input in this reduced portfolio workflow to avoid double-counting strongly correlated geometries.

See `portfolio_summary.csv` for the cross-city index and `portfolio_validation.json` for per-city checks.
"""
    (OUTPUTS / "README.md").write_text(readme)
    print(f"Cities: {len(summary)}; raster complete: {complete}; WSF complete: {wsf_complete}")


if __name__ == "__main__":
    main()
