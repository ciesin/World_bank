#!/usr/bin/env python3
"""Run reduced vector analysis and best-available selection for one portfolio city."""

from __future__ import annotations

import argparse
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
CITIES = ROOT / "data/cities"
OUTPUTS = ROOT / "outputs/cities"
MIN_AREA_M2 = 4.0
MIN_INTERSECTION_M2 = 1.0
DUPLICATE_COVERAGE = 0.35
NEAR_DISTANCE_M = 4.0
MATCH_CHUNK = 100_000


def clean(data: gpd.GeoDataFrame, crs) -> gpd.GeoDataFrame:
    data = data.to_crs(crs).copy()
    data = data.loc[data.geometry.notna() & ~data.geometry.is_empty].copy()
    invalid = ~shapely.is_valid(data.geometry.to_numpy())
    if invalid.any():
        data.loc[invalid, "geometry"] = shapely.make_valid(data.loc[invalid, "geometry"].to_numpy())
    data = data.loc[np.isin(shapely.get_type_id(data.geometry.to_numpy()), [3, 6])].copy()
    data["area_m2"] = shapely.area(data.geometry.to_numpy())
    return data.loc[data.area_m2 >= MIN_AREA_M2].reset_index(drop=True)


def keep_points_in_aoi(data: gpd.GeoDataFrame, aoi) -> gpd.GeoDataFrame:
    points = shapely.point_on_surface(data.geometry.to_numpy())
    return data.loc[shapely.within(points, aoi)].reset_index(drop=True)


def source_value(value, key, default=None):
    if value is None or len(value) == 0:
        return default
    return value[0].get(key, default)


def load_sources(city_slug: str, crs, aoi, bounds_wgs84):
    source_dir = CITIES / city_slug / "sources"
    columns = [
        "id", "sources", "height", "num_floors", "class", "subtype",
        "names", "geometry",
    ]
    overture = clean(gpd.read_parquet(source_dir / "overture_buildings.parquet", columns=columns), crs)
    overture = keep_points_in_aoi(overture, aoi)
    overture["provider"] = [source_value(v, "provider", "unknown") for v in overture.sources]
    overture["source_dataset"] = [source_value(v, "dataset", "unknown") for v in overture.sources]
    overture["source_record_id"] = [source_value(v, "record_id") for v in overture.sources]
    overture["source_update_time"] = [source_value(v, "update_time") for v in overture.sources]
    overture["source_version"] = [source_value(v, "version") for v in overture.sources]
    overture["source_license"] = [source_value(v, "license") for v in overture.sources]
    overture["native_height_m"] = pd.to_numeric(overture.height, errors="coerce")
    overture["native_floors"] = pd.to_numeric(overture.num_floors, errors="coerce")

    usage = pd.read_csv(CITIES / "globfp_city_tile_usage.csv")
    tile_ids = usage.loc[usage.city_slug.eq(city_slug), "grid_ID"].astype(int).tolist()
    pieces = []
    for tile_id in tile_ids:
        directory = ROOT / "data/raw/3d_globfp/portfolio_tiles" / f"tile_{tile_id}"
        # Most archives are flat; several updated tiles contain a nested folder.
        files = list(directory.rglob("*.shp"))
        if len(files) != 1:
            raise ValueError(f"Grid {tile_id}: expected one shapefile, found {len(files)}")
        part = gpd.read_file(
            files[0], bbox=tuple(bounds_wgs84), columns=["BFID", "Height"], engine="pyogrio"
        )
        if len(part):
            # Some distributed tiles omit BFID; a stable tile-local row key is sufficient.
            if "BFID" not in part:
                part["BFID"] = np.arange(len(part), dtype="int64").astype(str)
            part["tile_id"] = tile_id
            part["globfp_id"] = str(tile_id) + ":" + part.BFID.astype(str)
            pieces.append(part)
    if pieces:
        globfp = gpd.GeoDataFrame(pd.concat(pieces, ignore_index=True), geometry="geometry", crs=pieces[0].crs)
        globfp = clean(globfp, crs)
        globfp = keep_points_in_aoi(globfp, aoi)
        globfp = globfp.drop_duplicates("globfp_id").reset_index(drop=True)
        globfp["BFID"] = globfp.BFID.astype(str)
        globfp["Height"] = pd.to_numeric(globfp.Height, errors="coerce")
    else:
        globfp = gpd.GeoDataFrame(
            columns=["BFID", "Height", "tile_id", "globfp_id", "area_m2", "geometry"],
            geometry="geometry", crs=crs,
        )
    overture.to_parquet(source_dir / "overture_clipped.parquet", index=False)
    globfp.to_parquet(source_dir / "globfp3d_clipped.parquet", index=False)
    return overture, globfp


