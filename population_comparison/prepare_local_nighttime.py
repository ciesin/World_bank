#!/usr/bin/env python3
"""Crop the large local LandScan 2025 nighttime raster to each city grid."""

from __future__ import annotations

import argparse

import geopandas as gpd
import rasterio
from rasterio.windows import Window, from_bounds

from common import PIPELINE, city_output_stem, load_buffers, load_config, raster_path


def integer_window(window: Window, width: int, height: int) -> Window:
    rounded = window.round_offsets().round_lengths()
    col0 = max(0, int(rounded.col_off))
    row0 = max(0, int(rounded.row_off))
    col1 = min(width, int(rounded.col_off + rounded.width))
    row1 = min(height, int(rounded.row_off + rounded.height))
    return Window(col0, row0, max(0, col1 - col0), max(0, row1 - row0))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", action="append")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    config = load_config()
    fields = config["city_fields"]
    buffers = load_buffers(config)
    if args.city:
        wanted = {name.casefold() for name in args.city}
        buffers = buffers.loc[buffers[fields["name"]].str.casefold().isin(wanted)]

    with rasterio.open(config["local_nighttime_raster"]) as source:
        projected_buffers = buffers.to_crs(source.crs)
        for city in projected_buffers.itertuples(index=False):
            stem = city_output_stem(city, fields)
            output = raster_path("landscan_nighttime_2025", stem)
            if output.exists() and not args.force:
                print(f"Exists: {output}", flush=True)
                continue
            window = integer_window(
                from_bounds(*city.geometry.bounds, transform=source.transform),
                source.width,
                source.height,
            )
            if window.width <= 0 or window.height <= 0:
                raise ValueError(f"City does not overlap nighttime raster: {stem}")
            data = source.read(window=window)
            profile = source.profile.copy()
            width, height = int(window.width), int(window.height)
            profile.update(width=width, height=height,
                           transform=source.window_transform(window), compress="zstd")
            if width >= 16 and height >= 16:
                profile.update(
                    tiled=True,
                    blockxsize=max(16, min(512, (width // 16) * 16)),
                    blockysize=max(16, min(512, (height // 16) * 16)),
                )
            else:
                profile.update(tiled=False)
                profile.pop("blockxsize", None)
                profile.pop("blockysize", None)
            output.parent.mkdir(parents=True, exist_ok=True)
            with rasterio.open(output, "w", **profile) as target:
                target.write(data)
                target.update_tags(**source.tags())
                target.update_tags(1, **source.tags(1))
                target.set_band_description(1, source.descriptions[0])
            print(f"Wrote {output}", flush=True)


if __name__ == "__main__":
    main()
