# Full-AOI Blocks and Segments with Building Indicators (3 Pilot Cities)

R workflow that extends GRID3 blocks and pilot-city neighborhood segments to cover each SPARC city's full area of interest (AOI), and attaches building, population, WSF Tracker, new-building, and complex-morphology attributes to every block and segment.

Developed by CIESIN, Columbia University, for the World Bank LandAtlas / SPARC Urban Analytics Platform. Pilot cities: Juba (South Sudan), Kigali (Rwanda), Johannesburg (South Africa).

Method descriptions are in the final report, Sections 4.2.3 (augmented blocks and segments), 4.3 (building indicators), and 4.4 (flags). Each script also has a detailed header describing its steps and parameters.

---

## Workflow

Run the scripts in order. Each script reads the previous script's output; the suffix of each output (`_cln1`, `_cln2`, ...) matches the script number.

| Script | What it does | Main output |
|---|---|---|
| `1_blocks_coverAOI.R` | Clips GRID3 blocks to the AOI, resolves enclosed blocks ("donuts"), divides the remaining space into void units (skeleton split, 10 ha hexagons, small pieces merged), and counts Overture buildings per unit | layer `blocks_cln1` |
| `2_segment_coverAOI.R` | Cleans blocks and void units (small units merged, void units cut at narrow necks), sums LandScan-Mosaic-Ambient population per unit, assigns units to the original segments, forms new segments, then splits and merges segments along major linear breaks to meet size and shape rules | layers `blocks_cln2`, `segments_cln2`, `breaks_cln2` |
| `3_stats_grid3Vaugmented.R` | Summary statistics comparing original and augmented segments (report Table 4.9) | `table_4_9_metrics_cln3.csv` |
| `4_segment_coverAOI_attributes.R` | Building statistics (Overture and integrated layers), WSF Tracker counts, building:WSF ratios, new-building estimates, and complex-morphology flags for every block and segment | `<city>_blocks_segments_complete_in_AOI_cln4.gpkg` with layers `blocks_cln4`, `segments_cln4` |

The final deliverable is the output of script 4: one GeoPackage per city, with a block layer and a segment layer linked by `segment_id`.

---

## Requirements

- R ≥ 4.1
- Packages: `sf` (≥ 1.0, GEOS ≥ 3.8), `dplyr` (≥ 1.1), `terra`, `exactextractr`

```r
install.packages(c("sf", "dplyr", "terra", "exactextractr"))
```

All geometry operations are planar in each city's projected UTM coordinate system (`sf_use_s2(FALSE)`).

---

## Setup

Paths are set in the configuration section at the top of each script and point to a CIESIN network drive. Edit them to match your environment:

| Variable | Script | Points to |
|---|---|---|
| `out_dir`, `root_blocks`, `root_overture`, `root_aoi` | 1 | Output folder; GRID3 block input folder; Overture buildings; SPARC AOI files |
| `root`, `seg_dir`, `blk_dir`, `landscan_root` | 2 | Output folder; original segments; block coverage; LandScan Mosaic rasters |
| `root`, `aug_dir`, `orig_dir` | 3 | Output folder; original segments |
| `srv`, `aug_dir`, `old_dir`, `ovr_dir`, `int_dir`, `wsf_dir`, `aoi_dir` | 4 | Output folder and all attribute inputs |
| `terra_tmp` | 4 | A local folder with a few GB free for raster temporary files |

Choose which cities to run with `run_cities` (scripts 1–2) or `cities` (scripts 3–4).

---

## Inputs

