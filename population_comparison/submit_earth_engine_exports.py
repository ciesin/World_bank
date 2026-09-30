#!/usr/bin/env python3
"""Plan, or explicitly submit, native-grid population raster exports by city."""

from __future__ import annotations

import argparse
import json

import ee
import geopandas as gpd
from shapely.geometry import box

from common import PIPELINE, city_output_stem, load_buffers, load_config


def source_image(spec: dict, region: ee.Geometry) -> tuple[ee.Image, ee.Projection, int]:
    collection = ee.ImageCollection(spec["collection"])
    if "filter_property" in spec:
        collection = collection.filter(
            ee.Filter.eq(spec["filter_property"], spec["filter_value"])
        )
    if spec.get("filter_bounds"):
        collection = collection.filterBounds(region)
    count = int(collection.size().getInfo())
    if count == 0:
        raise RuntimeError(f"No source images for {spec['collection']}")
    first = ee.Image(collection.first()).select(spec["band"])
    projection = first.projection()
    image = collection.select(spec["band"]).mosaic().rename("population")
    image = image.setDefaultProjection(projection)
    return image, projection, count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--submit", action="store_true", help="Create Drive export tasks")
    parser.add_argument("--project", help="Earth Engine Cloud project")
    parser.add_argument("--city", action="append")
    parser.add_argument("--product", action="append")
    parser.add_argument("--max-tasks", type=int)
    args = parser.parse_args()
    config = load_config()
    fields = config["city_fields"]
    ee_config = config["earth_engine"]
    products = ee_config["products"]
    unknown = set(args.product or []) - set(products)
    if unknown:
        raise ValueError(f"Unknown products: {sorted(unknown)}")
    selected_products = args.product or list(products)
    buffers = load_buffers(config).to_crs("EPSG:4326")
    if args.city:
        wanted = {name.casefold() for name in args.city}
        buffers = buffers.loc[buffers[fields["name"]].str.casefold().isin(wanted)]
    plan = []
    for city in buffers.itertuples(index=False):
        stem = city_output_stem(city, fields)
        segment_file = PIPELINE / "data" / "prepared_segments" / f"{stem}.parquet"
        if segment_file.exists():
            segment_geometry = gpd.read_parquet(segment_file, columns=["geometry"]).to_crs("EPSG:4326")
            xmin, ymin, xmax, ymax = segment_geometry.total_bounds
            # Include a small margin so every edge pixel intersecting a segment
            # is present while keeping the export compact.
            geometry = box(xmin - 0.002, ymin - 0.002, xmax + 0.002, ymax + 0.002).__geo_interface__
            region_source = "prepared_segment_bounds"
        else:
            geometry = city.geometry.__geo_interface__
            region_source = "city_buffer"
        for product in selected_products:
            plan.append({
                "city_id": int(getattr(city, fields["id"])),
                "city_name": str(getattr(city, fields["name"])),
                "product": product,
                "file_name": f"{stem}_{product}.tif",
                "drive_folder": ee_config["drive_folder"],
                "geometry": geometry,
                "region_source": region_source,
                "task_id": None,
            })
    if args.max_tasks is not None:
        plan = plan[: args.max_tasks]
    manifest_path = PIPELINE / "data" / "earth_engine_export_manifest.json"
    if not args.submit:
        manifest_path.write_text(json.dumps({"submitted": False, "exports": plan}, indent=2) + "\n")
        print(f"Planned {len(plan)} exports; wrote {manifest_path}")
        print("No Earth Engine tasks were submitted.")
        return

    project = args.project or ee_config["project"]
    ee.Initialize(project=project)
    nodata = ee_config["nodata"]
    for index, item in enumerate(plan, start=1):
        region = ee.Geometry(item.pop("geometry"), proj="EPSG:4326", geodesic=False)
        spec = products[item["product"]]
        image, projection, source_count = source_image(spec, region)
        projection_info = projection.getInfo()
        task = ee.batch.Export.image.toDrive(
            # These products store population counts and commonly mask valid
            # zero-population cells. All configured sources cover the study
            # countries, so masked cells within the export region are zero.
            image=image.toFloat().unmask(0, sameFootprint=False),
            description=item["file_name"].removesuffix(".tif"),
            folder=item["drive_folder"],
            fileNamePrefix=item["file_name"].removesuffix(".tif"),
            region=region,
            crs=projection_info.get("crs") or projection_info.get("wkt"),
            crsTransform=projection_info["transform"],
            maxPixels=ee_config["max_pixels"],
            fileFormat="GeoTIFF",
            formatOptions={"cloudOptimized": True, "noData": nodata},
        )
        task.start()
        item["task_id"] = task.id
        item["source_image_count"] = source_count
        print(f"Submitted {index}/{len(plan)}: {item['file_name']} ({task.id})", flush=True)
    manifest_path.write_text(
        json.dumps({"submitted": True, "project": project, "exports": plan}, indent=2) + "\n"
    )
    print(f"Wrote {manifest_path}")


if __name__ == "__main__":
    main()
