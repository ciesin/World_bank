#!/usr/bin/env python3
"""Build a provenance-rich, best-available building layer for expanded Juba.

Selection policy
----------------
1. Retain valid OSM-derived Overture footprints as the preferred geometry.
2. Add non-OSM Overture footprints only where they do not duplicate OSM.
3. Add Google-family Global Building Atlas polygons only where they do not
   duplicate a selected Overture footprint.
4. Never delete a selected footprint solely because WSF 2019 is absent.
5. Attach all available height estimates and select a documented best value.

The script intentionally keeps geometry selection and height selection separate.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import geopandas as gpd
import matplotlib
import numpy as np
import pandas as pd
import rasterio
import shapely
from matplotlib import pyplot as plt
from rasterio.features import rasterize


matplotlib.use("Agg")

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/integrated"
CRS = "EPSG:32636"
MIN_AREA_M2 = 4.0
OVERLAP_DUPLICATE_COVERAGE = 0.35
MIN_INTERSECTION_M2 = 1.0
NEAR_MAX_DISTANCE_M = 4.0

OVERTURE_PATH = ROOT / "data/processed/overture_juba_expanded.parquet"
GBA_PATH = ROOT / "data/processed/gba_polygon_juba_expanded.parquet"
GLOBFP_PATH = ROOT / "data/processed/3d_globfp_juba_expanded.parquet"
SEGMENTS_PATH = ROOT / "data/processed/juba_segments_20260821.gpkg"
RASTER_30M = ROOT / "outputs/juba_30m_comparison.tif"
RASTER_100M = ROOT / "outputs/juba_100m_height_comparison.tif"


def clean_polygons(data: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    data = data.to_crs(CRS).copy()
    valid = data.geometry.notna() & ~data.geometry.is_empty
    data = data.loc[valid].copy()
    invalid = ~shapely.is_valid(data.geometry.to_numpy())
    if invalid.any():
        data.loc[invalid, "geometry"] = shapely.make_valid(
            data.loc[invalid, "geometry"].to_numpy()
        )
    types = shapely.get_type_id(data.geometry.to_numpy())
    data = data.loc[np.isin(types, [3, 6])].copy()
    data["area_m2"] = shapely.area(data.geometry.to_numpy())
    return data.loc[data.area_m2 >= MIN_AREA_M2].reset_index(drop=True)


def source_value(value, key, default=None):
    if value is None or len(value) == 0:
        return default
    return value[0].get(key, default)


def load_overture() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    columns = [
        "id", "sources", "height", "num_floors", "class", "subtype",
        "names", "geometry",
    ]
    data = clean_polygons(gpd.read_parquet(OVERTURE_PATH, columns=columns))
    data["provider"] = [source_value(v, "provider", "unknown") for v in data.sources]
    data["source_dataset"] = [source_value(v, "dataset", "unknown") for v in data.sources]
    data["source_record_id"] = [source_value(v, "record_id") for v in data.sources]
    data["source_update_time"] = [source_value(v, "update_time") for v in data.sources]
    data["source_version"] = [source_value(v, "version") for v in data.sources]
    data["source_license"] = [source_value(v, "license") for v in data.sources]
    data["native_height_m"] = pd.to_numeric(data.height, errors="coerce")
    data["native_floors"] = pd.to_numeric(data.num_floors, errors="coerce")
    osm = data.loc[data.provider.eq("osm")].copy().reset_index(drop=True)
    other = data.loc[~data.provider.eq("osm")].copy().reset_index(drop=True)
    return osm, other


def intersect_matches(
    candidates: gpd.GeoDataFrame,
    preferred: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """Return each candidate's best preferred match and aggregate coverage."""
    if len(candidates) == 0 or len(preferred) == 0:
        return pd.DataFrame(columns=[
            "candidate_index", "preferred_index", "intersection_m2",
            "candidate_coverage", "smaller_overlap", "iou", "relation",
        ])
    joined = gpd.sjoin(
        candidates[["geometry"]], preferred[["geometry"]],
        how="inner", predicate="intersects",
    )
    if joined.empty:
        return pd.DataFrame(columns=[
            "candidate_index", "preferred_index", "intersection_m2",
            "candidate_coverage", "smaller_overlap", "iou", "relation",
        ])
    ci = joined.index.to_numpy(dtype="int64")
    pi = joined.index_right.to_numpy(dtype="int64")
    intersections = shapely.area(shapely.intersection(
        candidates.geometry.to_numpy()[ci], preferred.geometry.to_numpy()[pi]
    ))
    keep = intersections >= MIN_INTERSECTION_M2
    ci, pi, intersections = ci[keep], pi[keep], intersections[keep]
    if len(ci) == 0:
        return pd.DataFrame(columns=[
            "candidate_index", "preferred_index", "intersection_m2",
            "candidate_coverage", "smaller_overlap", "iou", "relation",
        ])
    area_c = candidates.area_m2.to_numpy()[ci]
    area_p = preferred.area_m2.to_numpy()[pi]
    edges = pd.DataFrame({
        "candidate_index": ci,
        "preferred_index": pi,
        "intersection_m2": intersections,
        "smaller_overlap": intersections / np.minimum(area_c, area_p),
        "iou": intersections / np.maximum(area_c + area_p - intersections, 1e-9),
    })
    coverage = edges.groupby("candidate_index").intersection_m2.sum()
    best_rows = edges.groupby("candidate_index").intersection_m2.idxmax()
    best = edges.loc[best_rows].copy().reset_index(drop=True)
    best["candidate_coverage"] = np.minimum(
        1.0,
        best.candidate_index.map(coverage).to_numpy()
        / candidates.area_m2.to_numpy()[best.candidate_index.to_numpy()],
    )
    best["relation"] = np.where(
        best.candidate_coverage >= OVERLAP_DUPLICATE_COVERAGE,
        "overlap_duplicate", "weak_overlap",
    )
    return best


