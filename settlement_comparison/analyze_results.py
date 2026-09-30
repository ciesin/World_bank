#!/usr/bin/env python3
"""Produce coverage summaries, footprint-size correlations, and geometry IoU matrices."""

from __future__ import annotations

import argparse
import os
from itertools import combinations
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/mpl_city_boundary_comparison")

import geopandas as gpd
import matplotlib
import numpy as np
import pandas as pd
import seaborn as sns
import shapely
from matplotlib import pyplot as plt

from common import PIPELINE, load_config


matplotlib.use("Agg")
PRODUCTS = [
    "ghs_ucdb_2024", "ghs_fua", "grid3_settlements_2024", "africapolis_2020",
    "city_adm_2026", "wsf_tracker_2025", "dynamic_world_2025", "google_2_5d_2023",
]
LABELS = {
    "ghs_ucdb_2024": "GHS-UCDB 2024", "ghs_fua": "GHS-FUA",
    "grid3_settlements_2024": "GRID3 2024", "africapolis_2020": "Africapolis 2020",
    "city_adm_2026": "Admin 2026", "wsf_tracker_2025": "WSF Tracker 2025",
    "dynamic_world_2025": "Dynamic World 2025", "google_2_5d_2023": "Google 2.5D 2023",
}


def groups(data: pd.DataFrame, cutoff: float):
    return {
        "all_cities": data,
        "population_lt_300k": data.loc[data.population_2025 < cutoff],
        "population_ge_300k": data.loc[data.population_2025 >= cutoff],
    }


def save_heatmap(matrix, path, title, vmin=-1, vmax=1, cmap="vlag", fmt=".2f"):
    shown = matrix.rename(index=LABELS, columns=LABELS)
    size = max(7, 0.75 * len(shown) + 2)
    fig, axis = plt.subplots(figsize=(size, size * 0.88))
    sns.heatmap(shown, annot=True, fmt=fmt, cmap=cmap, vmin=vmin, vmax=vmax,
                square=True, linewidths=.4, cbar_kws={"shrink": .75}, ax=axis)
    axis.set_title(title, pad=14)
    fig.tight_layout()
    fig.savefig(path, dpi=220, facecolor="white", bbox_inches="tight")
    plt.close(fig)


def summary_statistics(coverage, cutoff):
    rows = []
    for group, subset in groups(coverage, cutoff).items():
        for product in PRODUCTS:
            values = subset.loc[subset["product"].eq(product), "coverage_pct"].dropna()
            q25 = values.quantile(.25) if len(values) else np.nan
            q75 = values.quantile(.75) if len(values) else np.nan
            rows.append({
                "population_group": group, "product": product, "n_cities": len(values),
                "median_coverage_pct": values.median() if len(values) else np.nan,
                "q25_coverage_pct": q25, "q75_coverage_pct": q75,
                "iqr_coverage_pct": q75 - q25 if len(values) else np.nan,
                "mean_coverage_pct": values.mean() if len(values) else np.nan,
            })
    return pd.DataFrame(rows)


def correlations(coverage, cutoff, output):
    long_rows = []
    for group, subset in groups(coverage, cutoff).items():
        wide = subset.pivot(index="city_id", columns="product", values="coverage_pct").reindex(columns=PRODUCTS)
        for method in ("spearman", "pearson"):
            matrix = wide.corr(method=method, min_periods=3)
            matrix.to_csv(output / f"correlation_{method}_{group}.csv")
            save_heatmap(matrix, output / f"correlation_{method}_{group}.png",
                         f"{method.title()} correlation of buffer coverage: {group.replace('_', ' ')}")
            for left, right in combinations(PRODUCTS, 2):
                valid = wide[[left, right]].dropna()
                long_rows.append({
                    "population_group": group, "method": method,
                    "product_a": left, "product_b": right, "n_common_cities": len(valid),
                    "correlation": valid[left].corr(valid[right], method=method) if len(valid) >= 3 else np.nan,
                })
    pd.DataFrame(long_rows).to_csv(output / "correlation_statistics_long.csv", index=False)


