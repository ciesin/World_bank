# World_bank
Materials, guides, and codes supporting the 2026 World Bank report.


1. Create_sam3_parcel_tiles.py
This code needs to be run on the image being input into the SAM3 parcel extraction model. The code will create 512x512 tiles that are masked to only include one "block" at a time. Blocks are created by running a semantic segmentation model on the imagery trained for parcel extraction.

2. 