def overlap_matches(candidates, preferred) -> pd.DataFrame:
    results = []
    for start in range(0, len(candidates), MATCH_CHUNK):
        stop = min(start + MATCH_CHUNK, len(candidates))
        chunk = candidates.iloc[start:stop]
        joined = gpd.sjoin(chunk[["geometry"]], preferred[["geometry"]], how="inner", predicate="intersects")
        if joined.empty:
            continue
        ci = joined.index.to_numpy(dtype="int64")
        pi = joined.index_right.to_numpy(dtype="int64")
        intersection = shapely.area(shapely.intersection(
            candidates.geometry.to_numpy()[ci], preferred.geometry.to_numpy()[pi]
        ))
        keep = intersection >= MIN_INTERSECTION_M2
        ci, pi, intersection = ci[keep], pi[keep], intersection[keep]
        if not len(ci):
            continue
        area_c = candidates.area_m2.to_numpy()[ci]
        area_p = preferred.area_m2.to_numpy()[pi]
        edges = pd.DataFrame({
            "candidate_index": ci, "preferred_index": pi,
            "intersection_m2": intersection,
            "smaller_overlap": intersection / np.minimum(area_c, area_p),
            "iou": intersection / np.maximum(area_c + area_p - intersection, 1e-9),
        })
        grouped = edges.groupby("candidate_index")
        total = grouped.intersection_m2.sum()
        counts = grouped.size()
        best = edges.loc[grouped.intersection_m2.idxmax()].copy()
        best["intersection_sum_m2"] = best.candidate_index.map(total).to_numpy()
        best["preferred_overlap_count"] = best.candidate_index.map(counts).to_numpy(dtype="int32")
        best["candidate_coverage"] = np.minimum(
            1.0, best.intersection_sum_m2.to_numpy()
            / candidates.area_m2.to_numpy()[best.candidate_index.to_numpy()],
        )
        results.append(best)
    if not results:
        return pd.DataFrame(columns=[
            "candidate_index", "preferred_index", "intersection_m2", "intersection_sum_m2",
            "preferred_overlap_count", "candidate_coverage", "smaller_overlap", "iou", "relation",
        ])
    matches = pd.concat(results, ignore_index=True)
    matches["relation"] = np.where(
        matches.candidate_coverage >= DUPLICATE_COVERAGE, "overlap_duplicate", "weak_overlap"
    )
    preferred_counts = matches.loc[matches.relation.eq("overlap_duplicate")].groupby(
        "preferred_index"
    ).size()
    matches["match_structure"] = "one_to_one_or_weak"
    matches.loc[
        matches.preferred_overlap_count > 1, "match_structure"
    ] = "candidate_to_multiple_preferred"
    multiple_candidates = matches.preferred_index.map(preferred_counts).fillna(0).to_numpy() > 1
    matches.loc[multiple_candidates, "match_structure"] = "multiple_candidates_to_preferred"
    return matches


def add_near_matches(candidates, preferred, matches) -> pd.DataFrame:
    duplicates = set(matches.loc[matches.relation.eq("overlap_duplicate"), "candidate_index"].astype(int))
    remaining = np.array([i for i in range(len(candidates)) if i not in duplicates], dtype="int64")
    if not len(remaining) or not len(preferred):
        return matches
    cp = gpd.GeoDataFrame(
        {"candidate_index": remaining},
        geometry=shapely.centroid(candidates.geometry.to_numpy()[remaining]), crs=candidates.crs,
    )
    pp = gpd.GeoDataFrame(
        {"preferred_index": np.arange(len(preferred), dtype="int64")},
        geometry=shapely.centroid(preferred.geometry.to_numpy()), crs=preferred.crs,
    )
    near = gpd.sjoin_nearest(cp, pp, how="inner", max_distance=NEAR_DISTANCE_M,
                             distance_col="centroid_distance_m")
    if near.empty:
        return matches
    ci = near.candidate_index.to_numpy(dtype="int64")
    pi = near.preferred_index.to_numpy(dtype="int64")
    ac = candidates.area_m2.to_numpy()[ci]
    ap = preferred.area_m2.to_numpy()[pi]
    ratio = ac / np.maximum(ap, 1e-9)
    adaptive = np.minimum(NEAR_DISTANCE_M, .35 * np.sqrt(np.minimum(ac, ap)))
    good = (near.centroid_distance_m.to_numpy() <= adaptive) & (ratio >= .35) & (ratio <= 2.85)
    near = near.loc[good].sort_values("centroid_distance_m").drop_duplicates("candidate_index")
    if near.empty:
        return matches
    addition = pd.DataFrame({
        "candidate_index": near.candidate_index.astype("int64"),
        "preferred_index": near.preferred_index.astype("int64"),
        "intersection_m2": 0.0, "intersection_sum_m2": 0.0,
        "preferred_overlap_count": 0, "candidate_coverage": 0.0,
        "smaller_overlap": 0.0, "iou": 0.0, "relation": "near_duplicate",
        "match_structure": "near_one_to_one",
        "centroid_distance_m": near.centroid_distance_m.to_numpy(),
    })
    matches = matches.loc[~matches.candidate_index.isin(addition.candidate_index)]
    return pd.concat([matches, addition], ignore_index=True)


