# =============================================================================
# 2_segment_coverAOI.R
# -----------------------------------------------------------------------------
# Project : World Bank LandAtlas / SPARC Urban Analytics Platform
# Author  : Dana Thomson, CIESIN, Columbia University
# Created : 2026-11-08
#
# DESCRIPTION
#   Groups the AOI block coverage (output of blocks_segments_metrics_coverAOI.R)
#   into segments. Original segments are kept and extended to adjacent
#   blocks; remaining blocks form new segments. Segments are then refined:
#   large and awkward segments are split along major linear breaks in the
#   block pattern, donuts and fragments are removed, sparsely settled units
#   are grouped into compact segments and stranded units are absorbed.
#   For each city:
#     1. Read original segments; convert multipart segments to single parts
#                                                   -> seg_part_ID
#     2. Read the block coverage; drop artifact polygons; merge each
#        still_small polygon into the neighbor sharing its longest edge;
#        reassign detached fragments
#     3. Detect shared edges between blocks and derive major linear breaks
#        ("strokes") from them
#     4. Assign blocks to original segment parts: (a) by area of overlap,
#        (b) by longest shared edge, (c) clusters bordered by a single
#        segment; record the nearest segment part and distance per block
#     5. Form initial new segments from unassigned blocks: zoned clusters,
#        then no-zone blocks joined to adjacent zoned clusters, then
#        no-zone clusters
#     6. Refine segments, repeating until no change or `max_iter` passes:
#        a. split large segments along the strongest breaks
#        b. split non-compact (elongated or snaking) segments
#        c. separate disconnected parts of segments
#        d. merge tiny segments into the neighbor with the longest edge
#        e. merge segments with exactly one neighbor into it (donuts)
#        f. group sparsely settled segments into compact segments
#        g. merge stranded segments with few buildings into a neighbor,
#           preferring the same zone
#     7. Dissolve blocks into segments; heal; fill empty holes
#     8. Add blocks, segments and breaks as new layers to the block
#        coverage GeoPackage
#
# USAGE
#   Set `run_cities` in SECTION 1 to one or more of the cities in `cities`,
#   then source the whole script. Cities are processed one at a time.
#
# INPUTS (per city; see `cities` in SECTION 1)
#   <city>_blocks_segments_metrics.gpkg           layer "segments"
#   <city>_blocks_segments_metrics_coverAOI.gpkg  layer "blocks"
#
# OUTPUT
#   Layers added to <city>_blocks_segments_metrics_coverAOI.gpkg. The input
#   "blocks" layer is read only and never modified; on rerun the output
#   layers are replaced. Population is not used and is dropped.
#     blocks_cln1    block coverage after merging still_small polygons
#                    seg_part_ID        final segment of the block
#                    seg_source         "original" (from, or split from, an
#                                       original segment) or "new"
#                    seg_assign         Step 4 assignment: "overlap", "edge",
#                                       "enclosed" or "new"
#                    seg_overlap_share  share of block area inside its
#                                       best-overlapping original segment part
#                    seg_part_nearest   original segment part nearest to the
#                                       block centroid
#                    dist_to_seg_part   distance from the centroid to
#                                       seg_part_nearest (m); 0 if inside it
#                    n_merged           polygons combined into the block
#                    <block coverage attributes>
#     segments_cln1  blocks dissolved by seg_part_ID
#                    seg_part_ID        segment ID (original segment part ID,
#                                       or a new ID above the original range)
#                    seg_source         "original" or "new"
#                    seg_fid            fid of the source segment (segments
#                                       keeping an original ID)
#                    n_blocks           number of blocks
#                    area_m2, area_ha   area of the segment
#                    bldg_cnt, bldg_area_m2  sums over blocks
#                    bldg_per_ha        buildings per hectare
#                    zone_id            dominant `zone_field` value of the
#                                       segment's blocks (by area)
#                    seg_part_area_m2   area of the source segment part
#                    segment_area_m2    area of the whole source segment
#                    compact_j          normalized moment of inertia (1 =
#                                       disc; higher = less compact)
#                    polsby_popper      4 * pi * area / perimeter^2 (1 =
#                                       disc; lower = less compact)
#                    n_holes            interior rings (enclosed segments
#                                       that could not be merged)
#     breaks_cln1    shared block edges (arcs) with stroke attributes, for
#                    review of the major linear breaks
#                    stroke_id, stroke_len_m, arc_len_m, block_i, block_j
#                    (block_i, block_j = block_id_coverAOI)
#
# METHOD
#   Step 1  Segments are made valid and exploded to single polygons. Each
#           part receives a sequential seg_part_ID; the source fid is kept
#           as seg_fid. Segments are transformed to the CRS of the blocks.
#   Step 2  Polygons smaller than `tiny_m2` (geometric artifacts) are
#           dropped. Polygons with still_small = TRUE are merged, smallest
#           first, into the neighbor sharing the longest edge
#           (>= `min_edge_m` within `edge_tol_m`); if none is found, the
#           neighbor with the longest edge within `gap_tol_m` is used and
#           the merged polygon is closed with radius `gap_tol_m` so the gap
#           is filled. The merged polygon keeps the neighbor's
#           block_id_coverAOI and attributes; `sum_fields` are summed.
#           Zone preference applies (see Zones). Detached parts of merged
#           polygons, other than the largest, are moved to the adjacent
#           polygon sharing their longest edge.
#   Zones   `zone_field` (NA = no zone; typically void units) is preserved
#           where possible:
#           - merges (Steps 2, 6d, 6f, 6g) process no-zone polygons first and
#             choose the receiving neighbor by tier: a polygon with a zone
#             prefers the same zone, then no zone, then another zone; a
#             polygon without a zone prefers any zoned neighbor, then a
#             no-zone neighbor. Within a tier, the longest shared edge wins
#             (6f: see below).
#           - Step 5 builds new segments around zoned blocks.
#           - block pairs across a boundary between two different zones get
#             `zone_break_m` added to their break strength, so the splits in
#             Step 6a-b cut along zone boundaries first.
#   Step 3  Shared edges. Block boundaries are densified (max segment
#           `densify_m`) and decomposed into vertex-to-vertex segments. A
#           segment is shared with another block if (i) its endpoints,
#           rounded to `digits`, match a segment of exactly one other block,
#           or otherwise (ii) an unmatched segment of another block lies
#           within `edge_snap_m` of the segment midpoint (nearest if
#           several). Rule (ii) captures T-junctions, small offsets and
#           hairline gaps. Consecutive segments of a ring with the same
#           neighbor form an arc; each boundary is drawn once. Block pairs
#           sharing >= `min_edge_m` are adjacent; this adjacency is used in
#           all later steps.
#           Strokes. Arc ends within `edge_snap_m` of each other form a
#           junction. At each junction, arc ends are paired greedily by
#           smallest deflection angle (direction measured over the last
#           `stroke_look_m` of each arc), up to `stroke_max_deflect` degrees.
#           Chains of paired arcs form strokes; a stroke's length measures
#           how major a linear break is (after Thomson & Richardson, 1999;
#           Jiang et al., 2008). Each adjacent block pair gets a break
#           strength equal to the longest stroke along their shared
#           boundary.
#   Step 4  (a) A block wholly within an original segment part is assigned
#           to it; other blocks are assigned to the best-overlapping part if
#           the overlap covers >= `overlap_min_share` of the block. (b) Each
#           unassigned block adjacent to assigned blocks joins the segment
#           with which it shares the longest total edge
#           (`edge_assign_passes` passes). (c) Connected clusters of
#           still-unassigned blocks whose only neighboring segment is a
#           single segment join it if the result stays <= `hard_max_ha`.
#   Step 5  Unassigned blocks with a zone form connected clusters of a
#           single zone; unassigned no-zone blocks touching such a cluster
#           join the one with which they share the longest edge; remaining
#           no-zone blocks form connected clusters. Each cluster is a new
#           segment.
#   Step 6  Segments are refined on block labels, using block adjacency,
#           areas, building counts, centroids and break strengths;
#           geometry is dissolved only in Step 7. Each pass runs the splits
#           (a-b), then the merges (c-g), so a pass ends without donuts or
#           stranded units. Passes repeat until labels no longer change or
#           `max_iter` is reached.
#           (a) Large: a segment exceeding `target_ha` or `bldg_soft_max`
#               (first pass) or `hard_max_ha` (later passes) is split along
#               its strongest breaks. Its blocks form a minimum spanning tree
#               on break strength (Kruskal); the tree is cut recursively,
#               and by the MST cycle property every boundary crossing a cut
#               has a break strength >= that of the removed edge. Parts must
#               be >= `min_piece_ha` unless the region exceeds `hard_max_ha`.
#               Cut score: (strength / max strength) *
#               (2 * balance) ^ balance_weight, where balance =
#               min(load_a, load_b) / (load_a + load_b) and load =
#               max(area / target_ha, buildings / bldg_soft_max).
#           (b) Non-compact: compactness is the normalized moment of
#               inertia J = 2 * pi * I / A^2 of the segment's blocks
#               (centroids weighted by area, blocks treated as discs).
#               J = 1 for a disc, 1.05 for a square, 1.75 for a 3:1
#               rectangle, 4.25 for an 8:1 rectangle; bent or snaking shapes
#               score higher than straight shapes of the same proportions.
#               Segments with J > `j_max` are cut along their minimum
#               spanning tree into up to `max_parts` parts of
#               >= `min_piece_ha`, each cut maximizing
#               (J_parent - area-weighted J of parts) / J_parent *
#               (strength / max strength) ^ strength_weight.
#           (c) Disconnected: a segment whose blocks form more than one
#               connected group is separated; the largest group keeps the ID.
#           (d) Tiny: segments < `min_seg_ha` merge into the neighbor
#               sharing the longest edge.
#           (e) Donuts: a segment with exactly one neighbor (enclosed by it,
#               or attached only to it) merges into that neighbor.
#           (f) Sparse: segments with < `density_max` buildings per hectare
#               and area < `max_ha` merge (no-zone first, then smallest)
#               into (1) a zoned neighbor of any density (same zone if the
#               segment has a zone), longest edge; otherwise (2) a sparse
#               no-zone neighbor, then (3) any sparse neighbor, choosing the
#               most compact result (lowest J). The result must stay
#               <= `max_ha`.
#           (g) Stranded: segments with < `bldg_threshold` buildings and
#               area < `max_ha` merge by zone tier and longest edge.
#           Merges (d, e, g) are skipped if the result would exceed
#           `hard_max_ha`. Merged segments keep the receiving segment's ID.
#           Split parts inherit the parent's source; the largest part keeps
#           the parent's ID.
#   Step 7  Blocks are dissolved by seg_part_ID and healed (closing of
#           `heal_m`; interior rings < `min_hole_m2` filled). Interior rings
#           that contain no block are filled.
#   All geometry operations are planar in the projected CRS of the blocks.
#
# REFERENCES
#   Jiang, B., Zhao, S., & Yin, J. (2008). Self-organized natural roads for
#     predicting traffic flow: A sensitivity study. Journal of Statistical
#     Mechanics: Theory and Experiment, 2008(07), P07008.
#   Thomson, R. C., & Richardson, D. E. (1999). The 'good continuation'
#     principle of perceptual organization applied to the generalization of
#     road networks. Proceedings of the 19th International Cartographic
#     Conference, Ottawa.
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
run_cities <- c("juba", "kigali", "johannesburg")

