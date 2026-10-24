# 93-city settlement extent and built-up footprint comparison

This pipeline implements an eight-product city-level comparison using the 93
buffers and their segment subdivisions.

## Completed run

The full workflow was completed on 2026-09-26. Final availability is 93 cities
for GHS-UCDB, GHS-FUA, Africapolis 2020, WSF Tracker, and Dynamic World; 85 for
GRID3; 85 for Google 2.5D; and 39 for the intentionally reduced administrative
boundary sample. Google 2.5D is unavailable for all eight Libyan study cities.
The consolidated geometries are valid and the analysis tables use
pairwise-complete cities.

Final files are under `outputs/`, with summary statistics, correlations, IoU
tables, and figures under `outputs/analysis/`.

## Inputs

Study geometry:

- `../cities93_v4_buffer.gpkg`, layer `cities93_v4_buffer` (93 cities)
- `../segments_hexbin_20260821 (1).gpkg`, layer
  `cities93_segments_hexbins` (2,265,624 segment polygons)

Five vector settlement boundaries:

1. GHS-UCDB R2024A v1.2
2. GHS-FUA UCDB2015 R2019A v1.0
3. GRID3 Settlement Extents 2024 (country-specific v3.x releases)
4. Africapolis 2020 urban extents (published later)
5. `city_adm_v4.gpkg` administrative boundaries (2026; intentionally missing
   for some cities)

Three reference built-up rasters:

1. WSF Tracker at 2025-01-01, read at native resolution from the public GeoZarr
2. Dynamic World 2025, using the median annual `built` probability >= 0.5
3. Google Open Buildings 2.5D 2023, using `building_presence` >= 0.5

Google 2.5D exports that are entirely zero across an urban buffer are treated
as unavailable rather than as evidence of zero buildings. This applies to the
seven all-zero Libyan exports in this run; Tubruq has no 2023 source image and
is also unavailable.

The Dynamic World composite and thresholds are explicit configuration choices,
not fixed requirements of the source dataset. They can be changed in
`config.json` before the analysis is run.

## Manual inputs

Africapolis does not expose a stable anonymous download URL through the supplied
portal, and the administrative file is on project Google Drive. Place the files
in `inputs/manual/` as described in `inputs/manual/README.md`. The acquisition
stage also searches `wb_buildings/data/raw` for existing matching copies.

## Raster preparation

`submit_earth_engine_exports.py` prepares one projected binary GeoTIFF export
per city for Dynamic World and Google 2.5D. Its default behavior is dry-run: it
writes the complete export plan without contacting Earth Engine. Passing
`--submit --project YOUR_PROJECT` creates Drive export tasks. Download completed
GeoTIFFs are downloaded from the generated Drive folder with
`download_earth_engine_exports.py --project YOUR_EARTH_ENGINE_PROJECT`.

`prepare_wsf_rasters.py` reads native level 0 of the official cloud GeoZarr and
creates one 10 m projected binary raster per city. For the requested 2025
snapshot it considers WSF date-index values 1–18 built by 2025-01-01.

## Segment classification

For each city and raster product, the pipeline:

1. overlays each segment on the binary built-up raster;
2. weights every built pixel by the fraction of its area covered by the
   segment;
3. calculates `built_up_area_m2 = sum(fractional built-pixel coverage) ×
   projected pixel area`;
4. flags the segment when the coverage-weighted built-up area is strictly
   greater than 40 m²;
5. dissolves flagged segments into one city footprint and clips it to the city
   buffer.

The confirmed rule is **>40 m² of built-up area after accounting for fractional
pixel coverage**. It is stored in `config.json` and is applied consistently to
all three raster products. It is a continuous area calculation rather than an
integer pixel-count rule. Segment audit files report both
`built_pixel_equivalents` (the fractional sum) and `built_up_area_m2`.

## Vector selection

All source polygons intersecting a city buffer are retained as candidates. One
boundary is selected per available city/product by ranking:

1. exact city ID match, when the source has a compatible ID;
2. normalized city-name match;
3. containment of the buffer's interior anchor point; and
4. largest area of intersection with the buffer.

The selected boundary is clipped to the buffer. The complete selection audit,
including candidate count and selection reason, is retained. Missing
administrative boundaries stay missing rather than being imputed. For
`city_adm_v4.gpkg`, selection requires an explicit normalized city-name match,
including the three documented renamed-city aliases in `config.json`; spatial
intersection alone is not sufficient.

## Analyses and outputs

`analyze_results.py` produces:

- `coverage_summary_statistics.csv`: n, median, quartiles, IQR, and mean buffer
  coverage for each product, for all cities, population <300,000, and
  population >=300,000;
- `buffer_coverage_by_product.png/.svg`: percent of buffer covered by product;
- Pearson and Spearman correlation matrices and long-form pair statistics for
  percent buffer coverage, including pairwise common-city sample sizes;
- per-city pairwise IoU for all available geometries; and
- median IoU matrices by the same three population groups, with n and IQR in a
  long-form table.

All correlation and IoU calculations use pairwise-complete cities. This matters
especially for the reduced administrative-boundary sample.

## Installation

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Staged execution

Use the stages in this order:

```bash
.venv/bin/python scripts/run_pipeline.py audit
.venv/bin/python scripts/run_pipeline.py acquire-vectors
.venv/bin/python scripts/run_pipeline.py plan-ee

# Review the plan, then explicitly submit Earth Engine exports:
.venv/bin/python scripts/submit_earth_engine_exports.py \
  --submit --project YOUR_EARTH_ENGINE_PROJECT

# Monitor and download completed Drive exports:
.venv/bin/python scripts/download_earth_engine_exports.py \
  --project YOUR_EARTH_ENGINE_PROJECT

# After all Earth Engine exports have downloaded:
.venv/bin/python scripts/run_pipeline.py prepare-wsf
.venv/bin/python scripts/run_pipeline.py build
.venv/bin/python scripts/run_pipeline.py analyze
```

`build --allow-missing` is available for diagnostic partial runs. It should not
be used for final results unless the missing-product audit has been reviewed.

## Provenance

- [GHS-UCDB and GHS-FUA downloads](https://human-settlement.emergency.copernicus.eu/download.php)
- [GRID3 Settlement Extents 2024](https://grid3.org/news/grid3-updates-settlement-extents-for-all-of-subsaharan-africa)
- [Africapolis](https://africapolis.org/data/downloadAdmin)
- [WSF Tracker raster access](https://worldsettlementfootprint.com/raster-download)
- [Dynamic World V1](https://developers.google.com/earth-engine/datasets/catalog/GOOGLE_DYNAMICWORLD_V1)
- [Google Open Buildings Temporal V1](https://developers.google.com/earth-engine/datasets/catalog/GOOGLE_Research_open-buildings-temporal_v1)
