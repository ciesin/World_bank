import os
from samgeo import SamGeo3
from qgis.core import QgsProject, QgsRasterLayer
import rasterio
from rasterio.merge import merge

from datetime import datetime
import time

# 1. Record and print the start time
start_wall = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
start_perf = time.perf_counter()
print(f"Script started at: {start_wall}")

prompt = 'land parcel'

n = f"juba_parcels_sam_all_tiles512"


# 1. Initialize the SAM 3 Engine
sam3 = SamGeo3(
    model_id="sam3-h",         
    backend="meta",
    device="cuda",             
    confidence_threshold=0.1,
    load_from_HF=True
)

# 2. Setup  batch directories (Using clean forward slashes)
input_folder = r"D:\mwalter\world_bank\juba_clipped_tiles1"
output_folder = f"D:/mwalter/sam/{n}/"

if not os.path.exists(output_folder):
    os.makedirs(output_folder)
    
import rasterio

def copy_georeference(src_path, dst_path):
    # Read georeferencing from source
    with rasterio.open(src_path) as src:
        crs = src.crs
        transform = src.transform

    # Reopen output and write georeferencing
    with rasterio.open(dst_path, "r+") as dst:
        dst.crs = crs
        dst.transform = transform

# 3. Main processing loop
for filename in os.listdir(input_folder):
    if filename.lower().endswith(('.tif', '.tiff')):
        
        image_path = os.path.join(input_folder, filename).replace("\\", "/")
        output_name = f"mask_{filename}"
        output_tif_path = os.path.join(output_folder, output_name).replace("\\", "/")
        
        print(f"Processing: {filename}...")
        
        # TRY BLOCK - All operational logic inside here must be indented exactly the same
        try:
            sam3.set_image(image_path, bands=[5, 3, 2])
            
            sam3.generate_masks(
                prompt=f"{prompt}",
                box_threshold=0.0,
                text_threshold=0.0,
                reference_path=image_path
            )
            
            sam3.save_masks(output=output_tif_path, unique=True)
            copy_georeference(image_path, output_tif_path)
            print(f"Successfully saved mask to: {output_tif_path}")
            
        # EXCEPT BLOCK - Handles errors if any code above breaks, keeping the loop running
        except Exception as e:
            print(f"Failed to process {filename}. Error: {e}")
            
mask_files = [
    os.path.join(output_folder, f)
    for f in os.listdir(output_folder)
    if f.startswith("mask_") and f.endswith(".TIF")
]


srcs = [rasterio.open(f) for f in mask_files]

mosaic, transform = merge(srcs, method="max")

out_meta = srcs[0].meta.copy()
out_meta.update({
    "height": mosaic.shape[1],
    "width": mosaic.shape[2],
    "transform": transform,
    "compress": "lzw"
})

merged_output = os.path.join(output_folder, f"{n}.tif")

with rasterio.open(merged_output, "w", **out_meta) as dest:
    dest.write(mosaic)

for src in srcs:
    src.close()

print(f"Merged raster saved to {merged_output}")

print("Batch processing complete!")

end_wall = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
end_perf = time.perf_counter()
print(f"Script finished at: {end_wall}")

# 3. Calculate and print execution time
execution_time = end_perf - start_perf
print(f"Total time taken: {execution_time:.4f} seconds")
