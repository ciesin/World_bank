import arcpy
import os
from pathlib import Path

def split_building_tiles(folder,img,size):

    # Create folder 
    img_name = Path(img).stem
    target_folder = (rf"{folder}/{img_name}_{size}")
    print(target_folder)

    # Extract parent directory and folder name
    parent_dir = os.path.dirname(target_folder)
    folder_name = os.path.basename(target_folder)

    # Check if the folder exists and delete it
    if arcpy.Exists(target_folder):
        arcpy.management.Delete(target_folder)
        print(f"Deleted existing folder: {target_folder}")

    # Create the folder
    arcpy.management.CreateFolder(parent_dir, folder_name)
    print(f"Folder created: {target_folder}")

    arcpy.management.SplitRaster(
        in_raster=img,
        out_folder=target_folder,
        out_base_name=f"{img_name}_{size}",
        split_method="SIZE_OF_TILE",
        format="TIFF",
        resampling_type="NEAREST",
        num_rasters="1 1",
        tile_size=f"{size} {size}",
        overlap=0,
        units="PIXELS",
        cell_size=None,
        origin=None,
        split_polygon_feature_class=None,
        clip_type="NONE",
        template_extent="DEFAULT",
        nodata_value=""
    )

    print(f"raster split for {img}")

#folder = folder path for exporting - subfolder will be created
#img = path for raster image

split_building_tiles(folder,img,256)
split_building_tiles(folder,img,512)
split_building_tiles(folder,img,1024)
