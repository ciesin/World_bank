#!/usr/bin/env python3
"""Run the Juba 30 m footprint comparison and common-grid height evaluation."""

from __future__ import annotations

import json
import math
from pathlib import Path

import geopandas as gpd
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
from scipy.stats import spearmanr


ROOT = Path(__file__).resolve().parents[1]
AOI_PATH = ROOT / "data/aoi/juba_expanded.geojson"
CRS = "EPSG:32636"
NODATA = -9999.0
HIGH_RES = 30.0
HEIGHT_RES = 100.0
FINE_RES = 3.0
PRIMARY_AREA_THRESHOLD_M2 = 25.0
WSF2019_SETTLEMENT_FRACTION_THRESHOLD = 0.10
FOOTPRINT_SOURCES = [
    "Overture",
    "Google 2.5D",
    "GlobalBuildingAtlas",
    "3D-GloBFP",
]
HEIGHT_SOURCES = ["TEMPO", "Google 2.5D", "GBA.Height", "3D-GloBFP", "WSF 3D v2"]


def make_grid(aoi, resolution):
    xmin, ymin, xmax, ymax = aoi.bounds
    xmin = math.floor(xmin / resolution) * resolution
    ymin = math.floor(ymin / resolution) * resolution
    xmax = math.ceil(xmax / resolution) * resolution
    ymax = math.ceil(ymax / resolution) * resolution
    width = int(round((xmax - xmin) / resolution))
    height = int(round((ymax - ymin) / resolution))
    transform = from_origin(xmin, ymax, resolution, resolution)
    cols, rows = np.meshgrid(np.arange(width), np.arange(height))
    left = xmin + cols.ravel() * resolution
    top = ymax - rows.ravel() * resolution
    cells = shapely.box(left, top - resolution, left + resolution, top)
    intersections = shapely.intersection(cells, aoi)
    aoi_fraction = (shapely.area(intersections) / resolution**2).reshape(height, width)
    return transform, width, height, cells, aoi_fraction, (xmin, ymin, xmax, ymax)


def load_vectors():
    return {
        "Overture": gpd.read_parquet(
            ROOT / "data/processed/overture_juba_expanded.parquet"
        ).to_crs(CRS),
        "GlobalBuildingAtlas": gpd.read_parquet(
            ROOT / "data/processed/global_building_atlas_juba_expanded.parquet"
        ).to_crs(CRS),
        "3D-GloBFP": gpd.read_parquet(
            ROOT / "data/processed/3d_globfp_juba_expanded.parquet"
        ).to_crs(CRS),
    }


def fine_vector_grid(data, transform, width, height, value_column=None):
    """Rasterize at 3 m, then aggregate 10x10 pixels to avoid centroid assignment."""
    factor = int(HIGH_RES / FINE_RES)
    fine_shape = (height * factor, width * factor)
    fine_transform = transform * Affine.scale(1 / factor, 1 / factor)
    valid = data.geometry.notna() & ~data.geometry.is_empty
    data = data.loc[valid]
    presence = rasterize(
        ((geom, 1) for geom in data.geometry),
        out_shape=fine_shape,
        transform=fine_transform,
        fill=0,
        dtype="uint8",
        all_touched=False,
    )
    fraction = presence.reshape(height, factor, width, factor).mean(axis=(1, 3)).astype("float32")
    height_grid = None
    if value_column is not None:
        values = pd.to_numeric(data[value_column], errors="coerce").to_numpy(dtype="float32")
        good = np.isfinite(values) & (values > 0) & (values <= 100)
        height_fine = rasterize(
            ((geom, float(value)) for geom, value in zip(data.geometry[good], values[good])),
            out_shape=fine_shape,
            transform=fine_transform,
            fill=0,
            dtype="float32",
            all_touched=False,
        )
        numerator = (height_fine * presence).reshape(height, factor, width, factor).mean(axis=(1, 3))
        height_grid = np.full_like(fraction, np.nan, dtype="float32")
        np.divide(numerator, fraction, out=height_grid, where=fraction > 0)
    del presence
    return fraction, height_grid


