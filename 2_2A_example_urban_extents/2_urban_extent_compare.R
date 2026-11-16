################################################################################
### World Bank Contract 2026
### Example Urban Extents: comparison with other boundary datasets
### Author: Dana R. Thomson (d.r.thomson@ciesin.columbia.edu)
###
### Purpose:
###   Compare the example urban extents (WSF Tracker + Google 2.5D) for three
###   pilot cities (Juba, Kigali, Johannesburg) against:
###     - GHS-UCDB urban centre
###     - GHS-FUA functional urban area
###     - Africapolis 2020 agglomeration (original boundaries)
###     - SPARC AOI
###
### Metrics (area-based, computed in each city's local UTM zone):
###   F1  = 2 x overlap / (example area + comparison area)
###   IoU = overlap / (example area + comparison area - overlap)
###   % of example extent inside comparison boundary
###   % of comparison boundary inside example extent
###
### Outputs (in out_dir):
###   compare_extents_table.csv    City | Comparison Dataset | F1 | IoU
###   compare_extents_detail.csv   all areas and "% inside each other" metrics
################################################################################

rm(list = ls())

library(sf)
library(dplyr)
library(readr)

sf::sf_use_s2(FALSE)   # planar operations; all metrics use local UTM


# ==============================================================================
# 1. PATHS AND INPUTS
# ==============================================================================

data_dir <- "//DATASERVER1/ScienceData$/dthomson/Documents/zzz_WORK/p_WB_LandAtlas/AOIs_extents"
out_dir  <- data_dir

# Example urban extents
example_path       <- file.path(data_dir, "pilot_city_density_supported_urban_core.gpkg")
example_layer      <- "all_cities_density_supported_urban_core"
example_city_field <- "city"

# Pilot cities, in table order
cities <- c("Juba", "Kigali", "Johannesburg")

# Comparison datasets, in table order.
#   files: either one file for all cities (matched by largest spatial overlap),
#          or a named vector with one file per city (all features in the file used).
#   layer: layer name for multi-layer files (NULL if the file has one layer).
comparisons <- list(
  "GHS-UCDB" = list(
    files = file.path(data_dir, "ghs_ucdb_3cities.gpkg"),
    layer = "ghs_ucdb_3cities__ghsucdb",   # file also has layer "ghsucdb"
    match = "max_overlap"
  ),
  "GHS-FUA" = list(
    files = file.path(data_dir, "ghs_fua_3cities.gpkg"),
    layer = NULL,                          # set if the file has multiple layers
    match = "max_overlap"
  ),
  "Africapolis" = list(
    files = c(
      Juba         = file.path(data_dir, "africapolis_2020_Juba.gpkg"),
      Kigali       = file.path(data_dir, "africapolis_2020_kigali.gpkg"),
      Johannesburg = file.path(data_dir, "africapolis_2020_Johannesburg.gpkg")
    ),
    match = "all_features"
  ),
  "SPARC AOI" = list(
    files = c(
      Juba         = file.path(data_dir, "SPARC_AOI_Juba.geojson"),
      Kigali       = file.path(data_dir, "SPARC_AOI_Kigali.geojson"),
      Johannesburg = file.path(data_dir, "SPARC_AOI_CoJ.geojson")
    ),
    match = "all_features"
  )
)


# ==============================================================================
# 2. HELPER FUNCTIONS
# ==============================================================================

# Read a vector file; stop if a multi-layer file is given without a layer name
read_vector <- function(path, layer = NULL) {
  if (!file.exists(path)) stop("File does not exist: ", path, call. = FALSE)
  
  if (is.null(layer)) {
    layers <- sf::st_layers(path)$name
    if (length(layers) > 1) {
      stop("File has multiple layers (", paste(layers, collapse = ", "),
           "). Specify the layer to use: ", path, call. = FALSE)
    }
    x <- sf::st_read(path, quiet = TRUE)
  } else {
    x <- sf::st_read(path, layer = layer, quiet = TRUE)
  }
  
  if (is.na(sf::st_crs(x))) stop("No CRS defined for: ", path, call. = FALSE)
  
  x <- sf::st_make_valid(x)
  x <- x[!sf::st_is_empty(x), ]
  if (any(sf::st_geometry_type(x) == "GEOMETRYCOLLECTION")) {
    x <- sf::st_collection_extract(x, "POLYGON")
  }
  x
}

