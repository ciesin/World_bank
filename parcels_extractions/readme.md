# Parcels
Materials, guides, and codes supporting the creation of parcels


1. Create_sam3_parcel_tiles.py
This code needs to be run on the image being input into the SAM3 parcel extraction model. The code will create 512x512 tiles that are masked to only include one "block" at a time. Blocks are created by running a semantic segmentation model on the imagery trained for parcel extraction.

2. d
This code is run in QGIS to input all the tiles created in the previous step into SAM3 model. SAM3 must first be authenticated and loaded using hugging face. Apply here to access SAM3: https://huggingface.co/facebook/sam3. Once approved, authenticate HuggingFace in the QGIS:

```python
from huggingface_hub import login

# Replace Hugging Face token - https://huggingface.co/docs/hub/en/security-tokens
HF_TOKEN = "hf_your_actual_token_here"

# Authenticate session
login(token=HF_TOKEN, add_to_git_credential=False)