root <- "//DATASERVER1/ScienceData$/dthomson/Documents/zzz_WORK/p_WB_LandAtlas/deliverables/Data - Revising block segment attributes"

cities <- dplyr::tribble(
  ~city,          ~segments_file,                               ~blocks_file,
  "juba",         "juba_blocks_segments_metrics.gpkg",          "juba_blocks_segments_metrics_coverAOI.gpkg",
  "kigali",       "kigali_blocks_segments_metrics.gpkg",        "kigali_blocks_segments_metrics_coverAOI.gpkg",
  "johannesburg", "johannesburg_blocks_segments_metrics.gpkg",  "johannesburg_blocks_segments_metrics_coverAOI.gpkg"
)

segments_layer     <- "segments"       # input layer in <city>_blocks_segments_metrics.gpkg
blocks_layer       <- "blocks"         # input layer in the coverAOI file (read only)
out_blocks_layer   <- "blocks_cln1"    # output layers in the coverAOI file
out_segments_layer <- "segments_cln1"
out_breaks_layer   <- "breaks_cln1"

zone_field <- "zones_5_ID"             # zone attribute of the blocks (NA = no zone)

# Step 2: artifacts and still_small merge
merge_params <- list(
  tiny_m2    = 1,        # polygons smaller than this are dropped as artifacts (m2)
  edge_tol_m = 0.01,     # tolerance for shared-edge detection (m)
  min_edge_m = 0.1,      # minimum shared-edge length for adjacency (m)
  gap_tol_m  = 2         # fallback: neighbors across a gap up to this width (m)
)

# Steps 3-6: shared edges, strokes, assignment, segment size
seg_params <- list(
  digits             = 2,     # coordinate rounding for exact edge matching (2 = 1 cm)
  densify_m          = 25,    # max boundary segment length for edge matching (m)
  edge_snap_m        = 1,     # proximity for shared edges and junctions (m)
  min_edge_m         = 1,     # minimum shared boundary for block adjacency (m)
  stroke_look_m      = 30,    # arc length used for direction at junctions (m)
  stroke_max_deflect = 40,    # max deflection for arcs to continue a stroke (deg)
  zone_break_m       = 5000,  # added to break strength across a zone boundary (m)
  overlap_min_share  = 0.5,   # min share of block area in an original segment part
  edge_assign_passes = 1,     # passes of longest-edge assignment (Step 4b)
  target_ha          = 50,    # split segments larger than this (ha)
  hard_max_ha        = 100,   # segments are kept below this (ha)
  bldg_soft_max      = 1000,  # split segments with more buildings than this
  min_piece_ha       = 5,     # min area of each part of a split (ha)
  balance_weight     = 1,     # weight of balance vs break strength in cuts
  min_seg_ha         = 5,     # merge segments smaller than this (ha)
  split_original     = TRUE,  # allow splitting segments derived from originals
  max_iter           = 3      # max refinement passes (Step 6)
)

# Step 6b: non-compact segments
compact_params <- list(
  j_max           = 4.0,      # split segments less compact than this (4.0 ~ 8:1 rectangle)
  max_parts       = 4,        # max parts per split
  min_piece_ha    = 5,        # min area of each part (ha)
  strength_weight = 0.5       # weight of break strength in cut choice
)

# Step 6f: sparsely settled segments
sparse_params <- list(
  density_max = 2,            # buildings per hectare below which a segment is sparse
  max_ha      = 50            # max area of a grouped sparse segment (ha)
)

# Step 6g: stranded segments
stranded_params <- list(
  bldg_threshold = 10,        # segments with fewer buildings than this ...
  max_ha         = 10         # ... and smaller than this (ha) are stranded
)

# Healing of polygons created by a union
heal_params <- list(
  heal_m      = 0.05,    # closing distance (m)
  min_hole_m2 = 1        # fill interior rings smaller than this (m2)
)

sum_fields   <- c("area_m2", "bldg_cnt", "bldg_area_m2")  # summed when merging
drop_fields  <- c("population")                            # dropped on read
centroid_fun <- st_centroid

# -----------------------------------------------------------------------------
# SECTION 2. General helpers
# -----------------------------------------------------------------------------

log_msg <- function(...) message(format(Sys.time(), "%H:%M:%S"), "  ", ...)

area_m2 <- function(x) as.numeric(st_area(x))

# Valid, non-empty MULTIPOLYGON geometry (sf or sfc)
clean_polygons <- function(x) {
  x <- st_make_valid(x)
  x <- suppressWarnings(st_collection_extract(x, "POLYGON"))
  keep <- !st_is_empty(x)
  x <- if (inherits(x, "sf")) x[keep, ] else x[keep]
  st_cast(x, "MULTIPOLYGON")
}

read_layer <- function(path, layer, ...) {
  log_msg("Reading ", basename(path), " [", layer, "]")
  st_read(path, layer = layer, quiet = TRUE, ...)
}

# Length of the boundary of a lying within tol of the boundary of b
# (a, b: sfc of length 1)
edge_length <- function(a, b, tol) {
  s <- suppressWarnings(st_intersection(st_boundary(a), st_buffer(st_boundary(b), tol)))
  if (length(s) == 0) 0 else sum(as.numeric(st_length(s)))
}

# Features of b within distance d of each feature of a (sparse list). Uses
# st_intersects on buffered a, which is spatially indexed; planar
# st_is_within_distance is not and scales with length(a) * length(b).
within_dist <- function(a, b, d) {
  st_intersects(st_buffer(st_geometry(a), d, nQuadSegs = 2), st_geometry(b))
}

# Pairs of points (i < j) within distance d, by grid hashing (cells of size
# d; each point is compared with points in its own and the 8 adjacent cells)
near_pairs <- function(x, y, d) {
  cell <- data.frame(i = seq_along(x), cx = floor(x / d), cy = floor(y / d))
  pr <- vector("list", 9)
  k  <- 0L
  for (ox in -1:1) for (oy in -1:1) {
    k  <- k + 1L
    sh <- data.frame(j = cell$i, cx = cell$cx + ox, cy = cell$cy + oy)
    mm <- inner_join(cell, sh, by = c("cx", "cy"), relationship = "many-to-many")
    pr[[k]] <- mm[mm$i < mm$j, c("i", "j")]
  }
  pr <- do.call(rbind, pr)
  pr <- pr[!duplicated(pr), ]
  pr[sqrt((x[pr$i] - x[pr$j])^2 + (y[pr$i] - y[pr$j])^2) <= d, ]
}

