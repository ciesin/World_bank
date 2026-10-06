#!/usr/bin/env python3
"""Calculate segment road lengths, 10 m matches, summaries, and correlations."""

from __future__ import annotations

import argparse

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

from common import PIPELINE, city_output_stem, load_buffers, load_config, normalize_text, prepared_segment_path, road_path


SOURCES = ("osm", "overture", "microsoft")
LINE_TYPES = {"LineString", "MultiLineString"}
GROUPS = [
    ("all_cities", None, None),
    ("cities_lt_300k_urban_center", "population_lt_300k", "city_segment"),
    ("cities_lt_300k_peri_urban", "population_lt_300k", "hexgrid"),
    ("cities_ge_300k_urban_center", "population_ge_300k", "city_segment"),
    ("cities_ge_300k_peri_urban", "population_ge_300k", "hexgrid"),
]
CORRELATION_GROUPS = [
    ("all_urban_center", None, "city_segment"), ("all_peri_urban", None, "hexgrid"),
    ("cities_lt_300k_urban_center", "population_lt_300k", "city_segment"),
    ("cities_lt_300k_peri_urban", "population_lt_300k", "hexgrid"),
    ("cities_ge_300k_urban_center", "population_ge_300k", "city_segment"),
    ("cities_ge_300k_peri_urban", "population_ge_300k", "hexgrid"),
]


def read_union(path, crs):
    frame=gpd.read_parquet(path)
    if frame.crs is None: frame=frame.set_crs(crs)
    if frame.empty: return shapely.GeometryCollection()
    geometry=shapely.union_all(frame.geometry.to_numpy())
    return geometry if geometry is not None else shapely.GeometryCollection()


def line_lengths_by_segment(geometry, segments):
    result=pd.Series(0.0,index=segments.segment_id,dtype="float64")
    if geometry is None or geometry.is_empty: return result
    parts=gpd.GeoDataFrame({"geometry":[geometry]},crs=segments.crs).explode(index_parts=False).reset_index(drop=True)
    parts=parts.loc[parts.geometry.geom_type.isin(LINE_TYPES)]
    if parts.empty: return result
    # A spatial join exposes only true segment/line candidates; vectorized
    # intersection is materially faster than a full overlay for large cities.
    candidates=gpd.sjoin(segments[["segment_id","geometry"]],parts[["geometry"]],how="inner",predicate="intersects")
    if candidates.empty: return result
    left=candidates.geometry.reset_index(drop=True)
    right=parts.geometry.iloc[candidates.index_right.to_numpy()].reset_index(drop=True)
    lengths=shapely.length(shapely.intersection(left.array,right.array))
    totals=pd.Series(lengths).groupby(candidates.segment_id.to_numpy()).sum()
    result.loc[totals.index]=totals
    return result


def subset_group(data,size,zone):
    if size is None: return data if zone is None else data.loc[data.source_zone==zone]
    return data.loc[(data.city_size_group==size)&(data.source_zone==zone)]


