#!/usr/bin/env python3
"""Compare Juba vector footprint geometry by neighborhood and matched objects."""

from __future__ import annotations

import json
import math
from itertools import combinations
from pathlib import Path

import geopandas as gpd
import matplotlib
import numpy as np
import pandas as pd
import shapely
from matplotlib import pyplot as plt
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components


matplotlib.use("Agg")

ROOT = Path(__file__).resolve().parents[1]
SEGMENTS_PATH = ROOT / "outputs/juba_neighborhood_consistency.gpkg"
CRS = "EPSG:32636"
MIN_FEATURE_AREA_M2 = 1.0
MIN_INTERSECTION_AREA_M2 = 1.0
MIN_SMALLER_OVERLAP = 0.10

SOURCES = {
    "Overture": (ROOT / "data/processed/overture_juba_expanded.parquet", "id"),
    "GlobalBuildingAtlas": (
        ROOT / "data/processed/global_building_atlas_juba_expanded.parquet",
        "id",
    ),
    "3D-GloBFP": (ROOT / "data/processed/3d_globfp_juba_expanded.parquet", "BFID"),
}
SLUG = {
    "Overture": "overture",
    "GlobalBuildingAtlas": "gba",
    "3D-GloBFP": "globfp3d",
}


def pair_slug(left, right):
    return f"{SLUG[left]}_vs_{SLUG[right]}"


def safe_ratio(numerator, denominator):
    return float(numerator / denominator) if denominator > 0 else np.nan


def rectangle_angles(geometries):
    if len(geometries) == 0:
        return np.array([], dtype="float64")
    rectangles = shapely.minimum_rotated_rectangle(geometries)
    coords, indexes = shapely.get_coordinates(rectangles, return_index=True)
    counts = np.bincount(indexes, minlength=len(geometries))
    if np.all(counts == 5):
        points = coords.reshape(len(geometries), 5, 2)
        edges = points[:, 1:5, :] - points[:, :4, :]
        lengths = np.hypot(edges[:, :, 0], edges[:, :, 1])
        longest = np.argmax(lengths, axis=1)
        selected = edges[np.arange(len(geometries)), longest]
        return np.degrees(np.arctan2(selected[:, 1], selected[:, 0])) % 180
    result = np.full(len(geometries), np.nan, dtype="float64")
    for index in range(len(geometries)):
        points = coords[indexes == index]
        if len(points) < 4:
            continue
        edges = points[1:5] - points[:4]
        lengths = np.hypot(edges[:, 0], edges[:, 1])
        edge = edges[int(np.argmax(lengths))]
        result[index] = math.degrees(math.atan2(edge[1], edge[0])) % 180
    return result


def orientation_difference(left, right):
    difference = np.abs(left - right)
    return np.minimum(difference, 180 - difference)


def load_sources(segments):
    coverage = segments.geometry.union_all()
    loaded = {}
    for source, (path, id_field) in SOURCES.items():
        data = gpd.read_parquet(path).to_crs(CRS)
        # Expanded source Parquets were already clipped to this exact segment union.
        data = data[[id_field, "geometry"]].copy().reset_index(drop=True)
        data = data[
            data.geometry.notna()
            & ~data.geometry.is_empty
            & (shapely.area(data.geometry.to_numpy()) >= MIN_FEATURE_AREA_M2)
        ].copy().reset_index(drop=True)
        data["source_row"] = np.arange(len(data), dtype="int32")
        data["source_id"] = data[id_field].astype(str)
        data["area_m2"] = shapely.area(data.geometry.to_numpy())
        data["ID_SEG"] = assign_segments(data.geometry, segments)
        data = data[data.ID_SEG >= 0].reset_index(drop=True)
        data["source_row"] = np.arange(len(data), dtype="int32")
        loaded[source] = data[["source_row", "source_id", "ID_SEG", "area_m2", "geometry"]]
        loaded[source].to_parquet(
            ROOT / f"data/processed/juba_geometry_{SLUG[source]}_footprints.parquet",
            index=False,
        )
        print(f"{source}: {len(data):,} footprints", flush=True)
    return loaded, coverage