def add_near_matches(
    candidates: gpd.GeoDataFrame,
    preferred: gpd.GeoDataFrame,
    matches: pd.DataFrame,
) -> pd.DataFrame:
    duplicated = set(matches.loc[
        matches.relation.eq("overlap_duplicate"), "candidate_index"
    ].astype(int))
    remaining_index = np.array(
        [i for i in range(len(candidates)) if i not in duplicated], dtype="int64"
    )
    if len(remaining_index) == 0:
        return matches
    remaining = candidates.iloc[remaining_index]
    candidate_points = gpd.GeoDataFrame(
        {"candidate_index": remaining_index},
        geometry=shapely.centroid(remaining.geometry.to_numpy()), crs=CRS,
    )
    preferred_points = gpd.GeoDataFrame(
        {"preferred_index": np.arange(len(preferred), dtype="int64")},
        geometry=shapely.centroid(preferred.geometry.to_numpy()), crs=CRS,
    )
    near = gpd.sjoin_nearest(
        candidate_points, preferred_points, how="inner",
        max_distance=NEAR_MAX_DISTANCE_M, distance_col="centroid_distance_m",
    )
    if near.empty:
        return matches
    ci = near.candidate_index.to_numpy(dtype="int64")
    pi = near.preferred_index.to_numpy(dtype="int64")
    area_c = candidates.area_m2.to_numpy()[ci]
    area_p = preferred.area_m2.to_numpy()[pi]
    ratio = area_c / np.maximum(area_p, 1e-9)
    adaptive = np.minimum(
        NEAR_MAX_DISTANCE_M, 0.35 * np.sqrt(np.minimum(area_c, area_p))
    )
    good = (
        (near.centroid_distance_m.to_numpy() <= adaptive)
        & (ratio >= 0.35) & (ratio <= 2.85)
    )
    near = near.loc[good].copy()
    if near.empty:
        return matches
    near = near.sort_values("centroid_distance_m").drop_duplicates(
        "candidate_index", keep="first"
    )
    addition = pd.DataFrame({
        "candidate_index": near.candidate_index.to_numpy(dtype="int64"),
        "preferred_index": near.preferred_index.to_numpy(dtype="int64"),
        "intersection_m2": 0.0,
        "candidate_coverage": 0.0,
        "smaller_overlap": 0.0,
        "iou": 0.0,
        "relation": "near_duplicate",
        "centroid_distance_m": near.centroid_distance_m.to_numpy(),
    })
    # A weak intersection record is replaced if the near test is more decisive.
    matches = matches.loc[~matches.candidate_index.isin(addition.candidate_index)]
    return pd.concat([matches, addition], ignore_index=True)