| Input | Format | Used by | Notes |
|---|---|---|---|
| GRID3 blocks and pilot-city segments | GeoPackage `<city>_blocks_segments_metrics.gpkg`, layers `blocks` and `segments` | 1, 2, 3 | Outputs of the pilot-city workflow (report Section 4.2.2) |
| SPARC AOI | GeoJSON (`SPARC_AOI_Juba.geojson`, `SPARC_AOI_Kigali.geojson`, `SPARC_AOI_CoJ.geojson`) | 1, 4 | |
| Overture building footprints | File geodatabase | 1, 4 | June 2026 release |
| Integrated, cleaned Overture + 3D-GloBFP buildings | GeoPackage `bldgs_intgr_cln_<city>.gpkg` | 4 | Must include the 50 m neighbor fields `nn_count_50m`, `nn_size_mean`, `nn_size_min`, `nn_size_max`, `nn_edge_dist_mean_m` |
| LandScan-Mosaic-Ambient | GeoTIFF `landscan-mosaic-<country>-v1.tif` | 2 | Population per block (area-weighted, exact extraction) |
| WSF Tracker | GeoTIFF `<City>_WSFtracker_20160701-20260101.tif` | 4 | Values 1–20 = six-month period first detected as impervious (1 = Jul 2016 … 15 = Jul 2023, 19 = Jul 2025, 20 = Jan 2026) |
| Earlier full-AOI segments | GeoPackage `<city>_segments_complete_in_AOI.gpkg` | 4 | Source of `ovr_bldg_wsf_ratio` and `intgr_bldg_wsf_ratio` (based on 2023 footprints), transferred by overlay |

---

## Outputs

All outputs are written to the output folder set in each script.

**`<city>_blocks_segments_metrics_coverAOI.gpkg`** (scripts 1–2)

- `blocks_cln1`: GRID3 blocks and void units covering the AOI, with Overture building counts and areas, `source` (`grid3_block` or `aoi_void`), and void-unit diagnostics
- `blocks_cln2`: cleaned blocks with population (`pop_lsm_ambient`), segment ID (`seg_part_ID`), and assignment diagnostics
- `segments_cln2`: segments (dissolved blocks) with origin (`seg_source`: original or new), number of blocks, dominant zone, population, and compactness (`compact_j`, `polsby_popper`)
- `breaks_cln2`: shared block boundaries with stroke length, for reviewing the linear breaks used to split segments

**`table_4_9_metrics_cln3.csv`** (script 3): counts, median areas, and AOI coverage of original and augmented segments.

**`<city>_blocks_segments_complete_in_AOI_cln4.gpkg`** (script 4): final deliverable.