def assign_segments(geometries, segments):
    points = gpd.GeoDataFrame(
        {"source_index": np.arange(len(geometries))},
        geometry=shapely.point_on_surface(np.asarray(geometries)),
        crs=CRS,
    )
    joined = gpd.sjoin(
        points, segments[["ID_SEG", "geometry"]], how="left", predicate="within"
    )
    joined = joined[~joined.index.duplicated(keep="first")]
    result = np.full(len(geometries), -1, dtype="int32")
    valid = joined.ID_SEG.notna().to_numpy()
    result[joined.index.to_numpy()[valid]] = joined.ID_SEG.to_numpy()[valid].astype("int32")
    return result


def neighborhood_unions(source, segments):
    joined = gpd.sjoin(
        source[["source_row", "geometry"]],
        segments[["ID_SEG", "geometry"]],
        how="inner",
        predicate="intersects",
    )
    segment_geometry = segments.geometry.to_numpy()[joined.index_right.to_numpy()]
    clipped = shapely.intersection(joined.geometry.to_numpy(), segment_geometry)
    areas = shapely.area(clipped)
    table = pd.DataFrame(
        {
            "ID_SEG": joined.ID_SEG.to_numpy(dtype="int32"),
            "source_row": joined.source_row.to_numpy(dtype="int32"),
            "area_m2": areas,
        }
    )
    table["geometry"] = clipped
    table = table[table.area_m2 > 0]
    records = []
    for identifier, group in table.groupby("ID_SEG", sort=False):
        records.append(
            {
                "ID_SEG": int(identifier),
                "feature_count_intersecting": int(group.source_row.nunique()),
                "geometry": shapely.union_all(group.geometry.to_numpy()),
            }
        )
    result = gpd.GeoDataFrame(records, geometry="geometry", crs=CRS)
    result["union_area_m2"] = result.geometry.area
    return segments[["ID_SEG"]].merge(result, on="ID_SEG", how="left")


def union_pair_metrics(left_name, right_name, left, right, segments):
    left_map = left.set_index("ID_SEG")
    right_map = right.set_index("ID_SEG")
    rows = []
    for identifier in segments.ID_SEG:
        left_row = left_map.loc[identifier]
        right_row = right_map.loc[identifier]
        ga, gb = left_row.geometry, right_row.geometry
        if ga is None or gb is None or shapely.is_missing(ga) or shapely.is_missing(gb):
            intersection_area = 0.0
            area_a = 0.0 if ga is None or shapely.is_missing(ga) else float(ga.area)
            area_b = 0.0 if gb is None or shapely.is_missing(gb) else float(gb.area)
        else:
            area_a, area_b = float(ga.area), float(gb.area)
            intersection_area = float(ga.intersection(gb).area)
        union_area = area_a + area_b - intersection_area
        rows.append(
            {
                "ID_SEG": int(identifier),
                "source_a": left_name,
                "source_b": right_name,
                "features_a": int(left_row.feature_count_intersecting)
                if pd.notna(left_row.feature_count_intersecting)
                else 0,
                "features_b": int(right_row.feature_count_intersecting)
                if pd.notna(right_row.feature_count_intersecting)
                else 0,
                "area_a_ha": area_a / 10_000,
                "area_b_ha": area_b / 10_000,
                "intersection_ha": intersection_area / 10_000,
                "union_ha": union_area / 10_000,
                "iou": safe_ratio(intersection_area, union_area),
                "dice": safe_ratio(2 * intersection_area, area_a + area_b),
                "overlap_pct_a": 100 * safe_ratio(intersection_area, area_a),
                "overlap_pct_b": 100 * safe_ratio(intersection_area, area_b),
                "exclusive_a_ha": (area_a - intersection_area) / 10_000,
                "exclusive_b_ha": (area_b - intersection_area) / 10_000,
                "symmetric_difference_ha": (union_area - intersection_area) / 10_000,
            }
        )
    return pd.DataFrame(rows)


