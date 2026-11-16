#!/usr/bin/env python3
"""Regenerate portfolio city raster products with GBA.Height."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data/cities/city_manifest.csv"
STATUS = ROOT / "outputs/cities/gba_height_processing_status.json"
PROCESSOR = ROOT / "scripts/process_portfolio_city_rasters.py"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", action="append", help="Process only specified city slugs")
    parser.add_argument("--include-juba", action="store_true", help="Also rerun Juba in the portfolio tree")
    parser.add_argument("--continue-on-error", action="store_true")
    args = parser.parse_args()
    cities = pd.read_csv(MANIFEST).city_slug.tolist()
    if args.city:
        unknown = set(args.city) - set(cities)
        if unknown:
            raise ValueError(f"Unknown city slugs: {sorted(unknown)}")
        cities = args.city
    elif not args.include_juba:
        cities = [city for city in cities if city != "juba"]

    status = json.loads(STATUS.read_text()) if STATUS.exists() else {}
    failures = []
    for number, city in enumerate(cities, 1):
        print(f"[{number}/{len(cities)}] Processing {city}", flush=True)
        started = datetime.now(timezone.utc).isoformat()
        result = subprocess.run(
            [sys.executable, "-u", str(PROCESSOR), "--city", city, "--force"],
            cwd=ROOT,
        )
        status[city] = {
            "started_at_utc": started,
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            "returncode": result.returncode,
        }
        STATUS.write_text(json.dumps(status, indent=2) + "\n")
        if result.returncode:
            failures.append(city)
            if not args.continue_on_error:
                raise SystemExit(result.returncode)
    if failures:
        raise RuntimeError(f"Failed cities: {failures}")
    print(f"Completed {len(cities)} cities", flush=True)


if __name__ == "__main__":
    main()
