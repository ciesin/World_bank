#!/usr/bin/env python3
"""Selectively download the official GBA.Height tiles needed by the city portfolio.

The mediaTUM distribution stores 0.2 degree GeoTIFF members in very large
5 degree ZIP archives.  This script resolves AOI intersections from the
official index, downloads each unique member once with HTTP/FTP byte ranges,
and writes a city-to-tile usage table plus a checksummed provenance manifest.
"""

from __future__ import annotations

import argparse
import json
import os
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import fsspec
import geopandas as gpd
import pandas as pd

from download_gba_height_juba import (
    DATASET_DOI,
    FTP_ARCHIVE_PREFIX,
    FTP_HOST,
    LICENSE,
    PUBLIC_PASSWORD,
    PUBLIC_USERNAME,
    digest,
    extract_member_by_range,
)


ROOT = Path(__file__).resolve().parents[1]
CITIES = ROOT / "data/cities"
GBA_ROOT = ROOT / "data/raw/portfolio/gba_height"
TILE_DIR = GBA_ROOT / "tiles"
INDEX_PATH = ROOT / "data/raw/gba_height/representative/height_tif.geojson"
ARCHIVE_INDEX_PATH = ROOT / "data/raw/gba_height/representative/height_zip.geojson"
USAGE_PATH = CITIES / "gba_height_city_usage.csv"
MANIFEST_PATH = CITIES / "gba_height_download_manifest.json"


def resolve_usage(selected_cities: set[str] | None = None) -> pd.DataFrame:
    manifest = pd.read_csv(CITIES / "city_manifest.csv")
    if selected_cities:
        unknown = selected_cities - set(manifest.city_slug)
        if unknown:
            raise ValueError(f"Unknown city slugs: {sorted(unknown)}")
        manifest = manifest.loc[manifest.city_slug.isin(selected_cities)]
    index = gpd.read_file(INDEX_PATH)
    rows: list[dict] = []
    for city in manifest.itertuples(index=False):
        aoi = gpd.read_parquet(CITIES / city.city_slug / "inputs/aoi.parquet").to_crs(index.crs)
        selected = index.loc[index.intersects(aoi.geometry.union_all())]
        if selected.empty:
            rows.append({
                "city_slug": city.city_slug,
                "filename": None,
                "official_index_path": None,
                "parent_archive": None,
                "availability": "no_indexed_gba_height_tile",
            })
            continue
        for tile in selected.itertuples(index=False):
            rows.append({
                "city_slug": city.city_slug,
                "filename": Path(tile.path).name,
                "official_index_path": tile.path,
                "parent_archive": tile.zipfile_path,
                "availability": "indexed",
            })
    return pd.DataFrame(rows).sort_values(["city_slug", "filename"], na_position="last")


