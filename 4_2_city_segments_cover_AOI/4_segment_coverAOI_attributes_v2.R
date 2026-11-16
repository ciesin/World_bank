# =============================================================================
# 4_segment_coverAOI_attributes.R
# Populate augmented (full-AOI) BLOCKS and SEGMENTS with building, population,
# WSF Tracker, new-building and complex-morphology attributes
#
#   Step 1  Read units: blocks_cln2 and segments_cln2 (ID, area, perimeter)
#   Step 2  Building tables (Overture, Integrated): each building is assigned
#           to blocks ONCE: block of its point-on-surface; the block it lies
#           in (touches one block and does not cross the AOI edge); and, for
#           buildings crossing block edges or the AOI edge, its clipped
#           pieces per block. Geometry operations run on blocks only.
#   Step 3  Building statistics for blocks and segments from these tables;
#           segments via the block -> segment lookup, no further geometry:
#             count, max         : point-on-surface in the unit
#             total area         : contained footprints + clipped pieces
#             min/med/avg/sd     : fully contained buildings; at segment level
#                                  also buildings whose pieces all fall in
#                                  blocks of one segment (and not on AOI edge)
#   Step 4  WSF Tracker pixel counts per block (cell centre); segments = sum
#           of their blocks
#   Step 5  Building:WSF ratios transferred from OLDseg (Matt's segments) by
#           overlay onto SEGMENTS (they rest on 2023 footprints not available
#           here); blocks inherit their segment's ratios, so block new-building
#           estimates sum to the segment's
#   Step 6  Assemble; new buildings = new WSF pixels x ratio; complexity flags
#           and NN summaries (Integrated buildings); write both layers
#   Population (pop_lsm_ambient, LandScan Mosaic) is summed per block in
#   2_segment_coverAOI.R and carried in blocks_cln2 / segments_cln2.
#   Segments are dissolved blocks, so block results sum exactly to segments
#   except in interior rings filled during the dissolve (no blocks; tiny).
#
# INCREMENTAL UPDATE
#   Blocks and segments are matched to the previous run by geometry (area,
#   perimeter, centroid rounded to `match_digits`). Only buildings touching
#   new or changed blocks are reassigned; WSF counts run only for changed
#   blocks and the OLDseg overlay only for changed segments. All statistics
#   are then re-aggregated from the tables (seconds). Caches:
#   <aug_dir>/_cache/<city>_cln4_*.rds; delete them or set
#   `force_full <- TRUE` to recompute everything. Building layers are cached
#   as <city>_cln4_bldg_ovr.rds / _bldg_int.rds.
#
# INPUT   <city>_blocks_segments_metrics_coverAOI.gpkg, layers "blocks_cln2"
#         and "segments_cln2" (outputs of 2_segment_coverAOI.R); SPARC AOI
# OUTPUT  <city>_blocks_segments_complete_in_AOI_cln4.gpkg, layers
#         "blocks_cln4" and "segments_cln4" (linked by segment_id)
# =============================================================================

rm(list = ls())

library(sf)
library(dplyr)
library(terra)
terra_tmp <- "C:/temp/terra"            # any local folder with a few GB free
dir.create(terra_tmp, recursive = TRUE, showWarnings = FALSE)
terraOptions(tempdir = terra_tmp, memfrac = 0.7)
sf_use_s2(FALSE)   # planar geometry; units are in projected UTM

# ---- Paths ------------------------------------------------------------------
srv     <- "//DATASERVER1/ScienceData$/dthomson/Documents/zzz_WORK"
dlv     <- file.path(srv, "p_WB_LandAtlas/deliverables")
aug_dir <- file.path(dlv, "Data - Revising block segment attributes")
old_dir <- file.path(aug_dir, "matt segments")              # OLDseg (from Matt)
ovr_dir <- file.path(srv, "Data/WorldBank/Overture")
int_dir <- file.path(srv, "Data/WorldBank/Integrated_Overture_3DGloBFP/__integrated_cleaned_six_cities")
wsf_dir <- file.path(srv, "Data/WorldBank/WSFtracker_202601")
aoi_dir <- file.path(srv, "p_WB_LandAtlas/experiment_urban_extents")
cache_dir <- file.path(aug_dir, "_cache")
dir.create(cache_dir, showWarnings = FALSE)

