"""
The benchmark query catalog.

This is the intellectual core of the "ability to write complex queries"
criterion. Each entry models a *real data-lineage question* an investment
bank running capital-markets processes would ask of a lineage knowledge graph,
for example:

  - "If this source feed is wrong, which regulatory reports are affected?"
  - "Show the full upstream lineage of this figure on the FRTB report."
  - "Which fields feed a report but have no data-quality control on the path?"

Every query is written twice:

  * `cypher`      - idiomatic Neo4j Cypher (may use APOC / shortestPath).
  * `opencypher`  - a Neptune-compatible rewrite (or None if there is simply
                    no equivalent, which is itself a finding we report).

Neptune's openCypher engine, per AWS documentation, does NOT support:
  - shortestPath() / allShortestPaths()
  - APOC procedures (apoc.*)
  - MANDATORY MATCH
  - non-static SKIP / LIMIT
So where a Neo4j query uses those, the openCypher variant uses a supported
construction (bounded variable-length paths, WITH aggregation, etc.), and the
`neptune_native` flag records whether Neptune can express the query at all.

The GRAPH MODEL these queries run against (built by data_generator.py):

  (:SourceSystem)-[:PRODUCES]->(:Dataset)-[:HAS_FIELD]->(:Field)
  (:Field)-[:DERIVES_FROM]->(:Field)            # field-level lineage edges
  (:Transformation)-[:CONSUMES]->(:Field)
  (:Transformation)-[:OUTPUTS]->(:Field)
  (:Field)-[:FEEDS]->(:Report)
  (:Control)-[:VALIDATES]->(:Field)
  (:Dataset)-[:OWNED_BY]->(:BusinessUnit)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class BenchQuery:
    key: str
    title: str
    category: str          # simple | filter | aggregation | multi_hop | lineage | path | recursion
    description: str
    cypher: str            # Neo4j variant
    opencypher: Optional[str]   # Neptune-safe variant (None = cannot be expressed)
    neptune_native: bool   # True if Neptune can run *some* equivalent
    note: str = ""         # human note about any semantic difference


# A representative :Report id and :Field id are injected at run time so the
# parameterised queries hit real data. We use $reportId / $fieldId params.

QUERIES: list[BenchQuery] = [

    BenchQuery(
        key="q01_count_nodes",
        title="Count all nodes by label",
        category="simple",
        description="Baseline scan. Sanity check + raw read throughput.",
        cypher="MATCH (n) RETURN labels(n)[0] AS label, count(*) AS c ORDER BY c DESC",
        opencypher="MATCH (n) RETURN labels(n)[0] AS label, count(*) AS c ORDER BY c DESC",
        neptune_native=True,
    ),

    BenchQuery(
        key="q02_fields_of_dataset",
        title="Fields produced by a source system",
        category="filter",
        description="One-hop filter: given a source system, list its dataset fields.",
        cypher="""
            MATCH (s:SourceSystem {name: $sourceName})-[:PRODUCES]->(d:Dataset)-[:HAS_FIELD]->(f:Field)
            RETURN d.name AS dataset, count(f) AS fields
            ORDER BY fields DESC
        """,
        opencypher="""
            MATCH (s:SourceSystem {name: $sourceName})-[:PRODUCES]->(d:Dataset)-[:HAS_FIELD]->(f:Field)
            RETURN d.name AS dataset, count(f) AS fields
            ORDER BY fields DESC
        """,
        neptune_native=True,
    ),

    BenchQuery(
        key="q03_direct_upstream",
        title="Direct upstream fields of a field",
        category="multi_hop",
        description="Immediate DERIVES_FROM parents of a field (1 hop).",
        cypher="""
            MATCH (f:Field {fieldId: $fieldId})-[:DERIVES_FROM]->(up:Field)
            RETURN up.fieldId AS upstream, up.name AS name
        """,
        opencypher="""
            MATCH (f:Field {fieldId: $fieldId})-[:DERIVES_FROM]->(up:Field)
            RETURN up.fieldId AS upstream, up.name AS name
        """,
        neptune_native=True,
    ),

    BenchQuery(
        key="q04_upstream_depth5",
        title="Upstream lineage to depth 5",
        category="lineage",
        description="Bounded variable-length traversal: all ancestors within 5 hops.",
        cypher="""
            MATCH path = (f:Field {fieldId: $fieldId})-[:DERIVES_FROM*1..5]->(up:Field)
            RETURN DISTINCT up.fieldId AS upstream
        """,
        opencypher="""
            MATCH (f:Field {fieldId: $fieldId})-[:DERIVES_FROM*1..5]->(up:Field)
            RETURN DISTINCT up.fieldId AS upstream
        """,
        neptune_native=True,
        note="Bounded *1..5 is supported by Neptune openCypher.",
    ),

    BenchQuery(
        key="q05_full_upstream_unbounded",
        title="Full upstream lineage (unbounded)",
        category="recursion",
        description="Every ancestor of a field to the source, arbitrary depth.",
        cypher="""
            MATCH path = (f:Field {fieldId: $fieldId})-[:DERIVES_FROM*]->(root:Field)
            WHERE NOT (root)-[:DERIVES_FROM]->()
            RETURN DISTINCT root.fieldId AS source_field
        """,
        # Neptune supports unbounded *, but pattern-in-WHERE (anti-join) is
        # limited; we express "roots" by out-degree via a WITH+OPTIONAL MATCH.
        opencypher="""
            MATCH (f:Field {fieldId: $fieldId})-[:DERIVES_FROM*1..20]->(root:Field)
            OPTIONAL MATCH (root)-[r:DERIVES_FROM]->()
            WITH root, count(r) AS outdeg
            WHERE outdeg = 0
            RETURN DISTINCT root.fieldId AS source_field
        """,
        neptune_native=True,
        note="Neo4j uses truly unbounded *; Neptune variant caps at 20 hops "
             "(practical safeguard) and derives 'root' via out-degree instead "
             "of a NOT-EXISTS subpattern.",
    ),

    BenchQuery(
        key="q06_impact_downstream_reports",
        title="Impact analysis: reports affected by a field",
        category="lineage",
        description="Downstream blast radius: which reports consume this field "
                    "directly or transitively? Core regulatory-impact question.",
        cypher="""
            MATCH (f:Field {fieldId: $fieldId})<-[:DERIVES_FROM*0..8]-(dep:Field)-[:FEEDS]->(r:Report)
            RETURN DISTINCT r.name AS report
            ORDER BY report
        """,
        opencypher="""
            MATCH (f:Field {fieldId: $fieldId})<-[:DERIVES_FROM*0..8]-(dep:Field)-[:FEEDS]->(r:Report)
            RETURN DISTINCT r.name AS report
            ORDER BY report
        """,
        neptune_native=True,
        note="*0..8 includes the field itself (zero-length) — supported by both.",
    ),

    BenchQuery(
        key="q07_shortest_path",
        title="Shortest lineage path between two fields",
        category="path",
        description="Explain HOW an output derives from an input: shortest chain.",
        cypher="""
            MATCH (a:Field {fieldId: $fieldId}), (b:Field {fieldId: $fieldId2})
            MATCH p = shortestPath((a)-[:DERIVES_FROM*..15]-(b))
            RETURN [n IN nodes(p) | n.fieldId] AS chain, length(p) AS hops
        """,
        # Neptune has NO shortestPath(). Best effort: enumerate bounded paths
        # and take the minimum length. This is the key capability gap.
        opencypher="""
            MATCH p = (a:Field {fieldId: $fieldId})-[:DERIVES_FROM*1..8]-(b:Field {fieldId: $fieldId2})
            RETURN [n IN nodes(p) | n.fieldId] AS chain, length(p) AS hops
            ORDER BY hops ASC
            LIMIT 1
        """,
        neptune_native=False,
        note="Neptune does not support shortestPath(). The openCypher variant "
             "enumerates all paths up to 8 hops and sorts by length — correct "
             "but far more expensive, and it can miss paths longer than the cap.",
    ),

    BenchQuery(
        key="q08_uncontrolled_paths",
        title="Report fields with no control within 2 hops upstream",
        category="lineage",
        description="Governance gap: fields feeding a report whose immediate "
                    "lineage (the field itself or its parents within 2 hops) "
                    "has no :Control validating it. A real DQ-coverage question.",
        cypher="""
            MATCH (f:Field)-[:FEEDS]->(r:Report {name: $reportName})
            WHERE NOT EXISTS {
                MATCH (f)-[:DERIVES_FROM*0..2]->(up:Field)<-[:VALIDATES]-(:Control)
            }
            RETURN f.fieldId AS uncontrolled_field
        """,
        # Neptune lacks EXISTS{} subqueries in older engines; rewrite with
        # OPTIONAL MATCH + null test on the aggregated control count.
        opencypher="""
            MATCH (f:Field)-[:FEEDS]->(r:Report {name: $reportName})
            OPTIONAL MATCH (f)-[:DERIVES_FROM*0..2]->(up:Field)<-[:VALIDATES]-(c:Control)
            WITH f, count(c) AS controls
            WHERE controls = 0
            RETURN f.fieldId AS uncontrolled_field
        """,
        neptune_native=True,
        note="Neo4j uses an EXISTS{} anti-join; Neptune uses OPTIONAL MATCH + "
             "count()=0 which is semantically equivalent here.",
    ),

    BenchQuery(
        key="q09_cross_bu_lineage",
        title="Cross business-unit data flows",
        category="aggregation",
        description="Count lineage edges that cross a business-unit boundary — "
                    "a data-ownership / governance metric.",
        cypher="""
            MATCH (f1:Field)<-[:HAS_FIELD]-(:Dataset)-[:OWNED_BY]->(bu1:BusinessUnit)
            MATCH (f1)-[:DERIVES_FROM]->(f2:Field)<-[:HAS_FIELD]-(:Dataset)-[:OWNED_BY]->(bu2:BusinessUnit)
            WHERE bu1 <> bu2
            RETURN bu1.name AS from_bu, bu2.name AS to_bu, count(*) AS crossings
            ORDER BY crossings DESC
            LIMIT 25
        """,
        opencypher="""
            MATCH (f1:Field)<-[:HAS_FIELD]-(:Dataset)-[:OWNED_BY]->(bu1:BusinessUnit)
            MATCH (f1)-[:DERIVES_FROM]->(f2:Field)<-[:HAS_FIELD]-(:Dataset)-[:OWNED_BY]->(bu2:BusinessUnit)
            WHERE bu1 <> bu2
            RETURN bu1.name AS from_bu, bu2.name AS to_bu, count(*) AS crossings
            ORDER BY crossings DESC
            LIMIT 25
        """,
        neptune_native=True,
    ),

    BenchQuery(
        key="q10_transform_fanout",
        title="Highest fan-out transformations",
        category="aggregation",
        description="Transformations that read many fields and write many — "
                    "hotspots for lineage complexity.",
        cypher="""
            MATCH (t:Transformation)-[:CONSUMES]->(inf:Field)
            WITH t, count(DISTINCT inf) AS inputs
            MATCH (t)-[:OUTPUTS]->(outf:Field)
            RETURN t.name AS transform, inputs, count(DISTINCT outf) AS outputs
            ORDER BY inputs + outputs DESC
            LIMIT 20
        """,
        opencypher="""
            MATCH (t:Transformation)-[:CONSUMES]->(inf:Field)
            WITH t, count(DISTINCT inf) AS inputs
            MATCH (t)-[:OUTPUTS]->(outf:Field)
            RETURN t.name AS transform, inputs, count(DISTINCT outf) AS outputs
            ORDER BY inputs + outputs DESC
            LIMIT 20
        """,
        neptune_native=True,
    ),

    BenchQuery(
        key="q11_all_paths_enumerate",
        title="Enumerate all lineage paths to depth 6",
        category="path",
        description="Stress test: enumerate (not just reach) every upstream "
                    "path up to 6 hops. Exercises path materialisation.",
        cypher="""
            MATCH p = (f:Field {fieldId: $fieldId})-[:DERIVES_FROM*1..6]->(up:Field)
            RETURN count(p) AS path_count
        """,
        opencypher="""
            MATCH p = (f:Field {fieldId: $fieldId})-[:DERIVES_FROM*1..6]->(up:Field)
            RETURN count(p) AS path_count
        """,
        neptune_native=True,
    ),

    BenchQuery(
        key="q12_apoc_subgraph",
        title="Neighbourhood subgraph extraction",
        category="recursion",
        description="Extract the k-hop neighbourhood around a field. In Neo4j "
                    "this is a one-liner with APOC; Neptune must do it in plain "
                    "openCypher.",
        cypher="""
            MATCH (f:Field {fieldId: $fieldId})
            CALL apoc.path.subgraphNodes(f, {maxLevel: 3, relationshipFilter: 'DERIVES_FROM'})
            YIELD node
            RETURN count(node) AS neighbourhood_size
        """,
        opencypher="""
            MATCH (f:Field {fieldId: $fieldId})-[:DERIVES_FROM*0..3]-(n:Field)
            RETURN count(DISTINCT n) AS neighbourhood_size
        """,
        neptune_native=True,
        note="Neo4j convenience via APOC (apoc.path.subgraphNodes). Neptune has "
             "no APOC, but the plain bounded traversal is equivalent here.",
    ),
]


def by_key(key: str) -> BenchQuery:
    for q in QUERIES:
        if q.key == key:
            return q
    raise KeyError(key)
