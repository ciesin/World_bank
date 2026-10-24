# 93-city segment population comparison

This folder contains a staged, restartable pipeline for comparing five gridded
population products across the segment geometries in the 93 study-city buffers.
The full workflow was completed and validated on 2026-09-28.

## Study inputs

- City buffers: `../../built_up_analysis/cities93_v4_buffer.gpkg`
- Segment geometries: `../../built_up_analysis/segments_hexbin_20260821 (1).gpkg`
- Local LandScan nighttime raster: `../landscan-mosaic-2025-night-africa.tif`

Segments are assigned exactly once using their existing city label. Normalized
names handle routine spelling differences; the six labels with no direct match
are assigned to the city buffer having the largest geometric overlap. This
avoids duplicating segments where city buffers overlap. The original label and
the complete crosswalk are retained for audit.

## Population products

| Pipeline name | Source used | Epoch | Native resolution | Access |
|---|---|---:|---:|---|
| WorldPop Global2 | R2025A v1, constrained | 2025 | 100 m | Earth Engine community mirror of the official R2025A release |
| GHS-POP | R2023A `population_count` | 2025 | 100 m | Official Earth Engine collection |
| HRSL | v1.5+ overall population | nominal 2020 | about 30 m | Earth Engine community catalog |
| LandScan Mosaic Ambient | global Mosaic time series | 2024 | about 90 m | Earth Engine community catalog |
| LandScan Mosaic Nighttime | local Africa mosaic, v1.0 | 2025 | about 90 m | Existing local GeoTIFF |

Important source-year notes:

- The original LandScan Mosaic catalog collection linked for this work contains
  2024 images and currently covers only six of the twelve study countries. The
  configured global time-series collection covers every study city, but its
  latest ambient layer is 2024. The pipeline therefore labels it **2024**.
- The local nighttime raster's internal metadata explicitly identifies it as
  the 2025 LandScan Mosaic nighttime product.
- The HRSL collection exposes tile-specific version metadata (v1.5+) but not a
  single observation-year field. `nominal 2020` records the study convention;
  it should not be interpreted as a uniform raster timestamp.

Source documentation:

