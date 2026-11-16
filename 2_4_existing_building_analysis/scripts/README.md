

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
.venv/bin/python -u scripts/make_gap_hotspots.py
.venv/bin/python -u scripts/download_gba_height_juba.py
.venv/bin/python -u scripts/make_gap_hotspots_30m.py
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

## Neighborhood-segment analysis


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
