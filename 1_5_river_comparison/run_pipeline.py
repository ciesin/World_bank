#!/usr/bin/env python3
"""Small stage runner for the water comparison pipeline."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parent
STAGES = {
    "audit": "audit_inputs.py",
    "prepare-segments": "prepare_segments.py",
    "prepare-grwl": "prepare_grwl.py",
    "download-osm": "download_osm.py",
    "download-osm-geofabrik": "download_geofabrik_osm.py",
    "plan-jrc": "submit_jrc_exports.py",
    "download-jrc": "download_jrc_exports.py",
    "analyze": "analyze_cities.py",
    "plot": "plot_results.py",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=STAGES)
    args, flags = parser.parse_known_args()
    command = [sys.executable, str(SCRIPTS / STAGES[args.stage]), *flags]
    raise SystemExit(subprocess.call(command))


if __name__ == "__main__":
    main()
