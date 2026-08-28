# nearest neighbor k-means analysis

This repository contains Jupyter notebooks used to analyze building-level nearest-neighbor distance patterns. Later notebooks apply k-means clustering to assign individual buildings to clusters.

The analysis focuses on how buildings are distributed in space by measuring the distance from inidividual buildings to the nearest: 5th, 10th, 20th, 40th, 80th, 200th, 500th, 1000th, 2000th, 4000th, and 8000th building. These distance metrics are then used to classify buildings according to local and broader scale spatial patterns.

## Notebook overview

The notebooks are intended to be read in order. They form two related workflows:

1. A local nearest neighbor + k-means workflow using relatively small neighbor counts.
2. A broader multi-scale nearest neighbor k-means workflow using much larger neighbor counts.

`nnkm_1.ipynb` — **Summarize and explore local nearest neighbor distances**

The main purpose of this notebook is exploratory. It helps identify the scale of local building spacing and prepares a wide summary table of nearest neighbor distance metrics. This notebook summarizes raw nearest-neighbor tables for several values of k, including:

5 nearest neighbors
10 nearest neighbors
20 nearest neighbors
40 nearest neighbors
80 nearest neighbors

It creates exploratory plots to understand the distribution of distances, including histograms, log-scale histograms, boxplots, ECDF plots, scatterplots, and ratio plots.

`nnkm_2.ipynb` — **Join local nearest neighbor metrics to building points**

The purpose of this notebook is to convert the nearest neighbor summary table into a spatial dataset for mapping and further analysis. This notebook joins the summarized nearest neighbor metrics from nnkm_1.ipynb to a building point layer that can be opened in GIS software and mapped by fields such as:

distance to the 5th nearest neighbor
distance to the 80th nearest neighbor
ratio of 80th-neighbor distance to 5th-neighbor distance
quantile classes for selected KNN metrics

`nnkm_3.ipynb` — **Run local nearest neighbor-based k-means clustering**

The purpose of this notebook is to classify buildings according to local-scale spatial structure. This notebook performs k-means clustering using local nearest neighbor distance metrics. The clustering variables are based on local building spacing and local spacing ratios, including log-transformed versions of fields such as:

distance to the 5th nearest neighbor
distance to the 20th nearest neighbor
distance to the 80th nearest neighbor
ratio of 20th-neighbor distance to 5th-neighbor distance
ratio of 80th-neighbor distance to 20th-neighbor distance

The notebook tests several values of k and creates cluster labels such as km2, km3, km4, km5, and km6.

`nnkm_4.ipynb` — **Calculate broad nearest neighbor distances**

The purpose of this notebook is to create broad-scale nearest neighbor distance metrics that can complement the local metrics. This notebook extends the nearest-neighbor analysis to much larger values of k. It calculates broader-neighborhood distances such as:

distance to the 200th, 500th, 1000th, 2000th, 4000th, and 8000th nearest neighbor

`nnkm_5.ipynb` — **Join broad nearest neighbor metrics to building points**

The purpose of this notebook is to prepare a richer spatial point layer containing both local and broad nearest neighbor metrics. This notebook joins the broad nearest neighbor metrics from nnkm_4.ipynb back to the building point layer.

It adds broad-distance fields such as: `d200_near`, `d500_near`, `d1000_near`, `d2000_near`, `d4000_near`, `d8000_near`

It also creates broad-to-local ratio fields, such as the ratio between the 8000th-neighbor distance and the 80th-neighbor distance.

These ratios help distinguish buildings that are locally dense but located in less continuous broader settlement patterns from buildings embedded in larger continuous dense areas.

`nnkm_6.ipynb` — **Run broad/multi-scale nearest neighbor-based k-means clustering**

The purpose of this notebook is to classify buildings according to multi-scale spatial context, rather than only local building spacing. This notebook performs a second k-means clustering analysis using broader multi-scale nearest neighbor variables. The clustering variables include log-transformed versions of fields such as:

distance to the 20th nearest neighbor, distance to the 80th nearest neighbor, distance to the 500th nearest neighbor, distance to the 2000th nearest neighbor, distance to the 8000th nearest neighbor, ratio of 8000th-neighbor distance to 80th-neighbor distance.

The notebook tests several values of k and creates broad-scale cluster labels such as bkm2, bkm3, bkm4, bkm5, and bkm6.

## Conceptual distinction between the two workflows

The first workflow, represented by nnkm_1.ipynb through nnkm_3.ipynb, focuses on local morphology. It asks:

How close are nearby buildings to one another?

The second workflow, represented by nnkm_4.ipynb through nnkm_6.ipynb, focuses on broader urban context. It asks:

How does each building sit within the larger settlement fabric?

The local model is useful for identifying fine-grained spacing patterns. The broad model is useful for distinguishing compact, continuous urban areas from more dispersed or peripheral development patterns.

## Data and file paths

The notebooks use local GIS datasets stored outside this repository. These local datasets are not included in the repository because they may be large, project-specific, or stored in local file geodatabases and GeoPackages.

Several notebooks reference local Windows paths on the E: drive. Users running these notebooks on another machine will need to update the input and output paths before running the code.

## Software environment

The notebooks were developed in a Python/Jupyter environment and use a combination of geospatial and data science libraries, including tools such as:

pandas
geopandas
numpy
matplotlib
scikit-learn
