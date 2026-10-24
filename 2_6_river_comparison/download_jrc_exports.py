#!/usr/bin/env python3
"""Download completed JRC-GSW Earth Engine exports from Google Drive."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import ee
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from common import PIPELINE


def drive_folders(service, name: str) -> list[str]:
    escaped = name.replace("'", "\\'")
    result = service.files().list(
        q=f"name = '{escaped}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
        spaces="drive", fields="files(id,name,createdTime)", orderBy="createdTime desc", pageSize=100,
    ).execute()
    return [item["id"] for item in result.get("files", [])]


def drive_files(service, folder_id: str) -> dict[str, str]:
    found, token = {}, None
    while True:
        result = service.files().list(
            q=f"'{folder_id}' in parents and trashed = false", spaces="drive",
            fields="nextPageToken,files(id,name,size)", pageSize=1000, pageToken=token,
        ).execute()
        found.update({item["name"]: item["id"] for item in result.get("files", [])})
        token = result.get("nextPageToken")
        if not token:
            return found


def download(service, file_id: str, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tif.partial")
    request = service.files().get_media(fileId=file_id)
    with temporary.open("wb") as target:
        transfer = MediaIoBaseDownload(target, request, chunksize=32 * 1024 * 1024)
        done = False
        while not done:
            _, done = transfer.next_chunk(num_retries=5)
    temporary.replace(output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    manifest = json.loads((PIPELINE / "data" / "jrc_export_manifest.json").read_text())
    exports = manifest["exports"]
    if not manifest.get("submitted") or not all(item.get("task_id") for item in exports):
        raise RuntimeError("JRC export manifest does not contain submitted task IDs")
    ee.Initialize(project=args.project)
    drive = build("drive", "v3", credentials=ee.data.get_persistent_credentials(), cache_discovery=False)
    output_root = PIPELINE / "data" / "jrc_gsw"
    while True:
        folder_ids = drive_folders(drive, exports[0]["drive_folder"])
        remote = {}
        for folder_id in folder_ids:
            remote.update(drive_files(drive, folder_id))
        for item in exports:
            output = output_root / item["file_name"]
            if not output.exists() and item["file_name"] in remote:
                download(drive, remote[item["file_name"]], output)
                print(f"Downloaded {output}", flush=True)
        count = sum((output_root / item["file_name"]).exists() for item in exports)
        print(f"Local JRC rasters {count}/{len(exports)}", flush=True)
        if count == len(exports) or args.once:
            return
        time.sleep(max(5, args.poll_seconds))


if __name__ == "__main__":
    main()
