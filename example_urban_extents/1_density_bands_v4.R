##############################################################
### World Bank Contract 2026
### Settlements: Density Bands and Refined Built-up Extents
### Author: Dana R. Thomson
### Cleaned/documented version prepared from 1_density_bands_v3.R
### Purpose:
###   1. Prepare WSF PIS_19 masks and WSF raster extent polygons.
###   2. Combine WSF Tracker and Google Open Buildings 2.5D metrics.
###   3. Generate density/intensity band rasters and vector GeoPackages.
###   4. Derive compact, density-supported built-up extents for pilot cities.
##############################################################

# ============================================================================
# 0. SETUP
# ============================================================================

rm(list = ls())

library(terra)
library(sf)
library(dplyr)
library(stringr)
library(concaveman)

# ----------------------------------------------------------------------------
# Run controls
# ----------------------------------------------------------------------------
# Set these to FALSE if you only want to rerun later steps.
# For example, once the WSF masks and GEE extent polygons are written, you can
# usually set RUN_PREPARE_WSF_MASKS_AND_EXTENTS <- FALSE.

RUN_PREPARE_WSF_MASKS_AND_EXTENTS <- FALSE
RUN_CREATE_DENSITY_BANDS <- TRUE
RUN_CREATE_REFINED_EXTENTS <- TRUE

# ----------------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------------

wsf_dir <- "//DATASERVER1/ScienceData$/dthomson/Documents/zzz_WORK/Data/WorldBank/WSFtracker_202601"
out_dir <- "//DATASERVER1/ScienceData$/dthomson/Documents/zzz_WORK/p_WB_LandAtlas/AOIs_extents"
google_dir <- out_dir

dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)

# ----------------------------------------------------------------------------
# Cities and input files
# ----------------------------------------------------------------------------

cities <- c("Juba", "Kigali", "Johannesburg")

wsf_tracker_files <- c(
  Juba = file.path(wsf_dir, "Juba_WSFimperviousness_20160701-20260101.tif"),
  Kigali = file.path(wsf_dir, "Kigali_WSFimperviousness_20160701-20260101.tif"),
  Johannesburg = file.path(wsf_dir, "Johannesburg_WSFimperviousness_20160701-20260101.tif")
)

google_metric_files <- c(
  Juba = file.path(google_dir, "Juba_Google2p5D_metrics_250mWindow_2023.tif"),
  Kigali = file.path(google_dir, "Kigali_Google2p5D_metrics_250mWindow_2023.tif"),
  Johannesburg = file.path(google_dir, "Johannesburg_Google2p5D_metrics_250mWindow_2023.tif")
)

combined_metric_files <- c(
  Juba = file.path(out_dir, "Juba_WSF_Google2p5D_combined_250m_metrics.tif"),
  Kigali = file.path(out_dir, "Kigali_WSF_Google2p5D_combined_250m_metrics.tif"),
  Johannesburg = file.path(out_dir, "Johannesburg_WSF_Google2p5D_combined_250m_metrics.tif")
)

# WSF masks produced by Part 1. These are used only for optional edge expansion
# in the refined-extent workflow.
wsf_mask_files <- c(
  Juba = file.path(out_dir, "Juba_WSF_PIS19_nonzero_mask.tif"),
  Kigali = file.path(out_dir, "Kigali_WSF_PIS19_nonzero_mask.tif"),
  Johannesburg = file.path(out_dir, "Johannesburg_WSF_PIS19_nonzero_mask.tif")
)

# ----------------------------------------------------------------------------
# Output files
# ----------------------------------------------------------------------------

out_extent_gpkg <- file.path(out_dir, "WSF_Tracker_raster_extents_pilot_cities.gpkg")
out_extent_geojson <- file.path(out_dir, "WSF_Tracker_raster_extents_pilot_cities.json")
out_extent_shp <- file.path(out_dir, "WSF_Tracker_raster_extents_pilot_cities.shp")

out_refined_gpkg <- file.path(out_dir, "pilot_city_density_supported_urban_core.gpkg")
out_blob_csv <- file.path(out_dir, "pilot_city_density_supported_blob_membership.csv")
out_gap_csv <- file.path(out_dir, "pilot_city_density_supported_gap_evaluation.csv")
out_wsf_edge_csv <- file.path(out_dir, "pilot_city_wsf_edge_fabric_patch_evaluation.csv")

# ============================================================================
# 1. PARAMETERS
# ============================================================================

# ----------------------------------------------------------------------------
# WSF and Google density-band parameters
# ----------------------------------------------------------------------------

# WSF Tracker band 20 is PIS_19.
pis_band <- 20

# Google GEE output is on a 100 m grid. Metrics were calculated using a 250 m
# moving window in GEE. We also calculate WSF 250 m moving-window summaries here.
window_radius_m <- 250

# Minimum polygon size retained when vectorizing bands.
min_patch_area_km2 <- 0.01 # 0.01 km2 = 1 hectare

building_density_breaks <- c(0, 25, 50, 100, 250, 500, 1000, 2000, Inf)
building_density_labels <- c(
  ">0-25 bldgs/km2",
  "25-50 bldgs/km2",
  "50-100 bldgs/km2",
  "100-250 bldgs/km2",
  "250-500 bldgs/km2",
  "500-1000 bldgs/km2",
  "1000-2000 bldgs/km2",
  ">2000 bldgs/km2"
)

impervious_breaks <- c(0, 1, 2, 5, 10, 20, 40, 60, 100)
impervious_labels <- c(
  ">0-1 mean PIS",
  "1-2 mean PIS",
  "2-5 mean PIS",
  "5-10 mean PIS",
  "10-20 mean PIS",
  "20-40 mean PIS",
  "40-60 mean PIS",
  "60-100 mean PIS"
)

# Combined settlement-intensity bands. Visual inspection suggested that the
# 0.05-0.10 band corresponds to built-up periphery, while >0-0.05 often behaves
# more like diffuse peri-urban background.
combined_breaks <- c(0, 0.05, 0.10, 0.20, 0.35, 0.50, 0.70, 1.00)
combined_labels <- c(
  ">0-0.05 very low",
  "0.05-0.10 low",
  "0.10-0.20 low-medium",
  "0.20-0.35 medium",
  "0.35-0.50 medium-high",
  "0.50-0.70 high",
  "0.70-1.00 very high"
)

# ----------------------------------------------------------------------------
# Refined built-up extent parameters
# ----------------------------------------------------------------------------

candidate_threshold <- 0.05

low_min <- 0.05
lowmed_min <- 0.10
med_min <- 0.20
medhigh_min <- 0.35
high_min <- 0.50

# Band weights for blob scoring. The >0-0.05 band is not used as urban-core
# evidence. Higher bands contribute much more strongly to blob membership.
w_low <- 0.25
w_lowmed <- 1.00
w_med <- 3.00
w_medhigh <- 6.00
w_high <- 10.00

