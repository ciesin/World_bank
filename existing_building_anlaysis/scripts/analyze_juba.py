#!/usr/bin/env python3
"""Harmonize six building products over the Juba pilot AOI."""

from __future__ import annotations

import json
import math
from collections import Counter
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
RESOLUTION = 100.0
PRESENCE_THRESHOLD = 0.005  # 50 square metres of inferred building per 100 m cell.
NODATA = -9999.0

SOURCE_ORDER = [
    "TEMPO",
    "Overture",
    "Google 2.5D",
    "GlobalBuildingAtlas",
    "3D-GloBFP",
    "WSF 3D v2",
]


def make_grid(aoi):
    xmin, ymin, xmax, ymax = aoi.bounds
    xmin = math.floor(xmin / RESOLUTION) * RESOLUTION
    ymin = math.floor(ymin / RESOLUTION) * RESOLUTION
    xmax = math.ceil(xmax / RESOLUTION) * RESOLUTION
    ymax = math.ceil(ymax / RESOLUTION) * RESOLUTION
    width = int(round((xmax - xmin) / RESOLUTION))
    height = int(round((ymax - ymin) / RESOLUTION))
    transform = from_origin(xmin, ymax, RESOLUTION, RESOLUTION)

    cols, rows = np.meshgrid(np.arange(width), np.arange(height))
    left = xmin + cols.ravel() * RESOLUTION
    right = left + RESOLUTION
    top = ymax - rows.ravel() * RESOLUTION
    bottom = top - RESOLUTION
    cells = shapely.box(left, bottom, right, top)
    intersections = shapely.intersection(cells, aoi)
    aoi_fraction = (shapely.area(intersections) / (RESOLUTION**2)).reshape(height, width)
    return transform, width, height, cells, aoi_fraction, (xmin, ymin, xmax, ymax)


def clip_overture(aoi):
    source = ROOT / "data/raw/overture/juba_buildings.parquet"
    data = gpd.read_parquet(source, columns=["id", "sources", "geometry"]).to_crs(CRS)
    data = data[data.geometry.intersects(aoi)].copy()
    data.geometry = data.geometry.intersection(aoi)
    data = data[~data.geometry.is_empty]
    data.to_parquet(ROOT / "data/processed/overture_juba.parquet", index=False)
    return data


def load_vectors(aoi):
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


def vector_to_grid(data, transform, width, height, bounds):
    data = data[~data.geometry.is_empty & data.geometry.notna()].copy()
    areas = data.geometry.area.to_numpy()
    points = data.geometry.representative_point()
    xmin, _, _, ymax = bounds
    cols = np.floor((points.x.to_numpy() - xmin) / RESOLUTION).astype(int)
    rows = np.floor((ymax - points.y.to_numpy()) / RESOLUTION).astype(int)
    valid = (cols >= 0) & (cols < width) & (rows >= 0) & (rows < height)

    area_grid = np.zeros((height, width), dtype="float64")
    count_grid = np.zeros((height, width), dtype="int32")
    np.add.at(area_grid, (rows[valid], cols[valid]), areas[valid])
    np.add.at(count_grid, (rows[valid], cols[valid]), 1)
    fraction = np.clip(area_grid / (RESOLUTION**2), 0, 1).astype("float32")

    # Presence uses every touched cell so buildings crossing a cell edge are not lost.
    presence = rasterize(
        ((geom, 1) for geom in data.geometry),
        out_shape=(height, width),
        transform=transform,
        fill=0,
        dtype="uint8",
        all_touched=True,
    ).astype(bool)
    return fraction, count_grid, presence, areas


def reproject_rasters(paths, band, transform, width, height, scale=1.0):
    total = np.zeros((height, width), dtype="float64")
    count = np.zeros((height, width), dtype="uint16")
    for path in paths:
        with rasterio.open(path) as src:
            temp = np.full((height, width), NODATA, dtype="float32")
            reproject(
                source=rasterio.band(src, band),
                destination=temp,
                src_transform=src.transform,
                src_crs=src.crs,
                src_nodata=src.nodata,
                dst_transform=transform,
                dst_crs=CRS,
                dst_nodata=NODATA,
                resampling=Resampling.average,
            )
            valid = temp != NODATA
            total[valid] += temp[valid] * scale
            count[valid] += 1
    result = np.full((height, width), np.nan, dtype="float32")
    valid = count > 0
    result[valid] = (total[valid] / count[valid]).astype("float32")
    return result


