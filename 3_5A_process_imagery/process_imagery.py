import arcpy
import os
import re

from arcpy.sa import (
    Raster,
    CellStatistics,
    SetNull,
    ExtractByMask
)


# Set inputs
input_folder = r"D:\mwalter\world_bank\joburg_imagery\all\extracted"

# Output geodatabase
gdb_name = "masked_images_1.gdb"
gdb_path = os.path.join(input_folder, gdb_name)

# Temporary folder
temp_folder = os.path.join(input_folder, "_temp_masks")

os.makedirs(temp_folder, exist_ok=True)

arcpy.env.overwriteOutput = True

arcpy.CheckOutExtension("Spatial")


# Create GDB
if not arcpy.Exists(gdb_path):

    print("\nCreating geodatabase:")
    print(gdb_path)

    arcpy.management.CreateFileGDB(
        input_folder,
        gdb_name
    )

else:

    print("\nGeodatabase already exists:")
    print(gdb_path)


# Get ID from file name
def get_image_id(filename):

    match = re.search(r"_(\d{15})_", filename)

    if match:
        return match.group(1)

    return None


# Get all tifs
print("\nSearching for TIFF files...")

all_tifs = []

for root, dirs, files in os.walk(input_folder):

    dirs[:] = [
        d for d in dirs
        if d not in [gdb_name, "_temp_masks"]
    ]

    for file in files:

        if file.lower().endswith((".tif", ".tiff")):

            all_tifs.append(
                os.path.join(root, file)
            )


print(f"Total TIFF files found: {len(all_tifs)}")


# Find tifs for images, cloud masks, and shadow masks
img_files = {}
ccs_files = {}
cld_files = {}


for tif in all_tifs:

    filename = os.path.basename(tif)
    upper_name = filename.upper()

    image_id = get_image_id(filename)

    if image_id is None:
        continue

    if upper_name.startswith("IMG"):

        img_files[image_id] = tif

    elif upper_name.startswith("CCS"):

        ccs_files[image_id] = tif

    elif upper_name.startswith("CLD"):

        cld_files[image_id] = tif


print(f"IMG files: {len(img_files)}")
print(f"CCS masks: {len(ccs_files)}")
print(f"CLD masks: {len(cld_files)}")



# Create masks
processed = 0
missing_ccs = 0
missing_cld = 0
failed = 0


for image_id, img_path in sorted(img_files.items()):

    print(f"Processing: {image_id}")
    print(f"IMG: {os.path.basename(img_path)}")

    ccs_path = ccs_files.get(image_id)

    if ccs_path is None:

        print(f"WARNING: No CCS mask found for {image_id}")

        missing_ccs += 1

        continue


    cld_path = cld_files.get(image_id)

    if cld_path is None:

        print(f"WARNING: No CLD mask found for {image_id}")

        missing_cld += 1

        continue


    try:


        img_raster = Raster(img_path)

        img_desc = arcpy.Describe(img_path)

        img_cell_x = float(
            arcpy.management.GetRasterProperties(
                img_path,
                "CELLSIZEX"
            ).getOutput(0)
        )

        img_cell_y = float(
            arcpy.management.GetRasterProperties(
                img_path,
                "CELLSIZEY"
            ).getOutput(0)
        )


        arcpy.env.snapRaster = img_path
        arcpy.env.cellSize = img_path
        arcpy.env.extent = img_path
        arcpy.env.outputCoordinateSystem = img_desc.spatialReference

        combined_mask_path = os.path.join(
            temp_folder,
            f"{image_id}_combined.tif"
        )

        mask_under100_path = os.path.join(
            temp_folder,
            f"{image_id}_under100.tif"
        )

        ccs = Raster(ccs_path)
        cld = Raster(cld_path)


        # Merge masks
        combined_mask = CellStatistics(
            [ccs, cld],
            statistics_type="MAXIMUM",
            ignore_nodata="DATA"
        )

        combined_mask.save(
            combined_mask_path
        )



        # Threshold mask at 100
        mask_under100 = SetNull(
            combined_mask >= 1000,
            combined_mask
        )

        mask_under100.save(
            mask_under100_path
        )


        arcpy.env.snapRaster = img_path
        arcpy.env.cellSize = img_path
        arcpy.env.extent = img_path
        arcpy.env.outputCoordinateSystem = img_desc.spatialReference


        # Apply mask
        extracted = ExtractByMask(
            in_raster=img_raster,
            in_mask_data=mask_under100_path
        )

        output_name = f"IMG_{image_id}"

        output_path = os.path.join(
            gdb_path,
            output_name
        )

        extracted.save(output_path)

        processed += 1

        for temp_file in [
            combined_mask_path,
            mask_under100_path
        ]:

            if arcpy.Exists(temp_file):

                arcpy.management.Delete(
                    temp_file
                )


    except Exception as e:

        print("\nERROR")
        print(f"Image ID: {image_id}")
        print(f"Error: {e}")

        failed += 1

print(
    f"Successfully processed: {processed}"
)
