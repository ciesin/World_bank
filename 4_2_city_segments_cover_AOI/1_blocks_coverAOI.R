# =============================================================================
# blocks_segments_metrics_coverAOI.R
# -----------------------------------------------------------------------------
# Project : World Bank LandAtlas / SPARC Urban Analytics Platform
# Author  : Dana Thomson, CIESIN, Columbia University
# Created : 2026-11-07
#
# DESCRIPTION
#   Builds a complete polygon coverage of each SPARC city AOI from GRID3
#   blocks plus generated "void" units, and computes Overture building
#   metrics for every polygon. For each city:
#     1. Tidy existing GRID3 block building attributes
#     2. Cut GRID3 blocks to the SPARC AOI; resolve donut blocks
#     3. Derive the void (AOI not covered by blocks) as one multipart feature;
#        skeletonize each part, extend skeleton ends to the part boundary and
#        split the part along those lines where applicable
#     4. Cut the void pieces with a 10 ha hexagonal grid; merge polygons
#        < 5 ha into the neighbor sharing the longest edge
#     5. Combine blocks and void units into one AOI coverage; resolve any
#        remaining donut polygons
#     6. Convert Overture building footprints to points and dissolve
#        footprints into one feature
#     7. Count building points per polygon            -> bldg_cnt
#     8. Sum dissolved building area per polygon      -> bldg_area_m2
#     9. Assign a unique polygon ID                   -> block_id_coverAOI
#
# USAGE
#   Set `run_cities` in SECTION 1 to one or more of the cities in `cities`,
#   then source the script. Cities are processed one at a time. Results are
#   checkpointed in <out_dir>/checkpoints/ after Step 3
#   (<city>_void_checkpoint.rds) and Step 5
#   (<city>_coverage_checkpoint.rds); with resume = TRUE a rerun loads the
#   latest checkpoint and continues from the following step.
#
# INPUTS (per city; see `cities` in SECTION 1)
#   blocks_file    GeoPackage, layer "blocks": GRID3 blocks with existing
#                  metrics (building_count, total_building_area, ...)
#   overture_file  File geodatabase of Overture building footprints
#   aoi_file       SPARC city AOI (GeoJSON)
#   epsg           Projected CRS (UTM, metres) used for all geometry ops
#
# OUTPUT
#   <out_dir>/<city>_blocks_segments_metrics_coverAOI.gpkg, layer "blocks"
#     block_id_coverAOI  unique polygon ID within the city coverage
#     source             "grid3_block" or "aoi_void"
#     block_fid          fid of the source GRID3 block (NA for void units)
#     void_unit_id       ID of the void unit (NA for GRID3 blocks)
#     area_m2            area of the polygon as written (after AOI cut)
#     bldg_cnt           number of Overture buildings (point on surface)
#     bldg_area_m2       dissolved Overture building footprint area (m2)
#     n_members          void-skeleton-hex polygons merged into the unit
#     hex_ids            hexagon IDs contributing to the unit
#     still_small        void unit < 5 ha with no edge neighbor to merge into
#     donut_split        1 if the polygon is a host carved by donut resolution
#     donut_merged       1 if the polygon is a donut merged with a carved piece
#     n_holes            interior rings remaining (0 unless a donut could
#                        not be split)
#     <existing GRID3 block attributes>  (NA for void units)
#   Optional QA layers (write_qa_layers = TRUE):
#   <out_dir>/<city>_coverAOI_qa.gpkg
#     aoi_void, void_skeleton, void_pieces, hex_grid, void_hex
#
# METHOD
#   Step 1  building_count == 1: min/max/median/average_building_area are
#           set to total_building_area and stdev_building_area to 0.
#           building_count == 0: min/max/median/average/stdev set to NULL.
#   Step 2  Blocks are intersected with the dissolved AOI. Blocks crossing
#           the AOI edge are clipped; blocks outside are dropped. Existing
#           GRID3 attributes (block_area_m2, block_perim_m, building_count,
#           densities, pctranks, population) describe the unclipped block;
#           area_m2 describes the clipped polygon.
#   Donuts  A donut is a polygon completely surrounded by another polygon
#           (the host), which therefore has an interior ring (hole). Each
#           hole is removed by cutting the host along two "bridges":
#           straight segments from a hole vertex to an outer-ring vertex.
#           Candidates: for each hole vertex (thinned to at most
#           `max_hole_vertices`), the `k_nearest` outer-ring vertices;
#           a candidate is valid if it lies within the host polygon (so it
#           cannot cross another hole or leave the polygon). The shortest
#           valid bridge per hole vertex is kept. The chosen pair minimizes
#           total bridge length, subject to: hole anchors separated by at
#           least `min_sep_frac` of the hole perimeter, distinct outer
#           vertices, and non-intersecting bridges. The bridges cut the host
#           into two pieces.
#           If the hole holds exactly one feature (overlap >
#           `min_overlap_m2`), that donut is merged with the smaller carved
#           piece and keeps its own attributes (donut_merged = 1); the host
#           keeps the larger piece (donut_split = 1). If the hole is empty or
#           holds several features, the host is split into two features
#           that both inherit its attributes (donut_split = 1).
#           Hosts are processed one hole at a time until no holes remain. A
#           hole with no valid bridge pair is left in place and reported in
#           n_holes. Donut resolution runs on GRID3 blocks (Step 2) and
#           again on the combined coverage (Step 5) to catch donuts created
#           by the void split and merge steps. Polygons changed in Step 5
#           are not re-merged, so a void polygon may be < 5 ha.
#   Step 3  Void = AOI minus union(blocks). A morphological opening (inward
#           then outward buffer of `sliver_m`) removes hairline gaps between
#           adjacent blocks; parts < `min_part_m2` are dropped.
#           Skeleton: approximate medial axis from the Voronoi diagram of the
#           simplified (`simplify_m`) and densified (`densify_m`) part
#           boundary, keeping Voronoi edges strictly inside the part.
#           Dangling branches < `prune_m` are removed iteratively (the
#           longest line is always retained). Each remaining dangling end is
#           extended along its final direction (over the last `look_back_m`)
#           to the part boundary. The part is split by noding and
#           polygonizing its boundary with the skeleton lines.
#           A part is left whole if it is < `min_split_m2`, its pruned
#           skeleton is empty, the split yields one piece, or the pieces'
#           total area differs from the part's by more than 0.5%.
#   Step 4  Hexagons from st_make_grid(square = FALSE), anchored on the AOI
#           bounding box. sf sizes hexagons by the edge-to-edge distance,
#           d = sqrt(2 * A / sqrt(3)); A = 10 ha gives d = 339.8 m.
#           Void pieces are intersected with the grid and exploded to single
#           polygons. Polygons < `merge_below_m2` are merged, smallest first,
#           into the neighbor sharing the longest edge (>= `min_edge_m`;
#           corner contact does not count). Merged areas and shared-edge
#           lengths are updated after each merge, so a unit still below the
#           threshold continues merging. Polygons with no edge neighbor are
#           retained and flagged still_small.
#   Step 5  Blocks and void units are combined into a single layer.
#   Step 6  Building points use st_point_on_surface() so each point falls
#           inside its own footprint. Footprints are made valid and dissolved
#           (st_union) so overlapping or duplicate footprints are not double
#           counted in area.
#   Step 7  Each point is assigned to one polygon (first match), so points
#           on shared boundaries are counted once.
#   Step 8  Area is the dissolved footprint clipped to each polygon. A
#           building crossing a polygon edge contributes area to each polygon
#           it overlaps but is counted (Step 7) in one.
#   Performance: the dissolved feature is handled as its polygon parts, and
#           only footprints touching another footprint are unioned; parts
#           wholly within one polygon contribute their full area and only
#           parts crossing polygon edges are intersected. Blocks wholly
#           inside the AOI are not intersected in Step 2. Shared-edge
#           lengths for Step 4 come from matching identical boundary
#           segments, with a geometric fallback for polygons that have no
#           matched segment.
#           Donut resolution runs only on hosts and the features inside
#           their outer rings. Results are identical to the direct
#           operations.
#   All geometry operations are planar in the city's UTM CRS.
#
# DEPENDENCIES
#   R >= 4.1; sf >= 1.0 (GEOS >= 3.8); dplyr >= 1.1
# =============================================================================

