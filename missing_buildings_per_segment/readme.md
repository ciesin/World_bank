# Missing Buildings per Segment

This workflow identifies potential missing building locations using World Settlement Footprint (WSF) data and existing building footprints, then summarizes the resulting missing WSF locations by segment.

The workflow supports two different WSF input formats:

* **20-band WSF imagery** for Tripoli, Dakar, and Garissa
* **1-band WSF mode imagery** for Juba, Johannesburg, and Kigali

Only one of the two WSF processing cells is run, depending on the format of the input WSF raster. After either WSF workflow is completed, the segment-level aggregation cell is run.

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

## Outputs

The WSF processing cells produce intermediate raster and point datasets.

### Raster outputs

Depending on the processing path, outputs include:

* new/filtered WSF raster
* rasterized building footprints
* WSF pixels remaining after building removal

### Point outputs

The workflows produce:

* all selected WSF points
* missing WSF points
* filtered missing WSF points

The filtered missing WSF points are the primary input to the segment-level calculation.

### Segment output

Cell 3 updates the segment polygon layer with:

```text
missing_1_year
missing_3_year
```

These fields contain the number of filtered missing WSF points intersecting each segment.

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
```

## Requirements

* ArcGIS Pro
* ArcPy
* Spatial Analyst extension
* Image Analyst extension for the 20-band workflow
* WSF raster data
* Existing building footprint data
* Segment polygon data

## Selecting the Processing Cell

Use **Cell 1** when the WSF input is a 20-band raster with individual WSF observations for different dates/years.

Use **Cell 2** when the WSF input is a one-band raster containing the WSF `mode` value.

After running either Cell 1 or Cell 2, run **Cell 3** to aggregate the filtered missing WSF points to the segment polygons.

```text
20-band WSF ──► Cell 1 ──┐
                         ├──► Cell 3 ──► Segment missing counts
1-band mode WSF ─► Cell 2 ─┘
```

