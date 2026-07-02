"""The graded control probes. Each returns an exit code; 0 means the control HELD.

They deliberately try to break an invariant and pass only if the system refuses.
"""
from __future__ import annotations

import json

from .agents.verifier import Verifier
from .audit_log import AuditLog
from .config import Config
from .contracts import NormalizedRecord, WorkerResult
from .datetimes import base_clock
from .stages.review import Approval, DeliveryRefused


def _norm(amount=5000.0, **kw) -> NormalizedRecord:
    base = dict(id="PROBE-1", schema_version="output.v1", source_format="feed",
                source_version_hash="sha256:probe", version=1, owner="probe.user",
                deadline="2026-07-30", amount=amount, category="REPORT", notes="")
    base.update(kw)
    return NormalizedRecord(**base)


# --------------------------------------------------------------------------- #
def probe_approval(cfg: Config) -> int:
    """A non-approved item — and an amendment-scoped item lacking the second
    approver — must both be REFUSED delivery server-side; the approved+countersigned
    item then delivers."""
    audit = AuditLog(base_clock(cfg))
    ok = True

    # (1) not approved at all → refused
    a1 = Approval("P-UNAPPROVED", 100.0, cfg.amendment_role, cfg.amendment_threshold, audit)
    a1.transition("in_review", "operator")
    try:
        a1.deliver()
        print("FAIL: delivered an item that was never approved"); ok = False
    except DeliveryRefused as e:
        print(f"OK refused (not approved): {e}")

    # (2) amount >= threshold, operator-approved but NO amendment approver → refused
    hi = cfg.amendment_threshold + 1000
    a2 = Approval("P-HIGH", hi, cfg.amendment_role, cfg.amendment_threshold, audit)
    a2.transition("in_review", "operator")
    a2.transition("approved", "operator", reason="verifier passed")
    try:
        a2.deliver()
        print(f"FAIL: delivered ${hi} without {cfg.amendment_role} approval"); ok = False
    except DeliveryRefused as e:
        print(f"OK refused (amendment): {e}")

    # (3) add the amendment approval → now it delivers
    a2._entry("approved", cfg.amendment_role, reason="amendment countersign")
    try:
        a2.deliver()
        print(f"OK delivered ${hi} after {cfg.amendment_role} countersigned")
    except DeliveryRefused as e:
        print(f"FAIL: still refused after countersign: {e}"); ok = False

    return 0 if ok else 1


# --------------------------------------------------------------------------- #
def probe_agent_failure(cfg: Config) -> int:
    """A hallucinated and a malformed Worker output must both be caught by the
    Verifier and routed (never delivered)."""
    v = Verifier()
    rec = _norm(amount=5000.0)
    ok = True

    hallucinated = WorkerResult(
        id=rec.id, delivered_fields={
            "record_id": rec.id, "payer": rec.owner, "amount_usd": rec.amount,
            "currency": "USD", "due_date": rec.deadline, "category": rec.category,
            "disposition": "READY_FOR_APPROVAL",
            "summary": "Report invoice for probe.user: USD 5000 plus a 99999 surcharge."},
        confidence=0.9)
    r1 = v.run(rec, hallucinated)
    if r1.verdict == "fail" and r1.reason_code == "AGENT_HALLUCINATION" and r1.overruled:
        print(f"OK Verifier caught hallucination + overruled: {r1.issues}")
    else:
        print(f"FAIL: hallucination not caught: {r1}"); ok = False

    malformed = WorkerResult(id=rec.id, delivered_fields=None, malformed=True)
    r2 = v.run(rec, malformed)
    if r2.verdict == "fail" and r2.reason_code == "AGENT_MALFORMED":
        print(f"OK Verifier caught malformed output: {r2.issues}")
    else:
        print(f"FAIL: malformed not caught: {r2}"); ok = False

    # tampered structured field (worker altered the amount) → also caught
    tampered = WorkerResult(
        id=rec.id, delivered_fields={
            "record_id": rec.id, "payer": rec.owner, "amount_usd": 1.0,
            "currency": "USD", "due_date": rec.deadline, "category": rec.category,
            "disposition": "READY_FOR_APPROVAL", "summary": "ok"}, confidence=0.9)
    r3 = v.run(rec, tampered)
    if r3.verdict == "fail":
        print(f"OK Verifier caught altered field: {r3.issues}")
    else:
        print(f"FAIL: altered field not caught: {r3}"); ok = False

    return 0 if ok else 1


