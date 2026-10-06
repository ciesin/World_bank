#!/usr/bin/env python3
"""Download Overture transportation road segments by city."""

from __future__ import annotations

import argparse

import geopandas as gpd
import shapely

from common import city_output_stem, load_buffers, load_config, normalize_text, road_path


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--city",action="append"); parser.add_argument("--force",action="store_true"); args=parser.parse_args()
    from overturemaps import geodataframe
    config=load_config(); fields=config["city_fields"]; buffers=load_buffers(config)
    if args.city:
        wanted={normalize_text(x) for x in args.city}; buffers=buffers.loc[buffers[fields["name"]].map(normalize_text).isin(wanted)]
    for city in buffers.itertuples(index=False):
        stem=city_output_stem(city,fields); output=road_path("overture",stem)
        if output.exists() and not args.force: print(f"Exists: {stem}",flush=True); continue
        metric=gpd.GeoDataFrame({"geometry":[city.geometry]},crs=buffers.crs); wgs=metric.to_crs("EPSG:4326"); bbox=tuple(wgs.total_bounds)
        print(f"Downloading Overture {stem}",flush=True)
        frame=geodataframe(config["overture"]["type"],bbox=bbox,release=config["overture"]["release"],connect_timeout=30,request_timeout=300,stac=True)
        if frame.crs is None: frame=frame.set_crs("EPSG:4326")
        frame=frame.loc[frame.get("subtype").eq(config["overture"]["subtype"]) & frame.geometry.notna() & ~frame.geometry.is_empty].to_crs(config["analysis_crs"])
        pieces=[g.intersection(city.geometry) for g in frame.loc[frame.intersects(city.geometry),"geometry"]]
        pieces=[g for g in pieces if not g.is_empty]; union=shapely.union_all(pieces) if pieces else None
        result=gpd.GeoDataFrame({"geometry":[] if union is None else [union]},crs=config["analysis_crs"]).explode(index_parts=False).reset_index(drop=True)
        output.parent.mkdir(parents=True,exist_ok=True); result.to_parquet(output,index=False)
        print(f"Wrote {output} ({len(result):,} parts)",flush=True)


if __name__ == "__main__": main()
