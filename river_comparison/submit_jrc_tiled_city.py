#!/usr/bin/env python3
"""Submit aligned tiled JRC exports for one unusually large city."""

from __future__ import annotations

import argparse
import json

import ee
from shapely.geometry import box, mapping

from common import PIPELINE, city_output_stem, load_buffers, load_config, normalize_text
from submit_jrc_exports import export_image


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", required=True)
    parser.add_argument("--project")
    parser.add_argument("--rows", type=int, default=2)
    parser.add_argument("--cols", type=int, default=2)
    args = parser.parse_args()
    config = load_config()
    fields = config["city_fields"]
    spec = config["jrc_gsw"]
    buffers = load_buffers(config).to_crs("EPSG:4326")
    wanted = normalize_text(args.city)
    selected = buffers.loc[buffers[fields["name"]].map(normalize_text) == wanted]
    if len(selected) != 1:
        raise ValueError(f"Expected one city matching {args.city!r}; found {len(selected)}")
    city = next(selected.itertuples(index=False))
    stem = city_output_stem(city, fields)
    minx, miny, maxx, maxy = city.geometry.bounds
    x_edges = [minx + (maxx - minx) * index / args.cols for index in range(args.cols + 1)]
    y_edges = [miny + (maxy - miny) * index / args.rows for index in range(args.rows + 1)]
    project = args.project or spec["project"]
    ee.Initialize(project=project)
    image, projection = export_image(config)
    projection_info = projection.getInfo()
    exports = []
    for row in range(args.rows):
        for col in range(args.cols):
            name = f"{stem}_tile_r{row + 1}_c{col + 1}"
            geometry = box(x_edges[col], y_edges[row], x_edges[col + 1], y_edges[row + 1])
            region = ee.Geometry(mapping(geometry), proj="EPSG:4326", geodesic=False)
            task = ee.batch.Export.image.toDrive(
                image=image,
                description=name,
                folder=spec["drive_folder"],
                fileNamePrefix=name,
                region=region,
                crs=projection_info.get("crs") or projection_info.get("wkt"),
                crsTransform=projection_info["transform"],
                maxPixels=spec["max_pixels"],
                fileFormat="GeoTIFF",
                formatOptions={"cloudOptimized": True, "noData": spec["nodata"]},
            )
            task.start()
            exports.append({"file_name": f"{name}.tif", "task_id": task.id})
            print(f"Submitted {name} ({task.id})", flush=True)
    manifest = PIPELINE / "data" / f"{stem}_jrc_tiled_export_manifest.json"
    manifest.write_text(json.dumps({
        "city": str(getattr(city, fields["name"])), "stem": stem,
        "project": project, "drive_folder": spec["drive_folder"], "exports": exports,
    }, indent=2) + "\n")
    print(f"Wrote {manifest}")


if __name__ == "__main__":
    main()
