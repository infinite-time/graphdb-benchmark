"""
Unified client layer.

Both Neo4j and Amazon Neptune speak the Bolt protocol, so the *same* `neo4j`
Python driver can talk to either one — that keeps the benchmark fair (identical
client, identical serialisation path) and the code small.

  * Neo4jClient   -> bolt://host:7687        (local Docker container)
  * NeptuneClient -> bolt+s://endpoint:8182  (encrypted; empty auth)

A second Neptune transport (`NeptuneHttpClient`) uses boto3's `neptunedata`
API over HTTPS. Use it when Bolt is blocked or when IAM auth is required; it is
slightly slower per call because of request signing but needs no open Bolt port.

All clients expose the same tiny interface:

    client.run(cypher, params) -> list[dict]     # materialised rows
    client.name                                  # "Neo4j" / "Neptune"
    client.close()
"""
from __future__ import annotations

from typing import Any, Optional

from .config import CONFIG, Neo4jConfig, NeptuneConfig


class Neo4jClient:
    def __init__(self, cfg: Neo4jConfig | None = None):
        from neo4j import GraphDatabase  # imported lazily so importing this
        self.cfg = cfg or CONFIG.neo4j    # module never *requires* the driver
        self.name = "Neo4j"
        self._driver = GraphDatabase.driver(
            self.cfg.uri, auth=(self.cfg.user, self.cfg.password)
        )

    def run(self, cypher: str, params: Optional[dict] = None) -> list[dict]:
        with self._driver.session(database=self.cfg.database) as s:
            res = s.run(cypher, params or {})
            return [r.data() for r in res]

    def verify(self) -> None:
        self._driver.verify_connectivity()

    def close(self) -> None:
        self._driver.close()


class NeptuneBoltClient:
    """Neptune over the Bolt protocol using the neo4j driver."""

    def __init__(self, cfg: NeptuneConfig | None = None):
        from neo4j import GraphDatabase
        self.cfg = cfg or CONFIG.neptune
        self.name = "Neptune"
        # Neptune requires encryption. Auth is empty ("","") unless IAM is on
        # (IAM signing over Bolt needs a custom auth token; see docs). For a
        # test cluster with IAM auth disabled, empty auth is correct.
        self._driver = GraphDatabase.driver(
            self.cfg.bolt_uri,
            auth=("", ""),
            encrypted=True,
        )

    def run(self, cypher: str, params: Optional[dict] = None) -> list[dict]:
        with self._driver.session() as s:
            res = s.run(cypher, params or {})
            return [r.data() for r in res]

    def verify(self) -> None:
        self._driver.verify_connectivity()

    def close(self) -> None:
        self._driver.close()


class NeptuneHttpClient:
    """Neptune over HTTPS via boto3 neptunedata (no open Bolt port needed)."""

    def __init__(self, cfg: NeptuneConfig | None = None):
        import boto3
        from botocore.config import Config as BotoConfig
        self.cfg = cfg or CONFIG.neptune
        self.name = "Neptune"
        self._client = boto3.client(
            "neptunedata",
            endpoint_url=self.cfg.https_url,
            region_name=self.cfg.region,
            config=BotoConfig(read_timeout=None, retries={"total_max_attempts": 1}),
        )

    def run(self, cypher: str, params: Optional[dict] = None) -> list[dict]:
        import json
        kwargs: dict[str, Any] = {"openCypherQuery": cypher}
        if params:
            kwargs["parameters"] = json.dumps(params)
        resp = self._client.execute_open_cypher_query(**kwargs)
        return resp.get("results", [])

    def verify(self) -> None:
        # a trivial query proves connectivity
        self.run("MATCH (n) RETURN n LIMIT 1")

    def close(self) -> None:
        pass


def make_neptune_client(cfg: NeptuneConfig | None = None):
    cfg = cfg or CONFIG.neptune
    if cfg.access_mode == "https":
        return NeptuneHttpClient(cfg)
    return NeptuneBoltClient(cfg)
