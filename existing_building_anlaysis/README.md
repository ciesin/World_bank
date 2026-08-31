# Juba building-coverage pilot

This workspace contains a one-city comparison of six building products over the
SPARC Juba AOI (345.887 km²):

- Microsoft TEMPO, global 2023 Q4 building-density band
- Overture Buildings release 2026-07-22.0
- Google Open Buildings Temporal 2.5D, 2023-06-30
- GlobalBuildingAtlas Polygon plus ODbLPolygon components
- GlobalBuildingAtlas GBA.Height, 2025 mediaTUM release (3 m modeled height)
- 3D-GloBFP, 2020 footprints
- DLR WSF 3D v2 Building Fraction

## Primary outputs

- `outputs/juba_source_summary.csv` — source-level counts, inferred built area,
  relative completeness, and gap totals.
- `outputs/juba_pairwise_agreement.csv` — pairwise cell Jaccard and building-
  fraction rank correlations.
- `outputs/juba_threshold_sensitivity.csv` — results for three Google confidence
  thresholds and three 100 m-cell built-area thresholds.
- `outputs/juba_100m_comparison.tif` — 11-band harmonized GeoTIFF in EPSG:32636.
- `outputs/juba_comparison_grid.gpkg` — 100 m analysis cells with source
  fractions, presence flags, and gap flags.
- `outputs/juba_gap_hotspots.gpkg` — contiguous likely-gap clusters of at least
  three cells.
- `outputs/juba_gap_hotspots_top20.csv` — largest 20 clusters per source with
  centroid coordinates.
- `outputs/juba_source_fractions.png` — six-panel static comparison.

## 30 m and height extension

The higher-resolution rerun deliberately separates products by their native
support. Overture, Google 2.5D, GlobalBuildingAtlas, and 3D-GloBFP are compared
on a 30 m grid. TEMPO (about 76 m) and WSF 3D v2 (90 m) remain in the 100 m
benchmark rather than being presented as genuinely 30 m information.

- `outputs/juba_30m_source_summary.csv` — 30 m relative completeness and gaps.
- `outputs/juba_30m_pairwise_agreement.csv` — pairwise 30 m presence agreement.
- `outputs/juba_30m_threshold_sensitivity.csv` — 10, 25, and 50 m² cell-
  presence thresholds.
- `outputs/juba_30m_comparison.tif` — footprint fractions, agreement, gap count,
  and Google/3D-GloBFP heights.
- `outputs/juba_30m_comparison_grid.gpkg` and `.parquet` — cell-level values and
  source-specific gap flags.
- `outputs/juba_30m_gap_hotspots.gpkg` and
  `outputs/juba_30m_gap_hotspots_top20.csv` — contiguous review areas of at
  least ten 30 m cells.
- `outputs/juba_100m_height_comparison.tif` — mean height and inferred built
  volume for TEMPO, Google 2.5D, GBA.Height, 3D-GloBFP, and WSF 3D v2, plus
  valid-product counts and ranges with GBA excluded and included.
- `outputs/juba_height_source_summary.csv` and
  `outputs/juba_height_pairwise_agreement.csv` — coverage, distributions,
  volume, bias, error, and rank-correlation diagnostics.
- `outputs/juba_height_availability.csv` — evaluated and unavailable height
  fields by source.
- `outputs/juba_height_sensitivity.csv` — 100 m height disagreement summaries
  with GBA excluded and included.
- `outputs/juba_height_hotspots_top100.csv` and `.gpkg` — 100 m cells with the
  largest GBA-included ranges, retaining both sensitivity scenarios.
- `outputs/juba_30m_source_fractions.png` and
  `outputs/juba_30m_source_gaps.png` — static footprint and explicit gap maps.
- `outputs/juba_100m_height_comparison.png` and
  `outputs/juba_100m_height_diagnostics.png` — height, valid-source count, and
  inter-source range maps.