rm(list = ls())

suppressPackageStartupMessages({
  library(sf)
  library(dplyr)
})

sf_use_s2(FALSE)

# -----------------------------------------------------------------------------
# SECTION 1. Configuration
# -----------------------------------------------------------------------------

# Cities to process: any subset of cities$city
run_cities <- c("johannesburg", "juba", "kigali")

root_blocks   <- "//DATASERVER1/ScienceData$/dthomson/Documents/zzz_WORK/p_WB_LandAtlas/deliverables/Data - Revising block segment attributes"
root_overture <- "//DATASERVER1/ScienceData$/dthomson/Documents/zzz_WORK/Data/WorldBank/Overture"
root_aoi      <- "//DATASERVER1/ScienceData$/dthomson/Documents/zzz_WORK/p_WB_LandAtlas/experiment_urban_extents"
out_dir       <- root_blocks

write_qa_layers <- FALSE

# Checkpointing: results after Step 3 and Step 5 are saved per city. With
# resume = TRUE, the latest checkpoint is loaded and earlier steps are
# skipped. Set resume = FALSE (or delete the checkpoints) after changing
# the steps they cover.
checkpoint_dir <- file.path(out_dir, "checkpoints")
resume         <- TRUE

cities <- dplyr::tribble(
  ~city,          ~blocks_file,                                ~overture_file,                  ~aoi_file,                  ~epsg,
  "johannesburg", "johannesburg_blocks_segments_metrics.gpkg", "jburg_overture_buildings.gdb/jburg_overture_buildings.gdb",   "SPARC_AOI_CoJ.geojson",    32735,
  "juba",         "juba_blocks_segments_metrics.gpkg",         "juba_overture_buildings.gdb",                                 "SPARC_AOI_Juba.geojson",   32636,
  "kigali",       "kigali_blocks_segments_metrics.gpkg",       "kigali_overture_buildings.gdb/kigali_overture_buildings.gdb", "SPARC_AOI_Kigali.geojson", 32735
)

dir.create(checkpoint_dir, showWarnings = FALSE, recursive = TRUE)

void_params <- list(
  sliver_m     = 0.5,      # opening radius for hairline gaps (m)
  min_part_m2  = 100,      # minimum void part area retained (m2)
  min_split_m2 = 10000,    # minimum void part area for skeleton split (m2)
  simplify_m   = 1,        # boundary simplification tolerance (m)
  densify_m    = 5,        # boundary vertex spacing for Voronoi (m)
  prune_m      = 100,      # minimum dangling branch length retained (m)
  look_back_m  = 20        # branch length used for extension direction (m)
)

donut_params <- list(
  k_nearest         = 50,   # outer-ring vertices tested per hole vertex
  max_hole_vertices = 200,  # hole vertices tested as bridge anchors
  min_sep_frac      = 0.25, # min anchor separation (fraction of hole perimeter)
  min_overlap_m2    = 1     # min overlap for a feature to count as in a hole (m2)
)

