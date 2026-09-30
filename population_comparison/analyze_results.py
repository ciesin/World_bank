#!/usr/bin/env python3
"""Create boxplots, summary tables, and pairwise correlation matrices."""

from __future__ import annotations

import itertools
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/wb-population-matplotlib")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from common import PIPELINE, load_config


GROUPS = [
    ("all_cities", "All cities", None),
    ("population_lt_300k", "Cities <300,000", "population_lt_300k"),
    ("population_ge_300k", "Cities 300,000+", "population_ge_300k"),
]
CLASSES = [
    ("urban_center", "Urban centers"),
    ("peri_urban", "Peri-urban areas"),
]


def load_values(products: list[str]) -> pd.DataFrame:
    columns = [
        "segment_id", "city_id", "city_name", "country", "city_size_group",
        "source_zone", "segment_area_km2", "density_class",
        "consensus_density_people_per_km2", "valid_product_count",
    ] + [f"pop_{product}" for product in products] + [
        f"coverage_pct_{product}" for product in products
    ]
    files = sorted((PIPELINE / "outputs" / "segment_population").glob("*.parquet"))
    if not files:
        raise FileNotFoundError("No population outputs; run aggregate_population.py")
    return pd.concat((pd.read_parquet(path, columns=columns) for path in files), ignore_index=True)


def box_statistics(values: pd.Series) -> dict:
    clean = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype="float64")
    if not len(clean):
        return {"n": 0, "q1": np.nan, "median": np.nan, "q3": np.nan,
                "iqr": np.nan, "whislo": np.nan, "whishi": np.nan, "mean": np.nan}
    q1, median, q3 = np.quantile(clean, [0.25, 0.5, 0.75])
    iqr = q3 - q1
    inside = clean[(clean >= q1 - 1.5 * iqr) & (clean <= q3 + 1.5 * iqr)]
    return {
        "n": int(len(clean)), "q1": q1, "median": median, "q3": q3, "iqr": iqr,
        "whislo": float(inside.min()), "whishi": float(inside.max()),
        "mean": float(clean.mean()),
    }


def draw_boxplot(stats: list[dict], title: str, output: Path, config: dict, symlog=False) -> None:
    fig, ax = plt.subplots(figsize=(config["plot"]["width_inches"], config["plot"]["height_inches"]))
    bxp = [
        {"label": row["product_label"], "med": row["median"], "q1": row["q1"],
         "q3": row["q3"], "whislo": row["whislo"], "whishi": row["whishi"],
         "fliers": []}
        for row in stats if row["n"] > 0
    ]
    artists = ax.bxp(bxp, patch_artist=True, showfliers=False, widths=0.62)
    for patch, row in zip(artists["boxes"], (row for row in stats if row["n"] > 0)):
        patch.set_facecolor(row["color"])
        patch.set_alpha(0.88)
        patch.set_edgecolor("#333333")
    for name in ("whiskers", "caps", "medians"):
        for artist in artists[name]:
            artist.set_color("#333333")
            artist.set_linewidth(1.1)
    ax.set_title(title, loc="left", fontsize=12, weight="bold")
    ax.set_ylabel("Estimated population per segment (people)")
    ax.set_xlabel("")
    ax.grid(axis="y", alpha=0.22, linewidth=0.7)
    ax.tick_params(axis="x", labelrotation=18)
    if symlog:
        ax.set_yscale("symlog", linthresh=1)
        ax.set_ylabel("Estimated population per segment (people; symlog scale)")
    fig.tight_layout()
    fig.savefig(output.with_suffix(".png"), dpi=config["plot"]["dpi"], bbox_inches="tight")
    fig.savefig(output.with_suffix(".svg"), bbox_inches="tight")
    plt.close(fig)