def selected_overture(data, city_slug) -> gpd.GeoDataFrame:
    osm = data.provider.eq("osm").to_numpy()
    source = np.where(osm, "OpenStreetMap", "Overture_nonOSM")
    frame = gpd.GeoDataFrame({
        "geometry_source": source,
        "geometry_source_id": data.id.astype(str).to_numpy(),
        "geometry_provider": data.provider.astype(str).to_numpy(),
        "geometry_dataset": data.source_dataset.astype(str).to_numpy(),
        "geometry_license": data.source_license.astype(str).to_numpy(),
        "source_record_id": data.source_record_id.to_numpy(),
        "source_update_time": data.source_update_time.to_numpy(),
        "source_version": data.source_version.to_numpy(),
        "native_height_m": data.native_height_m.to_numpy(),
        "native_floors": data.native_floors.to_numpy(),
        "area_m2": data.area_m2.to_numpy(),
        "geometry": data.geometry.to_numpy(),
    }, geometry="geometry", crs=data.crs)
    frame["integrated_id"] = [f"{city_slug}-OVR-{i:08d}" for i in range(1, len(frame) + 1)]
    return frame


def selected_globfp(data, city_slug) -> gpd.GeoDataFrame:
    frame = gpd.GeoDataFrame({
        "geometry_source": "3D-GloBFP_gapfill",
        "geometry_source_id": data.globfp_id.astype(str).to_numpy(),
        "geometry_provider": "3D-GloBFP",
        "geometry_dataset": "3D-GloBFP / GBA-correlated family",
        "geometry_license": "CC-BY-4.0",
        "source_record_id": data.BFID.astype(str).to_numpy(),
        "source_update_time": None, "source_version": "2020 model",
        "native_height_m": pd.to_numeric(data.Height, errors="coerce").to_numpy(),
        "native_floors": np.nan, "area_m2": data.area_m2.to_numpy(),
        "geometry": data.geometry.to_numpy(),
    }, geometry="geometry", crs=data.crs)
    frame["integrated_id"] = [f"{city_slug}-GLO-{i:08d}" for i in range(1, len(frame) + 1)]
    return frame


def attach_globfp_point_heights(integrated, globfp):
    result = pd.DataFrame(index=np.arange(len(integrated)))
    result["height_globfp_vector_m"] = np.nan
    result["globfp_height_id"] = None
    if not len(globfp):
        return result
    points = gpd.GeoDataFrame(
        {"integrated_index": np.arange(len(integrated), dtype="int64")},
        geometry=shapely.point_on_surface(integrated.geometry.to_numpy()), crs=integrated.crs,
    )
    target = globfp[["globfp_id", "Height", "area_m2", "geometry"]]
    joined = gpd.sjoin(points, target, how="left", predicate="within")
    joined = joined.sort_values(["integrated_index", "area_m2"]).drop_duplicates("integrated_index")
    joined = joined.set_index("integrated_index")
    result.loc[joined.index, "height_globfp_vector_m"] = pd.to_numeric(joined.Height, errors="coerce")
    result.loc[joined.index, "globfp_height_id"] = joined.globfp_id
    return result


