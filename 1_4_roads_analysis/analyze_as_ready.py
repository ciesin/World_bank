#!/usr/bin/env python3
"""Analyze each city as soon as all three road inputs are available."""

import subprocess,sys,time
from common import PIPELINE,city_output_stem,load_buffers,load_config,prepared_segment_path,road_path

config=load_config(); fields=config["city_fields"]; buffers=load_buffers(config); script=PIPELINE/"scripts"/"analyze_roads.py"
while True:
    for city in buffers.itertuples(index=False):
        stem=city_output_stem(city,fields); output=PIPELINE/"outputs"/"segment_roads"/f"{stem}_segment_roads.parquet"
        required=[prepared_segment_path(stem),*[road_path(source,stem) for source in ("osm","overture","microsoft")]]
        if not output.exists() and all(path.exists() for path in required): subprocess.run([sys.executable,str(script),"--city",str(getattr(city,fields["name"])),"--skip-aggregate"],check=True)
    count=len(list((PIPELINE/"outputs"/"segment_roads").glob("*_segment_roads.parquet"))); print(f"Analyzed {count}/93 cities",flush=True)
    if count==len(buffers): break
    time.sleep(30)
