#!/usr/bin/env python3
"""Selectively extract the four official GBA.Height tiles intersecting Juba.

The mediaTUM release packages 0.2 degree GeoTIFFs inside very large 5 degree
ZIP archives.  fsspec supplies a seekable FTP stream, so ZipFile requests only
the central directory and the selected compressed members instead of fetching
the 125 GB parent archive.
"""

from __future__ import annotations

import argparse
import binascii
import hashlib
import json
import os
import struct
import subprocess
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path

import fsspec
import geopandas as gpd


ROOT = Path(__file__).resolve().parents[1]
AOI_PATH = ROOT / "data/aoi/juba_expanded.geojson"
INDEX_PATH = ROOT / "data/raw/gba_height/representative/height_tif.geojson"
ARCHIVE_INDEX_PATH = ROOT / "data/raw/gba_height/representative/height_zip.geojson"
OUT_DIR = ROOT / "data/raw/gba_height/juba"
MANIFEST_PATH = ROOT / "data/raw/gba_height/juba_download_manifest.json"
FTP_HOST = "dataserv.ub.tum.de"
FTP_ARCHIVE_PREFIX = "/FD_Server_5/m1782307/"
PUBLIC_USERNAME = "m1782307"
PUBLIC_PASSWORD = "m1782307"
DATASET_DOI = "https://doi.org/10.14459/2025mp1782307"
LICENSE = "CC BY-NC 4.0"


def digest(path: Path, algorithm: str) -> str:
    hasher = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def curl_range(url: str, username: str, password: str, start: int, end: int, output: Path) -> None:
    subprocess.run(
        [
            "curl", "-L", "--fail", "--silent", "--show-error",
            "--retry", "6", "--retry-delay", "3",
            "--user", f"{username}:{password}", "--range", f"{start}-{end}",
            url, "-o", str(output),
        ],
        check=True,
    )