def gba_height_grid(data, paths, transform, width, height):
    """Aggregate native 3 m GBA.Height pixels within GBA footprints to 30 m.

    GBA.Height is a continuous modeled raster.  Restricting it to the fused GBA
    footprint mask avoids treating background predictions as buildings.  The
    resulting mean is weighted by the area of valid 3 m building pixels.
    """
    factor = int(HIGH_RES / FINE_RES)
    fine_shape = (height * factor, width * factor)
    fine_transform = transform * Affine.scale(1 / factor, 1 / factor)
    valid_geom = data.geometry.notna() & ~data.geometry.is_empty
    footprint = rasterize(
        ((geom, 1) for geom in data.loc[valid_geom].geometry),
        out_shape=fine_shape,
        transform=fine_transform,
        fill=0,
        dtype="uint8",
        all_touched=False,
    ).astype(bool)
    height_sum = np.zeros((height, width), dtype="float64")
    valid_count = np.zeros((height, width), dtype="uint16")
    assigned = np.zeros(fine_shape, dtype=bool)
    for path in paths:
        print(f"Projecting GBA.Height {path.name}", flush=True)
        with rasterio.open(path) as src:
            if src.crs is None or src.crs.to_epsg() != 32636:
                raise ValueError(f"Unexpected GBA.Height CRS for {path}: {src.crs}")
            if not np.allclose(src.res, (FINE_RES, FINE_RES)):
                raise ValueError(f"Unexpected GBA.Height resolution for {path}: {src.res}")
            if src.count != 1 or src.nodata != -1.0 or src.dtypes[0] != "float32":
                raise ValueError(
                    f"Unexpected GBA.Height raster schema for {path}: "
                    f"count={src.count}, dtype={src.dtypes[0]}, nodata={src.nodata}"
                )
            projected = np.full(fine_shape, NODATA, dtype="float32")
            reproject(
                source=rasterio.band(src, 1), destination=projected,
                src_transform=src.transform, src_crs=src.crs, src_nodata=src.nodata,
                dst_transform=fine_transform, dst_crs=CRS, dst_nodata=NODATA,
                resampling=Resampling.bilinear,
            )
        valid = footprint & ~assigned & np.isfinite(projected) & (projected > 0) & (projected <= 100)
        assigned[valid] = True
        height_sum += np.where(valid, projected, 0).reshape(
            height, factor, width, factor
        ).sum(axis=(1, 3))
        valid_count += valid.reshape(height, factor, width, factor).sum(axis=(1, 3)).astype("uint16")
        del projected, valid
    mean_height = np.full((height, width), np.nan, dtype="float32")
    np.divide(height_sum, valid_count, out=mean_height, where=valid_count > 0)
    valid_fraction = (valid_count / factor**2).astype("float32")
    del footprint, assigned
    return valid_fraction, mean_height


def google_grid(urls, transform, width, height, bounds):
    fraction_sum = np.zeros((height, width), dtype="float64")
    height_sum = np.zeros((height, width), dtype="float64")
    tile_count = np.zeros((height, width), dtype="uint8")
    xmin, ymin, xmax, ymax = bounds
    with rasterio.Env(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
        GDAL_HTTP_MULTIPLEX="YES",
        GDAL_CACHEMAX=512,
    ):
        for index, url in enumerate(urls, 1):
            print(f"Google tile {index}/{len(urls)}", flush=True)
            with rasterio.open(url) as src:
                left, bottom = max(xmin, src.bounds.left), max(ymin, src.bounds.bottom)
                right, top = min(xmax, src.bounds.right), min(ymax, src.bounds.top)
                if left >= right or bottom >= top:
                    continue
                raw = from_bounds(left, bottom, right, top, src.transform)
                window = Window(
                    max(0, math.floor(raw.col_off)),
                    max(0, math.floor(raw.row_off)),
                    min(src.width, math.ceil(raw.col_off + raw.width)) - max(0, math.floor(raw.col_off)),
                    min(src.height, math.ceil(raw.row_off + raw.height)) - max(0, math.floor(raw.row_off)),
                )
                out_width = max(1, math.ceil(window.width / 8))
                out_height = max(1, math.ceil(window.height / 8))
                presence = src.read(3, window=window, out_shape=(out_height, out_width), masked=True,
                                    resampling=Resampling.bilinear)
                building_height = src.read(2, window=window, out_shape=(out_height, out_width), masked=True,
                                           resampling=Resampling.bilinear)
                src_transform = src.window_transform(window) * Affine.scale(
                    window.width / out_width, window.height / out_height
                )
                valid = ~np.ma.getmaskarray(presence) & ~np.ma.getmaskarray(building_height)
                p = np.asarray(presence.filled(0), dtype="float32")
                h = np.asarray(building_height.filled(0), dtype="float32")
                building = valid & (p >= 0.5) & (h > 0) & (h <= 100)
                source_fraction = building.astype("float32")
                source_height_sum = np.where(building, h, 0).astype("float32")
                projected = []
                for source_values in (source_fraction, source_height_sum):
                    temp = np.full((height, width), NODATA, dtype="float32")
                    reproject(
                        source=source_values,
                        destination=temp,
                        src_transform=src_transform,
                        src_crs=src.crs,
                        src_nodata=None,
                        dst_transform=transform,
                        dst_crs=CRS,
                        dst_nodata=NODATA,
                        resampling=Resampling.average,
                    )
                    projected.append(temp)
                tile_valid = projected[0] != NODATA
                fraction_sum[tile_valid] += projected[0][tile_valid]
                height_sum[tile_valid] += projected[1][tile_valid]
                tile_count[tile_valid] += 1
    fraction = np.full((height, width), np.nan, dtype="float32")
    valid = tile_count > 0
    fraction[valid] = (fraction_sum[valid] / tile_count[valid]).astype("float32")
    numerator = np.zeros_like(fraction)
    numerator[valid] = (height_sum[valid] / tile_count[valid]).astype("float32")
    mean_height = np.full_like(fraction, np.nan)
    np.divide(numerator, fraction, out=mean_height, where=fraction > 0)
    return fraction, mean_height