# --------------------------------------------------------------------------- #
def probe_budget(cfg: Config) -> int:
    """A record whose processing would exceed the per-record cost ceiling must raise
    BUDGET_EXCEEDED and be routed — never silently overspent."""
    from .llm.client import LLMClient
    from .pipeline import build_fleet
    from .prep import prepare

    # squeeze the ceiling to $0 so the very first (real, non-zero) LLM call trips it
    tight = Config(**{**cfg.__dict__, "max_cost_usd_per_record": 0.0})
    client = LLMClient(tight)
    _, orch = build_fleet(tight, client)
    prep = prepare(tight)
    clean = next(r for r in prep.active if r.category and r.category.upper() == "REPORT"
                 and "figure" not in (r.notes or "").lower()
                 and "inconsistent" not in (r.notes or "").lower())
    out = orch.process(clean, prep.med, prep.scale, schema_drift=False)
    if out.status == "exception" and out.reason_code == "BUDGET_EXCEEDED":
        print(f"OK {clean.id}: BUDGET_EXCEEDED raised + routed ({out.detail})")
        return 0
    print(f"FAIL: expected BUDGET_EXCEEDED, got status={out.status} code={out.reason_code}")
    return 1


# --------------------------------------------------------------------------- #
def probe_append_only(cfg: Config) -> int:
    """The audit event log is hash-chained. The genuine log must verify; any mutation
    of a past entry must be detected (refused)."""
    p = cfg.out_dir / "audit.json"
    if not p.exists():
        print("run `make demo` first"); return 1
    audit = json.loads(p.read_text(encoding="utf-8"))
    events = audit["events"]
    if not AuditLog.verify_chain(events):
        print("FAIL: genuine audit chain did not verify"); return 1
    print(f"OK genuine chain verifies ({len(events)} events)")

    tampered = json.loads(json.dumps(events))
    if len(tampered) < 2:
        print("FAIL: not enough events to test"); return 1
    tampered[0]["actor"] = "attacker"          # mutate a past entry
    if AuditLog.verify_chain(tampered):
        print("FAIL: tamper of a past entry was NOT detected"); return 1
    print("OK tamper of past entry detected -> refused")

    dropped = [e for e in events if e["seq"] != 1]  # delete an entry
    if AuditLog.verify_chain(dropped):
        print("FAIL: deletion of a past entry was NOT detected"); return 1
    print("OK deletion of past entry detected -> refused")
    return 0


# --------------------------------------------------------------------------- #
def probe_idempotency(cfg: Config) -> int:
    """Running demo twice must produce an identical package + record set (no dupes)."""
    from .pipeline import run
    b1 = run(cfg, log=False)
    b2 = run(cfg, log=False)
    same_hash = b1["output_package_hash"] == b2["output_package_hash"]
    same_ids = [r["id"] for r in b1["records"]] == [r["id"] for r in b2["records"]]
    same_n = len(b1["records"]) == len(b2["records"])
    if same_hash and same_ids and same_n:
        print(f"OK run twice identical: {len(b2['records'])} records, "
              f"package {b2['output_package_hash'][:20]}.. unchanged")
        return 0
    print(f"FAIL: not idempotent (hash={same_hash} ids={same_ids} n={same_n})")
    return 1


# --------------------------------------------------------------------------- #
def probe_crash(cfg: Config) -> int:
    """BONUS: simulate a crash after intake by deleting the audit, then re-run. The
    persisted raw store lets the run resume deterministically with no duplicates."""
    from .pipeline import run
    b1 = run(cfg, log=False)
    h1 = b1["output_package_hash"]
    (cfg.out_dir / "audit.json").unlink(missing_ok=True)   # crash: audit lost
    # raw store under out/store/raw survives; re-run reproduces identical output
    b2 = run(cfg, log=False)
    if b2["output_package_hash"] == h1 and len(b2["records"]) == len(b1["records"]):
        print(f"OK resumed after crash with identical output ({len(b2['records'])} records)")
        return 0
    print("FAIL: re-run after crash diverged")
    return 1