def extract_member_by_range(
    url: str, username: str, password: str, info: zipfile.ZipInfo, output: Path
) -> None:
    """Fetch one compressed ZIP member by byte range and inflate it locally."""
    header_path = output.with_suffix(output.suffix + ".header.part")
    compressed_path = output.with_suffix(output.suffix + ".compressed.part")
    temporary = output.with_suffix(output.suffix + ".part")
    try:
        curl_range(url, username, password, info.header_offset, info.header_offset + 65535, header_path)
        header = header_path.read_bytes()
        if len(header) < 30 or header[:4] != b"PK\x03\x04":
            raise RuntimeError(f"Invalid ZIP local header for {info.filename}")
        fields = struct.unpack("<4s5H3I2H", header[:30])
        filename_length, extra_length = fields[-2:]
        data_start = info.header_offset + 30 + filename_length + extra_length
        data_end = data_start + info.compress_size - 1
        curl_range(url, username, password, data_start, data_end, compressed_path)
        if compressed_path.stat().st_size != info.compress_size:
            raise RuntimeError(f"Incomplete compressed range for {info.filename}")

        if info.compress_type == zipfile.ZIP_DEFLATED:
            decompressor = zlib.decompressobj(-zlib.MAX_WBITS)
        elif info.compress_type == zipfile.ZIP_STORED:
            decompressor = None
        else:
            raise RuntimeError(f"Unsupported ZIP compression type {info.compress_type}")
        crc = 0
        written = 0
        with compressed_path.open("rb") as source, temporary.open("wb") as target:
            for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
                data = decompressor.decompress(chunk) if decompressor else chunk
                target.write(data)
                crc = binascii.crc32(data, crc)
                written += len(data)
            if decompressor:
                data = decompressor.flush()
                target.write(data)
                crc = binascii.crc32(data, crc)
                written += len(data)
        if written != info.file_size or (crc & 0xFFFFFFFF) != info.CRC:
            raise RuntimeError(f"Size or CRC mismatch for {info.filename}")
        temporary.replace(output)
    finally:
        for temporary_path in (header_path, compressed_path, temporary):
            temporary_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Replace existing tile files")
    args = parser.parse_args()

    if not INDEX_PATH.exists() or not ARCHIVE_INDEX_PATH.exists():
        raise FileNotFoundError("Download the official height_tif and height_zip indexes first")

    aoi = gpd.read_file(AOI_PATH)
    fine_index = gpd.read_file(INDEX_PATH).to_crs(aoi.crs)
    selected = fine_index[fine_index.intersects(aoi.geometry.union_all())].copy()
    if len(selected) != 4:
        raise RuntimeError(f"Expected four GBA.Height tiles for Juba; selected {len(selected)}")
    archives = selected.zipfile_path.unique().tolist()
    if len(archives) != 1:
        raise RuntimeError(f"Expected one parent archive; selected {archives}")

    archive_index = gpd.read_file(ARCHIVE_INDEX_PATH)
    archive_row = archive_index[archive_index.path == archives[0]]
    if len(archive_row) != 1:
        raise RuntimeError(f"Archive index entry not found for {archives[0]}")

    username = os.environ.get("GBA_FTP_USER", PUBLIC_USERNAME)
    password = os.environ.get("GBA_FTP_PASSWORD", PUBLIC_PASSWORD)
    remote_relative = archives[0].removeprefix("./")
    remote_path = FTP_ARCHIVE_PREFIX + remote_relative
    fs = fsspec.filesystem(
        "ftp",
        host=FTP_HOST,
        username=username,
        password=password,
        block_size=8 * 1024 * 1024,
        timeout=300,
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    records = []
    requested_names = {Path(path).name for path in selected.path}
    ftp_url = f"ftp://{FTP_HOST}/{remote_relative}"
    with fs.open(remote_path, "rb") as remote_stream:
        remote_size = remote_stream.size
        with zipfile.ZipFile(remote_stream) as archive:
            available = set(archive.namelist())
            missing = requested_names - available
            if missing:
                raise RuntimeError(f"Expected members missing from archive: {sorted(missing)}")
            for member in sorted(requested_names):
                info = archive.getinfo(member)
                output = OUT_DIR / member
                if output.exists() and not args.force:
                    if output.stat().st_size != info.file_size:
                        raise RuntimeError(f"Existing tile has unexpected size: {output}")
                    status = "reused"
                else:
                    print(f"Extracting {member} ({info.compress_size / 1e6:.1f} MB compressed)", flush=True)
                    extract_member_by_range(ftp_url, username, password, info, output)
                    status = "downloaded"
                records.append(
                    {
                        "filename": member,
                        "official_index_path": next(
                            path for path in selected.path if Path(path).name == member
                        ),
                        "uncompressed_bytes": info.file_size,
                        "compressed_bytes": info.compress_size,
                        "zip_crc32": f"{info.CRC:08x}",
                        "sha256": digest(output, "sha256"),
                        "sha512": digest(output, "sha512"),
                        "status": status,
                    }
                )

    manifest = {
        "dataset": "GlobalBuildingAtlas GBA.Height",
        "dataset_record": DATASET_DOI,
        "dataset_publication_date": "2025-09-02",
        "data_production_end_date": "2025-04-30",
        "representative_metadata_readme_date": "2026-02-01",
        "license": LICENSE,
        "license_constraint": "Attribution required; non-commercial use only; indicate modifications.",
        "official_distribution": "mediaTUM FTP",
        "official_ftp_url": ftp_url,
        "parent_archive": remote_relative,
        "parent_archive_bytes": remote_size,
        "parent_archive_sha512": archive_row.iloc[0].SHA512,
        "selection_method": "Official 0.2-degree height_tif.geojson tiles intersecting juba_expanded.geojson",
        "selection_metadata": {
            "height_tif_geojson_sha512": digest(INDEX_PATH, "sha512"),
            "height_zip_geojson_sha512": digest(ARCHIVE_INDEX_PATH, "sha512"),
            "official_checksums_file": "data/raw/gba_height/representative/checksums.sha512",
        },
        "download_method": "Range-aware selective ZIP member extraction; parent archive not downloaded",
        "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
        "tiles": records,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2), flush=True)


if __name__ == "__main__":
    main()
