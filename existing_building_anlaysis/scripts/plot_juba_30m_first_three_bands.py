#!/usr/bin/env python3
"""Plot bands 1–3 of the Juba 30 m comparison with a shared legend."""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import rasterio


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "outputs/juba_30m_comparison.tif"
OUTPUT = ROOT / "outputs/juba_30m_first_three_bands.png"
VMAX = 0.60


def main():
    with rasterio.open(INPUT) as src:
        arrays = [src.read(index, masked=True).filled(np.nan) for index in range(1, 4)]
        names = [src.descriptions[index - 1] or f"Band {index}" for index in range(1, 4)]
        bounds = src.bounds

    extent = (bounds.left, bounds.right, bounds.bottom, bounds.top)
    fig, axes = plt.subplots(1, 3, figsize=(18, 5.4), constrained_layout=True)
    image = None
    for index, (ax, values, name) in enumerate(zip(axes, arrays, names), 1):
        image = ax.imshow(
            values,
            extent=extent,
            origin="upper",
            cmap="magma",
            vmin=0,
            vmax=VMAX,
            interpolation="nearest",
        )
        ax.set_title(f"Band {index}: {name}")
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])

    colorbar = fig.colorbar(image, ax=axes, shrink=0.78, pad=0.015)
    colorbar.set_label("Building-footprint fraction per 30 m cell")
    colorbar.set_ticks(np.arange(0, VMAX + 0.001, 0.1))
    colorbar.set_ticklabels(["0", "0.1", "0.2", "0.3", "0.4", "0.5", "≥0.6"])
    fig.suptitle("Juba 30 m building-footprint comparison")
    fig.savefig(OUTPUT, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(OUTPUT)


if __name__ == "__main__":
    main()