def project_file(paths, band, transform, width, height, scale=1.0):
    total = np.zeros((height, width), dtype="float64")
    count = np.zeros((height, width), dtype="uint16")
    for path in paths:
        with rasterio.open(path) as src:
            temp = np.full((height, width), NODATA, dtype="float32")
            reproject(
                source=rasterio.band(src, band), destination=temp,
                src_transform=src.transform, src_crs=src.crs, src_nodata=src.nodata,
                dst_transform=transform, dst_crs=CRS, dst_nodata=NODATA,
                resampling=Resampling.average,
            )
            valid = temp != NODATA
            total[valid] += temp[valid] * scale
            count[valid] += 1
    result = np.full((height, width), np.nan, dtype="float32")
    valid = count > 0
    result[valid] = (total[valid] / count[valid]).astype("float32")
    return result


def project_array(values, src_transform, dst_transform, width, height):
    out = np.full((height, width), np.nan, dtype="float32")
    reproject(
        source=values, destination=out, src_transform=src_transform, src_crs=CRS,
        src_nodata=np.nan, dst_transform=dst_transform, dst_crs=CRS, dst_nodata=np.nan,
        resampling=Resampling.average,
    )
    return out


def write_tif(path, arrays, transform, aoi_fraction):
    first = next(iter(arrays.values()))
    profile = dict(driver="GTiff", height=first.shape[0], width=first.shape[1],
                   count=len(arrays), dtype="float32", crs=CRS, transform=transform,
                   nodata=NODATA, compress="deflate", tiled=True)
    with rasterio.open(path, "w", **profile) as dst:
        for i, (name, values) in enumerate(arrays.items(), 1):
            data = values.astype("float32").copy()
            data[(aoi_fraction <= 0) | ~np.isfinite(data)] = NODATA
            dst.write(data, i)
            dst.set_band_description(i, name)


def pairwise_presence(fractions, positives, mask):
    rows = []
    names = list(fractions)
    for i, left in enumerate(names):
        for right in names[i + 1:]:
            union = positives[left] | positives[right]
            common = mask & np.isfinite(fractions[left]) & np.isfinite(fractions[right]) & union
            corr = spearmanr(fractions[left][common], fractions[right][common]).statistic if common.sum() > 2 else np.nan
            rows.append({"source_a": left, "source_b": right,
                         "jaccard_positive_cells": float((positives[left] & positives[right]).sum()) / max(1, int(union.sum())),
                         "spearman_fraction_on_union": corr})
    return pd.DataFrame(rows)


def height_statistics(heights, fractions, mask, resolution, label):
    summary, pairs = [], []
    valid_by_source = {}
    min_fraction = 50.0 / resolution**2
    for name, values in heights.items():
        valid = mask & np.isfinite(values) & (values > 0) & (values <= 100) & (fractions[name] >= min_fraction)
        valid_by_source[name] = valid
        volume = fractions[name] * resolution**2 * values
        summary.append({
            "grid": label, "source": name, "valid_height_cells": int(valid.sum()),
            "height_coverage_of_aoi_pct": 100 * float(valid.sum()) / max(1, int(mask.sum())),
            "median_height_m": float(np.nanmedian(values[valid])) if valid.any() else np.nan,
            "p90_height_m": float(np.nanquantile(values[valid], 0.9)) if valid.any() else np.nan,
            "estimated_built_volume_m3": float(np.nansum(np.where(valid, volume, 0))),
        })
    names = list(heights)
    for i, left in enumerate(names):
        for right in names[i + 1:]:
            common = valid_by_source[left] & valid_by_source[right]
            a, b = heights[left][common], heights[right][common]
            d = a - b
            class_a = np.digitize(a, [3.0, 6.0, 10.0])
            class_b = np.digitize(b, [3.0, 6.0, 10.0])
            pairs.append({
                "grid": label, "source_a": left, "source_b": right, "common_cells": int(common.sum()),
                "mean_bias_a_minus_b_m": float(np.mean(d)) if len(d) else np.nan,
                "mae_m": float(np.mean(np.abs(d))) if len(d) else np.nan,
                "rmse_m": float(np.sqrt(np.mean(d**2))) if len(d) else np.nan,
                "spearman_height": float(spearmanr(a, b).statistic) if len(d) > 2 else np.nan,
                "same_height_class_pct": 100 * float(np.mean(class_a == class_b)) if len(d) else np.nan,
            })
    return pd.DataFrame(summary), pd.DataFrame(pairs)


def plot_footprints(path, fractions, aoi_fraction, bounds):
    fig, axes = plt.subplots(2, 2, figsize=(11, 8), constrained_layout=True)
    positive = np.concatenate([v[(aoi_fraction > 0) & np.isfinite(v) & (v > 0)] for v in fractions.values()])
    vmax = max(0.05, float(np.quantile(positive, 0.98)))
    image = None
    for ax, name in zip(axes.ravel(), FOOTPRINT_SOURCES):
        values = fractions[name].copy(); values[aoi_fraction <= 0] = np.nan
        extent = (bounds[0], bounds[2], bounds[1], bounds[3])
        image = ax.imshow(values, extent=extent, origin="upper", vmin=0, vmax=vmax, cmap="magma")
        ax.set_title(name); ax.set_xticks([]); ax.set_yticks([])
    fig.colorbar(image, ax=axes, label="Estimated building fraction per 30 m cell", shrink=0.8)
    fig.savefig(path, dpi=180); plt.close(fig)