min_blob_area_km2 <- 0.10

near_blob_distance_m <- 1500
near_blob_min_score <- 0.25
near_blob_min_share_010_plus <- 0.05

distant_blob_distance_m <- 3500
distant_blob_min_score <- 0.45
distant_blob_min_share_020_plus <- 0.15

dense_nearby_distance_m <- 1500
dense_nearby_min_score <- 0.75
dense_nearby_min_share_035_plus <- 0.02

density_gap_close_buffer_m <- 500
density_gap_support_ring_m <- 250
min_gap_ring_share_005_plus <- 0.30
min_gap_ring_share_010_plus <- 0.10
max_density_supported_gap_area_km2 <- 8
density_gap_fill_iterations <- 1

final_prune_dist_m <- 100
simplify_tolerance_m <- 100

apply_final_concave_hull <- TRUE
concave_hull_concavity <- 2
concave_hull_length_threshold_m <- 500
concave_hull_simplify_tolerance_m <- 100

drop_islands_before_hull <- TRUE
min_island_part_area_km2 <- 2.0

# Optional WSF edge expansion. This expands the density-derived boundary only
# where there is nearby clustered WSF impervious fabric.
apply_wsf_edge_expansion <- TRUE
wsf_edge_search_buffer_m <- 750
wsf_resample_to_m <- 30
wsf_local_window_cells <- 5
wsf_min_local_count <- 3
wsf_max_patch_distance_to_core_m <- 200
wsf_min_patch_area_km2 <- 0.01
wsf_close_dist_m <- 60
wsf_prune_tendrils_m <- 45
wsf_simplify_tolerance_m <- 30

# ============================================================================
# 2. GENERAL HELPER FUNCTIONS
# ============================================================================

check_files_exist <- function(paths, label) {
  missing <- paths[!file.exists(paths)]
  if (length(missing) > 0) {
    stop(
      "Missing ", label, " file(s):\n",
      paste(missing, collapse = "\n"),
      call. = FALSE
    )
  }
}

get_band_by_pattern <- function(r, patterns, fallback_index = NULL) {
  nms <- names(r)

  for (p in patterns) {
    hit <- grep(p, nms, ignore.case = TRUE)
    if (length(hit) > 0) return(r[[hit[1]]])
  }

  if (!is.null(fallback_index)) {
    warning(
      "No band name matched patterns: ",
      paste(patterns, collapse = ", "),
      ". Using fallback band index: ",
      fallback_index
    )
    return(r[[fallback_index]])
  }

  stop(
    "Could not find a matching band. Available bands are:\n",
    paste(nms, collapse = "\n"),
    call. = FALSE
  )
}

classify_by_breaks <- function(r, breaks) {
  # Creates classes 1:(length(breaks)-1) using left-open, right-closed intervals:
  # > lower and <= upper. Zero/background values are set to NA.
  m <- cbind(
    breaks[-length(breaks)],
    breaks[-1],
    seq_len(length(breaks) - 1)
  )

  classified <- classify(r, rcl = m, include.lowest = FALSE, right = TRUE)
  classified <- ifel(r > 0, classified, NA)
  return(classified)
}

polygonize_bands <- function(band_raster, city, band_type, labels, out_gpkg) {
  message("Polygonizing ", city, " - ", band_type)

  names(band_raster) <- "band"

  p <- as.polygons(band_raster, dissolve = TRUE, values = TRUE, na.rm = TRUE)

  if (is.null(p) || nrow(p) == 0) {
    warning("No polygons created for ", city, " - ", band_type)
    return(NULL)
  }

  p_sf <- st_as_sf(p)
  band_col <- names(p_sf)[names(p_sf) != attr(p_sf, "sf_column")][1]
  names(p_sf)[names(p_sf) == band_col] <- "band"

  p_sf <- p_sf %>%
    mutate(
      city = city,
      band_type = band_type,
      band = as.integer(band),
      band_label = labels[band],
      area_km2 = as.numeric(st_area(.)) / 1e6
    ) %>%
    filter(area_km2 >= min_patch_area_km2) %>%
    select(city, band_type, band, band_label, area_km2, geometry)

  layer_name <- paste0(city, "_", band_type)

  st_write(
    p_sf,
    out_gpkg,
    layer = layer_name,
    append = file.exists(out_gpkg),
    quiet = TRUE
  )

  return(p_sf)
}

raster_extent_to_polygon <- function(city_name, raster_path) {
  r <- rast(raster_path)
  p <- as.polygons(ext(r), crs = crs(r))

  st_as_sf(p) %>%
    mutate(city = city_name) %>%
    select(city, geometry)
}

get_combined_intensity_band <- function(r) {
  hit <- grep("combined_settlement_intensity_250m", names(r), ignore.case = TRUE)

  if (length(hit) == 0) {
    stop(
      "Could not find band 'combined_settlement_intensity_250m'. Available bands are:\n",
      paste(names(r), collapse = "\n"),
      call. = FALSE
    )
  }

  x <- r[[hit[1]]]
  names(x) <- "combined_settlement_intensity_250m"
  return(x)
}

raster_to_union_sf <- function(r, value_name = "value") {
  names(r) <- value_name

  p <- as.polygons(r, dissolve = TRUE, values = TRUE, na.rm = TRUE)
  if (is.null(p) || nrow(p) == 0) return(NULL)

  p_sf <- st_make_valid(st_as_sf(p))
  out_geom <- st_union(st_geometry(p_sf))

  st_make_valid(st_sf(geometry = st_sfc(out_geom, crs = st_crs(p_sf))))
}

remove_polygon_holes <- function(poly) {
  if (is.null(poly) || nrow(poly) == 0) return(NULL)

  poly <- st_make_valid(poly)
  parts <- st_cast(st_geometry(poly), "POLYGON", warn = FALSE)
  if (length(parts) == 0) return(NULL)

  no_holes <- lapply(seq_along(parts), function(i) {
    coords <- st_coordinates(parts[i])
    exterior <- coords[coords[, "L1"] == 1, c("X", "Y"), drop = FALSE]

    if (nrow(exterior) < 4) return(NULL)
    if (!all(exterior[1, ] == exterior[nrow(exterior), ])) {
      exterior <- rbind(exterior, exterior[1, ])
    }

    st_polygon(list(as.matrix(exterior)))
  })

  no_holes <- no_holes[!sapply(no_holes, is.null)]
  if (length(no_holes) == 0) return(NULL)

  out_geom <- st_union(st_sfc(no_holes, crs = st_crs(poly)))
  st_make_valid(st_sf(geometry = st_sfc(out_geom, crs = st_crs(poly))))
}

