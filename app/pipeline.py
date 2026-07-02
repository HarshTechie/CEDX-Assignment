"""The end-to-end run (`make demo`). Wires the 5 governed stages under the agent fleet
and writes out/audit.json, out/exception_queue.json and the branded package.
"""
from __future__ import annotations

import json
from datetime import datetime, time

from . import PIPELINE_VERSION
from .agents.base import AgentSpec, Fleet
from .agents.orchestrator import Orchestrator
from .agents.verifier import PROMPT_VERSION as VV
from .agents.verifier import Verifier
from .agents.worker import PROMPT_VERSION as WV
from .agents.worker import Worker
from .audit_log import AuditLog, RunClock
from .config import Config
from .contracts import reason_class
from .llm.client import LLMClient
from .llm.router import ModelRouter
from .prep import Prepared, prepare
from .stages.review import Approval, DeliveryRefused, approve_verified
from .util import sha


def build_fleet(cfg: Config, client: LLMClient) -> tuple[Fleet, Orchestrator]:
    router = ModelRouter(replay=cfg.replay_llm, cheap_model=cfg.llm_model)
    fleet = Fleet([
        AgentSpec("Orchestrator", "orchestrator", [], "orch.v1", ["Worker", "Verifier"]),
        AgentSpec("Worker", "worker", [cfg.llm_model, router.strong], WV, []),
        AgentSpec("Verifier", "verifier", [], VV, []),
    ])
    orch = Orchestrator(fleet, Worker(client), Verifier(), router,
                        cfg.pipeline_now, cfg.max_cost_usd_per_record, cfg.max_steps_per_record)
    return fleet, orch


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((pct / 100.0) * (len(s) - 1)))))
    return s[k]


def run(cfg: Config, prep: Prepared | None = None, log: bool = True) -> dict:
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    clock = RunClock(datetime.combine(cfg.pipeline_now, time(0, 0, 0)))
    audit = AuditLog(clock)
    client = LLMClient(cfg)
    fleet, orch = build_fleet(cfg, client)
    prep = prep or prepare(cfg)

    audit.append("Orchestrator", "run_start")
    records: list[dict] = []
    exceptions: list[dict] = []
    package: list[dict] = []
    per_record_latency: list[float] = []

    def base_record(rec, outcome=None):
        return {
            "id": rec.id, "version": rec.version, "source_format": rec.source_format,
            "source_version_hash": rec.source_version_hash,
        }

    for rec in prep.active:
        outcome = orch.process(rec, prep.med, prep.scale, schema_drift=bool(rec.drift_notes))
        spans = [s.model_dump() for s in outcome.spans]
        per_record_latency.append(sum((s.get("latency_ms") or 0.0) for s in spans))
        rd = base_record(rec)
        rd["agent_trace"] = spans

        if outcome.status == "candidate":
            appr = Approval(rec.id, rec.amount, cfg.amendment_role,
                            cfg.amendment_threshold, audit)
            approve_verified(appr)
            try:
                appr.deliver()
                delivered = True
            except DeliveryRefused:
                delivered = False
            rd["approval_trail"] = appr.trail
            if delivered:
                df = outcome.worker.delivered_fields
                rd.update({
                    "status": "delivered",
                    "reason_code": "SCHEMA_DRIFT" if rec.drift_notes else None,
                    "reason_class": "B" if rec.drift_notes else None,
                    "transcript_hash": outcome.worker.transcript_hash,
                    "delivered_fields": df,
                    "delivered_fields_hash": outcome.worker.delivered_fields_hash,
                })
                package.append(df)
                audit.append("Verifier", "verified_pass", rec.id)
            else:  # held by the amendment / approval gate — never delivered unapproved
                rd.update({"status": "exception", "reason_code": "MISSING_INPUT",
                           "reason_class": "A"})
        else:
            rd["approval_trail"] = [{
                "state": "blocked", "actor": "Orchestrator",
                "ts": clock.tick(), "reason": outcome.reason_code}]
            rd.update({"status": "exception", "reason_code": outcome.reason_code,
                       "reason_class": outcome.reason_class or reason_class(outcome.reason_code)})
            audit.append("Orchestrator", f"exception:{outcome.reason_code}", rec.id)
            exceptions.append({"id": rec.id, "reason_code": outcome.reason_code,
                               "reason_class": rd["reason_class"], "detail": outcome.detail})
        records.append(rd)

    # superseded older versions
    for s in prep.superseded:
        records.append({
            "id": s.id, "version": s.version, "source_format": s.source_format,
            "source_version_hash": s.source_version_hash, "status": "superseded",
            "reason_code": "SUPERSEDED_VERSION", "reason_class": "B",
            "agent_trace": [], "approval_trail": [],
        })
        audit.append("Orchestrator", "superseded", s.id)
        exceptions.append({"id": s.id, "reason_code": "SUPERSEDED_VERSION",
                           "reason_class": "B", "detail": f"version {s.version} superseded"})

    # branded package + stable hash (over sorted delivered fields only)
    package_sorted = sorted(package, key=lambda d: d.get("record_id", ""))
    output_package_hash = sha(package_sorted)
    generated_at = datetime.combine(cfg.pipeline_now, time(0, 0, 0)).isoformat()

    # cost summary
    total_cost = 0.0
    for r in records:
        for sp in r.get("agent_trace", []):
            c = sp.get("cost_usd")
            if isinstance(c, (int, float)):
                total_cost += c
    n = len(prep.active)
    cost = {
        "total_usd": round(total_cost, 8),
        "avg_usd_per_record": round(total_cost / n, 8) if n else 0.0,
        "p95_latency_ms": round(_percentile(per_record_latency, 95), 2),
        "records": n,
        "projected_usd_per_10k": round((total_cost / n) * 10_000, 4) if n else 0.0,
    }

    audit.append("system", "run_complete")

    bundle = {
        "case_id": cfg.case_id,
        "pipeline_version": PIPELINE_VERSION,
        "generated_at": generated_at,
        "seed_dir": str(cfg.seed_dir),
        "pipeline_now": cfg.pipeline_now.isoformat(),
        "amendment": {"role": cfg.amendment_role, "threshold": cfg.amendment_threshold},
        "agents": fleet.roster(),
        "cost": cost,
        "output_package_hash": output_package_hash,
        "records": records,
        "events": audit.public_events(),
    }

    # write outputs
    (cfg.out_dir / "audit.json").write_text(
        json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8")
    (cfg.out_dir / "exception_queue.json").write_text(
        json.dumps(exceptions, indent=2, ensure_ascii=False), encoding="utf-8")
    pkg_dir = cfg.out_dir / "package"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "invoice_approvals.json").write_text(json.dumps({
        "case_id": cfg.case_id, "generated_at": generated_at,
        "count": len(package_sorted), "output_package_hash": output_package_hash,
        "approvals": package_sorted,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    if log:
        print(cfg.banner())
        delivered_n = sum(1 for r in records if r["status"] == "delivered")
        print(f"records={len(records)} delivered={delivered_n} "
              f"exceptions={len(exceptions)} cost=${cost['total_usd']:.5f} "
              f"proj_10k=${cost['projected_usd_per_10k']:.2f}")
    return bundle