def iou_statistics(footprints, cutoff, output):
    rows = []
    by_city = {city_id: group.set_index("product") for city_id, group in footprints.groupby("city_id")}
    for city_id, group in by_city.items():
        population = float(group.population_2025.iloc[0])
        for left, right in combinations(PRODUCTS, 2):
            if left not in group.index or right not in group.index:
                continue
            a = shapely.make_valid(
                group.loc[left].geometry, method="structure", keep_collapsed=False,
            )
            b = shapely.make_valid(
                group.loc[right].geometry, method="structure", keep_collapsed=False,
            )
            if a is None or b is None or a.is_empty or b.is_empty:
                iou = np.nan
            else:
                intersection = shapely.area(shapely.intersection(a, b))
                union = shapely.area(shapely.union(a, b))
                iou = intersection / union if union > 0 else np.nan
            rows.append({
                "city_id": city_id, "city_name": group.city_name.iloc[0],
                "population_2025": population, "product_a": left, "product_b": right, "iou": iou,
            })
    per_city = pd.DataFrame(rows)
    per_city.to_csv(output / "iou_by_city_and_product_pair.csv", index=False)
    summaries = []
    for label, subset in groups(per_city, cutoff).items():
        for (left, right), pair in subset.groupby(["product_a", "product_b"]):
            values = pair.iou.dropna()
            summaries.append({
                "population_group": label, "product_a": left, "product_b": right,
                "n_common_cities": len(values), "median_iou": values.median(),
                "q25_iou": values.quantile(.25), "q75_iou": values.quantile(.75),
                "iqr_iou": values.quantile(.75) - values.quantile(.25),
            })
    summary = pd.DataFrame(summaries)
    summary.to_csv(output / "iou_summary_long.csv", index=False)
    for label in ("all_cities", "population_lt_300k", "population_ge_300k"):
        matrix = pd.DataFrame(np.eye(len(PRODUCTS)), index=PRODUCTS, columns=PRODUCTS)
        selected = summary.loc[summary.population_group.eq(label)]
        for row in selected.itertuples(index=False):
            matrix.loc[row.product_a, row.product_b] = row.median_iou
            matrix.loc[row.product_b, row.product_a] = row.median_iou
        matrix.to_csv(output / f"iou_median_matrix_{label}.csv")
        save_heatmap(matrix, output / f"iou_median_matrix_{label}.png",
                     f"Median footprint IoU: {label.replace('_', ' ')}", vmin=0, vmax=1, cmap="YlGnBu")


def coverage_plot(coverage, cutoff, output):
    data = coverage.copy()
    data["product_label"] = data["product"].map(LABELS)
    data["population_group"] = np.where(
        data.population_2025 < cutoff, "Population <300k", "Population 300k+"
    )
    fig, axis = plt.subplots(figsize=(11, 5.8))
    sns.boxplot(
        data=data, x="product_label", y="coverage_pct", hue="population_group",
        order=[LABELS[value] for value in PRODUCTS], showfliers=False, ax=axis,
    )
    axis.set_xlabel("")
    axis.set_ylabel("City buffer covered (%)")
    axis.tick_params(axis="x", rotation=35)
    axis.legend(title="")
    axis.grid(axis="y", color="#dddddd", linewidth=.6)
    fig.tight_layout()
    fig.savefig(output / "buffer_coverage_by_product.png", dpi=240, facecolor="white", bbox_inches="tight")
    fig.savefig(output / "buffer_coverage_by_product.svg", facecolor="white", bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    config = load_config()
    output = PIPELINE / "outputs/analysis"
    output.mkdir(parents=True, exist_ok=True)
    footprints = gpd.read_parquet(PIPELINE / "outputs/city_product_footprints.parquet")
    coverage = footprints.drop(columns="geometry").copy()
    coverage.to_csv(output / "city_product_buffer_coverage.csv", index=False)
    cutoff = float(config["population_break"])
    summary_statistics(coverage, cutoff).to_csv(output / "coverage_summary_statistics.csv", index=False)
    coverage_plot(coverage, cutoff, output)
    correlations(coverage, cutoff, output)
    iou_statistics(footprints, cutoff, output)
    print(f"Wrote analysis outputs: {output}")


if __name__ == "__main__":
    main()