def google_to_grid(urls, transform, width, height, grid_bounds):
    layers = {
        "confidence": np.full((height, width), np.nan, dtype="float32"),
        "fraction_gt_030": np.full((height, width), np.nan, dtype="float32"),
        "fraction_gt_050": np.full((height, width), np.nan, dtype="float32"),
        "fraction_gt_070": np.full((height, width), np.nan, dtype="float32"),
    }
    xmin, ymin, xmax, ymax = grid_bounds
    with rasterio.Env(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
        GDAL_HTTP_MULTIPLEX="YES",
        GDAL_CACHEMAX=512,
    ):
        for index, url in enumerate(urls, 1):
            print(f"Google tile {index}/{len(urls)}", flush=True)
            with rasterio.open(url) as src:
                left = max(xmin, src.bounds.left)
                bottom = max(ymin, src.bounds.bottom)
                right = min(xmax, src.bounds.right)
                top = min(ymax, src.bounds.top)
                if left >= right or bottom >= top:
                    continue
                window = from_bounds(left, bottom, right, top, src.transform)
                window = Window(
                    max(0, math.floor(window.col_off)),
                    max(0, math.floor(window.row_off)),
                    min(src.width, math.ceil(window.col_off + window.width))
                    - max(0, math.floor(window.col_off)),
                    min(src.height, math.ceil(window.row_off + window.height))
                    - max(0, math.floor(window.row_off)),
                )
                # The product is stored at 0.5 m but has an effective 4 m resolution.
                out_width = max(1, math.ceil(window.width / 8))
                out_height = max(1, math.ceil(window.height / 8))
                presence = src.read(
                    3,
                    window=window,
                    out_shape=(out_height, out_width),
                    masked=True,
                    resampling=Resampling.bilinear,
                )
                source_transform = src.window_transform(window) * Affine.scale(
                    window.width / out_width, window.height / out_height
                )
                valid = ~np.ma.getmaskarray(presence)
                values = np.asarray(presence.filled(NODATA), dtype="float32")
                source_layers = {
                    "confidence": values,
                    "fraction_gt_030": np.where(valid, values >= 0.30, NODATA).astype("float32"),
                    "fraction_gt_050": np.where(valid, values >= 0.50, NODATA).astype("float32"),
                    "fraction_gt_070": np.where(valid, values >= 0.70, NODATA).astype("float32"),
                }
                for name, source_values in source_layers.items():
                    temp = np.full((height, width), NODATA, dtype="float32")
                    reproject(
                        source=source_values,
                        destination=temp,
                        src_transform=source_transform,
                        src_crs=src.crs,
                        src_nodata=NODATA,
                        dst_transform=transform,
                        dst_crs=CRS,
                        dst_nodata=NODATA,
                        resampling=Resampling.average,
                    )
                    tile_valid = temp != NODATA
                    existing = layers[name]
                    replace = tile_valid & (np.isnan(existing) | (temp > existing))
                    existing[replace] = temp[replace]
    return layers


def source_upstream_counts(vectors):
    counts = {}
    overture = Counter()
    for entries in vectors["Overture"]["sources"]:
        if entries is None:
            continue
        for entry in entries:
            dataset = entry.get("dataset") if hasattr(entry, "get") else None
            if dataset:
                overture[dataset] += 1
    counts["Overture"] = dict(overture.most_common())
    counts["GlobalBuildingAtlas"] = (
        vectors["GlobalBuildingAtlas"]["source"].value_counts().to_dict()
    )
    return counts


def write_multiband(path, arrays, transform, aoi_fraction):
    names = list(arrays)
    profile = {
        "driver": "GTiff",
        "height": next(iter(arrays.values())).shape[0],
        "width": next(iter(arrays.values())).shape[1],
        "count": len(arrays),
        "dtype": "float32",
        "crs": CRS,
        "transform": transform,
        "nodata": NODATA,
        "compress": "deflate",
        "tiled": True,
    }
    with rasterio.open(path, "w", **profile) as dst:
        for index, name in enumerate(names, 1):
            values = arrays[name].astype("float32").copy()
            values[(aoi_fraction <= 0) | ~np.isfinite(values)] = NODATA
            dst.write(values, index)
            dst.set_band_description(index, name)