def duplicate_matches(
    candidates: gpd.GeoDataFrame,
    preferred: gpd.GeoDataFrame,
) -> pd.DataFrame:
    return add_near_matches(candidates, preferred, intersect_matches(candidates, preferred))


def selected_frame(data, source_name, prefix) -> gpd.GeoDataFrame:
    result = gpd.GeoDataFrame({
        "geometry_source": source_name,
        "geometry_source_id": data.id.astype(str).to_numpy(),
        "geometry_provider": data.get("provider", pd.Series("google", index=data.index)).astype(str).to_numpy(),
        "geometry_dataset": data.get("source_dataset", pd.Series("Global Building Atlas", index=data.index)).astype(str).to_numpy(),
        "geometry_license": data.get("source_license", pd.Series("CC-BY-4.0", index=data.index)).astype(str).to_numpy(),
        "source_record_id": data.get("source_record_id", pd.Series(None, index=data.index)).to_numpy(),
        "source_update_time": data.get("source_update_time", pd.Series(None, index=data.index)).to_numpy(),
        "source_version": data.get("source_version", pd.Series(None, index=data.index)).to_numpy(),
        "native_height_m": pd.to_numeric(data.get("native_height_m"), errors="coerce").to_numpy() if "native_height_m" in data else np.nan,
        "native_floors": pd.to_numeric(data.get("native_floors"), errors="coerce").to_numpy() if "native_floors" in data else np.nan,
        "area_m2": data.area_m2.to_numpy(),
        "geometry": data.geometry.to_numpy(),
    }, geometry="geometry", crs=CRS)
    result["integrated_id"] = [f"JUB-{prefix}-{i:07d}" for i in range(1, len(result) + 1)]
    return result


def raster_values_at_points(path: Path, band_names: list[str], points) -> dict[str, np.ndarray]:
    with rasterio.open(path) as src:
        descriptions = list(src.descriptions)
        xs = shapely.get_x(points)
        ys = shapely.get_y(points)
        inv = ~src.transform
        cols_f, rows_f = inv * (xs, ys)
        cols = np.floor(cols_f).astype("int64")
        rows = np.floor(rows_f).astype("int64")
        inside = (rows >= 0) & (rows < src.height) & (cols >= 0) & (cols < src.width)
        result = {}
        for name in band_names:
            band = descriptions.index(name) + 1
            values = src.read(band)
            sampled = np.full(len(points), np.nan, dtype="float32")
            sampled[inside] = values[rows[inside], cols[inside]]
            sampled[(sampled == src.nodata) | ~np.isfinite(sampled)] = np.nan
            result[name] = sampled
    return result


