#!/usr/bin/env python3
"""Download an Overture building extract for one prepared city AOI."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", required=True, help="Prepared city slug")
    parser.add_argument("--overture-cli", help="Path to the overturemaps executable")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    city_root = WORKFLOW / "data" / args.city
    metadata = json.loads((city_root / "inputs/metadata.json").read_text())
    output = city_root / "sources/overture_buildings.parquet"
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and not args.force:
        print(f"Existing: {output}")
        return
    cli = args.overture_cli or shutil.which("overturemaps")
    if not cli:
        local_cli = WORKFLOW / ".venv/bin/overturemaps"
        cli = str(local_cli) if local_cli.exists() else None
    if not cli:
        raise FileNotFoundError(
            "The overturemaps executable was not found. Install requirements.txt "
            "or pass --overture-cli."
        )
    partial = output.with_name("overture_buildings.partial.parquet")
    if partial.exists():
        partial.unlink()
    bbox = ",".join(str(value) for value in metadata["bbox_wgs84"])
    command = [
        cli, "download", "--bbox", bbox, "-f", "geoparquet",
        "--type", "building", "-o", str(partial),
        "--connect_timeout", "60", "--request_timeout", "600",
    ]
    completed = subprocess.run(command, cwd=WORKFLOW, timeout=7200)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    partial.replace(output)
    partial_state = partial.with_suffix(partial.suffix + ".state")
    if partial_state.exists():
        partial_state.replace(output.with_suffix(output.suffix + ".state"))
    print(f"Downloaded: {output}")


if __name__ == "__main__":
    main()
