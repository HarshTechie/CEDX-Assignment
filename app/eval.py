"""Agent eval harness (`make eval`).

Scores each agent independently:
  * Orchestrator — routing accuracy vs the golden oracle (>=10 golden cases).
  * Verifier     — precision/recall on catching bad Worker output (synthetic drafts).
  * Worker       — LLM-judged groundedness of its generated summaries (Judge agent).

Exit 0 iff every agent clears its bar; the per-agent scores are printed regardless.
"""
from __future__ import annotations

from .agents import judge
from .agents.verifier import Verifier
from .config import Config
from .contracts import NormalizedRecord, WorkerResult
from .golden import GOLDEN
from .llm.client import LLMClient
from .pipeline import run

BAR = 0.80


def _draft(rec: NormalizedRecord, **over) -> WorkerResult:
    f = {"record_id": rec.id, "payer": rec.owner, "amount_usd": rec.amount,
         "currency": "USD", "due_date": rec.deadline, "category": rec.category,
         "disposition": "READY_FOR_APPROVAL",
         "summary": f"{rec.category} invoice for {rec.owner}: USD {int(rec.amount)}."}
    f.update(over.pop("fields", {}))
    return WorkerResult(id=rec.id, delivered_fields=f, confidence=0.9, **over)


def main(cfg: Config) -> int:
    bundle = run(cfg, log=False)
    # an id can map to several records (e.g. a delivered latest + a superseded prior),
    # so group and match if ANY record for the id satisfies the golden expectation.
    groups: dict[str, list] = {}
    for r in bundle["records"]:
        groups.setdefault(r["id"], []).append(r)
    client = LLMClient(cfg)

    # --- Orchestrator: routing accuracy vs golden --------------------------- #
    hits = 0
    for rid, exp_status, exp_code in GOLDEN:
        if any(r.get("status") == exp_status and (r.get("reason_code") or None) == exp_code
               for r in groups.get(rid, [])):
            hits += 1
    orch_score = hits / len(GOLDEN)

    # --- Verifier: catch-rate on synthetic good/bad drafts ------------------ #
    v = Verifier()
    rec = NormalizedRecord(id="EVAL-1", schema_version="output.v1", source_format="feed",
                           source_version_hash="sha256:eval", version=1, owner="e.user",
                           deadline="2026-07-30", amount=5000.0, category="REPORT", notes="")
    checks = [
        (_draft(rec), "pass"),                                                   # clean
        (_draft(rec, fields={"summary": "USD 5000 plus 99999 extra."}), "fail"), # hallucination
        (_draft(rec, fields={"amount_usd": 1.0}), "fail"),                        # tampered field
        (WorkerResult(id=rec.id, delivered_fields=None, malformed=True), "fail"), # malformed
    ]
    vhits = sum(1 for d, want in checks if v.run(rec, d).verdict == want)
    ver_score = vhits / len(checks)

    # --- Worker: LLM-judged groundedness of delivered summaries ------------- #
    prep_ids = {rid for rid, s, _ in GOLDEN if s == "delivered"}
    scores = []
    from .prep import prepare
    prep = prepare(cfg)
    norm_by_id = {n.id: n for n in prep.active}
    for rid in sorted(prep_ids):
        delivered = next((r for r in groups.get(rid, []) if r.get("status") == "delivered"), {})
        df = delivered.get("delivered_fields") or {}
        summary = df.get("summary", "")
        if rid in norm_by_id and summary:
            scores.append(judge.score(client, norm_by_id[rid], summary, cfg.llm_model))
    worker_score = sum(scores) / len(scores) if scores else 0.0

    print("=== agent eval ===")
    print(f"Orchestrator routing  : {orch_score:.2f}  ({hits}/{len(GOLDEN)} golden cases)")
    print(f"Verifier catch-rate   : {ver_score:.2f}  ({vhits}/{len(checks)} synthetic drafts)")
    print(f"Worker groundedness   : {worker_score:.2f}  (LLM-judge over {len(scores)} deliveries)")
    passed = orch_score >= BAR and ver_score >= BAR and worker_score >= BAR
    print(f"RESULT: {'PASS' if passed else 'FAIL'} (bar={BAR})")
    return 0 if passed else 1
