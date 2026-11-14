# City Segments

Notebook workflows for creating city-segment polygons from **existing blocks, building footprints, population data, and spatial grouping constraints**.

The code characterizes building spacing, constructs larger grouping zones, subdivides selected blocks, and merges prepared blocks into population-based segments. Building-pattern similarity, spatial adjacency, geometry, and population are used at different stages.

> **Before running:** These are individually configured notebooks, not a single automated pipeline. The checked-in paths refer to different city workspaces, including Juba, Kigali, and Johannesburg. Input data, layer names, coordinate systems, and intermediate-file paths must be aligned before notebooks can be used together. The GIS input datasets are not included in this directory.

## Directory structure

```text
city_segments/
|-- README.md
|-- knn_buildings/       # 6 notebooks: building spacing and k-means classes
|-- zones/               # 14 notebooks: road, administrative, and building-pattern zones
|-- block_splitting/     # 15 notebooks: screening, subdivision, and parent/child replacement
`-- block_merging/       # 4 notebooks: preparation, diagnostics, and final segment merging
```

## Workflow and dependencies

The following is the logical dependency order after paths and input schemas have been reconciled. It is not a command that executes the directory end to end.

1. **Characterize building patterns with `knn_buildings/`.** Summarize existing nearest-neighbor tables, calculate broader-scale point distances, and produce building cluster labels. The broad-scale `bkm2` field is read by `zones/07_knn_blocks.ipynb`.
2. **Prepare zones and block population with `zones/`.** Create road- and boundary-based zones, attach administrative and building-cluster attributes to blocks, allocate population, and refine the result into `zones_5`.
3. **Subdivide selected blocks with `block_splitting/`.** Screen populated blocks, generate candidate subdivisions, assign population, select a subdivision, and replace parent blocks in a copy of the master layer. BEAM search is an optional alternative for explicitly selected blocks.
4. **Create segments with `block_merging/`.** Prepare the chosen block layer and `zones_5`, calculate building statistics, inspect topology, merge adjacent blocks, and optionally run the second-stage island-resolution cell.

The splitting workflow can start from independently prepared blocks and population inputs. Likewise, merging can start from prepared blocks and zones without rerunning the other folders. Neither workflow automatically resolves differences between the city-specific layer names used in the notebooks.

## Requirements

### Software and environment

The imports and tool calls in the notebooks use the following components:

| Component | Use in this directory |
| --- | --- |
| Python notebook environment | Execute notebook cells in order. |
| ArcPy / ArcGIS Pro Python environment | Road processing, spatial joins, raster preparation, hotspot analysis, spatially constrained clustering, workspace preparation, and block replacement. |
| Spatial Analyst | Explicitly checked out in `zones/09_pop_workflow.ipynb` for raster operations. |
| `geopandas`, `pandas`, `numpy`, `shapely`, `pyogrio` | Vector processing, spatial queries, geometry operations, attributes, and file input/output. Several notebooks use Shapely 2-style vectorized geometry functions. |
| `momepy` | Building-level and zone-level morphological tessellation. |
| `scipy`, `scikit-learn`, `matplotlib` | Point-neighbor searches, k-means clustering, scaling, cluster diagnostics, and plots. |
| `networkx` | Graph construction and connectivity checks in the BEAM partitioning workflow. |
| `osmnx` | Download the drivable OpenStreetMap network in `zones/01_download_roads.ipynb`. |
| `overturemaps` command-line executable | Invoked by `zones/09_pop_workflow.ipynb` to download building footprints for its outer population-grid area. |

The non-ArcPy notebooks still read Esri File Geodatabases. Some also write them through `pyogrio`, so the installed GDAL drivers must support the required FileGDB operations. For example, `block_splitting/06_label_smoothing.ipynb` explicitly checks for **OpenFileGDB write support**. GeoPackage and, in the Overture download step, GeoParquet support are also needed.

No shared environment file or pinned package versions are supplied in `city_segments/`. The imports above are a dependency inventory, not a tested installation specification. The download cells require network access; the other processing stages operate on configured local data.

### Input data

| Input | Where it is used / required details |
| --- | --- |
| Existing city block polygons | The notebooks start from a prepared block layer; they do not provide a general raw-data-to-master-blocks workflow. Splitting expects `block_id` and `population`. Initial IDs such as `blk_442` are used to create folders such as `_442`. |
| Building footprints | Used for hotspot analysis, tessellation, local context, population allocation, and merging statistics. `area_m_utm` is an input field in the splitting screening/context stages. Other stages calculate geometry areas themselves. |
| Building points | The nearest-neighbor workflow uses a point layer with `ORIG_FID`, linked to `building_id` in its summary tables. |
| Existing nearest-neighbor tables | `nnkm_1.ipynb` reads `_5_nearest_neighbors`, `_10_nearest_neighbors`, `_20_nearest_neighbors`, `_40_nearest_neighbors`, and `_80_nearest_neighbors`. It requires `IN_FID`, `NEAR_DIST`, and `NEAR_RANK`; it does not create these tables. |
| Population raster / prepared population layers | The zone workflow starts from a configured raster. Splitting reads `pop_grid` with `grid_code` and a `buildings_inside` layer from a prepared population geodatabase. Its configured geodatabase is not the same workspace as the example in the zone workflow. |
| Waterways and administrative polygons | Local inputs to `zones/04_zones_setup.ipynb`. Road lines are produced by the preceding road notebooks. |
| Prepared dissolved extent | `zones/09_pop_workflow.ipynb` expects `blocks_dissolve_wgs84`. The road-download notebook writes `extent_hull`, not this separately named layer. |
| Final grouping zones and block classifications | Merging setup expects `zones_5` with `population`, and blocks with `composite_class`. It derives flags from the exact values `open_space` and `airport`. The classification itself must already exist. |

For the optional pre-BEAM summaries, source blocks also need `building_area_sum` and `block_area_sqm`; Step 10 reads these fields rather than creating them.

Use compatible projected coordinate systems with meter-based units for distance and area calculations. The code contains city-specific EPSG settings rather than one shared CRS parameter. In the splitting population routines, bounding-box filtering occurs before reprojection, so source-layer CRS alignment must be checked before those reads.

## Notebook inventory

### Building-neighbor analysis: `knn_buildings/`

| Notebook | Implemented processing |
| --- | --- |
| [nnkm_1.ipynb](knn_buildings/nnkm_1.ipynb) | Summarizes the existing nearest-neighbor tables by building; writes per-k and combined CSVs, spacing ratios, and exploratory plots. |
| [nnkm_2.ipynb](knn_buildings/nnkm_2.ipynb) | Joins distance summaries to building points using `ORIG_FID`; creates ratio and quantile fields; exports a GeoPackage. |
| [nnkm_3.ipynb](knn_buildings/nnkm_3.ipynb) | Fits k-means models to standardized log-transformed local-distance and ratio variables; evaluates 2-6 clusters and writes cluster profiles and spatial output. |
| [nnkm_4.ipynb](knn_buildings/nnkm_4.ipynb) | Uses `cKDTree` to calculate point distances to the 200th, 500th, 1,000th, 2,000th, 4,000th, and 8,000th other buildings. |
| [nnkm_5.ipynb](knn_buildings/nnkm_5.ipynb) | Adds broad-scale distances, broad-to-local ratios, and mapping classes to the building-point layer. |
| [nnkm_6.ipynb](knn_buildings/nnkm_6.ipynb) | Fits the broad-scale compact k-means models and exports `bkm2` through `bkm6`. Also contains a separate ArcPy example selecting buildings from a near table. |

The broad-scale path used by zone construction is `nnkm_1` -> `nnkm_2`, together with `nnkm_4` -> `nnkm_5` -> `nnkm_6`. `nnkm_3` provides a separate local-scale clustering analysis. Check the execution issues below before running the broad-scale path.

### Zone construction: `zones/`

| Notebook | Implemented processing |
| --- | --- |
| [01_download_roads.ipynb](zones/01_download_roads.ipynb) | Dissolves the input blocks, creates a convex-hull download extent, and downloads drivable OSM roads as `roads_1`. |
| [02_major_roads_pt1.ipynb](zones/02_major_roads_pt1.ipynb) | Projects and filters roads, merges divided roads, processes road polygons, and extends linework. Contains a manually specified polygon-removal ID. |
| [03_major_roads_pt2.ipynb](zones/03_major_roads_pt2.ipynb) | Further processes road polygons and linework to produce `roads_9`. |
| [04_zones_setup.ipynb](zones/04_zones_setup.ipynb) | Combines road, waterway, administrative-boundary, and extent linework into `zones_1`. |
| [05_blocks_zones_sj.ipynb](zones/05_blocks_zones_sj.ipynb) | Assigns initial-zone IDs to block interior points and updates `initial_zones_1_sj` on the blocks. |
| [06_admin_to_blocks.ipynb](zones/06_admin_to_blocks.ipynb) | Assigns administrative IDs to blocks and updates `geoboundaries`. |
| [07_knn_blocks.ipynb](zones/07_knn_blocks.ipynb) | Assigns each block the majority building `bkm2` class, with a `tie` flag. Only building classes 1 and 2 are counted. |
| [08_null_cluster.ipynb](zones/08_null_cluster.ipynb) | Revises missing block clusters using shared-boundary evidence and configured defaults; writes `cluster_revised` and diagnostic recommendations. |
| [09_pop_workflow.ipynb](zones/09_pop_workflow.ipynb) | Creates population-grid polygons, prepares building intersections, calculates building-area-weighted population allocations, and updates block population. See the join warning below. |
| [10_zones_2.ipynb](zones/10_zones_2.ipynb) | Groups blocks by initial zone, administrative unit, and revised cluster; creates `zones_2` and aggregates population. |
| [11_zones_3.ipynb](zones/11_zones_3.ipynb) | Iteratively merges low-population zones using grouping attributes, edge adjacency, and Reock compactness comparisons; writes `zones_3` and merge logs. |
| [12_morph_tess.ipynb](zones/12_morph_tess.ipynb) | Generates morphological tessellation from `zones_3` within the extent hull. |
| [13_zones_4.ipynb](zones/13_zones_4.ipynb) | Uses tessellation groups and spatial joins to create `zones_4`. |
| [14_zones_5.ipynb](zones/14_zones_5.ipynb) | Processes low-population neighboring and island zones through `zones_4a` and `zones_4b` to `zones_5`, preserving merge logs. |

### Block subdivision: `block_splitting/`

| Notebook | Implemented processing |
| --- | --- |
| [01_block_screening.ipynb](block_splitting/01_block_screening.ipynb) | Runs optimized hotspot analysis on building area, summarizes `Gi_Bin` by block, and selects blocks with heterogeneity or high-population flags. |
| [02_block_splitting_workspace.ipynb](block_splitting/02_block_splitting_workspace.ipynb) | Creates one folder and FileGDB per selected block, containing `block` and clipped `buildings`. |
| [03_building_tessellation_cells.ipynb](block_splitting/03_building_tessellation_cells.ipynb) | Creates building-level morphological tessellation and associated cell attributes. |
| [04_building_context_pre-clustering.ipynb](block_splitting/04_building_context_pre-clustering.ipynb) | Calculates polygon edge-distance neighbor metrics, nearby-building summaries, and tessellation-based context features. |
| [05_spatially_constrained_multivariate_clustering.ipynb](block_splitting/05_spatially_constrained_multivariate_clustering.ipynb) | Runs ArcGIS spatially constrained multivariate clustering for 2-5 clusters per block. |
| [06_label_smoothing.ipynb](block_splitting/06_label_smoothing.ipynb) | Revises building cluster labels from unweighted neighbor-label evidence within 30 m. |
| [07_new_blocks.ipynb](block_splitting/07_new_blocks.ipynb) | Joins smoothed building labels to tessellation cells and dissolves cells into candidate block subdivisions. |
| [08_new_blocks_populated.ipynb](block_splitting/08_new_blocks_populated.ipynb) | Allocates grid population to candidate subdivisions using intersected building area; adds original screening flags. |
| [09_select_block_layer.ipynb](block_splitting/09_select_block_layer.ipynb) | Selects one candidate layer per parent block using population/area tests and explicit fallback rules. |
| [10_pre_beam_tables.ipynb](block_splitting/10_pre_beam_tables.ipynb) | Counts dissolved building groups at 2, 3, and 4 m buffer distances and joins parent-block characteristics for review. |
| [11_beam_splitting.ipynb](block_splitting/11_beam_splitting.ipynb) | Uses bounded beam search to divide tessellation cells into 2-4 connected groups, balancing building area and penalizing boundary complexity. |
| [12_beam_blocks_populated.ipynb](block_splitting/12_beam_blocks_populated.ipynb) | Populates BEAM candidates and selects one for each explicitly listed block, replacing the canonical selection while preserving a pre-BEAM copy. |
| [13_new_block_ids.ipynb](block_splitting/13_new_block_ids.ipynb) | Assigns child IDs encoding parent, subdivision, screening flags, and BEAM lineage. |
| [14_multipart_features_explode_workflow.ipynb](block_splitting/14_multipart_features_explode_workflow.ipynb) | Diagnoses multipart features, explodes or absorbs parts, recalculates population, and compares population totals with the original selection. |
| [15_parent_child_block_swap.ipynb](block_splitting/15_parent_child_block_swap.ipynb) | Replaces selected parents in a copy of the master block layer; gives multipart-processed selections priority and adds `was_split` and `beam` lineage fields. |

Steps 10-12 are a review-driven alternative, not an automatic fallback for every unsuccessful clustering result. The BEAM notebooks contain explicit block-folder lists that must be reviewed together.

### Final segment creation: `block_merging/`

| Notebook | Implemented processing |
| --- | --- |
| [01_block_merging_setup.ipynb](block_merging/01_block_merging_setup.ipynb) | Creates the working workspace; checks/repairs copied block geometry; assigns zone IDs and population; selects and projects buildings; derives exclusion and geometry fields; exports `blocks_5.gpkg`. |
| [02_blocks_building_stats.ipynb](block_merging/02_blocks_building_stats.ipynb) | Adds building counts, building-size summaries, intersected footprint area, and building density to blocks. |
| [03_topology_diagnostics.ipynb](block_merging/03_topology_diagnostics.ipynb) | Diagnoses adjacency, overlaps, near gaps, point-only contacts, connected components, and candidate precision grids. A separate cell checks missing/duplicate block IDs. |
| [04_block_merging_city_segments.ipynb](block_merging/04_block_merging_city_segments.ipynb) | First code cell: constrained same-zone merging. Second code cell: resolution of eligible under-500 island-derived segments. Each stage writes a separate GeoPackage and diagnostics. |

Building counts and size summaries use building representative points within blocks. `bldg_area_sum` is calculated separately from the actual building-polygon/block-polygon intersections. The two density fields are footprint area divided by block area, and building count per hectare, respectively.

## Important processing rules

### Screening and subdivision

The screening flags in `01_block_screening.ipynb` are:

- `HH_CC`: both `Gi_Bin = 3` and `Gi_Bin = -3` buildings occur in the block.
- `HH_Grtr10ha` / `CC_Grtr10ha`: only the corresponding extreme class occurs, and block area is greater than **100,000 m^2**.
- `LargePop`: block population is greater than **1,000**.

A block is selected when any flag is 1. The three heterogeneity flags are mutually exclusive; `LargePop` can overlap them.

Spatially constrained clustering tests **2, 3, 4, and 5 clusters**, with **10 buildings per cluster** as the configured minimum and `TRIMMED_DELAUNAY_TRIANGULATION` as the spatial constraint. The context stage calculates neighbor distances for ranks 1, 5, 10, 20, and 40 and building summaries within 30, 60, and 100 m.

The standard selection step prefers the first candidate passing its applicable population or area test. When none passes, it can still retain the highest-k candidate and log a warning. A selected layer is therefore **not a guarantee that every subdivision satisfies the thresholds**.

The population-allocation routines assign population in proportion to building area within each grid cell. They retain an outside-block share in diagnostic tables; they do not force all intersecting grid population into the selected parent. Missing grid coverage or missing building intersections can result in zero assigned population, with a logged status.

### Merging and island resolution

The final merging notebook sets:

| Setting | Checked-in value / behavior |
| --- | --- |
| Minimum target population | `MIN_POP = 500` |
| Soft maximum population | `SOFT_MAX_POP = 1000`; overflow can be allowed and penalized. |
| Small-zone rule | For `zones_5_pop <= 1000`, eligible blocks are dissolved within the zone, including disconnected blocks. Multipart output is allowed. |
| Excluded blocks | `open_space` and `airport` blocks remain separate and are excluded from merging. |
| Stage-1 adjacency | Same-zone shared-edge adjacency, with `MIN_SHARED_EDGE_M = 0.05`. Point-only contact is not a rook edge. |
| Precision grid | `PRECISION_GRID_M = 0.01`; `OUTPUT_WORKING_GEOMETRY = True`. Inspect topology diagnostics before accepting these geometry settings. |
| Repeated searches | `N_RUNS = 20` in each stage. The seed settings are 102 for stage 1 and 2026 for stage 2. |
| Building-pattern tolerance | `HETERO_TOL = 0.20` for relative building-area-density and count-density differences. |
| Stage-2 candidates | `K_NEAREST = 8` same-zone eligible regions, using polygon-to-polygon distance. |

Stage 1 scores merges using population, building-pattern differences, compactness, narrow shared boundaries, and the risk of leaving neighbors unable to reach the population target. Stage 2 attaches eligible under-500 island-derived segments to nearby regions without reopening or splitting stage-1 segments.

The resulting segments are **not guaranteed to fall between 500 and 1,000 people**. Small-zone dissolves, excluded features, unresolved components, and allowed overflow are retained with diagnostic attributes. Stage-2 attachments can create spatially disconnected, multipart segments.

## Main outputs

Output locations are configured in each notebook. The following names are used in the supplied code:

| Stage | Main output |
| --- | --- |
| Building clusters | `buildings_points_knn_kmeans_broad_compact.gpkg`, layer `wb_buildings_knn_broad_compact_kmeans`. |
| Zones | `zones.gdb`, layer `zones_5`, together with intermediate layers and merge logs. |
| Selected subdivisions | One `new_blocks_populated.gpkg` per parent-block folder in `heterogeneous_largePop_selection`; multipart-processed overrides are stored separately. |
| Revised city blocks | The configured master-copy output ending in `_hetero_swapped`, plus a swap-summary CSV. |
| Prepared merging inputs | `segments_v2/blocks_5.gpkg` and `segments_v2/blocks_5_with_building_stats.gpkg`. |
| Stage-1 segments | `citywide_merge_experiment/blocks_5_citywide_merged.gpkg`, with `segments_best` and `blocks_with_segment_id`. |
| Stage-2 segments | `citywide_island_resolution/blocks_5_citywide_islands_resolved.gpkg`, with `segments_islands_resolved`, `blocks_with_final_segment_id`, and an attachment-line layer when attachments exist. |

The merging stages also write CSV summaries and merge logs. Stage 2 writes `stage1_to_final_crosswalk.csv`; the block output retains `stage1_segment_id`, adds `final_segment_id`, and updates `segment_id` to the final assignment. Some nonspatial summaries are additionally written into the GeoPackage when the installed driver supports that operation.

## Execution notes and checks before use

### Configure every notebook, not only the first one

Paths and constants are repeated across notebooks and sometimes across cells. Check the active city, layer names, population workspace, ID fields, output destinations, and projection in every step. In particular:

- Splitting Step 15 writes an output ending in `_hetero_swapped`, while merging setup currently reads a layer ending in `_hetero_swapped_v2_SPARC`. Those are different inputs; selecting the intended layer is a required handoff.
- The block-ID diagnostic cell in merging Step 3 points to Kigali, while the main topology cell points to Johannesburg.
- The road-cleaning notebook contains `objectids_to_remove = [21]`. BEAM processing also uses fixed block lists. These are case-specific settings, not general selection rules.

### Known code-level execution issues

| Location | Issue to resolve before running |
| --- | --- |
| `knn_buildings/nnkm_4.ipynb`, final plotting cell | Uses `plot_folder`, but does not define it in this notebook. Define the destination before running that cell. The preceding CSV export is separate. |
| `knn_buildings/nnkm_6.ipynb`, required-field check | Requires `d20_near` before the following cell joins it. The preceding `nnkm_5` export does not retain that field. Move the check after the join or provide the field before the check. |
| `zones/09_pop_workflow.ipynb`, final population update | The summary step groups by `block_ID` and requests the value-field name `sum_allocated_pop`; the update cell instead looks for `OBJECTID` and `SUM_allocated_pop`. Verify the actual summary schema and join using the stored source-block key. A summary-table row ID must not be assumed to equal the original block ID. |

These observations come from the code, not from an end-to-end execution. The notebooks have not been run here against the external GIS datasets or an ArcGIS Pro environment.

### Protect inputs and inspect diagnostics

Several zone notebooks update attributes on the configured block layer in place. Other steps delete and recreate working geodatabases or GeoPackages. Splitting Steps 13 and 14 can rewrite selected GeoPackages with backup behavior controlled by their settings. Use a working copy and review overwrite/backup flags before execution.

Run topology diagnostics before final merging. Check missing or duplicate IDs, unmatched spatial joins, geometry changes, population totals, failed or skipped blocks, and the selection/merge logs before accepting outputs. Some batch loops continue after an individual failure, so a final completion message does not establish that every block or candidate succeeded.

---

Source snapshot: notebooks in `World_bank-master.zip`; archive commit identifier `960afaffcf0e6ec6d0afe6124436bf19745911bc`. Notebook links above are relative to this directory.