def plot_heights(path, heights, aoi_fraction, bounds):
    ncols = 3
    nrows = math.ceil(len(HEIGHT_SOURCES) / ncols)
    fig, axes = plt.subplots(nrows, ncols, figsize=(14, 8), constrained_layout=True)
    image = None
    for ax, name in zip(axes.ravel(), HEIGHT_SOURCES):
        values = heights[name].copy(); values[aoi_fraction <= 0] = np.nan
        extent = (bounds[0], bounds[2], bounds[1], bounds[3])
        image = ax.imshow(values, extent=extent, origin="upper", vmin=1, vmax=10, cmap="viridis")
        ax.set_title(name); ax.set_xticks([]); ax.set_yticks([])
    for ax in axes.ravel()[len(HEIGHT_SOURCES):]:
        ax.set_visible(False)
    fig.colorbar(image, ax=axes, label="Mean building height (m), clipped display range 1–10 m", shrink=0.8)
    fig.savefig(path, dpi=180); plt.close(fig)


def plot_gaps(path, positives, consensus, aoi_fraction, bounds):
    fig, axes = plt.subplots(2, 2, figsize=(11, 8), constrained_layout=True)
    extent = (bounds[0], bounds[2], bounds[1], bounds[3])
    for ax, name in zip(axes.ravel(), FOOTPRINT_SOURCES):
        context = np.where((aoi_fraction > 0) & consensus, 1.0, np.nan)
        gaps = np.where(consensus & ~positives[name], 1.0, np.nan)
        ax.imshow(context, extent=extent, origin="upper", vmin=0, vmax=1, cmap="Greys", alpha=0.25)
        ax.imshow(gaps, extent=extent, origin="upper", vmin=0, vmax=1, cmap="Reds")
        ax.set_title(f"{name} gaps"); ax.set_xticks([]); ax.set_yticks([])
    fig.suptitle("Red: source absent where at least two of four products indicate buildings")
    fig.savefig(path, dpi=180, bbox_inches="tight"); plt.close(fig)


def plot_height_diagnostics(path, legacy_count, all_count, legacy_range, all_range, aoi_fraction, bounds):
    fig, axes = plt.subplots(2, 2, figsize=(11, 8), constrained_layout=True)
    extent = (bounds[0], bounds[2], bounds[1], bounds[3])
    displays = [legacy_count.astype("float32"), all_count.astype("float32"), legacy_range.copy(), all_range.copy()]
    for values in displays: values[aoi_fraction <= 0] = np.nan
    images = [
        axes[0, 0].imshow(displays[0], extent=extent, origin="upper", vmin=1, vmax=4, cmap="viridis"),
        axes[0, 1].imshow(displays[1], extent=extent, origin="upper", vmin=1, vmax=5, cmap="viridis"),
        axes[1, 0].imshow(displays[2], extent=extent, origin="upper", vmin=0, vmax=5, cmap="magma"),
        axes[1, 1].imshow(displays[3], extent=extent, origin="upper", vmin=0, vmax=5, cmap="magma"),
    ]
    titles = ["Valid sources (GBA excluded)", "Valid products (GBA included)",
              "Height range (GBA excluded)", "Height range (GBA included)"]
    for ax, title in zip(axes.ravel(), titles): ax.set_title(title); ax.set_xticks([]); ax.set_yticks([])
    fig.colorbar(images[0], ax=axes[0, 0], label="Source count", shrink=0.8)
    fig.colorbar(images[1], ax=axes[0, 1], label="Product count", shrink=0.8)
    fig.colorbar(images[2], ax=axes[1, 0], label="Range (m), clipped at 5 m", shrink=0.8)
    fig.colorbar(images[3], ax=axes[1, 1], label="Range (m), clipped at 5 m", shrink=0.8)
    fig.savefig(path, dpi=180); plt.close(fig)


def plot_wsf_screen(path, wsf_fraction, source_count, wsf_no_footprint, aoi_fraction, bounds):
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.4), constrained_layout=True)
    extent = (bounds[0], bounds[2], bounds[1], bounds[3])
    settlement = wsf_fraction.copy(); settlement[aoi_fraction <= 0] = np.nan
    count = source_count.astype("float32"); count[aoi_fraction <= 0] = np.nan
    gaps = np.where(wsf_no_footprint, wsf_fraction, np.nan)
    left = axes[0].imshow(settlement, extent=extent, origin="upper", vmin=0, vmax=1, cmap="Greys")
    middle = axes[1].imshow(count, extent=extent, origin="upper", vmin=0, vmax=4, cmap="viridis")
    right = axes[2].imshow(gaps, extent=extent, origin="upper", vmin=0, vmax=1, cmap="Reds")
    axes[0].set_title("WSF 2019 settlement fraction")
    axes[1].set_title("Positive footprint sources")
    axes[2].set_title("WSF settlement; no footprints")
    for ax in axes: ax.set_xticks([]); ax.set_yticks([])
    fig.colorbar(left, ax=axes[0], label="Settlement fraction", shrink=0.75)
    fig.colorbar(middle, ax=axes[1], label="Source count", shrink=0.75)
    fig.colorbar(right, ax=axes[2], label="WSF fraction", shrink=0.75)
    fig.savefig(path, dpi=180, bbox_inches="tight"); plt.close(fig)