clean_polygon <- function(poly, close_dist_m = 0, prune_dist_m = 0, simplify_tolerance_m = 0) {
  if (is.null(poly) || nrow(poly) == 0) return(NULL)

  x_geom <- st_geometry(st_make_valid(poly))

  # Closing: expands then contracts to fill small gaps and concavities.
  if (close_dist_m > 0) {
    x_geom <- st_buffer(x_geom, close_dist_m)
    x_geom <- st_union(x_geom)
    x_geom <- st_buffer(x_geom, -close_dist_m)
    x_geom <- st_make_valid(x_geom)
  }

  # Opening: contracts then expands to remove narrow tendrils.
  if (prune_dist_m > 0) {
    x_geom <- st_buffer(x_geom, -prune_dist_m)
    x_geom <- st_make_valid(x_geom)

    if (length(x_geom) == 0 || all(st_is_empty(x_geom))) return(NULL)

    x_geom <- st_buffer(x_geom, prune_dist_m)
    x_geom <- st_make_valid(x_geom)
  }

  x_geom <- st_make_valid(st_union(x_geom))

  if (simplify_tolerance_m > 0) {
    x_geom <- st_simplify(
      x_geom,
      dTolerance = simplify_tolerance_m,
      preserveTopology = TRUE
    )
    x_geom <- st_make_valid(x_geom)
  }

  st_make_valid(st_sf(geometry = st_sfc(x_geom, crs = st_crs(poly))))
}

drop_small_island_parts <- function(poly, min_part_area_km2 = 1.0, keep_largest = TRUE) {
  if (is.null(poly) || nrow(poly) == 0) return(NULL)

  poly <- st_make_valid(poly)
  parts <- st_cast(st_geometry(poly), "POLYGON", warn = FALSE)
  if (length(parts) == 0) return(NULL)

  parts_sf <- st_sf(part_id = seq_along(parts), geometry = parts, crs = st_crs(poly))
  parts_sf <- st_make_valid(parts_sf)
  parts_sf$part_area_km2 <- as.numeric(st_area(parts_sf)) / 1e6

  largest_id <- parts_sf$part_id[which.max(parts_sf$part_area_km2)]

  if (keep_largest) {
    parts_keep <- parts_sf %>%
      filter(part_id == largest_id | part_area_km2 >= min_part_area_km2)
  } else {
    parts_keep <- parts_sf %>%
      filter(part_area_km2 >= min_part_area_km2)
  }

  if (nrow(parts_keep) == 0) {
    parts_keep <- parts_sf %>% filter(part_id == largest_id)
  }

  out_geom <- st_union(st_geometry(parts_keep))
  st_make_valid(st_sf(geometry = st_sfc(out_geom, crs = st_crs(poly))))
}

zonal_tbl <- function(x, zones, fun, value_name) {
  z <- terra::zonal(x, zones, fun = fun, na.rm = TRUE)
  z <- as.data.frame(z)
  names(z)[1] <- "blob_id"
  names(z)[2] <- value_name
  return(z)
}

extract_mean_raster_value <- function(r, poly) {
  if (is.null(poly) || nrow(poly) == 0) return(0)

  val <- terra::extract(r, vect(poly), fun = mean, na.rm = TRUE)
  if (is.null(val) || nrow(val) == 0) return(0)

  out <- as.numeric(val[1, 2])
  if (is.na(out)) out <- 0
  return(out)
}

# ============================================================================
# 3. OPTIONAL WSF MASK AND GEE EXTENT PREPARATION
# ============================================================================

prepare_wsf_masks_and_extents <- function() {
  check_files_exist(wsf_tracker_files, "WSF Tracker")

  for (city in names(wsf_tracker_files)) {
    message("Processing WSF mask for: ", city)

    r <- rast(wsf_tracker_files[[city]])
    pis19 <- r[[pis_band]]
    names(pis19) <- "PIS_19"

    mask <- ifel(pis19 > 0, 1, NA)
    names(mask) <- "WSF_PIS19_nonzero_mask"

    out_file <- wsf_mask_files[[city]]

    writeRaster(
      mask,
      out_file,
      overwrite = TRUE,
      datatype = "INT1U",
      NAflag = 255
    )

    message("Written: ", out_file)
  }

  extent_polys <- bind_rows(
    lapply(names(wsf_tracker_files), function(city) {
      raster_extent_to_polygon(city, wsf_tracker_files[[city]])
    })
  )

  extent_polys_wgs84 <- st_transform(extent_polys, 4326)

  st_write(
    extent_polys_wgs84,
    out_extent_gpkg,
    layer = "wsf_tracker_extents",
    delete_dsn = TRUE,
    quiet = TRUE
  )

  st_write(extent_polys_wgs84, out_extent_geojson, delete_dsn = TRUE, quiet = TRUE)
  st_write(extent_polys_wgs84, out_extent_shp, delete_layer = TRUE, quiet = TRUE)

  message("Written WSF extent outputs:")
  message("  ", out_extent_gpkg)
  message("  ", out_extent_geojson)
  message("  ", out_extent_shp)

  invisible(extent_polys_wgs84)
}

# ============================================================================
# 4. DENSITY-BAND CREATION
# ============================================================================