The primary 30 m presence threshold is 25 m² of inferred footprint per cell.
Footprint polygons are rasterized at 3 m and aggregated, avoiding the earlier
representative-point assignment at cell boundaries. A 30 m consensus cell has
at least two of the four products present. Because the vector products share
some upstream inputs, this consensus is a completeness proxy rather than
independent ground truth.

Height comparisons use 100 m as the common support for all five evaluated
height sources and require at least 50 m² of inferred building area in a cell.
The reported bias, MAE, RMSE, and correlation measure consistency only: no
surveyed reference heights were available. Google 2.5D, GBA.Height, and
3D-GloBFP are also compared at 30 m using the same 50 m² validity threshold.
Overture height attributes in Juba remain too sparse for comparison.

GBA.Height is a modeled raster, not ground truth. Native 3 m values are
restricted to GlobalBuildingAtlas footprints, filtered to finite values in
(0, 100] m with source NoData −1 excluded, and aggregated to 30 m by valid
building-pixel area. The 100 m value is then weighted by valid building area.
GBA is excluded from independent-source counts because it shares PlanetScope
imagery with TEMPO. Its LoD1/footprint lineage also overlaps Google, Microsoft,
and OSM-derived sources represented here. Sensitivity outputs therefore report
height ranges with and without GBA.

Clipped footprint extracts are in `data/processed/` as GeoParquet.

## Interpretation

The primary cell-presence threshold is 0.005 building fraction, equivalent to
50 m² in a full 100 m cell. A consensus cell has buildings in at least two of
four source families: TEMPO, vector syntheses, Google 2.5D, and WSF 3D v2.
`consensus_recall_proxy_pct` is relative coverage of those cells—not accuracy
against independent ground truth.

The vector family combines Overture, GlobalBuildingAtlas, and 3D-GloBFP because
these products share upstream Google, Microsoft, and OSM footprints. Agreement
among them is therefore not independent validation.

Google `building_presence` is uncalibrated. The primary map thresholds it at
0.5, while `juba_threshold_sensitivity.csv` also reports 0.3 and 0.7. WSF
NoData within its global domain is interpreted as zero outside the sparse
settlement mask; the native valid-cell percentage and this assumption are
recorded in `outputs/juba_analysis_metadata.json`.

Vector fractions use exact polygon areas assigned to the 100 m cell containing
each footprint representative point. This preserves small buildings but is an
approximation for footprints crossing cell boundaries.

## Reproduce the processing

The large GlobalBuildingAtlas GeoJSON tiles are clipped with a streaming parser:

```bash
.venv/bin/python scripts/clip_large_geojson.py INPUT.geojson OUTPUT.parquet \
  --aoi data/aoi/juba.geojson --region SSD
```

With the raw inputs in the paths used by the script, run:

```bash
.venv/bin/python -u scripts/analyze_juba.py
.venv/bin/python -u scripts/make_gap_hotspots.py
.venv/bin/python -u scripts/download_gba_height_juba.py
.venv/bin/python -u scripts/analyze_juba_30m_heights.py
.venv/bin/python -u scripts/make_gap_hotspots_30m.py
.venv/bin/python -u scripts/analyze_juba_neighborhoods.py
.venv/bin/python -u scripts/summarize_juba_expanded_run.py
.venv/bin/python -u scripts/create_juba_report.py
```

For the 93-city portfolio, resolve and selectively acquire the official
GBA.Height members, then regenerate the non-Juba city products:

```bash
.venv/bin/python -u scripts/download_gba_height_portfolio.py
.venv/bin/python -u scripts/run_portfolio_gba_height.py --continue-on-error
.venv/bin/python -u scripts/summarize_portfolio.py
```

The portfolio downloader writes `data/cities/gba_height_city_usage.csv` and a
checksummed `gba_height_download_manifest.json`. Each unique 0.2-degree tile is
stored once under `data/raw/portfolio/gba_height/tiles`. Portfolio GBA.Height
values are restricted to the available 3D-GloBFP proxy footprint mask at 5 m,
then building-area weighted to 30 m and 100 m. This proxy is necessary because
the full official GBA.Polygon layer is not locally available for every city;
Juba continues to use the official GBA footprint layer. Outputs report
GBA-excluded and GBA-included sensitivity separately. Kumba has no intersecting
tile in the official GBA.Height fine-tile index and is recorded as unavailable.