def main():
    out = ROOT / "outputs"; out.mkdir(exist_ok=True)
    aoi = gpd.read_file(AOI_PATH).to_crs(CRS).geometry.union_all()
    t30, w30, h30, cells30, af30, bounds30 = make_grid(aoi, HIGH_RES)
    mask30 = af30 >= 0.5
    vectors = load_vectors()

    fractions30, vector_height30 = {}, {}
    for name, data in vectors.items():
        print(f"Fine rasterizing {name}: {len(data):,} features", flush=True)
        fraction, height = fine_vector_grid(data, t30, w30, h30, "Height" if name == "3D-GloBFP" else None)
        fractions30[name] = fraction
        if height is not None: vector_height30[name] = height
    urls = [line.strip() for line in (ROOT / "data/raw/google_2_5d/urls_expanded.txt").read_text().splitlines() if line.strip()]
    google_fraction30, google_height30 = google_grid(urls, t30, w30, h30, bounds30)
    fractions30["Google 2.5D"] = google_fraction30
    fractions30 = {name: fractions30[name] for name in FOOTPRINT_SOURCES}
    gba_height_paths = sorted((ROOT / "data/raw/gba_height/juba").glob("*.tif"))
    if len(gba_height_paths) != 4:
        raise FileNotFoundError(
            "Expected four Juba GBA.Height tiles; run scripts/download_gba_height_juba.py"
        )
    gba_height_fraction30, gba_height30 = gba_height_grid(
        vectors["GlobalBuildingAtlas"], gba_height_paths, t30, w30, h30
    )

    threshold_fraction30 = PRIMARY_AREA_THRESHOLD_M2 / HIGH_RES**2
    positives30 = {name: mask30 & np.isfinite(v) & (v >= threshold_fraction30) for name, v in fractions30.items()}
    source_count30 = sum(v.astype("uint8") for v in positives30.values())
    consensus30 = mask30 & (source_count30 >= 2)
    gap_count30 = sum((consensus30 & ~v).astype("uint8") for v in positives30.values())

    # WSF 2019 is an external settlement screen, not a member of the footprint consensus.
    wsf2019_fraction = np.clip(project_file(
        [ROOT / "data/raw/wsf2019/WSF2019_v1_30_4.tif"], 1, t30, w30, h30, scale=1 / 255.0
    ), 0, 1)
    wsf2019_present = mask30 & np.isfinite(wsf2019_fraction) & (
        wsf2019_fraction >= WSF2019_SETTLEMENT_FRACTION_THRESHOLD
    )
    any_footprint_present = mask30 & (source_count30 > 0)
    wsf2019_no_footprint = wsf2019_present & ~any_footprint_present
    wsf2019_no_footprint_settled_area = np.where(
        wsf2019_no_footprint, wsf2019_fraction * HIGH_RES**2 * af30, 0
    ).astype("float32")

    summary_rows = []
    for name in FOOTPRINT_SOURCES:
        pos = positives30[name]
        summary_rows.append({"source": name, "positive_30m_cells": int(pos.sum()),
                             "consensus_recall_proxy_pct": 100 * float((pos & consensus30).sum()) / max(1, int(consensus30.sum())),
                             "gap_30m_cells": int((consensus30 & ~pos).sum()),
                             "estimated_built_area_km2": float(np.nansum(fractions30[name] * HIGH_RES**2 * af30) / 1e6)})
    pd.DataFrame(summary_rows).sort_values("consensus_recall_proxy_pct", ascending=False).to_csv(
        out / "juba_30m_source_summary.csv", index=False)
    pairwise_presence(fractions30, positives30, mask30).to_csv(out / "juba_30m_pairwise_agreement.csv", index=False)

    sensitivity = []
    for area_threshold in (10.0, 25.0, 50.0):
        p = {n: mask30 & np.isfinite(v) & (v >= area_threshold / HIGH_RES**2) for n, v in fractions30.items()}
        consensus = mask30 & (sum(x.astype("uint8") for x in p.values()) >= 2)
        for name in FOOTPRINT_SOURCES:
            sensitivity.append({"building_area_threshold_m2": area_threshold, "source": name,
                                "consensus_cells": int(consensus.sum()), "positive_cells": int(p[name].sum()),
                                "consensus_recall_proxy_pct": 100 * float((p[name] & consensus).sum()) / max(1, int(consensus.sum())),
                                "gap_cells": int((consensus & ~p[name]).sum())})
    pd.DataFrame(sensitivity).to_csv(out / "juba_30m_threshold_sensitivity.csv", index=False)

    wsf_rows = []
    total_wsf_area = float(np.nansum(np.where(wsf2019_present, wsf2019_fraction * HIGH_RES**2 * af30, 0)))
    for name in FOOTPRINT_SOURCES:
        absent = wsf2019_present & ~positives30[name]
        absent_settled_area = float(np.nansum(np.where(absent, wsf2019_fraction * HIGH_RES**2 * af30, 0)))
        wsf_rows.append({
            "screen": f"WSF 2019 settled, {name} absent",
            "source": name,
            "screen_cells": int(absent.sum()),
            "screen_support_area_km2": float(np.sum(af30[absent]) * HIGH_RES**2 / 1e6),
            "estimated_wsf_settled_area_km2": absent_settled_area / 1e6,
            "pct_of_total_wsf_settled_area": 100 * absent_settled_area / total_wsf_area if total_wsf_area else np.nan,
        })
    no_fp_area = float(wsf2019_no_footprint_settled_area.sum())
    wsf_rows.append({
        "screen": "WSF 2019 settled, all four footprint sources absent",
        "source": "None of four footprint sources",
        "screen_cells": int(wsf2019_no_footprint.sum()),
        "screen_support_area_km2": float(np.sum(af30[wsf2019_no_footprint]) * HIGH_RES**2 / 1e6),
        "estimated_wsf_settled_area_km2": no_fp_area / 1e6,
        "pct_of_total_wsf_settled_area": 100 * no_fp_area / total_wsf_area if total_wsf_area else np.nan,
    })
    pd.DataFrame(wsf_rows).to_csv(out / "juba_30m_wsf2019_gap_summary.csv", index=False)

    raster30 = dict(fractions30)
    raster30["source agreement count"] = source_count30.astype("float32")
    raster30["consensus"] = consensus30.astype("float32")
    raster30["source gap count"] = gap_count30.astype("float32")
    raster30["WSF 2019 settlement fraction"] = wsf2019_fraction
    raster30["WSF 2019 settlement present"] = wsf2019_present.astype("float32")
    raster30["any footprint source present"] = any_footprint_present.astype("float32")
    raster30["WSF 2019 settlement no footprints"] = wsf2019_no_footprint.astype("float32")
    raster30["WSF 2019 no-footprint settled area m2"] = wsf2019_no_footprint_settled_area
    raster30["Google mean height m"] = google_height30
    raster30["GBA.Height footprint-weighted mean height m"] = gba_height30
    raster30["GBA.Height valid building fraction"] = gba_height_fraction30
    raster30["3D-GloBFP mean height m"] = vector_height30["3D-GloBFP"]
    write_tif(out / "juba_30m_comparison.tif", raster30, t30, af30)

    grid = gpd.GeoDataFrame({"row": np.repeat(np.arange(h30), w30), "col": np.tile(np.arange(w30), h30),
                             "aoi_fraction": af30.ravel(), "source_count": source_count30.ravel(),
                             "consensus": consensus30.ravel().astype("uint8"), "gap_count": gap_count30.ravel()},
                            geometry=cells30, crs=CRS)
    fields = {"Overture": "overture", "Google 2.5D": "google25d", "GlobalBuildingAtlas": "gba", "3D-GloBFP": "globfp3d"}
    for name, field in fields.items():
        grid[f"{field}_fraction"] = fractions30[name].ravel()
        grid[f"{field}_present"] = positives30[name].ravel().astype("uint8")
        grid[f"{field}_gap"] = (consensus30 & ~positives30[name]).ravel().astype("uint8")
    grid["google_height_m"] = google_height30.ravel()
    grid["gba_height_m"] = gba_height30.ravel()
    grid["gba_height_valid_fraction"] = gba_height_fraction30.ravel()
    grid["globfp_height_m"] = vector_height30["3D-GloBFP"].ravel()
    grid["wsf2019_settlement_fraction"] = wsf2019_fraction.ravel()
    grid["wsf2019_settlement_present"] = wsf2019_present.ravel().astype("uint8")
    grid["any_footprint_present"] = any_footprint_present.ravel().astype("uint8")
    grid["wsf2019_no_footprint"] = wsf2019_no_footprint.ravel().astype("uint8")
    grid["wsf2019_no_footprint_settled_area_m2"] = wsf2019_no_footprint_settled_area.ravel()
    grid = grid[grid.aoi_fraction > 0].copy()
    grid.to_parquet(out / "juba_30m_comparison_grid.parquet", index=False)
    grid.to_file(out / "juba_30m_comparison_grid.gpkg", layer="comparison_30m", driver="GPKG")
    plot_footprints(out / "juba_30m_source_fractions.png", fractions30, af30, bounds30)
    plot_gaps(out / "juba_30m_source_gaps.png", positives30, consensus30, af30, bounds30)
    plot_wsf_screen(
        out / "juba_30m_wsf2019_settlement_gaps.png", wsf2019_fraction,
        source_count30, wsf2019_no_footprint, af30, bounds30
    )

    # Height comparison at 100 m, the coarsest common analytical support.
    t100, w100, h100, cells100, af100, bounds100 = make_grid(aoi, HEIGHT_RES)
    mask100 = af100 >= 0.5
    tempo_paths = sorted((ROOT / "data/raw/tempo_2023q4").glob("*.tif"))
    tempo_fraction = np.clip(project_file(tempo_paths, 1, t100, w100, h100), 0, 1)
    tempo_height = project_file(tempo_paths, 2, t100, w100, h100, scale=100.0)
    wsf_fraction = np.clip(project_file([
        ROOT / "data/raw/wsf3d/expanded/wsf3d_v02_building_fraction_juba_expanded_raw.tif"
    ], 1, t100, w100, h100, scale=0.01), 0, 1)
    wsf_height = project_file([
        ROOT / "data/raw/wsf3d/expanded/wsf3d_v02_building_height_juba_expanded_raw.tif"
    ], 1, t100, w100, h100)

    google_fraction100 = project_array(google_fraction30, t30, t100, w100, h100)
    google_numerator100 = project_array(np.nan_to_num(google_fraction30 * google_height30), t30, t100, w100, h100)
    google_height100 = np.full_like(google_fraction100, np.nan)
    np.divide(google_numerator100, google_fraction100, out=google_height100, where=google_fraction100 > 0)
    gba_fraction100 = project_array(gba_height_fraction30, t30, t100, w100, h100)
    gba_numerator100 = project_array(
        np.nan_to_num(gba_height_fraction30 * gba_height30), t30, t100, w100, h100
    )
    gba_height100 = np.full_like(gba_fraction100, np.nan)
    np.divide(gba_numerator100, gba_fraction100, out=gba_height100, where=gba_fraction100 > 0)
    glob_fraction30 = fractions30["3D-GloBFP"]
    glob_fraction100 = project_array(glob_fraction30, t30, t100, w100, h100)
    glob_numerator100 = project_array(np.nan_to_num(glob_fraction30 * vector_height30["3D-GloBFP"]), t30, t100, w100, h100)
    glob_height100 = np.full_like(glob_fraction100, np.nan)
    np.divide(glob_numerator100, glob_fraction100, out=glob_height100, where=glob_fraction100 > 0)

    heights100 = {"TEMPO": tempo_height, "Google 2.5D": google_height100,
                  "GBA.Height": gba_height100,
                  "3D-GloBFP": glob_height100, "WSF 3D v2": wsf_height}
    height_fractions100 = {"TEMPO": tempo_fraction, "Google 2.5D": google_fraction100,
                           "GBA.Height": gba_fraction100,
                           "3D-GloBFP": glob_fraction100, "WSF 3D v2": wsf_fraction}
    height_summary100, height_pairs100 = height_statistics(heights100, height_fractions100, mask100, HEIGHT_RES, "100m")
    height_summary30, height_pairs30 = height_statistics(
        {"Google 2.5D": google_height30, "GBA.Height": gba_height30,
         "3D-GloBFP": vector_height30["3D-GloBFP"]},
        {"Google 2.5D": google_fraction30, "GBA.Height": gba_height_fraction30,
         "3D-GloBFP": glob_fraction30}, mask30, HIGH_RES, "30m")
    pd.concat([height_summary100, height_summary30], ignore_index=True).to_csv(out / "juba_height_source_summary.csv", index=False)
    pd.concat([height_pairs100, height_pairs30], ignore_index=True).to_csv(out / "juba_height_pairwise_agreement.csv", index=False)

    availability = pd.DataFrame([
        {"source": "TEMPO", "height_status": "evaluated", "coverage_note": "Band 2, modeled height; native support about 76 m"},
        {"source": "Google 2.5D", "height_status": "evaluated", "coverage_note": "Building-height band; effective support about 4 m"},
        {"source": "3D-GloBFP", "height_status": "evaluated", "coverage_note": "Height populated for all 503,016 Juba footprints"},
        {"source": "WSF 3D v2", "height_status": "evaluated", "coverage_note": "Separate average-height layer; native support 90 m"},
        {"source": "Overture", "height_status": "not evaluated", "coverage_note": "Height and floor attributes remain too sparse for a defensible surface-level comparison"},
        {"source": "GBA.Height", "height_status": "evaluated", "coverage_note": "Native 3 m modeled height restricted to GBA footprints; 2025 release, predominantly 2019 PlanetScope imagery; CC BY-NC 4.0"},
    ])
    availability.to_csv(out / "juba_height_availability.csv", index=False)
    height_raster = {}
    height_valid = {}
    for name in HEIGHT_SOURCES:
        height_raster[f"{name} mean height m"] = heights100[name]
        height_raster[f"{name} built volume m3"] = heights100[name] * height_fractions100[name] * HEIGHT_RES**2
        height_valid[name] = (mask100 & np.isfinite(heights100[name]) & (heights100[name] > 0)
                              & (heights100[name] <= 100) & (height_fractions100[name] >= 50.0 / HEIGHT_RES**2))
    def count_and_range(names):
        count = sum(height_valid[name].astype("uint8") for name in names)
        minimum = np.min(np.stack([
            np.where(height_valid[name], heights100[name], np.inf) for name in names
        ]), axis=0)
        maximum = np.max(np.stack([
            np.where(height_valid[name], heights100[name], -np.inf) for name in names
        ]), axis=0)
        value_range = maximum - minimum
        value_range[count < 2] = np.nan
        return count, value_range.astype("float32")

    legacy_height_sources = [name for name in HEIGHT_SOURCES if name != "GBA.Height"]
    legacy_source_count, legacy_height_range = count_and_range(legacy_height_sources)
    all_product_count, all_height_range = count_and_range(HEIGHT_SOURCES)
    height_raster["valid height source count GBA excluded"] = legacy_source_count.astype("float32")
    height_raster["inter-source height range m GBA excluded"] = legacy_height_range
    height_raster["valid height product count GBA included"] = all_product_count.astype("float32")
    height_raster["inter-product height range m GBA included"] = all_height_range
    write_tif(out / "juba_100m_height_comparison.tif", height_raster, t100, af100)
    plot_heights(out / "juba_100m_height_comparison.png", heights100, af100, bounds100)
    plot_height_diagnostics(
        out / "juba_100m_height_diagnostics.png", legacy_source_count, all_product_count,
        legacy_height_range, all_height_range, af100, bounds100
    )

    sensitivity_rows = []
    for label, names, count, value_range in (
        ("GBA excluded", legacy_height_sources, legacy_source_count, legacy_height_range),
        ("GBA included", HEIGHT_SOURCES, all_product_count, all_height_range),
    ):
        comparable = mask100 & (count >= 2) & np.isfinite(value_range)
        sensitivity_rows.append({
            "scenario": label,
            "products": "; ".join(names),
            "comparable_100m_cells": int(comparable.sum()),
            "median_inter_product_range_m": float(np.nanmedian(value_range[comparable])),
            "p90_inter_product_range_m": float(np.nanquantile(value_range[comparable], 0.9)),
            "mean_valid_product_count": float(np.mean(count[mask100])),
        })
    pd.DataFrame(sensitivity_rows).to_csv(out / "juba_height_sensitivity.csv", index=False)

    hotspot_mask = mask100 & np.isfinite(all_height_range)
    hotspot = gpd.GeoDataFrame({
        "row": np.repeat(np.arange(h100), w100),
        "col": np.tile(np.arange(w100), h100),
        "aoi_fraction": af100.ravel(),
        "valid_count_gba_excluded": legacy_source_count.ravel(),
        "valid_count_gba_included": all_product_count.ravel(),
        "range_m_gba_excluded": legacy_height_range.ravel(),
        "range_m_gba_included": all_height_range.ravel(),
        "range_change_m": (all_height_range - legacy_height_range).ravel(),
    }, geometry=cells100, crs=CRS)
    for name in HEIGHT_SOURCES:
        hotspot[f"{name.lower().replace(' ', '_').replace('.', '')}_height_m"] = heights100[name].ravel()
    hotspot = hotspot.loc[hotspot_mask.ravel()].nlargest(100, "range_m_gba_included").copy()
    hotspot.to_file(out / "juba_height_hotspots_top100.gpkg", layer="height_hotspots_100m", driver="GPKG")
    hotspot.drop(columns="geometry").to_csv(out / "juba_height_hotspots_top100.csv", index=False)

    metadata = {
        "aoi_area_km2": float(aoi.area / 1e6), "analysis_crs": CRS,
        "aoi_definition": "Union of 14,759 Juba features in segments_hexbin_20260821.gpkg",
        "high_resolution_grid_m": HIGH_RES, "high_resolution_analysis_cells": int(mask30.sum()),
        "primary_presence_area_threshold_m2": PRIMARY_AREA_THRESHOLD_M2,
        "primary_presence_fraction_threshold_30m": threshold_fraction30,
        "high_resolution_consensus_cells": int(consensus30.sum()),
        "high_resolution_consensus_definition": "At least two of four products positive; interpret cautiously because vector products share upstream inputs.",
        "wsf2019_role": "Independent settlement screen; excluded from the footprint consensus.",
        "wsf2019_positive_definition": "At least 10% settlement fraction in a 30 m cell, approximately one native 10 m WSF pixel.",
        "wsf2019_no_footprint_definition": "WSF-positive 30 m cell with no positive footprint source under the 25 m2 threshold.",
        "wsf2019_settlement_cells": int(wsf2019_present.sum()),
        "wsf2019_settlement_area_km2": total_wsf_area / 1e6,
        "wsf2019_no_footprint_cells": int(wsf2019_no_footprint.sum()),
        "wsf2019_no_footprint_settled_area_km2": no_fp_area / 1e6,
        "vector_fraction_method": "Footprints rasterized at 3 m and aggregated to 30 m.",
        "height_comparison_grid_m": HEIGHT_RES,
        "height_validity_filter": "Positive height <=100 m and at least 50 m2 inferred building area per common-grid cell.",
        "gba_height_method": "Native 3 m GBA.Height values restricted to GlobalBuildingAtlas footprints, aggregated by valid 3 m building-pixel area to 30 m, then by valid building area to 100 m.",
        "gba_height_units": "metres",
        "gba_height_nodata": "Source nodata is -1; only finite values >0 and <=100 m within GBA footprints are retained.",
        "gba_height_release": "mediaTUM DOI 10.14459/2025mp1782307; publication 2025-09-02; production ended 2025-04-30; imagery predominantly 2019 PlanetScope with 2018 supplementation.",
        "gba_height_license": "CC BY-NC 4.0; attribution required and commercial use prohibited.",
        "gba_dependency": "Excluded from independent-source counts because GBA.Height and TEMPO share PlanetScope imagery; GBA LoD1/footprints also overlap Google, Microsoft, and OSM-derived lineages.",
        "height_sensitivity": "Reported with GBA excluded and included; all metrics are inter-product consistency, not accuracy.",
        "interpretation": "Inter-product consistency only; no independent reference heights were available.",
    }
    (out / "juba_30m_height_analysis_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(pd.DataFrame(summary_rows).sort_values("consensus_recall_proxy_pct", ascending=False).to_string(index=False), flush=True)
    print(height_summary100.to_string(index=False), flush=True)
    print(json.dumps(metadata, indent=2), flush=True)


if __name__ == "__main__":
    main()
