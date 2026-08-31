#!/usr/bin/env python3
"""Create an RGB composite from bands 1–3 of the Juba 30 m raster."""

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Patch
import numpy as np
import rasterio


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "outputs/juba_30m_comparison.tif"
OUTPUT = ROOT / "outputs/juba_30m_rgb_composite.png"
DISPLAY_MAX = 0.40


def main():
    with rasterio.open(INPUT) as src:
        bands = [src.read(index, masked=True) for index in range(1, 4)]
        bounds = src.bounds

    common_mask = np.logical_or.reduce([np.ma.getmaskarray(band) for band in bands])
    channels = [
        np.clip(np.asarray(band.filled(0), dtype="float32") / DISPLAY_MAX, 0, 1)
        for band in bands
    ]
    rgb = np.dstack(channels)
    rgba = np.dstack([rgb, (~common_mask).astype("float32")])

    fig, ax = plt.subplots(figsize=(13.5, 7.2), constrained_layout=True)
    extent = (bounds.left, bounds.right, bounds.bottom, bounds.top)
    ax.imshow(rgba, extent=extent, origin="upper", interpolation="nearest")
    ax.set_title("Juba 30 m RGB building-footprint composite")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_aspect("equal")

    legend_items = [
        Patch(facecolor=(1, 0, 0), edgecolor="0.35", label="Red — Overture (band 1)"),
        Patch(facecolor=(0, 1, 0), edgecolor="0.35", label="Green — Google 2.5D (band 2)"),
        Patch(facecolor=(0, 0, 1), edgecolor="0.35", label="Blue — Global Building Atlas (band 3)"),
        Patch(facecolor=(1, 1, 0), edgecolor="0.35", label="Yellow — Overture + Google"),
        Patch(facecolor=(1, 0, 1), edgecolor="0.35", label="Magenta — Overture + Global Atlas"),
        Patch(facecolor=(0, 1, 1), edgecolor="0.35", label="Cyan — Google + Global Atlas"),
        Patch(facecolor=(1, 1, 1), edgecolor="0.35", label="White/grey — all three similar"),
        Patch(facecolor=(0, 0, 0), edgecolor="0.35", label="Black — little or no footprint"),
    ]
    legend = ax.legend(
        handles=legend_items,
        title="Additive RGB legend\nChannel brightness = footprint fraction\n0 to ≥0.40 (display clipped)",
        loc="center left",
        bbox_to_anchor=(1.015, 0.5),
        frameon=False,
        borderaxespad=0,
        labelspacing=0.9,
    )
    legend._legend_box.align = "left"

    fig.savefig(OUTPUT, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(OUTPUT)


if __name__ == "__main__":
    main()
