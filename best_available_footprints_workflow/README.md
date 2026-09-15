# Best-available integrated building footprints

This folder is a reusable, non-city-specific workflow for creating an attributed
best-available building-footprint layer from:

- **Overture Maps Buildings**, including its OSM and non-OSM source lineage; and
- **3D-GloBFP**, used both as a footprint candidate and a source of vector height;
- **Google Open Buildings 2.5D Temporal**, using 2023 height and presence rasters;
- **TEMPO**, using the global 2023 Q4 building density and height rasters; and
- **GlobalBuildingAtlas GBA.Height**, using its native height tiles.

Raster height sources enrich the selected footprints but do not affect footprint
geometry or overlap resolution.

Source documentation:

- [Overture Maps Python client and downloader](https://docs.overturemaps.org/getting-data/overturemaps-py/)
- [3D-GloBFP official Figshare record, Part I](https://figshare.com/articles/dataset/3D-GloBFP_the_first_global_three-dimensional_building_footprint_dataset_PART_grid_ID_0-400_/28879733)
- [Google Open Buildings Temporal V1](https://developers.google.com/earth-engine/datasets/catalog/GOOGLE_Research_open-buildings-temporal_v1)
- [TEMPO building density and height data](https://github.com/microsoft/buildings)
- [GlobalBuildingAtlas data record](https://doi.org/10.14459/2025mp1782307)

## What the workflow produces

For a city slug such as `example_city`, outputs are written to:

```text
outputs/example_city/
├── best_available_footprints.parquet
├── best_available_footprints.gpkg
├── footprint_lineage.parquet
├── footprint_lineage.csv
├── height_enrichment_metadata.json
├── overview.png
└── summary.json
```

The building layer includes source IDs, provider, dataset, licence, source dates,
the date used for overlap resolution, footprint area, native height and floors,
3D-GloBFP vector height, Google 2.5D height and presence, TEMPO height and
density, GBA.Height, selected height, confidence, selection reason, review flag,
and optional reporting-segment identifiers.

The source-specific raster fields are `height_google_2_5d_m`,
`height_tempo_m`, and `height_gba_m`. The workflow retains the pre-raster result
in `height_best_vector_m` and `height_source_vector`, then recomputes
`height_best_m`, `height_source`, the source count, and inter-source range.

## Geometry-selection rule

All valid Overture and 3D-GloBFP polygons of at least 4 m² are candidates.

When the intersection covers **at least 20% of the smaller footprint**, the
features are treated as conflicting representations. The most recent footprint
is retained and the older footprint is removed in full. Smaller overlaps are
allowed. A 0.01 m² tolerance prevents boundary-touching and numerical slivers
from being treated as area overlaps.

Dates and tie-breaking are handled as follows:

1. Overture uses the latest valid `update_time` across all source records attached
   to the feature.
2. 3D-GloBFP uses its documented 2020 model vintage.
3. Known dates rank ahead of unknown dates.
4. Equal or unknown dates use OSM, then other Overture, then 3D-GloBFP, followed
   by stable feature ID.

The lineage files record each suppressed feature, the retained winner, both
dates, intersection area, and overlap fractions.

## Installation

From this folder, create an environment and install the dependencies:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

The `overturemaps` package supplies the command-line downloader used by the
workflow. 3D-GloBFP tiles are selected and downloaded directly from the ten
official Figshare article records.

Google 2.5D and TEMPO are streamed from official cloud-hosted COGs by default;
their small manifest/tile-index files are stored locally. GBA.Height's required
3 m tiles are selectively extracted from the official mediaTUM ZIP archives,
without downloading the complete parent archives. The mediaTUM public download
credentials are part of the official distribution and are embedded in the
acquisition script; environment credentials are not required. They can be
overridden with `GBA_FTP_USER`, `GBA_FTP_PASSWORD`, `GBA_REP_FTP_USER`, and
`GBA_REP_FTP_PASSWORD` when necessary.

## Required input

Supply one polygon AOI readable by GeoPandas, for example GeoJSON, GeoPackage,
Shapefile, or GeoParquet. The input must have a defined coordinate reference
system.

An optional polygon segment layer can also be supplied. If omitted, the complete
AOI is used as one reporting segment. Segments affect attributes and summaries;
they do not control building selection.

## One-command run

```bash
.venv/bin/python scripts/run_workflow.py \
  --city-name "Example City" \
  --country "Example Country" \
  --aoi /absolute/path/to/example_city_aoi.geojson
```

With optional reporting segments:

```bash
.venv/bin/python scripts/run_workflow.py \
  --city-name "Example City" \
  --country "Example Country" \
  --aoi /absolute/path/to/example_city_aoi.gpkg \
  --aoi-layer city_boundary \
  --segments /absolute/path/to/reporting_units.gpkg \
  --segments-layer reporting_units
```

Use `--city-slug` to override the automatically generated slug, `--analysis-crs`
to override the automatically selected local UTM CRS, and `--force` to replace
existing downloads and outputs.

Use `--download-tempo` to keep the intersecting TEMPO COGs locally. Use
`--skip-raster-heights` to create geometry and vector-derived heights only, or
`--skip-google-height`, `--skip-tempo-height`, and `--skip-gba-height` to omit
individual raster products. The default Google presence threshold is 0.5; the
default TEMPO density threshold is 0.001. Both can be changed on the one-command
runner.

## Step-by-step run

The same workflow can be run in stages:

```bash
.venv/bin/python scripts/prepare_city.py \
  --city-name "Example City" --country "Example Country" \
  --aoi /absolute/path/to/aoi.geojson

.venv/bin/python scripts/download_overture.py --city example_city
.venv/bin/python scripts/download_3d_globfp.py --city example_city
.venv/bin/python scripts/build_integrated_footprints.py --city example_city
.venv/bin/python scripts/acquire_height_sources.py --city example_city
.venv/bin/python scripts/enrich_raster_heights.py --city example_city
```

Each command supports `--help`.

## Intermediate data

Prepared inputs and downloaded source data are stored below `data/<city_slug>/`:

```text
data/example_city/
├── inputs/
│   ├── aoi.parquet
│   ├── aoi.geojson
│   ├── segments.parquet
│   └── metadata.json
└── sources/
    ├── overture_buildings.parquet
    ├── overture_clipped.parquet
    ├── globfp_tiles.json
    ├── globfp_tiles/
    ├── globfp3d_clipped.parquet
    ├── google_2_5d/manifests/
    ├── tempo_2023q4/            # only with --download-tempo
    ├── gba_height/
    └── height_sources.json
```

Shared TEMPO and GBA indexes are cached once below `data/shared/`.

## Raster-height assignment

Each raster is sampled at a point guaranteed to fall inside the selected
footprint. This is consistent and scalable for city-sized layers, but it means
the raster values describe the source cell containing that point, not an
independent measurement made specifically for that footprint.

- Google 2.5D uses band 2 height in metres only where band 3 building presence
  meets the configured threshold. The latest published annual layer is 2023.
- TEMPO uses band 2 multiplied by 100 to obtain metres, only where band 1
  building density meets the configured threshold. The default global layer is
  2023 Q4 at approximately 100 m.
- GBA.Height uses band 1 in metres from the official 3 m tiles.

Valid heights are restricted to 0.5–100 m. If multiple raster tiles cover the
same point, their valid samples are averaged. `height_best_m` uses this priority:
native footprint height, OSM floors × 3 m, 3D-GloBFP vector height, Google 2.5D,
GBA.Height, then TEMPO. Raster-selected heights receive low confidence.

## Important limitations

- “Best available” means newest among the two included footprint families under
  the documented overlap rule. It is not an independent accuracy assessment.
- Source dates are not equivalent to imagery-acquisition dates in every upstream
  dataset.
- Google 2.5D and TEMPO are gridded model outputs; assigning a cell value to a
  footprint does not turn it into a building-level observation.
- GBA.Height and TEMPO both use PlanetScope imagery, and GBA's modeling lineage
  overlaps other building sources. Source counts are product-availability
  counts, not counts of statistically independent evidence.
- GBA.Height is CC BY-NC 4.0 and therefore restricts commercial use of enriched
  outputs. TEMPO is CDLA-Permissive-2.0. Google 2.5D offers CC BY 4.0 or ODbL
  1.0 terms. Preserve provenance and review downstream licensing needs.
- A newer small feature can suppress an older large feature when their overlap
  reaches the threshold. Review the lineage and `review_required` attributes for
  operational use.
- OSM-derived Overture records carry ODbL licensing. 3D-GloBFP is distributed
  under CC BY 4.0. Preserve per-feature provenance and obtain appropriate legal
  review before redistribution.
- Large AOIs may require substantial download space and processing memory. The
  intended unit is one city or a similarly sized urban AOI.