# Ring area (shoelace) for a coordinate matrix
ring_area <- function(r) {
  n <- nrow(r)
  abs(sum(r[-n, 1] * r[-1, 2] - r[-1, 1] * r[-n, 2])) / 2
}

# Heal polygons created by a union: close cracks and slits narrower than
# 2 * heal_m and fill interior rings smaller than min_hole_m2
heal_polygons <- function(g, p = heal_params) {
  crs <- st_crs(g)
  g <- st_buffer(g,  p$heal_m, joinStyle = "MITRE", mitreLimit = 5)
  g <- st_buffer(g, -p$heal_m, joinStyle = "MITRE", mitreLimit = 5)
  g <- clean_polygons(g)
  st_sfc(lapply(g, function(mp) {
    st_multipolygon(lapply(mp, function(pg) {
      if (length(pg) == 1) return(pg)
      keep <- c(TRUE, vapply(pg[-1], ring_area, numeric(1)) >= p$min_hole_m2)
      pg[keep]
    }))
  }), crs = crs)
}

# Zone preference tiers for merging a feature with zone zc into neighbors
# with zones zn. Returns a list of index vectors into zn, most preferred
# first:
#   zc set     : same zone; no zone; other zone
#   zc missing : any zone; no zone
zone_tiers <- function(zc, zn) {
  if (!is.na(zc)) {
    list(which(!is.na(zn) & zn == zc), which(is.na(zn)), which(!is.na(zn) & zn != zc))
  } else {
    list(which(!is.na(zn)), which(is.na(zn)))
  }
}

# Sum that returns NA only when all values are NA
sum_na <- function(v) if (all(is.na(v))) NA else sum(v, na.rm = TRUE)

# Union-find: connected-component label for n nodes and edges (a, b)
uf_components <- function(n, a, b) {
  parent <- seq_len(n)
  for (e in seq_along(a)) {
    x <- a[e]
    while (parent[x] != x) { parent[x] <- parent[parent[x]]; x <- parent[x] }
    y <- b[e]
    while (parent[y] != y) { parent[y] <- parent[parent[y]]; y <- parent[y] }
    if (x != y) parent[x] <- y
  }
  for (v in seq_len(n)) {
    x <- v
    while (parent[x] != x) x <- parent[x]
    parent[v] <- x
  }
  parent
}

# Minimum spanning forest (Kruskal) over n nodes; returns tree edges
kruskal <- function(n, a, b, s) {
  parent  <- seq_len(n)
  in_tree <- logical(length(a))
  for (e in order(s)) {
    x <- a[e]
    while (parent[x] != x) { parent[x] <- parent[parent[x]]; x <- parent[x] }
    y <- b[e]
    while (parent[y] != y) { parent[y] <- parent[parent[y]]; y <- parent[y] }
    if (x != y) { parent[x] <- y; in_tree[e] <- TRUE }
  }
  data.frame(a = a[in_tree], b = b[in_tree], s = s[in_tree])
}

# Root a tree given its nodes v and edges e (columns a, b in the same index
# space as v). Returns BFS order, parent and parent edge, indexed by
# position in v (root = position 1).
root_tree <- function(v, e) {
  k   <- length(v)
  loc <- match(c(e$a, e$b), v)
  ea  <- loc[seq_len(nrow(e))]
  eb  <- loc[nrow(e) + seq_len(nrow(e))]
  adj_n <- split(c(eb, ea), c(ea, eb))
  adj_e <- split(rep(seq_len(nrow(e)), 2), c(ea, eb))
  parent <- integer(k); pedge <- integer(k); seen <- logical(k)
  order_ <- integer(k); head <- 1L; tail <- 1L
  order_[1] <- 1L; seen[1] <- TRUE
  while (head <= tail) {
    u  <- order_[head]; head <- head + 1L
    nb <- adj_n[[as.character(u)]]
    ne <- adj_e[[as.character(u)]]
    for (q in seq_along(nb)) {
      w <- nb[q]
      if (!seen[w]) {
        seen[w] <- TRUE; parent[w] <- u; pedge[w] <- ne[q]
        tail <- tail + 1L; order_[tail] <- w
      }
    }
  }
  list(order = order_[seq_len(tail)], parent = parent, pedge = pedge)
}

# Column sums of matrix m over each node's subtree
subtree_sums <- function(m, order_, parent) {
  for (q in rev(order_[-1])) m[parent[q], ] <- m[parent[q], ] + m[q, ]
  m
}

# Logical membership of the subtree rooted at node c
subtree_members <- function(c, order_, parent) {
  inside <- logical(length(parent))
  inside[c] <- TRUE
  for (q in order_) if (q != c && parent[q] > 0 && inside[parent[q]]) inside[q] <- TRUE
  inside
}

# -----------------------------------------------------------------------------
# SECTION 3. Steps 1-2: segment parts, artifacts, still_small merge
# -----------------------------------------------------------------------------

# Step 1: single-part segments with seg_part_ID
segment_parts <- function(seg) {
  seg <- clean_polygons(seg)
  seg <- suppressWarnings(st_cast(seg, "POLYGON"))
  seg$seg_part_ID      <- seq_len(nrow(seg))
  seg$seg_part_area_m2 <- area_m2(seg)
  relocate(seg, seg_part_ID, seg_fid, seg_part_area_m2)
}

# Step 2: drop polygons smaller than tiny_m2
drop_artifacts <- function(x, p) {
  a    <- area_m2(x)
  tiny <- a < p$tiny_m2
  if (any(tiny))
    log_msg("Artifact polygons dropped (< ", p$tiny_m2, " m2): ", sum(tiny),
            " (total ", signif(sum(a[tiny]), 3), " m2)")
  x[!tiny, ]
}

# Step 2: merge still_small polygons into the neighbor with the longest edge
merge_still_small <- function(x, p, fields) {
  x$n_merged <- if ("n_merged" %in% names(x)) coalesce(as.integer(x$n_merged), 1L) else 1L
  if (!"still_small" %in% names(x)) return(x)
  
  n  <- nrow(x)
  g  <- st_geometry(x)
  z  <- if (zone_field %in% names(x)) x[[zone_field]] else rep(NA, n)
  ss <- which(coalesce(as.logical(x$still_small), FALSE))
  if (length(ss) == 0) return(x)
  ss <- ss[order(!is.na(z[ss]), x$area_m2[ss])]     # no-zone polygons first
  nb <- within_dist(g[ss], g, p$edge_tol_m)
  grp <- seq_len(n)
  
  # Target: longest shared edge within the most preferred zone tier
  pick <- function(i, shared) {
    tg <- as.integer(names(shared))
    for (tier in zone_tiers(z[i], z[tg])) if (length(tier))
      return(tg[tier][which.max(shared[tier])])
  }
  
  # Shared edge length per neighboring group, at tolerance tol
  edge_neighbors <- function(i, js, tol) {
    js <- js[grp[js] != grp[i]]
    if (length(js) == 0) return(NULL)
    len  <- vapply(js, function(j) edge_length(g[i], g[j], tol), numeric(1))
    keep <- len >= p$min_edge_m
    if (!any(keep)) return(NULL)
    tapply(len[keep], grp[js[keep]], sum)
  }
  
  gap_grp  <- integer(0)
  unmerged <- 0L
  for (k in seq_along(ss)) {
    i <- ss[k]
    shared <- edge_neighbors(i, setdiff(nb[[k]], i), p$edge_tol_m)
    if (is.null(shared)) {
      nb2    <- setdiff(within_dist(g[i], g, p$gap_tol_m)[[1]], i)
      shared <- edge_neighbors(i, nb2, p$gap_tol_m)
      if (is.null(shared)) { unmerged <- unmerged + 1L; next }
      gap_grp <- c(gap_grp, pick(i, shared))
    }
    target <- pick(i, shared)
    grp[grp == grp[i]] <- target
    if (is.na(z[target])) z[target] <- z[i]
  }
  
  fields <- intersect(fields, names(x))
  agg <- st_drop_geometry(x)[c("n_merged", fields)]
  agg$.grp <- grp
  agg <- agg |>
    group_by(.grp) |>
    summarise(n_merged = sum(n_merged), across(all_of(fields), sum_na), .groups = "drop")
  
  rep <- which(grp == seq_len(n))
  out <- x[rep, ]
  m   <- match(rep, agg$.grp)
  for (f in c("n_merged", fields)) out[[f]] <- agg[[f]][m]
  
  # Heal merged polygons; groups merged across a gap are closed with radius
  # gap_tol_m so the gap becomes part of the merged polygon
  gap_rep <- unique(grp[gap_grp])
  for (r in which(out$n_merged > 1)) {
    hp <- heal_params
    if (rep[r] %in% gap_rep) hp$heal_m <- p$gap_tol_m
    st_geometry(out)[r] <- heal_polygons(st_union(g[grp == rep[r]]), hp)
  }
  
  log_msg("still_small polygons: ", length(ss), "; merged: ", length(ss) - unmerged,
          " (", length(gap_grp), " across a gap <= ", p$gap_tol_m, " m); no neighbor: ",
          unmerged, "; blocks: ", n, " -> ", nrow(out))
  reassign_fragments(out, which(out$n_merged > 1), p)
}

