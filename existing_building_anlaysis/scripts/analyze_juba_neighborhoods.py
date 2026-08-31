#!/usr/bin/env python3
"""Aggregate Juba footprint and height consistency metrics to neighborhood segments."""

from __future__ import annotations

import argparse
import json
from itertools import combinations
from pathlib import Path

import geopandas as gpd
import matplotlib
import numpy as np
import pandas as pd
import rasterio
import shapely
from matplotlib import pyplot as plt
from rasterio.transform import xy
from scipy.stats import spearmanr


matplotlib.use("Agg")

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEGMENTS = ROOT / "data/processed/juba_segments_20260821.gpkg"
AOI_PATH = ROOT / "data/aoi/juba_expanded.geojson"
GRID30_PATH = ROOT / "outputs/juba_30m_comparison_grid.parquet"
HEIGHT100_PATH = ROOT / "outputs/juba_100m_height_comparison.tif"
CRS = "EPSG:32636"

FOOTPRINT_FIELDS = {
    "Overture": ("overture_fraction", "overture_present", "overture_gap"),
    "Google 2.5D": ("google25d_fraction", "google25d_present", "google25d_gap"),
    "GlobalBuildingAtlas": ("gba_fraction", "gba_present", "gba_gap"),
    "3D-GloBFP": ("globfp3d_fraction", "globfp3d_present", "globfp3d_gap"),
}
HEIGHT_BANDS = {
    "TEMPO": ("TEMPO mean height m", "TEMPO built volume m3"),
    "Google 2.5D": ("Google 2.5D mean height m", "Google 2.5D built volume m3"),
    "GBA.Height": ("GBA.Height mean height m", "GBA.Height built volume m3"),
    "3D-GloBFP": ("3D-GloBFP mean height m", "3D-GloBFP built volume m3"),
    "WSF 3D v2": ("WSF 3D v2 mean height m", "WSF 3D v2 built volume m3"),
}
SLUG = {
    "Overture": "overture",
    "Google 2.5D": "google25d",
    "GlobalBuildingAtlas": "gba",
    "GBA.Height": "gba_height",
    "3D-GloBFP": "globfp3d",
    "TEMPO": "tempo",
    "WSF 3D v2": "wsf3d",
}


def weighted_mean(values, weights):
    valid = np.isfinite(values) & np.isfinite(weights) & (weights > 0)
    if not valid.any():
        return np.nan
    return float(np.sum(values[valid] * weights[valid]) / np.sum(weights[valid]))


def weighted_quantile(values, weights, quantile):
    valid = np.isfinite(values) & np.isfinite(weights) & (weights > 0)
    if not valid.any():
        return np.nan
    values, weights = values[valid], weights[valid]
    order = np.argsort(values)
    values, weights = values[order], weights[order]
    cumulative = np.cumsum(weights) - 0.5 * weights
    cumulative /= np.sum(weights)
    return float(np.interp(quantile, cumulative, values))


def weighted_spearman(a, b, weights):
    valid = np.isfinite(a) & np.isfinite(b) & np.isfinite(weights) & (weights > 0)
    if valid.sum() < 3 or np.ptp(a[valid]) == 0 or np.ptp(b[valid]) == 0:
        return np.nan
    # scipy has no weighted Spearman; repeat-free cell rank correlation is retained
    # and the exact overlap-weighted sample size is reported alongside it.
    return float(spearmanr(a[valid], b[valid]).statistic)


def intersect_weights(grid, geometry):
    indexes = np.asarray(grid.sindex.query(geometry, predicate="intersects"), dtype=int)
    if len(indexes) == 0:
        return indexes, np.array([], dtype="float64")
    intersections = shapely.intersection(grid.geometry.to_numpy()[indexes], geometry)
    areas = shapely.area(intersections).astype("float64")
    keep = areas > 1e-6
    return indexes[keep], areas[keep]