`download_gba_height_juba.py` reads the official mediaTUM fine-tile index and
selectively extracts only four 0.2° GeoTIFF members intersecting Juba from the
125.45 GB parent ZIP by FTP byte ranges; it does not download the full archive.
The provenance manifest records the DOI, official URL, parent SHA-512, ZIP
CRC32, and locally computed SHA-256/SHA-512 for every extracted tile.

## Neighborhood-segment analysis

`scripts/analyze_juba_neighborhoods.py` uses the prepared Juba reporting units
in `data/processed/juba_segments_20260821.gpkg`. The current expanded input
yields 14,759 non-overlapping units covering 785.63 km² and reports `GRID_ID`.

Footprint values are aggregated from the 30 m comparison using exact
segment–cell intersection areas. Height means and quantiles use the 100 m common
height grid, weighted by inferred building area and exact segment–cell overlap.
A separate 30 m Google–GBA.Height–3D-GloBFP pairwise comparison is retained.

Primary outputs include:

- `outputs/juba_neighborhood_consistency.gpkg` — one feature per `ID_SEG` with
  wide footprint, height, availability, and disagreement metrics.
- `outputs/juba_neighborhood_footprint_summary.csv` and
  `outputs/juba_neighborhood_footprint_pairwise.csv` — long-form source and
  pairwise footprint results.
- `outputs/juba_neighborhood_height_summary.csv` and
  `outputs/juba_neighborhood_height_pairwise.csv` — source and pairwise height
  results at 100 m plus the 30 m Google–GBA.Height–3D-GloBFP comparison.
- `outputs/juba_neighborhood_overview.csv` — compact one-row-per-segment table.
- `outputs/juba_neighborhood_top_footprint_disagreements.csv` and
  `outputs/juba_neighborhood_top_height_disagreements.csv` — the 30 highest-
  disagreement segments.
- `outputs/juba_neighborhood_footprint_completeness.png`,
  `outputs/juba_neighborhood_mean_heights.png`, and
  `outputs/juba_neighborhood_disagreement.png` — review maps.

Reproduce with:

```bash
.venv/bin/python -u scripts/analyze_juba_neighborhoods.py \
  --segments data/processed/juba_segments_20260821.gpkg
```

The analysis reads Google 2.5D Cloud-Optimized GeoTIFF windows directly from
Google Cloud Storage rather than downloading roughly 10 GB of complete tiles.

## Dataset documentation

- TEMPO: <https://github.com/microsoft/buildings>
- Overture Buildings: <https://docs.overturemaps.org/guides/buildings/>
- Google 2.5D: <https://developers.google.com/earth-engine/datasets/catalog/GOOGLE_Research_open-buildings-temporal_v1>
- GlobalBuildingAtlas: <https://github.com/zhu-xlab/GlobalBuildingAtlas>
- GBA.Height mediaTUM record: <https://doi.org/10.14459/2025mp1782307>
- 3D-GloBFP: <https://zenodo.org/doi/10.5281/zenodo.11391076>
- WSF 3D: <https://geoservice.dlr.de/web/datasets/wsf_3d>

GBA.Height, GBA.Polygon, and GBA.LoD1 are licensed CC BY-NC 4.0: attribution is
required, modifications must be indicated, and commercial use is prohibited.
GBA.ODbLPolygon is separately ODbL-licensed. Combining parts may create license
implications. The analyzed GBA.Height release was published 2025-09-02, records
production through 2025-04-30, and predominantly represents 2019 PlanetScope
imagery with 2018 supplementation. The official paper documents no African
height training or validation samples, so domain shift is a material Juba
limitation. Review every source's attribution and use restrictions before
redistribution or operational scaling.

