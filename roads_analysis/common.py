"""Shared helpers for the 93-city road comparison."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

import geopandas as gpd


PIPELINE = Path(__file__).resolve().parents[1]


def load_config() -> dict:
    config = json.loads((PIPELINE / "config.json").read_text())
    for key in ("city_buffers", "segments"):
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
    return gpd.read_file(config["city_buffers"], layer=config["city_layer"], engine="pyogrio").to_crs(config["analysis_crs"])


def city_output_stem(city, fields: dict) -> str:
    return f"{int(getattr(city, fields['id']))}_{slugify(getattr(city, fields['name']))}"


def prepared_segment_path(stem: str) -> Path:
    return PIPELINE / "data" / "prepared_segments" / f"{stem}.parquet"


def road_path(source: str, stem: str) -> Path:
    return PIPELINE / "data" / source / f"{stem}_{source}_roads.parquet"


def reporting_group(city_size_group: str, source_zone: str) -> str:
    size = "lt_300k" if city_size_group == "population_lt_300k" else "ge_300k"
    zone = "urban_center" if source_zone == "city_segment" else "peri_urban"
    return f"cities_{size}_{zone}"
