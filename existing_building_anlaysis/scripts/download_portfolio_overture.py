#!/usr/bin/env python3
"""Download Overture building extracts for every prepared city AOI."""

from __future__ import annotations

import concurrent.futures
import json
import subprocess
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data/cities/city_manifest.csv"
CLI = ROOT / ".venv/bin/overturemaps"
WORKERS = 3


def download(row) -> dict:
    city_dir = ROOT / "data/cities" / row.city_slug / "sources"
    city_dir.mkdir(parents=True, exist_ok=True)
    output = city_dir / "overture_buildings.parquet"
    if output.exists() and output.stat().st_size > 10_000:
        return {"city_slug": row.city_slug, "status": "existing", "bytes": output.stat().st_size}
    partial = city_dir / "overture_buildings.partial.parquet"
    bbox = f"{row.west},{row.south},{row.east},{row.north}"
    command = [
        str(CLI), "download", "--bbox", bbox, "-f", "geoparquet",
        "--type", "building", "-o", str(partial),
        "--connect_timeout", "60", "--request_timeout", "600",
    ]
    try:
        completed = subprocess.run(
            command, cwd=ROOT, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=3600,
        )
        if completed.returncode != 0:
            return {
                "city_slug": row.city_slug, "status": "failed",
                "returncode": completed.returncode, "log": completed.stdout[-2000:],
            }
        partial.replace(output)
        state = partial.with_suffix(partial.suffix + ".state")
        if state.exists():
            state.replace(output.with_suffix(output.suffix + ".state"))
        return {
            "city_slug": row.city_slug, "status": "downloaded",
            "bytes": output.stat().st_size, "log": completed.stdout[-500:],
        }
    except Exception as exc:
        return {"city_slug": row.city_slug, "status": "failed", "error": repr(exc)}


def main() -> None:
    manifest = pd.read_csv(MANIFEST)
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {executor.submit(download, row): row for row in manifest.itertuples()}
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            result = future.result()
            results.append(result)
            print(f"[{index:02d}/93] {result['city_slug']}: {result['status']}", flush=True)
            Path(ROOT / "data/cities/overture_download_status.json").write_text(
                json.dumps(results, indent=2) + "\n"
            )
    failed = [r for r in results if r["status"] == "failed"]
    print(f"Completed: {len(results) - len(failed)}; failed: {len(failed)}", flush=True)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
