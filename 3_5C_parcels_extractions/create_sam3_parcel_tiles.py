### Split segmented blocks into tiles containing only the input of one block
### Used as input for SAM3 parcel extraction

# Set up environment
import arcpy
from arcpy.sa import *
import os
import numpy as np

arcpy.env.overwriteOutput = True
arcpy.env.addOutputsToMap = False
arcpy.SetLogHistory(False)
arcpy.management.Delete(arcpy.env.scratchGDB)
arcpy.CheckOutExtension("Spatial")

## Add inputs
# polygon_fc = polygons containing "block" segmentation created by parcel-trained Unet
# input_raster = mosaiced raster image
# output_folder = destination for tiles
# tile_size = size of tiles to be created
polygon_fc = r"D:\mwalter\world_bank\arcgis_world_bank\arcgis_world_bank.gdb\parc_r"
input_raster = r"D:\mwalter\world_bank\juba_data\juba_all_images_merged.tif"
output_folder = r"D:\mwalter\world_bank\juba_clipped_tiles1_1"
tile_size = 512

# Create output folder if it doesn't exist
os.makedirs(output_folder, exist_ok=True)

# Define function to create tiles
def raster_to_512_multiband(raster, output_path):

    # Reshape tile
    arr = arcpy.RasterToNumPyArray(
        raster,
        nodata_to_value=0
    )

    if arr.ndim == 2:
        arr = arr[np.newaxis, :, :]

    bands, rows, cols = arr.shape

    output = np.zeros(
        (bands, tile_size, tile_size),
        dtype=arr.dtype
    )

    row_start = max((tile_size - rows)//2, 0)
    col_start = max((tile_size - cols)//2, 0)

    row_end = min(row_start + rows, tile_size)
    col_end = min(col_start + cols, tile_size)

    output[
        :,
        row_start:row_end,
        col_start:col_end
    ] = arr[
        :,
        :row_end-row_start,
        :col_end-col_start
    ]


    cell_x = raster.meanCellWidth
    cell_y = raster.meanCellHeight
    
    lower_left = arcpy.Point(
        raster.extent.XMin - col_start * cell_x,
        raster.extent.YMin - row_start * cell_y
    )

    out_raster = arcpy.NumPyArrayToRaster(
        output,
        lower_left,
        raster.meanCellWidth,
        raster.meanCellHeight
    )

    # Save and project
    out_raster.save(output_path)

    arcpy.management.DefineProjection(
        output_path,
        raster.spatialReference
    )

# Clip to each feature in block polygon layer
with arcpy.da.SearchCursor(
    polygon_fc,
    ["OID@", "SHAPE@"]
) as cursor:

    for oid, geom in cursor:

        oid1 = f"{oid}_1"

        print(f"Processing {oid1}")

        temp_clip = os.path.join(
            arcpy.env.scratchFolder,
            f"clip_{oid1}.tif"
        )

        out = os.path.join(
            output_folder,
            f"chip_{oid1}.tif"
        )


        # remove previous files
        for f in [temp_clip, out]:
            if arcpy.Exists(f):
                arcpy.management.Delete(f)


        arcpy.management.Clip(
            in_raster=input_raster,
            rectangle="#",
            out_raster=temp_clip,
            in_template_dataset=geom,
            nodata_value=0,
            clipping_geometry="ClippingGeometry",
            maintain_clipping_extent="NO_MAINTAIN_EXTENT"
        )


    
    
        raster_to_512_multiband(
            arcpy.Raster(temp_clip),
            out
        )

        # convert NoData to 0
        out_zero = arcpy.sa.Con(
            arcpy.sa.IsNull(out),
            0,
            out
        )
        
        # overwrite original output
        temp_zero = out.replace(".tif", "_zero.tif")
        
        out_zero.save(temp_zero)
        
        # replace original
        arcpy.management.Delete(out)
        arcpy.management.Rename(
            temp_zero,
            out
        )

        arcpy.management.Delete(temp_clip)


print("Complete")
