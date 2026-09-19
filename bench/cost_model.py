"""
Three-year total-cost-of-ownership model for Neo4j vs Amazon Neptune.

All figures are US-East-1 list prices verified against AWS and public pricing
guides in 2026. They are ENCODED AS ASSUMPTIONS you can edit — a cost model is
only as honest as its inputs, so every number is named and sourced in-line.

Sources (verified 2026):
  * Neptune provisioned db.r5.large on-demand: $0.348/hr (~$254/mo). [AWS pricing]
  * Neptune serverless: $0.1098 per NCU-hour; 1 NCU ~= 2 GB RAM; ~$80/mo min. [AWS]
  * Storage (Standard): $0.10 / GB-month.  I/O: $0.20 per 1M requests. [AWS]
  * Graviton r6g ~= 10% cheaper than r5; r7g/r8g ~= 16% cheaper than r6g. [AWS]
  * Neptune Free Tier: 750 hrs/month db.t3.medium or db.t4g.medium (limited period).
  * Database Savings Plans: up to 35% (1-yr, covers provisioned+serverless). [AWS Mar 2026]

Neo4j side:
  * Community/self-hosted is licence-free but you pay for the VM + ops.
  * We model self-hosted on EC2 and, separately, note Neo4j AuraDB managed as
    an alternative (its list price varies; left as an editable input).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

HOURS_PER_MONTH = 730


@dataclass
class Assumptions:
    # ---- workload sizing ----
    storage_gb: float = 50.0
    monthly_io_millions: float = 200.0   # 200M I/Os/month (traversal-heavy)
    # ---- Neptune provisioned ----
    neptune_instance_hourly: float = 0.348    # db.r5.large on-demand
    neptune_replicas: int = 0                 # extra read replicas (same hourly each)
    # ---- Neptune serverless ----
    neptune_ncu_hour: float = 0.1098
    neptune_avg_ncu: float = 2.5              # avg sustained NCUs (dev-ish load)
    neptune_serverless_hours: float = HOURS_PER_MONTH  # if paused less, lower this
    # ---- shared AWS storage/io ----
    storage_per_gb_month: float = 0.10
    io_per_million: float = 0.20
    backup_per_gb_month: float = 0.021
    backup_gb: float = 50.0
    # ---- Neo4j self-hosted on EC2 ----
    neo4j_ec2_hourly: float = 0.1664          # r6i.large on-demand ~ $0.1664/hr
    neo4j_ebs_gb: float = 100.0
    neo4j_ebs_per_gb_month: float = 0.08      # gp3
    neo4j_ops_monthly: float = 500.0          # part-time DBA / patching overhead
    # ---- discounts ----
    savings_plan_discount: float = 0.0        # e.g. 0.35 for 1-yr DSP on Neptune
    # ---- horizon ----
    years: int = 3


def _monthly_storage_io(a: Assumptions) -> float:
    return (a.storage_gb * a.storage_per_gb_month
            + a.monthly_io_millions * a.io_per_million
            + a.backup_gb * a.backup_per_gb_month)


def neptune_provisioned_monthly(a: Assumptions) -> dict:
    instances = 1 + a.neptune_replicas
    compute = a.neptune_instance_hourly * HOURS_PER_MONTH * instances
    compute *= (1 - a.savings_plan_discount)
    storage_io = _monthly_storage_io(a)
    return {
        "compute": round(compute, 2),
        "storage_io": round(storage_io, 2),
        "total": round(compute + storage_io, 2),
    }


def neptune_serverless_monthly(a: Assumptions) -> dict:
    compute = a.neptune_ncu_hour * a.neptune_avg_ncu * a.neptune_serverless_hours
    compute *= (1 - a.savings_plan_discount)
    storage_io = _monthly_storage_io(a)
    return {
        "compute": round(compute, 2),
        "storage_io": round(storage_io, 2),
        "total": round(compute + storage_io, 2),
    }


def neo4j_selfhosted_monthly(a: Assumptions) -> dict:
    compute = a.neo4j_ec2_hourly * HOURS_PER_MONTH
    storage = a.neo4j_ebs_gb * a.neo4j_ebs_per_gb_month
    ops = a.neo4j_ops_monthly
    return {
        "compute": round(compute, 2),
        "storage": round(storage, 2),
        "ops": round(ops, 2),
        "total": round(compute + storage + ops, 2),
    }


def build_report(a: Assumptions | None = None) -> dict:
    a = a or Assumptions()
    months = a.years * 12

    scenarios = {
        "Neptune Provisioned (db.r5.large)": neptune_provisioned_monthly(a),
        "Neptune Serverless (avg %.1f NCU)" % a.neptune_avg_ncu: neptune_serverless_monthly(a),
        "Neo4j Self-Hosted (EC2 r6i.large)": neo4j_selfhosted_monthly(a),
    }

    report = {"assumptions": asdict(a), "months": months, "scenarios": {}}
    for name, monthly in scenarios.items():
        report["scenarios"][name] = {
            "monthly": monthly,
            "monthly_total": monthly["total"],
            "annual_total": round(monthly["total"] * 12, 2),
            "horizon_total": round(monthly["total"] * months, 2),
        }
    # rank
    ranked = sorted(report["scenarios"].items(),
                    key=lambda kv: kv[1]["horizon_total"])
    report["cheapest"] = ranked[0][0]
    report["ranking"] = [name for name, _ in ranked]
    return report


def print_report(report: dict) -> None:
    m = report["months"]
    print("=" * 74)
    print(f"3-YEAR TCO ({m} months)  —  editable assumptions in cost_model.py")
    print("=" * 74)
    for name, s in report["scenarios"].items():
        print(f"\n{name}")
        print(f"    Monthly: ${s['monthly_total']:>10,.2f}")
        print(f"    Annual:  ${s['annual_total']:>10,.2f}")
        print(f"    {m}-mo:   ${s['horizon_total']:>10,.2f}")
    print("\n" + "-" * 74)
    print(f"Cheapest over horizon: {report['cheapest']}")
    print("Ranking: " + "  <  ".join(report["ranking"]))
    print("=" * 74)


if __name__ == "__main__":
    print_report(build_report())