cfg <- list(
  juba = list(
    aug = file.path(aug_dir, "juba_blocks_segments_metrics_coverAOI.gpkg"),
    ovr = file.path(ovr_dir, "juba_overture_buildings.gdb"),
    int = file.path(int_dir, "bldgs_intgr_cln_juba.gpkg"),
    wsf = file.path(wsf_dir, "Juba_WSFtracker_20160701-20260101.tif"),
    old = file.path(old_dir, "juba_segments_complete_in_AOI.gpkg"),
    aoi = file.path(aoi_dir, "SPARC_AOI_Juba.geojson")),
  kigali = list(
    aug = file.path(aug_dir, "kigali_blocks_segments_metrics_coverAOI.gpkg"),
    ovr = file.path(ovr_dir, "kigali_overture_buildings.gdb/kigali_overture_buildings.gdb"),
    int = file.path(int_dir, "bldgs_intgr_cln_kigali.gpkg"),
    wsf = file.path(wsf_dir, "Kigali_WSFtracker_20160701-20260101.tif"),
    old = file.path(old_dir, "kigali_segments_complete_in_AOI.gpkg"),
    aoi = file.path(aoi_dir, "SPARC_AOI_Kigali.geojson")),
  johannesburg = list(
    aug = file.path(aug_dir, "johannesburg_blocks_segments_metrics_coverAOI.gpkg"),
    ovr = file.path(ovr_dir, "jburg_overture_buildings.gdb/jburg_overture_buildings.gdb"),
    int = file.path(int_dir, "bldgs_intgr_cln_johannesburg.gpkg"),
    wsf = file.path(wsf_dir, "Johannesburg_WSFtracker_20160701-20260101.tif"),
    old = file.path(old_dir, "johannesburg_segments_complete_in_AOI.gpkg"),
    aoi = file.path(aoi_dir, "SPARC_AOI_CoJ.geojson"))
)

seg_layer     <- "segments_cln2"      # input layers (outputs of 2_segment)
blk_layer     <- "blocks_cln2"
out_seg_layer <- "segments_cln4"      # output layers
out_blk_layer <- "blocks_cln4"
zone_field    <- "zones_5_ID"         # block zone attribute
cities    <- "juba"  #names(cfg)                 # or e.g. "juba" to run one city

force_full      <- FALSE                # TRUE = recompute everything (ignore the cache)
cache_buildings <- TRUE                 # cache the cleaned building layers as .rds
match_digits    <- 1                    # rounding (m, m2) for geometry matching

# WSF Tracker coding: value = 6-month step in which a cell is first classified
# impervious; 1 = Jul 2016 ... 15 = Jul 2023, 19 = Jul 2025, 20 = Jan 2026.
wsf_steps <- list(
  wsf_pixel_ct_07_2023    = 1:15,    # impervious by Jul 2023
  wsf_pixel_ct_01_2026    = 1:20,    # impervious by Jan 2026
  new_wsf_pixel_last_1yr  = 19:20,   # newly impervious Jul 2025 - Jan 2026
  new_wsf_pixel_last_3yrs = 15:20    # newly impervious Jul 2023 - Jan 2026
)

# ---- Helpers ----------------------------------------------------------------
cache_file <- function(city, step) file.path(cache_dir, paste0(city, "_cln4_", step, ".rds"))
read_cache <- function(city, step) {
  f <- cache_file(city, step)
  if (!force_full && file.exists(f)) readRDS(f) else NULL
}

# Write to a temporary file, then replace, so an interrupted run keeps the old cache
save_cache <- function(x, city, step) {
  f <- cache_file(city, step); tmp <- paste0(f, ".tmp")
  saveRDS(x, tmp)
  file.rename(tmp, f) || { file.copy(tmp, f, overwrite = TRUE); file.remove(tmp) }
}

timed <- function(label, expr) {
  t0 <- Sys.time(); x <- expr
  message(sprintf("  [done]  %s (%.1f min)", label, as.numeric(difftime(Sys.time(), t0, units = "mins"))))
  x
}

# Internal unit columns, shared by blocks and segments: seg_row = row number
# of the unit; segment_area_m2 / segment_perim_m = its area and perimeter
# (renamed block_area_m2 / block_perim_m on output)
prep_units <- function(x) {
  g <- st_geometry(x)
  x$seg_row <- seq_len(nrow(x))
  x$segment_area_m2 <- as.numeric(st_area(g))
  x$segment_perim_m <- as.numeric(st_length(st_boundary(g)))
  x
}

