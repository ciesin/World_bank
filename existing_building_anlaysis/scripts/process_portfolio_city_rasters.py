#!/usr/bin/env python3
"""Run reduced 30 m/100 m raster and segment analysis for one city."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/mpl_portfolio_rasters")

import geopandas as gpd
import matplotlib
import numpy as np
import pandas as pd
import rasterio
import shapely
from affine import Affine
from matplotlib import pyplot as plt
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.warp import reproject
from rasterio.windows import Window, from_bounds


matplotlib.use("Agg")
ROOT = Path(__file__).resolve().parents[1]
CITIES = ROOT / "data/cities"
OUTPUTS = ROOT / "outputs/cities"
RAW = ROOT / "data/raw/portfolio"
NODATA = -9999.0
RES30 = 30.0
RES100 = 100.0
FINE = 5.0
AREA_THRESHOLD_M2 = 25.0
HEIGHT_AREA_THRESHOLD_M2 = 50.0
WSF_THRESHOLD = .10
GBA_HEIGHT_DIR = RAW / "gba_height/tiles"


def make_grid(aoi, resolution):
    xmin, ymin, xmax, ymax = aoi.bounds
    xmin, ymin = math.floor(xmin / resolution) * resolution, math.floor(ymin / resolution) * resolution
    xmax, ymax = math.ceil(xmax / resolution) * resolution, math.ceil(ymax / resolution) * resolution
    width, height = int(round((xmax-xmin)/resolution)), int(round((ymax-ymin)/resolution))
    transform = from_origin(xmin, ymax, resolution, resolution)
    # 10 m subgrid gives partial-AOI fractions without materializing millions of polygons.
    factor = max(1, int(round(resolution / 10)))
    fine_transform = transform * Affine.scale(1/factor, 1/factor)
    mask = rasterize([(aoi, 1)], out_shape=(height*factor, width*factor),
                     transform=fine_transform, fill=0, dtype="uint8", all_touched=False)
    fraction = mask.reshape(height, factor, width, factor).mean(axis=(1,3)).astype("float32")
    return transform, width, height, fraction, (xmin,ymin,xmax,ymax)


def vector_grid(data, transform, width, height, height_field=None):
    factor = int(RES30/FINE)
    fine_shape = (height*factor, width*factor)
    fine_transform = transform * Affine.scale(1/factor,1/factor)
    presence = rasterize(((g,1) for g in data.geometry), out_shape=fine_shape,
                         transform=fine_transform, fill=0, dtype="uint8")
    fraction = presence.reshape(height,factor,width,factor).mean(axis=(1,3)).astype("float32")
    mean_height = np.full_like(fraction, np.nan)
    if height_field and len(data):
        values = pd.to_numeric(data[height_field], errors="coerce").to_numpy(dtype="float32")
        good = np.isfinite(values)&(values>=.5)&(values<=100)
        height_fine = rasterize(((g,float(v)) for g,v in zip(data.geometry[good],values[good])),
                                out_shape=fine_shape,transform=fine_transform,fill=0,dtype="float32")
        numerator = height_fine.reshape(height,factor,width,factor).mean(axis=(1,3))
        valid_fraction = (height_fine>0).reshape(height,factor,width,factor).mean(axis=(1,3))
        np.divide(numerator,valid_fraction,out=mean_height,where=valid_fraction>0)
        del height_fine
    del presence
    return fraction, mean_height


def gba_height_grid(footprints, paths, transform, width, height):
    """Area-weight GBA.Height within the portfolio's 3D-GloBFP support.

    The height raster is a continuous modeled surface, so it is restricted to
    building support before aggregation.  The portfolio does not contain the
    full GBA.Polygon layer outside Juba; 3D-GloBFP is therefore used as an
    explicit proxy footprint mask at the existing 5 m rasterization support.
    This limitation and the shared source lineage are retained in metadata.
    """
    factor = int(RES30 / FINE)
    fine_shape = (height * factor, width * factor)
    fine_transform = transform * Affine.scale(1 / factor, 1 / factor)
    valid_geom = footprints.geometry.notna() & ~footprints.geometry.is_empty
    support = rasterize(
        ((geom, 1) for geom in footprints.loc[valid_geom].geometry),
        out_shape=fine_shape, transform=fine_transform, fill=0,
        dtype="uint8", all_touched=False,
    ).astype(bool)
    height_sum = np.zeros((height, width), "float64")
    valid_count = np.zeros((height, width), "uint16")
    assigned = np.zeros(fine_shape, dtype=bool)
    for path in paths:
        with rasterio.open(path) as src:
            if src.crs is None or not src.crs.is_projected:
                raise ValueError(f"Unexpected GBA.Height CRS for {path}: {src.crs}")
            if not np.allclose(src.res, (3.0, 3.0), atol=0.01):
                raise ValueError(f"Unexpected GBA.Height resolution for {path}: {src.res}")
            if src.count != 1 or src.nodata != -1.0 or src.dtypes[0] != "float32":
                raise ValueError(
                    f"Unexpected GBA.Height schema for {path}: count={src.count}, "
                    f"dtype={src.dtypes[0]}, nodata={src.nodata}"
                )
            projected = np.full(fine_shape, NODATA, "float32")
            reproject(
                rasterio.band(src, 1), projected,
                src_transform=src.transform, src_crs=src.crs, src_nodata=src.nodata,
                dst_transform=fine_transform, dst_crs=transform_crs, dst_nodata=NODATA,
                resampling=Resampling.bilinear,
            )
        valid = support & ~assigned & np.isfinite(projected) & (projected > 0) & (projected <= 100)
        assigned[valid] = True
        height_sum += np.where(valid, projected, 0).reshape(
            height, factor, width, factor
        ).sum(axis=(1, 3))
        valid_count += valid.reshape(height, factor, width, factor).sum(axis=(1, 3)).astype("uint16")
    mean = np.full((height, width), np.nan, "float32")
    np.divide(height_sum, valid_count, out=mean, where=valid_count > 0)
    fraction = (valid_count / factor**2).astype("float32")
    return fraction, mean


def height_statistics(heights, fractions, aoi_mask, support_m):
    valid = {
        name: aoi_mask & np.isfinite(values) & (values > 0) & (values <= 100)
        & np.isfinite(fractions[name])
        & (fractions[name] >= HEIGHT_AREA_THRESHOLD_M2 / support_m**2)
        for name, values in heights.items()
    }
    summaries = []
    for name, values in heights.items():
        keep = valid[name]
        vals = values[keep]
        summaries.append({
            "source": name, "support_m": support_m,
            "valid_cells": int(keep.sum()),
            "coverage_pct_of_aoi": 100 * float(keep.sum()) / max(1, int(aoi_mask.sum())),
            "mean_height_m": float(np.mean(vals)) if len(vals) else np.nan,
            "median_height_m": float(np.median(vals)) if len(vals) else np.nan,
            "p90_height_m": float(np.quantile(vals, .9)) if len(vals) else np.nan,
        })
    pairs = []
    names = list(heights)
    for i, left in enumerate(names):
        for right in names[i + 1:]:
            keep = valid[left] & valid[right]
            a, b = heights[left][keep], heights[right][keep]
            delta = a - b
            pairs.append({
                "source_a": left, "source_b": right, "support_m": support_m,
                "common_cells": int(keep.sum()),
                "bias_a_minus_b_m": float(np.mean(delta)) if len(delta) else np.nan,
                "mae_m": float(np.mean(np.abs(delta))) if len(delta) else np.nan,
                "rmse_m": float(np.sqrt(np.mean(delta**2))) if len(delta) else np.nan,
                "spearman_rho": float(pd.Series(a).corr(pd.Series(b), method="spearman"))
                if len(delta) >= 3 else np.nan,
            })
    return pd.DataFrame(summaries), pd.DataFrame(pairs), valid


def count_and_range(heights, valid, names):
    count = sum(valid[name].astype("uint8") for name in names)
    minimum = np.min(np.stack([np.where(valid[name], heights[name], np.inf) for name in names]), axis=0)
    maximum = np.max(np.stack([np.where(valid[name], heights[name], -np.inf) for name in names]), axis=0)
    value_range = (maximum - minimum).astype("float32")
    value_range[count < 2] = np.nan
    return count, value_range


def google_urls(city_slug, bounds):
    usage = pd.read_csv(CITIES/"google_manifest_city_usage.csv")
    names = usage.loc[usage.city_slug.eq(city_slug),"filename"].tolist()
    urls=[]
    xmin,ymin,xmax,ymax=bounds
    for name in names:
        path=RAW/"google_manifests"/name
        try: manifest=json.loads(path.read_text())
        except Exception: continue
        for tileset in manifest["tilesets"]:
            for source in tileset["sources"]:
                t,d=source["affineTransform"],source["dimensions"]
                left,top=float(t["translateX"]),float(t["translateY"])
                right=left+float(t["scaleX"])*int(d["width"])
                bottom=top+float(t["scaleY"])*int(d["height"])
                tx0,tx1=sorted((left,right));ty0,ty1=sorted((bottom,top))
                if tx1<=xmin or tx0>=xmax or ty1<=ymin or ty0>=ymax: continue
                gs=manifest["uriPrefix"]+source["uris"][0]
                if gs.startswith("gs://"):
                    bucket,obj=gs[5:].split("/",1)
                    urls.append(f"https://storage.googleapis.com/{bucket}/{obj}")
    return sorted(set(urls))


def google_grid(urls, transform, width, height, bounds):
    fs=np.zeros((height,width),"float64");hs=np.zeros((height,width),"float64");count=np.zeros((height,width),"uint8")
    xmin,ymin,xmax,ymax=bounds
    opened = 0
    read_failures = 0
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
                      GDAL_HTTP_MULTIPLEX="YES",GDAL_CACHEMAX=512):
        for url in urls:
            try:
                src=rasterio.open(url)
            except Exception:
                continue
            try:
              with src:
                left,bottom=max(xmin,src.bounds.left),max(ymin,src.bounds.bottom)
                right,top=min(xmax,src.bounds.right),min(ymax,src.bounds.top)
                if left>=right or bottom>=top: continue
                raw=from_bounds(left,bottom,right,top,src.transform)
                window=Window(max(0,math.floor(raw.col_off)),max(0,math.floor(raw.row_off)),
                              min(src.width,math.ceil(raw.col_off+raw.width))-max(0,math.floor(raw.col_off)),
                              min(src.height,math.ceil(raw.row_off+raw.height))-max(0,math.floor(raw.row_off)))
                ow,oh=max(1,math.ceil(window.width/8)),max(1,math.ceil(window.height/8))
                p=src.read(3,window=window,out_shape=(oh,ow),masked=True,resampling=Resampling.bilinear)
                h=src.read(2,window=window,out_shape=(oh,ow),masked=True,resampling=Resampling.bilinear)
                opened += 1
                st=src.window_transform(window)*Affine.scale(window.width/ow,window.height/oh)
                valid=~np.ma.getmaskarray(p)&~np.ma.getmaskarray(h)
                pv=np.asarray(p.filled(0),"float32");hv=np.asarray(h.filled(0),"float32")
                building=valid&(pv>=.5)&(hv>0)&(hv<=100)
                projected=[]
                for values in (building.astype("float32"),np.where(building,hv,0).astype("float32")):
                    temp=np.full((height,width),NODATA,"float32")
                    reproject(values,temp,src_transform=st,src_crs=src.crs,dst_transform=transform,
                              dst_crs=transform_crs,src_nodata=None,dst_nodata=NODATA,resampling=Resampling.average)
                    projected.append(temp)
                good=projected[0]!=NODATA;fs[good]+=projected[0][good];hs[good]+=projected[1][good];count[good]+=1
            except Exception:
                read_failures += 1
                continue
    if urls and opened == 0:
        raise RuntimeError(f"Identified {len(urls)} Google COGs but could not open any")
    if read_failures:
        print(f"Google COG read failures skipped: {read_failures}/{len(urls)}", flush=True)
    fraction=np.full((height,width),np.nan,"float32");valid=count>0;fraction[valid]=(fs[valid]/count[valid]).astype("float32")
    numerator=np.zeros_like(fraction);numerator[valid]=(hs[valid]/count[valid]).astype("float32")
    mean=np.full_like(fraction,np.nan);np.divide(numerator,fraction,out=mean,where=fraction>0)
    return fraction,mean


def project_files(paths,band,transform,width,height,scale=1.0):
    total=np.zeros((height,width),"float64");count=np.zeros((height,width),"uint16")
    for path in paths:
        try: src=rasterio.open(path)
        except Exception: continue
        with src:
            temp=np.full((height,width),NODATA,"float32")
            reproject(rasterio.band(src,band),temp,src_transform=src.transform,src_crs=src.crs,
                      src_nodata=src.nodata,dst_transform=transform,dst_crs=transform_crs,
                      dst_nodata=NODATA,resampling=Resampling.average)
            valid=temp!=NODATA;total[valid]+=temp[valid]*scale;count[valid]+=1
    out=np.full((height,width),np.nan,"float32");valid=count>0;out[valid]=(total[valid]/count[valid]).astype("float32")
    return out


def resample_array(values,src_transform,dst_transform,width,height):
    out=np.full((height,width),np.nan,"float32")
    reproject(values,out,src_transform=src_transform,src_crs=transform_crs,src_nodata=np.nan,
              dst_transform=dst_transform,dst_crs=transform_crs,dst_nodata=np.nan,resampling=Resampling.average)
    return out


def write_tif(path,arrays,transform,aoi_fraction):
    first=next(iter(arrays.values()))
    profile=dict(driver="GTiff",height=first.shape[0],width=first.shape[1],count=len(arrays),dtype="float32",
                 crs=transform_crs,transform=transform,nodata=NODATA,compress="deflate",tiled=True,bigtiff="IF_SAFER")
    with rasterio.open(path,"w",**profile) as dst:
        for i,(name,values) in enumerate(arrays.items(),1):
            data=np.asarray(values,"float32").copy();data[(aoi_fraction<=0)|~np.isfinite(data)]=NODATA
            dst.write(data,i);dst.set_band_description(i,name)


def sample_array(values,transform,points):
    xs,ys=shapely.get_x(points),shapely.get_y(points);inv=~transform;cf,rf=inv*(xs,ys)
    c=np.floor(cf).astype(int);r=np.floor(rf).astype(int)
    good=(r>=0)&(r<values.shape[0])&(c>=0)&(c<values.shape[1])
    out=np.full(len(points),np.nan,"float32");out[good]=values[r[good],c[good]]
    return out


def segment_raster_summary(segments,arrays,transform,aoi_fraction):
    valid=np.flatnonzero(aoi_fraction.ravel()>0);width=aoi_fraction.shape[1]
    accum=[]
    for start in range(0,len(valid),250_000):
        idx=valid[start:start+250_000];rows=idx//width;cols=idx%width
        xs=transform.c+(cols+.5)*transform.a;ys=transform.f+(rows+.5)*transform.e
        pts=gpd.GeoDataFrame({"flat":idx},geometry=shapely.points(xs,ys),crs=segments.crs)
        join=gpd.sjoin(pts,segments[["ANALYSIS_ID","geometry"]],how="left",predicate="within")
        join=join.loc[~join.index.duplicated(keep="first")]
        frame=pd.DataFrame({"ANALYSIS_ID":join.ANALYSIS_ID.to_numpy(),"weight":aoi_fraction.ravel()[idx]})
        for name,values in arrays.items(): frame[name]=values.ravel()[idx]
        accum.append(frame.dropna(subset=["ANALYSIS_ID"]))
    cells=pd.concat(accum,ignore_index=True)
    rows=[]
    for identifier,g in cells.groupby("ANALYSIS_ID"):
        w=g.weight.to_numpy();record={"ANALYSIS_ID":int(identifier),"raster_cells":int(len(g))}
        for name in arrays:
            v=g[name].to_numpy();good=np.isfinite(v)
            record[name]=float(np.average(v[good],weights=w[good])) if good.any() else np.nan
        record["wsf_no_footprint_cells"]=int((g["wsf_no_footprint"]>=.5).sum())
        record["consensus_cells"]=int((g["consensus"]>=.5).sum())
        rows.append(record)
    return pd.DataFrame(rows)


def update_integrated(city_slug,grid30,t30,grid100,t100):
    path=OUTPUTS/city_slug/"integrated/best_available_footprints.parquet"
    data=gpd.read_parquet(path);points=shapely.point_on_surface(data.geometry.to_numpy())
    data["height_google_30m_m"]=sample_array(grid30["google_height"],t30,points)
    data["height_globfp_grid_30m_m"]=sample_array(grid30["globfp_height"],t30,points)
    data["height_gba_30m_m"]=sample_array(grid30["gba_height"],t30,points)
    data["height_tempo_100m_m"]=sample_array(grid100["tempo_height"],t100,points)
    data["height_wsf3d_100m_m"]=sample_array(grid100["wsf3d_height"],t100,points)
    data["wsf2019_settlement_fraction"]=sample_array(grid30["wsf_fraction"],t30,points)
    candidates=[
        ("native_geometry",pd.to_numeric(data.native_height_m,errors="coerce").to_numpy()),
        ("OSM_levels_x_3m",pd.to_numeric(data.height_floors_estimate_m,errors="coerce").to_numpy()),
        ("3D-GloBFP_vector",pd.to_numeric(data.height_globfp_vector_m,errors="coerce").to_numpy()),
        ("Google_2.5D_30m",data.height_google_30m_m.to_numpy()),
        ("WSF3D_v2_100m",data.height_wsf3d_100m_m.to_numpy()),
        ("TEMPO_100m",data.height_tempo_100m_m.to_numpy()),
    ]
    best=np.full(len(data),np.nan);source=np.full(len(data),None,dtype=object)
    stack=[]
    for name,v in candidates:
        v=np.asarray(v,"float64");valid=np.isfinite(v)&(v>=.5)&(v<=100);stack.append(np.where(valid,v,np.nan))
        take=np.isnan(best)&valid;best[take]=v[take];source[take]=name
    s=np.vstack(stack);valid=np.isfinite(s);count=valid.sum(axis=0);no=count==0;s[:,no]=0
    rng=np.nanmax(s,axis=0)-np.nanmin(s,axis=0);rng[no]=np.nan
    data["height_best_m"],data["height_source"],data["height_source_count"],data["height_range_m"]=best,source,count.astype("int16"),rng
    gba = pd.to_numeric(data.height_gba_30m_m, errors="coerce").to_numpy(dtype="float64")
    gba_valid = np.isfinite(gba) & (gba >= .5) & (gba <= 100)
    data["height_source_count_gba_excluded"] = count.astype("int16")
    data["height_range_m_gba_excluded"] = rng
    included = np.vstack([np.where(np.isfinite(v), v, np.nan) for v in stack] + [np.where(gba_valid, gba, np.nan)])
    included_valid = np.isfinite(included)
    included_count = included_valid.sum(axis=0)
    included_empty = included_count == 0
    included[:, included_empty] = 0
    included_range = np.nanmax(included, axis=0) - np.nanmin(included, axis=0)
    included_range[included_count < 2] = np.nan
    data["height_source_count_gba_included"] = included_count.astype("int16")
    data["height_range_m_gba_included"] = included_range
    data["height_confidence"]=np.where(pd.Series(source).isin(["native_geometry"]),"high",
                               np.where(pd.Series(source).isin(["OSM_levels_x_3m","3D-GloBFP_vector"]),"medium",
                                        np.where(pd.Series(source).notna(),"low",None)))
    data["review_required"]=data.geometry_confidence.eq("low")|((data.height_source_count>=2)&(data.height_range_m>5))
    data.to_parquet(path,index=False)
    return data


def plot_overview(path,city_name,source_count,wsf_gap,height_range,aoi_fraction):
    fig,axes=plt.subplots(1,3,figsize=(15,5),constrained_layout=True)
    panels=[(source_count,"viridis",0,3,"Positive footprint sources"),
            (np.where(wsf_gap,1,np.nan),"Reds",0,1,"WSF settlement; no footprints"),
            (height_range,"magma",0,5,"Height source range (m)")]
    for ax,(v,cmap,vmin,vmax,title) in zip(axes,panels):
        x=np.asarray(v,dtype="float32").copy();x[aoi_fraction<=0]=np.nan
        im=ax.imshow(x,cmap=cmap,vmin=vmin,vmax=vmax);ax.set_title(title);ax.set_axis_off();fig.colorbar(im,ax=ax,shrink=.7)
    fig.suptitle(f"{city_name}: reduced raster consistency analysis",fontsize=15)
    fig.savefig(path,dpi=180,bbox_inches="tight");plt.close(fig)


def process(city_slug,force=False):
    out=OUTPUTS/city_slug/"analysis";summary_path=out/"raster_summary.json"
    if summary_path.exists() and not force:
        print(f"{city_slug}: existing raster output",flush=True);return json.loads(summary_path.read_text())
    meta=json.loads((CITIES/city_slug/"inputs/metadata.json").read_text())
    global transform_crs
    transform_crs=f"EPSG:{meta['analysis_epsg']}"
    aoi=gpd.read_parquet(CITIES/city_slug/"inputs/aoi.parquet").to_crs(transform_crs).geometry.union_all()
    segments=gpd.read_parquet(CITIES/city_slug/"inputs/segments.parquet").to_crs(transform_crs)
    overture=gpd.read_parquet(CITIES/city_slug/"sources/overture_clipped.parquet").to_crs(transform_crs)
    globfp=gpd.read_parquet(CITIES/city_slug/"sources/globfp3d_clipped.parquet").to_crs(transform_crs)
    t30,w30,h30,af30,b30=make_grid(aoi,RES30)
    print(f"{city_slug}: rasterizing vectors",flush=True)
    ov_fraction,_=vector_grid(overture,t30,w30,h30)
    gl_fraction,gl_height=vector_grid(globfp,t30,w30,h30,"Height")
    urls=google_urls(city_slug,b30)
    print(f"{city_slug}: Google tiles {len(urls)}",flush=True)
    google_fraction,google_height=google_grid(urls,t30,w30,h30,b30) if urls else (
        np.full((h30,w30),np.nan,"float32"),np.full((h30,w30),np.nan,"float32"))
    gba_usage=pd.read_csv(CITIES/"gba_height_city_usage.csv")
    gba_names=gba_usage.loc[
        gba_usage.city_slug.eq(city_slug)&gba_usage.filename.notna(),"filename"
    ].tolist()
    gba_paths=[GBA_HEIGHT_DIR/name for name in gba_names]
    missing_gba=[str(path) for path in gba_paths if not path.exists()]
    if missing_gba:
        raise FileNotFoundError(
            f"{city_slug}: {len(missing_gba)} GBA.Height tiles missing; "
            "run scripts/download_gba_height_portfolio.py"
        )
    print(f"{city_slug}: GBA.Height tiles {len(gba_paths)}",flush=True)
    gba_fraction,gba_height=gba_height_grid(globfp,gba_paths,t30,w30,h30) if gba_paths else (
        np.full((h30,w30),np.nan,"float32"),np.full((h30,w30),np.nan,"float32"))
    wsf_usage=pd.read_csv(CITIES/"wsf2019_city_usage.csv")
    wsf_paths=[RAW/"wsf2019"/n for n in wsf_usage.loc[wsf_usage.city_slug.eq(city_slug),"filename"]]
    wsf_fraction=np.clip(project_files(wsf_paths,1,t30,w30,h30,1/255),0,1)
    mask=af30>=.5;threshold=AREA_THRESHOLD_M2/RES30**2
    fractions={"Overture":ov_fraction,"Google 2.5D":google_fraction,"3D-GloBFP":gl_fraction}
    positive={n:mask&np.isfinite(v)&(v>=threshold) for n,v in fractions.items()}
    source_count=sum(v.astype("uint8") for v in positive.values());consensus=mask&(source_count>=2)
    any_fp=mask&(source_count>0);wsf_present=mask&np.isfinite(wsf_fraction)&(wsf_fraction>=WSF_THRESHOLD)
    wsf_gap=wsf_present&~any_fp

    arrays30={"Overture fraction":ov_fraction,"Google 2.5D fraction":google_fraction,
              "3D-GloBFP fraction":gl_fraction,"source agreement count":source_count.astype("float32"),
              "consensus":consensus.astype("float32"),"WSF 2019 settlement fraction":wsf_fraction,
              "WSF 2019 settlement present":wsf_present.astype("float32"),
              "any footprint present":any_fp.astype("float32"),"WSF settlement no footprints":wsf_gap.astype("float32"),
              "Google mean height m":google_height,"GBA.Height mean height m":gba_height,
              "GBA.Height valid building fraction":gba_fraction,
              "3D-GloBFP mean height m":gl_height}
    write_tif(out/"comparison_30m.tif",arrays30,t30,af30)

    t100,w100,h100,af100,b100=make_grid(aoi,RES100)
    tempo_usage=pd.read_csv(CITIES/"tempo_city_usage.csv")
    tempo_paths=[RAW/"tempo_2023q4"/n for n in tempo_usage.loc[tempo_usage.city_slug.eq(city_slug),"filename"]]
    tempo_fraction=np.clip(project_files(tempo_paths,1,t100,w100,h100),0,1)
    tempo_height=project_files(tempo_paths,2,t100,w100,h100,100)
    gf100=resample_array(google_fraction,t30,t100,w100,h100)
    gn100=resample_array(np.nan_to_num(google_fraction*google_height),t30,t100,w100,h100)
    gh100=np.full_like(gf100,np.nan);np.divide(gn100,gf100,out=gh100,where=gf100>0)
    lf100=resample_array(gl_fraction,t30,t100,w100,h100)
    ln100=resample_array(np.nan_to_num(gl_fraction*gl_height),t30,t100,w100,h100)
    lh100=np.full_like(lf100,np.nan);np.divide(ln100,lf100,out=lh100,where=lf100>0)
    baf100=resample_array(gba_fraction,t30,t100,w100,h100)
    ban100=resample_array(np.nan_to_num(gba_fraction*gba_height),t30,t100,w100,h100)
    bah100=np.full_like(baf100,np.nan);np.divide(ban100,baf100,out=bah100,where=baf100>0)
    wsf3d_dir=RAW/"wsf3d"/city_slug
    wsf3d_fraction=np.clip(project_files([wsf3d_dir/"fraction.tif"],1,t100,w100,h100,.01),0,1)
    wsf3d_height=project_files([wsf3d_dir/"height.tif"],1,t100,w100,h100)
    heights100={"TEMPO":tempo_height,"Google 2.5D":gh100,"GBA.Height":bah100,
                "3D-GloBFP":lh100,"WSF 3D v2":wsf3d_height}
    fractions100={"TEMPO":tempo_fraction,"Google 2.5D":gf100,"GBA.Height":baf100,
                  "3D-GloBFP":lf100,"WSF 3D v2":wsf3d_fraction}
    height_summary,height_pairs,height_valid=height_statistics(
        heights100,fractions100,af100>=.5,RES100
    )
    legacy_names=[name for name in heights100 if name!="GBA.Height"]
    legacy_count,legacy_range=count_and_range(heights100,height_valid,legacy_names)
    included_count,included_range=count_and_range(heights100,height_valid,list(heights100))
    arrays100={"TEMPO mean height m":tempo_height,"Google 2.5D mean height m":gh100,
               "GBA.Height mean height m":bah100,"GBA.Height valid building fraction":baf100,
               "3D-GloBFP mean height m":lh100,"WSF 3D v2 mean height m":wsf3d_height,
               "valid height source count GBA excluded":legacy_count.astype("float32"),
               "inter-source height range m GBA excluded":legacy_range,
               "valid height product count GBA included":included_count.astype("float32"),
               "inter-product height range m GBA included":included_range}
    write_tif(out/"height_comparison_100m.tif",arrays100,t100,af100)
    height_summary.to_csv(out/"height_source_summary.csv",index=False)
    height_pairs.to_csv(out/"height_pairwise_agreement.csv",index=False)
    sensitivity=[]
    for label,names,count,value_range in (
        ("GBA excluded",legacy_names,legacy_count,legacy_range),
        ("GBA included",list(heights100),included_count,included_range),
    ):
        comparable=(af100>=.5)&(count>=2)&np.isfinite(value_range)
        sensitivity.append({
            "scenario":label,"products":"; ".join(names),
            "comparable_100m_cells":int(comparable.sum()),
            "median_inter_product_range_m":float(np.nanmedian(value_range[comparable])) if comparable.any() else np.nan,
            "p90_inter_product_range_m":float(np.nanquantile(value_range[comparable],.9)) if comparable.any() else np.nan,
            "mean_valid_product_count":float(np.mean(count[af100>=.5])),
        })
    pd.DataFrame(sensitivity).to_csv(out/"height_sensitivity.csv",index=False)
    height_metadata={
        "city_slug":city_slug,"analysis_crs":transform_crs,
        "height_comparison_grid_m":RES100,"building_area_threshold_m2":HEIGHT_AREA_THRESHOLD_M2,
        "gba_height_tiles":gba_names,
        "gba_height_dataset":"GlobalBuildingAtlas GBA.Height",
        "gba_height_record":"https://doi.org/10.14459/2025mp1782307",
        "gba_height_release_date":"2025-09-02","gba_height_production_end_date":"2025-04-30",
        "gba_height_units":"metres","gba_height_source_nodata":-1,
        "gba_height_filter":"Finite modeled values >0 and <=100 m within the proxy footprint mask.",
        "gba_height_aggregation":"Native 3 m raster reprojected to the 5 m portfolio mask; valid building-pixel-area weighted to 30 m, then valid building-area weighted to 100 m.",
        "gba_height_support":"3D-GloBFP proxy footprint mask; the official GBA.Polygon layer is not locally available portfolio-wide. Juba's dedicated analysis uses official GBA footprints.",
        "gba_height_license":"CC BY-NC 4.0; attribution required, indicate modifications, non-commercial use only.",
        "gba_height_dependency":"Excluded from independent-source interpretation because GBA.Height and TEMPO share PlanetScope imagery and the footprint/model lineage overlaps Google, Microsoft, and OSM-derived inputs.",
        "sensitivity":"GBA-excluded and GBA-included counts and ranges are both retained.",
        "interpretation":"Inter-product consistency only, not accuracy; no independent reference-height dataset is available.",
        "acquisition_manifest":"data/cities/gba_height_download_manifest.json",
    }
    (out/"height_analysis_metadata.json").write_text(json.dumps(height_metadata,indent=2)+"\n")
    hotspot_mask=(af100>=.5)&np.isfinite(included_range)
    hotspot_rows,hotspot_cols=np.where(hotspot_mask)
    if len(hotspot_rows):
        order=np.argsort(included_range[hotspot_mask])[-100:][::-1]
        hotspot_rows,hotspot_cols=hotspot_rows[order],hotspot_cols[order]
        left=t100.c+hotspot_cols*t100.a;top=t100.f+hotspot_rows*t100.e
        hotspot=gpd.GeoDataFrame({
            "row":hotspot_rows,"col":hotspot_cols,
            "valid_count_gba_excluded":legacy_count[hotspot_rows,hotspot_cols],
            "valid_count_gba_included":included_count[hotspot_rows,hotspot_cols],
            "range_m_gba_excluded":legacy_range[hotspot_rows,hotspot_cols],
            "range_m_gba_included":included_range[hotspot_rows,hotspot_cols],
        },geometry=shapely.box(left,top-RES100,left+RES100,top),crs=transform_crs)
        for name,values in heights100.items():
            field=name.lower().replace(" ","_").replace(".","")+"_height_m"
            hotspot[field]=values[hotspot_rows,hotspot_cols]
        hotspot.to_file(out/"height_hotspots_top100.gpkg",layer="height_hotspots_100m",driver="GPKG")
        hotspot.drop(columns="geometry").to_csv(out/"height_hotspots_top100.csv",index=False)

    seg_arrays={"overture_fraction":ov_fraction,"google_fraction":google_fraction,
                "globfp_fraction":gl_fraction,"source_count":source_count.astype("float32"),
                "consensus":consensus.astype("float32"),"wsf_fraction":wsf_fraction,
                "wsf_no_footprint":wsf_gap.astype("float32"),"google_height_m":google_height,
                "gba_height_m":gba_height,"gba_height_valid_fraction":gba_fraction,
                "globfp_height_m":gl_height}
    raster_seg=segment_raster_summary(segments,seg_arrays,t30,af30)
    vector_seg=gpd.read_parquet(out/"segment_vector_summary.parquet")
    combined=vector_seg.merge(raster_seg,on="ANALYSIS_ID",how="left")
    combined.to_parquet(out/"segment_analysis.parquet",index=False)
    combined.to_file(out/"segment_analysis.gpkg",layer="segment_analysis",driver="GPKG")
    grid30={"google_height":google_height,"gba_height":gba_height,
            "globfp_height":gl_height,"wsf_fraction":wsf_fraction}
    grid100={"tempo_height":tempo_height,"wsf3d_height":wsf3d_height}
    integrated=update_integrated(city_slug,grid30,t30,grid100,t100)
    source_rows=[]
    for name in fractions:
        source_rows.append({"source":name,"positive_cells":int(positive[name].sum()),
                            "consensus_recall_proxy_pct":100*float((positive[name]&consensus).sum())/max(1,int(consensus.sum())),
                            "estimated_built_area_km2":float(np.nansum(fractions[name]*RES30**2*af30)/1e6)})
    pd.DataFrame(source_rows).to_csv(out/"source_summary.csv",index=False)
    plot_overview(out/"overview.png",meta["city_name"],source_count,wsf_gap,
                  resample_array(included_range,t100,t30,w30,h30),af30)
    summary={"city_slug":city_slug,"city_name":meta["city_name"],"analysis_cells_30m":int(mask.sum()),
             "google_tiles":len(urls),"consensus_cells":int(consensus.sum()),
             "wsf_settlement_cells":int(wsf_present.sum()),"wsf_no_footprint_cells":int(wsf_gap.sum()),
             "wsf_no_footprint_settled_area_km2":float(np.nansum(np.where(wsf_gap,wsf_fraction*RES30**2*af30,0))/1e6),
             "height_available_buildings":int(integrated.height_best_m.notna().sum()),
             "height_available_pct":float(100*integrated.height_best_m.notna().mean()) if len(integrated) else 0,
             "gba_height_tiles":len(gba_paths),
             "gba_height_available_100m_cells":int(height_valid["GBA.Height"].sum()),
             "gba_height_support":"3D-GloBFP proxy footprint mask at 5 m; area-weighted to 30 m and 100 m",
             "gba_height_dependency":"Excluded from independent-source interpretation because of shared PlanetScope imagery with TEMPO and overlapping footprint/model lineages.",
             "height_interpretation":"Inter-product consistency, not accuracy; no independent reference heights.",
             "sources":"Overture, Google 2.5D, 3D-GloBFP, WSF2019 screen; TEMPO/Google/GBA.Height/3D-GloBFP/optional WSF3D heights"}
    summary_path.write_text(json.dumps(summary,indent=2)+"\n")
    existing=json.loads((OUTPUTS/city_slug/"integrated/summary.json").read_text());existing.update({"raster_analysis":summary})
    (OUTPUTS/city_slug/"integrated/summary.json").write_text(json.dumps(existing,indent=2)+"\n")
    print(json.dumps(summary),flush=True);return summary


def main():
    p=argparse.ArgumentParser();p.add_argument("--city",required=True);p.add_argument("--force",action="store_true");a=p.parse_args();process(a.city,a.force)


if __name__=="__main__": main()