hex_params <- list(
  hex_area_m2    = 100000, # hexagon area (10 ha)
  merge_below_m2 = 50000,  # merge polygons smaller than this (5 ha)
  edge_tol_m     = 0.01,   # tolerance for shared-edge detection (m)
  min_edge_m     = 0.1     # minimum shared-edge length for adjacency (m)
)

# -----------------------------------------------------------------------------
# SECTION 2. General helpers
# -----------------------------------------------------------------------------

log_msg <- function(...) message(format(Sys.time(), "%H:%M:%S"), "  ", ...)

# Valid, non-empty MULTIPOLYGON geometry (sf or sfc)
clean_polygons <- function(x) {
  x <- st_make_valid(x)
  x <- suppressWarnings(st_collection_extract(x, "POLYGON"))
  keep <- !st_is_empty(x)
  x <- if (inherits(x, "sf")) x[keep, ] else x[keep]
  st_cast(x, "MULTIPOLYGON")
}

# Area in m2 as numeric
area_m2 <- function(x) as.numeric(st_area(x))

# First polygon layer of a file geodatabase
read_overture <- function(gdb) {
  lyrs <- st_layers(gdb)
  poly <- lyrs$name[grepl("Polygon", sapply(lyrs$geomtype, `[`, 1))]
  if (length(poly) == 0) stop("No polygon layer found in ", gdb)
  log_msg("Overture layer: ", poly[1])
  st_read(gdb, layer = poly[1], quiet = TRUE)
}

# Step 1: tidy existing block building attributes
tidy_block_attributes <- function(blocks) {
  stat_cols <- c("min_building_area", "max_building_area",
                 "median_building_area", "average_building_area")
  blocks |>
    mutate(
      across(all_of(stat_cols),
             ~ case_when(building_count == 0 ~ NA_real_,
                         building_count == 1 ~ total_building_area,
                         TRUE                ~ .x)),
      stdev_building_area = case_when(building_count == 0 ~ NA_real_,
                                      building_count == 1 ~ 0,
                                      TRUE                ~ stdev_building_area)
    )
}

# Step 6: dissolve footprints into one feature, returned as its polygon
# parts. Only footprints that intersect another footprint are unioned;
# isolated footprints are already parts of the dissolved feature.
dissolve_footprints <- function(g) {
  iso <- lengths(st_intersects(g)) == 1
  parts <- g[iso]
  if (any(!iso))
    parts <- c(parts, st_cast(st_union(g[!iso]), "POLYGON"))
  log_msg("Footprints: ", length(g), " (", sum(!iso), " overlapping or touching)",
          " -> dissolved parts: ", length(parts))
  st_sf(geometry = st_cast(parts, "MULTIPOLYGON"))
}

# Steps 7-8: building count and dissolved building area per polygon
add_bldg_metrics <- function(units, id, bldg_pts, bldg_parts) {
  cnt <- st_join(bldg_pts, units[id], join = st_intersects, left = FALSE) |>
    st_drop_geometry() |>
    distinct(bldg_id, .keep_all = TRUE) |>
    count(across(all_of(id)), name = "bldg_cnt")
  
  # Footprints wholly within one polygon contribute their full area; only
  # footprints crossing polygon edges are intersected.
  pa     <- area_m2(bldg_parts)
  within <- st_within(bldg_parts, units)
  inside <- lengths(within) == 1
  area_in <- data.frame(row = unlist(within[inside]), a = pa[inside])
  
  cross <- bldg_parts[!inside, ]
  area_x <- data.frame(row = integer(), a = numeric())
  if (nrow(cross) > 0) {
    u <- units[id]
    u$row <- seq_len(nrow(u))
    st_agr(u) <- "constant"
    st_agr(cross) <- "constant"
    inter <- suppressWarnings(st_intersection(u["row"], cross))
    if (nrow(inter) > 0)
      area_x <- data.frame(row = inter$row, a = area_m2(inter))
  }
  
  area <- bind_rows(area_in, area_x) |>
    group_by(row) |>
    summarise(bldg_area_m2 = sum(a), .groups = "drop")
  area[[id]] <- units[[id]][area$row]
  area$row   <- NULL
  
  units |>
    left_join(cnt,  by = id) |>
    left_join(area, by = id) |>
    mutate(bldg_cnt     = coalesce(bldg_cnt, 0L),
           bldg_area_m2 = coalesce(bldg_area_m2, 0))
}

# -----------------------------------------------------------------------------
# SECTION 3. Skeleton and split helpers (Step 3)
# -----------------------------------------------------------------------------

pt_key <- function(m) paste(round(m[, 1], 3), round(m[, 2], 3))

# Start/end vertex degree of each LINESTRING
line_ends <- function(lines) {
  xy  <- st_coordinates(lines)
  idx <- split(seq_len(nrow(xy)), xy[, "L1"])
  s   <- xy[vapply(idx, function(i) i[1], 1L), 1:2, drop = FALSE]
  e   <- xy[vapply(idx, function(i) i[length(i)], 1L), 1:2, drop = FALSE]
  ks  <- pt_key(s)
  ke  <- pt_key(e)
  deg <- table(c(ks, ke))
  list(xy = xy, idx = idx,
       deg_s = as.integer(deg[ks]), deg_e = as.integer(deg[ke]))
}

# Union lines and merge through degree-2 nodes
merge_lines <- function(lines) {
  u <- st_cast(st_union(lines), "MULTILINESTRING")
  st_cast(st_line_merge(u), "LINESTRING")
}

