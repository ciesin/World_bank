# Block Merging Workflow

This repository contains the final workflow used to create city segments from a citywide blocks dataset. The workflow prepares the block and building inputs, calculates block-level building statistics, diagnoses block topology, and merges blocks within `zones_5` zones using population, rook adjacency, building-density similarity, compactness, and future merge feasibility.

The workflow generally targets segment populations between 500 and 1,000 people. Open-space and airport blocks are excluded from merging and remain standalone segments. A second-stage island-resolution procedure handles under-500 segments that cannot be resolved through the initial rook-adjacency merging process.

This repository represents the third major stage of the city-segments workflow. It follows the zone-creation workflow in `city-segments_01-zones` and the block-splitting workflow in `city-segments_02-block-splitting`.

## Workflow

| Step | Notebook | Purpose |
|---:|---|---|
| 01 | `01_block_merging_setup.ipynb` | Prepare the block and building inputs, assign blocks to `zones_5`, create open-space and airport flags, calculate block geometry attributes, and export the finalized `blocks_5` layer. |
| 02 | `02_blocks_building_stats.ipynb` | Calculate building counts, building-size statistics, building footprint area, and building-density measures for each block. |
| 03 | `03_topology_diagnostics.ipynb` | Diagnose rook adjacency, gaps, overlaps, islands, and connected components, and evaluate candidate coordinate-precision grids before merging. |
| 04 | `04_block_merging_city_segments.ipynb` | Merge eligible blocks into city segments and resolve remaining disconnected under-500 island segments. |

## Relationship to the upstream workflows

The city-segment workflow is organized into three related repositories.

1. `city-segments_01-zones` creates the sequence of analysis zones ending with `zones_5`.
2. `city-segments_02-block-splitting` identifies blocks that require subdivision and replaces selected parent blocks with their final child blocks.
3. `city-segments_03-block-merging` uses the resulting citywide blocks and `zones_5` geography to construct the final city segments.

The block-merging workflow therefore assumes that the relevant outputs from the first two workflows already exist.

## Input preparation

Step 01 creates the working datasets required by the subsequent notebooks.

Each block is assigned to a single `zones_5` feature using a guaranteed-inside block point. The original `OBJECTID` of the corresponding `zones_5` feature is retained as `zones_5_ID`, and the zone population is retained as `zones_5_pop`.

The notebook also selects Overture building footprints whose centers fall within the analysis blocks and projects those buildings to the local projected coordinate system.

Four fields used later in the workflow are created or calculated:

| Field | Meaning |
|---|---|
| `open_space` | Binary flag identifying blocks classified as open space |
| `airport` | Binary flag identifying blocks classified as airport |
| `block_area_m2` | Block area in square meters |
| `block_perimeter_m` | Block perimeter in meters |

Blocks flagged as open space or airport are excluded from block merging and remain standalone segments.

## Building statistics

Step 02 calculates building statistics used to characterize the built form within each block.

Building representative points are used to assign individual buildings to blocks for count-based and building-size statistics. These include:

- `bldg_count`
- `bldg_area_min`
- `bldg_area_max`
- `bldg_area_median`
- `bldg_area_stdev`

Building footprint area is handled separately. Actual building-polygon/block-polygon intersections are calculated so that a building crossing a block boundary contributes only the part of its footprint that lies within each block.

This produces:

- `bldg_area_sum`
- `bldg_area_density`
- `bldg_count_density`

The two density measures are later used to discourage merges between blocks with substantially different building patterns.

## Topology diagnostics

Step 03 evaluates the block topology before the merging algorithm is run.

For merge-eligible blocks within the same `zones_5` zone, the notebook constructs a rook-adjacency graph. Rook adjacency requires a shared polygon boundary rather than a point-only contact.

The diagnostics identify:

- valid rook adjacency;
- point-only contacts;
- small polygon overlaps;
- larger or material overlaps;
- small gaps between nearby same-zone blocks;
- connected components;
- topological islands;
- connected components whose combined population remains below 500.

The notebook also tests candidate coordinate-precision grids of:

- 0.01 m;
- 0.05 m;
- 0.10 m.

For each grid, it evaluates changes in adjacency, connected components, islands, geometry validity, and polygon area.

The purpose of this analysis is to select a precision that removes microscopic topology artifacts without materially changing the block geography or creating inappropriate new adjacency relationships.

The current block-merging workflow uses a 0.01 m working precision grid.

## Stage 1 block merging

The first cell of Step 04 creates the initial citywide segment geography.

The merging behavior depends on `zones_5_pop`.

### Zones with population of 1,000 or less

