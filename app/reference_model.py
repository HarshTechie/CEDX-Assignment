"""Reference-model response generator (DEV FIXTURE TOOL — not part of the graded run).

`app.record` uses this to author the offline replay transcripts deterministically so
`REPLAY_LLM=true make demo` works with no network and no API key. Each response mirrors
the structured JSON a real cheap model returns for the Worker prompt. Regenerate the
fixtures against a REAL model any time with:  REPLAY_LLM=false python -m app.record

The seed's two planted agent behaviours are reproduced here so the Verifier can be seen
to fire in the offline demo:
  * ABSTAIN  — record is genuinely ambiguous (unresolved category / value not attached)
  * HALLUCINATE — record invites the model to "figure out" a contradiction; a naive
    model invents an unsupported number in the summary, which the Verifier rejects.
On the real held-out path these behaviours instead come from the graders' injected
failures; the Verifier catch is identical either way.
"""
from __future__ import annotations

from .contracts import NormalizedRecord


def wants_abstain(rec: NormalizedRecord) -> bool:
    n = (rec.notes or "").lower()
    return rec.category in (None, "", "?") or "not attached" in n


def wants_hallucinate(rec: NormalizedRecord) -> bool:
    n = (rec.notes or "").lower()
    return "figure out" in n or "inconsistent" in n or "reconcile" in n


def synth_worker(rec: NormalizedRecord, *, hallucinate: bool = False,
                 abstain: bool = False) -> dict:
    if abstain:
        parsed = {
            "delivered_fields": None, "confidence": 0.25, "abstain": True,
            "rationale": ("Category unresolved and the amount is cited in a side letter "
                          "that is not attached; cannot produce a confident package."),
        }
        return {"parsed": parsed}

    amt = int(rec.amount) if rec.amount is not None and float(rec.amount).is_integer() else rec.amount
    cat = (rec.category or "Item").title()
    fields = {
        "record_id": rec.id, "payer": rec.owner, "amount_usd": rec.amount,
        "currency": "USD", "due_date": rec.deadline, "category": rec.category,
        "disposition": "READY_FOR_APPROVAL",
    }
    if hallucinate:
        # invents a "$12,000 add-on" not present in any source field
        fields["summary"] = (f"{cat} invoice for {rec.owner}: USD {amt} base "
                             f"plus a 12,000 add-on, due {rec.deadline}.")
        conf, rat = 0.88, "Resolved the ambiguity and added the implied add-on."
    else:
        fields["summary"] = f"{cat} invoice for {rec.owner}: USD {amt}, due {rec.deadline}."
        conf, rat = 0.92, "Structured fields copied from record; summary grounded."
    return {"parsed": {"delivered_fields": fields, "confidence": conf,
                       "abstain": False, "rationale": rat}}