process_city_density_bands <- function(city) {
  message("\n============================================================")
  message("Creating density bands for: ", city)
  message("============================================================")

  google <- rast(google_metric_files[[city]])
  message("Google bands for ", city, ":")
  print(names(google))

  bldg_count_250m <- get_band_by_pattern(
    google,
    patterns = c("sum_bldg_frac_count", "frac_count"),
    fallback_index = 1
  )
  names(bldg_count_250m) <- "google_sum_bldg_frac_count_250m"

  bldg_density_250m <- get_band_by_pattern(
    google,
    patterns = c("bldg_density", "density"),
    fallback_index = 2
  )
  names(bldg_density_250m) <- "google_bldg_density_km2_250m"

  mean_presence_250m <- get_band_by_pattern(
    google,
    patterns = c("mean_presence", "presence_mean"),
    fallback_index = 3
  )
  names(mean_presence_250m) <- "google_mean_presence_250m"

  p90_presence_250m <- get_band_by_pattern(
    google,
    patterns = c("p90_presence", "presence_p90"),
    fallback_index = 4
  )
  names(p90_presence_250m) <- "google_p90_presence_250m"

  mean_height_250m <- get_band_by_pattern(
    google,
    patterns = c("mean_height", "height"),
    fallback_index = 5
  )
  names(mean_height_250m) <- "google_mean_height_presence_gt_0p5_250m"

  volume_proxy_250m <- get_band_by_pattern(
    google,
    patterns = c("count_x_height", "volume"),
    fallback_index = 6
  )
  names(volume_proxy_250m) <- "google_sum_bldg_count_x_height_250m"

  template <- google[[1]]

  wsf <- rast(wsf_tracker_files[[city]])
  pis19 <- wsf[[pis_band]]
  names(pis19) <- "WSF_PIS19_raw"

  # Resample continuous PIS_19 values to the Google output grid.
  pis19_on_google_grid <- project(pis19, template, method = "bilinear")
  names(pis19_on_google_grid) <- "WSF_PIS19_on_google_grid"

  wsf_nonzero <- ifel(pis19_on_google_grid > 0, 1, NA)
  names(wsf_nonzero) <- "WSF_PIS19_nonzero_mask"

  wsf_nonzero_01 <- ifel(pis19_on_google_grid > 0, 1, 0)
  names(wsf_nonzero_01) <- "WSF_PIS19_nonzero_01"

  # WSF summaries in a circular 250 m window.
  w <- focalMat(template, d = window_radius_m, type = "circle")

  wsf_mean_pis19_250m <- focal(
    pis19_on_google_grid,
    w = w,
    fun = "mean",
    na.policy = "omit",
    na.rm = TRUE,
    fillvalue = NA
  )
  names(wsf_mean_pis19_250m) <- "WSF_mean_PIS19_250m"

  wsf_share_nonzero_250m <- focal(
    wsf_nonzero_01,
    w = w,
    fun = "mean",
    na.policy = "omit",
    na.rm = TRUE,
    fillvalue = NA
  )
  names(wsf_share_nonzero_250m) <- "WSF_share_nonzero_250m"

  # Combined settlement-intensity index, 0-1.
  wsf_norm <- clamp(wsf_mean_pis19_250m / 100, lower = 0, upper = 1)
  names(wsf_norm) <- "WSF_norm_250m"

  google_density_norm <- clamp(bldg_density_250m / 1000, lower = 0, upper = 1)
  names(google_density_norm) <- "Google_bldg_density_norm_250m"

  combined_intensity <- (0.5 * wsf_norm) + (0.5 * google_density_norm)
  names(combined_intensity) <- "combined_settlement_intensity_250m"

  combined_stack <- c(
    pis19_on_google_grid,
    wsf_nonzero,
    wsf_mean_pis19_250m,
    wsf_share_nonzero_250m,
    bldg_count_250m,
    bldg_density_250m,
    mean_presence_250m,
    p90_presence_250m,
    mean_height_250m,
    volume_proxy_250m,
    wsf_norm,
    google_density_norm,
    combined_intensity
  )

  names(combined_stack) <- c(
    "WSF_PIS19_on_google_grid",
    "WSF_PIS19_nonzero_mask",
    "WSF_mean_PIS19_250m",
    "WSF_share_nonzero_250m",
    "G2p5D_sum_bldg_frac_count_250m",
    "G2p5D_bldg_density_km2_250m",
    "G2p5D_mean_presence_250m",
    "G2p5D_p90_presence_250m",
    "G2p5D_mean_height_presence_gt_0p5_250m",
    "G2p5D_sum_bldg_count_x_height_250m",
    "WSF_norm_250m",
    "G2p5D_bldg_density_norm_250m",
    "combined_settlement_intensity_250m"
  )

  out_stack <- combined_metric_files[[city]]
  writeRaster(
    combined_stack,
    out_stack,
    overwrite = TRUE,
    datatype = "FLT4S",
    gdal = c("COMPRESS=LZW")
  )
  message("Written combined raster: ", out_stack)

  google_density_bands <- classify_by_breaks(bldg_density_250m, building_density_breaks)
  names(google_density_bands) <- "google_building_density_band"

  wsf_impervious_bands <- classify_by_breaks(wsf_mean_pis19_250m, impervious_breaks)
  names(wsf_impervious_bands) <- "wsf_imperviousness_band"

  combined_intensity_bands <- classify_by_breaks(combined_intensity, combined_breaks)
  names(combined_intensity_bands) <- "combined_intensity_band"

  band_stack <- c(google_density_bands, wsf_impervious_bands, combined_intensity_bands)

  out_bands_raster <- file.path(out_dir, paste0(city, "_WSF_Google2p5D_density_bands_250m.tif"))
  writeRaster(
    band_stack,
    out_bands_raster,
    overwrite = TRUE,
    datatype = "INT1U",
    gdal = c("COMPRESS=LZW")
  )
  message("Written band raster: ", out_bands_raster)

  out_gpkg <- file.path(out_dir, paste0(city, "_WSF_Google2p5D_density_bands_250m.gpkg"))
  if (file.exists(out_gpkg)) file.remove(out_gpkg)

  polygonize_bands(google_density_bands, city, "google_building_density", building_density_labels, out_gpkg)
  polygonize_bands(wsf_impervious_bands, city, "wsf_mean_imperviousness", impervious_labels, out_gpkg)
  polygonize_bands(combined_intensity_bands, city, "combined_settlement_intensity", combined_labels, out_gpkg)

  message("Written vector bands: ", out_gpkg)

  return(list(
    city = city,
    combined_raster = out_stack,
    band_raster = out_bands_raster,
    band_vectors = out_gpkg
  ))
}



# ============================================================================
# 5. REFINED BUILT-UP EXTENT HELPERS
# ============================================================================

make_wsf_30m_any_presence <- function(wsf_path, search_zone, reference_raster, target_res_m = 30, wsf_positive_min = 1) {
  if (!file.exists(wsf_path)) {
    warning("WSF file does not exist: ", wsf_path)
    return(NULL)
  }

  wsf <- rast(wsf_path)[[1]]
  wsf_bin <- ifel(!is.na(wsf) & wsf >= wsf_positive_min, 1, NA)
  names(wsf_bin) <- "wsf_10m_binary"

  search_zone <- st_make_valid(search_zone)
  search_zone_v <- vect(search_zone)

  template_10m <- rast(ext(search_zone_v), resolution = 10, crs = crs(reference_raster))

  wsf_10m_local <- project(wsf_bin, template_10m, method = "near")
  wsf_10m_local <- mask(wsf_10m_local, search_zone_v)

  wsf_10m_zero <- ifel(is.na(wsf_10m_local), 0, wsf_10m_local)
  agg_fact <- max(1, round(target_res_m / 10))

  wsf_30m_any <- aggregate(wsf_10m_zero, fact = agg_fact, fun = "max", na.rm = TRUE)
  wsf_30m_any <- ifel(wsf_30m_any >= 1, 1, NA)
  names(wsf_30m_any) <- "wsf_30m_any_presence"

  return(wsf_30m_any)
}

