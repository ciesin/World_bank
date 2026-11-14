# Juba Analysis Notebooks

This is a basic repository for notebooks related to blocks, zones, and segment analyses in Juba, South Sudan. These analyses will be applied to other cities as well, but for now this workflow has only been tested in Juba. 

When we feel confident that the methods produce the desired results in a cross-section of cities, we may formalize the repository to support a modular workflow, including helper and utility folders, etc...   

## Notebook order
(The notebooks are numbered in the intended order of execution.)

1. `01_download_roads.ipynb` - Download road data
2. `02_major_roads_pt1.ipynb` - Major roads processing, part 1
3. `03_major_roads_pt2.ipynb` - Major roads processing, part 2
4. `04_zones_setup.ipynb` - Initial zones setup
5. `05_blocks_zones_sj.ipynb` - Spatial join between blocks and zones
6. `06_admin_to_blocks.ipynb` - Assign administrative areas to blocks
7. `07_knn_blocks.ipynb` - K-nearest neighbor / block analysis
8. `08_null_cluster.ipynb` - Null cluster handling
9. `09_pop_workflow.ipynb` - Population assignment workflow
10. `10_zones_2.ipynb` - Create zones_2
11. `11_zones_3.ipynb` - Create zones_3
12. `12_morph_tess.ipynb` - Morphology / tessellation workflow
13. `13_zones_4.ipynb` - Create zones_4
14. `14_zones_5.ipynb` - Create zones_5

## Notes

These notebooks were developed for a local GIS workflow. Some paths refer to local drives and geodatabases that are not included in this repository.

The first cell of each notebook contains a description of its purpose, number of cells, inputs, outputs, and analytical logic. 