# Geometry key: area, perimeter, centroid (rounded)
geom_key <- function(u) {
  xy <- st_coordinates(suppressWarnings(st_centroid(st_geometry(u))))
  r  <- function(v) formatC(round(v, match_digits), format = "f", digits = match_digits)
  paste(r(u$segment_area_m2), r(u$segment_perim_m), r(xy[, 1]), r(xy[, 2]), sep = "_")
}

# Match units to the previous run. Returns map: names = previous row, values = new row
match_units <- function(city, lvl, units) {
  pk  <- read_cache(city, paste0(lvl, "_key"))
  key <- geom_key(units)
  map <- integer(0)
  if (!is.null(pk) && pk$crs == st_crs(units)) {
    m <- match(key, pk$key)
    m[duplicated(m) & !is.na(m)] <- NA       # guard against duplicate keys
    hit <- which(!is.na(m))
    map <- setNames(units$seg_row[hit], pk$seg_row[m[hit]])
  }
  message(sprintf("  [%s] units %d | unchanged %d | new or changed %d",
                  lvl, nrow(units), length(map), nrow(units) - length(map)))
  list(key = key, map = map)
}

# Previous row ids -> new row ids (NA if the unit changed)
remap <- function(v, map) unname(map[match(v, as.integer(names(map)))])

# Per-unit table from the cache, remapped to new rows (matched units only)
cached_rows <- function(city, step, map) {
  x <- read_cache(city, step)
  if (is.null(x) || length(map) == 0) return(NULL)
  x$seg_row <- remap(x$seg_row, map)
  x[!is.na(x$seg_row), , drop = FALSE]
}

# Cleaned building layer, cached as .rds
get_buildings <- function(city, tag, path, crs, keep = character(0)) {
  f <- cache_file(city, paste0("bldg_", tag))
  if (cache_buildings && file.exists(f)) {
    b <- readRDS(f)
    if (st_crs(b) == crs && all(keep %in% names(b))) { message("    [cache] buildings ", tag); return(b) }
  }
  b <- read_polys(path, crs, keep = keep)
  if (cache_buildings) saveRDS(b, f)
  b
}

# Step 2 (geometry): assign buildings `cand` (row ids in b) to blocks
#   pt_blk  : block containing the point-on-surface
#   hit_blk : the only block the footprint touches, if it does not cross the
#             AOI edge (= footprint within that block; replaces st_within)
#   pieces  : clipped pieces per block for all other touching footprints
assign_buildings <- function(b, cand, blk, aoi_line, keep) {
  g   <- st_geometry(b)[cand]
  pts <- st_point_on_surface(g)
  xy  <- st_coordinates(pts)
  first_hit <- function(l) vapply(l, function(v) if (length(v)) v[1] else NA_integer_, 1L)
  pt_blk <- blk$seg_row[first_hit(st_intersects(pts, blk))]
  hits <- st_intersects(g, blk)
  nh   <- lengths(hits)
  edge <- lengths(st_intersects(g, aoi_line)) > 0
  one  <- nh == 1 & !edge
  hit_blk <- rep(NA_integer_, length(cand))
  hit_blk[one] <- blk$seg_row[vapply(hits[one], `[`, 1L, 1)]
  bt <- data.frame(b_id = cand, b_area = as.numeric(st_area(g)), px = xy[, 1], py = xy[, 2],
                   pt_blk = pt_blk, hit_blk = hit_blk, edge = edge)
  if (length(keep)) bt <- cbind(bt, st_drop_geometry(b)[cand, keep, drop = FALSE])
  
  cr <- which(nh > 0 & !one)
  pc <- data.frame(b_id = integer(0), blk = integer(0), a = numeric(0))
  if (length(cr)) {
    x <- st_sf(b_id = cand[cr], geometry = g[cr])
    y <- blk[, "seg_row"]
    st_agr(x) <- "constant"; st_agr(y) <- "constant"
    pcs <- suppressWarnings(st_intersection(x, y))
    pc  <- data.frame(b_id = pcs$b_id, blk = pcs$seg_row, a = as.numeric(st_area(pcs)))
    pc  <- pc[pc$a > 1e-4, ]                      # drop touch-only contacts (lines, points)
  }
  message(sprintf("    %d buildings assigned: %d within one block, %d clipped",
                  sum(nh > 0), sum(one), length(cr)))
  list(bt = bt[nh > 0, , drop = FALSE], pc = pc)
}