# Approximate medial-axis skeleton of a single polygon
skeletonize <- function(poly, p) {
  g   <- st_simplify(poly, dTolerance = p$simplify_m, preserveTopology = TRUE)
  g   <- st_segmentize(g, dfMaxLength = p$densify_m)
  pts <- st_cast(g, "MULTIPOINT")
  env <- st_as_sfc(st_bbox(st_buffer(g, 10 * p$densify_m)))
  vor <- st_voronoi(pts, envelope = env, bOnlyEdges = TRUE)
  edg <- st_cast(vor, "LINESTRING")
  edg <- edg[st_contains_properly(g, edg)[[1]]]
  if (length(edg) == 0) return(edg)
  merge_lines(edg)
}

# Iteratively remove dangling branches shorter than prune_m
prune_spurs <- function(lines, prune_m) {
  repeat {
    if (length(lines) <= 1) return(lines)
    e    <- line_ends(lines)
    len  <- as.numeric(st_length(lines))
    spur <- (e$deg_s == 1 | e$deg_e == 1) & len < prune_m
    spur[which.max(len)] <- FALSE
    if (!any(spur)) return(lines)
    lines <- merge_lines(lines[!spur])
  }
}

# Segment from the last vertex of xy, along its final direction, to bnd
ray_to_boundary <- function(xy, look_back_m, ray_len, bnd) {
  n  <- nrow(xy)
  p  <- xy[n, ]
  rv <- xy[n:1, , drop = FALSE]
  d  <- cumsum(c(0, sqrt(rowSums(diff(rv)^2))))
  j  <- which(d >= look_back_m)[1]
  if (is.na(j)) j <- n
  v  <- p - rv[j, ]
  vl <- sqrt(sum(v^2))
  if (vl == 0) return(NULL)
  ray <- st_sfc(st_linestring(rbind(p, p + v / vl * ray_len)), crs = st_crs(bnd))
  hit <- suppressWarnings(st_intersection(ray, bnd))
  if (length(hit) == 0 || all(st_is_empty(hit))) return(NULL)
  hp <- st_coordinates(hit)[, 1:2, drop = FALSE]
  k  <- which.min((hp[, 1] - p[1])^2 + (hp[, 2] - p[2])^2)
  st_linestring(rbind(p, hp[k, ]))
}

# Extend each dangling skeleton end to the polygon boundary
extend_dangles <- function(lines, poly, look_back_m) {
  e   <- line_ends(lines)
  bnd <- st_boundary(poly)
  bb  <- st_bbox(poly)
  L   <- 2 * sqrt((bb[["xmax"]] - bb[["xmin"]])^2 + (bb[["ymax"]] - bb[["ymin"]])^2)
  ext <- list()
  for (i in seq_along(lines)) {
    xy <- e$xy[e$idx[[i]], 1:2, drop = FALSE]
    if (e$deg_e[i] == 1)
      ext <- c(ext, list(ray_to_boundary(xy, look_back_m, L, bnd)))
    if (e$deg_s[i] == 1)
      ext <- c(ext, list(ray_to_boundary(xy[nrow(xy):1, , drop = FALSE], look_back_m, L, bnd)))
  }
  ext <- Filter(Negate(is.null), ext)
  if (length(ext) == 0) return(lines)
  c(lines, st_sfc(ext, crs = st_crs(lines)))
}

# Split a polygon by lines; keep faces inside the polygon
split_by_lines <- function(poly, lines) {
  edges <- st_union(c(st_cast(st_boundary(poly), "MULTILINESTRING"),
                      st_cast(lines, "MULTILINESTRING")))
  faces <- st_cast(st_collection_extract(st_polygonize(edges), "POLYGON"), "POLYGON")
  inside <- lengths(st_within(st_point_on_surface(faces), poly)) > 0
  faces[inside]
}

# Skeletonize, extend and split one void part where applicable
split_void_part <- function(part, p) {
  whole <- list(pieces = part, lines = st_sfc(crs = st_crs(part)), split = 0L)
  if (area_m2(part) < p$min_split_m2) return(whole)
  
  tryCatch({
    sk <- skeletonize(part, p)
    if (length(sk) == 0) return(whole)
    sk  <- prune_spurs(sk, p$prune_m)
    sk  <- extend_dangles(sk, part, p$look_back_m)
    sk  <- suppressWarnings(st_intersection(sk, part))
    sk  <- suppressWarnings(st_collection_extract(sk, "LINESTRING"))
    pcs <- split_by_lines(part, sk)
    ok  <- length(pcs) > 1 &&
      abs(sum(area_m2(pcs)) / area_m2(part) - 1) < 0.005
    if (!ok) return(whole)
    list(pieces = pcs, lines = sk, split = 1L)
  }, error = function(err) {
    log_msg("Void part left whole after split error: ", conditionMessage(err))
    whole
  })
}

