#!/usr/bin/env python3
"""Download Geofabrik extracts and create city-level OSM vehicle-road layers."""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import requests
import shapely
from shapely.geometry import box

from common import PIPELINE, city_output_stem, load_buffers, load_config, normalize_text, road_path


def download(url: str, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_suffix(output.suffix + ".partial")
    offset = partial.stat().st_size if partial.exists() else 0
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    with requests.get(url, headers=headers, stream=True, timeout=(30, 300)) as response:
        response.raise_for_status()
        mode = "ab" if offset and response.status_code == 206 else "wb"
        if mode == "wb": offset = 0
        with partial.open(mode) as target:
            for block in response.iter_content(chunk_size=4 * 1024 * 1024):
                if block: target.write(block)
    partial.replace(output)


def from_wkb(value):
    if isinstance(value, str): value = bytes.fromhex(value)
    return shapely.from_wkb(value, on_invalid="ignore")


def extract(pbf: Path, cities: gpd.GeoDataFrame, config: dict) -> list:
    import osmium
    allowed = set(config["osm"]["vehicle_highways"])
    study = shapely.union_all(cities.geometry.to_numpy())
    bounds = box(*study.bounds)
    factory = osmium.geom.WKBFactory()
    roads = []
    for count, obj in enumerate(osmium.FileProcessor(str(pbf)).with_locations(), start=1):
        if count % 10_000_000 == 0: print(f"  scanned {count:,} OSM objects", flush=True)
        if not obj.is_way() or obj.tags.get("highway") not in allowed or obj.tags.get("area") == "yes":
            continue
        try: geometry = from_wkb(factory.create_linestring(obj))
        except (RuntimeError, ValueError): continue
        if geometry is not None and not geometry.is_empty and bounds.intersects(geometry) and study.intersects(geometry):
            roads.append(geometry)
    return roads


def write_cities(cities, roads, config, force):
    fields = config["city_fields"]
    frame = gpd.GeoDataFrame({"geometry": roads}, crs="EPSG:4326").to_crs(config["analysis_crs"])
    for city in cities.to_crs(config["analysis_crs"]).itertuples(index=False):
        stem = city_output_stem(city, fields); output = road_path("osm", stem)
        if output.exists() and not force: continue
        pieces = [g.intersection(city.geometry) for g in frame.loc[frame.intersects(city.geometry), "geometry"]]
        pieces = [g for g in pieces if not g.is_empty]
        union = shapely.union_all(pieces) if pieces else None
        result = gpd.GeoDataFrame({"geometry": [] if union is None else [union]}, crs=config["analysis_crs"]).explode(index_parts=False).reset_index(drop=True)
        output.parent.mkdir(parents=True, exist_ok=True); result.to_parquet(output, index=False)
        print(f"Wrote {stem}: {len(result):,} OSM road parts", flush=True)


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--country",action="append"); parser.add_argument("--force",action="store_true"); parser.add_argument("--keep-pbf",action="store_true"); args=parser.parse_args()
    config=load_config(); fields=config["city_fields"]; buffers=load_buffers(config).to_crs("EPSG:4326"); extracts=config["osm"]["geofabrik_extracts"]
    if args.country:
        wanted={normalize_text(x) for x in args.country}; extracts={k:v for k,v in extracts.items() if normalize_text(k) in wanted}
    root=PIPELINE/"data"/"osm"/"geofabrik"
    for country,slug in extracts.items():
        cities=buffers.loc[buffers[fields["country"]]==country].copy(); pbf=root/f"{slug}-latest.osm.pbf"
        if not pbf.exists():
            print(f"Downloading OSM {country}",flush=True); download(f"{config['osm']['geofabrik_base_url']}/{slug}-{config['osm']['snapshot']}.osm.pbf",pbf)
        print(f"Extracting OSM {country}",flush=True); roads=extract(pbf,cities,config); write_cities(cities,roads,config,args.force)
        if not args.keep_pbf: pbf.unlink(missing_ok=True)


if __name__ == "__main__": main()
