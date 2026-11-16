#!/usr/bin/env python3
"""Stream a very large GeoJSON FeatureCollection and clip it to an AOI."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import geopandas as gpd
import ijson
from shapely.geometry import shape
from shapely.prepared import prep


def coordinate_bounds(coordinates):
    """Return xmin, ymin, xmax, ymax for arbitrarily nested coordinates."""
    xmin = ymin = float("inf")
    xmax = ymax = float("-inf")
    stack = [coordinates]
    while stack:
        item = stack.pop()
        if not item:
            continue
        if isinstance(item[0], (int, float)):
            x, y = item[:2]
            xmin = min(xmin, x)
            ymin = min(ymin, y)
            xmax = max(xmax, x)
            ymax = max(ymax, y)
        else:
            stack.extend(item)
    return xmin, ymin, xmax, ymax


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("--aoi", required=True)
    parser.add_argument("--input-crs", default="EPSG:3857")
    parser.add_argument("--region")
    parser.add_argument("--progress-every", type=int, default=500_000)
    args = parser.parse_args()

    aoi = gpd.read_file(args.aoi).to_crs(args.input_crs).geometry.union_all()
    aoi_prepared = prep(aoi)
    axmin, aymin, axmax, aymax = aoi.bounds

    records = []
    geometries = []
    started = time.time()
    scanned = 0
    candidates = 0

    with open(args.input, "rb") as stream:
        for feature in ijson.items(stream, "features.item", use_float=True):
            scanned += 1
            props = feature.get("properties") or {}
            if args.region and props.get("region") != args.region:
                continue
            geometry = feature.get("geometry")
            if not geometry:
                continue
            xmin, ymin, xmax, ymax = coordinate_bounds(geometry["coordinates"])
            if xmax < axmin or xmin > axmax or ymax < aymin or ymin > aymax:
                continue
            candidates += 1
            geom = shape(geometry)
            if not aoi_prepared.intersects(geom):
                continue
            records.append(props)
            geometries.append(geom.intersection(aoi))
            if args.progress_every and scanned % args.progress_every == 0:
                print(
                    f"scanned={scanned:,} candidates={candidates:,} kept={len(records):,} "
                    f"elapsed={time.time() - started:.1f}s",
                    flush=True,
                )

    result = gpd.GeoDataFrame(records, geometry=geometries, crs=args.input_crs)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() in {".parquet", ".geoparquet"}:
        result.to_parquet(output, index=False)
    else:
        result.to_file(output)
    print(
        f"complete scanned={scanned:,} candidates={candidates:,} kept={len(result):,} "
        f"elapsed={time.time() - started:.1f}s output={output}",
        flush=True,
    )


if __name__ == "__main__":
    main()