def choose_vector_height(data):
    direct = pd.to_numeric(data.native_height_m, errors="coerce").to_numpy(dtype="float64")
    floors = pd.to_numeric(data.native_floors, errors="coerce").to_numpy(dtype="float64")
    floor_height = np.where((floors > 0) & (floors <= 40), floors * 3.0, np.nan)
    glob = pd.to_numeric(data.height_globfp_vector_m, errors="coerce").to_numpy(dtype="float64")
    data["height_floors_estimate_m"] = floor_height
    best = np.full(len(data), np.nan)
    source = np.full(len(data), None, dtype=object)
    confidence = np.full(len(data), None, dtype=object)
    for name, values, level in [
        ("native_geometry", direct, "high"),
        ("OSM_levels_x_3m", floor_height, "medium"),
        ("3D-GloBFP_vector", glob, "medium"),
    ]:
        good = np.isnan(best) & np.isfinite(values) & (values >= .5) & (values <= 100)
        best[good], source[good], confidence[good] = values[good], name, level
    data["height_best_m"] = best
    data["height_source"] = source
    data["height_confidence"] = confidence


def assign_segments(data, segments):
    points = gpd.GeoDataFrame(
        {"row_index": np.arange(len(data), dtype="int64")},
        geometry=shapely.point_on_surface(data.geometry.to_numpy()), crs=data.crs,
    )
    joined = gpd.sjoin(points, segments[["ANALYSIS_ID", "SEGMENT_UID", "geometry"]],
                       how="left", predicate="within")
    joined = joined.sort_index().loc[~joined.index.duplicated(keep="first")]
    return joined.set_index("row_index")[["ANALYSIS_ID", "SEGMENT_UID"]].reindex(range(len(data)))


def segment_summary(segments, integrated, overture, globfp, matches):
    out = segments.copy()
    int_group = integrated.groupby("ANALYSIS_ID")
    summary = pd.DataFrame(index=segments.ANALYSIS_ID)
    summary["integrated_count"] = int_group.size()
    summary["integrated_area_m2"] = int_group.area_m2.sum()
    summary["osm_count"] = int_group.geometry_source.apply(lambda s: int(s.eq("OpenStreetMap").sum()))
    summary["gapfill_count"] = int_group.geometry_source.apply(
        lambda s: int((~s.eq("OpenStreetMap")).sum())
    )
    summary["height_available"] = int_group.height_best_m.apply(lambda s: int(s.notna().sum()))
    summary["median_vector_height_m"] = int_group.height_best_m.median()
    for name, source in [("overture", overture), ("globfp3d", globfp)]:
        group = source.groupby("ANALYSIS_ID")
        summary[f"{name}_count"] = group.size()
        summary[f"{name}_area_m2"] = group.area_m2.sum()
    summary["matched_overlap_m2"] = 0.0
    if len(matches):
        matched = matches.copy()
        matched["ANALYSIS_ID"] = globfp.ANALYSIS_ID.to_numpy()[matched.candidate_index.astype(int)]
        matched["overlap_area_m2"] = matched.candidate_coverage.to_numpy() * globfp.area_m2.to_numpy()[matched.candidate_index.astype(int)]
        summary["matched_overlap_m2"] = matched.groupby("ANALYSIS_ID").overlap_area_m2.sum()
    numeric = summary.fillna(0)
    overlap = pd.to_numeric(numeric.matched_overlap_m2, errors="coerce").fillna(0).to_numpy(dtype="float64")
    denom = (
        pd.to_numeric(numeric.overture_area_m2, errors="coerce").fillna(0).to_numpy(dtype="float64")
        + pd.to_numeric(numeric.globfp3d_area_m2, errors="coerce").fillna(0).to_numpy(dtype="float64")
        - overlap
    )
    iou = np.full(len(summary), np.nan, dtype="float64")
    np.divide(overlap, denom, out=iou, where=denom > 0)
    summary["geometry_iou_proxy"] = iou
    summary = summary.reset_index()
    return out.merge(summary, on="ANALYSIS_ID", how="left")


