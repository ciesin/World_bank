#!/usr/bin/env python3
"""Plan or submit aligned multi-band JRC-GSW exports for all city buffers."""

from __future__ import annotations

import argparse
import json

import ee

from common import PIPELINE, city_output_stem, load_buffers, load_config, normalize_text


def merged_monthly(config: dict) -> ee.ImageCollection:
    spec = config["jrc_gsw"]
    historical = ee.ImageCollection(spec["historical_monthly_collection"])
    # The v1.5 2022-2024 images expose integer `year` and `month` properties
    # but currently omit system:time_start, so filterDate would otherwise
    # return an empty collection.
    recent = ee.ImageCollection(spec["recent_monthly_collection"]).map(
        lambda image: image.set(
            "system:time_start",
            ee.Date.fromYMD(ee.Number(image.get("year")), ee.Number(image.get("month")), 1).millis(),
        )
    )
    return historical.merge(recent)


def occurrence_bands(monthly: ee.ImageCollection, start_year: int, end_year: int, prefix: str, band: str) -> ee.Image:
    selected = monthly.filterDate(f"{start_year}-01-01", f"{end_year + 1}-01-01").select(band)
    water_count = selected.map(lambda image: image.eq(2)).sum()
    valid_count = selected.map(lambda image: image.gt(0)).sum()
    occurrence = water_count.divide(valid_count).multiply(100).updateMask(valid_count.gt(0))
    return ee.Image.cat([
        occurrence.rename(f"{prefix}_occurrence_pct"),
        valid_count.rename(f"{prefix}_valid_months"),
    ]).toFloat()


def export_image(config: dict) -> tuple[ee.Image, ee.Projection]:
    spec = config["jrc_gsw"]
    band = spec["water_band"]
    monthly = merged_monthly(config)
    recent = monthly.filterDate(
        f"{spec['recent_start_year']}-01-01", f"{spec['recent_end_year'] + 1}-01-01"
    ).select(band)
    water_count = recent.map(lambda image: image.eq(2)).sum()
    valid_count = recent.map(lambda image: image.gt(0)).sum()
    occurrence = water_count.divide(valid_count).multiply(100).updateMask(valid_count.gt(0))
    recent_bands = ee.Image.cat([
        occurrence.rename("recent_occurrence_pct"),
        water_count.gt(0).updateMask(valid_count.gt(0)).rename("recent_any_water"),
        occurrence.gte(spec["permanent_occurrence_threshold_percent"]).rename("recent_permanent_water"),
        valid_count.rename("recent_valid_months"),
    ]).toFloat()
    decade_bands = [
        occurrence_bands(monthly, years[0], years[1], f"decade_{key}", band)
        for key, years in spec["decades"].items()
    ]
    image = ee.Image.cat([recent_bands, *decade_bands])
    projection = ee.Image(monthly.first()).select(band).projection()
    return image.setDefaultProjection(projection), projection


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--submit", action="store_true")
    parser.add_argument("--project")
    parser.add_argument("--city", action="append")
    parser.add_argument("--max-tasks", type=int)
    args = parser.parse_args()
    config = load_config()
    fields = config["city_fields"]
    spec = config["jrc_gsw"]
    buffers = load_buffers(config).to_crs("EPSG:4326")
    if args.city:
        wanted = {normalize_text(name) for name in args.city}
        buffers = buffers.loc[buffers[fields["name"]].map(normalize_text).isin(wanted)]
    plan = []
    for city in buffers.itertuples(index=False):
        stem = city_output_stem(city, fields)
        plan.append({
            "city_id": int(getattr(city, fields["id"])),
            "city_name": str(getattr(city, fields["name"])),
            "file_name": f"{stem}_jrc_gsw.tif",
            "drive_folder": spec["drive_folder"],
            "geometry": city.geometry.__geo_interface__,
            "task_id": None,
        })
    if args.max_tasks is not None:
        plan = plan[:args.max_tasks]
    manifest = PIPELINE / "data" / "jrc_export_manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    if not args.submit:
        manifest.write_text(json.dumps({"submitted": False, "exports": plan}, indent=2) + "\n")
        print(f"Planned {len(plan)} exports; wrote {manifest}")
        return
    project = args.project or spec["project"]
    ee.Initialize(project=project)
    image, projection = export_image(config)
    projection_info = projection.getInfo()
    for index, item in enumerate(plan, start=1):
        region_geojson = item.pop("geometry")
        region = ee.Geometry(region_geojson, proj="EPSG:4326", geodesic=False)
        task = ee.batch.Export.image.toDrive(
            image=image,
            description=item["file_name"].removesuffix(".tif"),
            folder=item["drive_folder"],
            fileNamePrefix=item["file_name"].removesuffix(".tif"),
            region=region,
            crs=projection_info.get("crs") or projection_info.get("wkt"),
            crsTransform=projection_info["transform"],
            maxPixels=spec["max_pixels"], fileFormat="GeoTIFF",
            formatOptions={"cloudOptimized": True, "noData": spec["nodata"]},
        )
        task.start()
        item["task_id"] = task.id
        print(f"Submitted {index}/{len(plan)}: {item['file_name']} ({task.id})", flush=True)
    manifest.write_text(json.dumps({"submitted": True, "project": project, "exports": plan}, indent=2) + "\n")
    print(f"Wrote {manifest}")


if __name__ == "__main__":
    main()