# Local UTM zone (EPSG code) from a geometry's centroid
utm_epsg <- function(g) {
  ll   <- sf::st_coordinates(sf::st_centroid(sf::st_union(sf::st_transform(g, 4326))))
  zone <- floor((ll[1, "X"] + 180) / 6) + 1
  if (ll[1, "Y"] >= 0) 32600 + zone else 32700 + zone
}

area_km2 <- function(g) {
  if (length(g) == 0 || all(sf::st_is_empty(g))) return(0)
  sum(as.numeric(sf::st_area(g))) / 1e6
}

dissolve <- function(g) sf::st_make_valid(sf::st_union(g))

# Comparison geometry for one city, projected to the example's CRS
get_comparison_geom <- function(x, example_geom, match) {
  
  if (match == "all_features") {
    return(list(
      geom = dissolve(sf::st_geometry(sf::st_transform(x, sf::st_crs(example_geom)))),
      n_features = nrow(x)
    ))
  }
  
  # match == "max_overlap": feature with the largest overlap with the example extent
  ex_native <- sf::st_transform(example_geom, sf::st_crs(x))
  cand <- x[lengths(sf::st_intersects(x, ex_native)) > 0, ]
  if (nrow(cand) == 0) return(NULL)
  
  cand <- sf::st_make_valid(sf::st_transform(cand, sf::st_crs(example_geom)))
  overlap <- vapply(
    seq_len(nrow(cand)),
    function(i) area_km2(suppressWarnings(
      sf::st_intersection(sf::st_geometry(cand)[i], example_geom))),
    numeric(1)
  )
  
  list(
    geom = dissolve(sf::st_geometry(cand)[which.max(overlap)]),
    n_features = 1L
  )
}

# Area-based agreement between example extent (A) and comparison boundary (B)
compare_pair <- function(A, B) {
  a <- area_km2(A)
  b <- area_km2(B)
  i <- area_km2(suppressWarnings(sf::st_intersection(A, B)))
  
  tibble::tibble(
    example_km2           = a,
    comparison_km2        = b,
    overlap_km2           = i,
    pct_example_in_comp   = 100 * i / a,   # % of example extent inside comparison
    pct_comp_in_example   = 100 * i / b,   # % of comparison inside example extent
    F1                    = 2 * i / (a + b),
    IoU                   = i / (a + b - i)
  )
}


# ==============================================================================
# 3. READ EXAMPLE URBAN EXTENTS
# ==============================================================================

example <- read_vector(example_path, example_layer)

missing_cities <- setdiff(cities, example[[example_city_field]])
if (length(missing_cities) > 0) {
  stop("Cities not found in example extents: ",
       paste(missing_cities, collapse = ", "), call. = FALSE)
}

# Read shared (all-city) comparison files once
shared <- lapply(comparisons, function(d) {
  if (length(d$files) == 1 && is.null(names(d$files))) read_vector(d$files, d$layer) else NULL
})


# ==============================================================================
# 4. COMPARE
# ==============================================================================

results <- list()

for (city in cities) {
  
  message("\n=== ", city, " ===")
  
  ex   <- example[example[[example_city_field]] == city, ]
  epsg <- utm_epsg(sf::st_geometry(ex))
  A    <- dissolve(sf::st_transform(sf::st_geometry(ex), epsg))
  
  for (ds in names(comparisons)) {
    
    d <- comparisons[[ds]]
    x <- if (!is.null(shared[[ds]])) shared[[ds]] else read_vector(d$files[[city]], d$layer)
    
    m <- get_comparison_geom(x, A, d$match)
    
    if (is.null(m)) {
      warning(ds, ": no feature overlaps the ", city, " example extent")
      met <- tibble::tibble(F1 = NA_real_, IoU = NA_real_)
      n_feat <- 0L
    } else {
      met <- compare_pair(A, m$geom)
      n_feat <- m$n_features
    }
    
    results[[length(results) + 1]] <- bind_cols(
      tibble::tibble(City = city, `Comparison Dataset` = ds,
                     n_features = n_feat, utm_epsg = epsg),
      met
    )
    
    message(sprintf("  %-12s F1 %.2f | IoU %.2f", ds, met$F1, met$IoU))
  }
}

detail <- bind_rows(results)


# ==============================================================================
# 5. WRITE OUTPUTS
# ==============================================================================

table_out <- detail %>%
  transmute(
    City,
    `Comparison Dataset`,
    F1  = round(F1, 2),
    IoU = round(IoU, 2)
  )

write_csv(table_out, file.path(out_dir, "compare_extents_table.csv"))
write_csv(detail,    file.path(out_dir, "compare_extents_detail.csv"))

print(table_out, n = Inf)
message("\nDone. Outputs written to: ", out_dir)