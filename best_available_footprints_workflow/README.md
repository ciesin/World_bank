# Best-available integrated building footprints

This folder is a reusable, non-city-specific workflow for creating an attributed
best-available building-footprint layer from:

- **Overture Maps Buildings**, including its OSM and non-OSM source lineage; and
- **3D-GloBFP**, used both as a footprint candidate and a source of vector height.

It deliberately excludes all Juba-specific code and all WSF processing. It also
does not download or analyse Google 2.5D, TEMPO, or GBA.Height rasters. Those
products are not required to create the integrated footprint geometry.

Source documentation:

- [Overture Maps Python client and downloader](https://docs.overturemaps.org/getting-data/overturemaps-py/)
- [3D-GloBFP official Figshare record, Part I](https://figshare.com/articles/dataset/3D-GloBFP_the_first_global_three-dimensional_building_footprint_dataset_PART_grid_ID_0-400_/28879733)

## What the workflow produces

For a city slug such as `example_city`, outputs are written to:

```text
outputs/example_city/
├── best_available_footprints.parquet
├── best_available_footprints.gpkg
├── footprint_lineage.parquet
├── footprint_lineage.csv
├── overview.png
└── summary.json
```

The building layer includes source IDs, provider, dataset, licence, source dates,
the date used for overlap resolution, footprint area, native height and floors,
3D-GloBFP vector height, selected height, confidence, selection reason, review
flag, and optional reporting-segment identifiers.

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

## Step-by-step run

The same workflow can be run in stages:

```bash
.venv/bin/python scripts/prepare_city.py \
  --city-name "Example City" --country "Example Country" \
  --aoi /absolute/path/to/aoi.geojson

.venv/bin/python scripts/download_overture.py --city example_city
.venv/bin/python scripts/download_3d_globfp.py --city example_city
.venv/bin/python scripts/build_integrated_footprints.py --city example_city
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
    └── globfp3d_clipped.parquet
```

## Important limitations

- “Best available” means newest among the two included footprint families under
  the documented overlap rule. It is not an independent accuracy assessment.
- Source dates are not equivalent to imagery-acquisition dates in every upstream
  dataset.
- A newer small feature can suppress an older large feature when their overlap
  reaches the threshold. Review the lineage and `review_required` attributes for
  operational use.
- OSM-derived Overture records carry ODbL licensing. 3D-GloBFP is distributed
  under CC BY 4.0. Preserve per-feature provenance and obtain appropriate legal
  review before redistribution.
- Large AOIs may require substantial download space and processing memory. The
  intended unit is one city or a similarly sized urban AOI.