def write_manifest(records: dict[str, dict], usage: pd.DataFrame, archive_rows: dict) -> None:
    unavailable = sorted(usage.loc[usage.filename.isna(), "city_slug"].unique())
    payload = {
        "dataset": "GlobalBuildingAtlas GBA.Height",
        "dataset_record": DATASET_DOI,
        "dataset_publication_date": "2025-09-02",
        "data_production_end_date": "2025-04-30",
        "representative_metadata_readme_date": "2026-02-01",
        "license": LICENSE,
        "license_constraint": "Attribution required; non-commercial use only; indicate modifications.",
        "official_distribution": "mediaTUM FTP",
        "selection_method": "Official 0.2-degree height_tif.geojson tiles intersecting each portfolio AOI",
        "selection_metadata": {
            "height_tif_geojson_sha512": digest(INDEX_PATH, "sha512"),
            "height_zip_geojson_sha512": digest(ARCHIVE_INDEX_PATH, "sha512"),
            "official_checksums_file": "data/raw/gba_height/representative/checksums.sha512",
        },
        "download_method": "Range-aware selective ZIP member extraction; parent archives not downloaded",
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "cities_requested": int(usage.city_slug.nunique()),
        "cities_without_indexed_tiles": unavailable,
        "unique_tiles_requested": int(usage.filename.nunique()),
        "parent_archives": archive_rows,
        "tiles": [records[name] for name in sorted(records)],
    }
    MANIFEST_PATH.write_text(json.dumps(payload, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", action="append", help="Limit acquisition to one or more city slugs")
    parser.add_argument("--force", action="store_true", help="Replace existing tile files")
    parser.add_argument("--inventory-only", action="store_true", help="Write usage inventory without downloading")
    parser.add_argument("--workers", type=int, default=4, help="Concurrent selected-member downloads")
    args = parser.parse_args()
    if not INDEX_PATH.exists() or not ARCHIVE_INDEX_PATH.exists():
        raise FileNotFoundError("Official GBA.Height indexes are missing")

    usage = resolve_usage(set(args.city) if args.city else None)
    if args.city and USAGE_PATH.exists():
        previous = pd.read_csv(USAGE_PATH)
        previous = previous.loc[~previous.city_slug.isin(set(args.city))]
        usage = pd.concat([previous, usage], ignore_index=True).sort_values(
            ["city_slug", "filename"], na_position="last"
        )
    USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    usage.to_csv(USAGE_PATH, index=False)
    selected_usage = usage.loc[usage.city_slug.isin(args.city)] if args.city else usage
    requested = selected_usage.dropna(subset=["filename"]).copy()
    print(
        f"Resolved {requested.filename.nunique()} unique tiles in "
        f"{requested.parent_archive.nunique()} archives for "
        f"{selected_usage.city_slug.nunique()} cities",
        flush=True,
    )
    if args.inventory_only:
        return

    archive_index = gpd.read_file(ARCHIVE_INDEX_PATH)
    archive_lookup = archive_index.set_index("path")
    username = os.environ.get("GBA_FTP_USER", PUBLIC_USERNAME)
    password = os.environ.get("GBA_FTP_PASSWORD", PUBLIC_PASSWORD)
    TILE_DIR.mkdir(parents=True, exist_ok=True)
    records = {}
    archive_records = {}
    if MANIFEST_PATH.exists():
        old = json.loads(MANIFEST_PATH.read_text())
        records = {row["filename"]: row for row in old.get("tiles", [])}
        archive_records = old.get("parent_archives", {})

    grouped = list(requested.groupby("parent_archive", sort=True))
    for archive_number, (archive_path, group) in enumerate(grouped, 1):
        # Reconnect for every parent archive. Long batches of independent curl
        # range transfers otherwise leave the FTP control connection idle long
        # enough for the server's 600-second timeout.
        fs = fsspec.filesystem(
            "ftp", host=FTP_HOST, username=username, password=password,
            block_size=8 * 1024 * 1024, timeout=300,
        )
        remote_relative = archive_path.removeprefix("./")
        remote_path = FTP_ARCHIVE_PREFIX + remote_relative
        ftp_url = f"ftp://{FTP_HOST}/{remote_relative}"
        expected_names = set(group.filename)
        print(
            f"[{archive_number}/{len(grouped)}] {remote_relative}: "
            f"{len(expected_names)} selected members", flush=True,
        )
        with fs.open(remote_path, "rb") as remote_stream:
            remote_size = remote_stream.size
            with zipfile.ZipFile(remote_stream) as archive:
                missing = expected_names - set(archive.namelist())
                if missing:
                    raise RuntimeError(f"Missing members in {archive_path}: {sorted(missing)}")
                member_info = {member: archive.getinfo(member) for member in sorted(expected_names)}
                pending = []
                for member_number, member in enumerate(sorted(expected_names), 1):
                    info = member_info[member]
                    output = TILE_DIR / member
                    if output.exists() and not args.force:
                        if output.stat().st_size != info.file_size:
                            raise RuntimeError(f"Existing tile has unexpected size: {output}")
                        old_record = records.get(member, {})
                        sha256 = old_record.get("sha256") or digest(output, "sha256")
                        sha512 = old_record.get("sha512") or digest(output, "sha512")
                        status = "reused"
                        records[member] = {
                            "filename": member,
                            "official_index_path": group.loc[group.filename.eq(member), "official_index_path"].iloc[0],
                            "parent_archive": archive_path,
                            "uncompressed_bytes": info.file_size,
                            "compressed_bytes": info.compress_size,
                            "zip_crc32": f"{info.CRC:08x}",
                            "sha256": sha256,
                            "sha512": sha512,
                            "status": status,
                        }
                    else:
                        pending.append((member_number, member, info, output))
                def download(task):
                    member_number, member, info, output = task
                    print(
                        f"  [{member_number}/{len(expected_names)}] {member} "
                        f"({info.compress_size / 1e6:.1f} MB compressed)", flush=True,
                    )
                    extract_member_by_range(ftp_url, username, password, info, output)
                    return member, info, digest(output, "sha256"), digest(output, "sha512")

                with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
                    futures = {pool.submit(download, task): task for task in pending}
                    for future in as_completed(futures):
                        member, info, sha256, sha512 = future.result()
                        records[member] = {
                            "filename": member,
                            "official_index_path": group.loc[group.filename.eq(member), "official_index_path"].iloc[0],
                            "parent_archive": archive_path,
                            "uncompressed_bytes": info.file_size,
                            "compressed_bytes": info.compress_size,
                            "zip_crc32": f"{info.CRC:08x}",
                            "sha256": sha256,
                            "sha512": sha512,
                            "status": "downloaded",
                        }
                        print(f"    completed {member}", flush=True)
                        write_manifest(records, usage, archive_records)
        if archive_path not in archive_lookup.index:
            raise RuntimeError(f"No official archive index record for {archive_path}")
        archive_records[archive_path] = {
            "official_ftp_url": ftp_url,
            "bytes": remote_size,
            "official_sha512": archive_lookup.loc[archive_path, "SHA512"],
        }
        write_manifest(records, usage, archive_records)
    print(f"Complete: {len(records)} locally checksummed unique tiles", flush=True)


if __name__ == "__main__":
    main()
