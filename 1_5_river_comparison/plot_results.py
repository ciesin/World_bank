#!/usr/bin/env python3
"""Create Figure 4.1, six-city presence maps, and decadal occurrence maps."""

from __future__ import annotations

import os

os.environ.setdefault("MPLCONFIGDIR", "/tmp/wb-water-matplotlib")

import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib.colors import BoundaryNorm, ListedColormap, Normalize
from matplotlib.patches import Patch
import numpy as np
import pandas as pd
import rasterio

from common import PIPELINE, load_config, normalize_text
from analyze_cities import COMBINATIONS


GROUP_LABELS = {
    "all_cities": "All cities",
    "cities_lt_300k_urban_center": "Cities <300k - Urban centers",
    "cities_lt_300k_peri_urban": "Cities <300k - Peri-urban",
    "cities_ge_300k_urban_center": "Cities 300k+ - Urban centers",
    "cities_ge_300k_peri_urban": "Cities 300k+ - Peri-urban",
}
COMBINATION_LABELS = {
    "osm_only": "OSM only", "gsw_only": "JRC-GSW only", "grwl_only": "GRWL only",
    "osm_gsw": "OSM + JRC-GSW", "osm_grwl": "OSM + GRWL",
    "gsw_grwl": "JRC-GSW + GRWL", "all_three": "All three",
}
COMBINATION_ORDER = [
    "osm_only", "gsw_only", "grwl_only", "osm_gsw", "osm_grwl", "gsw_grwl", "all_three"
]


def save_figure(fig, output) -> None:
    fig.savefig(output.with_suffix(".png"), dpi=300, bbox_inches="tight")
    fig.savefig(output.with_suffix(".svg"), bbox_inches="tight")
    plt.close(fig)


def plot_figure_4_1(config: dict, analysis_root) -> None:
    data = pd.read_csv(analysis_root / "figure_4_1_presence_combinations.csv")
    pivot = data.pivot(index="reporting_group", columns="combination", values="proportion_percent").fillna(0)
    pivot = pivot.reindex(index=GROUP_LABELS, columns=COMBINATION_ORDER, fill_value=0)
    fig, ax = plt.subplots(figsize=(8.4, 4.8))
    left = np.zeros(len(pivot))
    for combination in COMBINATION_ORDER:
        values = pivot[combination].to_numpy()
        ax.barh(
            range(len(pivot)), values, left=left,
            color=config["presence"]["combination_colors"][combination],
            label=COMBINATION_LABELS[combination], height=0.66,
        )
        left += values
    ax.set_yticks(range(len(pivot)), [GROUP_LABELS[key] for key in pivot.index])
    ax.invert_yaxis()
    ax.set_xlim(0, 100)
    ax.set_xlabel("Proportion of water-positive 30 m cells (%)")
    ax.set_title("Permanent-water presence agreement across OSM, JRC-GSW, and GRWL", loc="left", weight="bold")
    ax.grid(axis="x", alpha=0.22)
    ax.legend(ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.18), frameon=False)
    fig.tight_layout()
    save_figure(fig, analysis_root / "figure_4_1_permanent_water_presence")


def presence_colormap(config: dict):
    code_labels = [(code, COMBINATIONS[code]) for code in range(1, 8)]
    colors = [config["presence"]["combination_colors"][label] for _, label in code_labels]
    return ListedColormap(colors), BoundaryNorm(np.arange(0.5, 8.5, 1), len(colors)), code_labels


def example_files(config: dict):
    root = PIPELINE / "outputs" / "segment_water"
    by_name = {}
    for path in root.glob("*_segment_water.parquet"):
        row = pd.read_parquet(path, columns=["city_name"]).iloc[0]
        by_name[normalize_text(row.city_name)] = path
    for city in config["example_cities"]:
        path = by_name.get(normalize_text(city))
        if path is None:
            raise FileNotFoundError(f"No completed segment output for example city: {city}")
        stem = path.name.removesuffix("_segment_water.parquet")
        yield city, stem, path


def plot_presence_examples(config: dict, analysis_root) -> None:
    cmap, norm, code_labels = presence_colormap(config)
    examples = list(example_files(config))
    fig, axes = plt.subplots(2, 3, figsize=(10.2, 7.2))
    for ax, (city, stem, _) in zip(axes.flat, examples):
        path = PIPELINE / "outputs" / "presence_grids" / f"{stem}_presence_combinations.tif"
        with rasterio.open(path) as source:
            data = source.read(1, masked=True)
        # Code 0 means dry in all three products. It is outside the
        # water-positive comparison denominator and should remain blank rather
        # than inheriting the first categorical color (OSM only).
        data = np.ma.masked_where((data == 0) | np.ma.getmaskarray(data), data)
        ax.imshow(data, cmap=cmap, norm=norm, interpolation="nearest")
        ax.set_title(city, fontsize=10, weight="bold")
        ax.set_axis_off()
    handles = [Patch(color=config["presence"]["combination_colors"][label], label=COMBINATION_LABELS[label]) for _, label in code_labels]
    fig.legend(handles=handles, ncol=4, loc="lower center", frameon=False)
    fig.suptitle("30 m cells with permanent-water presence in three datasets", weight="bold")
    fig.tight_layout(rect=(0, 0.08, 1, 0.96))
    save_figure(fig, analysis_root / "figure_4_2_example_city_presence_maps")


def plot_decadal_examples(config: dict, analysis_root) -> None:
    decade_keys = list(config["jrc_gsw"]["decades"])
    for city, stem, path in example_files(config):
        segments = gpd.read_parquet(path)
        fig, axes = plt.subplots(1, 4, figsize=(11.5, 3.4))
        for ax, key in zip(axes, decade_keys):
            column = f"jrc_occurrence_{key}_mean_pct"
            segments.plot(column=column, ax=ax, cmap="Blues", vmin=0, vmax=100, linewidth=0)
            start, end = config["jrc_gsw"]["decades"][key]
            ax.set_title(f"{start}-{end}", fontsize=10)
            ax.set_axis_off()
        scalar = plt.cm.ScalarMappable(cmap="Blues", norm=Normalize(0, 100))
        scalar.set_array([])
        fig.colorbar(scalar, ax=axes, fraction=0.025, pad=0.01, label="Mean water occurrence by segment (%)")
        fig.suptitle(f"JRC-GSW water occurrence by segment - {city}", weight="bold")
        fig.subplots_adjust(left=0.01, right=0.92, bottom=0.02, top=0.84, wspace=0.03)
        save_figure(fig, analysis_root / f"figure_decadal_water_occurrence_{stem}")


def main() -> None:
    config = load_config()
    analysis_root = PIPELINE / "outputs" / "analysis"
    analysis_root.mkdir(parents=True, exist_ok=True)
    plot_figure_4_1(config, analysis_root)
    plot_presence_examples(config, analysis_root)
    plot_decadal_examples(config, analysis_root)
    print(f"Wrote plots to {analysis_root}")


if __name__ == "__main__":
    main()
