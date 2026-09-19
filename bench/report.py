"""
Build a single self-contained HTML report from the JSON files in results/.

Combines whatever is present:
  * bench_neo4j.json / bench_neptune.json  (per-query latencies)
  * load_neo4j.json / load_neptune.json    (load throughput)
  * cost.json                              (TCO model)
  * graph_stats.json                       (dataset size)

If only Neo4j was run (no Neptune configured), the report still renders and
notes Neptune as "not run" rather than failing.
"""
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone


def _load(path: Path):
    if path.exists():
        return json.loads(path.read_text())
    return None


def _fmt(v, suffix="ms"):
    if v is None:
        return "—"
    return f"{v:,.2f}{suffix}"


def build_html(results_dir: Path) -> Path:
    results_dir = Path(results_dir)
    neo = _load(results_dir / "bench_neo4j.json")
    nep = _load(results_dir / "bench_neptune.json")
    load_neo = _load(results_dir / "load_neo4j.json")
    load_nep = _load(results_dir / "load_neptune.json")
    cost = _load(results_dir / "cost.json")
    gstats = _load(results_dir / "graph_stats.json")

    # index query results by key
    def index(payload):
        if not payload:
            return {}
        return {r["key"]: r for r in payload["results"]}

    neo_idx = index(neo)
    nep_idx = index(nep)
    all_keys = [r["key"] for r in (neo["results"] if neo else nep["results"])] if (neo or nep) else []

    rows_html = []
    for k in all_keys:
        n = neo_idx.get(k)
        p = nep_idx.get(k)
        title = (n or p)["title"]
        category = (n or p)["category"]
        neo_warm = n["warm_mean_ms"] if n else None
        nep_warm = p["warm_mean_ms"] if p else None
        neo_ok = n["supported"] if n else None
        nep_ok = p["supported"] if p else None

        # winner
        winner = ""
        if neo_warm is not None and nep_warm is not None:
            if neo_warm < nep_warm:
                winner = "Neo4j"
            elif nep_warm < neo_warm:
                winner = "Neptune"
            else:
                winner = "tie"

        nep_cell = _fmt(nep_warm)
        if p and not p["supported"]:
            nep_cell = f'<span class="fail">not supported</span>'
        neo_cell = _fmt(neo_warm)
        if n and not n["supported"]:
            neo_cell = f'<span class="fail">error</span>'

        rows_html.append(f"""
        <tr>
          <td class="mono">{k}</td>
          <td>{title}<div class="cat">{category}</div></td>
          <td class="num">{neo_cell}</td>
          <td class="num">{nep_cell}</td>
          <td class="win">{winner}</td>
        </tr>""")

    # cost section
    cost_html = "<p>No cost model run.</p>"
    if cost:
        crows = []
        for name, s in cost["scenarios"].items():
            crows.append(f"""
            <tr>
              <td>{name}</td>
              <td class="num">${s['monthly_total']:,.2f}</td>
              <td class="num">${s['annual_total']:,.2f}</td>
              <td class="num">${s['horizon_total']:,.2f}</td>
            </tr>""")
        cost_html = f"""
        <table>
          <thead><tr><th>Scenario</th><th>Monthly</th><th>Annual</th>
          <th>{cost['months']//12}-Year</th></tr></thead>
          <tbody>{''.join(crows)}</tbody>
        </table>
        <p class="note">Cheapest over horizon: <b>{cost['cheapest']}</b>.
        All inputs are editable in <span class="mono">bench/cost_model.py</span>.</p>
        """

    # load section
    load_html = ""
    if load_neo or load_nep:
        def load_line(label, d):
            if not d:
                return f"<li>{label}: not run</li>"
            return (f"<li>{label}: {d.get('load_seconds_total','?')}s total "
                    f"(nodes {d.get('load_seconds_nodes','?')}s, "
                    f"rels {d.get('load_seconds_rels','?')}s)</li>")
        load_html = f"""
        <h2>Load throughput</h2>
        <ul>
          {load_line('Neo4j', load_neo)}
          {load_line('Neptune', load_nep)}
        </ul>
        <p class="note">Both loads use identical batched UNWIND Cypher over Bolt
        for a fair comparison (not engine-specific bulk loaders).</p>
        """

    # dataset section
    ds_html = ""
    if gstats:
        ds_html = f"""
        <h2>Dataset</h2>
        <p>Synthetic capital-markets data-lineage graph:
        <b>{gstats.get('_total_nodes',0):,}</b> nodes,
        <b>{gstats.get('_total_rels',0):,}</b> edges.
        Deterministic (seeded) so every run is identical and comparable.</p>
        """

    neo_status = "run" if neo else "not run"
    nep_status = "run" if nep else "not run (Neptune not configured)"

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Neo4j vs Neptune — Data Lineage Benchmark</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif;
    max-width: 960px; margin: 2rem auto; padding: 0 1rem; line-height: 1.5; }}
  h1 {{ font-size: 1.6rem; }}
  h2 {{ margin-top: 2rem; border-bottom: 2px solid #8884; padding-bottom: .3rem; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
  th, td {{ border: 1px solid #8884; padding: .5rem .6rem; text-align: left; }}
  th {{ background: #8882; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .mono {{ font-family: ui-monospace, Menlo, Consolas, monospace; font-size: .85em; }}
  .cat {{ font-size: .75em; opacity: .6; }}
  .win {{ font-weight: 600; }}
  .fail {{ color: #c0392b; font-weight: 600; }}
  .note {{ font-size: .9em; opacity: .8; }}
  .status {{ display: inline-block; padding: .1rem .5rem; border-radius: 4px;
    background: #8882; font-size: .85em; margin-right: .5rem; }}
</style></head><body>
<h1>Neo4j vs Amazon Neptune — Capital-Markets Data-Lineage Benchmark</h1>
<p class="note">Generated {generated}</p>
<p>
  <span class="status">Neo4j: {neo_status}</span>
  <span class="status">Neptune: {nep_status}</span>
</p>

{ds_html}

<h2>Query latency (warm mean, lower is better)</h2>
<table>
  <thead><tr><th>Key</th><th>Query</th><th class="num">Neo4j</th>
  <th class="num">Neptune</th><th>Faster</th></tr></thead>
  <tbody>{''.join(rows_html)}</tbody>
</table>
<p class="note">Warm mean excludes the first (cold) execution. Each query runs
multiple iterations; see the JSON in <span class="mono">results/</span> for
cold, p50, p95, p99 and stddev.</p>

<h2>Query complexity &amp; compatibility</h2>
<p>Every query is authored in idiomatic Neo4j Cypher and again in
Neptune-compatible openCypher. Where Neptune cannot express a construct
(notably <span class="mono">shortestPath()</span> and APOC procedures), the
openCypher variant either uses a supported rewrite or is flagged as
<span class="fail">not supported</span> above — that gap is itself a result.</p>

{load_html}

<h2>3-year total cost of ownership</h2>
{cost_html}

<h2>How to read this</h2>
<ul>
  <li><b>Performance:</b> the latency table. Neo4j (in-memory, local) will
  usually win raw latency; Neptune trades some latency for being fully managed.</li>
  <li><b>Cost:</b> the TCO table. Neptune serverless can be cheapest at low/
  variable load; self-hosted Neo4j cost is dominated by ops effort (editable).</li>
  <li><b>Complexity:</b> the compatibility column. Neo4j runs everything;
  Neptune needs rewrites and cannot do shortestPath()/APOC.</li>
</ul>
</body></html>"""

    out = results_dir / "report.html"
    out.write_text(html, encoding="utf-8")
    return out
