# =============================================================================
# Script 2 -- Segment review flags from WSF change + edge spacing
# -----------------------------------------------------------------------------
# Inputs per city:
#   metrics : .../[city]/analysis/segment_vector_summary_metrics.gpkg  (script 1)
#   wsf     : one .tif per city in cfg$wsf$dir. TWO products, same 20-band,
#             semiannual (Jul 2016 ... Jan 2026) structure:
#               * WSFimperviousness  -- bands PIS, PIS_1, ...  : impervious
#                 FRACTION per pixel (0-1).
#               * WSFtracker         -- bands built_YYYYMMDD   : BINARY 0/1,
#                 pixel is "majority settled" (impervious > 0.5). Underestimates
#                 impervious surface in low-density areas; used only where
#                 imperviousness is unavailable.
#             Resolver PREFERS imperviousness and FALLS BACK to tracker; the
#             product used is recorded per segment in `wsf_product`.
#
# Both products are in [0,1] per pixel, so impervious/settled AREA per segment
# = coverage-weighted sum of pixel value x cell area. One code path, scale = 1.
#
# Logic:
#   Per segment, extract impervious/settled area at the BASELINE band (the band
#   for create_year_q1, the IQR-low creation year) and the LATEST band, as a
#   SHARE of the segment.
#   change_pct = abs(a - b) / max(a, b) * 100                  (symmetric, 0-100)
#   class : "poor" when flag_outdated condition (a) holds;
#           "medium" when change_pct >= THRESH_MEDIUM; else "high";
#           "no footprints" when there is meaningful impervious surface
#           today (> NOFOOT_SHARE OR > NOFOOT_AREA_M2) but no footprints.
#
# OUTPUT FLAGS:
#   flag_outdated = 1 if
#     (a) change_pct > THRESH_POOR AND coverage_overture > COVERAGE_MIN AND
#         imperv_area_change_m2 > CHANGE_AREA_MIN_M2 ; OR
#     (b) no-footprints class AND imperv_area_today_m2 > TODAY_AREA_MIN_M2.
#   flag_complex  = 1 if edge_knn_q1 < COMPLEX_EDGE_THRESH metres AND
#                   bldg_density_per_ha > COMPLEX_DENS_MIN (both from script 1).
#
# Output: .../[city]/analysis/segment_flags.gpkg
# =============================================================================

rm(list = ls())

suppressPackageStartupMessages({
  library(sf)
  library(terra)
  library(exactextractr)
  library(dplyr)
  library(tibble)
})

# ============================== CONFIG =======================================
cfg <- list(
  base_dir = "//DATASERVER1/ScienceData$/dthomson/Documents/zzz_WORK/Data/WorldBank/Integrated_Overture_3DGloBFP",
  
  # hard-coded run list (folder names, lowercase to match the metrics folders;
  # WSF filenames are matched case-insensitively)
 # cities = c("juba", "kigali", "johannesburg", "garissa", "dakar", "tripoli"),
  cities = c("juba", "kigali", "garissa", "dakar", "tripoli"),
  
  # ---- WSF rasters ----------------------------------------------------------
  wsf = list(
    dir    = "//DATASERVER1/ScienceData$/dthomson/Documents/zzz_WORK/Data/WorldBank/WSFtracker_202601",
    scale  = 1,            # PIS is 0-1; tracker is 0/1 -> both already fractional
    nodata = NA,           # set numeric to force-mask a fill value
    baseline_half = "first",  # band within create_year_q1's year: "first"|"jan"|"jul"
    latest_band   = "last"    # "last" or an integer band number
  ),
  
  # ---- thresholds -----------------------------------------------------------
  THRESH_POOR         = 25,
  THRESH_MEDIUM       = 10,
  NOFOOT_SHARE        = 0.10,
  NOFOOT_AREA_M2      = 100,
  COVERAGE_MIN        = 0.05,   # min Overture footprint coverage for flag_outdated
  CHANGE_AREA_MIN_M2  = 10000,  # (a) min absolute settled-area CHANGE (m2)
  TODAY_AREA_MIN_M2   = 10000,  # (b) min impervious area TODAY (no-footprints gap)
  COMPLEX_EDGE_THRESH = 1.5,   # edge_knn_q1 below this (m) ...
  COMPLEX_DENS_MIN    = 25,    # ...AND bldg_density_per_ha above this -> complex
  
  # ---- output ---------------------------------------------------------------
  in_name  = "segment_vector_summary_metrics.gpkg",
  out_name = "segment_flags.gpkg",
  out_subdir = "analysis",
  segment_id_col = "SEGMENT_UID"
)

