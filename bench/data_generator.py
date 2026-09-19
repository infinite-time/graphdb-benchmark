"""
Generate a deterministic, realistic *data-lineage* knowledge graph for an
investment bank's capital-markets processes, and write it to CSV files that
both loaders (Neo4j and Neptune) can consume.

Why synthetic-but-realistic instead of a random toy graph?
----------------------------------------------------------
Real data-lineage graphs have a specific shape that determines query cost:
  * a DAG (lineage flows one direction, source -> report),
  * high fan-in near reports, high fan-out near sources,
  * long-ish chains (a regulatory figure can be 5-12 transformations deep),
  * a minority of fields with no controls (the governance gaps we hunt for).
We model exactly that, seeded, so every run is identical and comparable.

Output CSVs (in <data_dir>/):
  nodes_source.csv        SourceSystem
  nodes_bu.csv            BusinessUnit
  nodes_dataset.csv       Dataset
  nodes_field.csv         Field
  nodes_transform.csv     Transformation
  nodes_report.csv        Report
  nodes_control.csv       Control
  rel_produces.csv        (SourceSystem)-[:PRODUCES]->(Dataset)
  rel_has_field.csv       (Dataset)-[:HAS_FIELD]->(Field)
  rel_owned_by.csv        (Dataset)-[:OWNED_BY]->(BusinessUnit)
  rel_derives.csv         (Field)-[:DERIVES_FROM]->(Field)
  rel_consumes.csv        (Transformation)-[:CONSUMES]->(Field)
  rel_outputs.csv         (Transformation)-[:OUTPUTS]->(Field)
  rel_feeds.csv           (Field)-[:FEEDS]->(Report)
  rel_validates.csv       (Control)-[:VALIDATES]->(Field)
"""
from __future__ import annotations

import csv
import os
import random
from pathlib import Path

from .config import CONFIG, DataConfig

BUSINESS_UNITS = [
    "Rates Trading", "Credit Trading", "FX Trading", "Equities Trading",
    "Prime Brokerage", "Risk", "Finance", "Treasury", "Regulatory Reporting",
    "Collateral Management",
]

REPORT_NAMES = [
    "FRTB-SA Capital", "FRTB-IMA Capital", "Basel III LCR", "Basel III NSFR",
    "CCAR Submission", "Volcker Metrics", "MiFID II Transaction Report",
    "EMIR Trade Report", "Liquidity Coverage", "VaR Backtest",
    "Counterparty Credit Exposure", "Leverage Ratio",
]

SOURCE_KINDS = ["Booking", "MarketData", "ReferenceData", "Settlement", "PnL"]


def _w(path: Path, header: list[str], rows) -> int:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh)
        wr.writerow(header)
        n = 0
        for r in rows:
            wr.writerow(r)
            n += 1
    return n


