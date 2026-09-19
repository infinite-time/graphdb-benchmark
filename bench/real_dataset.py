"""
OPTIONAL: build the benchmark graph from a REAL, openly-available large dataset
instead of the synthetic generator.

We use a Stack Exchange site data dump (CC-BY-SA, published on the Internet
Archive). Each dump is a set of XML files; we use Posts.xml and PostLinks.xml.
The natural graph is:

    (:Post)-[:LINKED_TO]->(:Post)         from PostLinks.xml (related/duplicate)
    (:Post)-[:ANSWERS]->(:Post)           answers -> their question (ParentId)
    (:Post)-[:TAGGED]->(:Tag)             from the Tags field
    (:User)-[:AUTHORED]->(:Post)          from OwnerUserId

This is a genuine, messy, real-world graph with the same *query shapes* as data
lineage: multi-hop traversal (answer -> question -> linked question -> ...),
impact/reachability, and shortest path between two posts. So the SAME query
categories apply; only the labels change.

Why keep the synthetic generator as the default?
  * Stack Exchange now gates some dumps behind site login, and file sizes range
    from a few MB (small sites) to 90GB (Stack Overflow). Availability and size
    are not guaranteed, so the synthetic path guarantees the bench always runs.
  * The synthetic graph is a *purpose-built lineage DAG* — a cleaner match for
    the investment-banking scenario. The real dataset proves the bench also
    works on external data, as required.

Recommended small sites (tens of MB, download in seconds, still 100k+ edges):
    - datascience.stackexchange.com
    - ai.stackexchange.com
    - quant.stackexchange.com   <-- topical: quantitative finance
We suggest quant.stackexchange.com for a finance flavour.

Usage:
    python -m bench.real_dataset --url <archive.org 7z url> --out data_real
then point the loader at data_real:
    python run_benchmark.py load --db neo4j            # after setting BENCH_DATA_DIR=data_real
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path
from xml.etree import ElementTree as ET


def _download(url: str, dest: Path) -> Path:
    import urllib.request
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {url}\n  -> {dest} (this can take a while)...")
    urllib.request.urlretrieve(url, dest)
    print(f"  done ({dest.stat().st_size/1e6:.1f} MB)")
    return dest


def _extract_7z(archive: Path, out_dir: Path) -> None:
    """Extract a .7z dump. Tries py7zr, falls back to the `7z` CLI."""
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        import py7zr
        print("Extracting with py7zr...")
        with py7zr.SevenZipFile(archive, mode="r") as z:
            z.extractall(path=out_dir)
        return
    except ImportError:
        pass
    import shutil, subprocess
    if shutil.which("7z"):
        print("Extracting with system 7z...")
        subprocess.run(["7z", "x", str(archive), f"-o{out_dir}", "-y"], check=True)
        return
    raise RuntimeError(
        "Cannot extract .7z. Install py7zr (`pip install py7zr`) or the 7-Zip CLI."
    )


def _iter_rows(xml_path: Path):
    """Stream <row .../> elements from a Stack Exchange XML file."""
    if not xml_path.exists():
        return
    for _, elem in ET.iterparse(xml_path, events=("end",)):
        if elem.tag == "row":
            yield dict(elem.attrib)
            elem.clear()


def build_csvs(dump_dir: Path, out_dir: Path, max_posts: int | None = None) -> dict:
    """Convert a Stack Exchange dump directory into the loader's CSV format,
    but with real labels. Produces node/edge CSVs analogous to the synthetic
    ones so the SAME loader and (adapted) queries work."""
    out_dir.mkdir(parents=True, exist_ok=True)
    posts_xml = dump_dir / "Posts.xml"
    links_xml = dump_dir / "PostLinks.xml"
    if not posts_xml.exists():
        raise FileNotFoundError(f"{posts_xml} not found (unexpected dump layout)")

    post_ids = set()
    n_posts = 0
    with open(out_dir / "nodes_post.csv", "w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh); wr.writerow(["postId", "title", "type"])
        for row in _iter_rows(posts_xml):
            pid = row.get("Id")
            if pid is None:
                continue
            post_ids.add(pid)
            wr.writerow([pid, (row.get("Title", "") or "")[:120],
                         row.get("PostTypeId", "")])
            n_posts += 1
            if max_posts and n_posts >= max_posts:
                break

    # ANSWERS edges (answer -> question via ParentId)
    n_ans = 0
    with open(out_dir / "rel_answers.csv", "w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh); wr.writerow(["answerId", "questionId"])
        for row in _iter_rows(posts_xml):
            pid = row.get("Id"); parent = row.get("ParentId")
            if parent and pid in post_ids and parent in post_ids:
                wr.writerow([pid, parent]); n_ans += 1
            if max_posts and int(pid or 0) > max_posts:
                pass

    # LINKED_TO edges from PostLinks.xml
    n_links = 0
    with open(out_dir / "rel_linked.csv", "w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh); wr.writerow(["postId", "relatedPostId"])
        for row in _iter_rows(links_xml):
            a = row.get("PostId"); b = row.get("RelatedPostId")
            if a in post_ids and b in post_ids:
                wr.writerow([a, b]); n_links += 1

    stats = {"Post": n_posts, "ANSWERS": n_ans, "LINKED_TO": n_links}
    # sample params: two well-connected posts
    ids = sorted(post_ids, key=lambda x: int(x))
    if ids:
        with open(out_dir / "sample_params.csv", "w", newline="", encoding="utf-8") as fh:
            wr = csv.writer(fh); wr.writerow(["param", "value"])
            wr.writerow(["postId", ids[len(ids)//2]])
            wr.writerow(["postId2", ids[len(ids)//10]])
    print("Real dataset CSVs written:")
    for k, v in stats.items():
        print(f"  {k:12s} {v:>10,}")
    return stats


# --- loader + queries for the REAL graph (kept separate from the synthetic
#     loader so each stays simple). Same Bolt client, same runner. ---

REAL_NODE_FILES = [("nodes_post.csv", "Post", ["postId", "title", "type"])]
REAL_REL_FILES = [
    ("rel_answers.csv", "Post", "postId", "answerId", "ANSWERS", "Post", "postId", "questionId"),
    ("rel_linked.csv",  "Post", "postId", "postId",   "LINKED_TO", "Post", "postId", "relatedPostId"),
]


def load_real(client, data_dir="data_real", batch_size=1000, do_wipe=True):
    """Load the real Post graph using the same batched-UNWIND approach."""
    from . import loader as _L
    from pathlib import Path
    data_dir = Path(data_dir)
    if do_wipe:
        print("  Wiping..."); _L.wipe(client)
    print("  Constraint on Post.postId...")
    try:
        client.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Post) REQUIRE n.postId IS UNIQUE")
    except Exception as e:
        print(f"    (skipped: {str(e)[:60]})")
    # nodes
    import time
    t0 = time.time()
    for fname, label, cols in REAL_NODE_FILES:
        path = data_dir / fname
        set_clause = "SET " + ", ".join(f"n.{c}=row.{c}" for c in cols[1:])
        cy = f"UNWIND $rows AS row MERGE (n:{label} {{{cols[0]}: row.{cols[0]}}}) {set_clause}"
        n = 0
        for b in _L._batches(_L._read_csv(path), batch_size):
            client.run(cy, {"rows": b}); n += len(b)
        print(f"    {label:10s} {n:>10,} nodes")
    tn = time.time() - t0
    # rels
    t0 = time.time()
    for (fname, fl, fk, fc, rel, tl, tk, tc) in REAL_REL_FILES:
        path = data_dir / fname
        if not path.exists():
            continue
        cy = (f"UNWIND $rows AS row MATCH (a:{fl} {{{fk}: row.{fc}}}) "
              f"MATCH (b:{tl} {{{tk}: row.{tc}}}) MERGE (a)-[:{rel}]->(b)")
        n = 0
        for b in _L._batches(_L._read_csv(path), batch_size):
            client.run(cy, {"rows": b}); n += len(b)
        print(f"    {rel:10s} {n:>10,} edges")
    tr = time.time() - t0
    return {"load_seconds_nodes": round(tn, 2), "load_seconds_rels": round(tr, 2),
            "load_seconds_total": round(tn + tr, 2)}


# A compact query set for the real Post graph, mirroring the synthetic
# categories (filter / multi-hop / lineage / path / aggregation). Neo4j and
# Neptune variants, same compatibility rules (no shortestPath on Neptune).
from .queries import BenchQuery  # noqa: E402

REAL_QUERIES = [
    BenchQuery("rq01_count", "Count posts", "simple",
               "Baseline scan.",
               "MATCH (p:Post) RETURN count(p) AS c",
               "MATCH (p:Post) RETURN count(p) AS c", True),
    BenchQuery("rq02_answers", "Answers of a question", "filter",
               "Direct ANSWERS edges into a question.",
               "MATCH (a:Post)-[:ANSWERS]->(q:Post {postId:$postId}) RETURN a.postId",
               "MATCH (a:Post)-[:ANSWERS]->(q:Post {postId:$postId}) RETURN a.postId", True),
    BenchQuery("rq03_linked2", "Linked posts within 2 hops", "multi_hop",
               "Bounded traversal over LINKED_TO.",
               "MATCH (p:Post {postId:$postId})-[:LINKED_TO*1..2]-(o:Post) RETURN DISTINCT o.postId",
               "MATCH (p:Post {postId:$postId})-[:LINKED_TO*1..2]-(o:Post) RETURN DISTINCT o.postId", True),
    BenchQuery("rq04_reach5", "Reachable posts within 5 hops", "lineage",
               "Blast radius over LINKED_TO to depth 5.",
               "MATCH (p:Post {postId:$postId})-[:LINKED_TO*1..5]-(o:Post) RETURN count(DISTINCT o) AS reach",
               "MATCH (p:Post {postId:$postId})-[:LINKED_TO*1..5]-(o:Post) RETURN count(DISTINCT o) AS reach", True),
    BenchQuery("rq05_shortest", "Shortest link path between two posts", "path",
               "shortestPath on Neo4j; bounded-enumerate rewrite on Neptune.",
               "MATCH (a:Post {postId:$postId}),(b:Post {postId:$postId2}) "
               "MATCH p=shortestPath((a)-[:LINKED_TO*..8]-(b)) RETURN length(p) AS hops",
               "MATCH p=(a:Post {postId:$postId})-[:LINKED_TO*1..6]-(b:Post {postId:$postId2}) "
               "RETURN length(p) AS hops ORDER BY hops ASC LIMIT 1",
               False,
               "Neptune has no shortestPath(); rewrite enumerates paths<=6 and takes min."),
    BenchQuery("rq06_hubs", "Most-linked posts (hubs)", "aggregation",
               "Degree ranking over LINKED_TO.",
               "MATCH (p:Post)-[:LINKED_TO]-(o:Post) RETURN p.postId, count(o) AS deg ORDER BY deg DESC LIMIT 20",
               "MATCH (p:Post)-[:LINKED_TO]-(o:Post) RETURN p.postId, count(o) AS deg ORDER BY deg DESC LIMIT 20", True),
]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build benchmark graph from a real "
                                             "Stack Exchange dump")
    ap.add_argument("--url", help="archive.org .7z URL for a Stack Exchange site")
    ap.add_argument("--archive", help="path to an already-downloaded .7z")
    ap.add_argument("--dump-dir", help="path to an already-extracted dump dir")
    ap.add_argument("--out", default="data_real", help="output CSV dir")
    ap.add_argument("--max-posts", type=int, default=None,
                    help="cap posts for a quick test")
    args = ap.parse_args(argv)

    work = Path("dump_download")
    if args.dump_dir:
        dump_dir = Path(args.dump_dir)
    else:
        if args.archive:
            archive = Path(args.archive)
        elif args.url:
            archive = _download(args.url, work / "site.7z")
        else:
            ap.error("provide --url, --archive, or --dump-dir")
        dump_dir = work / "extracted"
        _extract_7z(archive, dump_dir)

    build_csvs(dump_dir, Path(args.out), args.max_posts)
    print(f"\nDone. Now run the benchmark against it, e.g. (PowerShell):")
    print(f'  $env:BENCH_DATA_DIR="{args.out}"')
    print(f"  python run_benchmark.py load --db neo4j")


if __name__ == "__main__":
    main()
