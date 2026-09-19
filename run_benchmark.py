#!/usr/bin/env python3
"""
Main entry point for the Neo4j vs Neptune data-lineage benchmark.

Usage (from the project root, inside your virtualenv):

    python run_benchmark.py generate          # build the CSV graph
    python run_benchmark.py load --db neo4j    # load into Neo4j
    python run_benchmark.py load --db neptune  # load into Neptune (if configured)
    python run_benchmark.py bench --db neo4j   # run queries on Neo4j
    python run_benchmark.py bench --db neptune # run queries on Neptune
    python run_benchmark.py cost               # print/write the TCO model
    python run_benchmark.py report             # build the HTML report
    python run_benchmark.py all --db neo4j     # generate+load+bench+cost+report

Neptune is only touched if NEPTUNE_ENDPOINT is set (env var or config.py).
Everything writes JSON into ./results so `report` can combine runs.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bench.config import CONFIG
from bench import data_generator, loader, runner, cost_model
from bench.queries import QUERIES


def _results_dir() -> Path:
    d = Path(CONFIG.run.results_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def cmd_generate(args):
    print("Generating capital-markets data-lineage graph...")
    stats = data_generator.generate()
    print("\nGraph statistics:")
    for k, v in stats.items():
        if not k.startswith("_"):
            print(f"  {k:16s} {v:>10,}")
    print(f"  {'TOTAL nodes':16s} {stats['_total_nodes']:>10,}")
    print(f"  {'TOTAL edges':16s} {stats['_total_rels']:>10,}")
    (_results_dir() / "graph_stats.json").write_text(json.dumps(stats, indent=2))


def _make_client(db: str):
    if db == "neo4j":
        from bench.clients import Neo4jClient
        return Neo4jClient()
    elif db == "neptune":
        if not CONFIG.neptune.enabled:
            print("ERROR: Neptune not configured. Set NEPTUNE_ENDPOINT "
                  "(see docs/AWS_NEPTUNE_SETUP.md).")
            sys.exit(2)
        from bench.clients import make_neptune_client
        return make_neptune_client()
    raise ValueError(db)


def cmd_load(args):
    client = _make_client(args.db)
    print(f"Connecting to {client.name}...")
    try:
        client.verify()
    except Exception as e:
        print(f"ERROR: could not connect to {client.name}: {e}")
        sys.exit(2)
    print(f"Loading graph into {client.name}...")
    if getattr(args, "real", False):
        from bench.real_dataset import load_real
        stats = load_real(client, data_dir=os.environ.get("BENCH_DATA_DIR", "data_real"),
                          do_wipe=not args.no_wipe)
    else:
        stats = loader.load_all(client, do_wipe=not args.no_wipe)
    out = _results_dir() / f"load_{args.db}.json"
    out.write_text(json.dumps(stats, indent=2))
    print(f"Wrote {out}")
    client.close()


def cmd_bench(args):
    client = _make_client(args.db)
    variant = "cypher" if args.db == "neo4j" else "opencypher"
    print(f"Connecting to {client.name}...")
    try:
        client.verify()
    except Exception as e:
        print(f"ERROR: could not connect to {client.name}: {e}")
        sys.exit(2)
    queries = None
    if getattr(args, "real", False):
        from bench.real_dataset import REAL_QUERIES
        queries = REAL_QUERIES
        print("  Using the REAL-dataset query set.")
    results = runner.run_suite(client, variant, queries=queries)
    payload = {
        "database": client.name,
        "variant": variant,
        "config": {
            "iterations": CONFIG.run.iterations,
            "warmup": CONFIG.run.warmup,
        },
        "results": runner.results_to_dicts(results),
    }
    out = _results_dir() / f"bench_{args.db}.json"
    out.write_text(json.dumps(payload, indent=2))
    print(f"\nWrote {out}")

    # quick summary
    ok = sum(1 for r in results if r.supported)
    print(f"\nSummary for {client.name}: {ok}/{len(results)} queries ran successfully.")
    unsupported = [r.key for r in results if not r.supported]
    if unsupported:
        print("  Could not run (engine limitation or error):")
        for k in unsupported:
            print(f"    - {k}")
    client.close()


def cmd_cost(args):
    report = cost_model.build_report()
    cost_model.print_report(report)
    out = _results_dir() / "cost.json"
    out.write_text(json.dumps(report, indent=2))
    print(f"\nWrote {out}")


def cmd_report(args):
    from bench.report import build_html
    path = build_html(_results_dir())
    print(f"Wrote {path}")


def cmd_all(args):
    cmd_generate(args)
    cmd_load(args)
    cmd_bench(args)
    cmd_cost(args)
    cmd_report(args)


def main():
    p = argparse.ArgumentParser(description="Neo4j vs Neptune lineage benchmark")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("generate", help="build the CSV graph")
    sp.set_defaults(func=cmd_generate)

    sp = sub.add_parser("load", help="load graph into a database")
    sp.add_argument("--db", choices=["neo4j", "neptune"], required=True)
    sp.add_argument("--no-wipe", action="store_true", help="do not clear first")
    sp.add_argument("--real", action="store_true",
                    help="load the real dataset CSVs (from BENCH_DATA_DIR)")
    sp.set_defaults(func=cmd_load)

    sp = sub.add_parser("bench", help="run the query suite")
    sp.add_argument("--db", choices=["neo4j", "neptune"], required=True)
    sp.add_argument("--real", action="store_true",
                    help="use the real-dataset query set (needs a real load)")
    sp.set_defaults(func=cmd_bench)

    sp = sub.add_parser("cost", help="compute the TCO model")
    sp.set_defaults(func=cmd_cost)

    sp = sub.add_parser("report", help="build the HTML report")
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("all", help="generate + load + bench + cost + report")
    sp.add_argument("--db", choices=["neo4j", "neptune"], required=True)
    sp.add_argument("--no-wipe", action="store_true")
    sp.set_defaults(func=cmd_all)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
