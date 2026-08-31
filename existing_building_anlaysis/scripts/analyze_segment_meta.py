#!/usr/bin/env python3
"""Combine footprint-review and WSF gap signals by segment across all cities."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/mpl_segment_meta")

import geopandas as gpd
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import rasterize
from scipy.stats import spearmanr


matplotlib.use("Agg")
ROOT = Path(__file__).resolve().parents[1]
CITIES = ROOT / "data/cities"
CITY_OUTPUTS = ROOT / "outputs/cities"
OUT = ROOT / "outputs/meta_analysis"
CELL_AREA_M2 = 30.0 * 30.0
MIN_BUILDINGS_FOR_RATE = 10


def grouped_review_metrics(path: Path) -> pd.DataFrame:
    columns = [
        "ANALYSIS_ID", "review_required", "geometry_confidence",
        "height_source_count", "height_range_m", "geometry_source",
    ]
    data = pd.read_parquet(path, columns=columns)
    data = data.loc[data.ANALYSIS_ID.notna()].copy()
    data["geometry_review"] = data.geometry_confidence.eq("low")
    data["height_review"] = (
        data.height_source_count.fillna(0).ge(2)
        & data.height_range_m.fillna(-np.inf).gt(5)
    )
    data["both_review"] = data.geometry_review & data.height_review
    data["review_osm"] = data.review_required & data.geometry_source.eq("OpenStreetMap")
    data["review_other_overture"] = (
        data.review_required & data.geometry_source.eq("Overture_nonOSM")
    )
    data["review_globfp_gapfill"] = (
        data.review_required & data.geometry_source.eq("3D-GloBFP_gapfill")
    )
    result = data.groupby("ANALYSIS_ID", observed=True).agg(
        building_count=("review_required", "size"),
        review_required_count=("review_required", "sum"),
        geometry_review_count=("geometry_review", "sum"),
        height_review_count=("height_review", "sum"),
        geometry_and_height_review_count=("both_review", "sum"),
        review_osm_count=("review_osm", "sum"),
        review_other_overture_count=("review_other_overture", "sum"),
        review_globfp_gapfill_count=("review_globfp_gapfill", "sum"),
    ).reset_index()
    return result


def wsf_zonal_metrics(segments: gpd.GeoDataFrame, path: Path) -> pd.DataFrame:
    with rasterio.open(path) as src:
        segments = segments.to_crs(src.crs)
        labels = rasterize(
            ((geom, i + 1) for i, geom in enumerate(segments.geometry)),
            out_shape=(src.height, src.width), transform=src.transform,
            fill=0, dtype="int32", all_touched=False,
        )
        wsf_fraction = src.read(6).astype("float64")
        wsf_present = src.read(7)
        any_footprint = src.read(8)
        wsf_gap = src.read(9)
        nodata = src.nodata

    valid = (labels > 0) & np.isfinite(wsf_fraction)
    if nodata is not None:
        valid &= wsf_fraction != nodata
    ids = labels[valid]
    n = len(segments) + 1
    cell_count = np.bincount(ids, minlength=n)
    impervious = np.clip(wsf_fraction[valid], 0, 1)
    wsf_area = np.bincount(ids, weights=impervious * CELL_AREA_M2, minlength=n)
    settled = wsf_present[valid] >= 0.5
    gap = wsf_gap[valid] >= 0.5
    footprint = any_footprint[valid] >= 0.5
    settled_cells = np.bincount(ids, weights=settled, minlength=n)
    gap_cells = np.bincount(ids, weights=gap, minlength=n)
    footprint_cells = np.bincount(ids, weights=footprint, minlength=n)
    gap_impervious = np.bincount(
        ids, weights=np.where(gap, impervious * CELL_AREA_M2, 0), minlength=n
    )
    return pd.DataFrame({
        "ANALYSIS_ID": segments.ANALYSIS_ID.to_numpy(),
        "raster_center_cells": cell_count[1:].astype("int64"),
        "wsf_settled_cells": settled_cells[1:].astype("int64"),
        "footprint_present_cells": footprint_cells[1:].astype("int64"),
        "wsf_gap_cells": gap_cells[1:].astype("int64"),
        "wsf_impervious_area_m2": wsf_area[1:],
        "wsf_gap_impervious_area_m2": gap_impervious[1:],
        "wsf_gap_support_area_m2": gap_cells[1:] * CELL_AREA_M2,
    })


def percentile(series: pd.Series, eligible: pd.Series) -> pd.Series:
    result = pd.Series(np.nan, index=series.index, dtype="float64")
    if eligible.any():
        result.loc[eligible] = series.loc[eligible].rank(method="average", pct=True)
    return result


def threshold(series: pd.Series, eligible: pd.Series, statistic: str) -> float:
    values = series.loc[eligible & series.notna()]
    if values.empty:
        return float("nan")
    return float(getattr(values, statistic)())


def add_city_flags(data: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, dict]:
    building = data.building_count.gt(0)
    rate_eligible = data.building_count.ge(MIN_BUILDINGS_FOR_RATE)
    raster = data.raster_center_cells.gt(0)
    wsf = data.wsf_impervious_area_m2.gt(0)

    data["review_required_pct"] = np.where(
        building, 100 * data.review_required_count / data.building_count, np.nan
    )
    data["geometry_review_pct"] = np.where(
        building, 100 * data.geometry_review_count / data.building_count, np.nan
    )
    data["height_review_pct"] = np.where(
        building, 100 * data.height_review_count / data.building_count, np.nan
    )
    data["review_rate_eligible"] = rate_eligible
    data["wsf_gap_impervious_ha"] = data.wsf_gap_impervious_area_m2 / 10_000
    data["wsf_gap_support_ha"] = data.wsf_gap_support_area_m2 / 10_000
    data["wsf_impervious_ha"] = data.wsf_impervious_area_m2 / 10_000
    data["wsf_gap_share_pct"] = np.where(
        wsf, 100 * data.wsf_gap_impervious_area_m2 / data.wsf_impervious_area_m2, np.nan
    )
    data["wsf_gap_segment_area_pct"] = np.where(
        data.analysis_area_ha.gt(0),
        100 * data.wsf_gap_support_ha / data.analysis_area_ha,
        np.nan,
    )

    definitions = {
        "review_count": ("review_required_count", building),
        "review_pct": ("review_required_pct", rate_eligible),
        "gap_ha": ("wsf_gap_impervious_ha", raster),
        "gap_share_pct": ("wsf_gap_share_pct", wsf),
    }
    stats = {}
    for label, (column, eligible) in definitions.items():
        mean = threshold(data[column], eligible, "mean")
        median = threshold(data[column], eligible, "median")
        q75 = float(data.loc[eligible, column].quantile(.75)) if eligible.any() else np.nan
        stats[f"{label}_mean"] = mean
        stats[f"{label}_median"] = median
        stats[f"{label}_q75"] = q75
        data[f"{label}_gt_city_mean"] = eligible & data[column].gt(mean)
        data[f"{label}_gt_city_median"] = eligible & data[column].gt(median)

    data["review_above_city_mean"] = (
        data.review_count_gt_city_mean | data.review_pct_gt_city_mean
    )
    data["review_above_city_median"] = (
        data.review_count_gt_city_median | data.review_pct_gt_city_median
    )
    data["missing_above_city_mean"] = (
        data.gap_ha_gt_city_mean | data.gap_share_pct_gt_city_mean
    )
    data["missing_above_city_median"] = (
        data.gap_ha_gt_city_median | data.gap_share_pct_gt_city_median
    )
    data["dual_above_city_mean"] = (
        data.review_above_city_mean & data.missing_above_city_mean
    )
    data["dual_above_city_median"] = (
        data.review_above_city_median & data.missing_above_city_median
    )

    data["review_count_city_percentile"] = percentile(
        data.review_required_count, building
    )
    data["review_pct_city_percentile"] = percentile(
        data.review_required_pct, rate_eligible
    )
    data["gap_area_city_percentile"] = percentile(
        data.wsf_gap_impervious_ha, raster
    )
    data["gap_share_city_percentile"] = percentile(
        data.wsf_gap_share_pct, wsf
    )
    data["review_signal_index"] = data[[
        "review_count_city_percentile", "review_pct_city_percentile"
    ]].mean(axis=1, skipna=True)
    data["missing_signal_index"] = data[[
        "gap_area_city_percentile", "gap_share_city_percentile"
    ]].mean(axis=1, skipna=True)
    data["dual_signal_score"] = np.sqrt(
        data.review_signal_index.fillna(0) * data.missing_signal_index.fillna(0)
    )

    no_buildings = data.building_count.eq(0) & data.gap_ha_gt_city_median
    critical = data.review_signal_index.ge(.90) & data.missing_signal_index.ge(.90)
    high = data.review_signal_index.ge(.75) & data.missing_signal_index.ge(.75)
    elevated = data.review_signal_index.ge(.50) & data.missing_signal_index.ge(.50)
    missing_only = data.missing_signal_index.ge(.75) & data.review_signal_index.lt(.50)
    review_only = data.review_signal_index.ge(.75) & data.missing_signal_index.lt(.50)
    data["meta_priority_class"] = np.select(
        [no_buildings, critical, high, elevated, missing_only, review_only],
        ["missing_no_footprints", "critical_dual_q90", "high_dual_q75",
         "elevated_dual", "missing_gap_only", "review_only"],
        default="background",
    )
    return data, stats


def city_summary(data: gpd.GeoDataFrame, meta: dict, stats: dict, wsf_complete: bool) -> dict:
    buildings = int(data.building_count.sum())
    reviewed = int(data.review_required_count.sum())
    eligible = data.review_rate_eligible & data.wsf_impervious_area_m2.gt(0)
    corr_n = int(eligible.sum())
    corr = np.nan
    pvalue = np.nan
    if corr_n >= 10:
        x = data.loc[eligible, "review_required_pct"]
        y = data.loc[eligible, "wsf_gap_share_pct"]
        if x.nunique() > 1 and y.nunique() > 1:
            corr, pvalue = spearmanr(x, y, nan_policy="omit")
    result = {
        "city_slug": meta["city_slug"], "city_name": meta["city_name"],
        "country": meta["country"], "segments": int(len(data)),
        "segments_with_buildings": int(data.building_count.gt(0).sum()),
        "review_rate_eligible_segments": int(data.review_rate_eligible.sum()),
        "buildings": buildings, "review_required_buildings": reviewed,
        "review_required_pct": 100 * reviewed / buildings if buildings else np.nan,
        "geometry_review_buildings": int(data.geometry_review_count.sum()),
        "height_review_buildings": int(data.height_review_count.sum()),
        "geometry_and_height_review_buildings": int(data.geometry_and_height_review_count.sum()),
        "wsf_impervious_area_km2": data.wsf_impervious_area_m2.sum() / 1e6,
        "wsf_gap_impervious_area_km2": data.wsf_gap_impervious_area_m2.sum() / 1e6,
        "wsf_gap_share_pct": (
            100 * data.wsf_gap_impervious_area_m2.sum() / data.wsf_impervious_area_m2.sum()
            if data.wsf_impervious_area_m2.sum() else np.nan
        ),
        "dual_above_city_mean_segments": int(data.dual_above_city_mean.sum()),
        "dual_above_city_median_segments": int(data.dual_above_city_median.sum()),
        "critical_dual_q90_segments": int(data.meta_priority_class.eq("critical_dual_q90").sum()),
        "high_dual_q75_segments": int(data.meta_priority_class.isin(["critical_dual_q90", "high_dual_q75"]).sum()),
        "missing_no_footprints_segments": int(data.meta_priority_class.eq("missing_no_footprints").sum()),
        "review_gap_spearman": corr, "review_gap_spearman_p": pvalue,
        "review_gap_correlation_n": corr_n, "wsf2019_complete": bool(wsf_complete),
    }
    denom = max(1, result["segments_with_buildings"])
    result["high_dual_segments_pct"] = 100 * result["high_dual_q75_segments"] / denom
    result.update(stats)
    return result


def make_plots(cities: pd.DataFrame) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(15, 12))
    sizes = 20 + 150 * np.sqrt(cities.buildings / max(1, cities.buildings.max()))
    axes[0, 0].scatter(cities.review_required_pct, cities.wsf_gap_share_pct,
                       s=sizes, alpha=.72, color="#3973ac", edgecolor="white", linewidth=.5)
    rank = cities.review_required_pct.rank(pct=True) + cities.wsf_gap_share_pct.rank(pct=True)
    for row in cities.loc[rank.nlargest(12).index].itertuples():
        axes[0, 0].annotate(row.city_name.replace("_", " "),
                            (row.review_required_pct, row.wsf_gap_share_pct), fontsize=8)
    axes[0, 0].set_xlabel("Buildings requiring review (%)")
    axes[0, 0].set_ylabel("WSF impervious area without footprints (%)")
    axes[0, 0].set_title("City-level relationship between the two signals")

    top = cities.nlargest(20, "high_dual_segments_pct").sort_values("high_dual_segments_pct")
    axes[0, 1].barh(top.city_name.str.replace("_", " "), top.high_dual_segments_pct,
                    color="#d8654f")
    axes[0, 1].set_xlabel("High/critical dual-signal segments (% of built segments)")
    axes[0, 1].set_title("Cities with concentrated dual-signal hotspots")

    parts = cities.assign(
        geometry_only=lambda x: x.geometry_review_buildings - x.geometry_and_height_review_buildings,
        height_only=lambda x: x.height_review_buildings - x.geometry_and_height_review_buildings,
    ).nlargest(20, "review_required_pct").sort_values("review_required_pct")
    denom = parts.buildings.replace(0, np.nan)
    left = np.zeros(len(parts))
    for values, label, color in [
        (100 * parts.geometry_only / denom, "Geometry only", "#3973ac"),
        (100 * parts.height_only / denom, "Height disagreement only", "#5a9f68"),
        (100 * parts.geometry_and_height_review_buildings / denom, "Both", "#d8654f"),
    ]:
        axes[1, 0].barh(parts.city_name.str.replace("_", " "), values, left=left,
                        label=label, color=color)
        left += values.fillna(0).to_numpy()
    axes[1, 0].set_xlabel("Buildings requiring review (%)")
    axes[1, 0].set_title("Composition of review flags")
    axes[1, 0].legend(frameon=False, fontsize=8)

    valid = cities.loc[cities.review_gap_spearman.notna()].copy()
    axes[1, 1].hist(valid.review_gap_spearman, bins=np.linspace(-1, 1, 21),
                    color="#8064a2", edgecolor="white")
    axes[1, 1].axvline(0, color="#444444", linewidth=1)
    axes[1, 1].set_xlabel("Within-city Spearman correlation")
    axes[1, 1].set_ylabel("Cities")
    axes[1, 1].set_title("Do review rates and WSF gaps co-locate?")
    fig.suptitle("Segment meta-analysis across 93 cities", fontsize=16)
    fig.tight_layout()
    fig.savefig(OUT / "cross_city_patterns.png", dpi=180, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = pd.read_csv(CITIES / "city_manifest.csv")
    portfolio = pd.read_csv(CITY_OUTPUTS / "portfolio_summary.csv").set_index("city_slug")
    city_rows = []
    threshold_rows = []
    indexes = []
    priority_geometries = []
    for position, row in enumerate(manifest.itertuples(index=False), start=1):
        slug = row.city_slug
        print(f"[{position:02d}/93] {slug}", flush=True)
        analysis_path = CITY_OUTPUTS / slug / "analysis/segment_analysis.parquet"
        footprint_path = CITY_OUTPUTS / slug / "integrated/best_available_footprints.parquet"
        raster_path = CITY_OUTPUTS / slug / "analysis/comparison_30m.tif"
        segments = gpd.read_parquet(analysis_path)
        base_columns = [
            "ANALYSIS_ID", "SEGMENT_UID", "analysis_area_ha", "geometry",
        ]
        for optional in ["GRID_ID", "ID_SEG", "UC_NM_MN", "CANONICAL_CITY"]:
            if optional in segments:
                base_columns.insert(-1, optional)
        segments = segments[base_columns].copy()
        reviews = grouped_review_metrics(footprint_path)
        wsf = wsf_zonal_metrics(segments, raster_path)
        data = segments.merge(reviews, on="ANALYSIS_ID", how="left").merge(
            wsf, on="ANALYSIS_ID", how="left"
        )
        integer_columns = [
            "building_count", "review_required_count", "geometry_review_count",
            "height_review_count", "geometry_and_height_review_count", "review_osm_count",
            "review_other_overture_count", "review_globfp_gapfill_count",
            "raster_center_cells", "wsf_settled_cells", "footprint_present_cells",
            "wsf_gap_cells",
        ]
        data[integer_columns] = data[integer_columns].fillna(0).astype("int64")
        for column in ["wsf_impervious_area_m2", "wsf_gap_impervious_area_m2",
                       "wsf_gap_support_area_m2"]:
            data[column] = data[column].fillna(0.0)
        data, stats = add_city_flags(data)
        data.insert(0, "city_slug", slug)
        data.insert(1, "city_name", row.city_name)
        data.insert(2, "country", row.country)
        output = CITY_OUTPUTS / slug / "analysis"
        data.to_parquet(output / "segment_meta_analysis.parquet", index=False)
        data.to_file(output / "segment_meta_analysis.gpkg", layer="segment_meta", driver="GPKG")

        meta = {"city_slug": slug, "city_name": row.city_name, "country": row.country}
        complete = bool(portfolio.loc[slug, "wsf2019_complete"])
        city_rows.append(city_summary(data, meta, stats, complete))
        threshold_rows.append({"city_slug": slug, **stats})
        index_columns = [c for c in data.columns if c != "geometry"]
        indexes.append(pd.DataFrame(data[index_columns]))
        priority = data.loc[data.meta_priority_class.ne("background")].copy()
        if len(priority):
            priority_geometries.append(priority)

    city_table = pd.DataFrame(city_rows).sort_values(["country", "city_name"])
    city_table["review_city_percentile"] = city_table.review_required_pct.rank(pct=True)
    city_table["gap_city_percentile"] = city_table.wsf_gap_share_pct.rank(pct=True)
    city_table["dual_hotspot_city_percentile"] = city_table.high_dual_segments_pct.rank(pct=True)
    city_table["city_pattern"] = np.select(
        [
            city_table.review_city_percentile.ge(.75) & city_table.gap_city_percentile.ge(.75),
            city_table.review_city_percentile.ge(.75) & city_table.gap_city_percentile.lt(.50),
            city_table.gap_city_percentile.ge(.75) & city_table.review_city_percentile.lt(.50),
            city_table.dual_hotspot_city_percentile.ge(.75),
        ],
        ["high_review_and_gap", "review_dominant", "gap_dominant",
         "locally_concentrated_dual_hotspots"],
        default="mixed_or_moderate",
    )
    city_table["review_gap_spearman_q"] = np.nan
    valid_p = city_table.review_gap_spearman_p.notna()
    if valid_p.any():
        pvalues = city_table.loc[valid_p, "review_gap_spearman_p"].to_numpy()
        order = np.argsort(pvalues)
        ranked = pvalues[order] * len(pvalues) / np.arange(1, len(pvalues) + 1)
        ranked = np.minimum.accumulate(ranked[::-1])[::-1].clip(0, 1)
        adjusted = np.empty_like(ranked)
        adjusted[order] = ranked
        city_table.loc[valid_p, "review_gap_spearman_q"] = adjusted
    city_table["review_gap_correlation_fdr05"] = city_table.review_gap_spearman_q.lt(.05)
    city_table.to_csv(OUT / "city_meta_summary.csv", index=False)
    pd.DataFrame(threshold_rows).to_csv(OUT / "city_thresholds.csv", index=False)

    country = city_table.groupby("country", observed=True).agg(
        cities=("city_slug", "size"), buildings=("buildings", "sum"),
        review_required_buildings=("review_required_buildings", "sum"),
        wsf_impervious_area_km2=("wsf_impervious_area_km2", "sum"),
        wsf_gap_impervious_area_km2=("wsf_gap_impervious_area_km2", "sum"),
        high_dual_segments=("high_dual_q75_segments", "sum"),
        built_segments=("segments_with_buildings", "sum"),
    ).reset_index()
    country["review_required_pct"] = (
        100 * country.review_required_buildings / country.buildings
    )
    country["wsf_gap_share_pct"] = (
        100 * country.wsf_gap_impervious_area_km2 / country.wsf_impervious_area_km2
    )
    country["high_dual_segments_pct"] = (
        100 * country.high_dual_segments / country.built_segments
    )
    country.sort_values("country").to_csv(OUT / "country_meta_summary.csv", index=False)

    segment_index = pd.concat(indexes, ignore_index=True)
    for label, column, eligible in [
        ("review_count", "review_required_count", segment_index.building_count.gt(0)),
        ("review_pct", "review_required_pct", segment_index.review_rate_eligible),
        ("gap_area", "wsf_gap_impervious_ha", segment_index.raster_center_cells.gt(0)),
        ("gap_share", "wsf_gap_share_pct", segment_index.wsf_impervious_area_m2.gt(0)),
    ]:
        mean = segment_index.loc[eligible, column].mean()
        median = segment_index.loc[eligible, column].median()
        segment_index[f"{label}_gt_portfolio_mean"] = eligible & segment_index[column].gt(mean)
        segment_index[f"{label}_gt_portfolio_median"] = eligible & segment_index[column].gt(median)
    segment_index.to_parquet(OUT / "all_segments_meta_index.parquet", index=False)

    if priority_geometries:
        # City files use different projected CRSs; normalize the combined review layer.
        pieces = [part.to_crs(4326) for part in priority_geometries]
        priority = gpd.GeoDataFrame(pd.concat(pieces, ignore_index=True), geometry="geometry", crs=4326)
        priority.to_parquet(OUT / "priority_segments.parquet", index=False)

    make_plots(city_table)
    significant = city_table.loc[city_table.review_gap_correlation_fdr05]
    report = {
        "cities": int(len(city_table)),
        "segments": int(len(segment_index)),
        "buildings": int(city_table.buildings.sum()),
        "review_required_buildings": int(city_table.review_required_buildings.sum()),
        "review_required_pct": float(
            100 * city_table.review_required_buildings.sum() / city_table.buildings.sum()
        ),
        "wsf_gap_impervious_area_km2": float(city_table.wsf_gap_impervious_area_km2.sum()),
        "high_or_critical_dual_segments": int(city_table.high_dual_q75_segments.sum()),
        "missing_no_footprints_segments": int(city_table.missing_no_footprints_segments.sum()),
        "cities_with_positive_review_gap_correlation": int(
            (significant.review_gap_spearman > 0).sum()
        ),
        "cities_with_negative_review_gap_correlation": int(
            (significant.review_gap_spearman < 0).sum()
        ),
        "minimum_buildings_for_review_rate": MIN_BUILDINGS_FOR_RATE,
        "wsf_area_method": "30 m cell-center assignment; WSF fractional impervious area summed within gap cells",
    }
    (OUT / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    readme = f"""# Segment meta-analysis

