#!/usr/bin/env python3
"""Monitor Earth Engine exports and download completed GeoTIFFs from Drive."""

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


def drive_folder(service, name: str) -> str | None:
    escaped = name.replace("'", "\\'")
    response = service.files().list(
        q=(
            f"name = '{escaped}' and "
            "mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        ),
        spaces="drive",
        fields="files(id,name,createdTime)",
        orderBy="createdTime desc",
        pageSize=100,
    ).execute()
    files = response.get("files", [])
    return files[0]["id"] if files else None


def drive_files(service, folder_id: str) -> dict[str, str]:
    found = {}
    page_token = None
    while True:
        response = service.files().list(
            q=f"'{folder_id}' in parents and trashed = false",
            spaces="drive",
            fields="nextPageToken,files(id,name,size)",
            pageSize=1000,
            pageToken=page_token,
        ).execute()
        for item in response.get("files", []):
            found[item["name"]] = item["id"]
        page_token = response.get("nextPageToken")
        if not page_token:
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
    args = parser.parse_args()

    manifest_path = PIPELINE / "data/earth_engine_export_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    exports = manifest["exports"]
    if not manifest.get("submitted") or not all(item.get("task_id") for item in exports):
        raise RuntimeError("Earth Engine export manifest does not contain submitted task IDs")

    ee.Initialize(project=args.project)
    credentials = ee.data.get_persistent_credentials()
    drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
    folder_name = exports[0]["drive_folder"]
    output_root = PIPELINE / "data/rasters/earth_engine"
    output_root.mkdir(parents=True, exist_ok=True)
    task_ids = [item["task_id"] for item in exports]
    expected = {item["file_name"]: item for item in exports}

    while True:
        statuses = task_statuses(task_ids)
        by_id = {status["id"]: status for status in statuses}
        counts = Counter(status.get("state", "UNKNOWN") for status in statuses)
        folder_id = drive_folder(drive, folder_name)
        remote = drive_files(drive, folder_id) if folder_id else {}
        downloaded_now = 0
        for name, item in expected.items():
            output = output_root / name
            if output.exists() or name not in remote:
                continue
            download_file(drive, remote[name], output)
            downloaded_now += 1
            print(f"Downloaded: {output}", flush=True)

        local_count = sum((output_root / name).exists() for name in expected)
        print(
            f"Tasks {dict(sorted(counts.items()))}; Drive files {len(remote)}; "
            f"local rasters {local_count}/{len(expected)}; downloaded now {downloaded_now}",
            flush=True,
        )
        failures = [
            {"task_id": task_id, "file_name": item["file_name"],
             "state": by_id.get(task_id, {}).get("state", "UNKNOWN"),
             "error_message": by_id.get(task_id, {}).get("error_message")}
            for item, task_id in ((item, item["task_id"]) for item in exports)
            if by_id.get(task_id, {}).get("state") in {"FAILED", "CANCELLED"}
        ]
        (PIPELINE / "data/earth_engine_failures.json").write_text(
            json.dumps(failures, indent=2) + "\n"
        )
        terminal = counts.get("COMPLETED", 0) + counts.get("FAILED", 0) + counts.get("CANCELLED", 0)
        if args.once or terminal == len(exports):
            if counts.get("COMPLETED", 0) == len(exports) and local_count == len(exports):
                print("All Earth Engine exports completed and downloaded.", flush=True)
                return
            if terminal == len(exports):
                raise RuntimeError(
                    f"Earth Engine exports ended with {len(failures)} failures and "
                    f"{local_count}/{len(exports)} downloaded rasters"
                )
            return
        time.sleep(max(5, args.poll_seconds))


if __name__ == "__main__":
    main()
