# Parcels
Materials, guides, and codes supporting the extraction of buildings

1. split_building_tiles.py
Code to split raster into tiles (256, 512, or 1024) which are input into SAM3.

2. batch_segment_sam3.py
Run code in QGIS using GeoAI plugin to batch classify tiles created by split_building_tiles.py using SAM3 and a text prompt. SAM3 must first be authenticated and loaded using hugging face. Apply here to access SAM3: https://huggingface.co/facebook/sam3. Once approved, authenticate HuggingFace in the QGIS:

```python
from huggingface_hub import login

# Replace Hugging Face token - https://huggingface.co/docs/hub/en/security-tokens
HF_TOKEN = "hf_your_actual_token_here"

# Authenticate session
login(token=HF_TOKEN, add_to_git_credential=False)
```