def plot_sources(path, arrays, aoi_fraction, bounds):
    fig, axes = plt.subplots(2, 3, figsize=(14, 8), constrained_layout=True)
    positive = np.concatenate(
        [a[(aoi_fraction > 0) & np.isfinite(a) & (a > 0)] for a in arrays.values()]
    )
    vmax = float(np.quantile(positive, 0.98)) if len(positive) else 0.25
    vmax = max(vmax, 0.05)
    image = None
    for ax, name in zip(axes.ravel(), SOURCE_ORDER):
        data = arrays[name].copy()
        data[aoi_fraction <= 0] = np.nan
        image = ax.imshow(
            data,
            extent=(bounds[0], bounds[2], bounds[1], bounds[3]),
            origin="upper",
            vmin=0,
            vmax=vmax,
            cmap="magma",
        )
        ax.set_title(name)
        ax.set_xticks([])
        ax.set_yticks([])
    fig.colorbar(image, ax=axes, label="Estimated building fraction per 100 m cell", shrink=0.8)
    fig.savefig(path, dpi=180)
    plt.close(fig)


def main():
    (ROOT / "outputs").mkdir(exist_ok=True)
    (ROOT / "data/processed").mkdir(exist_ok=True)
    aoi_gdf = gpd.read_file(AOI_PATH).to_crs(CRS)
    aoi = aoi_gdf.geometry.union_all()
    transform, width, height, cells, aoi_fraction, bounds = make_grid(aoi)
    analysis_mask = aoi_fraction >= 0.5
    cell_area = (RESOLUTION**2) * aoi_fraction

    vectors = load_vectors(aoi)
    vector_grids = {}
    vector_counts = {}
    vector_presence = {}
    vector_areas = {}
    for name, data in vectors.items():
        print(f"Rasterizing {name}: {len(data):,} features", flush=True)
        fraction, counts, presence, areas = vector_to_grid(
            data, transform, width, height, bounds
        )
        vector_grids[name] = fraction
        vector_counts[name] = counts
        vector_presence[name] = presence
        vector_areas[name] = areas

    tempo_paths = sorted((ROOT / "data/raw/tempo_2023q4").glob("*.tif"))
    tempo = reproject_rasters(tempo_paths, 1, transform, width, height)
    wsf = reproject_rasters(
        [ROOT / "data/raw/wsf3d/expanded/wsf3d_v02_building_fraction_juba_expanded_raw.tif"],
        1,
        transform,
        width,
        height,
        scale=0.01,
    )
    wsf_native_valid = np.isfinite(wsf)
    # WSF 3D is a global sparse settlement product: source NoData outside its
    # modeled settlement mask is interpreted as zero building fraction.
    wsf = np.where(np.isfinite(wsf), wsf, 0.0).astype("float32")
    urls = [
        line.strip()
        for line in (ROOT / "data/raw/google_2_5d/urls_expanded.txt").read_text().splitlines()
        if line.strip()
    ]
    google_layers = google_to_grid(urls, transform, width, height, bounds)

    fractions = {
        "TEMPO": np.clip(tempo, 0, 1),
        "Overture": vector_grids["Overture"],
        "Google 2.5D": np.clip(google_layers["fraction_gt_050"], 0, 1),
        "GlobalBuildingAtlas": vector_grids["GlobalBuildingAtlas"],
        "3D-GloBFP": vector_grids["3D-GloBFP"],
        "WSF 3D v2": np.clip(wsf, 0, 1),
    }
    positives = {
        name: analysis_mask & np.isfinite(values) & (values >= PRESENCE_THRESHOLD)
        for name, values in fractions.items()
    }

    vector_family = (
        positives["Overture"]
        | positives["GlobalBuildingAtlas"]
        | positives["3D-GloBFP"]
    )
    family_layers = {
        "TEMPO": positives["TEMPO"],
        "Vector syntheses": vector_family,
        "Google 2.5D": positives["Google 2.5D"],
        "WSF 3D v2": positives["WSF 3D v2"],
    }
    family_count = sum(layer.astype("uint8") for layer in family_layers.values())
    consensus = analysis_mask & (family_count >= 2)

    sensitivity_rows = []
    google_variants = {
        0.3: google_layers["fraction_gt_030"],
        0.5: google_layers["fraction_gt_050"],
        0.7: google_layers["fraction_gt_070"],
    }
    for google_threshold, google_fraction in google_variants.items():
        variant_fractions = dict(fractions)
        variant_fractions["Google 2.5D"] = google_fraction
        for area_threshold in (0.0025, 0.005, 0.01):
            variant_positive = {
                name: analysis_mask
                & np.isfinite(values)
                & (values >= area_threshold)
                for name, values in variant_fractions.items()
            }
            variant_vector_family = (
                variant_positive["Overture"]
                | variant_positive["GlobalBuildingAtlas"]
                | variant_positive["3D-GloBFP"]
            )
            variant_family_count = (
                variant_positive["TEMPO"].astype("uint8")
                + variant_vector_family.astype("uint8")
                + variant_positive["Google 2.5D"].astype("uint8")
                + variant_positive["WSF 3D v2"].astype("uint8")
            )
            variant_consensus = analysis_mask & (variant_family_count >= 2)
            for name in SOURCE_ORDER:
                sensitivity_rows.append(
                    {
                        "google_confidence_threshold": google_threshold,
                        "cell_building_fraction_threshold": area_threshold,
                        "source": name,
                        "consensus_cells": int(variant_consensus.sum()),
                        "positive_cells": int(variant_positive[name].sum()),
                        "consensus_recall_proxy_pct": 100
                        * float((variant_positive[name] & variant_consensus).sum())
                        / max(1, int(variant_consensus.sum())),
                        "gap_cells": int((variant_consensus & ~variant_positive[name]).sum()),
                    }
                )
    pd.DataFrame(sensitivity_rows).to_csv(
        ROOT / "outputs/juba_threshold_sensitivity.csv", index=False
    )

    summaries = []
    vintage = {
        "TEMPO": "2023 Q4",
        "Overture": "2026-08-19.0",
        "Google 2.5D": "2023-06-30",
        "GlobalBuildingAtlas": "2025 release",
        "3D-GloBFP": "2020",
        "WSF 3D v2": "v2",
    }
    upstream = source_upstream_counts(vectors)
    for name in SOURCE_ORDER:
        values = fractions[name]
        positive = positives[name]
        if name in vectors:
            feature_count = len(vectors[name])
            built_area_km2 = float(vector_areas[name].sum() / 1e6)
            median_area = float(np.median(vector_areas[name]))
            p90_area = float(np.quantile(vector_areas[name], 0.9))
        else:
            feature_count = np.nan
            built_area_km2 = float(np.nansum(values * cell_area) / 1e6)
            median_area = np.nan
            p90_area = np.nan
        gap = consensus & ~positive
        other_family = family_count.copy()
        if name in family_layers:
            other_family = other_family - family_layers[name].astype("uint8")
        elif name in {"Overture", "GlobalBuildingAtlas", "3D-GloBFP"}:
            vector_without = np.zeros_like(vector_family)
            for peer in {"Overture", "GlobalBuildingAtlas", "3D-GloBFP"} - {name}:
                vector_without |= positives[peer]
            other_family = (
                positives["TEMPO"].astype("uint8")
                + vector_without.astype("uint8")
                + positives["Google 2.5D"].astype("uint8")
                + positives["WSF 3D v2"].astype("uint8")
            )
        isolated = positive & (other_family == 0)
        summaries.append(
            {
                "source": name,
                "type": "vector" if name in vectors else "raster",
                "vintage": vintage[name],
                "feature_count": feature_count,
                "estimated_built_area_km2": built_area_km2,
                "median_footprint_m2": median_area,
                "p90_footprint_m2": p90_area,
                "positive_100m_cells": int(positive.sum()),
                "consensus_recall_proxy_pct": 100 * float((positive & consensus).sum()) / max(1, int(consensus.sum())),
                "gap_100m_cells": int(gap.sum()),
                "isolated_positive_cells_pct": 100 * float(isolated.sum()) / max(1, int(positive.sum())),
                "upstream_sources": json.dumps(upstream.get(name, {}), sort_keys=True),
                "nodata_treatment": (
                    "NoData interpreted as zero outside sparse settlement mask"
                    if name == "WSF 3D v2"
                    else "NoData excluded"
                ),
            }
        )
    summary = pd.DataFrame(summaries).sort_values(
        "consensus_recall_proxy_pct", ascending=False
    )
    summary.to_csv(ROOT / "outputs/juba_source_summary.csv", index=False)

    pairwise = []
    for i, left_name in enumerate(SOURCE_ORDER):
        for right_name in SOURCE_ORDER[i + 1 :]:
            left = positives[left_name]
            right = positives[right_name]
            union = left | right
            common = analysis_mask & np.isfinite(fractions[left_name]) & np.isfinite(fractions[right_name])
            corr_mask = common & (union)
            corr = np.nan
            if corr_mask.sum() > 2:
                corr = float(
                    spearmanr(
                        fractions[left_name][corr_mask], fractions[right_name][corr_mask]
                    ).statistic
                )
            pairwise.append(
                {
                    "source_a": left_name,
                    "source_b": right_name,
                    "jaccard_positive_cells": float((left & right).sum()) / max(1, int(union.sum())),
                    "spearman_fraction_on_union": corr,
                }
            )
    pd.DataFrame(pairwise).to_csv(ROOT / "outputs/juba_pairwise_agreement.csv", index=False)

    gap_count = sum((consensus & ~positives[name]).astype("uint8") for name in SOURCE_ORDER)
    family_fraction = family_count.astype("float32") / len(family_layers)
    raster_outputs = dict(fractions)
    raster_outputs["Google confidence mean"] = google_layers["confidence"]
    raster_outputs["Google fraction confidence >=0.3"] = google_layers["fraction_gt_030"]
    raster_outputs["Google fraction confidence >=0.7"] = google_layers["fraction_gt_070"]
    raster_outputs["Independent-family agreement"] = family_fraction
    raster_outputs["Source gap count"] = gap_count.astype("float32")
    write_multiband(
        ROOT / "outputs/juba_100m_comparison.tif", raster_outputs, transform, aoi_fraction
    )

    grid = gpd.GeoDataFrame(
        {
            "row": np.repeat(np.arange(height), width),
            "col": np.tile(np.arange(width), height),
            "aoi_fraction": aoi_fraction.ravel(),
            "family_count": family_count.ravel(),
            "consensus": consensus.ravel().astype("uint8"),
            "gap_count": gap_count.ravel(),
        },
        geometry=cells,
        crs=CRS,
    )
    for name in SOURCE_ORDER:
        field = {
            "TEMPO": "tempo",
            "Overture": "overture",
            "Google 2.5D": "google25d",
            "GlobalBuildingAtlas": "gba",
            "3D-GloBFP": "globfp3d",
            "WSF 3D v2": "wsf3d",
        }[name]
        grid[f"{field}_fraction"] = fractions[name].ravel()
        grid[f"{field}_present"] = positives[name].ravel().astype("uint8")
        grid[f"{field}_gap"] = (consensus & ~positives[name]).ravel().astype("uint8")
    grid = grid[grid.aoi_fraction > 0].copy()
    grid.to_file(ROOT / "outputs/juba_comparison_grid.gpkg", layer="comparison_100m", driver="GPKG")
    grid.to_parquet(ROOT / "outputs/juba_comparison_grid.parquet", index=False)

    plot_sources(ROOT / "outputs/juba_source_fractions.png", fractions, aoi_fraction, bounds)

    metadata = {
        "aoi_area_km2": float(aoi.area / 1e6),
        "aoi_definition": "Union of 14,759 Juba features in segments_hexbin_20260821.gpkg",
        "analysis_crs": CRS,
        "grid_resolution_m": RESOLUTION,
        "presence_threshold_fraction": PRESENCE_THRESHOLD,
        "presence_threshold_area_m2_per_full_cell": PRESENCE_THRESHOLD * RESOLUTION**2,
        "analysis_cells": int(analysis_mask.sum()),
        "consensus_cells": int(consensus.sum()),
        "consensus_definition": "At least two of four source families positive: TEMPO, vector syntheses, Google 2.5D, and WSF 3D v2.",
        "google_primary_threshold": 0.5,
        "vector_fraction_method": "Exact footprint area assigned to the 100 m cell containing each footprint representative point; clipped to [0,1].",
        "wsf_native_valid_analysis_cells_pct": 100
        * float((wsf_native_valid & analysis_mask).sum())
        / max(1, int(analysis_mask.sum())),
        "wsf_nodata_interpretation": "NoData inside the global product domain was treated as zero building fraction (outside the sparse settlement mask).",
    }
    (ROOT / "outputs/juba_analysis_metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n"
    )
    print(summary.to_string(index=False), flush=True)
    print(json.dumps(metadata, indent=2), flush=True)


if __name__ == "__main__":
    main()
