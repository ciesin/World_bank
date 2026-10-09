# Example Urban Extents: WSF Tracker + Google Open Buildings 2.5D

Experimental built-up urban extent boundaries for three pilot cities (**Juba, Kigali, Johannesburg**), derived by combining WSF Tracker imperviousness with Google Open Buildings 2.5D building density.

Prepared by Dana R. Thomson (CIESIN, Columbia University) for the World Bank GFDRR/SPARC Land Atlas work, 2026. Produced 2 July 2026.

> **Status: exploratory / proof of concept.** Thresholds and weights were tuned by visual inspection on three cities and have not been formally validated. 

---

## Contents

| File | Description |
|---|---|
| `pilot_city_density_supported_urban_core.gpkg` | Final boundaries. One layer, `all_cities_density_supported_urban_core`, one polygon per city. |
| `0_GEE_Google2.5D_metrics.R` | Google Earth Engine code (JavaScript, stored as comments) that computes Google 2.5D metrics on a 100 m grid with a 250 m moving window. |
| `1_density_bands_v4.R` | R script that combines WSF + Google metrics, creates density bands, and derives the boundaries in this GeoPackage. |
| `README.md` | This file. |

> Note: file and field names use "urban_core" for historical reasons. In the accompanying report these are called **example urban extents**.

---

## Data dictionary

Layer: `all_cities_density_supported_urban_core` · Geometry: Polygon · CRS: **EPSG:3857 (Web Mercator)**

| Field | Description |
|---|---|
| `city` | City name |
| `zone` | Always `urban_core` |
| `method` | Processing chain applied |
| `candidate_threshold` | Minimum settlement-intensity value for candidate built-up cells (0.05) |
| `main_blob_id` | ID of the blob selected as the main urban fabric |
| `n_accepted_blobs` | Number of settlement blobs retained |
| `density_gap_close_buffer_m` | Closing distance used to find candidate gaps (500 m) |
| `density_gap_support_ring_m` | Width of ring used to test density support for gap filling (250 m) |
| `gap_fill_iterations` | Number of gap-fill passes (1) |
| `wsf_edge_expansion` | Whether WSF edge expansion was applied (1 = yes) |
| `wsf_edge_search_buffer_m` | Search distance for WSF edge patches (750 m) |
| `wsf_min_local_count` | Minimum WSF-present cells in a 5×5 window at 30 m (3) |
| `wsf_max_patch_distance_to_core_m` | Maximum distance of an added WSF patch from the boundary (200 m) |
| `final_concave_hull` | Whether a final concave hull was applied (1 = yes) |
| `concave_hull_concavity` | `concaveman` concavity parameter (2) |
| `concave_hull_length_threshold_m` | `concaveman` length threshold (500 m) |
| `area_km2` | Area calculated in EPSG:3857. **Overstated, especially for Johannesburg; see table below.** |

### Summary

| City | Accepted blobs | `area_km2` (EPSG:3857) | True area (km², equal-area) |
|---|---|---|---|
| Juba | 4 | 171.0 | 169 |
| Kigali | 9 | 399.4 | 398 |
| Johannesburg | 77 | 3,614.3 | 2,906 |

Use the true-area column for reporting, or reproject to an equal-area or local UTM CRS before computing area.

---

## Input data

| Dataset | Version / band | Use |
|---|---|---|
| WSF Tracker imperviousness | 2016-07-01 to 2026-01-01 series; band 20 (`PIS_19`, 0–100), most recent epoch | Recent impervious surface and built-up edge expansion |
| Google Open Buildings 2.5D Temporal | `GOOGLE/Research/open-buildings-temporal/v1`, 2023 | Building density (fractional building count) |

Analysis window: the raster extent of each city's WSF Tracker file.

---

## Method

### Stage 1: Prepare WSF (R, `1_density_bands_v4.R`)
1. Build a WSF built-up mask (`PIS_19 > 0`).
2. Convert each city's WSF raster extent to a polygon (WGS84) and upload it to GEE as the analysis window.

### Stage 2: Google 2.5D metrics (GEE, `0_GEE_Google2.5D_metrics.R`)
3. Mosaic the 2023 Google 2.5D tiles over the window (buffered by 250 m).
4. Aggregate to a 100 m grid (EPSG:3857): summed fractional building count, mean presence, mean height where presence ≥ 0.5, and count × height.
5. Summarize in a circular 250 m-radius moving window. Building density = windowed count ÷ 0.196 km².
6. Clip to the window and export one multi-band GeoTIFF per city.