def candidate_edges(left, right):
    candidates = gpd.sjoin(
        left[["geometry"]], right[["geometry"]], how="inner", predicate="intersects"
    )
    left_index = candidates.index.to_numpy(dtype="int32")
    right_index = candidates.index_right.to_numpy(dtype="int32")
    intersections = shapely.area(
        shapely.intersection(
            left.geometry.to_numpy()[left_index], right.geometry.to_numpy()[right_index]
        )
    )
    area_left = left.area_m2.to_numpy()[left_index]
    area_right = right.area_m2.to_numpy()[right_index]
    smaller_overlap = intersections / np.minimum(area_left, area_right)
    keep = (intersections >= MIN_INTERSECTION_AREA_M2) & (
        smaller_overlap >= MIN_SMALLER_OVERLAP
    )
    return left_index[keep], right_index[keep]


def component_members(labels, component_count):
    order = np.argsort(labels, kind="stable")
    counts = np.bincount(labels, minlength=component_count)
    starts = np.r_[0, np.cumsum(counts)]
    return order, counts, starts


def simple_one_to_one_rows(pair, labels_a, labels_b, counts_a, counts_b, left, right):
    component_ids = np.flatnonzero((counts_a == 1) & (counts_b == 1))
    first_a = np.full(len(counts_a), -1, dtype="int32")
    first_b = np.full(len(counts_b), -1, dtype="int32")
    first_a[labels_a] = np.arange(len(labels_a), dtype="int32")
    first_b[labels_b] = np.arange(len(labels_b), dtype="int32")
    ai, bi = first_a[component_ids], first_b[component_ids]
    ga, gb = left.geometry.to_numpy()[ai], right.geometry.to_numpy()[bi]
    area_a, area_b = left.area_m2.to_numpy()[ai], right.area_m2.to_numpy()[bi]
    intersection = shapely.area(shapely.intersection(ga, gb))
    union = area_a + area_b - intersection
    perimeter_a, perimeter_b = shapely.length(ga), shapely.length(gb)
    compact_a = 4 * np.pi * area_a / np.maximum(perimeter_a**2, 1e-9)
    compact_b = 4 * np.pi * area_b / np.maximum(perimeter_b**2, 1e-9)
    angle_a, angle_b = rectangle_angles(ga), rectangle_angles(gb)
    group_geometry = shapely.union(ga, gb)
    segment_id = left.ID_SEG.to_numpy()[ai]
    frame = gpd.GeoDataFrame(
        {
            "group_id": [f"{pair}_{value}" for value in component_ids],
            "component": component_ids,
            "ID_SEG": segment_id,
            "match_type": "one_to_one",
            "count_a": 1,
            "count_b": 1,
            "rows_a": ai.astype(str),
            "rows_b": bi.astype(str),
            "area_a_m2": area_a,
            "area_b_m2": area_b,
            "intersection_m2": intersection,
            "union_m2": union,
            "iou": intersection / union,
            "dice": 2 * intersection / (area_a + area_b),
            "overlap_pct_a": 100 * intersection / area_a,
            "overlap_pct_b": 100 * intersection / area_b,
            "centroid_distance_m": shapely.distance(shapely.centroid(ga), shapely.centroid(gb)),
            "hausdorff_distance_m": shapely.hausdorff_distance(ga, gb),
            "area_ratio_a_over_b": area_a / area_b,
            "perimeter_ratio_a_over_b": perimeter_a / perimeter_b,
            "compactness_difference": np.abs(compact_a - compact_b),
            "orientation_difference_deg": orientation_difference(angle_a, angle_b),
        },
        geometry=group_geometry,
        crs=CRS,
    )
    return frame