# Step 3: void as one multipart feature; split each part
build_void <- function(aoi, blocks, p) {
  v <- st_difference(aoi, st_union(st_geometry(blocks)))
  if (p$sliver_m > 0) {
    v <- st_buffer(v, -p$sliver_m, joinStyle = "MITRE", mitreLimit = 2)
    v <- st_buffer(v,  p$sliver_m, joinStyle = "MITRE", mitreLimit = 2)
  }
  v     <- clean_polygons(v)
  parts <- st_cast(st_union(v), "POLYGON")
  parts <- parts[area_m2(parts) >= p$min_part_m2]
  log_msg("Void parts: ", length(parts))
  
  void <- st_sf(void_id = 1L,
                area_m2 = sum(area_m2(parts)),
                n_parts = length(parts),
                geometry = st_cast(st_union(parts), "MULTIPOLYGON"))
  
  pieces <- vector("list", length(parts))
  skel   <- vector("list", length(parts))
  for (i in seq_along(parts)) {
    r <- split_void_part(parts[i], p)
    pieces[[i]] <- st_sf(void_part_id = i, split = r$split, geometry = r$pieces)
    if (length(r$lines) > 0)
      skel[[i]] <- st_sf(void_part_id = i, geometry = r$lines)
  }
  pieces <- do.call(rbind, pieces)
  pieces$piece_id <- seq_len(nrow(pieces))
  pieces$area_m2  <- area_m2(pieces)
  pieces <- relocate(pieces, piece_id, void_part_id, split, area_m2)
  skel   <- do.call(rbind, Filter(Negate(is.null), skel))
  
  log_msg("Void pieces: ", nrow(pieces), " (", sum(pieces$split), " from split parts)")
  list(void = void, pieces = pieces, skeleton = skel)
}

# -----------------------------------------------------------------------------
# SECTION 3b. Donut resolution helpers (Steps 2 and 5)
# -----------------------------------------------------------------------------

# Number of interior rings per MULTIPOLYGON feature
n_holes <- function(g) {
  vapply(g, function(mp) as.integer(sum(lengths(mp) - 1L)), integer(1))
}

# Two bridges from hole `h` of a single polygon to its outer ring, or NULL
find_bridges <- function(poly, h, p) {
  rings <- poly[[1]]
  outer <- rings[[1]][-nrow(rings[[1]]), 1:2, drop = FALSE]
  hole  <- rings[[h]][-nrow(rings[[h]]), 1:2, drop = FALSE]
  nh    <- nrow(hole)
  k     <- min(p$k_nearest, nrow(outer))
  
  # Position of each hole vertex along the hole ring
  seg_len <- sqrt(rowSums(diff(rbind(hole, hole[1, ]))^2))
  P       <- sum(seg_len)
  pos     <- c(0, cumsum(seg_len))[seq_len(nh)]
  
  anchors <- unique(round(seq(1, nh, length.out = min(nh, p$max_hole_vertices))))
  cand <- do.call(rbind, lapply(anchors, function(i) {
    d <- sqrt((outer[, 1] - hole[i, 1])^2 + (outer[, 2] - hole[i, 2])^2)
    j <- order(d)[seq_len(k)]
    data.frame(i = i, j = j, len = d[j])
  }))
  segs <- st_sfc(lapply(seq_len(nrow(cand)), function(r)
    st_linestring(rbind(hole[cand$i[r], ], outer[cand$j[r], ]))), crs = st_crs(poly))
  
  valid <- lengths(st_covered_by(segs, poly)) > 0
  if (!any(valid)) return(NULL)
  cand <- cand[valid, ]
  segs <- segs[valid]
  cand$row <- seq_len(nrow(cand))
  
  best <- cand |>
    group_by(i) |>
    slice_min(len, n = 1, with_ties = FALSE) |>
    ungroup()
  m <- nrow(best)
  if (m < 2) return(NULL)
  
  pr  <- which(upper.tri(matrix(FALSE, m, m)), arr.ind = TRUE)
  sep <- abs(pos[best$i[pr[, 1]]] - pos[best$i[pr[, 2]]])
  sep <- pmin(sep, P - sep)
  ok  <- sep >= p$min_sep_frac * P & best$j[pr[, 1]] != best$j[pr[, 2]]
  if (!any(ok)) return(NULL)
  pr  <- pr[ok, , drop = FALSE]
  tot <- best$len[pr[, 1]] + best$len[pr[, 2]]
  
  for (q in head(order(tot), 50)) {
    a <- segs[best$row[pr[q, 1]]]
    b <- segs[best$row[pr[q, 2]]]
    if (!st_intersects(a, b, sparse = FALSE)[1, 1]) return(c(a, b))
  }
  NULL
}

# Union of the host's untouched parts with a new piece, as MULTIPOLYGON
host_geometry <- function(other_parts, piece) {
  g <- if (length(other_parts)) st_union(c(other_parts, piece)) else piece
  st_cast(g, "MULTIPOLYGON")
}

# Resolve donuts: carve the host along two bridges, then merge the donut
# with the smaller carved piece. Only hosts and the features inside their
# outer rings are processed; all other features pass through unchanged.
resolve_donuts <- function(x, p) {
  x <- clean_polygons(x)
  for (col in c("donut_split", "donut_merged"))
    x[[col]] <- if (col %in% names(x)) coalesce(x[[col]], 0L) else 0L
  
  hosts <- which(n_holes(st_geometry(x)) > 0)
  if (length(hosts) == 0) return(x)
  filled <- st_sfc(lapply(st_geometry(x)[hosts], function(mp)
    st_multipolygon(lapply(mp, function(pg) pg[1]))), crs = st_crs(x))
  involved <- sort(unique(c(hosts, unlist(st_intersects(filled, x)))))
  log_msg("Donut hosts: ", length(hosts), " (", length(involved), " features involved)")
  
  sub <- resolve_donuts_subset(x[involved, ], p)
  rbind(x[-involved, ], sub)
}