def load_segments(path):
    aoi = gpd.read_file(AOI_PATH).to_crs(CRS).geometry.union_all()
    source = gpd.read_file(path, layer="juba_segments").to_crs(CRS)
    source = source[source.geometry.intersects(aoi)].copy()
    source.geometry = source.geometry.intersection(aoi)
    source = source[~source.geometry.is_empty & source.geometry.notna()].copy()
    if "ANALYSIS_ID" not in source.columns or "GRID_ID" not in source.columns:
        raise ValueError("Prepared input must contain ANALYSIS_ID and GRID_ID")
    if source.ANALYSIS_ID.isna().any() or source.ANALYSIS_ID.duplicated().any():
        raise ValueError("ANALYSIS_ID must be non-null and unique")
    if source.GRID_ID.isna().any() or source.GRID_ID.duplicated().any():
        raise ValueError("GRID_ID must be non-null and unique")
    # Retain the existing numerical analysis machinery while reporting GRID_ID.
    source["ID_SEG"] = source.ANALYSIS_ID.astype(int)
    source = source.sort_values("ID_SEG").reset_index(drop=True)
    source["analysis_area_ha"] = source.geometry.area / 10_000
    return source, aoi


def wsf2019_analysis(segments, grid):
    rows = []
    fraction_all = grid.wsf2019_settlement_fraction.to_numpy(dtype="float64")
    present_all = grid.wsf2019_settlement_present.to_numpy(dtype=bool)
    no_fp_all = grid.wsf2019_no_footprint.to_numpy(dtype=bool)
    for segment in segments.itertuples():
        indexes, overlap = intersect_weights(grid, segment.geometry)
        segment_area = float(segment.geometry.area)
        fraction = fraction_all[indexes]
        present = present_all[indexes]
        no_fp = no_fp_all[indexes]
        valid_fraction = np.where(np.isfinite(fraction), fraction, 0)
        settlement_area = float(np.sum(valid_fraction * overlap))
        no_fp_settlement_area = float(np.sum(np.where(no_fp, valid_fraction * overlap, 0)))
        no_fp_support = float(np.sum(overlap[no_fp]))
        rows.append({
            "ID_SEG": segment.ID_SEG,
            "GRID_ID": segment.GRID_ID,
            "segment_area_ha": segment_area / 10_000,
            "wsf2019_settlement_area_ha": settlement_area / 10_000,
            "wsf2019_settlement_coverage_pct": 100 * settlement_area / segment_area,
            "wsf2019_no_footprint_support_ha": no_fp_support / 10_000,
            "wsf2019_no_footprint_settled_area_ha": no_fp_settlement_area / 10_000,
            "wsf2019_no_footprint_pct_of_settlement": (
                100 * no_fp_settlement_area / settlement_area if settlement_area > 0 else np.nan
            ),
            "wsf2019_no_footprint_cells": int(no_fp.sum()),
        })
    return pd.DataFrame(rows)


def make_100m_grid(src):
    rows, cols = np.meshgrid(np.arange(src.height), np.arange(src.width), indexing="ij")
    left, top = xy(src.transform, rows.ravel(), cols.ravel(), offset="ul")
    right = np.asarray(left) + src.transform.a
    bottom = np.asarray(top) + src.transform.e
    cells = shapely.box(np.asarray(left), np.asarray(bottom), right, np.asarray(top))
    return gpd.GeoDataFrame(
        {"row": rows.ravel(), "col": cols.ravel()}, geometry=cells, crs=src.crs
    )


