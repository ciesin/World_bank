#!/usr/bin/env python3
"""Process remaining road cities in reverse order alongside the primary worker."""

import subprocess
import sys

from common import PIPELINE, city_output_stem, load_buffers, load_config


config = load_config()
fields = config["city_fields"]
script = PIPELINE / "scripts" / "analyze_roads.py"
for city in reversed(list(load_buffers(config).itertuples(index=False))):
    stem = city_output_stem(city, fields)
    output = PIPELINE / "outputs" / "segment_roads" / f"{stem}_segment_roads.parquet"
    if output.exists():
        continue
    subprocess.run(
        [sys.executable, str(script), "--city", str(getattr(city, fields["name"])), "--skip-aggregate"],
        check=True,
    )
    count = len(list((PIPELINE / "outputs" / "segment_roads").glob("*_segment_roads.parquet")))
    print(f"Analyzed {count}/93 cities", flush=True)