def unmatched_rows(pair, side, component_ids, labels, data):
    first = np.full(int(labels.max()) + 1, -1, dtype="int32")
    first[labels] = np.arange(len(labels), dtype="int32")
    indexes = first[component_ids]
    areas = data.area_m2.to_numpy()[indexes]
    zeros = np.zeros(len(indexes), dtype="float64")
    if side == "a":
        count_a, count_b = np.ones(len(indexes), dtype="int16"), np.zeros(len(indexes), dtype="int16")
        area_a, area_b = areas, zeros
        rows_a, rows_b = indexes.astype(str), np.repeat("", len(indexes))
        match_type = "unmatched_a"
    else:
        count_a, count_b = np.zeros(len(indexes), dtype="int16"), np.ones(len(indexes), dtype="int16")
        area_a, area_b = zeros, areas
        rows_a, rows_b = np.repeat("", len(indexes)), indexes.astype(str)
        match_type = "unmatched_b"
    return gpd.GeoDataFrame(
        {
            "group_id": [f"{pair}_{value}" for value in component_ids],
            "component": component_ids,
            "ID_SEG": data.ID_SEG.to_numpy()[indexes],
            "match_type": match_type,
            "count_a": count_a,
            "count_b": count_b,
            "rows_a": rows_a,
            "rows_b": rows_b,
            "area_a_m2": area_a,
            "area_b_m2": area_b,
            "intersection_m2": zeros,
            "union_m2": areas,
            "iou": zeros,
            "dice": zeros,
            "overlap_pct_a": zeros,
            "overlap_pct_b": zeros,
            "centroid_distance_m": np.full(len(indexes), np.nan),
            "hausdorff_distance_m": np.full(len(indexes), np.nan),
            "area_ratio_a_over_b": np.full(len(indexes), np.nan),
            "perimeter_ratio_a_over_b": np.full(len(indexes), np.nan),
            "compactness_difference": np.full(len(indexes), np.nan),
            "orientation_difference_deg": np.full(len(indexes), np.nan),
        },
        geometry=data.geometry.to_numpy()[indexes],
        crs=CRS,
    )


def complex_rows(
    pair,
    component_ids,
    order_a,
    starts_a,
    order_b,
    starts_b,
    counts_a,
    counts_b,
    left,
    right,
):
    records, geometries = [], []
    left_geom, right_geom = left.geometry.to_numpy(), right.geometry.to_numpy()
    left_segments, right_segments = left.ID_SEG.to_numpy(), right.ID_SEG.to_numpy()
    for component in component_ids:
        ai = order_a[starts_a[component] : starts_a[component + 1]]
        bi = order_b[starts_b[component] : starts_b[component + 1]]
        ga = shapely.union_all(left_geom[ai]) if len(ai) else None
        gb = shapely.union_all(right_geom[bi]) if len(bi) else None
        area_a = float(ga.area) if ga is not None else 0.0
        area_b = float(gb.area) if gb is not None else 0.0
        intersection = float(ga.intersection(gb).area) if ga is not None and gb is not None else 0.0
        union_area = area_a + area_b - intersection
        if len(ai) == 1 and len(bi) > 1:
            match_type = "one_a_to_many_b"
        elif len(ai) > 1 and len(bi) == 1:
            match_type = "many_a_to_one_b"
        else:
            match_type = "many_to_many"
        segment_id = int(left_segments[ai[0]]) if len(ai) else int(right_segments[bi[0]])
        records.append(
            {
                "group_id": f"{pair}_{component}",
                "component": int(component),
                "ID_SEG": segment_id,
                "match_type": match_type,
                "count_a": int(len(ai)),
                "count_b": int(len(bi)),
                "rows_a": ",".join(map(str, ai)),
                "rows_b": ",".join(map(str, bi)),
                "area_a_m2": area_a,
                "area_b_m2": area_b,
                "intersection_m2": intersection,
                "union_m2": union_area,
                "iou": safe_ratio(intersection, union_area),
                "dice": safe_ratio(2 * intersection, area_a + area_b),
                "overlap_pct_a": 100 * safe_ratio(intersection, area_a),
                "overlap_pct_b": 100 * safe_ratio(intersection, area_b),
                "centroid_distance_m": float(ga.centroid.distance(gb.centroid)),
                "hausdorff_distance_m": float(ga.hausdorff_distance(gb)),
                "area_ratio_a_over_b": safe_ratio(area_a, area_b),
                "perimeter_ratio_a_over_b": safe_ratio(ga.length, gb.length),
                "compactness_difference": np.nan,
                "orientation_difference_deg": np.nan,
            }
        )
        geometries.append(shapely.union(ga, gb))
    return gpd.GeoDataFrame(records, geometry=geometries, crs=CRS)