# Move detached parts (all but the largest) of the given features to the
# adjacent feature sharing their longest edge; parts with no edge neighbor
# stay in place. Attributes are unchanged; area_m2 is remeasured.
reassign_fragments <- function(x, rows, p) {
  gcol <- attr(x, "sf_column")
  rows <- rows[vapply(x[[gcol]][rows], length, integer(1)) > 1]
  if (length(rows) == 0) return(x)
  
  moved <- 0L
  kept  <- 0L
  for (r in rows) {
    parts <- st_cast(x[[gcol]][r], "POLYGON")
    main  <- which.max(area_m2(parts))
    stay  <- parts[main]
    for (k in setdiff(seq_along(parts), main)) {
      frag <- parts[k]
      nb   <- setdiff(within_dist(frag, x[[gcol]], p$edge_tol_m)[[1]], r)
      len  <- vapply(nb, function(j) edge_length(frag, x[[gcol]][j], p$edge_tol_m), numeric(1))
      if (length(nb) == 0 || max(len) < p$min_edge_m) {
        stay <- c(stay, frag)
        kept <- kept + 1L
        next
      }
      t <- nb[which.max(len)]
      x[[gcol]][t] <- heal_polygons(st_union(c(x[[gcol]][t], frag)))
      moved <- moved + 1L
    }
    x[[gcol]][r] <- heal_polygons(st_union(stay))
  }
  if ("area_m2" %in% names(x)) x$area_m2 <- area_m2(x)
  log_msg("Detached fragments: ", moved, " reassigned; ", kept, " left in place")
  x
}

# -----------------------------------------------------------------------------
# SECTION 4. Step 3: shared edges, strokes and block adjacency
# -----------------------------------------------------------------------------

# Shared boundaries between blocks as arcs, with the pair of blocks (row
# indices i < j) on either side
block_arcs <- function(blocks, p) {
  g0 <- st_geometry(blocks)
  xy <- st_coordinates(st_segmentize(g0, dfMaxLength = p$densify_m))
  fc <- ncol(xy)
  rs <- do.call(paste, as.data.frame(xy[, 3:fc, drop = FALSE]))
  n  <- nrow(xy)
  rid <- cumsum(c(TRUE, rs[-1] != rs[-n]))          # ring id, in ring order
  a  <- seq_len(n - 1)
  b  <- a + 1L
  ok <- rid[a] == rid[b]
  a  <- a[ok]
  b  <- b[ok]
  
  seg <- data.frame(poly = as.integer(xy[a, fc]), rid = rid[a],
                    x1 = xy[a, 1], y1 = xy[a, 2], x2 = xy[b, 1], y2 = xy[b, 2])
  seg <- seg[!(seg$x1 == seg$x2 & seg$y1 == seg$y2), ]
  m   <- nrow(seg)
  
  # Exact match: rounded endpoints shared by exactly two segments
  d   <- p$digits
  r1  <- paste(round(seg$x1, d), round(seg$y1, d))
  r2  <- paste(round(seg$x2, d), round(seg$y2, d))
  key <- ifelse(r1 < r2, paste(r1, r2), paste(r2, r1))
  o   <- order(key)
  ks  <- key[o]
  same_next  <- c(ks[-1] == ks[-m], FALSE)
  same_prev  <- c(FALSE, ks[-1] == ks[-m])
  same_next2 <- c(ks[-(1:2)] == ks[-c(m - 1, m)], FALSE, FALSE)
  start <- which(same_next & !same_prev & !same_next2)
  pa <- o[start]
  pb <- o[start + 1]
  dp <- seg$poly[pa] != seg$poly[pb]
  nb <- rep(NA_integer_, m)
  nb[pa[dp]] <- seg$poly[pb[dp]]
  nb[pb[dp]] <- seg$poly[pa[dp]]
  n_exact <- sum(!is.na(nb))
  
  log_msg("Boundary segments: ", m, "; shared by exact match: ", n_exact)
  
  # Proximity match: the midpoint of each unmatched segment is compared with
  # the other unmatched segments (a shared stretch that failed the exact
  # match is unmatched on both sides); the nearest segment of another block
  # within edge_snap_m gives the neighbor
  um <- which(is.na(nb))
  if (length(um)) {
    lines <- st_sfc(lapply(um, function(k)
      st_linestring(matrix(c(seg$x1[k], seg$x2[k], seg$y1[k], seg$y2[k]), 2))),
      crs = st_crs(blocks))
    mx <- (seg$x1[um] + seg$x2[um]) / 2
    my <- (seg$y1[um] + seg$y2[um]) / 2
    mid  <- st_as_sf(data.frame(x = mx, y = my), coords = c("x", "y"), crs = st_crs(blocks))
    hits <- within_dist(mid, lines, p$edge_snap_m)
    hk <- rep(seq_along(hits), lengths(hits))       # query (midpoint) index
    hs <- unlist(hits)                              # candidate segment index
    other <- seg$poly[um[hs]] != seg$poly[um[hk]]
    hk <- hk[other]
    hs <- hs[other]
    if (length(hk)) {
      # Point-to-segment distance, vectorized
      ax <- seg$x1[um[hs]]; ay <- seg$y1[um[hs]]
      dx <- seg$x2[um[hs]] - ax; dy <- seg$y2[um[hs]] - ay
      t  <- pmin(1, pmax(0, ((mx[hk] - ax) * dx + (my[hk] - ay) * dy) / (dx^2 + dy^2)))
      d  <- sqrt((mx[hk] - ax - t * dx)^2 + (my[hk] - ay - t * dy)^2)
      o  <- order(hk, d)
      f  <- o[!duplicated(hk[o])]
      nb[um[hk[f]]] <- seg$poly[um[hs[f]]]
    }
  }
  log_msg("Shared by proximity (<= ", p$edge_snap_m, " m): ", sum(!is.na(nb)) - n_exact,
          "; not shared: ", sum(is.na(nb)))
  
  # Runs of consecutive segments with the same neighbor (lower index side)
  lab <- ifelse(!is.na(nb) & seg$poly < nb, nb, 0L)
  run <- cumsum(c(TRUE, seg$rid[-1] != seg$rid[-m] | lab[-1] != lab[-m]))
  pos <- seq_len(m)
  rf  <- which(!duplicated(seg$rid))
  rl  <- which(!duplicated(seg$rid, fromLast = TRUE))
  w   <- which(lab[rf] == lab[rl] & lab[rf] != 0 & run[rf] != run[rl])
  if (length(w)) {                                  # join runs across ring start
    remap <- setNames(run[rf[w]], run[rl[w]])
    idx   <- which(run %in% run[rl[w]])
    pos[idx] <- pos[idx] - m
    run[idx] <- remap[as.character(run[idx])]
  }
  keep <- which(lab != 0)
  keep <- keep[order(run[keep], pos[keep])]
  runs <- split(keep, run[keep])
  
  geoms <- lapply(runs, function(s)
    st_linestring(cbind(c(seg$x1[s], seg$x2[s[length(s)]]),
                        c(seg$y1[s], seg$y2[s[length(s)]]))))
  first <- vapply(runs, `[`, integer(1), 1)
  arcs  <- st_sfc(geoms, crs = st_crs(blocks))
  out <- st_sf(arc_id = seq_along(arcs), block_i = seg$poly[first], block_j = lab[first],
               arc_len_m = as.numeric(st_length(arcs)), geometry = arcs)
  log_msg("Shared arcs: ", nrow(out))
  out
}

