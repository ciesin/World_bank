#!/usr/bin/env python3
"""Restart-safe batch runner for all city raster/segment analyses."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]


def run(row):
    command = [
        str(ROOT / ".venv/bin/python"), "-u",
        str(ROOT / "scripts/process_portfolio_city_rasters.py"),
        "--city", row.city_slug,
    ]
    result = subprocess.run(command, cwd=ROOT, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=7200)
    return {
        "city_slug": row.city_slug, "returncode": result.returncode,
        "log": result.stdout[-3000:],
    }


def main():
    manifest = pd.read_csv(ROOT / "data/cities/city_manifest.csv").sort_values("source_area_km2")
    rows = [r for r in manifest.itertuples() if not (
        ROOT / "outputs/cities" / r.city_slug / "analysis/raster_summary.json"
    ).exists()]
    results = []
    # Sequential execution keeps 30 m fine-grid memory bounded and is more stable
    # for remote COG range requests than many simultaneous GDAL clients.
    complete = 93 - len(rows)
    for row in rows:
        result = run(row)
        results.append(result)
        complete += 1
        print(f"[{complete:02d}/93] {row.city_slug}: "
              f"{'ok' if result['returncode'] == 0 else 'FAILED'}", flush=True)
        (ROOT / "outputs/cities/raster_batch_status.json").write_text(
            json.dumps(results, indent=2) + "\n"
        )
    failures = [r for r in results if r["returncode"]]
    print(f"Complete: {complete}; failures: {len(failures)}", flush=True)
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
