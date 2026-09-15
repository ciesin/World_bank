

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

