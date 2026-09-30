#!/usr/bin/env python3
"""Monitor submitted exports and download completed GeoTIFFs from Drive."""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path

import ee
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from common import PIPELINE


def task_statuses(task_ids: list[str]) -> list[dict]:
    statuses = []
    for start in range(0, len(task_ids), 100):
        statuses.extend(ee.data.getTaskStatus(task_ids[start:start + 100]))
    return statuses


def drive_folders(service, name: str) -> list[str]:
    escaped = name.replace("'", "\\'")
    response = service.files().list(
        q=f"name = '{escaped}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
        spaces="drive", fields="files(id,name,createdTime)", orderBy="createdTime desc", pageSize=100,
    ).execute()
    return [item["id"] for item in response.get("files", [])]


def drive_files(service, folder_id: str) -> dict[str, str]:
    found = {}
    token = None
    while True:
        response = service.files().list(
            q=f"'{folder_id}' in parents and trashed = false",
            spaces="drive", fields="nextPageToken,files(id,name,size)",
            pageSize=1000, pageToken=token,
        ).execute()
        found.update({item["name"]: item["id"] for item in response.get("files", [])})
        token = response.get("nextPageToken")
        if not token:
            return found


def download_file(service, file_id: str, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".partial")
    request = service.files().get_media(fileId=file_id)
    with temporary.open("wb") as target:
        downloader = MediaIoBaseDownload(target, request, chunksize=32 * 1024 * 1024)
        done = False
        while not done:
            _, done = downloader.next_chunk(num_retries=5)
    temporary.replace(output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--skip-status", action="store_true",
                        help="Poll Drive only; avoids slow Earth Engine batch-status calls")
    args = parser.parse_args()
    manifest = json.loads((PIPELINE / "data" / "earth_engine_export_manifest.json").read_text())
    exports = manifest["exports"]
    if not manifest.get("submitted") or not exports or not all(item.get("task_id") for item in exports):
        raise RuntimeError("The manifest does not contain submitted Earth Engine task IDs")
    ee.Initialize(project=args.project)
    credentials = ee.data.get_persistent_credentials()
    drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
    output_root = PIPELINE / "data" / "rasters" / "earth_engine"
    output_root.mkdir(parents=True, exist_ok=True)
    task_ids = [item["task_id"] for item in exports]

    while True:
        statuses = [] if args.skip_status else task_statuses(task_ids)
        counts = Counter(status.get("state", "UNKNOWN") for status in statuses)
        folder_ids = drive_folders(drive, exports[0]["drive_folder"])
        remote = {}
        for folder_id in folder_ids:
            remote.update(drive_files(drive, folder_id))
        for item in exports:
            name = item["file_name"]
            output = output_root / name
            if output.exists() or name not in remote:
                continue
            download_file(drive, remote[name], output)
            print(f"Downloaded {output}", flush=True)
        local_count = sum((output_root / item["file_name"]).exists() for item in exports)
        state_text = f"Task states {dict(counts)}; " if statuses else ""
        print(f"{state_text}Drive folders {len(folder_ids)}; local rasters {local_count}/{len(exports)}", flush=True)
        if local_count == len(exports):
            print("All Earth Engine exports downloaded.", flush=True)
            return
        terminal = sum(counts.get(state, 0) for state in ("COMPLETED", "FAILED", "CANCELLED"))
        if args.once or terminal == len(exports):
            break
        time.sleep(max(5, args.poll_seconds))


if __name__ == "__main__":
    main()