def match_pair(left_name, right_name, left, right, segments):
    pair = pair_slug(left_name, right_name)
    print(f"Matching {left_name} vs {right_name}", flush=True)
    edge_a, edge_b = candidate_edges(left, right)
    node_count = len(left) + len(right)
    rows = np.r_[edge_a, len(left) + edge_b]
    cols = np.r_[len(left) + edge_b, edge_a]
    graph = coo_matrix(
        (np.ones(len(rows), dtype="uint8"), (rows, cols)), shape=(node_count, node_count)
    ).tocsr()
    component_count, labels = connected_components(graph, directed=False)
    labels_a, labels_b = labels[: len(left)], labels[len(left) :]
    order_a, counts_a, starts_a = component_members(labels_a, component_count)
    order_b, counts_b, starts_b = component_members(labels_b, component_count)

    one_ids = np.flatnonzero((counts_a == 1) & (counts_b == 1))
    unmatched_a_ids = np.flatnonzero((counts_a == 1) & (counts_b == 0))
    unmatched_b_ids = np.flatnonzero((counts_a == 0) & (counts_b == 1))
    complex_ids = np.flatnonzero((counts_a > 0) & (counts_b > 0) & ~((counts_a == 1) & (counts_b == 1)))
    frames = [
        simple_one_to_one_rows(pair, labels_a, labels_b, counts_a, counts_b, left, right),
        unmatched_rows(pair, "a", unmatched_a_ids, labels_a, left),
        unmatched_rows(pair, "b", unmatched_b_ids, labels_b, right),
        complex_rows(
            pair, complex_ids, order_a, starts_a, order_b, starts_b,
            counts_a, counts_b, left, right,
        ),
    ]
    groups = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), geometry="geometry", crs=CRS)
    groups["source_a"] = left_name
    groups["source_b"] = right_name
    groups.to_parquet(ROOT / f"outputs/juba_geometry_match_groups_{pair}.parquet", index=False)
    review = groups[(groups.match_type != "one_to_one") & (groups.union_m2 >= 25)].copy()
    review.to_file(
        ROOT / f"outputs/juba_geometry_review_groups_{pair}.gpkg",
        layer="review_groups",
        driver="GPKG",
    )

    summaries = []
    for identifier in segments.ID_SEG:
        subset = groups[groups.ID_SEG == identifier]
        both = (subset.count_a > 0) & (subset.count_b > 0)
        one = subset.match_type == "one_to_one"
        total_a, total_b = int(subset.count_a.sum()), int(subset.count_b.sum())
        matched_a = int(subset.loc[both, "count_a"].sum())
        matched_b = int(subset.loc[both, "count_b"].sum())
        counts = subset.match_type.value_counts()
        summaries.append(
            {
                "ID_SEG": int(identifier),
                "source_a": left_name,
                "source_b": right_name,
                "features_a": total_a,
                "features_b": total_b,
                "matched_features_a_pct": 100 * matched_a / total_a if total_a else np.nan,
                "matched_features_b_pct": 100 * matched_b / total_b if total_b else np.nan,
                "one_to_one_groups": int(counts.get("one_to_one", 0)),
                "one_a_to_many_b_groups": int(counts.get("one_a_to_many_b", 0)),
                "many_a_to_one_b_groups": int(counts.get("many_a_to_one_b", 0)),
                "many_to_many_groups": int(counts.get("many_to_many", 0)),
                "unmatched_a_groups": int(counts.get("unmatched_a", 0)),
                "unmatched_b_groups": int(counts.get("unmatched_b", 0)),
                "one_to_one_median_iou": float(subset.loc[one, "iou"].median()),
                "one_to_one_median_centroid_m": float(
                    subset.loc[one, "centroid_distance_m"].median()
                ),
                "one_to_one_median_hausdorff_m": float(
                    subset.loc[one, "hausdorff_distance_m"].median()
                ),
                "one_to_one_median_area_ratio": float(
                    subset.loc[one, "area_ratio_a_over_b"].median()
                ),
                "one_to_one_median_orientation_diff_deg": float(
                    subset.loc[one, "orientation_difference_deg"].median()
                ),
            }
        )
    print(
        f"  edges={len(edge_a):,}, groups={len(groups):,}, "
        f"one-to-one={len(one_ids):,}, review={len(review):,}",
        flush=True,
    )
    return groups, pd.DataFrame(summaries)


