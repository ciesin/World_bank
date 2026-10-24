#!/usr/bin/env python3
"""Download Microsoft Road Detections regional archives and clip to study cities."""

from __future__ import annotations

import argparse
import zipfile

import geopandas as gpd
import orjson
import requests
import shapely
from shapely.geometry import box, shape

from common import PIPELINE, city_output_stem, load_buffers, load_config, road_path


def download(url, output):
    output.parent.mkdir(parents=True,exist_ok=True); partial=output.with_suffix(".zip.partial"); offset=partial.stat().st_size if partial.exists() else 0
    with requests.get(url,headers={"Range":f"bytes={offset}-"} if offset else {},stream=True,timeout=(30,300)) as response:
        response.raise_for_status(); mode="ab" if offset and response.status_code==206 else "wb"
        with partial.open(mode) as target:
            for block in response.iter_content(4*1024*1024):
                if block: target.write(block)
    partial.replace(output)


def parse_archive(path, cities_by_code):
    study_by_code={code:shapely.union_all(frame.geometry.to_numpy()) for code,frame in cities_by_code.items()}; bbox_by_code={code:box(*geom.bounds) for code,geom in study_by_code.items()}; found={code:[] for code in cities_by_code}
    with zipfile.ZipFile(path) as archive:
        members=[x for x in archive.infolist() if not x.is_dir()]; member=max(members,key=lambda x:x.file_size)
        print(f"  parsing {member.filename} ({member.file_size/1e9:.2f} GB uncompressed)",flush=True)
        with archive.open(member) as stream:
            for count,line in enumerate(stream,start=1):
                if count%2_000_000==0: print(f"  parsed {count:,} Microsoft roads",flush=True)
                try: code,payload=line.rstrip(b"\r\n").split(b"\t",1)
                except ValueError: continue
                code=code.decode("ascii",errors="ignore")
                if code not in found: continue
                try:
                    obj=orjson.loads(payload); geometry=shape(obj.get("geometry",obj))
                except Exception: continue
                if not geometry.is_empty and bbox_by_code[code].intersects(geometry) and study_by_code[code].intersects(geometry): found[code].append(geometry)
    return found


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--region",action="append"); parser.add_argument("--force",action="store_true"); parser.add_argument("--keep-zip",action="store_true"); args=parser.parse_args()
    config=load_config(); fields=config["city_fields"]; ms=config["microsoft"]; buffers=load_buffers(config).to_crs("EPSG:4326"); regions=ms["regions"]
    if args.region: regions={k:v for k,v in regions.items() if k in set(args.region)}
    root=PIPELINE/"data"/"microsoft"/"regional_archives"
    for region,codes in regions.items():
        zip_path=root/f"{region}.zip"
        if not zip_path.exists(): print(f"Downloading Microsoft {region}",flush=True); download(f"{ms['base_url']}/{region}.zip",zip_path)
        cities_by_code={}
        for country,code in ms["country_codes"].items():
            if code in codes: cities_by_code[code]=buffers.loc[buffers[fields["country"]]==country].copy()
        found=parse_archive(zip_path,cities_by_code)
        for code,cities in cities_by_code.items():
            frame=gpd.GeoDataFrame({"geometry":found[code]},crs="EPSG:4326").to_crs(config["analysis_crs"])
            for city in cities.to_crs(config["analysis_crs"]).itertuples(index=False):
                stem=city_output_stem(city,fields); output=road_path("microsoft",stem)
                if output.exists() and not args.force: continue
                pieces=[g.intersection(city.geometry) for g in frame.loc[frame.intersects(city.geometry),"geometry"]]; pieces=[g for g in pieces if not g.is_empty]
                union=shapely.union_all(pieces) if pieces else None; result=gpd.GeoDataFrame({"geometry":[] if union is None else [union]},crs=config["analysis_crs"]).explode(index_parts=False).reset_index(drop=True)
                output.parent.mkdir(parents=True,exist_ok=True); result.to_parquet(output,index=False); print(f"Wrote {output} ({len(result):,} parts)",flush=True)
        if not args.keep_zip: zip_path.unlink(missing_ok=True)


if __name__ == "__main__": main()