# Building table for one layer, updated incrementally. Only buildings that
# touch new or changed blocks (or referenced blocks that changed) are
# reassigned; the rest keep their records with block ids remapped.
building_table <- function(city, tag, path, crs, keep, blk, blk_map, aoi_line) {
  old <- read_cache(city, paste0("bt_", tag))
  changed <- setdiff(blk$seg_row, blk_map)
  if (!is.null(old)) {
    rb <- function(v) ifelse(is.na(v), NA_integer_, remap(v, blk_map))
    lost <- function(v0, v1) !is.na(v0) & is.na(v1)
    bt <- old$bt; pc <- old$pc
    p1 <- rb(bt$pt_blk); h1 <- rb(bt$hit_blk); k1 <- rb(pc$blk)
    bad <- unique(c(bt$b_id[lost(bt$pt_blk, p1) | lost(bt$hit_blk, h1)], pc$b_id[is.na(k1)]))
    bt$pt_blk <- p1; bt$hit_blk <- h1; pc$blk <- k1
    if (length(changed) == 0 && length(bad) == 0) {
      message("    [cache] building table ", tag)
      return(list(n_b = old$n_b, bt = bt, pc = pc))
    }
  }
  b <- get_buildings(city, tag, path, crs, keep)
  if (!is.null(old) && old$n_b != nrow(b)) {
    message("    building layer changed; reassigning all ", tag, " buildings")
    old <- NULL
  }
  if (is.null(old)) {
    cand <- which(lengths(st_intersects(b, blk)) > 0)
    bt <- NULL; pc <- NULL
  } else {
    near <- which(lengths(st_intersects(b, blk[blk$seg_row %in% changed, ])) > 0)
    cand <- sort(unique(c(near, bad)))
    bt <- bt[!bt$b_id %in% cand, ]; pc <- pc[!pc$b_id %in% cand, ]
  }
  new <- assign_buildings(b, cand, blk, aoi_line, keep)
  list(n_b = nrow(b), bt = bind_rows(bt, new$bt), pc = bind_rows(pc, new$pc))
}

# Step 3: building statistics per unit from a building table
#   u_of_blk : unit row of each block row (identity for blocks)
#   units    : data frame with seg_row and segment_area_m2
unit_stats <- function(tb, u_of_blk, units, prefix, sfx) {
  bt <- tb$bt; pc <- tb$pc
  pt_u   <- u_of_blk[bt$pt_blk]
  cont_u <- u_of_blk[bt$hit_blk]
  if (nrow(pc)) {                                  # pieces all in one unit, not on AOI edge
    pu <- data.frame(b_id = pc$b_id, u = u_of_blk[pc$blk]) |>
      group_by(b_id) |> summarise(n = n_distinct(u), u1 = first(u), .groups = "drop")
    pu <- pu[pu$n == 1, ]
    i  <- match(pu$b_id, bt$b_id)
    ok <- !is.na(i) & !bt$edge[i]
    cont_u[i[ok]] <- pu$u1[ok]
  }
  contained <- !is.na(cont_u)
  part <- pc[!pc$b_id %in% bt$b_id[contained], ]
  
  by_pt <- data.frame(seg_row = pt_u, b = bt$b_area) |> filter(!is.na(seg_row)) |>
    group_by(seg_row) |> summarise(ct = n(), mx = max(b), .groups = "drop")
  by_in <- data.frame(seg_row = cont_u[contained], b = bt$b_area[contained]) |>
    group_by(seg_row) |>
    summarise(mn = min(b), md = median(b), av = mean(b),
              sdv = if (n() > 1) sd(b) else 0, tot_in = sum(b), .groups = "drop")
  by_cl <- data.frame(seg_row = u_of_blk[part$blk], a = part$a) |>
    group_by(seg_row) |> summarise(tot_cl = sum(a), .groups = "drop")
  
  o <- data.frame(seg_row = units$seg_row, seg_area = units$segment_area_m2) |>
    left_join(by_pt, by = "seg_row") |> left_join(by_in, by = "seg_row") |>
    left_join(by_cl, by = "seg_row") |>
    mutate(ct = coalesce(ct, 0L), tot = coalesce(tot_in, 0) + coalesce(tot_cl, 0))
  
  nm <- function(x) paste0(prefix, "_", x, "_", sfx)
  res <- data.frame(seg_row = o$seg_row)
  res[[nm("bldg_ct")]]        <- o$ct
  res[[nm("tot_bldg_area")]]  <- o$tot
  res[[nm("min_bldg_area")]]  <- o$mn
  res[[nm("max_bldg_area")]]  <- o$mx
  res[[nm("med_bldg_area")]]  <- o$md
  res[[nm("avg_bldg_area")]]  <- o$av
  res[[nm("sd_bldg_area")]]   <- o$sdv
  res[[nm("bldg_area_dens")]] <- o$tot / o$seg_area
  res[[nm("bldg_ct_dens")]]   <- o$ct / (o$seg_area / 1e4)
  res
}

