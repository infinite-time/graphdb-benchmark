"""
Central configuration for the Neo4j vs Neptune benchmark.

Everything that you might want to change lives here so you don't have to edit
the individual test scripts. Values can also be overridden with environment
variables (handy on Windows: `set NEO4J_PASSWORD=...`).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class Neo4jConfig:
    # Bolt URI. For a local Docker container this is the default below.
    uri: str = _env("NEO4J_URI", "bolt://localhost:7687")
    user: str = _env("NEO4J_USER", "neo4j")
    password: str = _env("NEO4J_PASSWORD", "")
    database: str = _env("NEO4J_DATABASE", "neo4j")


@dataclass
class NeptuneConfig:
    # Neptune speaks the Bolt protocol on port 8182 with encryption on.
    # The endpoint is the *cluster* (writer) endpoint from the AWS console.
    # Leave endpoint empty to skip Neptune entirely (Neo4j-only run).
    endpoint: str = _env("NEPTUNE_ENDPOINT", "")  # e.g. mydb.cluster-xxxx.us-east-1.neptune.amazonaws.com
    port: int = int(_env("NEPTUNE_PORT", "8182"))
    region: str = _env("NEPTUNE_REGION", "us-east-1")
    # "bolt"  -> use the neo4j bolt driver over bolt+s://endpoint:8182
    # "https" -> use boto3 neptunedata execute_open_cypher_query
    access_mode: str = _env("NEPTUNE_ACCESS_MODE", "bolt")
    # IAM auth adds request signing. If your cluster has IAM auth disabled
    # (simplest for a test cluster) leave this False.
    iam_auth: bool = _env("NEPTUNE_IAM_AUTH", "false").lower() == "true"

    @property
    def bolt_uri(self) -> str:
        return f"bolt+s://{self.endpoint}:{self.port}"

    @property
    def https_url(self) -> str:
        return f"https://{self.endpoint}:{self.port}"

    @property
    def enabled(self) -> bool:
        return bool(self.endpoint.strip())


@dataclass
class DataConfig:
    # Where generated / downloaded data lands.
    data_dir: str = _env("BENCH_DATA_DIR", "data")
    # Size of the synthetic capital-markets lineage graph.
    # These defaults produce ~50k nodes / ~150k edges: big enough to be
    # meaningful on a laptop, small enough to load in a few minutes.
    n_source_systems: int = int(_env("BENCH_N_SOURCES", "40"))
    n_datasets: int = int(_env("BENCH_N_DATASETS", "1200"))
    n_fields: int = int(_env("BENCH_N_FIELDS", "18000"))
    n_transformations: int = int(_env("BENCH_N_TRANSFORMS", "3500"))
    n_reports: int = int(_env("BENCH_N_REPORTS", "600"))
    n_controls: int = int(_env("BENCH_N_CONTROLS", "900"))
    # Random seed so every run builds an identical graph (reproducibility).
    seed: int = int(_env("BENCH_SEED", "42"))
    # Batch size for writes (UNWIND chunks).
    load_batch_size: int = int(_env("BENCH_BATCH", "1000"))


@dataclass
class RunConfig:
    # How many times each query is executed. First run is a warm-up and is
    # reported separately (cold vs warm), the rest are averaged.
    iterations: int = int(_env("BENCH_ITER", "6"))
    warmup: int = int(_env("BENCH_WARMUP", "1"))
    # Per-query timeout in seconds (guards against a pathological traversal).
    timeout_s: int = int(_env("BENCH_TIMEOUT", "120"))
    results_dir: str = _env("BENCH_RESULTS_DIR", "results")


@dataclass
class Config:
    neo4j: Neo4jConfig = field(default_factory=Neo4jConfig)
    neptune: NeptuneConfig = field(default_factory=NeptuneConfig)
    data: DataConfig = field(default_factory=DataConfig)
    run: RunConfig = field(default_factory=RunConfig)


CONFIG = Config()