resolve_donuts_subset <- function(x, p) {
  gcol     <- attr(x, "sf_column")
  crs      <- st_crs(x)
  x$.uid   <- seq_len(nrow(x))
  next_uid <- nrow(x) + 1L
  failed   <- integer(0)
  n_merge  <- 0L
  n_split  <- 0L
  n_hosts  <- sum(n_holes(x[[gcol]]) > 0)
  max_iter <- 10L * sum(n_holes(x[[gcol]])) + 100L
  
  for (iter in seq_len(max_iter)) {
    hh <- which(n_holes(x[[gcol]]) > 0 & !(x$.uid %in% failed))
    if (length(hh) == 0) break
    r     <- hh[1]
    parts <- st_cast(x[[gcol]][r], "POLYGON")
    pi    <- which(lengths(parts) > 1)[1]
    part  <- parts[pi]
    other <- parts[-pi]
    done  <- FALSE
    
    for (h in 2:length(part[[1]])) {
      br <- find_bridges(part, h, p)
      if (is.null(br)) next
      pcs <- tryCatch(split_by_lines(part, br), error = function(e) NULL)
      if (is.null(pcs) || length(pcs) != 2 ||
          abs(sum(area_m2(pcs)) / area_m2(part) - 1) > 0.005) next
      
      # Features occupying the hole
      hole  <- st_sfc(st_polygon(list(part[[1]][[h]])), crs = crs)
      cands <- setdiff(st_intersects(hole, x)[[1]], r)
      ov    <- vapply(cands, function(i)
        sum(area_m2(suppressWarnings(st_intersection(x[[gcol]][i], hole)))), numeric(1))
      donut <- cands[ov > p$min_overlap_m2]
      
      small <- which.min(area_m2(pcs))
      large <- 3L - small
      
      if (length(donut) == 1) {
        # One donut: merge it with the smaller carved piece
        merged <- st_cast(st_union(c(x[[gcol]][donut], pcs[small])), "MULTIPOLYGON")
        x[[gcol]][donut]    <- merged
        x$donut_merged[donut] <- 1L
        x[[gcol]][r]        <- host_geometry(other, pcs[large])
        x$donut_split[r]    <- 1L
        n_merge <- n_merge + 1L
      } else {
        # Empty hole or several features: split the host only
        new_row         <- x[r, ]
        new_row[[gcol]] <- st_cast(pcs[2], "MULTIPOLYGON")
        new_row$.uid    <- next_uid
        next_uid        <- next_uid + 1L
        x[[gcol]][r]     <- host_geometry(other, pcs[1])
        x$donut_split[r] <- 1L
        new_row$donut_split <- 1L
        x <- rbind(x, new_row)
        n_split <- n_split + 1L
      }
      done <- TRUE
      break
    }
    if (!done) failed <- c(failed, x$.uid[r])
  }
  
  x$.uid <- NULL
  log_msg("Donut hosts resolved: ", n_hosts, "; donuts merged: ", n_merge,
          "; hosts split without merge: ", n_split,
          "; features with holes remaining: ", sum(n_holes(x[[gcol]]) > 0))
  x
}

# -----------------------------------------------------------------------------
# SECTION 4. Hexagon grid and merge helpers (Step 4)
# -----------------------------------------------------------------------------

# Hexagonal grid with cells of area_m2 covering the AOI
make_hex_grid <- function(aoi, area) {
  d <- sqrt(2 * area / sqrt(3))
  g <- st_make_grid(aoi, cellsize = d, square = FALSE)
  g <- g[lengths(st_intersects(g, aoi)) > 0]
  log_msg("Hex cells: ", length(g), " (cell area ", round(area_m2(g[1]) / 1e4, 2), " ha)")
  st_sf(hex_id = seq_along(g), geometry = g)
}

# Intersect void pieces with hexagons; explode to single polygons
cut_by_hex <- function(pieces, hex) {
  a <- pieces[c("piece_id", "void_part_id", "split")]
  b <- hex["hex_id"]
  st_agr(a) <- "constant"
  st_agr(b) <- "constant"
  x <- clean_polygons(st_intersection(a, b))
  x <- suppressWarnings(st_cast(x, "POLYGON"))
  x$vsh_id  <- seq_len(nrow(x))
  x$area_m2 <- area_m2(x)
  relocate(x, vsh_id)
}