expand_core_with_wsf_edge_fabric <- function(
    poly,
    wsf_path,
    reference_raster,
    city = NA_character_,
    search_buffer_m = 750,
    target_res_m = 30,
    local_window_cells = 5,
    min_local_count = 3,
    max_patch_distance_to_core_m = 200,
    min_patch_area_km2 = 0.01,
    close_dist_m = 60,
    prune_tendrils_m = 45,
    simplify_tolerance_m = 30) {

  if (is.null(poly) || nrow(poly) == 0) {
    return(list(polygon = poly, patch_eval = tibble()))
  }

  poly <- st_make_valid(poly)
  search_zone <- st_make_valid(st_buffer(poly, search_buffer_m))

  wsf_30m_any <- make_wsf_30m_any_presence(
    wsf_path = wsf_path,
    search_zone = search_zone,
    reference_raster = reference_raster,
    target_res_m = target_res_m
  )

  if (is.null(wsf_30m_any)) {
    return(list(
      polygon = poly,
      patch_eval = tibble(
        city = city,
        patch_id = NA_integer_,
        patch_area_km2 = NA_real_,
        dist_to_core_m = NA_real_,
        keep_patch = FALSE,
        reason = "missing_wsf_file"
      )
    ))
  }

  wsf_30m_zero <- ifel(is.na(wsf_30m_any), 0, wsf_30m_any)
  local_w <- matrix(1, local_window_cells, local_window_cells)

  wsf_local_count <- focal(
    wsf_30m_zero,
    w = local_w,
    fun = "sum",
    na.policy = "omit",
    na.rm = TRUE,
    fillvalue = 0
  )

  wsf_fabric <- ifel(wsf_30m_zero == 1 & wsf_local_count >= min_local_count, 1, NA)
  names(wsf_fabric) <- "wsf_edge_fabric"

  if (global(wsf_fabric, "sum", na.rm = TRUE)[1, 1] == 0) {
    return(list(
      polygon = poly,
      patch_eval = tibble(
        city = city,
        patch_id = NA_integer_,
        patch_area_km2 = NA_real_,
        dist_to_core_m = NA_real_,
        keep_patch = FALSE,
        reason = "no_wsf_edge_fabric"
      )
    ))
  }

  wsf_patches <- patches(wsf_fabric, directions = 8, zeroAsNA = TRUE)
  names(wsf_patches) <- "wsf_patch_id"

  wsf_patch_poly <- as.polygons(wsf_patches, dissolve = TRUE, values = TRUE, na.rm = TRUE)
  if (is.null(wsf_patch_poly) || nrow(wsf_patch_poly) == 0) {
    return(list(
      polygon = poly,
      patch_eval = tibble(
        city = city,
        patch_id = NA_integer_,
        patch_area_km2 = NA_real_,
        dist_to_core_m = NA_real_,
        keep_patch = FALSE,
        reason = "no_wsf_patch_polygons"
      )
    ))
  }

  wsf_patch_sf <- st_make_valid(st_as_sf(wsf_patch_poly))
  patch_id_col <- names(wsf_patch_sf)[names(wsf_patch_sf) != attr(wsf_patch_sf, "sf_column")][1]
  names(wsf_patch_sf)[names(wsf_patch_sf) == patch_id_col] <- "patch_id"

  wsf_patch_sf$patch_area_km2 <- as.numeric(st_area(wsf_patch_sf)) / 1e6
  core_geom <- st_union(st_geometry(poly))
  wsf_patch_sf$dist_to_core_m <- as.numeric(st_distance(st_geometry(wsf_patch_sf), core_geom))

  patch_eval <- wsf_patch_sf %>%
    st_drop_geometry() %>%
    mutate(
      city = city,
      keep_patch = patch_area_km2 >= min_patch_area_km2 & dist_to_core_m <= max_patch_distance_to_core_m,
      reason = case_when(
        keep_patch ~ "kept_wsf_edge_fabric_patch",
        patch_area_km2 < min_patch_area_km2 ~ "excluded_small_patch",
        dist_to_core_m > max_patch_distance_to_core_m ~ "excluded_too_far_from_core",
        TRUE ~ "excluded"
      )
    )

  patches_keep <- wsf_patch_sf %>%
    filter(patch_area_km2 >= min_patch_area_km2, dist_to_core_m <= max_patch_distance_to_core_m)

  if (nrow(patches_keep) == 0) {
    return(list(polygon = poly, patch_eval = patch_eval))
  }

  wsf_keep <- st_sf(geometry = st_sfc(st_union(st_geometry(patches_keep)), crs = st_crs(poly)))
  wsf_keep <- st_make_valid(wsf_keep)

  wsf_keep <- clean_polygon(
    wsf_keep,
    close_dist_m = close_dist_m,
    prune_dist_m = prune_tendrils_m,
    simplify_tolerance_m = simplify_tolerance_m
  )

  if (is.null(wsf_keep) || nrow(wsf_keep) == 0) {
    return(list(polygon = poly, patch_eval = patch_eval))
  }

  out_geom <- st_union(st_union(st_geometry(poly)), st_union(st_geometry(wsf_keep)))
  out <- st_make_valid(st_sf(geometry = st_sfc(out_geom, crs = st_crs(poly))))
  out <- remove_polygon_holes(out)

  return(list(polygon = out, patch_eval = patch_eval))
}

