# Block Splitting Workflow

The workflow begins with citywide building and block datasets, identifies blocks that warrant subdivision, creates building-level tessellations and neighborhood metrics, clusters buildings into spatially contiguous groups, converts those groups into candidate block subdivisions, assigns population to the new blocks, and selects a preferred subdivision.

Blocks that are not satisfactorily resolved by the primary clustering approach can be processed with an alternative BEAM partitioning workflow. The final steps resolve multipart child-block geometries, recalculate population where geometry changes, and replace the original parent blocks in a copy of the citywide blocks dataset.

## Workflow

| Step | Notebook | Purpose |
|---:|---|---|
| 01 | `01_block_screening.ipynb` | Identify blocks that should enter the splitting workflow using building hot-spot patterns, block area, and population. |
| 02 | `02_block_splitting_workspace.ipynb` | Create a separate working folder and geodatabase for each selected source block. |
| 03 | `03_building_tessellation_cells.ipynb` | Create a building-level tessellation within each selected block. |
| 04 | `04_building_context_pre-clustering.ipynb` | Calculate building-context variables, including polygon edge-to-edge neighborhood measures and tessellation-cell characteristics. |
| 05 | `05_spatially_constrained_multivariate_clustering.ipynb` | Create spatially constrained building-cluster solutions for k = 2–5. |
| 06 | `06_label_smoothing.ipynb` | Smooth locally inconsistent building-cluster labels using nearby-building relationships. |
| 07 | `07_new_blocks.ipynb` | Dissolve tessellation cells by smoothed cluster to create candidate new blocks. |
| 08 | `08_new_blocks_populated.ipynb` | Assign population to each candidate block subdivision using building-area-weighted population allocation. |
| 09 | `09_select_block_layer.ipynb` | Select one preferred k = 2–5 subdivision for each source block. |
| 10 | `10_pre_beam_tables.ipynb` | Create building buffer/dissolve diagnostics used to identify blocks that may require alternative BEAM processing. |
| 11 | `11_beam_splitting.ipynb` | Create alternative n = 2–4 contiguous block partitions using bounded beam search for manually selected blocks. |
| 12 | `12_beam_blocks_populated.ipynb` | Assign population to BEAM candidates and select the preferred BEAM subdivision. |
| 13 | `13_new_block_ids.ipynb` | Assign final child-block IDs that preserve parent-block and splitting-method lineage. |
| 14 | `14_multipart_features_explode_workflow.ipynb` | Resolve multipart child blocks, absorb surrounded components where appropriate, recalculate population, and perform QA. |
| 15 | `15_parent_child_block_swap.ipynb` | Replace selected parent blocks with their final child blocks in a copy of the citywide blocks dataset. |

## Primary and BEAM splitting approaches

Steps 03–09 form the primary subdivision workflow. Buildings are assigned a cluster ID using the SKATER algorithm (ArcGIS Spatially Constrained Multivariate Clustering). Building tessellation cells are then dissolved by their spatially joined cluster ID into candidate child blocks.

Step 10 provides diagnostics for identifying blocks that may not be satisfactorily resolved by this approach. Those blocks can be manually routed through Steps 11–12.

The BEAM workflow partitions the tessellation into spatially contiguous groups while balancing building footprint area and penalizing unnecessarily complex internal boundaries. Candidate two-, three-, and four-part solutions are generated and evaluated using the same population and area criteria used for the primary workflow.

BEAM is an alternative splitting method within the same workflow. After Step 12, downstream notebooks use the same block files regardless of which method produced them.

## Population allocation

Population is assigned to newly created block features using population-grid cells and building footprints.

Within each population-grid cell, population is apportioned among intersecting block features according to their share of building footprint area. Population is calculated for the standard clustering-derived blocks, the BEAM-derived blocks, and again after multipart geometry changes in Step 14.

This ensures that final child-block population values reflect their final geometries rather than simply inheriting population from the original parent block.

