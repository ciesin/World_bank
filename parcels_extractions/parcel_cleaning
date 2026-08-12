import arcpy
from arcpy.sa import Reclassify, Expand, ExtractByAttributes
import os

def get_poly(gdb,ras):
    arcpy.env.overwriteOutput = True
    # Create polygons from sam3 output
    ex = f"{gdb}\\sam3_ex"
    with arcpy.EnvManager(scratchWorkspace=gdb):
        out_raster = arcpy.sa.ExtractByAttributes(
            in_raster=ras,
            where_clause="VALUE > 0"
        )
    out_raster.save(ex)

    sam_poly = f"{gdb}\\mask_sam3_poly"
    
    arcpy.conversion.RasterToPolygon(
        in_raster=ex,
        out_polygon_features=sam_poly,
        simplify="NO_SIMPLIFY",
        raster_field="Value",
        create_multipart_features="SINGLE_OUTER_PART",
        max_vertices_per_feature=None
    )

    print(f"polygons generated for {ras}")

    return(sam_poly)

    
def process_sam3_parcels(gdb, ras, poly):


    arcpy.env.overwriteOutput = True

    # 1. Expand raster to increase gaps between adjacent parcels

    reclass = arcpy.sa.Reclassify(
        in_raster=ras,
        reclass_field="Value",
        remap="0 0;1 154 1",
        missing_values="DATA"
    )

    reclass_path = f"{gdb}\\sam3_parcel_reclass"
    reclass.save(reclass_path)

    with arcpy.EnvManager(scratchWorkspace=gdb):

        expand_1 = arcpy.sa.Expand(
            in_raster=reclass_path,
            number_cells=10,
            zone_values=[0],
            expand_method="MORPHOLOGICAL"
        )

        expand_1_path = f"{gdb}\\Expand_sam_2"
        expand_1.save(expand_1_path)

    with arcpy.EnvManager(scratchWorkspace=gdb):

        expand_2 = arcpy.sa.Expand(
            in_raster=expand_1_path,
            number_cells=8,
            zone_values=[1],
            expand_method="MORPHOLOGICAL"
        )

        expand_2_path = f"{gdb}\\Expand_sam_2_2"
        expand_2.save(expand_2_path)

    with arcpy.EnvManager(scratchWorkspace=gdb):

        extracted = arcpy.sa.ExtractByAttributes(
            in_raster=expand_2_path,
            where_clause="Value = 1"
        )

        extracted_path = f"{gdb}\\sam3_expand_ex1"
        extracted.save(extracted_path)



    raster_polygon = f"{gdb}\\mask_sam3_expand1"

    # 2. Convert expanded raster to polygons - use these as a mask to dissolve original polygons

    arcpy.conversion.RasterToPolygon(
        in_raster=extracted_path,
        out_polygon_features=raster_polygon,
        simplify="NO_SIMPLIFY",
        raster_field="Value",
        create_multipart_features="SINGLE_OUTER_PART",
        max_vertices_per_feature=None
    )


    arcpy.management.AlterField(
        in_table=raster_polygon,
        field="Id",
        new_field_name="dissolve_id",
        new_field_alias="dissolve_id"
    )

    spatial_join = os.path.join(gdb, "parcel_spatial_join")

    arcpy.analysis.SpatialJoin(
        target_features=poly,
        join_features=raster_polygon,
        out_feature_class=spatial_join,
        join_operation="JOIN_ONE_TO_ONE",
        join_type="KEEP_ALL",
        match_option="INTERSECT"
)


    dissolved = f"{gdb}\\parcel_dissolve"

    arcpy.analysis.PairwiseDissolve(
        in_features=spatial_join,
        out_feature_class=dissolved,
        dissolve_field="dissolve_id",
        statistics_fields=None,
        multi_part="SINGLE_PART",
        concatenation_separator="",
        out_lineage_table=None
    )


    # 3. Clean dissolved goemetries by converting to minimum bounding convex hull, eliminating small pieces, then converting to minimum bounding rectangles

    min_bound = f"{gdb}\\parcel_minimum_bounding"

    arcpy.management.MinimumBoundingGeometry(
        in_features=dissolved,
        out_feature_class=min_bound,
        geometry_type="CONVEX_HULL",
        group_option="NONE",
        group_field=None,
        mbg_fields_option="NO_MBG_FIELDS"
    )

    small_polygons = arcpy.management.SelectLayerByAttribute(
        in_layer_or_view=min_bound,
        selection_type="NEW_SELECTION",
        where_clause="Shape_Area < 300"
    )

    elim1 = f"{gdb}\\bound_eliminated1"

    arcpy.management.Eliminate(
        in_features=small_polygons,
        out_feature_class=elim1,
        selection="LENGTH",
        ex_where_clause="",
        ex_features=None
    )

    small_polygons = arcpy.management.SelectLayerByAttribute(
        in_layer_or_view=elim1,
        selection_type="NEW_SELECTION",
        where_clause="Shape_Area < 300"
    )

    elim2 = f"{gdb}\\bound_eliminated2"

    arcpy.management.Eliminate(
        in_features=small_polygons,
        out_feature_class=elim2,
        selection="LENGTH",
        ex_where_clause="",
        ex_features=None
    )

    min_bound1 = f"{gdb}\\parcel_minimum_bounding1"
    arcpy.management.MinimumBoundingGeometry(
        in_features=elim2,
        out_feature_class=min_bound1,
        geometry_type="RECTANGLE_BY_AREA",
        group_option="NONE",
        group_field=None,
        mbg_fields_option="NO_MBG_FIELDS"
    )

    # Process minimum bounding geometries by deleting identicals, converting to singlepart, and removing overlaps by merging them

    unique_polygons = f"{gdb}\\parcel_unique"

    arcpy.management.DeleteIdentical(
        in_dataset=min_bound1,
        fields="Shape",
        xy_tolerance=None,
        z_tolerance=0,
        out_mapping_table=None
    )

    singlepart = f"{gdb}\\parcel_singlepart"

    arcpy.management.MultipartToSinglepart(
        in_features=min_bound1,
        out_feature_class=singlepart
    )

    singlepart_layer = "parcel_singlepart_layer"

    arcpy.management.MakeFeatureLayer(
        singlepart,
        singlepart_layer
    )

    small_polygons = arcpy.management.SelectLayerByAttribute(
        in_layer_or_view=singlepart_layer,
        selection_type="NEW_SELECTION",
        where_clause="Shape_Area < 300"
    )

    eliminated = f"{gdb}\\parcel_eliminated"

    arcpy.management.Eliminate(
        in_features=small_polygons,
        out_feature_class=eliminated,
        selection="LENGTH",
        ex_where_clause="",
        ex_features=None
    )

    with arcpy.EnvManager(XYTolerance="1 Meters"):

        arcpy.analysis.PairwiseIntegrate(
            in_features=eliminated,
            cluster_tolerance=None
        )

    union = f"{gdb}\\parcel_union"

    arcpy.analysis.Union(
        in_features=f"{eliminated} #",
        out_feature_class=union,
        join_attributes="ALL",
        cluster_tolerance=None,
        gaps="GAPS"
    )

    arcpy.management.DeleteIdentical(
        in_dataset=union,
        fields="Shape",
        xy_tolerance=None,
        z_tolerance=0,
        out_mapping_table=None
    )

    final_singlepart = f"{gdb}\\parcel_final_singlepart"

    arcpy.management.MultipartToSinglepart(
        in_features=union,
        out_feature_class=final_singlepart
    )

    final_layer = "parcel_final_layer"

    arcpy.management.MakeFeatureLayer(
        final_singlepart,
        final_layer
    )

    final_small = arcpy.management.SelectLayerByAttribute(
        in_layer_or_view=final_layer,
        selection_type="NEW_SELECTION",
        where_clause="Shape_Area < 300"
    )

    final_output = f"{gdb}\\sam3_parcels_final"

    arcpy.management.Eliminate(
        in_features=final_small,
        out_feature_class=final_output,
        selection="LENGTH",
        ex_where_clause="",
        ex_features=None
    )


    print(f"Processing complete: {final_output}")

    return final_output


ras = tif file with sam3 output

# Define the folder directory and the geodatabase name
output_folder = folder
gdb_name = gdb name

# Combine them to get the full catalog path
gdb = os.path.join(output_folder, gdb_name)

# Check if the geodatabase exists
if not arcpy.Exists(gdb):
    # Create the File Geodatabase if it is missing
    arcpy.management.CreateFileGDB(output_folder, gdb_name)
    print(f"Created new geodatabase: {gdb}")
else:
    print(f"Geodatabase already exists at: {gdb}")

# Run functions
poly = get_poly(gdb,ras)

process_sam3_parcels(gdb,ras,poly)