density_supported_gap_fill_once <- function(
    poly,
    dens_005,
    dens_010,
    close_buffer_m = 500,
    support_ring_m = 250,
    min_ring_share_005_plus = 0.30,
    min_ring_share_010_plus = 0.10,
    max_gap_area_km2 = 8,
    city = NA_character_,
    iteration = 1) {

  if (is.null(poly) || nrow(poly) == 0) {
    return(list(polygon = poly, gap_eval = tibble()))
  }

  original_poly <- st_sf(
    geometry = st_sfc(st_union(st_geometry(st_make_valid(poly))), crs = st_crs(poly))
  )

  closed_poly <- clean_polygon(original_poly, close_dist_m = close_buffer_m)
  if (is.null(closed_poly)) return(list(polygon = poly, gap_eval = tibble()))

  added_geom <- suppressWarnings(
    st_difference(st_union(st_geometry(closed_poly)), st_union(st_geometry(original_poly)))
  )

  if (length(added_geom) == 0 || all(st_is_empty(added_geom))) {
    return(list(polygon = poly, gap_eval = tibble()))
  }

  added_geom <- st_collection_extract(added_geom, "POLYGON")
  if (length(added_geom) == 0 || all(st_is_empty(added_geom))) {
    return(list(polygon = poly, gap_eval = tibble()))
  }

  added_parts <- st_cast(added_geom, "POLYGON", warn = FALSE)
  if (length(added_parts) == 0) return(list(polygon = poly, gap_eval = tibble()))

  added_sf <- st_sf(gap_id = seq_along(added_parts), geometry = added_parts)
  added_sf <- st_make_valid(added_sf)
  added_sf$gap_area_km2 <- as.numeric(st_area(added_sf)) / 1e6
  added_sf <- added_sf %>% filter(gap_area_km2 <= max_gap_area_km2)

  if (nrow(added_sf) == 0) return(list(polygon = poly, gap_eval = tibble()))

  gap_eval <- lapply(seq_len(nrow(added_sf)), function(i) {
    gap <- added_sf[i, ]
    ring_outer <- st_buffer(gap, support_ring_m)

    ring_geom <- suppressWarnings(
      st_difference(st_union(st_geometry(ring_outer)), st_union(st_geometry(gap)))
    )

    if (length(ring_geom) == 0 || all(st_is_empty(ring_geom))) {
      return(tibble(
        gap_id = gap$gap_id,
        gap_area_km2 = gap$gap_area_km2,
        ring_share_005_plus = 0,
        ring_share_010_plus = 0,
        fill_gap = FALSE,
        fill_reason = "empty_ring"
      ))
    }

    ring <- st_make_valid(st_sf(geometry = st_sfc(ring_geom, crs = st_crs(gap))))
    ring_share_005 <- extract_mean_raster_value(dens_005, ring)
    ring_share_010 <- extract_mean_raster_value(dens_010, ring)

    fill_gap <- ring_share_005 >= min_ring_share_005_plus && ring_share_010 >= min_ring_share_010_plus

    tibble(
      gap_id = gap$gap_id,
      gap_area_km2 = gap$gap_area_km2,
      ring_share_005_plus = ring_share_005,
      ring_share_010_plus = ring_share_010,
      fill_gap = fill_gap,
      fill_reason = case_when(
        fill_gap ~ "density_supported_gap",
        ring_share_005 < min_ring_share_005_plus ~ "excluded_insufficient_005_support",
        ring_share_010 < min_ring_share_010_plus ~ "excluded_insufficient_010_support",
        TRUE ~ "excluded"
      )
    )
  }) %>%
    bind_rows() %>%
    mutate(city = city, iteration = iteration)

  gaps_to_fill <- added_sf %>%
    inner_join(gap_eval %>% filter(fill_gap) %>% select(gap_id), by = "gap_id")

  if (nrow(gaps_to_fill) == 0) {
    return(list(polygon = poly, gap_eval = gap_eval))
  }

  filled_geom <- st_union(st_union(st_geometry(original_poly)), st_union(st_geometry(gaps_to_fill)))
  filled_poly <- st_make_valid(st_sf(geometry = st_sfc(filled_geom, crs = st_crs(poly))))

  return(list(polygon = filled_poly, gap_eval = gap_eval))
}

fill_density_supported_gaps_iterative <- function(poly, dens_005, dens_010, n_iter = 1, city = NA_character_) {
  x <- poly
  all_gap_eval <- list()

  for (i in seq_len(n_iter)) {
    result <- density_supported_gap_fill_once(
      poly = x,
      dens_005 = dens_005,
      dens_010 = dens_010,
      close_buffer_m = density_gap_close_buffer_m,
      support_ring_m = density_gap_support_ring_m,
      min_ring_share_005_plus = min_gap_ring_share_005_plus,
      min_ring_share_010_plus = min_gap_ring_share_010_plus,
      max_gap_area_km2 = max_density_supported_gap_area_km2,
      city = city,
      iteration = i
    )

    x <- result$polygon
    all_gap_eval[[i]] <- result$gap_eval
  }

  return(list(polygon = x, gap_eval = bind_rows(all_gap_eval)))
}

make_concave_hull_envelope <- function(poly, concavity = 2, length_threshold = 500, simplify_tolerance_m = 100) {
  if (is.null(poly) || nrow(poly) == 0) return(NULL)

  poly <- st_make_valid(poly)
  dissolved <- st_union(st_geometry(poly))
  boundary_pts <- st_cast(st_boundary(dissolved), "POINT", warn = FALSE)
  pts_sf <- st_sf(geometry = boundary_pts, crs = st_crs(poly))

  if (nrow(pts_sf) < 4) return(poly)

  hull <- concaveman::concaveman(
    pts_sf,
    concavity = concavity,
    length_threshold = length_threshold
  )

  hull <- st_make_valid(st_as_sf(hull))

  if (simplify_tolerance_m > 0) {
    hull <- st_simplify(hull, dTolerance = simplify_tolerance_m, preserveTopology = TRUE)
    hull <- st_make_valid(hull)
  }

  remove_polygon_holes(hull)
}

# ============================================================================
# 6. REFINED BUILT-UP EXTENT CREATION
# ============================================================================