## Final block IDs

Split child blocks receive IDs with the structure:

`blk_<parent>_<subdivision>_<LargePop>_<heterogeneity>_<beam>`

For example:

`blk_401_2_0_2_0`

indicates a child of parent block 401, subdivision 2, not originally flagged `LargePop`, associated with heterogeneity category 2, and created without BEAM.

The final component is:

| Value | Meaning |
|---:|---|
| 0 | Standard clustering-based subdivision |
| 1 | BEAM subdivision |

The heterogeneity component is:

| Value | Meaning |
|---:|---|
| 0 | No heterogeneity flag |
| 1 | `HH_CC` (contains both hot and cold spots) |
| 2 | `HH_Grtr10ha` (contains building hotspots and block area greater than 10 hectares) |
| 3 | `CC_Grtr10ha` (contains building coldspots and block area greater than 10 hectares) |

## Final lineage fields

The final citywide blocks dataset created in Step 15 contains two additional binary lineage fields:

| Field | Value | Meaning |
|---|---:|---|
| `was_split` | 0 | Original block feature that was not subdivided |
| `was_split` | 1 | Child block created by the splitting workflow |
| `beam` | 0 | BEAM was not used for this feature |
| `beam` | 1 | Feature was produced through the BEAM workflow |

`was_split` precedes `beam` in the final attribute table.

Together, `block_id`, `was_split`, and `beam` provide both detailed and easily queryable information about the origin of each final block feature.

## Key area measures

Several related area measures occur throughout the workflow:

| Field | Meaning |
|---|---|
| `area_m_utm` | Building footprint area in square meters |
| `cell_area_m2` | Area of the building's tessellation cell |
| `bldg_coverage_ratio` | Building footprint area divided by tessellation-cell area |
| `partition_area_m2` | Geometry area of a dissolved BEAM partition, retained primarily for QA |

For BEAM partitioning, `area_m_utm` is used as the building-area balancing weight. Summed `cell_area_m2` is used when evaluating the 100,000 m² candidate-block area threshold.

## Directory structure

The workflow creates a block-specific workspace for each source block selected in Step 1. A folder such as `_401` contains the inputs and intermediate outputs associated with source block 401.

The block-specific workspaces contain the source block and buildings, tessellation, building-context data, clustering outputs, candidate subdivisions, population-assignment outputs, and—where applicable—BEAM outputs.

Selected final subdivisions are written to a separate `heterogeneous_largePop_selection` directory before being integrated into the citywide blocks dataset.

## Software

The workflow uses both ArcGIS Pro and open-source Python geospatial libraries.

Several notebooks require ArcPy and should be run in an ArcGIS Pro Python environment. Other portions use packages such as GeoPandas, pandas, NumPy, Shapely, pyogrio, momepy, and related Python geospatial/network-analysis libraries.

The notebooks contain their own imports and user-settings sections.

## Running the workflow

Run the notebooks sequentially from 01 through 15.

Steps 11–12 are conditional: only blocks manually selected for BEAM processing after review of the Step 10 diagnostics need to pass through those notebooks.

Step 14 acts only on selected outputs requiring multipart-geometry resolution.

Paths, city names, coordinate reference systems, input datasets, and other project-specific settings should be reviewed before running each notebook.

## Data

The source GIS datasets and generated intermediate datasets are not included in this repository.

The notebooks currently contain project-specific local file paths that serve as worked examples. Users applying the workflow to another city or directory structure should update the user-settings sections accordingly.

Large GIS files such as File Geodatabases, GeoPackages, shapefiles, rasters, and archives are excluded from version control through `.gitignore`.

## Repository structure

`notebooks/` contains the 15 ordered workflow notebooks.

`.gitignore` excludes local GIS datasets, temporary notebook files, archives, and common operating-system artifacts.

`README.md` provides an overview of the workflow and its execution sequence.
