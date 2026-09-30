# 93-city permanent-water comparison

This is a staged, restartable pipeline for comparing OpenStreetMap (OSM),
Global River Widths from Landsat (GRWL), and JRC Global Surface Water (JRC-GSW)
within the 93 study-city buffers.

The full run completed on 2026-09-29. The 93-city input audit passed, the
configured Earth Engine collections returned the expected 60 months for
2020-2024 and 120 months for 2015-2024, and all 93 city rasters were downloaded
and analyzed. Lagos used four aligned Earth Engine export tiles because the
single large export remained in finalization; the tiles were mosaicked to the
same native JRC grid before analysis. Local stages are restartable and skip
completed outputs unless `--force` is used.

## Inputs

- City buffers: `../../built_up_analysis/cities93_v4_buffer.gpkg`
- Segment geometries: `../../built_up_analysis/segments_hexbin_20260821 (1).gpkg`
- GRWL V01.01: `../GRWL_vector_V01.01/` (829 local shapefile tiles)
- OSM: dated country extracts from Geofabrik, clipped and cached by study city
- JRC-GSW: exported from Earth Engine using project `ee-gyetman`

The segment partition follows the audited, non-duplicating city-name crosswalk
used by the population workflow. `ID_SEG` distinguishes central-city segments
(`city_segment`) from peri-urban hexbins (`hexgrid`). The urban/peri-urban groups
in this analysis are therefore structural source zones, not population-density
classes.

## Source definitions

### OSM water

The Overpass query is the logical union of:

- `natural=water` with `water=lake|pond|reservoir|lagoon|basin` (generic
  `natural=water` polygons without a `water` subtype are also retained)
- `natural=wetland`
- `waterway=river|stream|canal|drain|ditch`

Polygon features and linear waterways are retained separately. Presence on the
JRC 30 m grid includes both polygons and every grid cell touched by an included
line. Table 4.2 uses only the five linear `waterway` classes. Overlapping and
duplicate OSM lines are unioned before length calculation.

### GRWL rivers

The local GRWL V01.01 archive contains 30 m line sections with measured
`width_m`. Each section is buffered by half its measured width in the city's
local UTM CRS and dissolved to a citywide river polygon. The configured river
map includes GRWL `lakeFlag` values 0 (river), 2 (tidal river), and 3 (canal),
and excludes 1 (lake/reservoir). This is configurable in `config.json`.

### JRC-GSW

The official JRC release now extends through the complete 2024 year. The
pipeline merges monthly history from `JRC/GSW1_4/MonthlyHistory` (through 2021)
with `projects/JRC/GSW1_5/MonthlyHistory_2022_2024`.

The v1.5 monthly images currently provide `year` and `month` properties but no
`system:time_start`. The export script creates that timestamp before applying
date filters; without this normalization, Earth Engine silently returns an
empty 2022-2024 date-filtered collection.

- **JRC-GSW all:** at least one monthly water detection during 2020-2024.
- **JRC-GSW permanent:** water detections in at least 90% of valid monthly
  observations during 2020-2024.
- Months without a valid Landsat classification are excluded from the
  occurrence denominator rather than treated as dry.
- Four historical occurrence layers are also exported: 1985-1994, 1995-2004,
  2005-2014, and 2015-2024.

The 2022-2024 extension uses Landsat Collection 2. JRC notes that combining it
with Collection 1 can produce a spatially variable, usually sub-pixel
co-registration offset. This caveat matters when interpreting 30 m cell-level
change near water edges.

Official documentation:

