### Cleaning_false_positives
Script to extract Google 2.5D, Dynamic World landcover, and AlphaEarth embeddings for integrated Overture-3D-Glo footprints. Requires Google Earth Engine authentication to extract data.

Additionally, script runs outlier detection on AlphaEarth data and produces t-SNE outputs displaying AlphaEarth outliers, Dynamic World class, and 2.5D confidence level. 

The final attributes for each building including dw_top_class (top dynamic world class), google25d_max_confidence (maximum building confidence from Google 2.5D), and isolation_forest_outlier (true or false if isolation forest detects an outlier across the 64 AlphaEarth bands), are useful for dropping false positive buildings.