# Shared-edge length for every pair of edge-adjacent polygons in x.
# Pass 1 (vectorized): every boundary is decomposed into vertex-to-vertex
# segments keyed by their endpoints (rounded to `digits`); a segment found
# in two polygons is a shared edge. Polygons produced by the same overlay
# share vertices along common edges, so this captures nearly all adjacency.
# Pass 2 (fallback): for polygons with no matched edge but a neighbor within
# `tol`, shared length is measured geometrically, pair by pair.
shared_edges <- function(x, tol, digits = 2) {
  g  <- st_geometry(x)
  xy <- st_coordinates(g)
  fc <- ncol(xy)                                   # feature index column
  ring <- paste(xy[, fc], xy[, 3])                 # feature + ring id
  n  <- nrow(xy)
  a  <- seq_len(n - 1)
  b  <- a + 1L
  ok <- ring[a] == ring[b]
  a  <- a[ok]
  b  <- b[ok]
  
  x1 <- round(xy[a, 1], digits); y1 <- round(xy[a, 2], digits)
  x2 <- round(xy[b, 1], digits); y2 <- round(xy[b, 2], digits)
  swap <- x1 > x2 | (x1 == x2 & y1 > y2)
  k1 <- ifelse(swap, paste(x2, y2), paste(x1, y1))
  k2 <- ifelse(swap, paste(x1, y1), paste(x2, y2))
  len <- sqrt((xy[b, 1] - xy[a, 1])^2 + (xy[b, 2] - xy[a, 2])^2)
  
  segs <- data.frame(key = paste(k1, k2), poly = as.integer(xy[a, fc]), len = len)
  segs <- segs[segs$len > 0, ]
  segs <- segs[!duplicated(segs[c("key", "poly")]), ]
  
  pairs <- segs |>
    group_by(key) |>
    filter(n() == 2) |>
    summarise(i = min(poly), j = max(poly), len = first(len), .groups = "drop") |>
    group_by(i, j) |>
    summarise(len = sum(len), .groups = "drop") |>
    as.data.frame()
  
  # Fallback for polygons without a matched edge
  matched <- unique(c(pairs$i, pairs$j))
  todo    <- setdiff(seq_along(g), matched)
  if (length(todo) > 0) {
    nb <- st_is_within_distance(g[todo], g, dist = tol)
    fb <- do.call(rbind, lapply(seq_along(todo), function(k) {
      j <- setdiff(nb[[k]], todo[k])
      if (length(j)) data.frame(i = pmin(todo[k], j), j = pmax(todo[k], j)) else NULL
    }))
    if (!is.null(fb)) {
      fb <- fb[!duplicated(fb), ]
      bd <- st_boundary(g)
      fb$len <- mapply(function(i, j) {
        s <- suppressWarnings(st_intersection(bd[i], st_buffer(bd[j], tol)))
        if (length(s) == 0) 0 else sum(as.numeric(st_length(s)))
      }, fb$i, fb$j)
      pairs <- rbind(pairs, fb[fb$len > 0, ])
      pairs <- pairs[!duplicated(pairs[c("i", "j")]), ]
    }
  }
  log_msg("Shared edges: ", nrow(pairs), " adjacent pairs (",
          length(todo), " polygons checked by fallback)")
  pairs
}

# Merge polygons < merge_below_m2 into the neighbor sharing the longest edge
merge_small_polygons <- function(x, p) {
  n     <- nrow(x)
  small <- which(x$area_m2 < p$merge_below_m2)
  grp   <- seq_len(n)
  ga    <- x$area_m2
  iso   <- logical(n)
  
  if (length(small) > 0) {
    pairs <- shared_edges(x, p$edge_tol_m)
    pairs <- pairs[pairs$len >= p$min_edge_m, ]
    
    repeat {
      cand <- which(grp == seq_len(n) & ga < p$merge_below_m2 & !iso)
      if (length(cand) == 0) break
      g   <- cand[which.min(ga[cand])]
      gi  <- grp[pairs$i]
      gj  <- grp[pairs$j]
      hit <- xor(gi == g, gj == g)
      if (!any(hit)) {
        iso[g] <- TRUE
        next
      }
      other  <- ifelse(gi[hit] == g, gj[hit], gi[hit])
      shared <- tapply(pairs$len[hit], other, sum)
      tg     <- as.integer(names(shared)[which.max(shared)])
      grp[grp == g] <- tg
      ga[tg] <- ga[tg] + ga[g]
      ga[g]  <- NA
    }
  }
  
  x$void_unit_id <- match(grp, unique(grp))
  x$still_small  <- iso[grp]
  units <- x |>
    group_by(void_unit_id) |>
    summarise(n_members   = n(),
              hex_ids     = paste(sort(unique(hex_id)), collapse = ";"),
              still_small = any(still_small),
              do_union    = TRUE, .groups = "drop") |>
    clean_polygons()
  
  log_msg("Void-skeleton-hex polygons: ", n, " (", length(small), " below threshold)",
          " -> void units: ", nrow(units), " (", sum(units$still_small), " still small)")
  units
}

# -----------------------------------------------------------------------------
# SECTION 5. Per-city workflow
# -----------------------------------------------------------------------------

# Steps 1-3: read and tidy blocks, cut to AOI, resolve donuts, build void
build_blocks_and_void <- function(paths, crs) {
  blocks <- st_read(paths[["blocks"]], layer = "blocks",
                    fid_column_name = "block_fid", quiet = TRUE) |>
    st_transform(crs)
  log_msg("GRID3 blocks: ", nrow(blocks))
  
  # Step 1: tidy existing attributes
  blocks <- tidy_block_attributes(blocks)
  
  # Step 2: cut blocks to AOI (only blocks crossing the AOI edge are
  # intersected); resolve donuts
  aoi <- st_read(paths[["aoi"]], quiet = TRUE) |>
    st_transform(crs) |>
    st_make_valid() |>
    st_union()
  st_agr(blocks) <- "constant"
  inside <- lengths(st_within(blocks, aoi)) > 0
  edge   <- !inside & lengths(st_intersects(blocks, aoi)) > 0
  blocks <- if (any(edge)) {
    rbind(blocks[inside, ], st_intersection(blocks[edge, ], aoi))
  } else {
    blocks[inside, ]
  }
  blocks <- clean_polygons(blocks)
  log_msg("GRID3 blocks within AOI: ", nrow(blocks))
  blocks <- resolve_donuts(blocks, donut_params)
  
  # Step 3: void; skeleton split
  vd <- build_void(aoi, blocks, void_params)
  
  list(aoi = aoi, blocks = blocks, vd = vd)
}