# Strokes: chain arcs through junctions by smallest deflection
build_strokes <- function(arcs, p) {
  ac  <- st_coordinates(arcs)
  L1  <- ac[, "L1"]
  sl  <- c(0, sqrt(diff(ac[, 1])^2 + diff(ac[, 2])^2))
  sl[!duplicated(L1)] <- 0
  d_s <- ave(sl, L1, FUN = cumsum)                  # distance from start
  d_e <- ave(d_s, L1, FUN = max) - d_s              # distance from end
  idx <- seq_len(nrow(ac))
  first <- idx[!duplicated(L1)]
  last  <- idx[!duplicated(L1, fromLast = TRUE)]
  n_arc <- length(first)
  
  # Vertex at stroke_look_m from each end (or the far end if shorter)
  cs <- idx[d_s >= p$stroke_look_m]
  ts <- last
  if (length(cs)) { v <- tapply(cs, L1[cs], min); ts[as.integer(names(v))] <- v }
  ce <- idx[d_e >= p$stroke_look_m]
  te <- first
  if (length(ce)) { v <- tapply(ce, L1[ce], max); te[as.integer(names(v))] <- v }
  
  unit <- function(dx, dy) { l <- sqrt(dx^2 + dy^2); l[l == 0] <- 1; cbind(dx / l, dy / l) }
  us <- unit(ac[ts, 1] - ac[first, 1], ac[ts, 2] - ac[first, 2])
  ue <- unit(ac[te, 1] - ac[last, 1],  ac[te, 2] - ac[last, 2])
  
  # Junctions: arc ends within edge_snap_m of each other
  ex   <- c(ac[first, 1], ac[last, 1])
  ey   <- c(ac[first, 2], ac[last, 2])
  np   <- near_pairs(ex, ey, p$edge_snap_m)
  node <- uf_components(length(ex), np$i, np$j)
  
  ends <- data.frame(arc = rep(seq_len(n_arc), 2), node = node,
                     dx = c(us[, 1], ue[, 1]), dy = c(us[, 2], ue[, 2]))
  ends$end_id <- seq_len(nrow(ends))
  
  # Candidate continuations at each junction, best (straightest) first
  pr <- inner_join(ends, ends, by = "node", suffix = c("_a", "_b"),
                   relationship = "many-to-many") |>
    filter(end_id_a < end_id_b, arc_a != arc_b) |>
    mutate(deflect = acos(pmin(1, pmax(-1, -(dx_a * dx_b + dy_a * dy_b)))) * 180 / pi) |>
    filter(deflect <= p$stroke_max_deflect) |>
    arrange(deflect)
  
  # Greedy pairing: each arc end continues at most one other arc end
  used <- logical(nrow(ends))
  keep <- logical(nrow(pr))
  ea <- pr$end_id_a
  eb <- pr$end_id_b
  for (k in seq_len(nrow(pr))) {
    if (!used[ea[k]] && !used[eb[k]]) {
      used[ea[k]] <- TRUE
      used[eb[k]] <- TRUE
      keep[k]     <- TRUE
    }
  }
  pr <- pr[keep, ]
  
  stroke <- uf_components(n_arc, pr$arc_a, pr$arc_b)
  arcs$stroke_id    <- match(stroke, unique(stroke))
  arcs$stroke_len_m <- ave(arcs$arc_len_m, arcs$stroke_id, FUN = sum)
  log_msg("Strokes: ", length(unique(stroke)), "; longest ",
          round(max(arcs$stroke_len_m)), " m; median ",
          round(median(tapply(arcs$arc_len_m, arcs$stroke_id, sum))), " m")
  arcs
}

# Block adjacency: shared boundary length and break strength per block pair
# (block row indices)
block_pairs <- function(arcs, p) {
  st_drop_geometry(arcs) |>
    group_by(block_i, block_j) |>
    summarise(len = sum(arc_len_m), s = max(stroke_len_m), .groups = "drop") |>
    filter(len >= p$min_edge_m) |>
    as.data.frame()
}

# -----------------------------------------------------------------------------
# SECTION 5. Step 4: assignment to original segments
# -----------------------------------------------------------------------------

# Step 4a: assign by overlap; nearest segment part and distance per block
assign_by_overlap <- function(blocks, seg, cent, p) {
  n     <- nrow(blocks)
  share <- numeric(n)
  part  <- rep(NA_integer_, n)
  
  # Blocks wholly within one segment part
  w <- st_within(blocks, seg)
  inside <- lengths(w) == 1
  share[inside] <- 1
  part[inside]  <- seg$seg_part_ID[unlist(w[inside])]
  
  # Other blocks: intersect with segment parts
  rest <- which(!inside)
  if (length(rest)) {
    b <- st_sf(.row = rest, geometry = st_geometry(blocks)[rest])
    s <- seg["seg_part_ID"]
    st_agr(b) <- "constant"
    st_agr(s) <- "constant"
    inter <- suppressWarnings(st_intersection(b, s))
    if (nrow(inter)) {
      inter$a <- area_m2(inter)
      best <- st_drop_geometry(inter) |>
        group_by(.row) |>
        slice_max(a, n = 1, with_ties = FALSE) |>
        ungroup()
      share[best$.row] <- best$a / blocks$area_m2[best$.row]
      part[best$.row]  <- best$seg_part_ID
    }
  }
  assigned <- share >= p$overlap_min_share
  
  blocks$seg_overlap_share <- pmin(share, 1)
  blocks$seg_part_ID       <- ifelse(assigned, part, NA_integer_)
  blocks$seg_assign        <- ifelse(assigned, "overlap", NA_character_)
  
  nf <- st_nearest_feature(cent, seg)
  blocks$seg_part_nearest <- seg$seg_part_ID[nf]
  blocks$dist_to_seg_part <- as.numeric(st_distance(cent, st_geometry(seg)[nf],
                                                    by_element = TRUE))
  log_msg("Blocks assigned by overlap: ", sum(assigned), " of ", n,
          " (overlap >= ", 100 * p$overlap_min_share, "%)")
  blocks
}

# Steps 4b-4c: assign by longest shared edge; then clusters bordered by a
# single segment
assign_by_adjacency <- function(blocks, pairs, p) {
  bp <- rbind(data.frame(u = pairs$block_i, v = pairs$block_j, len = pairs$len),
              data.frame(u = pairs$block_j, v = pairs$block_i, len = pairs$len))
  seg <- blocks$seg_part_ID
  
  # 4b: longest shared edge
  n_edge <- 0L
  for (pass in seq_len(p$edge_assign_passes)) {
    fixed <- seg
    cand  <- bp[is.na(fixed[bp$u]) & !is.na(fixed[bp$v]), ]
    if (nrow(cand) == 0) break
    cand$seg <- fixed[cand$v]
    best <- cand |>
      group_by(u, seg) |>
      summarise(len = sum(len), .groups = "drop") |>
      group_by(u) |>
      slice_max(len, n = 1, with_ties = FALSE) |>
      ungroup()
    seg[best$u] <- best$seg
    blocks$seg_assign[best$u] <- "edge"
    n_edge <- n_edge + nrow(best)
  }
  
  # 4c: clusters bordered by a single segment
  un    <- which(is.na(seg))
  n_enc <- 0L
  if (length(un)) {
    loc  <- integer(length(seg)); loc[un] <- seq_along(un)
    uu   <- bp[is.na(seg[bp$u]) & is.na(seg[bp$v]), ]
    comp <- uf_components(length(un), loc[uu$u], loc[uu$v])
    ext  <- bp[is.na(seg[bp$u]) & !is.na(seg[bp$v]), ]
    ext  <- distinct(data.frame(comp = comp[loc[ext$u]], seg = seg[ext$v]))
    nseg <- table(ext$comp)
    single <- as.integer(names(nseg)[nseg == 1])
    if (length(single)) {
      comp_area <- tapply(blocks$area_m2[un], comp, sum)
      seg_area  <- tapply(blocks$area_m2[!is.na(seg)], seg[!is.na(seg)], sum)
      tgt <- ext$seg[match(single, ext$comp)]
      ok  <- comp_area[as.character(single)] + seg_area[as.character(tgt)] <=
        p$hard_max_ha * 1e4
      for (k in which(ok)) {
        rows <- un[comp == single[k]]
        seg[rows] <- tgt[k]
        blocks$seg_assign[rows] <- "enclosed"
        n_enc <- n_enc + length(rows)
      }
    }
  }
  
  assigned <- !is.na(seg)
  blocks$seg_part_ID <- seg
  blocks$seg_source  <- ifelse(assigned, "original", "new")
  blocks$seg_assign[!assigned] <- "new"
  log_msg("Blocks assigned by longest shared edge: ", n_edge,
          "; as clusters bordered by one segment: ", n_enc,
          "; unassigned: ", sum(!assigned))
  blocks
}

# -----------------------------------------------------------------------------
# SECTION 6. Splitting helpers (Step 6a-b)
# -----------------------------------------------------------------------------