# ============================ BAND <-> YEAR ==================================
# band 1 = Jul 2016, 2 = Jan 2017, 3 = Jul 2017, ... (odd = Jul, even = Jan)
wsf_band_date <- function(n) if (n %% 2 == 1) c(2016 + (n - 1) / 2, 7L) else c(2016 + n / 2, 1L)

baseline_band_for_year <- function(year, nbands, half = "first") {
  if (is.na(year)) return(NA_integer_)
  yr <- round(year)
  if (yr <= 2016) return(1L)
  jan <- as.integer(2 * (yr - 2016)); jul <- jan + 1L
  band <- switch(half, jan = jan, jul = jul, first = jan, jan)
  as.integer(min(max(band, 1L), nbands))
}

# ============================ WSF RESOLUTION + EXTRACTION ====================
# Prefer imperviousness; fall back to tracker. Returns path + product label.
resolve_wsf <- function(dir, city) {
  cands <- list.files(dir, pattern = "\\.tif$", full.names = TRUE)
  bn <- basename(cands)
  imp <- cands[grepl(paste0("^", city, ".*WSFimperviousness"), bn, ignore.case = TRUE)]
  trk <- cands[grepl(paste0("^", city, ".*WSFtracker"),        bn, ignore.case = TRUE)]
  if (length(imp)) return(list(path = imp[1], product = "imperviousness"))
  if (length(trk)) return(list(path = trk[1], product = "tracker"))
  stop("No WSF raster for '", city, "' in ", dir)
}

# Returns matrix [n_segments x n_bands] of impervious/settled AREA (m2).
extract_impervious_m2 <- function(seg, wpath, wsfcfg) {
  r <- terra::rast(wpath)
  if (!is.na(wsfcfg$nodata)) terra::NAflag(r) <- wsfcfg$nodata
  seg_r  <- sf::st_transform(seg, terra::crs(r))       # match raster CRS
  frac   <- r * wsfcfg$scale                            # 0-1 fraction (or 0/1)
  area_r <- terra::cellSize(r[[1]], unit = "m")         # true m2 per cell
  m2 <- exactextractr::exact_extract(frac, seg_r, fun = "weighted_sum",
                                     weights = area_r, progress = FALSE)
  M <- as.matrix(m2); colnames(M) <- paste0("band", seq_len(ncol(M))); M
}