process_city <- function(city, blocks_file, overture_file, aoi_file, epsg) {
  log_msg("=== ", city, " ===")
  crs <- st_crs(epsg)
  
  # Check inputs before processing
  paths <- c(blocks   = file.path(root_blocks,   blocks_file),
             overture = file.path(root_overture, overture_file),
             aoi      = file.path(root_aoi,      aoi_file))
  missing <- paths[!file.exists(paths)]
  if (length(missing))
    stop("Input not found: ", paste(names(missing), missing, sep = " = ", collapse = "; "))
  
  ckpt   <- file.path(checkpoint_dir, paste0(city, "_coverage_checkpoint.rds"))
  ckpt_v <- file.path(checkpoint_dir, paste0(city, "_void_checkpoint.rds"))
  
  if (resume && file.exists(ckpt)) {
    # Resume after Step 5 from checkpoint
    ck       <- readRDS(ckpt)
    aoi      <- ck$aoi
    cover    <- ck$cover
    vd       <- ck$vd
    hex      <- ck$hex
    void_hex <- ck$void_hex
    rm(ck)
    log_msg("Resumed from checkpoint: ", ckpt, " (", nrow(cover), " polygons)")
  } else {
    # Steps 1-3, or resume after Step 3 from checkpoint
    if (resume && file.exists(ckpt_v)) {
      ck <- readRDS(ckpt_v)
      log_msg("Resumed from checkpoint: ", ckpt_v)
    } else {
      ck <- build_blocks_and_void(paths, crs)
      saveRDS(ck, ckpt_v)
      log_msg("Checkpoint saved: ", ckpt_v)
    }
    aoi    <- ck$aoi
    blocks <- ck$blocks
    vd     <- ck$vd
    rm(ck)
    
    # Step 4: hex grid; cut; merge polygons below threshold
    hex        <- make_hex_grid(aoi, hex_params$hex_area_m2)
    void_hex   <- cut_by_hex(vd$pieces, hex)
    void_units <- merge_small_polygons(void_hex, hex_params)
    
    # Step 5: combine blocks and void units into one AOI coverage
    blocks$source     <- "grid3_block"
    void_units$source <- "aoi_void"
    cover <- st_sf(
      bind_rows(st_drop_geometry(blocks), st_drop_geometry(void_units)),
      geom = c(st_geometry(blocks), st_geometry(void_units))
    )
    cover <- resolve_donuts(cover, donut_params)
    cover$n_holes           <- n_holes(st_geometry(cover))
    cover$block_id_coverAOI <- seq_len(nrow(cover))
    cover$area_m2           <- area_m2(cover)
    
    saveRDS(list(aoi = aoi, cover = cover, vd = vd, hex = hex, void_hex = void_hex), ckpt)
    log_msg("Checkpoint saved: ", ckpt)
  }
  
  # Step 6: Overture points and dissolved footprints
  bldg <- read_overture(paths[["overture"]]) |>
    st_transform(crs) |>
    st_zm(drop = TRUE) |>
    clean_polygons()
  bldg <- bldg[lengths(st_intersects(bldg, aoi)) > 0, ]
  log_msg("Overture footprints in AOI: ", nrow(bldg))
  
  bldg_pts   <- st_sf(bldg_id = seq_len(nrow(bldg)),
                      geometry = st_point_on_surface(st_geometry(bldg)))
  bldg_parts <- dissolve_footprints(st_geometry(bldg))
  
  # Steps 7-8: building metrics for all polygons
  cover <- add_bldg_metrics(cover, "block_id_coverAOI", bldg_pts, bldg_parts)
  
  # Step 9: final ID and column order
  cover <- cover |>
    arrange(block_id_coverAOI) |>
    relocate(block_id_coverAOI, source, block_fid, void_unit_id,
             area_m2, bldg_cnt, bldg_area_m2)
  
  # Summary
  aoi_a <- area_m2(aoi)
  is_b  <- cover$source == "grid3_block"
  log_msg("Polygons: ", nrow(cover), " (", sum(is_b), " blocks, ",
          sum(!is_b), " void units)")
  log_msg("AOI coverage: ", round(100 * sum(cover$area_m2) / aoi_a, 2), "%")
  log_msg("Buildings assigned: ", sum(cover$bldg_cnt), " of ", nrow(bldg_pts))
  log_msg("bldg_cnt == building_count: ",
          sum(cover$bldg_cnt[is_b] == cover$building_count[is_b], na.rm = TRUE),
          " of ", sum(is_b), " blocks")
  
  # Write
  out <- file.path(out_dir, paste0(city, "_blocks_segments_metrics_coverAOI.gpkg"))
  st_write(cover, out, layer = "blocks", delete_layer = TRUE, quiet = TRUE)
  log_msg("Written: ", out)
  
  if (write_qa_layers) {
    qa <- file.path(out_dir, paste0(city, "_coverAOI_qa.gpkg"))
    st_write(vd$void,   qa, layer = "aoi_void",    delete_layer = TRUE, quiet = TRUE)
    st_write(vd$pieces, qa, layer = "void_pieces", delete_layer = TRUE, quiet = TRUE)
    if (!is.null(vd$skeleton))
      st_write(vd$skeleton, qa, layer = "void_skeleton", delete_layer = TRUE, quiet = TRUE)
    st_write(hex,      qa, layer = "hex_grid", delete_layer = TRUE, quiet = TRUE)
    st_write(void_hex, qa, layer = "void_hex", delete_layer = TRUE, quiet = TRUE)
    log_msg("QA layers written: ", qa)
  }
  
  invisible(out)
}

# -----------------------------------------------------------------------------
# SECTION 6. Run
# -----------------------------------------------------------------------------
stopifnot(all(run_cities %in% cities$city))

for (cty in run_cities) {
  cfg <- cities[cities$city == cty, ]
  process_city(cfg$city, cfg$blocks_file, cfg$overture_file, cfg$aoi_file, cfg$epsg)
  gc()
}