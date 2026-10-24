#!/usr/bin/env python3
import argparse,subprocess,sys
from pathlib import Path
SCRIPTS=Path(__file__).resolve().parent
STAGES={"prepare-segments":"prepare_segments.py","download-osm":"download_osm_roads.py","download-overture":"download_overture_roads.py","download-microsoft":"download_microsoft_roads.py","analyze":"analyze_roads.py","plot":"plot_results.py"}
parser=argparse.ArgumentParser(); parser.add_argument("stage",choices=STAGES); args,flags=parser.parse_known_args(); raise SystemExit(subprocess.call([sys.executable,str(SCRIPTS/STAGES[args.stage]),*flags]))