def footprint_analysis(segments, grid):
    rows, pairs = [], []
    fraction_matrix = np.vstack(
        [grid[fields[0]].to_numpy(dtype="float64") for fields in FOOTPRINT_FIELDS.values()]
    )
    reference_fraction = np.nanmedian(fraction_matrix, axis=0)
    consensus_all = grid.consensus.to_numpy(dtype=bool)

    for segment in segments.itertuples():
        indexes, overlap = intersect_weights(grid, segment.geometry)
        segment_area = float(segment.geometry.area)
        consensus = consensus_all[indexes]
        consensus_support = float(np.sum(overlap[consensus]))
        source_arrays = {}

        for source, (fraction_field, present_field, gap_field) in FOOTPRINT_FIELDS.items():
            fraction = grid[fraction_field].to_numpy(dtype="float64")[indexes]
            present = grid[present_field].to_numpy(dtype=bool)[indexes]
            gap = grid[gap_field].to_numpy(dtype=bool)[indexes]
            built_area = float(np.nansum(fraction * overlap))
            covered_consensus = float(np.sum(overlap[consensus & present]))
            deficit = np.maximum(reference_fraction[indexes] - fraction, 0)
            deficit_area = float(np.nansum(np.where(consensus, deficit * overlap, 0)))
            source_arrays[source] = (fraction, present)
            rows.append(
                {
                    "ID_SEG": segment.ID_SEG,
                    "source": source,
                    "neighborhood_area_ha": segment_area / 10_000,
                    "intersecting_30m_cells": int(len(indexes)),
                    "consensus_support_area_ha": consensus_support / 10_000,
                    "estimated_built_area_ha": built_area / 10_000,
                    "building_coverage_pct": 100 * built_area / segment_area,
                    "positive_support_area_ha": float(np.sum(overlap[present])) / 10_000,
                    "consensus_completeness_pct": 100 * covered_consensus / consensus_support
                    if consensus_support > 0
                    else np.nan,
                    "gap_support_area_ha": float(np.sum(overlap[gap])) / 10_000,
                    "estimated_footprint_deficit_ha": deficit_area / 10_000,
                }
            )

        for left, right in combinations(FOOTPRINT_FIELDS, 2):
            left_fraction, left_present = source_arrays[left]
            right_fraction, right_present = source_arrays[right]
            union = left_present | right_present
            intersection = left_present & right_present
            union_weight = float(np.sum(overlap[union]))
            common_fraction = np.isfinite(left_fraction) & np.isfinite(right_fraction)
            pairs.append(
                {
                    "ID_SEG": segment.ID_SEG,
                    "source_a": left,
                    "source_b": right,
                    "intersecting_30m_cells": int(len(indexes)),
                    "weighted_jaccard_presence": float(np.sum(overlap[intersection])) / union_weight
                    if union_weight > 0
                    else np.nan,
                    "weighted_fraction_mae": weighted_mean(
                        np.abs(left_fraction - right_fraction), overlap * common_fraction
                    ),
                    "spearman_fraction": weighted_spearman(left_fraction, right_fraction, overlap),
                }
            )

    summary = pd.DataFrame(rows)
    summary["completeness_rank"] = summary.groupby("ID_SEG")[
        "consensus_completeness_pct"
    ].rank(method="min", ascending=False)
    return summary, pd.DataFrame(pairs)


def height_analysis(segments, grid100, heights, volumes):
    rows, pairs = [], []
    cell_area = 10_000.0
    for segment in segments.itertuples():
        indexes, overlap = intersect_weights(grid100, segment.geometry)
        segment_area = float(segment.geometry.area)
        overlap_fraction = overlap / cell_area
        source_arrays = {}

        for source in HEIGHT_BANDS:
            height = heights[source][indexes]
            volume = volumes[source][indexes]
            inferred_built_area = np.divide(
                volume,
                height,
                out=np.full_like(volume, np.nan),
                where=np.isfinite(height) & (height > 0),
            )
            valid = (
                np.isfinite(height)
                & (height > 0)
                & (height <= 100)
                & np.isfinite(inferred_built_area)
                & (inferred_built_area >= 50)
            )
            built_weights = np.where(valid, inferred_built_area * overlap_fraction, 0)
            allocated_volume = np.where(valid, volume * overlap_fraction, 0)
            mean_height = weighted_mean(height, built_weights)
            source_arrays[source] = (height, valid, inferred_built_area)
            rows.append(
                {
                    "ID_SEG": segment.ID_SEG,
                    "source": source,
                    "neighborhood_area_ha": segment_area / 10_000,
                    "intersecting_100m_cells": int(len(indexes)),
                    "valid_height_cells": int(valid.sum()),
                    "valid_height_support_pct": 100 * float(np.sum(overlap[valid])) / segment_area,
                    "height_weighted_mean_m": mean_height,
                    "height_weighted_median_m": weighted_quantile(height, built_weights, 0.5),
                    "height_weighted_p90_m": weighted_quantile(height, built_weights, 0.9),
                    "inferred_built_area_ha": float(np.sum(built_weights)) / 10_000,
                    "estimated_built_volume_m3": float(np.sum(allocated_volume)),
                }
            )

        for left, right in combinations(HEIGHT_BANDS, 2):
            left_height, left_valid, left_built = source_arrays[left]
            right_height, right_valid, right_built = source_arrays[right]
            common = left_valid & right_valid
            weights = overlap * common
            difference = left_height - right_height
            left_class = np.digitize(left_height, [3.0, 6.0, 10.0])
            right_class = np.digitize(right_height, [3.0, 6.0, 10.0])
            pairs.append(
                {
                    "ID_SEG": segment.ID_SEG,
                    "grid": "100m",
                    "source_a": left,
                    "source_b": right,
                    "common_valid_cells": int(common.sum()),
                    "common_support_area_ha": float(np.sum(overlap[common])) / 10_000,
                    "weighted_bias_a_minus_b_m": weighted_mean(difference, weights),
                    "weighted_mae_m": weighted_mean(np.abs(difference), weights),
                    "weighted_rmse_m": np.sqrt(weighted_mean(difference**2, weights))
                    if common.any()
                    else np.nan,
                    "spearman_height": weighted_spearman(left_height, right_height, weights),
                    "same_height_class_pct": 100
                    * weighted_mean((left_class == right_class).astype(float), weights),
                }
            )
    return pd.DataFrame(rows), pd.DataFrame(pairs)


