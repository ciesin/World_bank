"""Shared helpers for the 93-city boundary comparison pipeline."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

import geopandas as gpd
import numpy as np
import pyogrio
import shapely


PIPELINE = Path(__file__).resolve().parents[1]
WORKSPACE = PIPELINE.parents[1]


def load_config() -> dict:
    config = json.loads((PIPELINE / "config.json").read_text())
    config["city_buffers"] = str((PIPELINE / config["city_buffers"]).resolve())
    return config


def normalize_text(value) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def slugify(value) -> str:
    return normalize_text(value).replace(" ", "_")


def polygonal(data: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    data = data.loc[data.geometry.notna() & ~data.geometry.is_empty].copy()
    if data.empty:
        return data
    invalid = ~shapely.is_valid(data.geometry.to_numpy())
    if invalid.any():
        data.loc[invalid, "geometry"] = shapely.make_valid(
            data.loc[invalid, "geometry"].to_numpy()
        )
    data = data.loc[np.isin(shapely.get_type_id(data.geometry.to_numpy()), [3, 6])]
    return data.reset_index(drop=True)


def vector_layers(path: Path, layer_hint: str = "") -> list[str | None]:
    if path.suffix.lower() in {".parquet", ".geoparquet"}:
        return [None]
    layers = pyogrio.list_layers(path)
    polygon_layers = [
        str(name) for name, geometry_type in layers
        if geometry_type and "Polygon" in str(geometry_type)
    ]
    if layer_hint:
        preferred = [name for name in polygon_layers if re.search(layer_hint, name, re.I)]
        if preferred:
            return preferred
    return polygon_layers


def read_vector(path: Path, layer: str | None = None, bbox=None) -> gpd.GeoDataFrame:
    if path.suffix.lower() in {".parquet", ".geoparquet"}:
        data = gpd.read_parquet(path)
        if bbox is not None:
            xmin, ymin, xmax, ymax = bbox
            data = data.cx[xmin:xmax, ymin:ymax]
        return data
    kwargs = {"engine": "pyogrio"}
    if layer is not None:
        kwargs["layer"] = layer
    if bbox is not None:
        kwargs["bbox"] = bbox
    return gpd.read_file(path, **kwargs)


def first_existing(columns, preferences: list[str]) -> str | None:
    lookup = {str(column).lower(): str(column) for column in columns}
    for preference in preferences:
        if preference.lower() in lookup:
            return lookup[preference.lower()]
    return None