For zones with population less than or equal to 1,000, all merge-eligible blocks are dissolved directly into one segment.

Disconnected polygon parts are allowed, so these segments may be multipart.

Open-space and airport blocks remain separate.

### Zones with population greater than 1,000

For larger zones, the workflow builds a same-zone rook-adjacency graph among merge-eligible blocks.

The graph is separated into connected components. Degree-zero blocks are retained as deferred islands for the second stage.

Within each non-island connected component, blocks are iteratively merged into larger candidate regions.

The principal population target is approximately:

- minimum preferred population: 500;
- maximum preferred population: 1,000.

Population above 1,000 is permitted where necessary, but is penalized during candidate evaluation.

Candidate merges are evaluated using considerations including:

- whether a merge would leave neighboring regions stranded below 500 people;
- similarity in building-area density;
- similarity in building-count density;
- compactness of the resulting segment;
- resulting population;
- narrow shared-boundary or neck effects;
- population overflow above 1,000.

Differences of 20% or less in the building-density measures are treated as practically equivalent and receive no building-heterogeneity penalty.

The workflow performs multiple seeded near-greedy runs for each iterative connected component and retains the best-performing solution.

## Stage 2 island resolution

Some under-500 segments cannot be resolved during the rook-adjacency stage because they are disconnected from the primary same-zone graph or belong to an isolated under-populated component.

The second cell of Step 04 processes these remaining segments.

Eligible under-500 island-derived segments are considered for attachment to nearby segments within the same `zones_5` zone.

For each target, the workflow considers up to eight nearby eligible candidate regions based on polygon-to-polygon distance.

Candidate attachments are evaluated using:

- future feasibility for other unresolved islands;
- distance between polygons;
- building-density heterogeneity;
- resulting segment population;
- population overflow above 1,000.

Candidates that keep the resulting population at or below 1,000 are preferred. If no such candidate is available, an over-1,000 merge may be accepted with an overflow penalty.

Open-space and airport-derived segments remain excluded throughout this stage and can neither initiate nor receive an island-resolution merge.

## Final outputs

The first-stage merging process creates:

`citywide_merge_experiment/blocks_5_citywide_merged.gpkg`

with principal layers including:

- `segments_best`
- `blocks_with_segment_id`

The island-resolution stage creates:

`citywide_island_resolution/blocks_5_citywide_islands_resolved.gpkg`

with principal layers including:

- `segments_islands_resolved`
- `blocks_with_final_segment_id`
- `island_attachment_lines`

`segments_islands_resolved` is the principal final city-segment geography produced by this workflow.

A `stage1_to_final_crosswalk.csv` table records the relationship between first-stage segment IDs and final segment IDs.

The workflow also produces city-, zone-, component-, run-, merge-, and candidate-level QA tables that can be used to inspect how the final geography was constructed.

## Population preservation

Population is treated as an additive block attribute throughout the merging process.

Both stages contain QA checks to confirm that merging changes the geography and grouping of blocks without changing the total population represented by the source blocks.

Population preservation is checked both citywide and, where applicable, within `zones_5`.

## Software

The workflow uses both ArcGIS Pro and open-source Python geospatial libraries.

Step 01 uses ArcPy for geodatabase management, geometry checking and repair, spatial selection, projection, field calculation, and GeoPackage export.

Later notebooks primarily use open-source Python packages including:

- GeoPandas
- pandas
- NumPy
- Shapely
- pyogrio

The notebooks contain their own imports and project-specific input/output settings.

## Running the workflow

Run the notebooks sequentially from 01 through 04.

Step 03 is primarily diagnostic. Its outputs should be reviewed before choosing the coordinate precision used in Step 04.

Paths, city names, coordinate reference systems, source datasets, field names, and other project-specific settings should be reviewed before running the notebooks for another city.

The current notebooks use Johannesburg, South Africa as the worked example.

## Data

The source GIS datasets and generated intermediate datasets are not included in this repository.

The notebooks currently contain project-specific local Windows file paths that serve as worked examples. Users applying the workflow to another city or directory structure should update those paths and other city-specific settings before running the workflow.

Large GIS datasets such as File Geodatabases, GeoPackages, shapefiles, rasters, CSV diagnostic outputs, and other generated analysis files are intended to remain outside version control.

## Repository structure

`notebooks/` contains the four ordered workflow notebooks.

The notebooks are numbered according to their intended execution sequence.

Each notebook begins with a markdown cell describing its purpose, inputs, outputs, and main analytical logic.

`README.md` provides an overview of the complete block-merging workflow and its relationship to the upstream city-segment workflows.