# Recursively split one tree (nodes, tree edges) into regions
split_tree <- function(nodes, te, area, bldg, p) {
  tgt_a <- p$target_ha * 1e4
  max_a <- p$hard_max_ha * 1e4
  min_a <- p$min_piece_ha * 1e4
  load  <- function(a, b) pmax(a / tgt_a, b / p$bldg_soft_max)
  
  out   <- list()
  stack <- list(list(nodes = nodes, te = te))
  while (length(stack)) {
    cur   <- stack[[length(stack)]]
    stack <- stack[-length(stack)]
    v <- cur$nodes
    e <- cur$te
    A <- sum(area[v])
    B <- sum(bldg[v])
    if ((A <= tgt_a && B <= p$bldg_soft_max) || nrow(e) == 0) {
      out[[length(out) + 1]] <- v
      next
    }
    
    rt   <- root_tree(v, e)
    sums <- subtree_sums(cbind(area[v], bldg[v]), rt$order, rt$parent)
    
    # Candidate cuts: the tree edge above each non-root node
    child <- rt$order[-1]
    a_in  <- sums[child, 1]; b_in <- sums[child, 2]
    a_out <- A - a_in;       b_out <- B - b_in
    ok <- if (A > max_a) rep(TRUE, length(child)) else (a_in >= min_a & a_out >= min_a)
    if (!any(ok)) {
      out[[length(out) + 1]] <- v
      next
    }
    s   <- e$s[rt$pedge[child]]
    la  <- load(a_in, b_in)
    lb  <- load(a_out, b_out)
    sc  <- (s / max(s)) * (2 * pmin(la, lb) / (la + lb)) ^ p$balance_weight
    sc[!ok] <- -Inf
    cnode <- child[which.max(sc)]
    
    inside <- subtree_members(cnode, rt$order, rt$parent)
    e2   <- e[-rt$pedge[cnode], ]
    in_a <- inside[match(e2$a, v)]
    stack[[length(stack) + 1]] <- list(nodes = v[inside],  te = e2[in_a, ])
    stack[[length(stack) + 1]] <- list(nodes = v[!inside], te = e2[!in_a, ])
  }
  out
}

# Normalized moment of inertia from area-weighted sums (centered
# coordinates): columns A, Sx, Sy, Sxx = sum a*(x^2 + y^2), Q = sum a^2
moment_j <- function(S) {
  A <- S[, 1]
  I <- S[, 4] - (S[, 2]^2 + S[, 3]^2) / A + S[, 5] / (2 * pi)
  2 * pi * I / A^2
}

moment_sums <- function(x, y, a) cbind(a, a * x, a * y, a * (x^2 + y^2), a^2)

# Split one segment's blocks into compact parts; returns a list of node sets
split_compact <- function(nodes, te, x, y, a, p) {
  min_a <- p$min_piece_ha * 1e4
  parts <- list(list(nodes = nodes, te = te))
  done  <- FALSE
  jval  <- function(v) moment_j(matrix(colSums(moment_sums(x[v], y[v], a[v])), 1))
  
  while (length(parts) < p$max_parts) {
    js <- vapply(parts, function(pt) jval(pt$nodes), numeric(1))
    as <- vapply(parts, function(pt) sum(a[pt$nodes]), numeric(1))
    cand <- which(js > p$j_max & as >= 2 * min_a & !done)
    if (length(cand) == 0) break
    w <- cand[which.max(js[cand])]
    v <- parts[[w]]$nodes
    e <- parts[[w]]$te
    if (nrow(e) == 0) { done[w] <- TRUE; next }
    
    rt    <- root_tree(v, e)
    S     <- subtree_sums(moment_sums(x[v], y[v], a[v]), rt$order, rt$parent)
    child <- rt$order[-1]
    S_in  <- S[child, , drop = FALSE]
    S_out <- sweep(-S_in, 2, S[rt$order[1], ], "+")
    a_in  <- S_in[, 1]
    a_out <- S_out[, 1]
    ok    <- a_in >= min_a & a_out >= min_a
    if (!any(ok)) { done[w] <- TRUE; next }
    
    jw   <- (a_in * moment_j(S_in) + a_out * moment_j(S_out)) / (a_in + a_out)
    gain <- (js[w] - jw) / js[w]
    s    <- e$s[rt$pedge[child]]
    sc   <- gain * (s / max(s)) ^ p$strength_weight
    sc[!ok | gain <= 0] <- -Inf
    if (all(!is.finite(sc))) { done[w] <- TRUE; next }
    
    cnode  <- child[which.max(sc)]
    inside <- subtree_members(cnode, rt$order, rt$parent)
    e2     <- e[-rt$pedge[cnode], ]
    in_a   <- inside[match(e2$a, v)]
    parts[[w]] <- list(nodes = v[inside], te = e2[in_a, ])
    parts[[length(parts) + 1]] <- list(nodes = v[!inside], te = e2[!in_a, ])
    done <- c(replace(done, w, FALSE), FALSE)
  }
  lapply(parts, `[[`, "nodes")
}

# -----------------------------------------------------------------------------
# SECTION 7. Steps 5-6: segment labels and refinement
# -----------------------------------------------------------------------------
# Segments are handled as integer labels on blocks (L). Block data `blk`:
# a = area (m2), b = building count, cx, cy = centroid (city-centered), z = zone.

# Step 5: initial new segments from unassigned blocks (seg_ids NA).
#   1. unassigned blocks with a zone form connected clusters of one zone
#   2. unassigned blocks without a zone that touch such a cluster join the
#      cluster with which they share the longest edge
#   3. remaining blocks without a zone form connected clusters
# Returns seg_ids with unassigned blocks labelled 1..k.
initial_segments <- function(seg_ids, zone, pairs) {
  n  <- length(seg_ids)
  un <- which(is.na(seg_ids))
  if (length(un) == 0) return(seg_ids)
  bi <- pairs$block_i
  bj <- pairs$block_j
  is_un <- logical(n); is_un[un] <- TRUE
  lab <- rep(NA_integer_, n)
  
  cluster <- function(rows, keep) {
    loc  <- integer(n); loc[rows] <- seq_along(rows)
    comp <- uf_components(length(rows), loc[bi[keep]], loc[bj[keep]])
    match(comp, unique(comp))
  }
  
  # 1. Zoned clusters
  zr <- un[!is.na(zone[un])]
  if (length(zr)) {
    keep <- is_un[bi] & is_un[bj] & !is.na(zone[bi]) & !is.na(zone[bj]) & zone[bi] == zone[bj]
    lab[zr] <- cluster(zr, keep)
  }
  
  # 2. No-zone blocks touching a zoned cluster
  bp <- rbind(data.frame(u = bi, v = bj, len = pairs$len),
              data.frame(u = bj, v = bi, len = pairs$len))
  c2 <- bp[is_un[bp$u] & is.na(zone[bp$u]) & !is.na(lab[bp$v]), ]
  n2 <- 0L
  if (nrow(c2)) {
    c2$lab <- lab[c2$v]
    best <- c2 |>
      group_by(u, lab) |>
      summarise(len = sum(len), .groups = "drop") |>
      group_by(u) |>
      slice_max(len, n = 1, with_ties = FALSE)
    lab[best$u] <- best$lab
    n2 <- nrow(best)
  }
  
  # 3. Remaining no-zone clusters
  rest <- un[is.na(lab[un])]
  if (length(rest)) {
    is_r <- logical(n); is_r[rest] <- TRUE
    lab[rest] <- max(c(0L, lab), na.rm = TRUE) + cluster(rest, is_r[bi] & is_r[bj])
  }
  
  log_msg("Initial new segments: ", length(unique(lab[un])), " (", length(zr),
          " zoned blocks; ", n2, " no-zone blocks joined to a zoned cluster; ",
          length(rest), " no-zone blocks in no-zone clusters)")
  seg_ids[un] <- lab[un]
  seg_ids
}

# Per-segment statistics (additive sums) for labels L
seg_stats <- function(L, blk) {
  M <- rowsum(cbind(blk$a, blk$b, blk$a * blk$cx, blk$a * blk$cy,
                    blk$a * (blk$cx^2 + blk$cy^2), blk$a^2), L)
  ids <- as.integer(rownames(M))
  zt <- data.frame(L = L, z = blk$z, a = blk$a)
  zt <- zt[!is.na(zt$z), ]
  zone <- rep(NA, length(ids))
  if (nrow(zt)) {
    zt <- zt |>
      group_by(L, z) |>
      summarise(a = sum(a), .groups = "drop") |>
      group_by(L) |>
      slice_max(a, n = 1, with_ties = FALSE)
    zone <- zt$z[match(ids, zt$L)]
  }
  list(id = ids, A = M[, 1], B = M[, 2], S = M[, c(1, 3:6), drop = FALSE], zone = zone)
}

# Segment adjacency (shared edge length per pair of labels)
label_adjacency <- function(L, pairs) {
  si <- L[pairs$block_i]
  sj <- L[pairs$block_j]
  k  <- si != sj
  data.frame(a = pmin(si, sj)[k], b = pmax(si, sj)[k], len = pairs$len[k]) |>
    group_by(a, b) |>
    summarise(len = sum(len), .groups = "drop")
}

