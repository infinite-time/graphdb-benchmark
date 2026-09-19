#!/usr/bin/env python3
"""
Run this first: python verify_setup.py

It checks that the project structure is intact and all modules import, so you
catch a broken layout (e.g. files unzipped into the wrong place) before running
the benchmark. It does NOT need a database.
"""
import os
import sys

EXPECTED = [
    "run_benchmark.py",
    "requirements.txt",
    os.path.join("bench", "__init__.py"),
    os.path.join("bench", "config.py"),
    os.path.join("bench", "data_generator.py"),
    os.path.join("bench", "clients.py"),
    os.path.join("bench", "loader.py"),
    os.path.join("bench", "queries.py"),
    os.path.join("bench", "runner.py"),
    os.path.join("bench", "cost_model.py"),
    os.path.join("bench", "report.py"),
    os.path.join("bench", "real_dataset.py"),
]

here = os.path.dirname(os.path.abspath(__file__))
print("Project root:", here)

missing = [p for p in EXPECTED if not os.path.isfile(os.path.join(here, p))]
if missing:
    print("\nERROR: these files are missing or in the wrong place:")
    for m in missing:
        print("   -", m)
    print("\nThe 'bench' folder and its files must sit next to run_benchmark.py,")
    print("like this:\n")
    print("   graph-bench/")
    print("   |- run_benchmark.py")
    print("   |- bench/")
    print("   |   |- __init__.py")
    print("   |   |- config.py")
    print("   |   |- ... (the other modules)")
    print("   |- docs/  data/  requirements.txt  README.md")
    print("\nIf you downloaded files individually they may have been flattened.")
    print("Re-extract the .zip so the folder structure is preserved.")
    sys.exit(1)

print("All expected files present.")

sys.path.insert(0, here)
try:
    from bench.config import CONFIG
    from bench import (data_generator, loader, runner, cost_model,
                       clients, report, real_dataset)
    from bench.queries import QUERIES
except Exception as e:
    print("\nERROR importing the bench package:", e)
    print("Make sure you run this from the project root with the venv active.")
    sys.exit(1)

print(f"All modules import cleanly. Query catalog: {len(QUERIES)} queries "
      f"({sum(1 for q in QUERIES if q.neptune_native)} Neptune-native).")
print("\nSetup looks good. Next:")
print("  1. Start Neo4j (see docs/SETUP_WINDOWS.md)")
print("  2. python run_benchmark.py all --db neo4j")
