# Segment-Level Building and WSF Statistics

This workflow calculates segment-level statistics relating integrated building counts to World Settlement Footprint (WSF) pixel counts.

The workflow uses existing **building points**, **2023 WSF points**, and **2026 WSF points** to calculate building and WSF counts for each segment. It then uses the 2023 building-to-WSF relationship to estimate the number of buildings represented by the 2026 WSF pixels.

## Workflow

```text
Segment polygons
       │
       ├── Building points ────────► 2023 building count
       │
       ├── 2023 WSF points ────────► 2023 WSF count
       │
       └── 2026 WSF points ────────► 2026 WSF count
                                      │
                                      ▼
                         2023 building / WSF ratio
                                      │
                                      ▼
                       Estimated 2026 building count
```

## Inputs

All input data are stored in the ArcGIS file geodatabase:

```text
E:\WSF\wsf_work_arc\wsf_work_arc.gdb
```

### Segment layer

```text
segments_6_cities
```

The segment identifier used by the workflow is:

```text
OBJECTID
```

### Building points

```text
buildings_six_cities_points_2023_older
```

These points represent the integrated building dataset used to calculate the 2023 building count for each segment.

### 2023 WSF points

```text
wsf_points_2023
```

These points are used to calculate the number of WSF pixels associated with each segment in 2023.

### 2026 WSF points

```text
wsf_pixel_count_2026
```

These points are used to calculate the number of WSF pixels associated with each segment in 2026.

## Processing

### 1. Count building points

A spatial join is performed between the segment polygons and the 2023 building points.

The spatial join uses:

* `JOIN_ONE_TO_ONE`
* `KEEP_ALL`
* `INTERSECT`

The resulting `Join_Count` is stored as:

```text
integrated_building_count_2023
```

Segments with no building points receive a count of `0`.

### 2. Count 2023 WSF pixels

A second spatial join counts the 2023 WSF points within each segment.

The resulting count is stored as:

```text
wsf_pixel_count_2023
```

Segments with no 2023 WSF points receive a count of `0`.

### 3. Calculate the building-to-WSF ratio

For segments containing at least one 2023 WSF point, the workflow calculates:

```text
building_wsf_ratio =
    integrated_building_count_2023 /
    wsf_pixel_count_2023
```

If a segment has zero 2023 WSF points, the ratio is set to `NULL`.

This ratio represents the relationship between the number of integrated building points and WSF pixels within each segment based on the 2023 data.

### 4. Count 2026 WSF pixels

A third spatial join counts the 2026 WSF points within each segment.

The resulting count is stored as:

```text
wsf_pixel_count_2026
```

Segments with no 2026 WSF points receive a count of `0`.

### 5. Estimate the 2026 building count

The 2023 building-to-WSF ratio is applied to the 2026 WSF count:

```text
building_count_estimated_2026 =
    wsf_pixel_count_2026 *
    building_wsf_ratio
```

The estimate is only calculated when:

* `wsf_pixel_count_2026 > 0`, and
* `building_wsf_ratio` is not `NULL`.

Otherwise, the estimated building count is set to `NULL`.

## Output Fields

The workflow adds or updates five fields in the `segments_6_cities` layer.

| Field                            | Type   | Description                                                           |
| -------------------------------- | ------ | --------------------------------------------------------------------- |
| `integrated_building_count_2023` | LONG   | Number of 2023 building points within the segment                     |
| `wsf_pixel_count_2023`           | LONG   | Number of 2023 WSF points within the segment                          |
| `building_wsf_ratio`             | DOUBLE | 2023 building point count divided by 2023 WSF point count             |
| `wsf_pixel_count_2026`           | LONG   | Number of 2026 WSF points within the segment                          |
| `building_count_estimated_2026`  | DOUBLE | Estimated 2026 building count based on the 2023 building-to-WSF ratio |

## Temporary Outputs

Three temporary spatial-join feature classes are created during processing:

```text
temp_segment_building_counts
temp_segment_wsf_counts_2023
temp_segment_wsf_counts_2026
```

These are deleted automatically when processing is complete.

If they already exist when the script starts, they are deleted before the spatial joins are performed.

## Requirements

* ArcGIS Pro
* ArcPy
* File geodatabase containing the segment and point datasets
* Segment polygons with an `OBJECTID` field
* 2023 building points
* 2023 WSF points
* 2026 WSF points

## Output

The original segment layer is updated in place:

```text
E:\WSF\wsf_work_arc\wsf_work_arc.gdb\segments_6_cities
```

No separate segment feature class is created.

The final segment layer contains both the observed 2023 statistics and the 2026 WSF-based building estimate.

## Summary

The workflow establishes a segment-level relationship between integrated building points and WSF pixels in 2023, then applies that relationship to 2026 WSF counts.

The primary calculation is:

```text
2023:
building points / WSF pixels
            ↓
building_wsf_ratio
            ↓
2026:
WSF pixels × 2023 ratio
            ↓
estimated 2026 building count
```