def attach_globfp_heights(
    integrated: gpd.GeoDataFrame,
    globfp: gpd.GeoDataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    joined = gpd.sjoin(
        integrated[["geometry"]], globfp[["geometry"]],
        how="inner", predicate="intersects",
    )
    ii = joined.index.to_numpy(dtype="int64")
    gi = joined.index_right.to_numpy(dtype="int64")
    intersection = shapely.area(shapely.intersection(
        integrated.geometry.to_numpy()[ii], globfp.geometry.to_numpy()[gi]
    ))
    area_i = integrated.area_m2.to_numpy()[ii]
    area_g = globfp.area_m2.to_numpy()[gi]
    keep = (
        (intersection >= MIN_INTERSECTION_M2)
        & ((intersection / area_i >= 0.15) | (intersection / area_g >= 0.30))
    )
    edges = pd.DataFrame({
        "integrated_index": ii[keep],
        "globfp_index": gi[keep],
        "intersection_m2": intersection[keep],
    })
    valid_height = pd.to_numeric(globfp.Height, errors="coerce").to_numpy()
    edges["height_m"] = valid_height[edges.globfp_index.to_numpy()]
    edges = edges.loc[edges.height_m.between(0.5, 100)].copy()
    output = pd.DataFrame(index=np.arange(len(integrated)))
    if edges.empty:
        output["height_globfp_vector_m"] = np.nan
        output["globfp_height_overlap_fraction"] = np.nan
        output["globfp_bfid"] = None
        return output, edges
    edges["weighted_height"] = edges.height_m * edges.intersection_m2
    grouped = edges.groupby("integrated_index")
    output["height_globfp_vector_m"] = (
        grouped.weighted_height.sum() / grouped.intersection_m2.sum()
    )
    output["globfp_height_overlap_fraction"] = np.minimum(
        1.0,
        grouped.intersection_m2.sum()
        / integrated.area_m2.to_numpy()[grouped.intersection_m2.sum().index],
    )
    best = edges.loc[grouped.intersection_m2.idxmax(), ["integrated_index", "globfp_index"]]
    bfid = globfp.BFID.astype(str).to_numpy()
    output.loc[best.integrated_index, "globfp_bfid"] = bfid[best.globfp_index]
    return output, edges


def assign_segments(integrated: gpd.GeoDataFrame) -> pd.DataFrame:
    segments = gpd.read_file(SEGMENTS_PATH).to_crs(CRS)
    keep_fields = [c for c in ["ANALYSIS_ID", "GRID_ID", "ID_SEG", "UC_NM_MN"] if c in segments]
    points = gpd.GeoDataFrame(
        {"integrated_index": np.arange(len(integrated), dtype="int64")},
        geometry=shapely.point_on_surface(integrated.geometry.to_numpy()), crs=CRS,
    )
    joined = gpd.sjoin(points, segments[keep_fields + ["geometry"]], how="left", predicate="within")
    joined = joined.sort_index().loc[~joined.index.duplicated(keep="first")]
    result = pd.DataFrame(index=np.arange(len(integrated)))
    for field in keep_fields:
        result[field] = joined.set_index("integrated_index")[field]
    return result


def choose_heights(data: gpd.GeoDataFrame) -> None:
    direct = pd.to_numeric(data.native_height_m, errors="coerce").to_numpy(dtype="float64")
    floors = pd.to_numeric(data.native_floors, errors="coerce").to_numpy(dtype="float64")
    floor_estimate = np.where((floors > 0) & (floors <= 40), floors * 3.0, np.nan)
    data["height_floors_estimate_m"] = floor_estimate
    candidates = [
        ("native_geometry", direct, "high"),
        ("OSM_levels_x_3m", floor_estimate, "medium"),
        ("3D-GloBFP_vector", data.height_globfp_vector_m.to_numpy(), "medium"),
        ("Google_2.5D_30m", data.height_google_30m_m.to_numpy(), "low"),
        ("WSF3D_v2_100m", data.height_wsf3d_100m_m.to_numpy(), "low"),
        ("TEMPO_100m", data.height_tempo_100m_m.to_numpy(), "low"),
    ]
    best = np.full(len(data), np.nan)
    source = np.full(len(data), None, dtype=object)
    confidence = np.full(len(data), None, dtype=object)
    for name, values, level in candidates:
        values = np.asarray(values, dtype="float64")
        good = np.isnan(best) & np.isfinite(values) & (values >= 0.5) & (values <= 100)
        best[good] = values[good]
        source[good] = name
        confidence[good] = level
    height_arrays = [np.asarray(v, dtype="float64") for _, v, _ in candidates]
    stack = np.vstack(height_arrays)
    valid = np.isfinite(stack) & (stack >= 0.5) & (stack <= 100)
    data["height_best_m"] = best
    data["height_source"] = source
    data["height_confidence"] = confidence
    data["height_source_count"] = valid.sum(axis=0).astype("int16")
    safe_stack = np.where(valid, stack, np.nan)
    # Avoid all-NaN reduction warnings while preserving NaN for no-height rows.
    no_height = ~valid.any(axis=0)
    safe_stack[:, no_height] = 0.0
    minimum = np.nanmin(safe_stack, axis=0)
    maximum = np.nanmax(safe_stack, axis=0)
    minimum[no_height] = np.nan
    maximum[no_height] = np.nan
    data["height_range_m"] = maximum - minimum


def geometry_confidence(data: gpd.GeoDataFrame) -> None:
    osm = data.geometry_source.eq("OpenStreetMap").to_numpy()
    wsf = data.wsf2019_settlement_present.fillna(0).to_numpy() >= 0.5
    gba = data.gba_support.fillna(False).to_numpy(dtype=bool)
    revised_osm = data.osm_multiple_versions.fillna(False).to_numpy(dtype=bool)
    confidence = np.full(len(data), "low", dtype=object)
    # WSF confirms settlement, not exact boundary geometry. It only upgrades OSM
    # to high confidence when combined with a multiple-version history signal.
    confidence[osm & (gba | (revised_osm & wsf))] = "high"
    confidence[osm & ~(gba | (revised_osm & wsf))] = "medium"
    nonosm = ~osm
    confidence[nonosm & (gba | wsf)] = "medium"
    data["geometry_confidence"] = confidence
    reason = np.full(len(data), "GBA_gapfill_unconfirmed", dtype=object)
    reason[data.geometry_source.eq("GlobalBuildingAtlas") & wsf] = "GBA_gapfill_WSF_supported"
    reason[data.geometry_source.eq("Overture_nonOSM") & ~gba & wsf] = "Overture_gapfill_WSF_supported"
    reason[data.geometry_source.eq("Overture_nonOSM") & gba] = "Overture_gapfill_GBA_supported"
    reason[osm & ~gba & revised_osm] = "OSM_preferred_multiple_versions"
    reason[osm & ~gba & ~revised_osm] = "OSM_preferred_single_version"
    reason[osm & gba] = "OSM_preferred_supported"
    data["selection_reason"] = reason
    data["review_required"] = (
        data.geometry_confidence.eq("low")
        | (data.height_source_count >= 2) & (data.height_range_m > 5)
    )


def make_summary(data: gpd.GeoDataFrame) -> dict:
    source_counts = data.geometry_source.value_counts().to_dict()
    confidence_counts = data.geometry_confidence.value_counts().to_dict()
    return {
        "created_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "crs": CRS,
        "building_count": int(len(data)),
        "total_footprint_area_km2": float(data.area_m2.sum() / 1e6),
        "geometry_source_counts": {str(k): int(v) for k, v in source_counts.items()},
        "geometry_confidence_counts": {str(k): int(v) for k, v in confidence_counts.items()},
        "height_available_count": int(data.height_best_m.notna().sum()),
        "height_available_pct": float(100 * data.height_best_m.notna().mean()),
        "height_source_counts": {
            ("unavailable" if pd.isna(k) else str(k)): int(v)
            for k, v in data.height_source.value_counts(dropna=False).items()
        },
        "osm_multiple_versions_count": int(data.osm_multiple_versions.sum()),
        "review_required_count": int(data.review_required.sum()),
        "selection_rules": {
            "geometry_priority": ["OpenStreetMap", "Overture_nonOSM", "GlobalBuildingAtlas"],
            "overlap_duplicate_candidate_coverage": OVERLAP_DUPLICATE_COVERAGE,
            "near_duplicate_max_centroid_distance_m": NEAR_MAX_DISTANCE_M,
            "minimum_feature_area_m2": MIN_AREA_M2,
            "wsf_use": "confidence/support only; never used to delete a footprint",
            "correlated_family": "GBA Google polygons and 3D-GloBFP are not counted as independent geometry sources",
            "height_priority": [
                "native geometry height", "OSM levels x 3 m", "3D-GloBFP vector",
                "Google 2.5D 30 m", "WSF3D v2 100 m", "TEMPO 100 m",
            ],
        },
        "licensing_note": (
            "The integrated layer contains OSM-derived geometries (ODbL-1.0) and "
            "other provider data. Preserve per-feature lineage and obtain legal review "
            "before redistribution or creation of a public derivative database."
        ),
    }


def segment_summary(data: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    segments = gpd.read_file(SEGMENTS_PATH).to_crs(CRS)
    key = "ANALYSIS_ID" if "ANALYSIS_ID" in segments else "ID_SEG"
    records = []
    for identifier, group in data.groupby(key, dropna=True):
        records.append({
            key: identifier,
            "integrated_buildings": int(len(group)),
            "footprint_area_m2": float(group.area_m2.sum()),
            "osm_buildings": int(group.geometry_source.eq("OpenStreetMap").sum()),
            "gapfill_buildings": int((~group.geometry_source.eq("OpenStreetMap")).sum()),
            "height_available": int(group.height_best_m.notna().sum()),
            "median_height_m": float(group.height_best_m.median()) if group.height_best_m.notna().any() else np.nan,
            "review_required": int(group.review_required.sum()),
        })
    summary = pd.DataFrame(records)
    return segments.merge(summary, on=key, how="left")


def plot_overview(data: gpd.GeoDataFrame, path: Path) -> None:
    bounds = data.total_bounds
    resolution = 60.0
    width = int(math.ceil((bounds[2] - bounds[0]) / resolution))
    height = int(math.ceil((bounds[3] - bounds[1]) / resolution))
    transform = rasterio.transform.from_origin(bounds[0], bounds[3], resolution, resolution)
    source_code = data.geometry_source.map({
        "OpenStreetMap": 1, "Overture_nonOSM": 2, "GlobalBuildingAtlas": 3,
    }).to_numpy()
    # Draw lower-priority geometry first so preferred OSM remains visible.
    order = np.argsort(source_code)[::-1]
    source_grid = rasterize(
        ((data.geometry.iloc[i], int(source_code[i])) for i in order),
        out_shape=(height, width), transform=transform, fill=0, dtype="uint8",
    )
    height_grid = rasterize(
        ((g, float(h)) for g, h in zip(data.geometry, data.height_best_m) if pd.notna(h)),
        out_shape=(height, width), transform=transform, fill=np.nan, dtype="float32",
    )
    review_grid = rasterize(
        ((g, 1) for g in data.loc[data.review_required, "geometry"]),
        out_shape=(height, width), transform=transform, fill=0, dtype="uint8",
    )
    fig, axes = plt.subplots(1, 3, figsize=(15, 6), constrained_layout=True)
    from matplotlib.colors import BoundaryNorm, ListedColormap
    source_cmap = ListedColormap(["white", "#2b6cb0", "#ed8936", "#38a169"])
    axes[0].imshow(source_grid, cmap=source_cmap, norm=BoundaryNorm([-0.5, .5, 1.5, 2.5, 3.5], 4))
    axes[0].set_title("Selected geometry source")
    import matplotlib.patches as mpatches
    axes[0].legend(handles=[
        mpatches.Patch(color="#2b6cb0", label="OpenStreetMap"),
        mpatches.Patch(color="#ed8936", label="Overture non-OSM gap-fill"),
        mpatches.Patch(color="#38a169", label="GBA gap-fill"),
    ], loc="lower left", fontsize=8)
    image = axes[1].imshow(height_grid, cmap="viridis", vmin=0, vmax=np.nanquantile(data.height_best_m, .98))
    axes[1].set_title("Best available height (m)")
    fig.colorbar(image, ax=axes[1], shrink=.7, label="metres")
    axes[2].imshow(review_grid, cmap=ListedColormap(["white", "#c53030"]), vmin=0, vmax=1)
    axes[2].set_title("Review-required footprints")
    for ax in axes:
        ax.set_axis_off()
    fig.suptitle("Juba integrated building footprints", fontsize=15)
    fig.savefig(path, dpi=220, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print("Loading Overture and separating OSM lineage", flush=True)
    osm, overture_other = load_overture()
    print(f"OSM: {len(osm):,}; Overture non-OSM: {len(overture_other):,}", flush=True)

    selected_osm = selected_frame(osm, "OpenStreetMap", "OSM")
    print("Matching non-OSM Overture candidates to OSM", flush=True)
    other_matches = duplicate_matches(overture_other, selected_osm)
    other_duplicate = other_matches.relation.isin(["overlap_duplicate", "near_duplicate"])
    other_suppressed = set(other_matches.loc[other_duplicate, "candidate_index"].astype(int))
    other_keep = np.array([i not in other_suppressed for i in range(len(overture_other))])
    selected_other = selected_frame(
        overture_other.loc[other_keep].reset_index(drop=True), "Overture_nonOSM", "OVR"
    )
    selected_overture = pd.concat([selected_osm, selected_other], ignore_index=True)
    selected_overture = gpd.GeoDataFrame(selected_overture, geometry="geometry", crs=CRS)
    print(f"Selected Overture-family geometries: {len(selected_overture):,}", flush=True)

    print("Loading and matching GBA Google-family polygons", flush=True)
    gba = clean_polygons(gpd.read_parquet(GBA_PATH))
    gba["id"] = gba.id.astype(str)
    gba_matches = duplicate_matches(gba, selected_overture)
    gba_duplicate = gba_matches.relation.isin(["overlap_duplicate", "near_duplicate"])
    gba_suppressed = set(gba_matches.loc[gba_duplicate, "candidate_index"].astype(int))
    gba_keep = np.array([i not in gba_suppressed for i in range(len(gba))])
    selected_gba = selected_frame(gba.loc[gba_keep].reset_index(drop=True), "GlobalBuildingAtlas", "GBA")
    integrated = pd.concat([selected_overture, selected_gba], ignore_index=True)
    integrated = gpd.GeoDataFrame(integrated, geometry="geometry", crs=CRS)
    print(f"Integrated geometries: {len(integrated):,}", flush=True)

    # Mark selected Overture geometries independently supported by a GBA match.
    integrated["gba_support"] = False
    supported_preferred = gba_matches.loc[gba_duplicate, "preferred_index"].dropna().astype(int).unique()
    integrated.loc[supported_preferred, "gba_support"] = True
    integrated["osm_version"] = pd.to_numeric(
        integrated.source_record_id.astype("string").str.extract(r"@(\d+)$")[0],
        errors="coerce",
    ).astype("Int32")
    integrated["osm_multiple_versions"] = integrated.osm_version.fillna(0).ge(2)

    print("Attaching 3D-GloBFP vector heights", flush=True)
    globfp = clean_polygons(gpd.read_parquet(GLOBFP_PATH))
    height_table, height_edges = attach_globfp_heights(integrated, globfp)
    for column in height_table:
        integrated[column] = height_table[column].to_numpy()

    print("Sampling gridded height and settlement products", flush=True)
    points = shapely.point_on_surface(integrated.geometry.to_numpy())
    grid30 = raster_values_at_points(RASTER_30M, [
        "WSF 2019 settlement fraction", "WSF 2019 settlement present",
        "Google mean height m", "3D-GloBFP mean height m",
    ], points)
    integrated["wsf2019_settlement_fraction"] = grid30["WSF 2019 settlement fraction"]
    integrated["wsf2019_settlement_present"] = grid30["WSF 2019 settlement present"]
    integrated["height_google_30m_m"] = grid30["Google mean height m"]
    integrated["height_globfp_grid_30m_m"] = grid30["3D-GloBFP mean height m"]
    grid100 = raster_values_at_points(RASTER_100M, [
        "TEMPO mean height m", "WSF 3D v2 mean height m",
    ], points)
    integrated["height_tempo_100m_m"] = grid100["TEMPO mean height m"]
    integrated["height_wsf3d_100m_m"] = grid100["WSF 3D v2 mean height m"]

    print("Assigning segments, confidence, and best heights", flush=True)
    segment_fields = assign_segments(integrated)
    for column in segment_fields:
        integrated[column] = segment_fields[column].to_numpy()
    choose_heights(integrated)
    geometry_confidence(integrated)

    # Build a compact source-lineage table for selected and suppressed geometries.
    lineage = pd.DataFrame({
        "integrated_id": integrated.integrated_id,
        "role": "selected_geometry",
        "source_dataset": integrated.geometry_dataset,
        "source_id": integrated.geometry_source_id,
        "source_record_id": integrated.source_record_id,
        "relation": "selected",
        "candidate_coverage": 1.0,
        "iou": 1.0,
    })
    other_lineage = other_matches.loc[other_duplicate].copy()
    if len(other_lineage):
        other_lineage["integrated_id"] = selected_osm.integrated_id.to_numpy()[other_lineage.preferred_index.astype(int)]
        other_lineage["role"] = "suppressed_duplicate"
        other_lineage["source_dataset"] = overture_other.source_dataset.to_numpy()[other_lineage.candidate_index.astype(int)]
        other_lineage["source_id"] = overture_other.id.astype(str).to_numpy()[other_lineage.candidate_index.astype(int)]
        other_lineage["source_record_id"] = overture_other.source_record_id.to_numpy()[other_lineage.candidate_index.astype(int)]
    gba_lineage = gba_matches.loc[gba_duplicate].copy()
    if len(gba_lineage):
        gba_lineage["integrated_id"] = selected_overture.integrated_id.to_numpy()[gba_lineage.preferred_index.astype(int)]
        gba_lineage["role"] = "supporting_or_suppressed_duplicate"
        gba_lineage["source_dataset"] = "Global Building Atlas (Google family)"
        gba_lineage["source_id"] = gba.id.astype(str).to_numpy()[gba_lineage.candidate_index.astype(int)]
        gba_lineage["source_record_id"] = None
    lineage_columns = [
        "integrated_id", "role", "source_dataset", "source_id", "source_record_id",
        "relation", "candidate_coverage", "iou",
    ]
    lineage = pd.concat([
        lineage[lineage_columns],
        other_lineage.reindex(columns=lineage_columns),
        gba_lineage.reindex(columns=lineage_columns),
    ], ignore_index=True)

    print("Writing outputs", flush=True)
    integrated = integrated.sort_values("integrated_id").reset_index(drop=True)
    integrated.to_parquet(OUT / "juba_integrated_buildings.parquet", index=False)
    lineage.to_parquet(OUT / "juba_integrated_building_lineage.parquet", index=False)
    segment_result = segment_summary(integrated)
    segment_result.to_file(
        OUT / "juba_integrated_segment_summary.gpkg", layer="segment_summary",
        driver="GPKG",
    )
    segment_result.drop(columns="geometry").to_csv(
        OUT / "juba_integrated_segment_summary.csv", index=False
    )
    summary = make_summary(integrated)
    summary.update({
        "input_counts": {
            "overture_osm": int(len(osm)),
            "overture_non_osm": int(len(overture_other)),
            "gba_google_family": int(len(gba)),
            "globfp3d": int(len(globfp)),
        },
        "suppressed_duplicate_counts": {
            "overture_non_osm_against_osm": int(len(other_suppressed)),
            "gba_against_selected_overture": int(len(gba_suppressed)),
        },
    })
    (OUT / "juba_integrated_summary.json").write_text(
        json.dumps(summary, indent=2, default=str) + "\n"
    )
    plot_overview(integrated, OUT / "juba_integrated_overview.png")

    readme = "# Juba integrated building footprints\n\n"
    readme += "This prototype keeps OSM-derived Overture geometries as the preferred human-mapped source, then fills spatial gaps with non-OSM Overture and Global Building Atlas geometries. Height selection is independent from geometry selection.\n\n"
    readme += "## Main files\n\n"
    readme += "- `juba_integrated_buildings.parquet`: one selected footprint per integrated feature, with provenance, confidence, segment identifiers, all height estimates, and the selected best height.\n"
    readme += "- `juba_integrated_building_lineage.parquet`: selected and suppressed/supporting source records.\n"
    readme += "- `juba_integrated_segment_summary.gpkg`: polygon-unit aggregation for GIS use.\n"
    readme += "- `juba_integrated_summary.json`: methods, thresholds, counts, and licensing caution.\n"
    readme += "- `juba_integrated_overview.png`: geometry source, height, and review overview.\n\n"
    readme += "## Important interpretation\n\n"
    readme += "Raster heights are sampled at each footprint's point-on-surface. They describe the containing 30 m or 100 m cell and are not building-level measurements. `height_best_m` follows the hierarchy recorded in the JSON metadata. GBA and 3D-GloBFP are treated as a correlated geometry family, not independent confirmation.\n\n"
    readme += "The output contains OSM-derived ODbL geometries. Preserve lineage and obtain appropriate licensing review before public redistribution.\n"
    (OUT / "README.md").write_text(readme)
    print(json.dumps(summary, indent=2, default=str), flush=True)


if __name__ == "__main__":
    main()
