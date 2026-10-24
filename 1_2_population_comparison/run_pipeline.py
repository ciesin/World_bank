#!/usr/bin/env python3
"""Run one explicit stage of the population pipeline."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parent
PIPELINE = SCRIPTS.parent


def run(script: str, arguments: list[str] | None = None) -> None:
    command = [sys.executable, str(SCRIPTS / script), *(arguments or [])]
    print("Running:", " ".join(command), flush=True)
    subprocess.run(command, cwd=PIPELINE, check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "stage", choices=["audit", "plan-ee", "prepare-segments", "prepare-nighttime", "aggregate", "analyze"]
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--allow-missing", action="store_true")
    args = parser.parse_args()
    flags = ["--force"] if args.force else []
    if args.stage == "audit":
        run("audit_inputs.py")
    elif args.stage == "plan-ee":
        run("submit_earth_engine_exports.py")
    elif args.stage == "prepare-segments":
        run("prepare_segments.py", flags)
    elif args.stage == "prepare-nighttime":
        run("prepare_local_nighttime.py", flags)
    elif args.stage == "aggregate":
        if args.allow_missing:
            flags.append("--allow-missing")
        run("aggregate_population.py", flags)
    elif args.stage == "analyze":
        run("analyze_results.py")


if __name__ == "__main__":
    main()