# Generic merge of segment labels.
#   cand_ok(i, s)            -> TRUE if segment i (current state s) should merge
#   choose(i, nb, len, s)    -> index of the receiving neighbor, or NA
# State s holds additive sums (A, B, S) and zone per segment index; it is
# updated after each merge. Passes repeat until no merge occurs.
merge_labels <- function(L, blk, pairs, cand_ok, choose, label, max_pass = 20) {
  total <- 0L
  for (pass in seq_len(max_pass)) {
    st  <- seg_stats(L, blk)
    K   <- length(st$id)
    adj <- label_adjacency(L, pairs)
    ai  <- match(adj$a, st$id)
    bi  <- match(adj$b, st$id)
    len <- adj$len
    rows_of <- split(c(seq_along(ai), seq_along(bi)), c(ai, bi))
    s <- list(A = st$A, B = st$B, S = st$S, zone = st$zone)
    
    grp     <- seq_len(K)
    members <- as.list(seq_len(K))
    cand    <- which(vapply(seq_len(K), cand_ok, logical(1), s = s))
    cand    <- cand[order(!is.na(s$zone[cand]), s$A[cand])]   # no-zone first, then smallest
    merged  <- 0L
    for (c in cand) {
      if (grp[c] != c || !cand_ok(c, s)) next
      r <- unique(unlist(rows_of[as.character(members[[c]])], use.names = FALSE))
      if (length(r) == 0) next
      gi  <- grp[ai[r]]
      gj  <- grp[bi[r]]
      hit <- xor(gi == c, gj == c)
      if (!any(hit)) next
      other <- ifelse(gi[hit] == c, gj[hit], gi[hit])
      sh <- rowsum(len[r][hit], other)
      t  <- choose(c, as.integer(rownames(sh)), sh[, 1], s)
      if (is.na(t)) next
      grp[members[[c]]] <- t
      members[[t]] <- c(members[[t]], members[[c]])
      members[[c]] <- integer(0)
      s$A[t] <- s$A[t] + s$A[c]
      s$B[t] <- s$B[t] + s$B[c]
      s$S[t, ] <- s$S[t, ] + s$S[c, ]
      if (is.na(s$zone[t])) s$zone[t] <- s$zone[c]
      s$A[c] <- NA
      merged <- merged + 1L
    }
    if (merged == 0) break
    L <- st$id[grp[match(L, st$id)]]
    total <- total + merged
  }
  log_msg("  ", label, ": ", total, " merged")
  L
}

# Generic split of segment labels.
#   select   : segment IDs to process
#   splitter : function(nodes, te, rows) -> list of node sets (local indices)
# Each segment's blocks are first separated into connected groups (block
# adjacency within the segment); the splitter is applied to each group. The
# largest part keeps the ID; other parts get new IDs and inherit the source.
split_labels <- function(L, blk, pairs, select, splitter, state, label) {
  if (length(select) == 0) {
    log_msg("  ", label, ": 0 split")
    return(list(L = L, state = state))
  }
  lab_rows <- split(seq_along(L), L)
  within   <- L[pairs$block_i] == L[pairs$block_j]
  pw       <- pairs[within, ]
  pw_by    <- split(seq_len(nrow(pw)), L[pw$block_i])
  
  n_split <- 0L
  n_new   <- 0L
  for (id in select) {
    key  <- as.character(id)
    rows <- lab_rows[[key]]
    k    <- pw_by[[key]]
    pr   <- if (is.null(k)) pw[0, ] else pw[k, ]
    te   <- kruskal(length(rows), match(pr$block_i, rows), match(pr$block_j, rows), pr$s)
    comp <- uf_components(length(rows), te$a, te$b)
    ecmp <- comp[te$a]
    pieces <- unlist(lapply(unique(comp), function(cc)
      splitter(which(comp == cc), te[ecmp == cc, ], rows)), recursive = FALSE)
    if (length(pieces) < 2) next
    pieces <- pieces[order(-vapply(pieces, function(v) sum(blk$a[rows[v]]), numeric(1)))]
    for (q in pieces[-1]) {
      L[rows[q]] <- state$next_id
      state$src[as.character(state$next_id)] <- state$src[[key]]
      state$next_id <- state$next_id + 1L
    }
    n_split <- n_split + 1L
    n_new   <- n_new + length(pieces) - 1L
  }
  log_msg("  ", label, ": ", n_split, " split into ", n_split + n_new, " parts")
  list(L = L, state = state)
}

# Step 6: refinement passes
refine_segments <- function(L, blk, pairs, state) {
  p   <- seg_params
  cap <- p$hard_max_ha * 1e4
  splittable <- function(ids) p$split_original | state$src[as.character(ids)] == "new"
  
  # Merge rules
  longest <- function(c, nb, len, s) {
    for (t in nb[order(-len)]) if (s$A[c] + s$A[t] <= cap) return(t)
    NA
  }
  single <- function(c, nb, len, s) {
    if (length(nb) == 1 && s$A[c] + s$A[nb] <= cap) nb else NA
  }
  # Longest edge within the most preferred zone tier (see zone_tiers)
  longest_z <- function(c, nb, len, s) {
    for (tier in zone_tiers(s$zone[c], s$zone[nb])) {
      if (length(tier) == 0) next
      t <- longest(c, nb[tier], len[tier], s)
      if (!is.na(t)) return(t)
    }
    NA
  }
  # Sparse: (1) a zoned neighbor (same zone if the segment has one; any
  # zone if it has none), any density, longest edge; otherwise (2) a sparse
  # no-zone neighbor, then (3) any sparse neighbor, choosing the most
  # compact result. The result must stay <= sparse max_ha.
  sparse <- function(c, nb, len, s) {
    fit  <- s$A[c] + s$A[nb] <= sparse_params$max_ha * 1e4
    dens <- s$B[nb] / s$A[nb] * 1e4
    zc <- s$zone[c]
    zn <- s$zone[nb]
    pref <- if (!is.na(zc)) !is.na(zn) & zn == zc else !is.na(zn)
    ok <- pref & fit
    if (any(ok)) return(nb[ok][which.max(len[ok])])
    for (tier in list(is.na(zn), !is.na(zn))) {
      ok <- tier & fit & dens < sparse_params$density_max
      if (any(ok)) {
        cand <- nb[ok]
        j <- moment_j(sweep(s$S[cand, , drop = FALSE], 2, s$S[c, ], "+"))
        return(cand[which.min(j)])
      }
    }
    NA
  }
  
  for (it in seq_len(p$max_iter)) {
    log_msg("Refinement pass ", it)
    L0 <- L
    
    # (a) Large segments
    st  <- seg_stats(L, blk)
    lim <- if (it == 1) p$target_ha else p$hard_max_ha
    big <- st$id[(st$A > lim * 1e4 | (it == 1 & st$B > p$bldg_soft_max)) & splittable(st$id)]
    res <- split_labels(L, blk, pairs, big,
                        function(nodes, te, rows)
                          split_tree(nodes, te, blk$a[rows], blk$b[rows], p),
                        state, "Large segments")
    L <- res$L; state <- res$state
    
    # (b) Non-compact segments
    st <- seg_stats(L, blk)
    j  <- moment_j(st$S)
    nc <- st$id[j > compact_params$j_max &
                  st$A >= 2 * compact_params$min_piece_ha * 1e4 & splittable(st$id)]
    res <- split_labels(L, blk, pairs, nc,
                        function(nodes, te, rows)
                          split_compact(nodes, te, blk$cx[rows], blk$cy[rows],
                                        blk$a[rows], compact_params),
                        state, "Non-compact segments")
    L <- res$L; state <- res$state
    
    # (c) Disconnected segments
    res <- split_labels(L, blk, pairs, sort(unique(L)),
                        function(nodes, te, rows) list(nodes),
                        state, "Disconnected segments")
    L <- res$L; state <- res$state
    
    # (d) Tiny segments
    L <- merge_labels(L, blk, pairs,
                      function(i, s) !is.na(s$A[i]) && s$A[i] < p$min_seg_ha * 1e4,
                      longest_z, "Tiny segments")
    
    # (e) Donuts: segments with exactly one neighbor
    L <- merge_labels(L, blk, pairs,
                      function(i, s) !is.na(s$A[i]),
                      single, "Single-neighbor segments")
    
    # (f) Sparse segments
    L <- merge_labels(L, blk, pairs,
                      function(i, s) !is.na(s$A[i]) &&
                        s$B[i] / s$A[i] * 1e4 < sparse_params$density_max &&
                        s$A[i] < sparse_params$max_ha * 1e4,
                      sparse, "Sparse segments")
    
    # (g) Stranded segments
    L <- merge_labels(L, blk, pairs,
                      function(i, s) !is.na(s$A[i]) &&
                        s$B[i] < stranded_params$bldg_threshold &&
                        s$A[i] < stranded_params$max_ha * 1e4,
                      longest_z, "Stranded segments")
    
    log_msg("Pass ", it, ": ", length(unique(L)), " segments")
    if (identical(L, L0)) break
  }
  list(L = L, state = state)
}

# -----------------------------------------------------------------------------
# SECTION 8. Step 7: dissolve
# -----------------------------------------------------------------------------

# Number of interior rings per MULTIPOLYGON feature
n_holes <- function(g) {
  vapply(g, function(mp) as.integer(sum(lengths(mp) - 1L)), integer(1))
}

