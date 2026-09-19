"""
Benchmark runner: executes the query catalog against a client, times each run,
and produces per-query statistics (cold, warm-mean, p50/p95/p99, stddev).

Fairness rules:
  * Identical driver for both engines (Bolt).
  * First `warmup` executions are recorded but reported separately as "cold".
  * Remaining `iterations - warmup` are the "warm" sample used for percentiles.
  * A query that errors (e.g. Neptune meeting shortestPath) is recorded as a
    failure with the error text — not silently dropped. Compatibility is a
    first-class result, not an exception to hide.
"""
from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, asdict
from typing import Optional

from .config import CONFIG
from .queries import BenchQuery, QUERIES


@dataclass
class QueryResult:
    key: str
    title: str
    category: str
    database: str
    variant: str            # "cypher" | "opencypher"
    supported: bool         # did it run without error?
    error: Optional[str]
    cold_ms: Optional[float]
    warm_mean_ms: Optional[float]
    p50_ms: Optional[float]
    p95_ms: Optional[float]
    p99_ms: Optional[float]
    stddev_ms: Optional[float]
    rows: Optional[int]
    iterations: int


def _percentile(sorted_vals, pct):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def _time_once(client, cypher, params, timeout_s):
    t0 = time.perf_counter()
    rows = client.run(cypher, params)
    dt = (time.perf_counter() - t0) * 1000.0
    return dt, len(rows)


def run_one(client, q: BenchQuery, params: dict, variant: str,
            iterations: int, warmup: int, timeout_s: int) -> QueryResult:
    cypher = q.cypher if variant == "cypher" else q.opencypher

    base = QueryResult(
        key=q.key, title=q.title, category=q.category, database=client.name,
        variant=variant, supported=False, error=None, cold_ms=None,
        warm_mean_ms=None, p50_ms=None, p95_ms=None, p99_ms=None,
        stddev_ms=None, rows=None, iterations=iterations,
    )

    if cypher is None:
        base.error = "No equivalent query for this engine"
        return base

    samples = []
    cold = None
    rows_seen = None
    try:
        for i in range(iterations):
            dt, nrows = _time_once(client, cypher, params, timeout_s)
            rows_seen = nrows
            if i < warmup:
                if cold is None:
                    cold = round(dt, 3)
            else:
                samples.append(dt)
    except Exception as e:
        base.error = str(e)[:300]
        base.cold_ms = cold
        return base

    base.supported = True
    base.cold_ms = cold
    base.rows = rows_seen
    if samples:
        samples_sorted = sorted(samples)
        base.warm_mean_ms = round(statistics.fmean(samples), 3)
        base.p50_ms = round(_percentile(samples_sorted, 0.50), 3)
        base.p95_ms = round(_percentile(samples_sorted, 0.95), 3)
        base.p99_ms = round(_percentile(samples_sorted, 0.99), 3)
        base.stddev_ms = round(statistics.pstdev(samples), 3) if len(samples) > 1 else 0.0
    return base


def load_sample_params(data_dir) -> dict:
    import csv
    from pathlib import Path
    p = Path(data_dir) / "sample_params.csv"
    params = {}
    if p.exists():
        with open(p, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                params[row["param"]] = row["value"]
    return params


def run_suite(client, variant: str, queries=None) -> list[QueryResult]:
    """variant: 'cypher' for Neo4j, 'opencypher' for Neptune."""
    queries = queries or QUERIES
    rc = CONFIG.run
    params = load_sample_params(CONFIG.data.data_dir)

    results = []
    print(f"\n  Running {len(queries)} queries against {client.name} "
          f"({variant}), {rc.iterations} iterations each...")
    for q in queries:
        r = run_one(client, q, params, variant,
                    rc.iterations, rc.warmup, rc.timeout_s)
        status = "ok " if r.supported else "FAIL"
        warm = f"{r.warm_mean_ms:>8.2f}ms" if r.warm_mean_ms is not None else "     n/a"
        print(f"    [{status}] {q.key:26s} {warm}"
              + ("" if r.supported else f"  ({r.error[:50]})"))
        results.append(r)
    return results


def results_to_dicts(results: list[QueryResult]) -> list[dict]:
    return [asdict(r) for r in results]