read_polys <- function(path, crs, layer = NULL, keep = character(0)) {
  if (is.null(layer)) layer <- st_layers(path)$name[1]
  message("    reading ", basename(path), " [", layer, "]")
  x <- st_read(path, layer = layer, quiet = TRUE)
  miss <- setdiff(keep, names(x))
  if (length(miss)) stop("Missing fields in ", basename(path), ": ", paste(miss, collapse = ", "))
  x <- st_sf(st_drop_geometry(x)[keep], geometry = st_geometry(x))
  x <- x[!st_is_empty(x), ]
  x <- st_transform(x, crs)
  bad <- !st_is_valid(x)                      # repair only invalid features
  if (any(bad)) x[bad, ] <- st_make_valid(x[bad, ])
  st_collection_extract(x, "POLYGON", warn = FALSE)
}

# Complex morphology (Integrated buildings; nn_* = buildings within 50 m).
# A building is flagged if either rule holds:
#   rule A  footprint > 500 m2 AND >= 100 neighbours      (large building in a crowded fabric)
#   rule B  > 12 neighbours AND mean neighbour area < 35 m2 (dense cluster of very small buildings)
# Buildings are assigned to segments by point-on-surface, as for intgr_bldg_ct.
# A block or segment is flagged (cmplx_flag = 1) if it holds >= seg_min_n
# flagged buildings and their share is >= seg_min_share.
cmplx <- list(a_area = 500, a_nn = 100, b_nn = 12, b_size = 35,
              seg_min_n = 3, seg_min_share = 0)   # unit flag thresholds (blocks and segments)

nn_fields <- c("nn_count_50m", "nn_size_mean", "nn_size_min", "nn_size_max", "nn_edge_dist_mean_m")

# Segment summaries of the building-level 50 m NN metrics (Integrated buildings,
# point-on-surface assignment). Medians resist the skew of per-building values;
# p90 and CV keep the tail and the mix, so alternative flag rules can be tested
# at segment level without re-reading buildings.
nn_aggregates <- function(ci, seg) {
  q90 <- function(x) if (all(is.na(x))) NA_real_ else unname(quantile(x, 0.9, na.rm = TRUE))
  cv  <- function(x) { m <- mean(x, na.rm = TRUE); if (sum(!is.na(x)) > 1 && m > 0) sd(x, na.rm = TRUE) / m else NA_real_ }
  med <- function(x) if (all(is.na(x))) NA_real_ else median(x, na.rm = TRUE)
  ci |> rename(seg_row = seg_pt) |> filter(!is.na(seg_row)) |>
    group_by(seg_row) |>
    summarise(nn_count_med       = med(nn_count_50m),
              nn_count_p90       = q90(nn_count_50m),
              nn_size_mean_med   = med(nn_size_mean),
              nn_size_mean_cv    = cv(nn_size_mean),
              nn_size_min_med    = med(nn_size_min),
              nn_size_max_med    = med(nn_size_max),
              nn_edge_dist_med_m = med(nn_edge_dist_mean_m),
              bldg_gt500_ct      = sum(b_area > 500),
              .groups = "drop") |>
    right_join(data.frame(seg_row = seg$seg_row), by = "seg_row") |>
    mutate(bldg_gt500_ct = coalesce(bldg_gt500_ct, 0L)) |>
    arrange(seg_row)
}

complexity_stats <- function(seg_pt, b_area, nn_count, nn_size_mean, seg) {
  rule_a <- !is.na(nn_count) & b_area > cmplx$a_area & nn_count >= cmplx$a_nn
  rule_b <- !is.na(nn_count) & !is.na(nn_size_mean) &
    nn_count > cmplx$b_nn & nn_size_mean < cmplx$b_size
  d <- data.frame(seg_row = seg_pt, a = rule_a, b = rule_b) |> filter(!is.na(seg_row)) |>
    group_by(seg_row) |>
    summarise(n = n(), na = sum(a), nb = sum(b), nab = sum(a | b), .groups = "drop")
  data.frame(seg_row = seg$seg_row) |> left_join(d, by = "seg_row") |>
    transmute(seg_row,
              cmplx_bldg_ct_large = coalesce(na, 0L),
              cmplx_bldg_ct_small = coalesce(nb, 0L),
              cmplx_bldg_ct       = coalesce(nab, 0L),
              cmplx_bldg_share    = ifelse(coalesce(n, 0L) > 0, nab / n, NA_real_),
              cmplx_flag = as.integer(cmplx_bldg_ct >= cmplx$seg_min_n &
                                        coalesce(cmplx_bldg_share, 0) >= cmplx$seg_min_share))
}

