# =============================================================================
# 5_summary_tables.R
# Report summary tables from the final block and segment attribute tables
#
#   Table 4.11  Total and new buildings by city (3 Pilot Cities)
#   Table 4.13  Segments flagged for review by city (3 Pilot Cities)
#
# INPUT   <city>_blocks_segments_complete_in_AOI_final.gpkg, layer
#         "segments_final" (output of 4_segment_coverAOI_attributes.R).
#         Attributes only; no geometry is read.
# OUTPUT  <aug_dir>/table_4_11_buildings_final.csv
#         <aug_dir>/table_4_13_flags_final.csv
#
# DEFINITIONS
#   Impervious area      WSF Tracker cells x 100 m2 (10 m cells), in km2
#   Void segments        segments with seg_source == "new", i.e. formed from
#                        blocks outside the original (settlement) segments;
#                        set `void_def` to "void_share" to use segments whose
#                        area is mostly void units instead (needs blocks)
#   Complex flags        rule A / rule B: segment holds >= cmplx_min_n
#                        buildings meeting that rule; overall = cmplx_flag
#                        from script 4 (>= cmplx_min_n buildings meeting
#                        either rule)
#   Missing flag         PROVISIONAL: estimated new buildings in the past
#                        three years >= missing_min, using the `missing_src`
#                        building layer. Not yet in script 4; confirm the rule.
# =============================================================================

rm(list = ls())

library(sf)
library(dplyr)

# ---- Configuration -----------------------------------------------------------
aug_dir <- "//DATASERVER1/ScienceData$/dthomson/Documents/zzz_WORK/p_WB_LandAtlas/deliverables/Data - Revising block segment attributes"
cities  <- c(Juba = "juba", Kigali = "kigali") #c(Juba = "juba", Kigali = "kigali", Johannesburg = "johannesburg")

seg_layer <- "segments_final"
blk_layer <- "blocks_final"
gpkg_of   <- function(city) file.path(aug_dir, paste0(city, "_blocks_segments_complete_in_AOI_final.gpkg"))

void_def     <- "new_segments"   # "new_segments" or "void_share"
void_share   <- 0.5              # for "void_share": min share of segment area in void units
cmplx_min_n  <- 3                # buildings meeting a rule for a rule-specific flag (as script 4)
missing_min  <- 2                # PROVISIONAL: estimated new buildings (3 yrs) for a missing flag
missing_src  <- "intgr"          # "intgr" or "ovr": ratio used for the missing flag

cell_km2 <- 100 / 1e6            # one WSF Tracker cell (10 m x 10 m) in km2

# ---- Helpers -----------------------------------------------------------------
read_attrs <- function(path, layer) {
  if (!file.exists(path)) stop("Not found: ", path)
  st_drop_geometry(st_read(path, layer = layer, quiet = TRUE))
}

fmt_n   <- function(x) formatC(round(x), format = "d", big.mark = ",")
fmt_km2 <- function(x) formatC(x, format = "f", digits = 1, big.mark = ",")
fmt_pct <- function(x) paste0(formatC(100 * x, format = "f", digits = 1), "%")
n_pct   <- function(n, d) paste0(fmt_n(n), " (", fmt_pct(n / d), ")")

# Void segments: logical per segment row
void_segments <- function(seg, city) {
  if (void_def == "new_segments") return(seg$seg_source == "new")
  blk <- read_attrs(gpkg_of(city), blk_layer)
  sh <- blk |>
    group_by(segment_id) |>
    summarise(v = sum(block_area_m2[source == "aoi_void"]) / sum(block_area_m2), .groups = "drop")
  coalesce(sh$v[match(seg$segment_id, sh$segment_id)], 0) >= void_share
}