- [WorldPop API v2 and Global2 releases](https://api.worldpop.org/v2/)
- [GHS-POP R2023A](https://human-settlement.emergency.copernicus.eu/ghs_pop2023.php)
- [HRSL Earth Engine catalog entry](https://gee-community-catalog.org/projects/hrsl/)
- [LandScan Mosaic catalog entry](https://gee-community-catalog.org/projects/landscan_mosaic/)
- [LandScan Mosaic global time series](https://gee-community-catalog.org/projects/landcast/)

## Aggregation method

All five products store population counts per raster cell. For every segment,
the pipeline calculates:

`segment population = sum(cell population × fraction of cell covered by segment)`

This allocates boundary-cell population in proportion to polygon overlap. It
does not multiply population values by pixel area and does not reproject or
resample the population grids. Earth Engine exports preserve each product's
native grid. Zero is retained as valid population; NoData is not converted to
zero. Non-finite and negative population values are set to zero during
aggregation. This is needed because some exported GHS-POP coastal/background
cells contain NaN or the nonphysical fill value -200 even after export. True
raster NoData is still retained separately in the coverage calculation.

Each product also receives a valid-coverage percentage. A segment-product value
is excluded from classification and analysis when less than 95% of its raster
footprint is valid. Raw geometry and coverage fields remain available for
audit.

## Urban-center versus peri-urban rule

The primary operational threshold is **1,500 people per km²**. This borrows the
density component of the international Degree of Urbanisation urban-centre
definition. The formal DEGURBA definition also applies the rule to 1 km cells,
requires contiguity, and requires at least 50,000 people in the cluster. Because
this analysis applies the threshold to existing neighborhood segments inside
preselected city buffers, its labels are an operational neighborhood
classification, not a formal DEGURBA classification.

To avoid privileging any one population product, each segment is classified
once from the median of its available product-specific population densities:

- `urban_center`: consensus median density >= 1,500 people/km²
- `peri_urban`: consensus median density < 1,500 people/km²
- unclassified: fewer than three products have at least 95% valid coverage

The threshold, minimum product count, and coverage requirement are all stored
in `config.json`. The pipeline also writes alternative classifications at 300,
900, and 1,500 people/km² for sensitivity checks. The pre-existing structural
label (`city_segment` versus `hexgrid`) is retained as `source_zone`, and a
cross-tabulation against the density class is written to the analysis folder.

Reference: [Eurostat Degree of Urbanisation methodology](https://ec.europa.eu/eurostat/en/web/degree-of-urbanisation/methodology).

## Requested outputs

Population-enriched segment files are written by city to
`outputs/segment_population/`. Each contains the five population estimates,
valid-coverage percentages, densities, consensus density, classification,
source geometry type, city population group, and geometry.

`outputs/analysis/` contains:

- six vertical five-product boxplots of population per segment:
  - urban centers and peri-urban areas for all cities;
  - urban centers and peri-urban areas for cities below 300,000; and
  - urban centers and peri-urban areas for cities of 300,000 or more;
- PNG and SVG versions, with both linear and symlog y-axis versions;
- `population_boxplot_statistics.csv` with n, median, quartiles, IQR, mean,
  and standard 1.5-IQR whiskers;
- 5 x 5 Spearman and Pearson correlation matrices for the same six groups;
- matching pairwise sample-size matrices and a long-form statistics CSV;
- classification and source-zone cross-tabulation CSVs; and
- overall and city-level product coverage summaries.

Boxplots suppress individual outlier circles. Correlations use pairwise-complete
segment values, so every matrix is accompanied by its pairwise `n` matrix.

## Completed run summary

- 93 cities and 2,265,624 unique segments were processed.
- 233,164 segments were classified as `urban_center`; 2,032,460 were
  classified as `peri_urban`.
- All segments had at least four valid products, so none were unclassified.
- WorldPop, GHS-POP, HRSL, and LandScan Ambient had valid values for every
  segment.
- LandScan Nighttime had valid values for 1,956,900 segments (86.37%). Its
  local Africa mosaic did not provide at least 95% valid coverage for 308,724
  segments distributed across 23 cities. Those product-segment observations
  remain missing and are excluded pairwise from plots and correlations; they
  are not filled with zero.
- All population outputs were checked after aggregation and contain no negative
  population estimates.

The primary result tables are `classification_summary.csv`,
`population_boxplot_statistics.csv`, `correlation_statistics_long.csv`,
`product_coverage_summary.csv`, and `product_coverage_by_city.csv` in the
analysis output folder.

## Staged use

The existing project virtual environment already contains the required Python
packages. From the project root:

```bash
.venv/bin/python pop_analysis/population_segment_pipeline/scripts/run_pipeline.py audit
.venv/bin/python pop_analysis/population_segment_pipeline/scripts/run_pipeline.py plan-ee
```

Those two commands only audit local files and write an Earth Engine export
plan. They do not download or submit anything.

After reviewing the plan, submit the native-grid raster exports explicitly:

```bash
.venv/bin/python pop_analysis/population_segment_pipeline/scripts/submit_earth_engine_exports.py \
  --submit --project ee-gyetman
```

There are 372 planned exports (93 cities x four cloud products). `--city`,
`--product`, and `--max-tasks` can be used for a pilot or a quota-sized batch.
The task manifest makes downloading and resuming deterministic.

Then monitor/download completed exports and run the local stages:

```bash
.venv/bin/python pop_analysis/population_segment_pipeline/scripts/download_earth_engine_exports.py \
  --project ee-gyetman
.venv/bin/python pop_analysis/population_segment_pipeline/scripts/run_pipeline.py prepare-segments
.venv/bin/python pop_analysis/population_segment_pipeline/scripts/run_pipeline.py prepare-nighttime
.venv/bin/python pop_analysis/population_segment_pipeline/scripts/run_pipeline.py aggregate
.venv/bin/python pop_analysis/population_segment_pipeline/scripts/run_pipeline.py analyze
```

The aggregation is partitioned by city and skips existing outputs by default,
so an interrupted run can resume. Use `--force` only when intentionally
replacing completed city outputs.