This analysis combines two signals for {len(segment_index):,} segment polygons in 93 cities:

1. Integrated footprints for which `review_required` is true.
2. WSF2019 fractional impervious area in 30 m cells where none of the three footprint sources is present.

## Segment measures

Each city's `analysis/segment_meta_analysis.parquet` and `.gpkg` contains building totals, review counts and rates, geometry- and height-review components, WSF impervious area, WSF gap area, city-relative thresholds, percentile indexes, and a priority class. Review-rate comparisons require at least {MIN_BUILDINGS_FOR_RATE} buildings per segment; count comparisons remain available for smaller segments.

`review_above_city_mean` and `review_above_city_median` are true when either the review count or eligible review proportion exceeds the corresponding within-city reference. The equivalent `missing_...` flags use WSF gap area or WSF gap share. The `dual_...` flags require both signals.

Priority classes use within-city percentiles: `critical_dual_q90` requires both signal indexes at or above the 90th percentile; `high_dual_q75` requires both at or above the 75th; `elevated_dual` requires both at or above the median. `missing_no_footprints` identifies above-median WSF gap area in segments with no selected footprint.

## Area and interpretation

WSF area is estimated by assigning 30 m cell centers to segments and summing WSF fractional impervious area within cells flagged as WSF settlement without footprint evidence. Segment boundaries therefore have approximately 30 m positional granularity. This is a prioritization screen, not proof that buildings are missing: roads, paved compounds, temporal differences, and source errors can also create WSF-only impervious signal.

Cape Town retains the known unavailable ocean-edge WSF tile; `wsf2019_complete` flags this in the city summary. WSF3D is not used in this meta-analysis. Within-city Spearman correlation p-values are also reported with Benjamini-Hochberg false-discovery-rate adjustment across the 93 cities.

## Portfolio files

- `city_meta_summary.csv`: city totals, rates, correlations, and pattern classes.
- `country_meta_summary.csv`: weighted country aggregates for the represented cities.
- `city_thresholds.csv`: the exact mean, median, and 75th-percentile references used by each city.
- `all_segments_meta_index.parquet`: non-spatial combined index for all segments.
- `priority_segments.parquet`: spatial EPSG:4326 layer of all non-background priority segments.
- `cross_city_patterns.png`: cross-city diagnostic figure.
- `summary.json`: portfolio totals and method constants.
"""
    (OUT / "README.md").write_text(readme)
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