def high_res_height_pair(segments, grid30):
    rows = []
    sources = {
        "Google 2.5D": (
            grid30.google_height_m.to_numpy(dtype="float64"),
            grid30.google25d_fraction.to_numpy(dtype="float64"),
        ),
        "GBA.Height": (
            grid30.gba_height_m.to_numpy(dtype="float64"),
            grid30.gba_height_valid_fraction.to_numpy(dtype="float64"),
        ),
        "3D-GloBFP": (
            grid30.globfp_height_m.to_numpy(dtype="float64"),
            grid30.globfp3d_fraction.to_numpy(dtype="float64"),
        ),
    }
    for segment in segments.itertuples():
        indexes, overlap = intersect_weights(grid30, segment.geometry)
        for left, right in combinations(sources, 2):
            left_h, left_f = (array[indexes] for array in sources[left])
            right_h, right_f = (array[indexes] for array in sources[right])
            valid_left = np.isfinite(left_h) & (left_h > 0) & (left_h <= 100) & (left_f * 900 >= 50)
            valid_right = np.isfinite(right_h) & (right_h > 0) & (right_h <= 100) & (right_f * 900 >= 50)
            common = valid_left & valid_right
            weights = overlap * common
            difference = left_h - right_h
            rows.append(
                {
                    "ID_SEG": segment.ID_SEG,
                    "grid": "30m",
                    "source_a": left,
                    "source_b": right,
                    "common_valid_cells": int(common.sum()),
                    "common_support_area_ha": float(np.sum(overlap[common])) / 10_000,
                    "weighted_bias_a_minus_b_m": weighted_mean(difference, weights),
                    "weighted_mae_m": weighted_mean(np.abs(difference), weights),
                    "weighted_rmse_m": np.sqrt(weighted_mean(difference**2, weights)) if common.any() else np.nan,
                    "spearman_height": weighted_spearman(left_h, right_h, weights),
                    "same_height_class_pct": 100 * weighted_mean(
                        (np.digitize(left_h, [3, 6, 10]) == np.digitize(right_h, [3, 6, 10])).astype(float),
                        weights,
                    ),
                }
            )
    return pd.DataFrame(rows)