- [JRC Global Surface Water data access and 2024 update](https://global-surface-water.appspot.com/download)
- [JRC Global Surface Water FAQ](https://global-surface-water.appspot.com/faq)
- [OSM water tagging documentation](https://wiki.openstreetmap.org/wiki/Key:waterway)
- [Geofabrik OSM GIS-format documentation](https://download.geofabrik.de/osm-data-in-gis-formats-free.pdf)

## Figure 4.1: coverage consistency

OSM and GRWL are rasterized directly onto each native JRC-GSW 30 m export grid.
Each in-buffer cell receives a three-bit code: OSM = 1, JRC-GSW permanent = 2,
and GRWL = 4. The seven possible non-empty combinations are:

1. OSM only
2. JRC-GSW only
3. OSM + JRC-GSW
4. GRWL only
5. OSM + GRWL
6. JRC-GSW + GRWL
7. all three

The request describes these as six combinations but lists seven; the pipeline
retains all seven. The denominator for each proportional bar is the union of
water-positive cells in at least one dataset. Dry cells in all three products
are excluded. A single fixed color key is used in Figure 4.1 and the six-city
presence maps.

The five reporting groups are:

- all cities;
- cities below 300,000 - central-city segments;
- cities below 300,000 - peri-urban hexbins;
- cities of 300,000 or more - central-city segments; and
- cities of 300,000 or more - peri-urban hexbins.

## Table 4.2: OSM length beyond reference products

For every segment, the pipeline writes total OSM linear-waterway length and
three non-intersecting lengths, in kilometers:

- OSM linework outside JRC-GSW all-water cells;
- OSM linework outside JRC-GSW permanent-water cells; and
- OSM linework outside GRWL variable-width river polygons.

`table_4_2_osm_unmatched_length_km.csv` sums these values for the same five
reporting groups. Calculations are performed in World Mollweide, matching the
study segment CRS.

## Historical water and example maps

The example cities are Johannesburg, Dakar, Kigali, Tripoli, Juba (South
Sudan), and Garissa (Kenya). Edit `example_cities` in `config.json` before
plotting if a different set is required.

For each example city the pipeline produces:

- a permanent-water presence-combination map using the Figure 4.1 colors; and
- four segment maps showing mean JRC-GSW water occurrence for 1985-1994,
  1995-2004, 2005-2014, and 2015-2024 on a common 0-100% scale.

The decadal metric is the area-weighted mean of valid 30 m pixel occurrence
percentages within each segment. It describes historical surface-water
frequency and can identify areas with intermittent past water, but should be
treated as a flood-exposure indicator rather than a modeled flood hazard.

## Installation

The existing project environment contains the geospatial and Earth Engine
packages. OSMnx is the only additional dependency:

```bash
.venv/bin/pip install -r river_analysis/water_93city_pipeline/requirements.txt
```

## Staged run

Run commands from the `wb_buildings` project root.

Audit and prepare local inputs:

```bash
.venv/bin/python river_analysis/water_93city_pipeline/scripts/run_pipeline.py audit
.venv/bin/python river_analysis/water_93city_pipeline/scripts/run_pipeline.py prepare-segments
.venv/bin/python river_analysis/water_93city_pipeline/scripts/run_pipeline.py prepare-grwl
```

Download OSM features from dated Geofabrik country extracts. Each country is
processed once for all of its study cities, and its PBF is removed after the
city files have been written. Use `--keep-pbf` to retain the downloads. This is
the recommended full-run path because it is reproducible and avoids public
Overpass query limits:

```bash
.venv/bin/python river_analysis/water_93city_pipeline/scripts/run_pipeline.py download-osm-geofabrik
```

`download-osm` remains available as an Overpass-based alternative for a small
number of individual cities.

Plan the 93 JRC exports without submitting them:

```bash
.venv/bin/python river_analysis/water_93city_pipeline/scripts/run_pipeline.py plan-jrc
```

After reviewing `data/jrc_export_manifest.json`, submit and download:

```bash
.venv/bin/python river_analysis/water_93city_pipeline/scripts/submit_jrc_exports.py \
  --submit --project ee-gyetman
.venv/bin/python river_analysis/water_93city_pipeline/scripts/download_jrc_exports.py \
  --project ee-gyetman
```

Calculate segment statistics, tables, and figures:

```bash
.venv/bin/python river_analysis/water_93city_pipeline/scripts/run_pipeline.py analyze
MPLCONFIGDIR=/tmp/wb-water-matplotlib \
  .venv/bin/python river_analysis/water_93city_pipeline/scripts/run_pipeline.py plot
```

While OSM and JRC downloads are still running, completed city bundles can be
analyzed incrementally with:

```bash
.venv/bin/python -u river_analysis/water_93city_pipeline/scripts/analyze_as_ready.py
```

All city-processing stages accept repeatable `--city` filters. Preparation and
analysis stages skip existing outputs unless `--force` is supplied.

## Outputs

- `data/prepared_segments/`: audited city partitions
- `data/osm/`: cached OSM line and polygon files
- `data/grwl/`: clipped centerlines and dissolved variable-width polygons
- `data/jrc_gsw/`: aligned multi-band JRC exports
- `outputs/presence_grids/`: cell-level seven-class presence rasters
- `outputs/segment_water/`: population-independent segment water statistics
- `outputs/analysis/figure_4_1_presence_combinations.csv`
- `outputs/analysis/table_4_2_osm_unmatched_length_km.csv`
- PNG and SVG copies of Figure 4.1, the six-city presence figure, and the six
  four-decade city figures

Completed-run checks:

- 93 city segment files with 2,265,624 total segment records;
- 93 presence-combination rasters and 93 cell-count tables;
- both structural zones (`city_segment` and `hexgrid`) represented;
- five Figure 4.1 reporting groups, each summing to 100%; and
- no negative waterway-length estimates or missing city IDs.

## Roads section

The road requirements supplied with this specification are intentionally not
mixed into the water pipeline. They require separate Overture and Microsoft
Road Detection source locations, a 10 m road-matching workflow, and different
tables and correlation outputs. The prepared city/segment partitions here can
be reused when that road pipeline is set up.