# ============================ PER-CITY PIPELINE =============================
process_city <- function(city, cfg) {
  message("== ", city, " ==")
  in_path <- file.path(cfg$base_dir, city, "analysis", cfg$in_name)
  stopifnot(file.exists(in_path))
  
  seg <- sf::st_read(in_path, quiet = TRUE)
  id_col <- cfg$segment_id_col
  need <- c("create_year_q1", "bldg_count_overture")
  miss <- setdiff(need, names(seg))
  if (length(miss)) stop("Input missing column(s): ", paste(miss, collapse = ", "))
  if (!"edge_knn_q1" %in% names(seg))
    warning("Input lacks 'edge_knn_q1'; flag_complex will be NA.")
  
  seg_area <- if ("seg_area_m2" %in% names(seg)) seg$seg_area_m2 else as.numeric(sf::st_area(seg))
  
  # --- WSF: resolve product, extract impervious/settled area per band ---
  wsf_info <- resolve_wsf(cfg$wsf$dir, city)
  message("  WSF product: ", wsf_info$product, "  (", basename(wsf_info$path), ")")
  M <- extract_impervious_m2(seg, wsf_info$path, cfg$wsf)
  nbands <- ncol(M)
  if (nbands != 20) warning("  expected 20 WSF bands, found ", nbands)
  latest <- if (identical(cfg$wsf$latest_band, "last")) nbands else as.integer(cfg$wsf$latest_band)
  message("  bands: ", nbands, " | latest = band ", latest,
          " (", paste(wsf_band_date(latest), collapse = "-"), ")")
  
  n <- nrow(seg)
  base_band <- vapply(seg$create_year_q1, baseline_band_for_year,
                      integer(1), nbands = nbands, half = cfg$wsf$baseline_half)
  
  a_area <- rep(NA_real_, n)
  ok <- !is.na(base_band)
  a_area[ok] <- M[cbind(which(ok), base_band[ok])]
  b_area <- M[, latest]
  
  a <- a_area / seg_area          # share_baseline
  b <- b_area / seg_area          # share_today
  
  denom <- pmax(a, b)
  change_pct <- ifelse(denom > 0, abs(a - b) / denom * 100, 0)
  change_m2  <- abs(b_area - a_area)                 # absolute settled-area change
  
  cov   <- if ("coverage_overture" %in% names(seg)) seg$coverage_overture else rep(NA_real_, n)
  nbldg <- dplyr::coalesce(seg$bldg_count_overture, 0L)
  no_foot <- (b > cfg$NOFOOT_SHARE | b_area > cfg$NOFOOT_AREA_M2) & (nbldg == 0)
  
  # flag_outdated conditions:
  # (a) real growth: change_pct > THRESH_POOR AND footprint coverage > COVERAGE_MIN
  #     AND absolute settled-area CHANGE > CHANGE_AREA_MIN_M2.
  cond_a <- !is.na(change_pct) & change_pct > cfg$THRESH_POOR &
    !is.na(cov)        & cov        > cfg$COVERAGE_MIN &
    !is.na(change_m2)  & change_m2  > cfg$CHANGE_AREA_MIN_M2
  # (b) data gap: no-footprints class AND substantial impervious TODAY
  #     > TODAY_AREA_MIN_M2.
  cond_b <- no_foot &
    !is.na(b_area) & b_area > cfg$TODAY_AREA_MIN_M2
  
  flag_outdated <- as.integer(cond_a | cond_b)
  
  imperv_class <- ifelse(
    no_foot, "no footprints",
    ifelse(is.na(change_pct), NA_character_,
           ifelse(cond_a, "poor",
                  ifelse(change_pct >= cfg$THRESH_MEDIUM, "medium", "high"))))
  # complex/informal morphology: tight spacing AND genuinely dense, so sparse
  # segments with a stray close pair (edge_knn_q1 driven to ~0) don't qualify.
  edge_q1 <- if ("edge_knn_q1" %in% names(seg)) seg$edge_knn_q1 else rep(NA_real_, n)
  dens    <- if ("bldg_density_per_ha" %in% names(seg)) seg$bldg_density_per_ha else rep(NA_real_, n)
  flag_complex <- as.integer(!is.na(edge_q1) & edge_q1 < cfg$COMPLEX_EDGE_THRESH &
                               !is.na(dens)    & dens   > cfg$COMPLEX_DENS_MIN)
  
  seg_out <- seg |>
    dplyr::mutate(
      wsf_product             = wsf_info$product,
      imperv_baseline_band    = base_band,
      imperv_baseline_year    = round(create_year_q1),
      imperv_area_baseline_m2 = a_area,
      imperv_area_today_m2    = b_area,
      imperv_area_change_m2   = change_m2,
      imperv_share_baseline   = a,
      imperv_share_today      = b,
      imperv_change_pct       = change_pct,
      imperv_class            = imperv_class,
      flag_outdated           = flag_outdated,
      flag_complex            = flag_complex
    )
  
  out_dir <- file.path(cfg$base_dir, city, cfg$out_subdir)
  dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)
  out_path <- file.path(out_dir, cfg$out_name)
  sf::st_write(seg_out, out_path, delete_dsn = TRUE, quiet = TRUE)
  
  tb <- table(factor(imperv_class,
                     levels = c("poor", "medium", "high", "no footprints")),
              useNA = "ifany")
  message("  classes: ", paste(sprintf("%s=%d", names(tb), as.integer(tb)), collapse = "  "))
  message("  flag_outdated=", sum(flag_outdated, na.rm = TRUE),
          " (cond_a=", sum(cond_a, na.rm = TRUE), ", cond_b=", sum(cond_b, na.rm = TRUE), ")",
          "  flag_complex=", sum(flag_complex, na.rm = TRUE),
          "  -> wrote ", out_path)
  invisible(seg_out)
}

# ================================ RUN ========================================
main <- function(cfg) {
  cities <- cfg$cities
  res <- data.frame(city = cities, status = NA_character_, seconds = NA_real_,
                    stringsAsFactors = FALSE)
  for (k in seq_along(cities)) {
    city <- cities[k]
    message("\n[", k, "/", length(cities), "] ", city)
    t0 <- Sys.time()
    ok <- tryCatch({ process_city(city, cfg); TRUE },
                   error = function(e) { message("  !! ", city, " failed: ", conditionMessage(e)); FALSE })
    res$status[k]  <- if (ok) "ok" else "FAILED"
    res$seconds[k] <- round(as.numeric(difftime(Sys.time(), t0, units = "secs")), 1)
  }
  message("\n==== SUMMARY ====")
  for (k in seq_len(nrow(res)))
    message(sprintf("  %-22s %-7s %7.1fs", res$city[k], res$status[k], res$seconds[k]))
  message("  ", sum(res$status == "ok"), "/", nrow(res), " succeeded.")
  invisible(res)
}

if (sys.nframe() == 0) main(cfg)