def make_overview(segments, footprint, height, wsf):
    overview = pd.DataFrame(segments[["ID_SEG", "GRID_ID", "SOURCE_ID_SEG", "analysis_area_ha"]].copy())
    for source in FOOTPRINT_FIELDS:
        subset = footprint[footprint.source == source].set_index("ID_SEG")
        slug = SLUG[source]
        overview[f"fp_cov_{slug}_pct"] = overview.ID_SEG.map(subset.building_coverage_pct)
        overview[f"fp_complete_{slug}_pct"] = overview.ID_SEG.map(
            subset.consensus_completeness_pct
        )
        overview[f"fp_gap_{slug}_ha"] = overview.ID_SEG.map(subset.gap_support_area_ha)
    for source in HEIGHT_BANDS:
        subset = height[height.source == source].set_index("ID_SEG")
        slug = SLUG[source]
        overview[f"height_{slug}_m"] = overview.ID_SEG.map(subset.height_weighted_mean_m)
        overview[f"height_valid_{slug}_pct"] = overview.ID_SEG.map(
            subset.valid_height_support_pct
        )

    fp_cols = [f"fp_cov_{SLUG[source]}_pct" for source in FOOTPRINT_FIELDS]
    height_cols = [f"height_{SLUG[source]}_m" for source in HEIGHT_BANDS]
    overview["footprint_coverage_range_pp"] = overview[fp_cols].max(axis=1) - overview[
        fp_cols
    ].min(axis=1)
    overview["height_mean_range_m"] = overview[height_cols].max(axis=1) - overview[
        height_cols
    ].min(axis=1)
    legacy_height_cols = [f"height_{SLUG[source]}_m" for source in HEIGHT_BANDS if source != "GBA.Height"]
    overview["height_mean_range_gba_excluded_m"] = (
        overview[legacy_height_cols].max(axis=1) - overview[legacy_height_cols].min(axis=1)
    )
    overview["height_mean_range_gba_included_m"] = overview["height_mean_range_m"]
    overview["height_range_change_with_gba_m"] = (
        overview.height_mean_range_gba_included_m - overview.height_mean_range_gba_excluded_m
    )
    valid_completeness = footprint.dropna(subset=["consensus_completeness_pct"])
    best = valid_completeness.loc[
        valid_completeness.groupby("ID_SEG").consensus_completeness_pct.idxmax(),
        ["ID_SEG", "source"],
    ].set_index("ID_SEG")
    lowest = valid_completeness.loc[
        valid_completeness.groupby("ID_SEG").consensus_completeness_pct.idxmin(),
        ["ID_SEG", "source"],
    ].set_index("ID_SEG")
    overview["most_complete_source"] = overview.ID_SEG.map(best.source)
    overview["least_complete_source"] = overview.ID_SEG.map(lowest.source)
    wsf_lookup = wsf.set_index("ID_SEG")
    for column in (
        "wsf2019_settlement_area_ha",
        "wsf2019_settlement_coverage_pct",
        "wsf2019_no_footprint_support_ha",
        "wsf2019_no_footprint_settled_area_ha",
        "wsf2019_no_footprint_pct_of_settlement",
    ):
        overview[column] = overview.ID_SEG.map(wsf_lookup[column])
    return overview


def draw_context(ax, context, extent, bounds):
    ax.imshow(context, extent=extent, origin="upper", cmap="Greys", vmin=0, vmax=0.25, alpha=0.45)
    pad_x = (bounds[2] - bounds[0]) * 0.04
    pad_y = (bounds[3] - bounds[1]) * 0.04
    ax.set_xlim(bounds[0] - pad_x, bounds[2] + pad_x)
    ax.set_ylim(bounds[1] - pad_y, bounds[3] + pad_y)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])


