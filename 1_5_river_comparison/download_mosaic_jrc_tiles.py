#!/usr/bin/env python3
"""Download one tiled JRC city export and mosaic it to the normal input path."""

from __future__ import annotations

import argparse
import json
import time

import ee
import rasterio
from googleapiclient.discovery import build
from rasterio.merge import merge

from common import PIPELINE
from download_jrc_exports import download, drive_files, drive_folders


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--poll-seconds", type=int, default=30)
    args = parser.parse_args()
    manifest_path = PIPELINE / "data" / args.manifest
    manifest = json.loads(manifest_path.read_text())
    ee.Initialize(project=manifest["project"])
    drive = build("drive", "v3", credentials=ee.data.get_persistent_credentials(), cache_discovery=False)
    tile_root = PIPELINE / "data" / "jrc_gsw" / "tiles"
    while True:
        remote = {}
        for folder_id in drive_folders(drive, manifest["drive_folder"]):
            remote.update(drive_files(drive, folder_id))
        for item in manifest["exports"]:
            output = tile_root / item["file_name"]
            if not output.exists() and item["file_name"] in remote:
                download(drive, remote[item["file_name"]], output)
                print(f"Downloaded {output}", flush=True)
        paths = [tile_root / item["file_name"] for item in manifest["exports"]]
        count = sum(path.exists() for path in paths)
        print(f"Local tiled exports {count}/{len(paths)}", flush=True)
        if count == len(paths):
            break
        time.sleep(max(5, args.poll_seconds))
    sources = [rasterio.open(path) for path in paths]
    try:
        mosaic, transform = merge(sources, nodata=sources[0].nodata)
        profile = sources[0].profile.copy()
        profile.update(width=mosaic.shape[2], height=mosaic.shape[1], transform=transform)
        descriptions = sources[0].descriptions
    finally:
        for source in sources:
            source.close()
    final = PIPELINE / "data" / "jrc_gsw" / f"{manifest['stem']}_jrc_gsw.tif"
    temporary = final.with_suffix(".tif.partial")
    with rasterio.open(temporary, "w", **profile) as target:
        target.write(mosaic)
        for index, description in enumerate(descriptions, start=1):
            target.set_band_description(index, description)
    temporary.replace(final)
    print(f"Wrote mosaic {final}", flush=True)


if __name__ == "__main__":
    main()
