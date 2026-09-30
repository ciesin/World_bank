#!/usr/bin/env python3
"""Reuse or download the five vector-boundary inputs and write a manifest."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import requests

from common import PIPELINE, WORKSPACE, load_config


COUNTRY_ISO3 = {
    "Burkina Faso": "BFA", "Cameroon": "CMR",
    "Democratic Republic of the Congo": "COD", "Kenya": "KEN", "Libya": "LBY",
    "Mali": "MLI", "Nigeria": "NGA", "Republic of the Congo": "COG",
    "Rwanda": "RWA", "Senegal": "SEN", "South Africa": "ZAF",
    "South Sudan": "SSD",
}
VECTOR_SUFFIXES = {".gpkg", ".shp", ".parquet", ".geoparquet", ".geojson"}


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def safe_extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as source:
        for member in source.infolist():
            target = (root / member.filename).resolve()
            if root not in target.parents and target != root:
                raise ValueError(f"Unsafe archive member: {member.filename}")
        source.extractall(root)


def download(session: requests.Session, url: str, path: Path, force: bool) -> Path:
    if path.exists() and not force:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    with session.get(url, stream=True, timeout=(60, 900)) as response:
        response.raise_for_status()
        with temporary.open("wb") as target:
            shutil.copyfileobj(response.raw, target)
    temporary.replace(path)
    return path


def vector_files(root: Path) -> list[Path]:
    if root.is_file() and root.suffix.lower() in VECTOR_SUFFIXES:
        return [root.resolve()]
    if not root.exists():
        return []
    return sorted(path.resolve() for path in root.rglob("*") if path.suffix.lower() in VECTOR_SUFFIXES)


def existing_matches(patterns: list[str]) -> list[Path]:
    matches = []
    search_roots = [
        PIPELINE / "inputs/manual",
        PIPELINE / "data/raw",
        WORKSPACE / "data/raw",
    ]
    for root in search_roots:
        if not root.exists():
            continue
        for pattern in patterns:
            matches.extend(path.resolve() for path in root.rglob(pattern))
    return sorted(set(path for path in matches if path.suffix.lower() in VECTOR_SUFFIXES))


def grid3_catalog(session: requests.Session) -> dict[str, dict]:
    results = []
    start = 1
    while True:
        response = session.get(
            "https://www.arcgis.com/sharing/rest/search",
            params={
                "f": "json", "num": 100, "start": start,
                "q": 'orgid:BU6Aadhn6tbBEdyk AND title:"Settlement Extents"',
            }, timeout=120,
        )
        response.raise_for_status()
        payload = response.json()
        results.extend(payload.get("results", []))
        start = payload.get("nextStart", -1)
        if start == -1:
            break
    selected = {}
    for item in results:
        match = re.match(r"GRID3 ([A-Z]{3}) - Settlement Extents v(3(?:\.\d+)?)", item.get("title", ""))
        if not match:
            continue
        iso3, version = match.groups()
        current = selected.get(iso3)
        if current is None or tuple(map(int, version.split("."))) > current["version_key"]:
            item["version_key"] = tuple(map(int, version.split(".")))
            selected[iso3] = item
    return selected


def grid3_download_url(session: requests.Session, item: dict) -> str:
    response = session.get(
        f"https://www.arcgis.com/sharing/rest/content/items/{item['id']}",
        params={"f": "json"}, timeout=120,
    )
    response.raise_for_status()
    description = html.unescape(response.json().get("description", ""))
    links = re.findall(r"href=['\"]([^'\"]+)['\"][^>]*>(.*?)</a>", description, re.I | re.S)
    for url, label in links:
        if "settlement_extents" in re.sub(r"<[^>]+>", "", label).lower():
            return url
    raise RuntimeError(f"No settlement-extents download link in GRID3 item {item['id']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory-only", action="store_true", help="Do not download missing data")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    config = load_config()
    buffers = gpd.read_file(config["city_buffers"], layer=config["city_layer"])
    countries = sorted(buffers[config["city_fields"]["country"]].dropna().unique())
    raw = PIPELINE / "data/raw"
    records = []
    with requests.Session() as session:
        session.headers["User-Agent"] = "wb-buildings-city-boundary-comparison/1.0"
        for source in ("ghs_ucdb_2024", "ghs_fua"):
            specification = config["sources"][source]
            files = existing_matches(specification["existing_patterns"])
            if not files and not args.inventory_only:
                archive = download(
                    session, specification["url"], raw / source / specification["filename"], args.force
                )
                destination = raw / source / "extracted"
                if args.force or not destination.exists():
                    safe_extract(archive, destination)
                files = vector_files(destination)
            for path in files:
                records.append({"source": source, "path": str(path), "country": None, "status": "available"})
            if not files:
                records.append({"source": source, "path": None, "country": None, "status": "missing"})

        specification = config["sources"]["grid3_settlements_2024"]
        existing = existing_matches(specification["existing_patterns"])
        for path in existing:
            records.append({"source": "grid3_settlements_2024", "path": str(path), "country": None, "status": "available_existing"})
        if not existing:
            catalog = {} if args.inventory_only else grid3_catalog(session)
            for country in countries:
                iso3 = COUNTRY_ISO3.get(country)
                if iso3 == "LBY":
                    records.append({"source": "grid3_settlements_2024", "path": None, "country": country, "status": "outside_grid3_2024_coverage"})
                    continue
                item = catalog.get(iso3) if iso3 else None
                if item is None:
                    records.append({"source": "grid3_settlements_2024", "path": None, "country": country, "status": "not_downloaded" if args.inventory_only else "catalog_item_missing"})
                    continue
                url = grid3_download_url(session, item)
                path = raw / "grid3_settlements_2024" / f"{iso3}_settlement_extents_v3.gpkg"
                download(session, url, path, args.force)
                records.append({"source": "grid3_settlements_2024", "path": str(path), "country": country, "status": "available", "url": url, "arcgis_item_id": item["id"]})

        for source in ("africapolis_2020", "city_adm_2026"):
            specification = config["sources"][source]
            expected = PIPELINE / specification["manual_path"]
            files = vector_files(expected) + existing_matches(specification["existing_patterns"])
            files = sorted(set(files))
            if not files and specification.get("url") and not args.inventory_only:
                filename = Path(specification["url"].split("?", 1)[0]).name or f"{source}.gpkg"
                path = download(session, specification["url"], raw / source / filename, args.force)
                if zipfile.is_zipfile(path):
                    destination = raw / source / "extracted"
                    safe_extract(path, destination)
                    files = vector_files(destination)
                else:
                    files = vector_files(path)
            for path in files:
                records.append({"source": source, "path": str(path), "country": None, "status": "available"})
            if not files:
                records.append({"source": source, "path": None, "country": None, "status": "manual_input_required"})

    for record in records:
        path = Path(record["path"]) if record.get("path") else None
        record["sha256"] = sha256(path) if path and path.is_file() and path.suffix.lower() != ".shp" else None
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "inventory_only": args.inventory_only,
        "records": records,
    }
    output = PIPELINE / "data/vector_source_manifest.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"records": len(records), "missing": sum(r["path"] is None for r in records)}, indent=2))
    print(f"Wrote: {output}")


if __name__ == "__main__":
    main()
