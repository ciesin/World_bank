#!/usr/bin/env python3
"""Read native WSF Tracker GeoZarr subsets and create per-city 2025 binary COGs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import obstore
import rasterio
import xarray as xr
from affine import Affine
from obstore.store import S3Store
from rasterio.enums import Resampling
from rasterio.warp import calculate_default_transform, reproject
from zarr.storage import ObjectStore

from common import PIPELINE, load_config, slugify
from submit_earth_engine_exports import local_utm


def open_wsf(specification):
    prefix = specification["zarr_url"].split("source.coop/", 1)[1]
    store = S3Store(
        bucket="us-west-2.opendata.source.coop", prefix=prefix,
        config={"aws_skip_signature": "true", "aws_region": "us-west-2"},
    )
    return xr.open_zarr(ObjectStore(store, read_only=True), group=specification["group"], decode_coords="all")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", action="append")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    config = load_config()
    fields = config["city_fields"]
    spec = config["raster_processing"]["wsf_tracker_2025"]
    buffers = gpd.read_file(config["city_buffers"], layer=config["city_layer"]).to_crs(4326)
    if args.city:
        wanted = {slugify(value) for value in args.city}
        buffers = buffers.loc[buffers[fields["name"]].map(slugify).isin(wanted)]
    dataset = open_wsf(spec)
    output_root = PIPELINE / "data/rasters/wsf_tracker_2025"
    output_root.mkdir(parents=True, exist_ok=True)
    for row in buffers.itertuples(index=False):
        city_id = int(getattr(row, fields["id"]))
        name = str(getattr(row, fields["name"]))
        output = output_root / f"{city_id}_{slugify(name)}_wsf_tracker_2025.tif"
        if output.exists() and not args.force:
            print(f"Existing: {output}")
            continue
        west, south, east, north = row.geometry.bounds
        values = dataset[spec["variable"]].sel(x=slice(west, east), y=slice(north, south)).load()
        x = np.asarray(values.x)
        y = np.asarray(values.y)
        source = np.asarray(values)
        binary = ((source > 0) & (source <= spec["maximum_built_date_index"])).astype("uint8")
        xres = float(np.median(np.diff(x)))
        yres = float(abs(np.median(np.diff(y))))
        source_transform = Affine.translation(x[0] - xres / 2, y[0] + yres / 2) * Affine.scale(xres, -yres)
        dst_crs = local_utm(row.geometry)
        dst_transform, width, height = calculate_default_transform(
            "EPSG:4326", dst_crs, binary.shape[1], binary.shape[0],
            west, south, east, north, resolution=spec["resolution_m"],
        )
        destination = np.zeros((height, width), dtype="uint8")
        reproject(
            binary, destination, src_transform=source_transform, src_crs="EPSG:4326",
            dst_transform=dst_transform, dst_crs=dst_crs, resampling=Resampling.nearest,
        )
        profile = {
            "driver": "GTiff", "height": height, "width": width, "count": 1,
            "dtype": "uint8", "crs": dst_crs, "transform": dst_transform,
            "nodata": 0, "compress": "deflate", "tiled": True,
        }
        with rasterio.open(output, "w", **profile) as target:
            target.write(destination, 1)
            target.set_band_description(1, "built_by_2025_01_01")
        print(f"Wrote: {output}")


if __name__ == "__main__":
    main()
