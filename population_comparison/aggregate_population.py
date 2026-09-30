#!/usr/bin/env python3
"""Aggregate five population-count rasters to every prepared segment."""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from exactextract import exact_extract

from common import PIPELINE, load_config, raster_path


def valid_coverage_fraction(values, coverage):
    """Fraction of a polygon's raster-cell coverage having non-NoData values."""
    coverage = np.asarray(coverage, dtype="float64")
    denominator = coverage.sum()
    if denominator <= 0:
        return np.nan
    mask = np.ma.getmaskarray(values)
    return float(coverage[~mask].sum() / denominator)


def nonnegative_population_sum(values, coverage):
    """Area-weighted population sum with nonphysical raster values set to zero.

    Population-count grids cannot have negative or non-finite population.
    Some GHS-POP tiles use -200 and NaN as water/background fill even after
    export. Treat those values as zero while retaining true raster NoData in
    the separate coverage calculation.
    """
    coverage = np.asarray(coverage, dtype="float64")
    data = np.asarray(np.ma.asarray(values).filled(np.nan), dtype="float64")
    data = np.where(np.isfinite(data) & (data > 0), data, 0.0)
    return float(np.sum(data * coverage))


def aggregate_one(raster: Path, segments: gpd.GeoDataFrame) -> tuple[np.ndarray, np.ndarray]:
    with rasterio.open(raster) as source:
        if source.count != 1:
            raise ValueError(f"Expected one population band: {raster}")
        projected = segments[["segment_id", "geometry"]].to_crs(source.crs)
        result = exact_extract(
            source,
            projected,
            [nonnegative_population_sum, valid_coverage_fraction],
            include_cols=["segment_id"],
            output="pandas",
            strategy="raster-sequential",
        ).set_index("segment_id").reindex(segments["segment_id"])
    totals = pd.to_numeric(
        result["nonnegative_population_sum"], errors="coerce"
    ).to_numpy(dtype="float64")
    coverage = 100 * pd.to_numeric(
        result["valid_coverage_fraction"], errors="coerce"
    ).to_numpy(dtype="float64")
    return totals, coverage


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", action="append")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--allow-missing", action="store_true")
    parser.add_argument("--skip-incomplete", action="store_true",
                        help="Skip cities whose complete raster bundle has not downloaded yet")
    args = parser.parse_args()
    config = load_config()
    product_names = list(config["products"])
    minimum_coverage = config["classification"]["minimum_valid_coverage_percent"]
    input_root = PIPELINE / "data" / "prepared_segments"
    files = sorted(input_root.glob("*.parquet"))
    if args.city:
        wanted = {name.casefold() for name in args.city}
        files = [
            path for path in files
            if str(pd.read_parquet(path, columns=["city_name"]).iloc[0].city_name).casefold() in wanted
        ]
    if not files:
        raise FileNotFoundError("No prepared city segment files; run prepare_segments.py")
    output_root = PIPELINE / "outputs" / "segment_population"
    output_root.mkdir(parents=True, exist_ok=True)

    for segment_file in files:
        stem = segment_file.stem
        output = output_root / f"{stem}.parquet"
        if output.exists() and not args.force:
            print(f"Exists: {output}", flush=True)
            continue
        expected_paths = {product: raster_path(product, stem) for product in product_names}
        unavailable = [product for product, path in expected_paths.items() if not path.exists()]
        if unavailable and args.skip_incomplete:
            print(f"Not ready: {stem} ({', '.join(unavailable)})", flush=True)
            continue
        segments = gpd.read_parquet(segment_file)
        missing = []
        for product in product_names:
            path = expected_paths[product]
            if not path.exists():
                missing.append(product)
                segments[f"pop_{product}"] = np.nan
                segments[f"coverage_pct_{product}"] = np.nan
                segments[f"density_{product}"] = np.nan
                continue
            totals, coverage = aggregate_one(path, segments)
            valid_totals = totals.copy()
            valid_totals[coverage < minimum_coverage] = np.nan
            segments[f"pop_{product}"] = valid_totals
            segments[f"coverage_pct_{product}"] = coverage
            segments[f"density_{product}"] = valid_totals / segments["segment_area_km2"].to_numpy()
        if missing and not args.allow_missing:
            raise FileNotFoundError(f"Missing rasters for {stem}: {missing}")

        density_columns = [f"density_{product}" for product in product_names]
        available_count = segments[density_columns].notna().sum(axis=1)
        consensus = segments[density_columns].median(axis=1, skipna=True)
        consensus[available_count < config["classification"]["minimum_valid_products"]] = np.nan
        threshold = config["classification"]["primary_density_threshold_people_per_km2"]
        segments["valid_product_count"] = available_count.astype("int8")
        segments["consensus_density_people_per_km2"] = consensus
        segments["density_class"] = pd.Series(pd.NA, index=segments.index, dtype="string")
        segments.loc[consensus < threshold, "density_class"] = "peri_urban"
        segments.loc[consensus >= threshold, "density_class"] = "urban_center"
        for sensitivity in config["classification"]["sensitivity_thresholds_people_per_km2"]:
            column = f"density_class_{int(sensitivity)}"
            segments[column] = pd.Series(pd.NA, index=segments.index, dtype="string")
            segments.loc[consensus < sensitivity, column] = "peri_urban"
            segments.loc[consensus >= sensitivity, column] = "urban_center"
        segments.to_parquet(output, index=False)
        print(f"Wrote {output} ({len(segments):,} segments; missing={missing})", flush=True)


if __name__ == "__main__":
    main()