| Group | Fields | Blocks | Segments |
|---|---|:-:|:-:|
| Identity and geometry | `block_id`, `segment_id`, `source`, `zone_id`, area and perimeter | ✓ | ✓ |
| Overture buildings (Jun 2026) | `ovr_bldg_ct`, `ovr_tot / min / max / med / avg / sd_bldg_area`, `ovr_bldg_area_dens`, `ovr_bldg_ct_dens` (suffix `_06_2026`) | ✓ | ✓ |
| Integrated buildings (Jun 2026) | `intgr_` (same set) | ✓ | ✓ |
| Population | `pop_lsm_ambient`, `pop_per_ha` | ✓ | ✓ |
| WSF Tracker | `wsf_pixel_ct_07_2023`, `wsf_pixel_ct_01_2026`, `new_wsf_pixel_last_1yr`, `new_wsf_pixel_last_3yrs` | ✓ | ✓ |
| Ratios and new buildings | `ovr_bldg_wsf_ratio`, `intgr_bldg_wsf_ratio`, `ovr_new_bldgs_1yr / 3yr`, `intgr_new_bldgs_last_1yr / 3yr` | ✓ (segment's ratio) | ✓ |
| Complex morphology | `cmplx_bldg_ct_large`, `cmplx_bldg_ct_small`, `cmplx_bldg_ct`, `cmplx_bldg_share`, `cmplx_flag` | ✓ | ✓ |
| Neighbor summaries | `nn_count_med`, `nn_count_p90`, `nn_size_mean_med`, `nn_size_mean_cv`, `nn_size_min_med`, `nn_size_max_med`, `nn_edge_dist_med_m`, `bldg_gt500_ct` | ✓ | ✓ |
| Segment lineage | `seg_source`, `seg_fid`, `n_blocks`, `polsby_popper`, `compact_j`, `n_holes`, `oldseg_cover_share` | | ✓ |

Definitions:

- **Building count and largest building:** buildings whose point-on-surface falls in the unit.
- **Total footprint area:** footprint area inside the unit; buildings crossing a boundary are clipped.
- **Smallest, median, mean, and standard deviation of footprint area:** buildings wholly inside the unit.
- **WSF counts:** 10 m cells whose center falls in the unit.
- **New buildings:** newly impervious cells × building:WSF ratio.
- **Complex morphology:** a building meets rule A (footprint > 500 m² and ≥ 100 neighbors within 50 m) or rule B (> 12 neighbors within 50 m with mean size < 35 m²); a unit is flagged (`cmplx_flag = 1`) when it holds ≥ 3 such buildings.

Counts, footprint areas, WSF cells, and population sum exactly from blocks to segments. Size statistics (min, median, mean, sd) are computed separately for each level.

---

## Key parameters

Parameters are set in lists near the top of each script (`void_params`, `hex_params`, `merge_params`, `pinch_params`, `seg_params`, `compact_params`, `sparse_params`, `stranded_params`, `cmplx`). The main values:

| Parameter | Value | Script |
|---|---|---|
| Void split along skeleton | void parts ≥ 1 ha; branches < 100 m pruned | 1 |
| Hexagon size; merge threshold | 10 ha; pieces < 5 ha merged | 1 |
| Pinch-point cut in void units | necks < 30 m; cut pieces ≥ 0.5 ha | 2 |
| Assignment to original segment | ≥ 50% overlap | 2 |
| Split segments | > 1,500 people or > 50 ha (first pass); never > 100 ha; parts ≥ 5 ha or ≥ 500 people | 2 |
| Strokes (linear breaks) | ≤ 40° deflection at junctions; boundaries cut at bends > 40° over 30 m; zone boundaries + 5,000 m | 2 |
| Compactness split | J > 4.0 (≈ 8:1 rectangle) | 2 |
| Merge segments | < 5 ha and < 500 people; single neighbor; < 10 people/ha (≤ 50 ha); < 50 people and < 10 ha | 2 |
| Refinement passes | up to 2 | 2 |
| Complexity flag | rule A or B; ≥ 3 flagged buildings per unit | 4 |

---

## Caching and reruns

Intermediate results are saved so that reruns do not repeat completed work.

- **Script 1:** checkpoints after the void split and after the combined coverage (`checkpoints/<city>_cln1_*_checkpoint.rds`). With `resume = TRUE`, a rerun continues from the latest checkpoint. Set `resume = FALSE` or delete the checkpoints after changing the steps they cover.
- **Script 2:** block populations are cached (`_cache/<city>_cln2_pop_lsm_ambient.rds`) and recomputed automatically when block geometry or the raster changes.
- **Script 4:** buildings are assigned to blocks once and stored as building tables (`_cache/<city>_cln4_bt_ovr.rds`, `_bt_int.rds`). On rerun, blocks and segments are matched to the previous run by geometry; only buildings touching new or changed blocks are reassigned, WSF counts are recomputed only for changed blocks, and the overlay only for changed segments. Cleaned building layers are cached as `.rds` (`cache_buildings = TRUE`). Set `force_full <- TRUE` or delete a city's `_cln4_` cache files to recompute everything.

---

## Limitations

- Void units follow skeleton lines and hexagon edges where no road or river separates them, so many have geometric rather than physical boundaries.
- Linear breaks are inferred from block geometry alone; review them with `breaks_cln2`.
- LandScan-Mosaic-Ambient cells (100 m) are larger than many blocks, so block populations are approximate.
- Building:WSF ratios come from 2023 footprints in an earlier segment layer and are transferred by overlay; `oldseg_cover_share` records how much of each segment that layer covers.
- New-building estimates detect new impervious surface, not buildings; infill within areas already impervious and demolition are not captured.
- Parameters were set by visual review in the three pilot cities and have not been validated elsewhere.

---

## Citation

[Add report citation and data DOI.]

## License

[Add license.]

## Contact

Dana Thomson, CIESIN, Columbia University
