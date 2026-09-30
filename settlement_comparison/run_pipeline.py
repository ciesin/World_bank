#!/usr/bin/env python3
"""Run selected stages of the 93-city settlement-footprint comparison."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parent
PIPELINE = SCRIPTS.parent


def run(name: str, arguments: list[str] | None = None) -> None:
    command = [sys.executable, str(SCRIPTS / name), *(arguments or [])]
    print("Running:", " ".join(command), flush=True)
    subprocess.run(command, cwd=PIPELINE, check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "stage", choices=["audit", "acquire-vectors", "plan-ee", "prepare-wsf", "build", "analyze", "all-local"]
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--allow-missing", action="store_true")
    args = parser.parse_args()
    if args.stage == "audit":
        run("audit_inputs.py")
    elif args.stage == "acquire-vectors":
        run("acquire_vector_sources.py", ["--force"] if args.force else [])
    elif args.stage == "plan-ee":
        run("submit_earth_engine_exports.py")
    elif args.stage == "prepare-wsf":
        run("prepare_wsf_rasters.py", ["--force"] if args.force else [])
    elif args.stage == "build":
        run("build_product_footprints.py", ["--allow-missing"] if args.allow_missing else [])
    elif args.stage == "analyze":
        run("analyze_results.py")
    elif args.stage == "all-local":
        run("audit_inputs.py")
        run("acquire_vector_sources.py", ["--force"] if args.force else [])
        run("prepare_wsf_rasters.py", ["--force"] if args.force else [])
        run("build_product_footprints.py", ["--allow-missing"] if args.allow_missing else [])
        run("analyze_results.py")


if __name__ == "__main__":
    main()