# WSF pixel counts: a cell belongs to the segment containing its centre
# (terra::rasterize default), matching "pixels with a centroid in the segment".
# One rasterize + one integer tabulation; no per-segment extraction.
wsf_counts <- function(wsf_path, seg) {
  r <- rast(wsf_path)
  seg$.i <- seq_len(nrow(seg))                # local index (works on a subset of seg)
  v <- vect(st_transform(seg[, ".i"], crs(r)))
  r <- crop(r, v, snap = "out")
  z <- rasterize(v, r, field = ".i")
  zv <- values(z, mat = FALSE)
  rv <- values(r, mat = FALSE)
  ok <- !is.na(zv) & !is.na(rv) & rv >= 1 & rv <= 20
  ns <- nrow(seg)
  tab <- matrix(tabulate((zv[ok] - 1L) * 20L + rv[ok], nbins = ns * 20L),
                nrow = ns, ncol = 20, byrow = TRUE)     # rows = seg_row, cols = step 1..20
  res <- data.frame(seg_row = seg$seg_row)
  for (nm in names(wsf_steps)) res[[nm]] <- rowSums(tab[, wsf_steps[[nm]], drop = FALSE])
  res
}

# Fields transferred from OLDseg: building:WSF ratios only
extensive <- character(0)
intensive <- c("ovr_bldg_wsf_ratio", "intgr_bldg_wsf_ratio")

# Weight each OLDseg-by-segment piece by its share of the OLDseg segment's
# Overture buildings (points); by land area where the OLDseg segment has none.
transfer_oldseg <- function(old, seg, pts) {
  old$old_row  <- seq_len(nrow(old))
  old$old_area <- as.numeric(st_area(old))
  old$old_n    <- lengths(st_intersects(old, pts))
  pcs <- suppressWarnings(st_intersection(
    seg[, "seg_row"], old[, c("old_row", "old_area", "old_n", extensive, intensive)]))
  pcs$p_area <- as.numeric(st_area(pcs))
  pcs$p_n    <- lengths(st_intersects(pcs, pts))
  p <- st_drop_geometry(pcs)
  p$w <- ifelse(p$old_n > 0, p$p_n / p$old_n, p$p_area / p$old_area)
  
  ext <- p |> group_by(seg_row) |>
    summarise(across(all_of(extensive), ~ sum(.x * w, na.rm = TRUE)),
              cover = sum(p_area), .groups = "drop")
  int <- p |> group_by(seg_row) |>
    mutate(iw = if (sum(p_n) > 0) p_n else p_area) |>
    summarise(across(all_of(intensive), ~ weighted.mean(.x, iw, na.rm = TRUE)), .groups = "drop")
  
  data.frame(seg_row = seg$seg_row, seg_area = seg$segment_area_m2) |>
    left_join(ext, by = "seg_row") |> left_join(int, by = "seg_row") |>
    mutate(oldseg_cover_share = coalesce(cover, 0) / seg_area) |>
    select(-cover, -seg_area)
}

# ---- Output field order -----------------------------------------------------
bstat <- c("bldg_ct", "tot_bldg_area", "min_bldg_area", "max_bldg_area", "med_bldg_area",
           "avg_bldg_area", "sd_bldg_area", "bldg_area_dens", "bldg_ct_dens")
common_fields <- c(
  paste0("ovr_", bstat, "_06_2026"), paste0("intgr_", bstat, "_06_2026"),
  "pop_lsm_ambient", "pop_per_ha",
  names(wsf_steps),
  "ovr_bldg_wsf_ratio", "ovr_new_bldgs_1yr", "ovr_new_bldgs_3yr",
  "intgr_bldg_wsf_ratio", "intgr_new_bldgs_last_1yr", "intgr_new_bldgs_last_3yr",
  "cmplx_bldg_ct_large", "cmplx_bldg_ct_small", "cmplx_bldg_ct",
  "cmplx_bldg_share", "cmplx_flag",
  "nn_count_med", "nn_count_p90", "nn_size_mean_med", "nn_size_mean_cv",
  "nn_size_min_med", "nn_size_max_med", "nn_edge_dist_med_m",
  "bldg_gt500_ct")
