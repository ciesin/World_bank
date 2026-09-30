"""Shared helpers for the 93-city population segment pipeline."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

import geopandas as gpd


PIPELINE = Path(__file__).resolve().parents[1]
WORKSPACE = PIPELINE.parents[1]


def load_config() -> dict:
    config = json.loads((PIPELINE / "config.json").read_text())
    for key in ("city_buffers", "segments", "local_nighttime_raster"):
        config[key] = str((PIPELINE / config[key]).resolve())
    return config


def normalize_text(value) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def slugify(value) -> str:
    return normalize_text(value).replace(" ", "_")


def load_buffers(config: dict) -> gpd.GeoDataFrame:
    buffers = gpd.read_file(
        config["city_buffers"], layer=config["city_layer"], engine="pyogrio"
    )
    if buffers.crs is None:
        raise ValueError("City buffers do not have a CRS")
    return buffers.to_crs(config["analysis_crs"])


def city_output_stem(city, fields: dict) -> str:
    return f"{int(getattr(city, fields['id']))}_{slugify(getattr(city, fields['name']))}"


def raster_path(product: str, city_stem: str) -> Path:
    if product == "landscan_nighttime_2025":
        return PIPELINE / "data" / "rasters" / "local" / f"{city_stem}_{product}.tif"
    return PIPELINE / "data" / "rasters" / "earth_engine" / f"{city_stem}_{product}.tif"
