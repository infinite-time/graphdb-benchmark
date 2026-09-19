"""
Load the generated CSV graph into a target database (Neo4j or Neptune) using
batched, parameterised openCypher that runs identically on both engines.

We deliberately do NOT use Neo4j's `LOAD CSV` or Neptune's bulk loader here,
because those are engine-specific and would make load times incomparable.
Instead we stream rows from CSV and send them in UNWIND batches over Bolt —
the same code path for both databases, so "load throughput" is a fair number.

(For a real multi-million-node production load you would use Neptune's bulk
loader from S3 and Neo4j's admin import; that is called out in the docs.)
"""
from __future__ import annotations

import csv
import time
from pathlib import Path

from .config import CONFIG

# Index / constraint statements. Both engines accept these forms.
CONSTRAINTS = [
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Field)          REQUIRE n.fieldId IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Dataset)        REQUIRE n.datasetId IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:SourceSystem)   REQUIRE n.sourceId IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Transformation) REQUIRE n.transformId IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Report)         REQUIRE n.reportId IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Control)        REQUIRE n.controlId IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:BusinessUnit)   REQUIRE n.buId IS UNIQUE",
]

# Node file -> (label, id-property, all columns)
NODE_FILES = [
    ("nodes_bu.csv",        "BusinessUnit",   ["buId", "name"]),
    ("nodes_source.csv",    "SourceSystem",   ["sourceId", "name", "kind"]),
    ("nodes_dataset.csv",   "Dataset",        ["datasetId", "name"]),
    ("nodes_field.csv",     "Field",          ["fieldId", "name"]),
    ("nodes_transform.csv", "Transformation", ["transformId", "name"]),
    ("nodes_report.csv",    "Report",         ["reportId", "name"]),
    ("nodes_control.csv",   "Control",        ["controlId", "name"]),
]

# Rel file -> (fromLabel, fromKey, fromCol, REL, toLabel, toKey, toCol)
REL_FILES = [
    ("rel_produces.csv",  "SourceSystem",   "sourceId",    "sourceId",   "PRODUCES",     "Dataset",        "datasetId",   "datasetId"),
    ("rel_has_field.csv", "Dataset",        "datasetId",   "datasetId",  "HAS_FIELD",    "Field",          "fieldId",     "fieldId"),
    ("rel_owned_by.csv",  "Dataset",        "datasetId",   "datasetId",  "OWNED_BY",     "BusinessUnit",   "buId",        "buId"),
    ("rel_derives.csv",   "Field",          "fieldId",     "childFieldId","DERIVES_FROM","Field",          "fieldId",     "parentFieldId"),
    ("rel_consumes.csv",  "Transformation", "transformId", "transformId","CONSUMES",     "Field",          "fieldId",     "fieldId"),
    ("rel_outputs.csv",   "Transformation", "transformId", "transformId","OUTPUTS",      "Field",          "fieldId",     "fieldId"),
    ("rel_feeds.csv",     "Field",          "fieldId",     "fieldId",    "FEEDS",        "Report",         "reportId",    "reportId"),
    ("rel_validates.csv", "Control",        "controlId",   "controlId",  "VALIDATES",    "Field",          "fieldId",     "fieldId"),
]


def _read_csv(path: Path):
    with open(path, encoding="utf-8") as fh:
        rdr = csv.DictReader(fh)
        for row in rdr:
            yield row


def _batches(iterable, size):
    batch = []
    for item in iterable:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def wipe(client) -> None:
    """Delete everything. Batched so it works on both engines without OOM."""
    # Drop relationships then nodes in chunks.
    while True:
        res = client.run(
            "MATCH ()-[r]->() WITH r LIMIT 50000 DELETE r RETURN count(r) AS c"
        )
        if not res or res[0]["c"] == 0:
            break
    while True:
        res = client.run(
            "MATCH (n) WITH n LIMIT 50000 DELETE n RETURN count(n) AS c"
        )
        if not res or res[0]["c"] == 0:
            break


def create_constraints(client) -> None:
    for stmt in CONSTRAINTS:
        try:
            client.run(stmt)
        except Exception as e:  # Neptune manages some indexes automatically
            print(f"    (constraint skipped: {str(e)[:70]})")


def load_nodes(client, data_dir: Path, batch_size: int) -> dict:
    counts = {}
    for fname, label, cols in NODE_FILES:
        path = data_dir / fname
        if not path.exists():
            continue
        idprop = cols[0]
        # Build a SET clause for the non-id columns.
        set_props = ", ".join(f"n.{c} = row.{c}" for c in cols[1:])
        set_clause = f"SET {set_props}" if set_props else ""
        cypher = (
            f"UNWIND $rows AS row "
            f"MERGE (n:{label} {{{idprop}: row.{idprop}}}) "
            f"{set_clause}"
        )
        n = 0
        for batch in _batches(_read_csv(path), batch_size):
            client.run(cypher, {"rows": batch})
            n += len(batch)
        counts[label] = n
        print(f"    {label:16s} {n:>8,} nodes")
    return counts


def load_rels(client, data_dir: Path, batch_size: int) -> dict:
    counts = {}
    for (fname, from_label, from_key, from_col,
         rel, to_label, to_key, to_col) in REL_FILES:
        path = data_dir / fname
        if not path.exists():
            continue
        cypher = (
            f"UNWIND $rows AS row "
            f"MATCH (a:{from_label} {{{from_key}: row.{from_col}}}) "
            f"MATCH (b:{to_label} {{{to_key}: row.{to_col}}}) "
            f"MERGE (a)-[:{rel}]->(b)"
        )
        n = 0
        for batch in _batches(_read_csv(path), batch_size):
            client.run(cypher, {"rows": batch})
            n += len(batch)
        counts[rel] = n
        print(f"    {rel:16s} {n:>8,} edges")
    return counts


def load_all(client, data_dir: str | None = None, batch_size: int | None = None,
             do_wipe: bool = True) -> dict:
    data_dir = Path(data_dir or CONFIG.data.data_dir)
    batch_size = batch_size or CONFIG.data.load_batch_size

    result = {}
    if do_wipe:
        print("  Wiping existing graph...")
        wipe(client)

    print("  Creating constraints/indexes...")
    create_constraints(client)

    print("  Loading nodes...")
    t0 = time.time()
    result["nodes"] = load_nodes(client, data_dir, batch_size)
    t_nodes = time.time() - t0

    print("  Loading relationships...")
    t0 = time.time()
    result["rels"] = load_rels(client, data_dir, batch_size)
    t_rels = time.time() - t0

    result["load_seconds_nodes"] = round(t_nodes, 2)
    result["load_seconds_rels"] = round(t_rels, 2)
    result["load_seconds_total"] = round(t_nodes + t_rels, 2)
    print(f"  Load complete in {result['load_seconds_total']}s "
          f"(nodes {t_nodes:.1f}s, rels {t_rels:.1f}s)")
    return result