def make_neighborhood_layer(segments, union_metrics, match_summary):
    result = segments.copy()
    for frame in union_metrics:
        pair = pair_slug(frame.source_a.iloc[0], frame.source_b.iloc[0])
        selected = frame.set_index("ID_SEG")
        for column in [
            "iou", "dice", "overlap_pct_a", "overlap_pct_b",
            "exclusive_a_ha", "exclusive_b_ha", "symmetric_difference_ha",
        ]:
            result[f"{pair}_{column}"] = result.ID_SEG.map(selected[column])
    for frame in match_summary:
        pair = pair_slug(frame.source_a.iloc[0], frame.source_b.iloc[0])
        selected = frame.set_index("ID_SEG")
        for column in [
            "matched_features_a_pct", "matched_features_b_pct", "one_to_one_groups",
            "one_a_to_many_b_groups", "many_a_to_one_b_groups", "many_to_many_groups",
            "unmatched_a_groups", "unmatched_b_groups", "one_to_one_median_iou",
            "one_to_one_median_centroid_m", "one_to_one_median_hausdorff_m",
            "one_to_one_median_area_ratio", "one_to_one_median_orientation_diff_deg",
        ]:
            result[f"{pair}_{column}"] = result.ID_SEG.map(selected[column])
    return result


def plot_neighborhood_maps(path, mapped, frames, metric, title_label, vmin, vmax, cmap):
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.2), constrained_layout=True)
    image = None
    for ax, frame in zip(axes, frames):
        pair = pair_slug(frame.source_a.iloc[0], frame.source_b.iloc[0])
        column = f"{pair}_{metric}"
        mapped.plot(column=column, ax=ax, cmap=cmap, vmin=vmin, vmax=vmax,
                    edgecolor="0.25", linewidth=0.25)
        image = ax.collections[-1]
        ax.set_title(f"{frame.source_a.iloc[0]} vs\n{frame.source_b.iloc[0]}")
        ax.set_xticks([]); ax.set_yticks([]); ax.set_aspect("equal")
    fig.colorbar(image, ax=axes, label=title_label, shrink=0.75)
    fig.savefig(path, dpi=190, bbox_inches="tight")
    plt.close(fig)


def plot_match_types(path, counts_by_pair):
    categories = [
        "one_to_one", "one_a_to_many_b", "many_a_to_one_b",
        "many_to_many", "unmatched_a", "unmatched_b",
    ]
    labels = ["1:1", "1:A→many B", "many A→1 B", "many:many", "unmatched A", "unmatched B"]
    pairs, values = [], []
    for (left, right), counts in counts_by_pair.items():
        pairs.append(f"{left}\nvs {right}")
        total = int(counts.sum())
        values.append([100 * counts.get(category, 0) / total for category in categories])
    values = np.asarray(values)
    fig, ax = plt.subplots(figsize=(10, 5.2), constrained_layout=True)
    bottom = np.zeros(len(pairs))
    colors = plt.get_cmap("tab20")(np.linspace(0, 0.75, len(categories)))
    for index, (category, label) in enumerate(zip(categories, labels)):
        ax.bar(pairs, values[:, index], bottom=bottom, label=label, color=colors[index])
        bottom += values[:, index]
    ax.set_ylabel("Share of match groups (%)")
    ax.set_ylim(0, 100)
    ax.legend(ncol=3, frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.12))
    fig.savefig(path, dpi=190, bbox_inches="tight")
    plt.close(fig)