### Stage 3: Settlement-intensity surface (R)
7. Resample WSF `PIS_19` to the Google 100 m grid (bilinear) and compute its 250 m-window mean.
8. Compute **settlement intensity** (0–1):

   `intensity = 0.5 × (WSF mean PIS ÷ 100) + 0.5 × min(Google buildings/km² ÷ 1000, 1)`

9. Classify into seven bands with breaks at 0.05, 0.10, 0.20, 0.35, 0.50 and 0.70 (diagnostic). Visual inspection showed 0.05–0.10 corresponds to the built-up periphery and values below 0.05 to peri-urban background, so **0.05** was used as the candidate threshold.

Google building height and volume were computed but are **not** used in the index or the boundary.

### Stage 4: Boundary delineation (R)
10. **Candidate blobs:** cells with intensity ≥ 0.05, grouped into 8-connected blobs.
11. **Blob scoring:** area share in each band, weighted 0.25 (0.05–0.10), 1 (0.10–0.20), 3 (0.20–0.35), 6 (0.35–0.50), 10 (≥ 0.50).
12. **Main blob:** highest weighted band mass among blobs ≥ 0.1 km².
13. **Secondary blobs:** a blob ≥ 0.1 km² is retained if it meets any of these rules:

    | Rule | Distance to main blob | Weighted score | Density share |
    |---|---|---|---|
    | Nearby | ≤ 1,500 m | ≥ 0.25 | ≥ 5% at ≥ 0.10 |
    | Distant but strong | ≤ 3,500 m | ≥ 0.45 | ≥ 15% at ≥ 0.20 |
    | Nearby very dense | ≤ 1,500 m | ≥ 0.75 | ≥ 2% at ≥ 0.35 |

14. Union the accepted blobs and remove holes.
15. **Density-supported gap fill:** apply a 500 m closing. Gaps ≤ 8 km² are filled only if the surrounding 250 m ring has ≥ 30% of cells at ≥ 0.05 and ≥ 10% at ≥ 0.10. One iteration.
16. **Clean:** 100 m opening (removes tendrils), 100 m simplification, hole removal, and removal of parts < 2 km² (the largest part is always kept).
17. **WSF edge expansion:** within 750 m of the boundary, resample the WSF mask to 30 m. Keep cells with ≥ 3 WSF cells in their 5×5 neighborhood. Add 8-connected patches ≥ 0.01 km² that lie within 200 m of the boundary, after cleaning them (60 m closing, 45 m opening, 30 m simplification).
18. Remove holes again and drop parts < 2 km² again.
19. **Concave hull:** `concaveman` on the boundary vertices (concavity 2, length threshold 500 m), simplify by 100 m, remove holes.
20. Attach parameters as attributes and write the GeoPackage.

---

## Reproducing

**Requirements:** a Google Earth Engine account; R with `terra`, `sf`, `dplyr`, `stringr` and `concaveman`.

1. Obtain WSF Tracker imperviousness GeoTIFFs for each city.
2. In `1_density_bands_v4.R`, set `RUN_PREPARE_WSF_MASKS_AND_EXTENTS <- TRUE` and update the paths in the *Paths* section. Run it to create the WSF masks and extent polygons.
3. Upload the extent polygons to GEE as an asset. Copy the JavaScript from `0_GEE_Google2.5D_metrics.R` into the GEE Code Editor (remove the leading `#`), update the asset path, run it, and download the exported GeoTIFFs.
4. Place the GeoTIFFs in the R output folder and run `1_density_bands_v4.R` with `RUN_CREATE_DENSITY_BANDS` and `RUN_CREATE_REFINED_EXTENTS` set to `TRUE`.

Besides the GeoPackage, the script writes diagnostic CSVs (blob membership, gap evaluation, WSF patch evaluation) and per-city density-band rasters and vectors.

---

## Limitations

- **Exploratory.** Weights and thresholds were set by visual tuning on three cities and have not been validated.
- **Mixed dates.** Google 2.5D is from 2023; WSF Tracker is from January 2026.
- **Projection.** All processing used EPSG:3857, so distances are slightly shorter on the ground away from the equator (about 10% shorter at Johannesburg's latitude) and stored areas are inflated. Recompute areas in an equal-area CRS.
- **Built-up extent only.** These are built-up footprints, not administrative or functional city boundaries. For operational use, compare them with Africapolis, GHS-UCDB and administrative boundaries; intersecting with an administrative boundary may give the most useful city coverage area.