# ---- Per-city summaries ------------------------------------------------------
city_tables <- function(label, city) {
  message("Reading ", city)
  s <- read_attrs(gpkg_of(city), seg_layer)
  void <- void_segments(s, city)
  sum0 <- function(x) sum(x, na.rm = TRUE)
  new_with_void <- function(x) paste0(fmt_n(sum0(x)), " (", fmt_pct(sum0(x[void]) / sum0(x)), " in void segments)")
  
  t411 <- tibble(
    Indicator = c(
      "Population, LandScan-Mosaic-Ambient (#)",
      "Overture buildings, June 2026 (#)",
      "Integrated buildings, June 2026 (#)",
      "Impervious area, January 2026 (km²)",
      "New impervious area, past year (km²)",
      "New impervious area, past three years (km²)",
      "New buildings, past year",
      "   Overture (% in void segments)",
      "   Integrated, cleaned (% in void segments)",
      "New buildings, past three years",
      "   Overture (% in void segments)",
      "   Integrated, cleaned (% in void segments)",
      "Estimated total buildings, January 2026 (Overture / integrated ratio)",
      "Segments (#; % void segments)"),
    !!label := c(
      fmt_n(sum0(s$pop_lsm_ambient)),
      fmt_n(sum0(s$ovr_bldg_ct_06_2026)),
      fmt_n(sum0(s$intgr_bldg_ct_06_2026)),
      fmt_km2(sum0(s$wsf_pixel_ct_01_2026) * cell_km2),
      fmt_km2(sum0(s$new_wsf_pixel_last_1yr) * cell_km2),
      fmt_km2(sum0(s$new_wsf_pixel_last_3yrs) * cell_km2),
      "",
      new_with_void(s$ovr_new_bldgs_1yr),
      new_with_void(s$intgr_new_bldgs_last_1yr),
      "",
      new_with_void(s$ovr_new_bldgs_3yr),
      new_with_void(s$intgr_new_bldgs_last_3yr),
      paste(fmt_n(sum0(s$wsf_est_bldgs_01_2026_ovr)), "/", fmt_n(sum0(s$wsf_est_bldgs_01_2026_intgr))),
      n_pct(sum(void), nrow(s)))
  )
  
  # Flags
  N    <- nrow(s)
  pop  <- sum0(s$pop_lsm_ambient)
  flA  <- coalesce(s$cmplx_bldg_ct_large, 0L) >= cmplx_min_n
  flB  <- coalesce(s$cmplx_bldg_ct_small, 0L) >= cmplx_min_n
  flC  <- coalesce(s$cmplx_flag, 0L) == 1
  newb <- if (missing_src == "ovr") s$ovr_new_bldgs_3yr else s$intgr_new_bldgs_last_3yr
  flM  <- coalesce(newb, 0) >= missing_min
  pop_pct <- function(f) fmt_pct(sum0(s$pop_lsm_ambient[f]) / pop)
  
  t413 <- tibble(
    Flag = c(
      "Segments (#)",
      "Complex morphology, rule A: large buildings in crowded fabric (# and %)",
      "Complex morphology, rule B: dense small buildings (# and %)",
      "Complex morphology, either rule (# and %)",
      "Missing buildings (# and %) [provisional]",
      "Complex morphology and missing buildings (# and %)",
      "Any flag (# and %)",
      "Population in segments with any flag (% of AOI)",
      "Buildings meeting rule A / rule B (#)"),
    !!label := c(
      fmt_n(N),
      n_pct(sum(flA), N),
      n_pct(sum(flB), N),
      n_pct(sum(flC), N),
      n_pct(sum(flM), N),
      n_pct(sum(flC & flM), N),
      n_pct(sum(flC | flM), N),
      pop_pct(flC | flM),
      paste(fmt_n(sum0(s$cmplx_bldg_ct_large)), "/", fmt_n(sum0(s$cmplx_bldg_ct_small))))
  )
  
  # Consistency checks against the block layer (counts should sum exactly)
  b <- read_attrs(gpkg_of(city), blk_layer)
  chk <- c(ovr = sum0(b$ovr_bldg_ct_06_2026) - sum0(s$ovr_bldg_ct_06_2026),
           wsf = sum0(b$wsf_pixel_ct_01_2026) - sum0(s$wsf_pixel_ct_01_2026),
           pop = round(sum0(b$pop_lsm_ambient) - sum0(s$pop_lsm_ambient)))
  if (any(chk != 0))
    message("  NOTE: block and segment totals differ (blocks - segments): ",
            paste(names(chk), chk, sep = " = ", collapse = "; "))
  
  list(t411 = t411, t413 = t413)
}

res <- Map(city_tables, names(cities), cities)

join_all <- function(tabs, key) Reduce(function(x, y) left_join(x, y, by = key), tabs)
table_4_11 <- join_all(lapply(res, `[[`, "t411"), "Indicator")
table_4_13 <- join_all(lapply(res, `[[`, "t413"), "Flag")

print(table_4_11, n = Inf, width = Inf)
print(table_4_13, n = Inf, width = Inf)

f411 <- file.path(aug_dir, "table_4_11_buildings_final.csv")
f413 <- file.path(aug_dir, "table_4_13_flags_final.csv")
write.csv(table_4_11, f411, row.names = FALSE, fileEncoding = "UTF-8")
write.csv(table_4_13, f413, row.names = FALSE, fileEncoding = "UTF-8")
message("Written: ", f411, "\nWritten: ", f413)