def main():
    out = ROOT / "outputs"
    segments = gpd.read_file(SEGMENTS_PATH, layer="neighborhood_consistency").to_crs(CRS)
    segments = segments[["ID_SEG", "GRID_ID", "geometry"]].copy()
    sources, coverage = load_sources(segments)

    unions = {name: neighborhood_unions(data, segments) for name, data in sources.items()}
    union_frames, match_frames, counts_by_pair, overall_rows = [], [], {}, []
    for left_name, right_name in combinations(SOURCES, 2):
        union_frame = union_pair_metrics(
            left_name, right_name, unions[left_name], unions[right_name], segments
        )
        union_frames.append(union_frame)
        groups, match_frame = match_pair(
            left_name, right_name, sources[left_name], sources[right_name], segments
        )
        counts = groups.match_type.value_counts()
        counts_by_pair[(left_name, right_name)] = counts
        match_frames.append(match_frame)
        one = groups[groups.match_type == "one_to_one"]
        overall_rows.append(
            {
                "source_a": left_name,
                "source_b": right_name,
                "median_neighborhood_union_iou": float(union_frame.iou.median()),
                "median_neighborhood_dice": float(union_frame.dice.median()),
                "median_matched_features_a_pct": float(match_frame.matched_features_a_pct.median()),
                "median_matched_features_b_pct": float(match_frame.matched_features_b_pct.median()),
                "total_match_groups": int(len(groups)),
                "one_to_one_groups": int(counts.get("one_to_one", 0)),
                "one_a_to_many_b_groups": int(counts.get("one_a_to_many_b", 0)),
                "many_a_to_one_b_groups": int(counts.get("many_a_to_one_b", 0)),
                "many_to_many_groups": int(counts.get("many_to_many", 0)),
                "unmatched_a_groups": int(counts.get("unmatched_a", 0)),
                "unmatched_b_groups": int(counts.get("unmatched_b", 0)),
                "median_one_to_one_iou": float(one.iou.median()),
                "median_one_to_one_centroid_m": float(one.centroid_distance_m.median()),
                "median_one_to_one_hausdorff_m": float(one.hausdorff_distance_m.median()),
                "median_one_to_one_orientation_diff_deg": float(
                    one.orientation_difference_deg.median()
                ),
            }
        )
        del groups

    lookup = segments[["ID_SEG", "GRID_ID"]]
    union_all = pd.concat(union_frames, ignore_index=True).merge(lookup, on="ID_SEG", how="left")
    match_all = pd.concat(match_frames, ignore_index=True).merge(lookup, on="ID_SEG", how="left")
    union_all.to_csv(out / "juba_geometry_neighborhood_union_pairwise.csv", index=False)
    match_all.to_csv(out / "juba_geometry_neighborhood_object_matching.csv", index=False)
    mapped = make_neighborhood_layer(segments, union_frames, match_frames)
    mapped.to_file(
        out / "juba_geometry_neighborhood_summary.gpkg",
        layer="geometry_summary",
        driver="GPKG",
    )
    mapped.to_parquet(out / "juba_geometry_neighborhood_summary.parquet", index=False)

    plot_neighborhood_maps(
        out / "juba_geometry_neighborhood_union_iou.png", mapped, union_frames,
        "iou", "Neighborhood union IoU", 0.5, 1.0, "viridis",
    )
    plot_neighborhood_maps(
        out / "juba_geometry_neighborhood_object_iou.png", mapped, match_frames,
        "one_to_one_median_iou", "Median one-to-one building IoU", 0.3, 1.0, "magma",
    )
    plot_match_types(out / "juba_geometry_match_type_shares.png", counts_by_pair)
    overall = pd.DataFrame(overall_rows)
    overall.to_csv(out / "juba_geometry_overall_summary.csv", index=False)

    metadata = {
        "analysis_area_km2": float(coverage.area / 1e6),
        "neighborhoods": int(len(segments)),
        "reporting_id_field": "GRID_ID",
        "sources": list(SOURCES),
        "feature_counts": {name: int(len(data)) for name, data in sources.items()},
        "neighborhood_union_metrics": [
            "IoU", "Dice", "overlap relative to each source", "exclusive area",
            "symmetric difference",
        ],
        "match_edge_rule": (
            f"Intersection >= {MIN_INTERSECTION_AREA_M2} m2 and intersection covers at least "
            f"{100 * MIN_SMALLER_OVERLAP:.0f}% of the smaller footprint."
        ),
        "match_types": [
            "one_to_one", "one_a_to_many_b", "many_a_to_one_b", "many_to_many",
            "unmatched_a", "unmatched_b",
        ],
        "one_to_one_metrics": [
            "IoU", "Dice", "centroid distance", "Hausdorff distance", "area ratio",
            "perimeter ratio", "compactness difference", "orientation difference",
        ],
        "neighborhood_assignment": "Match group assigned using the representative point segment of its first source-A member (or source-B for unmatched-B).",
        "interpretation": "Geometry consistency, not independent accuracy; sources have shared upstream inputs and different segmentation conventions.",
    }
    (out / "juba_geometry_analysis_metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n"
    )
    print("\nOverall geometry comparison", flush=True)
    print(overall.to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