def generate(cfg: DataConfig | None = None) -> dict:
    cfg = cfg or CONFIG.data
    rng = random.Random(cfg.seed)
    out = Path(cfg.data_dir)
    out.mkdir(parents=True, exist_ok=True)

    stats: dict[str, int] = {}

    # ---- Nodes -----------------------------------------------------------
    # Business units
    bus = list(range(len(BUSINESS_UNITS)))
    stats["BusinessUnit"] = _w(
        out / "nodes_bu.csv", ["buId", "name"],
        ([f"BU{b}", BUSINESS_UNITS[b]] for b in bus),
    )

    # Source systems
    sources = list(range(cfg.n_source_systems))
    stats["SourceSystem"] = _w(
        out / "nodes_source.csv", ["sourceId", "name", "kind"],
        (
            [f"SRC{s}",
             f"{rng.choice(SOURCE_KINDS)}-{s:03d}",
             rng.choice(SOURCE_KINDS)]
            for s in sources
        ),
    )

    # Datasets, each produced by a source and owned by a BU
    datasets = list(range(cfg.n_datasets))
    dataset_rows = []
    produces_rows = []
    owned_rows = []
    for d in datasets:
        src = rng.randrange(cfg.n_source_systems)
        bu = rng.randrange(len(BUSINESS_UNITS))
        dataset_rows.append([f"DS{d}", f"dataset_{d:04d}"])
        produces_rows.append([f"SRC{src}", f"DS{d}"])
        owned_rows.append([f"DS{d}", f"BU{bu}"])
    stats["Dataset"] = _w(out / "nodes_dataset.csv", ["datasetId", "name"], dataset_rows)

    # Fields, each belonging to a dataset
    fields = list(range(cfg.n_fields))
    field_rows = []
    has_field_rows = []
    for f in fields:
        ds = rng.randrange(cfg.n_datasets)
        field_rows.append([f"F{f}", f"field_{f:05d}"])
        has_field_rows.append([f"DS{ds}", f"F{f}"])
    stats["Field"] = _w(out / "nodes_field.csv", ["fieldId", "name"], field_rows)

    # Transformations
    transforms = list(range(cfg.n_transformations))
    stats["Transformation"] = _w(
        out / "nodes_transform.csv", ["transformId", "name"],
        ([f"T{t}", f"transform_{t:04d}"] for t in transforms),
    )

    # Reports
    reports = list(range(cfg.n_reports))
    stats["Report"] = _w(
        out / "nodes_report.csv", ["reportId", "name"],
        (
            [f"R{r}", f"{REPORT_NAMES[r % len(REPORT_NAMES)]} #{r}"]
            for r in reports
        ),
    )

    # Controls
    controls = list(range(cfg.n_controls))
    stats["Control"] = _w(
        out / "nodes_control.csv", ["controlId", "name"],
        ([f"C{c}", f"DQ_control_{c:04d}"] for c in controls),
    )

    # ---- Relationships ---------------------------------------------------
    stats["PRODUCES"] = _w(out / "rel_produces.csv", ["sourceId", "datasetId"], produces_rows)
    stats["HAS_FIELD"] = _w(out / "rel_has_field.csv", ["datasetId", "fieldId"], has_field_rows)
    stats["OWNED_BY"] = _w(out / "rel_owned_by.csv", ["datasetId", "buId"], owned_rows)

    # DERIVES_FROM: build a DAG. Order fields by id; a field may derive from a
    # small number of *lower-id* fields (guarantees acyclicity). Fields in the
    # bottom ~15% are "sources" (no upstream) so lineage terminates cleanly.
    source_cutoff = int(cfg.n_fields * 0.15)
    derives_rows = []
    for f in range(source_cutoff, cfg.n_fields):
        # 1-4 upstream parents, chosen from strictly lower ids to keep a DAG.
        n_parents = rng.choice([1, 1, 2, 2, 3, 4])
        # Bias parents to be "nearby" so chains have realistic depth.
        for _ in range(n_parents):
            span = min(f, 400)
            parent = f - 1 - rng.randrange(span) if span > 0 else 0
            if 0 <= parent < f:
                derives_rows.append([f"F{f}", f"F{parent}"])
    # dedupe
    derives_rows = list({(a, b) for a, b in derives_rows})
    stats["DERIVES_FROM"] = _w(out / "rel_derives.csv", ["childFieldId", "parentFieldId"], derives_rows)

    # Transformations consume some fields and output others (aligned with a
    # derives edge so the transform "explains" a derivation).
    consumes_rows = []
    outputs_rows = []
    for t in transforms:
        out_field = rng.randrange(source_cutoff, cfg.n_fields)
        n_in = rng.choice([1, 2, 2, 3, 5])
        for _ in range(n_in):
            in_field = rng.randrange(0, out_field) if out_field > 0 else 0
            consumes_rows.append([f"T{t}", f"F{in_field}"])
        outputs_rows.append([f"T{t}", f"F{out_field}"])
    consumes_rows = list({(a, b) for a, b in consumes_rows})
    stats["CONSUMES"] = _w(out / "rel_consumes.csv", ["transformId", "fieldId"], consumes_rows)
    stats["OUTPUTS"] = _w(out / "rel_outputs.csv", ["transformId", "fieldId"], outputs_rows)

    # FEEDS: top ~10% of fields (highest ids = most-derived) feed reports.
    feeds_rows = []
    feed_cutoff = int(cfg.n_fields * 0.90)
    for f in range(feed_cutoff, cfg.n_fields):
        n_reports = rng.choice([1, 1, 2, 3])
        for _ in range(n_reports):
            r = rng.randrange(cfg.n_reports)
            feeds_rows.append([f"F{f}", f"R{r}"])
    feeds_rows = list({(a, b) for a, b in feeds_rows})
    stats["FEEDS"] = _w(out / "rel_feeds.csv", ["fieldId", "reportId"], feeds_rows)

    # VALIDATES: q08 asks "which report fields have NO control anywhere on
    # their upstream lineage?" To make that return a realistic *minority*, we
    # only ever attach controls to fields drawn from a fixed "controllable"
    # subset (~55% of fields, chosen by hash). The complement is never
    # controlled, so any lineage path staying within the uncontrolled set is a
    # genuine governance gap — while most paths do hit a control.
    validates_rows = []
    def controllable(field_idx: int) -> bool:
        return (field_idx * 2654435761) % 100 < 55  # deterministic ~55%
    for c in controls:
        n_val = rng.choice([1, 2, 3, 5, 8])
        for _ in range(n_val):
            f = rng.randrange(0, cfg.n_fields)
            if controllable(f):
                validates_rows.append([f"C{c}", f"F{f}"])
    validates_rows = list({(a, b) for a, b in validates_rows})
    stats["VALIDATES"] = _w(out / "rel_validates.csv", ["controlId", "fieldId"], validates_rows)

    # ---- pick representative ids for parameterised queries ----------------
    # For a good impact-analysis demo (q06) we want a field with BOTH upstream
    # ancestors and real downstream reach. A field around the 55-65% mark of
    # the id range sits mid-DAG: it derives from lower fields and is itself an
    # ancestor of many higher (report-feeding) fields.
    mid_field = int(cfg.n_fields * 0.60)
    # second param: a source-region field almost certainly upstream of mid.
    src_field = int(cfg.n_fields * 0.05)
    sample = {
        "fieldId": f"F{mid_field}",
        "fieldId2": f"F{src_field}",
        "sourceName": None,  # filled below from actual source names
        "reportName": None,
    }
    # read back one real source name and report name
    with open(out / "nodes_source.csv", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
        sample["sourceName"] = rows[1][1] if len(rows) > 1 else "Booking-000"
    with open(out / "nodes_report.csv", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
        sample["reportName"] = rows[1][1] if len(rows) > 1 else REPORT_NAMES[0]

    # persist the chosen sample params
    with open(out / "sample_params.csv", "w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh)
        wr.writerow(["param", "value"])
        for k, v in sample.items():
            wr.writerow([k, v])

    total_nodes = sum(stats[k] for k in
                      ["BusinessUnit", "SourceSystem", "Dataset", "Field",
                       "Transformation", "Report", "Control"])
    total_rels = sum(stats[k] for k in
                     ["PRODUCES", "HAS_FIELD", "OWNED_BY", "DERIVES_FROM",
                      "CONSUMES", "OUTPUTS", "FEEDS", "VALIDATES"])
    stats["_total_nodes"] = total_nodes
    stats["_total_rels"] = total_rels
    return stats


if __name__ == "__main__":
    s = generate()
    print("Generated capital-markets lineage graph:")
    for k, v in s.items():
        if not k.startswith("_"):
            print(f"  {k:16s} {v:>10,}")
    print(f"  {'TOTAL nodes':16s} {s['_total_nodes']:>10,}")
    print(f"  {'TOTAL edges':16s} {s['_total_rels']:>10,}")
