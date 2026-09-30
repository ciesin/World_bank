#!/usr/bin/env python3
"""Plan or submit per-city Dynamic World and Google 2.5D binary raster exports."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

import geopandas as gpd
from shapely.geometry import mapping

from common import PIPELINE, load_config, slugify


def local_utm(geometry_wgs84) -> str:
    point = geometry_wgs84.representative_point()
    zone = max(1, min(60, int((point.x + 180) // 6) + 1))
    return f"EPSG:{(32600 if point.y >= 0 else 32700) + zone}"


def ee_geometry(geometry, ee):
    return ee.Geometry(json.loads(json.dumps(mapping(geometry))))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", help="Google Cloud project registered for Earth Engine")
    parser.add_argument("--drive-folder", default="wb_city_built_rasters")
    parser.add_argument("--city", action="append", help="Limit to city name or normalized slug")
    parser.add_argument("--submit", action="store_true", help="Actually create Earth Engine tasks")
    args = parser.parse_args()
    config = load_config()
    fields = config["city_fields"]
    buffers = gpd.read_file(config["city_buffers"], layer=config["city_layer"]).to_crs(4326)
    if args.city:
        wanted = {slugify(value) for value in args.city}
        buffers = buffers.loc[buffers[fields["name"]].map(slugify).isin(wanted)]
    if buffers.empty:
        raise ValueError("No cities selected")
    if args.submit:
        import ee
        if not args.project:
            raise ValueError("--project is required with --submit")
        ee.Initialize(project=args.project)

    raster = config["raster_processing"]
    plans = []
    for row in buffers.itertuples(index=False):
        city_id = int(getattr(row, fields["id"]))
        city_name = str(getattr(row, fields["name"]))
        slug = slugify(city_name)
        geometry = row.geometry
        crs = local_utm(geometry)
        region = ee_geometry(geometry, ee) if args.submit else None
        products = [
            ("dynamic_world_2025", raster["dynamic_world_2025"]),
            ("google_2_5d_2023", raster["google_2_5d_2023"]),
        ]
        for product, specification in products:
            prefix = f"{city_id}_{slug}_{product}"
            task_id = None
            if args.submit:
                collection = ee.ImageCollection(specification["earth_engine_collection"]).filterBounds(region).filterDate(
                    specification["date_start"], specification["date_end"]
                )
                if product == "dynamic_world_2025":
                    image = collection.select(specification["band"]).median().gte(
                        specification["probability_threshold"]
                    )
                else:
                    image = collection.mosaic().select(specification["band"]).gte(
                        specification["probability_threshold"]
                    )
                image = image.unmask(0).toUint8().rename("built")
                task = ee.batch.Export.image.toDrive(
                    image=image, description=prefix, folder=args.drive_folder,
                    fileNamePrefix=prefix, region=region, crs=crs,
                    scale=specification["resolution_m"], maxPixels=1e13,
                    fileFormat="GeoTIFF", formatOptions={"cloudOptimized": True},
                )
                task.start()
                task_id = task.id
            plans.append({
                "city_id": city_id, "city_name": city_name, "city_slug": slug,
                "product": product, "earth_engine_collection": specification["earth_engine_collection"],
                "date_start": specification["date_start"], "date_end": specification["date_end"],
                "threshold": specification["probability_threshold"], "scale_m": specification["resolution_m"],
                "crs": crs, "drive_folder": args.drive_folder,
                "file_name": prefix + ".tif", "submitted": args.submit, "task_id": task_id,
            })
    output = PIPELINE / "data/earth_engine_export_manifest.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "submitted": args.submit, "exports": plans,
    }, indent=2) + "\n")
    print(f"{'Submitted' if args.submit else 'Planned'} {len(plans)} exports")
    print(f"Wrote: {output}")


if __name__ == "__main__":
    main()