# Fill interior rings that contain none of the points pts
fill_empty_holes <- function(g, pts) {
  hh <- which(n_holes(g) > 0)
  if (length(hh) == 0) return(g)
  ref <- do.call(rbind, lapply(hh, function(i) {
    mp <- g[[i]]
    do.call(rbind, lapply(seq_along(mp), function(k) {
      nr <- length(mp[[k]])
      if (nr > 1) data.frame(f = i, part = k, ring = 2:nr) else NULL
    }))
  }))
  holes <- st_sfc(lapply(seq_len(nrow(ref)), function(r)
    st_polygon(list(g[[ref$f[r]]][[ref$part[r]]][[ref$ring[r]]]))), crs = st_crs(g))
  empty <- lengths(st_intersects(holes, pts)) == 0
  if (!any(empty)) return(g)
  drop <- ref[empty, ]
  for (i in unique(drop$f)) {
    mp <- unclass(g[[i]])
    d  <- drop[drop$f == i, ]
    for (k in unique(d$part)) mp[[k]] <- mp[[k]][-d$ring[d$part == k]]
    g[i] <- st_sfc(st_multipolygon(mp), crs = st_crs(g))
  }
  log_msg("Empty interior rings filled: ", sum(empty))
  g
}

# Dissolve blocks into segments by seg_part_ID
dissolve_segments <- function(blocks, seg, pts, fields) {
  fields <- intersect(setdiff(fields, "area_m2"), names(blocks))
  out <- blocks |>
    group_by(seg_part_ID) |>
    summarise(seg_source = first(seg_source),
              n_blocks   = n(),
              across(all_of(fields), sum_na),
              do_union   = TRUE, .groups = "drop")
  g <- heal_polygons(st_geometry(out))
  st_geometry(out) <- fill_empty_holes(g, pts)
  out$area_m2 <- area_m2(out)
  out$area_ha <- out$area_m2 / 1e4
  out$bldg_per_ha <- coalesce(as.numeric(out$bldg_cnt), 0) / out$area_ha
  
  ref <- st_drop_geometry(seg)[intersect(c("seg_part_ID", "seg_fid", "seg_part_area_m2",
                                           "segment_area_m2"), names(seg))]
  out |>
    left_join(ref, by = "seg_part_ID") |>
    relocate(seg_part_ID, seg_source, any_of("seg_fid"), n_blocks, area_m2, area_ha)
}

# -----------------------------------------------------------------------------
# SECTION 9. Per-city workflow
# -----------------------------------------------------------------------------
process_city <- function(city, segments_file, blocks_file) {
  log_msg("=== ", city, " ===")
  
  paths <- c(segments = file.path(root, segments_file),
             blocks   = file.path(root, blocks_file))
  missing <- paths[!file.exists(paths)]
  if (length(missing))
    stop("Input not found: ", paste(names(missing), missing, sep = " = ", collapse = "; "))
  stopifnot(!blocks_layer %in% c(out_blocks_layer, out_segments_layer, out_breaks_layer))
  
  # Block coverage (defines the working CRS)
  blocks <- read_layer(paths[["blocks"]], blocks_layer) |>
    select(-any_of(drop_fields))
  crs <- st_crs(blocks)
  if (isTRUE(st_is_longlat(blocks)))
    stop("Block coverage must be in a projected CRS: ", paths[["blocks"]])
  log_msg("Blocks: ", nrow(blocks))
  
  # Step 1: original segments to single parts
  seg <- read_layer(paths[["segments"]], segments_layer, fid_column_name = "seg_fid") |>
    st_transform(crs) |>
    segment_parts()
  log_msg("Original segment parts: ", nrow(seg))
  
  # Step 2: artifacts; still_small merge (block geometry is final after this)
  blocks <- drop_artifacts(blocks, merge_params)
  blocks <- merge_still_small(blocks, merge_params, sum_fields)
  blocks$area_m2 <- area_m2(blocks)
  cent <- centroid_fun(st_geometry(blocks))
  cxy  <- st_coordinates(cent)
  pts  <- st_point_on_surface(st_geometry(blocks))
  
  # Step 3: shared edges, strokes, block adjacency (block row indices)
  arcs  <- block_arcs(blocks, seg_params)
  arcs  <- build_strokes(arcs, seg_params)
  pairs <- block_pairs(arcs, seg_params)
  
  # Step 4: assignment to original segments
  blocks <- assign_by_overlap(blocks, seg, cent, seg_params)
  blocks <- assign_by_adjacency(blocks, pairs, seg_params)
  
  # Zone per block; zone boundaries are strong breaks for splitting
  zone <- if (zone_field %in% names(blocks)) blocks[[zone_field]] else rep(NA, nrow(blocks))
  if (!zone_field %in% names(blocks))
    log_msg("WARNING: zone field '", zone_field, "' not found; zone preferences not applied")
  zi <- zone[pairs$block_i]
  zj <- zone[pairs$block_j]
  zb <- !is.na(zi) & !is.na(zj) & zi != zj
  pairs$s[zb] <- pairs$s[zb] + seg_params$zone_break_m
  log_msg("Block pairs across a zone boundary: ", sum(zb))
  
  # Step 5: initial new segments from unassigned blocks
  L <- initial_segments(blocks$seg_part_ID, zone, pairs)
  first_id <- max(seg$seg_part_ID) + 1L
  L[is.na(blocks$seg_part_ID)] <- L[is.na(blocks$seg_part_ID)] + first_id - 1L
  ids   <- sort(unique(L))
  state <- list(next_id = max(ids) + 1L,
                src = setNames(ifelse(ids < first_id, "original", "new"), ids))
  log_msg("Initial segments: ", length(ids), " (", sum(ids < first_id), " original, ",
          sum(ids >= first_id), " new)")
  
  # Step 6: refinement
  blk <- list(a = blocks$area_m2,
              b = coalesce(as.numeric(blocks$bldg_cnt), 0),
              cx = cxy[, 1] - mean(cxy[, 1]),
              cy = cxy[, 2] - mean(cxy[, 2]),
              z = zone)
  ref   <- refine_segments(L, blk, pairs, state)
  L     <- ref$L
  state <- ref$state
  blocks$seg_part_ID <- L
  blocks$seg_source  <- unname(state$src[as.character(L)])
  
  # Step 7: dissolve and attributes
  segments <- dissolve_segments(blocks, seg, pts, sum_fields)
  st <- seg_stats(L, blk)
  m  <- match(segments$seg_part_ID, st$id)
  segments$zone_id       <- st$zone[m]
  segments$compact_j     <- moment_j(st$S)[m]
  perim <- as.numeric(st_length(st_cast(st_geometry(segments), "MULTILINESTRING")))
  segments$polsby_popper <- 4 * pi * segments$area_m2 / perim^2
  segments$n_holes       <- n_holes(st_geometry(segments))
  segments <- arrange(segments, seg_part_ID)
  blocks   <- relocate(blocks, any_of(c("block_id_coverAOI", "source")),
                       seg_part_ID, seg_source, seg_assign, seg_overlap_share,
                       seg_part_nearest, dist_to_seg_part)
  
  arcs$block_i <- blocks$block_id_coverAOI[arcs$block_i]
  arcs$block_j <- blocks$block_id_coverAOI[arcs$block_j]
  
  # Summary
  a <- segments$area_ha
  log_msg("Final segments: ", nrow(segments), " (", sum(segments$seg_source == "original"),
          " original, ", sum(segments$seg_source == "new"), " new)")
  log_msg("Area (ha): median ", round(median(a), 1), "; > ", seg_params$target_ha, ": ",
          sum(a > seg_params$target_ha), "; > ", seg_params$hard_max_ha, ": ",
          sum(a > seg_params$hard_max_ha), "; < ", seg_params$min_seg_ha, ": ",
          sum(a < seg_params$min_seg_ha))
  log_msg("Segments with holes: ", sum(segments$n_holes > 0), "; compact_j > ",
          compact_params$j_max, ": ", sum(segments$compact_j > compact_params$j_max),
          "; < ", stranded_params$bldg_threshold, " buildings: ",
          sum(coalesce(as.numeric(segments$bldg_cnt), 0) < stranded_params$bldg_threshold))
  
  # Step 8: write
  out <- paths[["blocks"]]
  st_write(blocks,   out, layer = out_blocks_layer,   delete_layer = TRUE, quiet = TRUE)
  st_write(segments, out, layer = out_segments_layer, delete_layer = TRUE, quiet = TRUE)
  st_write(arcs,     out, layer = out_breaks_layer,   delete_layer = TRUE, quiet = TRUE)
  log_msg("Written layers '", out_blocks_layer, "', '", out_segments_layer, "', '",
          out_breaks_layer, "' to: ", out)
  
  invisible(out)
}

# -----------------------------------------------------------------------------
# SECTION 10. Run
# -----------------------------------------------------------------------------
stopifnot(all(run_cities %in% cities$city))

for (cty in run_cities) {
  cfg <- cities[cities$city == cty, ]
  process_city(cfg$city, cfg$segments_file, cfg$blocks_file)
  gc()
}