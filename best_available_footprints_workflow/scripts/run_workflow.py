#!/usr/bin/env python3
"""Prepare, download, and build best-available footprints for one city."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import unicodedata
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parent
WORKFLOW = SCRIPTS.parent


def slugify(value: str) -> str:
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()


def run(command: list[str]) -> None:
    print("Running:", " ".join(command), flush=True)
    completed = subprocess.run(command, cwd=WORKFLOW)
    if completed.returncode:
        raise SystemExit(completed.returncode)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city-name", required=True)
    parser.add_argument("--country", default="")
    parser.add_argument("--city-slug")
    parser.add_argument("--aoi", required=True)
    parser.add_argument("--aoi-layer")
    parser.add_argument("--segments")
    parser.add_argument("--segments-layer")
    parser.add_argument("--analysis-crs")
    parser.add_argument("--overture-cli")
    parser.add_argument("--skip-overture", action="store_true")
    parser.add_argument("--skip-3d-globfp", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    slug = args.city_slug or slugify(args.city_name)

    prepare = [
        sys.executable, str(SCRIPTS / "prepare_city.py"),
        "--city-name", args.city_name, "--country", args.country,
        "--city-slug", slug, "--aoi", str(Path(args.aoi).resolve()),
    ]
    for flag, value in [
        ("--aoi-layer", args.aoi_layer),
        ("--segments", str(Path(args.segments).resolve()) if args.segments else None),
        ("--segments-layer", args.segments_layer),
        ("--analysis-crs", args.analysis_crs),
    ]:
        if value:
            prepare.extend([flag, value])
    run(prepare)

    if not args.skip_overture:
        command = [sys.executable, str(SCRIPTS / "download_overture.py"), "--city", slug]
        if args.overture_cli:
            command.extend(["--overture-cli", args.overture_cli])
        if args.force:
            command.append("--force")
        run(command)

    if not args.skip_3d_globfp:
        command = [sys.executable, str(SCRIPTS / "download_3d_globfp.py"), "--city", slug]
        if args.force:
            command.append("--force")
        run(command)

    command = [sys.executable, str(SCRIPTS / "build_integrated_footprints.py"), "--city", slug]
    if args.force:
        command.append("--force")
    run(command)
    print(f"Finished: {WORKFLOW / 'outputs' / slug}")


if __name__ == "__main__":
    main()