process_city_refined_extent <- function(city) {
  message("\n============================================================")
  message("Creating refined built-up extent for: ", city)
  message("============================================================")

  r <- rast(combined_metric_files[[city]])
  intensity <- get_combined_intensity_band(r)
  cell_area_km2 <- cellSize(intensity, unit = "km")

  dens_005 <- ifel(intensity >= 0.05, 1, 0)
  dens_010 <- ifel(intensity >= 0.10, 1, 0)
  names(dens_005) <- "dens_005_plus"
  names(dens_010) <- "dens_010_plus"

  candidate <- ifel(intensity >= candidate_threshold, 1, NA)
  names(candidate) <- "candidate_builtup"

  blobs <- patches(candidate, directions = 8, zeroAsNA = TRUE)
  names(blobs) <- "blob_id"

  blob_freq <- as.data.frame(freq(blobs))
  blob_freq <- blob_freq[!is.na(blob_freq$value), ]

  if (nrow(blob_freq) == 0) {
    warning("No candidate blobs found for ", city)
    return(NULL)
  }

  names(blob_freq) <- c("layer", "blob_id", "n_cells")
  blob_freq <- blob_freq %>% select(blob_id, n_cells)

  area_low <- ifel(intensity >= low_min & intensity < lowmed_min, cell_area_km2, 0)
  area_lowmed <- ifel(intensity >= lowmed_min & intensity < med_min, cell_area_km2, 0)
  area_med <- ifel(intensity >= med_min & intensity < medhigh_min, cell_area_km2, 0)
  area_medhigh <- ifel(intensity >= medhigh_min & intensity < high_min, cell_area_km2, 0)
  area_high <- ifel(intensity >= high_min, cell_area_km2, 0)

  blob_stats <- blob_freq %>%
    left_join(zonal_tbl(cell_area_km2, blobs, "sum", "area_km2"), by = "blob_id") %>%
    left_join(zonal_tbl(intensity, blobs, "mean", "mean_intensity"), by = "blob_id") %>%
    left_join(zonal_tbl(intensity, blobs, "max", "max_intensity"), by = "blob_id") %>%
    left_join(zonal_tbl(area_low, blobs, "sum", "area_005_010_km2"), by = "blob_id") %>%
    left_join(zonal_tbl(area_lowmed, blobs, "sum", "area_010_020_km2"), by = "blob_id") %>%
    left_join(zonal_tbl(area_med, blobs, "sum", "area_020_035_km2"), by = "blob_id") %>%
    left_join(zonal_tbl(area_medhigh, blobs, "sum", "area_035_050_km2"), by = "blob_id") %>%
    left_join(zonal_tbl(area_high, blobs, "sum", "area_050_plus_km2"), by = "blob_id") %>%
    mutate(
      across(
        c(area_005_010_km2, area_010_020_km2, area_020_035_km2, area_035_050_km2, area_050_plus_km2),
        ~ ifelse(is.na(.x), 0, .x)
      ),
      share_005_010 = area_005_010_km2 / area_km2,
      share_010_020 = area_010_020_km2 / area_km2,
      share_020_035 = area_020_035_km2 / area_km2,
      share_035_050 = area_035_050_km2 / area_km2,
      share_050_plus = area_050_plus_km2 / area_km2,
      share_010_plus = share_010_020 + share_020_035 + share_035_050 + share_050_plus,
      share_020_plus = share_020_035 + share_035_050 + share_050_plus,
      share_035_plus = share_035_050 + share_050_plus,
      weighted_band_score =
        (w_low * share_005_010) +
        (w_lowmed * share_010_020) +
        (w_med * share_020_035) +
        (w_medhigh * share_035_050) +
        (w_high * share_050_plus),
      weighted_band_mass =
        (w_low * area_005_010_km2) +
        (w_lowmed * area_010_020_km2) +
        (w_med * area_020_035_km2) +
        (w_medhigh * area_035_050_km2) +
        (w_high * area_050_plus_km2),
      city = city
    )

  main_blob_id <- blob_stats %>%
    filter(area_km2 >= min_blob_area_km2) %>%
    arrange(desc(weighted_band_mass), desc(weighted_band_score), desc(area_km2)) %>%
    slice(1) %>%
    pull(blob_id)

  if (length(main_blob_id) == 0) {
    warning("No main urban blob found for ", city)
    return(NULL)
  }

  main_blob_raster <- ifel(blobs == main_blob_id, 1, NA)
  names(main_blob_raster) <- "main_blob"

  dist_to_main <- gridDist(main_blob_raster, target = NA)
  names(dist_to_main) <- "dist_to_main_blob_m"

  blob_stats <- blob_stats %>%
    left_join(zonal_tbl(dist_to_main, blobs, "min", "dist_to_main_blob_m"), by = "blob_id") %>%
    mutate(dist_to_main_blob_m = ifelse(blob_id == main_blob_id, 0, dist_to_main_blob_m)) %>%
    mutate(
      include_urban_core =
        blob_id == main_blob_id |
        (
          area_km2 >= min_blob_area_km2 &
          dist_to_main_blob_m <= near_blob_distance_m &
          weighted_band_score >= near_blob_min_score &
          share_010_plus >= near_blob_min_share_010_plus
        ) |
        (
          area_km2 >= min_blob_area_km2 &
          dist_to_main_blob_m <= distant_blob_distance_m &
          weighted_band_score >= distant_blob_min_score &
          share_020_plus >= distant_blob_min_share_020_plus
        ) |
        (
          area_km2 >= min_blob_area_km2 &
          dist_to_main_blob_m <= dense_nearby_distance_m &
          weighted_band_score >= dense_nearby_min_score &
          share_035_plus >= dense_nearby_min_share_035_plus
        ),
      inclusion_reason = case_when(
        blob_id == main_blob_id ~ "main_weighted_urban_blob",
        area_km2 >= min_blob_area_km2 &
          dist_to_main_blob_m <= near_blob_distance_m &
          weighted_band_score >= near_blob_min_score &
          share_010_plus >= near_blob_min_share_010_plus ~ "nearby_density_supported_blob",
        area_km2 >= min_blob_area_km2 &
          dist_to_main_blob_m <= distant_blob_distance_m &
          weighted_band_score >= distant_blob_min_score &
          share_020_plus >= distant_blob_min_share_020_plus ~ "distant_strong_density_blob",
        area_km2 >= min_blob_area_km2 &
          dist_to_main_blob_m <= dense_nearby_distance_m &
          weighted_band_score >= dense_nearby_min_score &
          share_035_plus >= dense_nearby_min_share_035_plus ~ "nearby_very_dense_blob",
        TRUE ~ "excluded"
      ),
      diagnostic_class = case_when(
        include_urban_core ~ "included",
        dist_to_main_blob_m <= distant_blob_distance_m &
          weighted_band_score >= 0.20 &
          share_010_plus >= 0.05 ~ "near_miss",
        TRUE ~ "excluded"
      )
    )

  accepted_ids <- blob_stats %>% filter(include_urban_core) %>% pull(blob_id)
  if (length(accepted_ids) == 0) {
    warning("No accepted urban-core blobs for ", city)
    return(NULL)
  }

  accepted_raster <- classify(
    blobs,
    rcl = cbind(accepted_ids, accepted_ids, rep(1, length(accepted_ids))),
    others = NA,
    include.lowest = TRUE,
    right = NA
  )
  names(accepted_raster) <- "accepted_urban_core_blobs"

  urban_core_raw <- raster_to_union_sf(accepted_raster, "accepted_urban_core_blobs")
  urban_core_raw <- remove_polygon_holes(urban_core_raw)

  if (is.null(urban_core_raw)) {
    warning("Accepted urban core polygon is NULL for ", city)
    return(NULL)
  }

  gap_result <- fill_density_supported_gaps_iterative(
    poly = urban_core_raw,
    dens_005 = dens_005,
    dens_010 = dens_010,
    n_iter = density_gap_fill_iterations,
    city = city
  )

  urban_core <- gap_result$polygon
  if (is.null(urban_core) || nrow(urban_core) == 0) {
    warning("Urban core polygon is NULL after density-supported gap filling for ", city)
    return(NULL)
  }

  urban_core <- clean_polygon(
    urban_core,
    close_dist_m = 0,
    prune_dist_m = final_prune_dist_m,
    simplify_tolerance_m = simplify_tolerance_m
  )
  urban_core <- st_make_valid(remove_polygon_holes(urban_core))

  if (drop_islands_before_hull) {
    urban_core <- drop_small_island_parts(
      urban_core,
      min_part_area_km2 = min_island_part_area_km2,
      keep_largest = TRUE
    )
  }

  if (apply_wsf_edge_expansion) {
    wsf_result <- expand_core_with_wsf_edge_fabric(
      poly = urban_core,
      wsf_path = wsf_mask_files[[city]],
      reference_raster = intensity,
      city = city,
      search_buffer_m = wsf_edge_search_buffer_m,
      target_res_m = wsf_resample_to_m,
      local_window_cells = wsf_local_window_cells,
      min_local_count = wsf_min_local_count,
      max_patch_distance_to_core_m = wsf_max_patch_distance_to_core_m,
      min_patch_area_km2 = wsf_min_patch_area_km2,
      close_dist_m = wsf_close_dist_m,
      prune_tendrils_m = wsf_prune_tendrils_m,
      simplify_tolerance_m = wsf_simplify_tolerance_m
    )

    urban_core <- wsf_result$polygon
    wsf_patch_eval <- wsf_result$patch_eval
  } else {
    wsf_patch_eval <- tibble(
      city = city,
      patch_id = NA_integer_,
      patch_area_km2 = NA_real_,
      dist_to_core_m = NA_real_,
      keep_patch = FALSE,
      reason = "wsf_edge_expansion_not_applied"
    )
  }

  urban_core <- st_make_valid(remove_polygon_holes(urban_core))

  if (drop_islands_before_hull) {
    urban_core <- drop_small_island_parts(
      urban_core,
      min_part_area_km2 = min_island_part_area_km2,
      keep_largest = TRUE
    )
  }

  if (apply_final_concave_hull) {
    urban_core <- make_concave_hull_envelope(
      urban_core,
      concavity = concave_hull_concavity,
      length_threshold = concave_hull_length_threshold_m,
      simplify_tolerance_m = concave_hull_simplify_tolerance_m
    )
  }

  urban_core <- st_make_valid(remove_polygon_holes(urban_core))

  urban_core$city <- city
  urban_core$zone <- "urban_core"
  urban_core$method <- paste(
    "weighted_density_blob_membership",
    "density_supported_gap_fill",
    ifelse(apply_wsf_edge_expansion, "wsf_edge_expansion", "no_wsf_edge_expansion"),
    ifelse(apply_final_concave_hull, "concave_hull", "no_concave_hull"),
    sep = "_"
  )
  urban_core$candidate_threshold <- candidate_threshold
  urban_core$main_blob_id <- main_blob_id
  urban_core$n_accepted_blobs <- length(accepted_ids)
  urban_core$density_gap_close_buffer_m <- density_gap_close_buffer_m
  urban_core$density_gap_support_ring_m <- density_gap_support_ring_m
  urban_core$gap_fill_iterations <- density_gap_fill_iterations
  urban_core$wsf_edge_expansion <- apply_wsf_edge_expansion
  urban_core$wsf_edge_search_buffer_m <- wsf_edge_search_buffer_m
  urban_core$wsf_min_local_count <- wsf_min_local_count
  urban_core$wsf_max_patch_distance_to_core_m <- wsf_max_patch_distance_to_core_m
  urban_core$final_concave_hull <- apply_final_concave_hull
  urban_core$concave_hull_concavity <- concave_hull_concavity
  urban_core$concave_hull_length_threshold_m <- concave_hull_length_threshold_m
  urban_core$area_km2 <- as.numeric(st_area(urban_core)) / 1e6

  urban_core <- urban_core %>%
    select(
      city,
      zone,
      method,
      candidate_threshold,
      main_blob_id,
      n_accepted_blobs,
      density_gap_close_buffer_m,
      density_gap_support_ring_m,
      gap_fill_iterations,
      wsf_edge_expansion,
      wsf_edge_search_buffer_m,
      wsf_min_local_count,
      wsf_max_patch_distance_to_core_m,
      final_concave_hull,
      concave_hull_concavity,
      concave_hull_length_threshold_m,
      area_km2,
      geometry
    )

  # IMPORTANT FIX relative to v3:
  # Return a named list so the final bind_rows() calls work.
  return(list(
    boundaries = urban_core,
    blob_stats = blob_stats,
    gap_eval = gap_result$gap_eval,
    wsf_patch_eval = wsf_patch_eval
  ))
}