seg_order <- c("segment_id", "segment_area_m2", "segment_perim_m", common_fields,
               "seg_source", "seg_fid", "n_blocks", "zone_id",
               "polsby_popper", "compact_j", "n_holes", "oldseg_cover_share")
blk_order <- c("block_id", "segment_id", "source", "zone_id",
               "block_area_m2", "block_perim_m", common_fields)

summary_msg <- function(lvl, x) {
  message(sprintf(paste0("  [%s] units %d | ovr bldgs %d | intgr bldgs %d | pop %s | WSF Jan 2026 px %d | ",
                         "new px 1yr %d, 3yr %d | complex: %d flagged"),
                  lvl, nrow(x), sum(x$ovr_bldg_ct_06_2026), sum(x$intgr_bldg_ct_06_2026),
                  format(round(sum(x$pop_lsm_ambient, na.rm = TRUE)), big.mark = ","),
                  sum(x$wsf_pixel_ct_01_2026), sum(x$new_wsf_pixel_last_1yr),
                  sum(x$new_wsf_pixel_last_3yrs), sum(x$cmplx_flag)))
}

# ---- Main -------------------------------------------------------------------
for (city in cities) {
  p <- cfg[[city]]
  message("== ", city)
  
  # Step 1: units (always re-read)
  seg <- st_read(p$aug, layer = seg_layer, quiet = TRUE) |> st_make_valid()
  seg$segment_id <- seg$seg_part_ID
  seg <- prep_units(seg) |>
    select(seg_row, segment_id, segment_area_m2, segment_perim_m,
           seg_source, seg_fid, n_blocks, zone_id, pop_lsm_ambient, pop_per_ha,
           polsby_popper, compact_j, n_holes)
  crs <- st_crs(seg)
  
  b0  <- st_read(p$aug, layer = blk_layer, quiet = TRUE) |> st_make_valid()
  blk <- st_sf(data.frame(block_id        = b0$block_id_coverAOI,
                          segment_id      = b0$seg_part_ID,
                          source          = b0$source,
                          zone_id         = if (zone_field %in% names(b0)) b0[[zone_field]] else NA,
                          pop_lsm_ambient = b0$pop_lsm_ambient),
               geometry = st_geometry(b0)) |> prep_units()
  blk$pop_per_ha <- blk$pop_lsm_ambient / (blk$segment_area_m2 / 1e4)
  rm(b0)
  if (st_crs(blk) != crs) stop("Blocks and segments have different CRS")
  seg_of_blk <- match(blk$segment_id, seg$segment_id)            # segment row of each block
  if (anyNA(seg_of_blk)) stop("Blocks with no matching segment_id")
  
  mb <- match_units(city, "blk", blk)
  ms <- match_units(city, "seg", seg)
  aoi_line <- st_read(p$aoi, quiet = TRUE) |> st_transform(crs) |> st_make_valid() |>
    st_union() |> st_boundary()
  
  # Step 2: building tables (geometry on blocks only)
  bt_ovr <- timed("2_overture", building_table(city, "ovr", p$ovr, crs, character(0),
                                               blk, mb$map, aoi_line))
  bt_int <- timed("2_integrated", building_table(city, "int", p$int, crs, nn_fields,
                                                 blk, mb$map, aoi_line))
  gc()
  
  # Step 4: WSF per block (changed blocks only); segments = sum of blocks
  wsf_b <- cached_rows(city, "blk_wsf", mb$map)
  todo  <- setdiff(blk$seg_row, wsf_b$seg_row)
  if (length(todo))
    wsf_b <- bind_rows(wsf_b, timed("4_wsf", wsf_counts(p$wsf, blk[blk$seg_row %in% todo, ])))
  wsf_b <- arrange(wsf_b, seg_row)
  stopifnot(identical(wsf_b$seg_row, blk$seg_row))
  wsf_s <- data.frame(seg_row = seg$seg_row,
                      rowsum(as.matrix(wsf_b[names(wsf_steps)]), seg_of_blk, reorder = TRUE)[
                        as.character(seg$seg_row), , drop = FALSE], row.names = NULL)
  
  # Step 5: OLDseg ratios onto segments (changed segments only); points from the Overture table
  trn <- cached_rows(city, "seg_trn", ms$map)
  todo <- setdiff(seg$seg_row, trn$seg_row)
  if (length(todo)) {
    pts <- st_as_sf(bt_ovr$bt[c("px", "py")], coords = c("px", "py"), crs = crs)
    trn <- bind_rows(trn, timed("5_oldseg_transfer", {
      old <- st_read(p$old, quiet = TRUE) |> st_transform(crs) |> st_make_valid()
      transfer_oldseg(old, seg[seg$seg_row %in% todo, ], pts)
    }))
    rm(pts)
  }
  trn <- arrange(trn, seg_row)
  stopifnot(identical(trn$seg_row, seg$seg_row))
  
  # Steps 3 and 6: statistics from the tables, per level
  lv <- list(blk = list(u = blk, u_of_blk = blk$seg_row, wsf = wsf_b,
                        ratios = data.frame(seg_row = blk$seg_row,
                                            trn[seg_of_blk, c("ovr_bldg_wsf_ratio", "intgr_bldg_wsf_ratio")])),
             seg = list(u = seg, u_of_blk = seg_of_blk, wsf = wsf_s, ratios = trn))
  fin <- list()
  for (l in names(lv)) {
    x  <- lv[[l]]
    ud <- st_drop_geometry(x$u)[c("seg_row", "segment_area_m2")]
    ci <- data.frame(seg_pt = x$u_of_blk[bt_int$bt$pt_blk], b_area = bt_int$bt$b_area,
                     bt_int$bt[nn_fields])
    ci <- ci[!is.na(ci$seg_pt), ]
    fin[[l]] <- x$u |>
      left_join(unit_stats(bt_ovr, x$u_of_blk, ud, "ovr",   "06_2026"), by = "seg_row") |>
      left_join(unit_stats(bt_int, x$u_of_blk, ud, "intgr", "06_2026"), by = "seg_row") |>
      left_join(x$wsf, by = "seg_row") |>
      left_join(x$ratios, by = "seg_row") |>
      left_join(with(ci, complexity_stats(seg_pt, b_area, nn_count_50m, nn_size_mean, ud)), by = "seg_row") |>
      left_join(nn_aggregates(ci, ud), by = "seg_row") |>
      mutate(ovr_new_bldgs_1yr        = new_wsf_pixel_last_1yr  * ovr_bldg_wsf_ratio,
             ovr_new_bldgs_3yr        = new_wsf_pixel_last_3yrs * ovr_bldg_wsf_ratio,
             intgr_new_bldgs_last_1yr = new_wsf_pixel_last_1yr  * intgr_bldg_wsf_ratio,
             intgr_new_bldgs_last_3yr = new_wsf_pixel_last_3yrs * intgr_bldg_wsf_ratio)
  }
  fin$seg <- select(fin$seg, all_of(seg_order))
  fin$blk <- fin$blk |>
    rename(block_area_m2 = segment_area_m2, block_perim_m = segment_perim_m) |>
    select(all_of(blk_order))
  summary_msg("blk", fin$blk)
  summary_msg("seg", fin$seg)
  message(sprintf("  segments < 95%% OLDseg cover: %d", sum(fin$seg$oldseg_cover_share < 0.95)))
  
  # Write both layers to one GeoPackage
  out <- file.path(aug_dir, paste0(city, "_blocks_segments_complete_in_AOI_cln4.gpkg"))
  if (file.exists(out)) file.remove(out)
  st_write(fin$blk, out, layer = out_blk_layer, quiet = TRUE)
  st_write(fin$seg, out, layer = out_seg_layer, quiet = TRUE)
  message("  wrote ", out, " [", out_blk_layer, ", ", out_seg_layer, "]")
  
  # Replace the cache with this run's results (basis for the next comparison)
  save_cache(list(key = mb$key, seg_row = blk$seg_row, crs = crs), city, "blk_key")
  save_cache(list(key = ms$key, seg_row = seg$seg_row, crs = crs), city, "seg_key")
  save_cache(bt_ovr, city, "bt_ovr")
  save_cache(bt_int, city, "bt_int")
  save_cache(wsf_b,  city, "blk_wsf")
  save_cache(trn,    city, "seg_trn")
  message("  cache updated")
  rm(seg, blk, bt_ovr, bt_int, wsf_b, wsf_s, trn, lv, fin); gc()
}