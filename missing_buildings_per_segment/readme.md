# Missing Buildings per Segment

This workflow identifies potential missing building locations using World Settlement Footprint (WSF) data and existing building footprints from the integrated building footprint layer, then summarizes the resulting missing WSF locations by segment.

The workflow supports two different WSF input formats:

* **20-band WSF imagery** for Tripoli, Dakar, and Garissa
* **1-band WSF mode imagery** for Juba, Johannesburg, and Kigali

Only one of the two WSF processing cells is run, depending on the format of the input WSF raster. After either WSF workflow is completed, the segment-level aggregation cells are run.

## Notebook

`get_missing_count_per_segment.ipynb`

## Workflow

```text
                    WSF input
                       │
             ┌─────────┴─────────┐
             │                   │
       20-band raster       1-band mode raster
             │                   │
          Cell 1             Cell 2
             │                   │
             └─────────┬─────────┘
                       │
              Missing WSF points
                       │
                     Cell 3
                       │
                Segment polygons
                       │
          missing_1_year / missing_3_year
                       │
                     Cell 4
                       │
          Building and WSF statistics
                       │
       ┌───────────────┴────────────────┐
       │                                │
  2023 building/WSF ratio       Estimated missing buildings
       │                                │
       └───────────────┬────────────────┘
                       │
             Segment-level statistics
```

## Cell 1: New WSF from 20-Band Imagery

### `Get NEW WSF missing data for 20-band imagery`

This workflow is used for the 20-band WSF imagery available for:

* Tripoli
* Dakar
* Garissa

The input raster contains multiple WSF observations over time. The band names contain the observation years.

The user specifies the number of recent years to analyze using:

```python
NUMBER_OF_YEARS = 1
```

The workflow separates the WSF observations into:

* a **recent period**, consisting of the selected number of most recent years
* a **historical period**, consisting of all years before the recent period

For each period, the maximum WSF value across all available bands is calculated.

### Identifying new WSF

A pixel is classified as new WSF when:

```text
Recent WSF > 0
AND
Historical WSF = 0 or NoData
```

This identifies WSF pixels that occur during the selected recent period but were not present in the historical observations.

The resulting raster is the `NEW_WSF_RASTER`.

### Removing building pixels

Existing building footprints are rasterized to the same grid as the WSF raster.

The WSF raster is aligned using:

* the WSF raster as the snap raster
* the WSF raster's numeric cell size
* `OBJECTID` as the building raster value

Building pixels are then removed from the new WSF raster.

The remaining pixels represent potential missing WSF locations that do not overlap existing building pixels.

### Converting to points

The workflow converts the rasters to point feature classes:

* all new WSF pixels
* missing new WSF pixels

The missing WSF points are then filtered spatially.

Any missing WSF point within **4 meters of an existing building footprint** is removed.

The final output is:

```text
{CITY}_new_WSF_missing_points_filtered_{NUMBER_OF_YEARS}_year
```

---

## Cell 2: New WSF from 1-Band Mode Imagery

### `Get WSF missing data for 1-band imagery with mode value`

This workflow is used for the one-band WSF mode raster available for:

* Juba
* Johannesburg
* Kigali

The raster contains a `mode` value representing the timing of WSF occurrence across 20 WSF timesteps.

The mode is interpreted as:

```text
mode = 1   → WSF present in all 20 timesteps
mode = 20  → WSF present only in the latest timestep
```

Therefore, a **higher mode indicates newer WSF**.

The workflow assumes:

```python
TIMESTEPS_PER_YEAR = 2
TOTAL_TIMESTEPS = 20
```

### Selecting recent WSF

The user specifies the number of years to retain:

```python
NUMBER_OF_YEARS = 3
```

The number of required timesteps is calculated as:

```text
NUMBER_OF_YEARS × TIMESTEPS_PER_YEAR
```

The minimum mode retained is:

```text
TOTAL_TIMESTEPS - REQUIRED_TIMESTEPS + 1
```

For example, for three years:

```text
3 years × 2 timesteps = 6 timesteps

20 - 6 + 1 = 15
```

Therefore, modes **15–20** are retained.

For one year, modes 19–20 are retained.

For two years, modes 17–20 are retained.

For three years, modes 15–20 are retained.

The filtering is performed using `SetNull`, with all older modes converted to NoData.

### Removing building pixels

Existing building footprints are rasterized using the filtered WSF raster as the reference grid.

