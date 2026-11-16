# =============================================================================
# Table 4.9. Full-coverage blocks and segments (3 Pilot Cities)
# Simple summary statistics from the attribute tables; no geometry operations.
#
# Original:  Data - Revising block segment attributes/alex blocks segments/
#            <city>_blocks_segments_metrics.gpkg
#            layers "blocks", "segments"
# Augmented: Data - Revising block segment attributes/
#            <city>_blocks_segments_metrics_coverAOI.gpkg
#            layers "blocks_cln2" (GRID3 blocks + void units), "segments_cln2"
#            (outputs of 2_segment_coverAOI.R)
# Output:    table_4_9_metrics_cln3.csv (in aug_dir)
#            Void units have a non-null hex_ids; GRID3 blocks have hex_ids = NULL.
#
# AOI area = total area of the augmented block layer (GRID3 blocks + void
# units), which tiles the full AOI.
# =============================================================================

library(sf)
library(dplyr)

rm(list = ls())

root     <- "//DATASERVER1/ScienceData$/dthomson/Documents/zzz_WORK/p_WB_LandAtlas/deliverables"
aug_dir  <- file.path(root, "Data - Revising block segment attributes")
orig_dir <- file.path(aug_dir, "alex blocks segments")      # original blocks/segments (from Alex)
cities   <- c("juba", "kigali", "johannesburg")

aug_block_layer   <- "blocks_cln2"    # cleaned layer that feeds segments_cln2
aug_segment_layer <- "segments_cln2"

read_tbl <- function(path, layer) st_drop_geometry(st_read(path, layer = layer, quiet = TRUE))

city_metrics <- function(city) {
  orig <- file.path(orig_dir, paste0(city, "_blocks_segments_metrics.gpkg"))
  aug  <- file.path(aug_dir,  paste0(city, "_blocks_segments_metrics_coverAOI.gpkg"))
  
  o_seg <- read_tbl(orig, "segments")
  a_blk <- read_tbl(aug,  aug_block_layer)
  a_seg <- read_tbl(aug,  aug_segment_layer)
  
  is_void <- !is.na(a_blk$hex_ids)
  aoi_ha  <- sum(a_blk$area_m2) / 1e4
  
  tibble(
    Units = c("GRID3 blocks within AOI (#)",
              "Void units (#)",
              "Final segments: original (#)",
              "Median segment area (ha)",
              "AOI covered (%)",
              "Final segments: augmented (#)",
              "Median segment area (ha) ",
              "AOI covered (%) ",
              "AOI area (ha) [check]"),
    !!city := c(sum(!is_void),
                sum(is_void),
                nrow(o_seg),
                median(o_seg$segment_area_m2) / 1e4,
                100 * sum(o_seg$segment_area_m2) / 1e4 / aoi_ha,
                nrow(a_seg),
                median(a_seg$area_ha),
                100 * sum(a_seg$area_ha) / aoi_ha,
                aoi_ha)
  )
}

table_4_9 <- Reduce(function(x, y) left_join(x, y, by = "Units"),
                    lapply(cities, city_metrics))

print(table_4_9, n = Inf)
out_csv <- file.path(aug_dir, "table_4_9_metrics_cln3.csv")
write.csv(table_4_9, out_csv, row.names = FALSE)
message("Written: ", out_csv)