def aggregate_outputs(config):
    files=sorted((PIPELINE/"outputs"/"segment_roads").glob("*_segment_roads.parquet"))
    columns=["city_id","city_name","country","city_size_group","source_zone",
             "osm_total_m","overture_total_m","microsoft_total_m",
             "overture_in_osm_buffer_m","microsoft_in_osm_buffer_m",
             "osm_in_overture_buffer_m","microsoft_in_overture_buffer_m",
             "osm_in_microsoft_buffer_m","overture_in_microsoft_buffer_m"]
    data=pd.concat((pd.read_parquet(path,columns=columns) for path in files),ignore_index=True)
    root=PIPELINE/"outputs"/"analysis"; root.mkdir(parents=True,exist_ok=True)
    table52=[]
    for label,size,zone in GROUPS:
        d=subset_group(data,size,zone)
        table52.append({"reporting_group":label,"segment_count":len(d),"city_count":d.city_id.nunique(),
                        "osm_vehicle_roads_km":d.osm_total_m.sum()/1000,
                        "overture_roads_km":d.overture_total_m.sum()/1000,
                        "microsoft_roads_km":d.microsoft_total_m.sum()/1000})
    pd.DataFrame(table52).to_csv(root/"table_5_2_total_road_length_km.csv",index=False)
    definitions=[
        ("overture_in_osm_buffer_ratio","overture_in_osm_buffer_m","osm_total_m"),
        ("microsoft_in_osm_buffer_ratio","microsoft_in_osm_buffer_m","osm_total_m"),
        ("osm_in_overture_buffer_ratio","osm_in_overture_buffer_m","overture_total_m"),
        ("microsoft_in_overture_buffer_ratio","microsoft_in_overture_buffer_m","overture_total_m"),
        ("osm_in_microsoft_buffer_ratio","osm_in_microsoft_buffer_m","microsoft_total_m"),
        ("overture_in_microsoft_buffer_ratio","overture_in_microsoft_buffer_m","microsoft_total_m"),
    ]
    table53=[]
    for label,size,zone in GROUPS:
        d=subset_group(data,size,zone); row={"reporting_group":label,"segment_count":len(d),"city_count":d.city_id.nunique()}
        for output,numerator,denominator in definitions:
            den=d[denominator].sum(); row[output]=d[numerator].sum()/den if den>0 else np.nan
        table53.append(row)
    pd.DataFrame(table53).to_csv(root/"table_5_3_road_match_ratios.csv",index=False)
    values=[f"{source}_total_m" for source in SOURCES]; rows=[]
    for label,size,zone in CORRELATION_GROUPS:
        d=subset_group(data,size,zone); corr=d[values].corr(method=config["correlation_method"])
        for left in SOURCES:
            for right in SOURCES:
                rows.append({"reporting_group":label,"method":config["correlation_method"],"source_1":left,"source_2":right,
                             "correlation":corr.loc[f"{left}_total_m",f"{right}_total_m"],"segment_count":len(d),"city_count":d.city_id.nunique()})
    pd.DataFrame(rows).to_csv(root/"figure_5_1_segment_road_length_correlations.csv",index=False)


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--city",action="append"); parser.add_argument("--force",action="store_true"); parser.add_argument("--skip-incomplete",action="store_true"); parser.add_argument("--skip-aggregate",action="store_true",help="Defer portfolio summary tables until all city outputs are complete"); args=parser.parse_args()
    config=load_config(); fields=config["city_fields"]; buffers=load_buffers(config)
    if args.city:
        wanted={normalize_text(x) for x in args.city}; buffers=buffers.loc[buffers[fields["name"]].map(normalize_text).isin(wanted)]
    root=PIPELINE/"outputs"/"segment_roads"; root.mkdir(parents=True,exist_ok=True)
    for city in buffers.itertuples(index=False):
        stem=city_output_stem(city,fields); output=root/f"{stem}_segment_roads.parquet"
        required={"segments":prepared_segment_path(stem),**{source:road_path(source,stem) for source in SOURCES}}
        missing=[key for key,path in required.items() if not path.exists()]
        if missing and args.skip_incomplete: print(f"Not ready: {stem} ({', '.join(missing)})",flush=True); continue
        if missing: raise FileNotFoundError(f"Missing inputs for {stem}: {missing}")
        if output.exists() and not args.force: print(f"Exists: {stem}",flush=True); continue
        segments=gpd.read_parquet(required["segments"]); roads={source:read_union(required[source],segments.crs) for source in SOURCES}
        buffers10={source:shapely.buffer(geometry,config["match_buffer_m"],quad_segs=2) if not geometry.is_empty else shapely.GeometryCollection() for source,geometry in roads.items()}
        variants={f"{source}_total_m":geometry for source,geometry in roads.items()}
        for reference in SOURCES:
            for matched in SOURCES:
                if reference==matched: continue
                variants[f"{matched}_in_{reference}_buffer_m"]=shapely.intersection(roads[matched],buffers10[reference])
        for column,geometry in variants.items(): segments[column]=line_lengths_by_segment(geometry,segments).to_numpy()
        segments.to_parquet(output,index=False); print(f"Wrote {output} ({len(segments):,} segments)",flush=True)
    if not args.skip_aggregate:
        aggregate_outputs(config)


if __name__ == "__main__": main()