The building raster uses:

* the WSF raster as the snap raster
* the WSF raster's cell size
* `OBJECTID` as the building value

The building pixels are then removed from the filtered WSF raster.

The remaining pixels represent WSF locations with no corresponding building footprint.

### Converting to points

The workflow converts both the filtered WSF raster and missing WSF raster to points.

The missing WSF points are then filtered using a **4-meter distance from existing building footprints**.

Points within 4 meters of an existing building are removed.

The final filtered missing WSF points are used by the segment-level analysis.

---

## Cell 3: Calculate Missing Data per Segment

### `Calculate missing data per segment`

Cell 3 is run after either Cell 1 or Cell 2.

The cell takes the resulting missing WSF point datasets and counts how many missing points occur within each segment.

Two time periods are calculated:

* 1 year
* 3 years

The segment polygons are spatially joined with the missing WSF points using:

```text
JOIN_ONE_TO_ONE
KEEP_ALL
INTERSECT
```

The resulting `Join_Count` is assigned to the corresponding segment.

### Output fields

Two fields are added to the segment layer:

| Field            | Description                                                           |
| ---------------- | --------------------------------------------------------------------- |
| `missing_1_year` | Number of missing WSF points within the segment for the 1-year period |
| `missing_3_year` | Number of missing WSF points within the segment for the 3-year period |

No percentages are calculated.

Segments without missing WSF points receive a value of `0`.

---

## Cell 4: Calculate Building and WSF Segment Statistics

### `Get segment stats for building and WSF pixel counts`

Cell 4 uses the segment layer produced by Cell 3 and adds building and WSF statistics to each segment.

The workflow uses:

* integrated building points from 2023
* 2023 WSF points
* 2026 WSF points
* the `missing_1_year` field from Cell 3
* the `missing_3_year` field from Cell 3

The segment identifier is the `OBJECTID` field.

### 1. Count 2023 building points

A spatial join is performed between the segment polygons and the integrated 2023 building points using:

```text
JOIN_ONE_TO_ONE
KEEP_ALL
INTERSECT
```

The resulting building count is stored in:

```text
integrated_building_count_2023
```

Segments without building points receive a value of `0`.

### 2. Count 2023 WSF pixels

A spatial join counts the 2023 WSF points within each segment.

The resulting count is stored in:

```text
wsf_pixel_count_2023
```

Segments without 2023 WSF points receive a value of `0`.

### 3. Calculate the building-to-WSF ratio

For segments with at least one 2023 WSF point, the workflow calculates:

```text
building_wsf_ratio =
    integrated_building_count_2023 /
    wsf_pixel_count_2023
```

If the segment has zero 2023 WSF points, `building_wsf_ratio` is set to `NULL`.

This ratio is subsequently used to convert WSF pixel counts into estimated building counts.

### 4. Count 2026 WSF pixels

A spatial join counts the 2026 WSF points within each segment.

The resulting count is stored in:

```text
wsf_pixel_count_2026
```

Segments without 2026 WSF points receive a value of `0`.

### 5. Estimate the 2026 building count

The 2023 building-to-WSF ratio is applied to the 2026 WSF count:

```text
building_count_estimated_2026 =
    wsf_pixel_count_2026 *
    building_wsf_ratio
```

The estimate is only calculated when both the 2026 WSF count and the building/WSF ratio are available.

Otherwise, the field is set to `NULL`.

### 6. Estimate missing buildings for one year

The `missing_1_year` value from Cell 3 is converted from a missing WSF point count into an estimated missing building count using the segment's 2023 building-to-WSF ratio:

```text
estimated_missing_count_1_year =
    missing_1_year *
    building_wsf_ratio
```

If either `missing_1_year` or `building_wsf_ratio` is `NULL`, the estimated value is also set to `NULL`.

### 7. Estimate missing buildings for three years

The same calculation is performed for the three-year missing WSF count:

```text
estimated_missing_count_3_year =
    missing_3_year *
    building_wsf_ratio
```

If either `missing_3_year` or `building_wsf_ratio` is `NULL`, the estimated value is set to `NULL`.

### Output fields

Cell 4 creates or updates seven segment-level fields:

| Field                            | Description                                                        |
| -------------------------------- | ------------------------------------------------------------------ |
| `integrated_building_count_2023` | Number of integrated 2023 building points within the segment       |
| `wsf_pixel_count_2023`           | Number of 2023 WSF points within the segment                       |
| `building_wsf_ratio`             | 2023 building point count divided by 2023 WSF point count          |
| `wsf_pixel_count_2026`           | Number of 2026 WSF points within the segment                       |
| `building_count_estimated_2026`  | Estimated 2026 building count based on the 2023 building/WSF ratio |
| `estimated_missing_count_1_year` | Estimated missing buildings based on the 1-year missing WSF count  |
| `estimated_missing_count_3_year` | Estimated missing buildings based on the 3-year missing WSF count  |

The final two fields are calculated as:

```text
missing WSF points × 2023 building/WSF ratio
```

---

## Input Data

### WSF data

The workflow supports two WSF formats.

**20-band WSF raster:**

```text
{CITY}_WSFtracker_20160701-20260101.tif
```

Used for:

```text
Tripoli
Dakar
Garissa
```

**1-band WSF mode raster:**

```text
{CITY}_WSFtracker_20160701-20260101.tif
```

Used for:

```text
Juba
Johannesburg
Kigali
```

The appropriate processing cell must be selected based on the raster format.

### Building footprints

Existing building footprints are used to remove WSF pixels that already correspond to mapped buildings.

The building layer follows the pattern:

```text
buildings_{CITY}_9_13_cleaned
```

### Segment polygons

The segment layer used by Cells 3 and 4 is:

```text
segments_6_cities
```

within:

```text
E:\WSF\wsf_work_arc\wsf_work_arc.gdb
```

## Outputs

### WSF processing outputs

The WSF processing cells produce intermediate raster and point datasets.

Depending on the processing path, outputs include:

* new/filtered WSF raster
* rasterized building footprints
* WSF pixels remaining after building removal
* all selected WSF points
* missing WSF points
* filtered missing WSF points

The filtered missing WSF points are the primary input to Cell 3.

### Segment output after Cell 3

Cell 3 updates the segment polygon layer with:

```text
missing_1_year
missing_3_year
```

These fields contain the number of filtered missing WSF points intersecting each segment.

### Segment output after Cell 4

Cell 4 adds the building and WSF statistics:

```text
integrated_building_count_2023
wsf_pixel_count_2023
building_wsf_ratio
wsf_pixel_count_2026
building_count_estimated_2026
estimated_missing_count_1_year
estimated_missing_count_3_year
```

The segment layer is updated in place rather than creating a separate output layer.

## Processing Logic

The overall workflow can be summarized as:

```text
WSF observations
       │
       ▼
Identify recent/new WSF
       │
       ▼
Remove pixels corresponding to
existing building footprints
       │
       ▼
Convert remaining WSF pixels to points
       │
       ▼
Remove points within 4 m of buildings
       │
       ▼
Filtered missing WSF points
       │
       ▼
Spatial join with segments
       │
       ▼
Count missing points per segment
       │
       ├── missing_1_year
       │
       └── missing_3_year
                  │
                  ▼
       Calculate 2023 building/WSF ratio
                  │
                  ▼
       Count 2026 WSF pixels
                  │
                  ├──► building_count_estimated_2026
                  │
                  ├──► estimated_missing_count_1_year
                  │
                  └──► estimated_missing_count_3_year
```

The estimation relationships are:

```text
building_wsf_ratio =
    2023 building count / 2023 WSF count

building_count_estimated_2026 =
    2026 WSF count × building_wsf_ratio

estimated_missing_count_1_year =
    missing_1_year × building_wsf_ratio

estimated_missing_count_3_year =
    missing_3_year × building_wsf_ratio
```


## Requirements

* ArcGIS Pro
* ArcPy
* Spatial Analyst extension
* Image Analyst extension for the 20-band workflow
* WSF raster data
* Existing building footprint data
* Integrated building point data
* 2023 WSF point data
* 2026 WSF point data
* Segment polygon data

## Processing Order

The notebook should be run in the following order:

### For 20-band WSF data

```text
Cell 1
  ↓
Cell 3
  ↓
Cell 4
```

### For 1-band mode WSF data

```text
Cell 2
  ↓
Cell 3
  ↓
Cell 4
```

Cell 1 and Cell 2 are alternative WSF preprocessing workflows. **Only one should be run for a given input dataset.**

Cell 3 calculates the missing WSF counts per segment, and Cell 4 combines those counts with the building/WSF relationship to produce estimated missing building counts.