def plot_overview(data, city_name, path):
    bounds = data.total_bounds
    resolution = max(30.0, max(bounds[2] - bounds[0], bounds[3] - bounds[1]) / 1800)
    width = max(1, int(math.ceil((bounds[2] - bounds[0]) / resolution)))
    height = max(1, int(math.ceil((bounds[3] - bounds[1]) / resolution)))
    transform = rasterio.transform.from_origin(bounds[0], bounds[3], resolution, resolution)
    codes = data.geometry_source.map({
        "OpenStreetMap": 1, "Overture_nonOSM": 2, "3D-GloBFP_gapfill": 3,
    }).to_numpy()
    order = np.argsort(codes)[::-1]
    source = rasterize(((data.geometry.iloc[i], int(codes[i])) for i in order),
                       out_shape=(height, width), transform=transform, fill=0, dtype="uint8")
    heights = rasterize(((g, float(h)) for g, h in zip(data.geometry, data.height_best_m) if pd.notna(h)),
                        out_shape=(height, width), transform=transform, fill=np.nan, dtype="float32")
    from matplotlib.colors import BoundaryNorm, ListedColormap
    import matplotlib.patches as patches
    fig, axes = plt.subplots(1, 2, figsize=(12, 6), constrained_layout=True)
    cmap = ListedColormap(["white", "#2b6cb0", "#ed8936", "#38a169"])
    axes[0].imshow(source, cmap=cmap, norm=BoundaryNorm([-.5,.5,1.5,2.5,3.5], 4))
    axes[0].legend(handles=[
        patches.Patch(color="#2b6cb0", label="OpenStreetMap"),
        patches.Patch(color="#ed8936", label="Overture non-OSM"),
        patches.Patch(color="#38a169", label="3D-GloBFP gap-fill"),
    ], loc="lower left", fontsize=8)
    axes[0].set_title("Selected geometry source")
    finite = data.height_best_m.dropna()
    vmax = float(finite.quantile(.98)) if len(finite) else 10
    image = axes[1].imshow(heights, cmap="viridis", vmin=0, vmax=max(3, vmax))
    axes[1].set_title("Best vector-derived height (m)")
    fig.colorbar(image, ax=axes[1], shrink=.7, label="metres")
    for ax in axes: ax.set_axis_off()
    fig.suptitle(f"{city_name}: reduced vector integration", fontsize=15)
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def process(city_slug: str, force: bool = False):
    city_input = CITIES / city_slug / "inputs"
    out_analysis = OUTPUTS / city_slug / "analysis"
    out_integrated = OUTPUTS / city_slug / "integrated"
    out_analysis.mkdir(parents=True, exist_ok=True)
    out_integrated.mkdir(parents=True, exist_ok=True)
    summary_path = out_integrated / "summary.json"
    if summary_path.exists() and not force:
        print(f"{city_slug}: existing output", flush=True)
        return json.loads(summary_path.read_text())
    metadata = json.loads((city_input / "metadata.json").read_text())
    crs = f"EPSG:{metadata['analysis_epsg']}"
    segments = gpd.read_parquet(city_input / "segments.parquet").to_crs(crs)
    aoi = gpd.read_parquet(city_input / "aoi.parquet").to_crs(crs).geometry.union_all()
    print(f"{city_slug}: loading sources", flush=True)
    overture, globfp = load_sources(city_slug, crs, aoi, metadata["bbox_wgs84"])
    print(f"{city_slug}: Overture {len(overture):,}; 3D-GloBFP {len(globfp):,}", flush=True)
    selected_ov = selected_overture(overture, city_slug)
    matches = add_near_matches(globfp, selected_ov, overlap_matches(globfp, selected_ov))
    duplicate = matches.relation.isin(["overlap_duplicate", "near_duplicate"])
    suppressed = set(matches.loc[duplicate, "candidate_index"].astype(int))
    keep = np.array([i not in suppressed for i in range(len(globfp))])
    selected_gl = selected_globfp(globfp.loc[keep].reset_index(drop=True), city_slug)
    integrated = gpd.GeoDataFrame(pd.concat([selected_ov, selected_gl], ignore_index=True),
                                  geometry="geometry", crs=crs)
    integrated["globfp_support"] = False
    supported = matches.loc[duplicate, "preferred_index"].dropna().astype(int).unique()
    integrated.loc[supported, "globfp_support"] = True
    integrated["osm_version"] = pd.to_numeric(
        integrated.source_record_id.astype("string").str.extract(r"@(\d+)$")[0], errors="coerce"
    ).astype("Int32")
    integrated["osm_multiple_versions"] = integrated.osm_version.fillna(0).ge(2)
    height = attach_globfp_point_heights(integrated, globfp)
    integrated["height_globfp_vector_m"] = height.height_globfp_vector_m.to_numpy()
    integrated["globfp_height_id"] = height.globfp_height_id.to_numpy()
    choose_vector_height(integrated)
    osm = integrated.geometry_source.eq("OpenStreetMap")
    integrated["geometry_confidence"] = "low"
    integrated.loc[osm, "geometry_confidence"] = "medium"
    integrated.loc[osm & (integrated.globfp_support | integrated.osm_multiple_versions), "geometry_confidence"] = "high"
    integrated.loc[~osm & integrated.globfp_support, "geometry_confidence"] = "medium"
    integrated.loc[integrated.geometry_source.eq("3D-GloBFP_gapfill") & integrated.height_best_m.notna(),
                   "geometry_confidence"] = "medium"
    integrated["selection_reason"] = np.select([
        integrated.geometry_source.eq("OpenStreetMap") & integrated.globfp_support,
        integrated.geometry_source.eq("OpenStreetMap") & integrated.osm_multiple_versions,
        integrated.geometry_source.eq("OpenStreetMap"),
        integrated.geometry_source.eq("Overture_nonOSM") & integrated.globfp_support,
        integrated.geometry_source.eq("Overture_nonOSM"),
    ], ["OSM_preferred_supported", "OSM_preferred_multiple_versions", "OSM_preferred_single_version",
        "Overture_gapfill_3D_supported", "Overture_gapfill"], default="3D-GloBFP_gapfill")
    integrated["review_required"] = integrated.geometry_confidence.eq("low")

    int_seg = assign_segments(integrated, segments)
    integrated["ANALYSIS_ID"] = int_seg.ANALYSIS_ID.to_numpy()
    integrated["SEGMENT_UID"] = int_seg.SEGMENT_UID.to_numpy()
    for source in (overture, globfp):
        fields = assign_segments(source, segments)
        source["ANALYSIS_ID"] = fields.ANALYSIS_ID.to_numpy()
        source["SEGMENT_UID"] = fields.SEGMENT_UID.to_numpy()

    lineage = pd.DataFrame({
        "integrated_id": integrated.integrated_id, "role": "selected_geometry",
        "source_dataset": integrated.geometry_dataset, "source_id": integrated.geometry_source_id,
        "relation": "selected", "candidate_coverage": 1.0, "iou": 1.0,
    })
    suppressed_rows = matches.loc[duplicate].copy()
    if len(suppressed_rows):
        suppressed_rows["integrated_id"] = selected_ov.integrated_id.to_numpy()[suppressed_rows.preferred_index.astype(int)]
        suppressed_rows["role"] = "supporting_or_suppressed_duplicate"
        suppressed_rows["source_dataset"] = "3D-GloBFP / GBA-correlated family"
        suppressed_rows["source_id"] = globfp.globfp_id.to_numpy()[suppressed_rows.candidate_index.astype(int)]
    cols = ["integrated_id", "role", "source_dataset", "source_id", "relation",
            "candidate_coverage", "iou", "match_structure"]
    lineage = pd.concat([lineage.reindex(columns=cols), suppressed_rows.reindex(columns=cols)], ignore_index=True)

    seg_summary = segment_summary(segments, integrated, overture, globfp, matches)
    integrated.to_parquet(out_integrated / "best_available_footprints.parquet", index=False)
    lineage.to_parquet(out_integrated / "lineage.parquet", index=False)
    seg_summary.to_parquet(out_analysis / "segment_vector_summary.parquet", index=False)
    seg_summary.to_file(out_analysis / "segment_vector_summary.gpkg", layer="segment_summary", driver="GPKG")
    plot_overview(integrated, metadata["city_name"], out_integrated / "overview.png")
    summary = {
        "city_slug": city_slug, "city_name": metadata["city_name"], "country": metadata["country"],
        "aoi_area_km2": metadata["aoi_area_km2"], "segments": int(len(segments)),
        "input_overture": int(len(overture)), "input_globfp3d": int(len(globfp)),
        "integrated_buildings": int(len(integrated)),
        "geometry_source_counts": {str(k): int(v) for k,v in integrated.geometry_source.value_counts().items()},
        "suppressed_globfp_duplicates": int(len(suppressed)),
        "geometry_confidence_counts": {str(k): int(v) for k,v in integrated.geometry_confidence.value_counts().items()},
        "vector_height_available": int(integrated.height_best_m.notna().sum()),
        "vector_height_available_pct": float(100 * integrated.height_best_m.notna().mean()) if len(integrated) else 0,
        "invalid_geometries": int((~integrated.geometry.is_valid).sum()),
        "unassigned_segments": int(integrated.ANALYSIS_ID.isna().sum()),
        "method": "Reduced portfolio vector workflow: OSM > other Overture > nonduplicating 3D-GloBFP/GBA-family geometry.",
    }
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    process(args.city, args.force)


if __name__ == "__main__":
    main()