# ============================================================================
# 7. RUN WORKFLOW
# ============================================================================

if (RUN_PREPARE_WSF_MASKS_AND_EXTENTS) {
  prepare_wsf_masks_and_extents()
}

if (RUN_CREATE_DENSITY_BANDS) {
  check_files_exist(wsf_tracker_files, "WSF Tracker")
  check_files_exist(google_metric_files, "Google 2.5D metric")

  density_band_outputs <- lapply(cities, process_city_density_bands)
  names(density_band_outputs) <- cities
  print(density_band_outputs)
}

if (RUN_CREATE_REFINED_EXTENTS) {
  check_files_exist(combined_metric_files, "combined WSF/Google metric")

  if (apply_wsf_edge_expansion) {
    # If these are missing, either run RUN_PREPARE_WSF_MASKS_AND_EXTENTS <- TRUE
    # first, or set apply_wsf_edge_expansion <- FALSE.
    check_files_exist(wsf_mask_files, "WSF nonzero mask")
  }

  if (file.exists(out_refined_gpkg)) file.remove(out_refined_gpkg)

  refined_results <- lapply(cities, process_city_refined_extent)
  names(refined_results) <- cities
  refined_results_nonnull <- refined_results[!sapply(refined_results, is.null)]

  if (length(refined_results_nonnull) == 0) {
    stop("No refined settlement extents were produced.", call. = FALSE)
  }

  all_boundaries <- bind_rows(lapply(refined_results_nonnull, `[[`, "boundaries"))
  all_blob_stats <- bind_rows(lapply(refined_results_nonnull, `[[`, "blob_stats"))
  all_gap_eval <- bind_rows(lapply(refined_results_nonnull, `[[`, "gap_eval"))
  all_wsf_patch_eval <- bind_rows(lapply(refined_results_nonnull, `[[`, "wsf_patch_eval"))

  st_write(
    all_boundaries,
    out_refined_gpkg,
    layer = "all_cities_density_supported_urban_core",
    append = FALSE,
    quiet = TRUE
  )

  write.csv(all_blob_stats, out_blob_csv, row.names = FALSE)
  write.csv(all_gap_eval, out_gap_csv, row.names = FALSE)
  write.csv(all_wsf_patch_eval, out_wsf_edge_csv, row.names = FALSE)

  message("Done.")
  message("Urban core boundaries written to: ", out_refined_gpkg)
  message("Blob membership written to: ", out_blob_csv)
  message("Density-supported gap evaluation written to: ", out_gap_csv)
  message("WSF edge-fabric patch evaluation written to: ", out_wsf_edge_csv)
}

# ============================================================================
# NOTE ON GOOGLE EARTH ENGINE STEP
# ============================================================================
# The original v3 script included a long commented Earth Engine block that creates
# the Google Open Buildings 2.5D 250 m metrics. I removed it from the executable
# R workflow to reduce clutter and prevent confusion. Keep that GEE code in a
# separate .js file, for example:
#   0_create_google2p5d_250m_metrics_GEE.js
#
# The R workflow assumes those GEE outputs already exist as:
#   <City>_Google2p5D_metrics_250mWindow_2023.tif
# ============================================================================