def plot_source_maps(path, mapped, columns, titles, context, extent, label, vmin, vmax, cmap):
    ncols = 3 if len(columns) > 4 else 2
    nrows = int(np.ceil(len(columns) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(14, 4.5 * nrows), constrained_layout=True)
    axes = np.atleast_1d(axes)
    image = None
    for ax, column, title in zip(axes.ravel(), columns, titles):
        draw_context(ax, context, extent, mapped.total_bounds)
        mapped.plot(column=column, ax=ax, cmap=cmap, vmin=vmin, vmax=vmax, edgecolor="0.25", linewidth=0.25)
        image = ax.collections[-1]
        ax.set_title(title)
    for ax in axes.ravel()[len(columns):]:
        ax.set_visible(False)
    fig.colorbar(image, ax=axes, label=label, shrink=0.8)
    fig.savefig(path, dpi=190, bbox_inches="tight")
    plt.close(fig)


def plot_disagreement(path, mapped, context, extent):
    fig, axes = plt.subplots(1, 3, figsize=(16, 5), constrained_layout=True)
    specs = [
        ("footprint_coverage_range_pp", "Footprint coverage range", "Range (percentage points)", "magma"),
        ("height_mean_range_gba_excluded_m", "Mean-height range (GBA excluded)", "Range (m)", "viridis"),
        ("height_mean_range_gba_included_m", "Mean-height range (GBA included)", "Range (m)", "viridis"),
    ]
    for ax, (column, title, label, cmap) in zip(axes, specs):
        draw_context(ax, context, extent, mapped.total_bounds)
        mapped.plot(column=column, ax=ax, cmap=cmap, edgecolor="0.25", linewidth=0.25, legend=True,
                    legend_kwds={"label": label, "shrink": 0.75})
        ax.set_title(title)
        top = mapped.nlargest(10, column)
        points = top.geometry.representative_point()
        for point, identifier in zip(points, top.GRID_ID):
            ax.text(point.x, point.y, str(identifier), fontsize=6, ha="center", va="center")
    fig.savefig(path, dpi=190, bbox_inches="tight")
    plt.close(fig)


def plot_wsf_segments(path, mapped, context, extent):
    fig, axes = plt.subplots(1, 3, figsize=(15, 5), constrained_layout=True)
    specs = [
        ("wsf2019_settlement_coverage_pct", "WSF settlement coverage", "Coverage (%)", "Greys"),
        ("wsf2019_no_footprint_settled_area_ha", "WSF settled area, no footprints", "Area (ha)", "Reds"),
        ("wsf2019_no_footprint_pct_of_settlement", "Share of WSF settlement uncovered", "Share (%)", "magma"),
    ]
    for ax, (column, title, label, cmap) in zip(axes, specs):
        draw_context(ax, context, extent, mapped.total_bounds)
        mapped.plot(column=column, ax=ax, cmap=cmap, edgecolor="0.35", linewidth=0.12,
                    legend=True, legend_kwds={"label": label, "shrink": 0.72})
        ax.set_title(title)
    fig.savefig(path, dpi=190, bbox_inches="tight")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--segments", type=Path, default=DEFAULT_SEGMENTS)
    args = parser.parse_args()
    out = ROOT / "outputs"
    out.mkdir(exist_ok=True)

    segments, aoi = load_segments(args.segments)
    print(f"Juba neighborhoods: {len(segments):,}", flush=True)
    grid30 = gpd.read_parquet(GRID30_PATH).to_crs(CRS)
    footprint, footprint_pairs = footprint_analysis(segments, grid30)
    wsf = wsf2019_analysis(segments, grid30)

    with rasterio.open(HEIGHT100_PATH) as src:
        grid100 = make_100m_grid(src).to_crs(CRS)
        descriptions = {description: index for index, description in enumerate(src.descriptions, 1)}
        heights = {
            source: src.read(descriptions[height_description], masked=True).filled(np.nan).ravel().astype("float64")
            for source, (height_description, _) in HEIGHT_BANDS.items()
        }
        volumes = {
            source: src.read(descriptions[volume_description], masked=True).filled(np.nan).ravel().astype("float64")
            for source, (_, volume_description) in HEIGHT_BANDS.items()
        }
    height, height_pairs100 = height_analysis(segments, grid100, heights, volumes)
    height_pairs30 = high_res_height_pair(segments, grid30)
    height_pairs = pd.concat([height_pairs100, height_pairs30], ignore_index=True)
    overview = make_overview(segments, footprint, height, wsf)

    lookup = segments[["ID_SEG", "GRID_ID"]]
    footprint = footprint.merge(lookup, on="ID_SEG", how="left")
    footprint_pairs = footprint_pairs.merge(lookup, on="ID_SEG", how="left")
    height = height.merge(lookup, on="ID_SEG", how="left")
    height_pairs = height_pairs.merge(lookup, on="ID_SEG", how="left")

    footprint.to_csv(out / "juba_neighborhood_footprint_summary.csv", index=False)
    footprint_pairs.to_csv(out / "juba_neighborhood_footprint_pairwise.csv", index=False)
    height.to_csv(out / "juba_neighborhood_height_summary.csv", index=False)
    height_pairs.to_csv(out / "juba_neighborhood_height_pairwise.csv", index=False)
    overview.to_csv(out / "juba_neighborhood_overview.csv", index=False)
    wsf.to_csv(out / "juba_segment_wsf2019_summary.csv", index=False)
    wsf.nlargest(100, "wsf2019_no_footprint_settled_area_ha").to_csv(
        out / "juba_segment_wsf2019_gap_hotspots_top100.csv", index=False
    )

    mapped = segments.merge(
        overview.drop(columns=["analysis_area_ha", "GRID_ID", "SOURCE_ID_SEG"]),
        on="ID_SEG",
        how="left",
    )
    mapped.to_file(out / "juba_neighborhood_consistency.gpkg", layer="neighborhood_consistency", driver="GPKG")
    mapped.to_parquet(out / "juba_neighborhood_consistency.parquet", index=False)
    mapped.nlargest(30, "footprint_coverage_range_pp").drop(columns="geometry").to_csv(
        out / "juba_neighborhood_top_footprint_disagreements.csv", index=False
    )
    mapped.nlargest(30, "height_mean_range_gba_included_m").drop(columns="geometry").to_csv(
        out / "juba_neighborhood_top_height_disagreements.csv", index=False
    )
    mapped.nlargest(30, "height_mean_range_gba_excluded_m").drop(columns="geometry").to_csv(
        out / "juba_neighborhood_top_height_disagreements_gba_excluded.csv", index=False
    )

    with rasterio.open(ROOT / "outputs/juba_30m_comparison.tif") as src:
        context_stack = np.stack(
            [src.read(i, masked=True).filled(np.nan) for i in range(1, 5)]
        )
        context_count = np.isfinite(context_stack).sum(axis=0)
        context = np.divide(
            np.nansum(context_stack, axis=0),
            context_count,
            out=np.full(context_count.shape, np.nan, dtype="float64"),
            where=context_count > 0,
        )
        extent = (src.bounds.left, src.bounds.right, src.bounds.bottom, src.bounds.top)
    fp_columns = [f"fp_complete_{SLUG[s]}_pct" for s in FOOTPRINT_FIELDS]
    plot_source_maps(
        out / "juba_neighborhood_footprint_completeness.png", mapped, fp_columns,
        list(FOOTPRINT_FIELDS), context, extent, "Consensus completeness (%)", 50, 100, "viridis"
    )
    height_columns = [f"height_{SLUG[s]}_m" for s in HEIGHT_BANDS]
    plot_source_maps(
        out / "juba_neighborhood_mean_heights.png", mapped, height_columns,
        list(HEIGHT_BANDS), context, extent, "Building-area-weighted mean height (m)", 1.5, 6, "magma"
    )
    plot_disagreement(out / "juba_neighborhood_disagreement.png", mapped, context, extent)
    plot_wsf_segments(out / "juba_segment_wsf2019_settlement_gaps.png", mapped, context, extent)

    metadata = {
        "input": str(args.segments),
        "reporting_id_field": "GRID_ID",
        "internal_id_field": "ANALYSIS_ID (aliased to ID_SEG within the analysis code)",
        "juba_features": int(len(segments)),
        "analyzed_area_km2": float(segments.geometry.area.sum() / 1e6),
        "selection": "All Juba features from segments_hexbin_20260821.gpkg; analysis extent is their union.",
        "footprint_grid_m": 30,
        "footprint_aggregation": "Cell values weighted by exact neighborhood-cell intersection area.",
        "height_grid_m": 100,
        "height_aggregation": "Mean/quantiles weighted by inferred building area and exact neighborhood-cell overlap.",
        "height_validity": "Positive height <=100 m and at least 50 m2 inferred building area in a full 100 m cell.",
        "high_resolution_height_pairs": "Google 2.5D, GBA.Height, and 3D-GloBFP pairwise at 30 m, requiring at least 50 m2 source building area per cell.",
        "gba_height_dependency": "GBA.Height excluded from independent-source counts because it shares PlanetScope imagery with TEMPO and its LoD1 footprint lineage overlaps Google, Microsoft, and OSM-derived sources.",
        "height_sensitivity": "Neighborhood height ranges are reported with GBA excluded and included.",
        "wsf2019_aggregation": "Settlement fraction and no-footprint screen weighted by exact segment-cell intersection area.",
        "interpretation": "Consistency and relative completeness, not validation against independent ground truth.",
    }
    (out / "juba_neighborhood_analysis_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(json.dumps(metadata, indent=2), flush=True)
    print("\nFootprint overall medians:", flush=True)
    print(footprint.groupby("source")[["building_coverage_pct", "consensus_completeness_pct"]].median().to_string(), flush=True)
    print("\nHeight overall medians:", flush=True)
    print(height.groupby("source")[["height_weighted_mean_m", "valid_height_support_pct"]].median().to_string(), flush=True)


if __name__ == "__main__":
    main()