def correlation_outputs(data: pd.DataFrame, products: list[str], labels: list[str], group_key: str,
                        class_key: str, output_root: Path, long_rows: list[dict]) -> None:
    values = data[[f"pop_{product}" for product in products]].copy()
    values.columns = labels
    for method in ("spearman", "pearson"):
        corr = values.corr(method=method, min_periods=3)
        valid = values.notna().astype("int64")
        counts = valid.T.dot(valid)
        stem = f"correlation_{method}_{group_key}_{class_key}"
        corr.to_csv(output_root / f"{stem}.csv")
        counts.to_csv(output_root / f"{stem}_n.csv")
        for left, right in itertools.combinations_with_replacement(labels, 2):
            long_rows.append({
                "method": method, "city_group": group_key, "density_class": class_key,
                "product_1": left, "product_2": right,
                "correlation": corr.loc[left, right], "n": int(counts.loc[left, right]),
            })
        fig, ax = plt.subplots(figsize=(6.2, 5.3))
        image = ax.imshow(corr.to_numpy(), vmin=-1, vmax=1, cmap="RdBu_r")
        ax.set_xticks(range(len(labels)), labels, rotation=35, ha="right")
        ax.set_yticks(range(len(labels)), labels)
        for row in range(len(labels)):
            for column in range(len(labels)):
                value = corr.iloc[row, column]
                text = "NA" if pd.isna(value) else f"{value:.2f}"
                color = "white" if pd.notna(value) and abs(value) >= 0.58 else "#222222"
                ax.text(column, row, text, ha="center", va="center", fontsize=8.5, color=color)
        pretty_group = dict((key, title) for key, title, _ in GROUPS)[group_key]
        pretty_class = dict(CLASSES)[class_key]
        ax.set_title(f"{method.title()} correlation — {pretty_class}, {pretty_group}", loc="left", fontsize=11, weight="bold")
        fig.colorbar(image, ax=ax, fraction=0.046, pad=0.04, label="Correlation")
        fig.tight_layout()
        fig.savefig(output_root / f"{stem}.png", dpi=300, bbox_inches="tight")
        fig.savefig(output_root / f"{stem}.svg", bbox_inches="tight")
        plt.close(fig)


def main() -> None:
    config = load_config()
    products = list(config["products"])
    labels = [config["products"][product]["label"] for product in products]
    data = load_values(products)
    output_root = PIPELINE / "outputs" / "analysis"
    output_root.mkdir(parents=True, exist_ok=True)
    box_rows = []
    correlation_rows = []

    for group_key, group_title, city_size in GROUPS:
        group = data if city_size is None else data.loc[data["city_size_group"] == city_size]
        for class_key, class_title in CLASSES:
            subset = group.loc[group["density_class"] == class_key]
            current_stats = []
            for product, label in zip(products, labels):
                stats = box_statistics(subset[f"pop_{product}"])
                row = {
                    "city_group": group_key, "density_class": class_key,
                    "product": product, "product_label": label,
                    "color": config["products"][product]["color"], **stats,
                }
                box_rows.append(row)
                current_stats.append(row)
            stem = output_root / f"boxplot_population_{group_key}_{class_key}"
            draw_boxplot(current_stats, f"{class_title}: {group_title}", stem, config)
            if config["plot"].get("also_write_log_scale"):
                draw_boxplot(current_stats, f"{class_title}: {group_title}",
                             output_root / f"{stem.name}_symlog", config, symlog=True)
            correlation_outputs(
                subset, products, labels, group_key, class_key, output_root, correlation_rows
            )

    pd.DataFrame(box_rows).drop(columns="color").to_csv(
        output_root / "population_boxplot_statistics.csv", index=False
    )
    pd.DataFrame(correlation_rows).to_csv(
        output_root / "correlation_statistics_long.csv", index=False
    )
    classification = (
        data.groupby(["city_size_group", "density_class"], dropna=False)
        .agg(segment_count=("segment_id", "size"), city_count=("city_id", "nunique"),
             median_consensus_density=("consensus_density_people_per_km2", "median"))
        .reset_index()
    )
    classification.to_csv(output_root / "classification_summary.csv", index=False)
    source_crosswalk = (
        data.groupby(["source_zone", "density_class"], dropna=False)
        .size().rename("segment_count").reset_index()
    )
    source_crosswalk.to_csv(output_root / "source_zone_by_density_class.csv", index=False)
    coverage_rows = []
    city_coverage_rows = []
    for product, label in zip(products, labels):
        valid = data[f"pop_{product}"].notna()
        coverage_rows.append({
            "product": product,
            "product_label": label,
            "total_segment_count": int(len(data)),
            "valid_segment_count": int(valid.sum()),
            "missing_segment_count": int((~valid).sum()),
            "percent_valid": float(100 * valid.mean()),
        })
        grouped = data.assign(_valid=valid).groupby(
            ["city_id", "city_name", "country"], as_index=False
        ).agg(total_segment_count=("segment_id", "size"), valid_segment_count=("_valid", "sum"))
        grouped["missing_segment_count"] = (
            grouped["total_segment_count"] - grouped["valid_segment_count"]
        )
        grouped["percent_valid"] = 100 * grouped["valid_segment_count"] / grouped["total_segment_count"]
        grouped.insert(3, "product_label", label)
        grouped.insert(3, "product", product)
        city_coverage_rows.append(grouped)
    pd.DataFrame(coverage_rows).to_csv(
        output_root / "product_coverage_summary.csv", index=False
    )
    pd.concat(city_coverage_rows, ignore_index=True).to_csv(
        output_root / "product_coverage_by_city.csv", index=False
    )
    print(f"Wrote analysis outputs to {output_root}")


if __name__ == "__main__":
    main()
