"""Verifier agent — Stage 4 Review (agent-checks-agent).

Independent, DETERMINISTIC-by-design critic. Determinism is a feature: a grounding
check that can never itself hallucinate is what lets the fleet reliably catch a
Worker that does, on data it has never seen. It:

  1. re-derives every structured field from the source and demands an exact match
     (a Worker that alters payer/amount/date is caught here);
  2. checks `summary` is grounded — every number/date in the prose must already exist
     in the structured fields (a Worker that invents "$12,000" is caught here);
  3. can OVERRULE the Worker: if the Worker was confident but the draft is ungrounded,
     the Verifier fails it and the disagreement is logged with both sides.

Contract: VerifierRequest -> VerifierResult. can_call = [].
"""
from __future__ import annotations

import re

from ..contracts import NormalizedRecord, VerifierResult, WorkerResult

PROMPT_VERSION = "verifier.v1"

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _num_norm(s: str) -> str:
    return s.replace(",", "").rstrip("0").rstrip(".") if "." in s else s.replace(",", "")


def _grounded_numbers(rec: NormalizedRecord, fields: dict) -> set[str]:
    allowed = set()
    if rec.amount is not None:
        allowed.add(_num_norm(str(rec.amount)))
        allowed.add(_num_norm(str(int(rec.amount))) if float(rec.amount).is_integer() else str(rec.amount))
    # digits that legitimately appear inside the id or the date are fine
    for token in re.findall(r"\d+", (rec.id or "") + " " + (rec.deadline or "")):
        allowed.add(token)
    return allowed


class Verifier:
    name = "Verifier"

    def run(self, rec: NormalizedRecord, draft: WorkerResult) -> VerifierResult:
        issues: list[str] = []

        if draft.malformed:
            return VerifierResult(
                id=rec.id, verdict="fail", grounded=False, overruled=True,
                issues=["worker output structurally invalid / unrepairable"],
                reason_code="AGENT_MALFORMED", prompt_version=PROMPT_VERSION)

        if draft.abstained or draft.delivered_fields is None:
            return VerifierResult(
                id=rec.id, verdict="needs_human", grounded=False, overruled=False,
                issues=["worker abstained (ambiguous record)"],
                reason_code=None, prompt_version=PROMPT_VERSION)

        f = draft.delivered_fields
        # 1. structured fields must match the source verbatim
        expect = {
            "record_id": rec.id,
            "payer": rec.owner,
            "amount_usd": rec.amount,
            "due_date": rec.deadline,
            "category": rec.category,
            "currency": "USD",
            "disposition": "READY_FOR_APPROVAL",
        }
        for k, v in expect.items():
            got = f.get(k)
            if k == "amount_usd":
                try:
                    if got is None or float(got) != float(v):
                        issues.append(f"amount_usd={got!r} != source {v!r}")
                except (TypeError, ValueError):
                    issues.append(f"amount_usd={got!r} not numeric")
            elif str(got) != str(v):
                issues.append(f"{k}={got!r} != source {v!r}")

        # 2. summary grounding — no invented numbers or dates
        summary = str(f.get("summary", ""))
        allowed = _grounded_numbers(rec, f)
        for tok in _NUM.findall(summary):
            if _num_norm(tok) not in allowed and tok not in allowed:
                issues.append(f"summary contains ungrounded number {tok!r}")
        for d in _DATE.findall(summary):
            if d != (rec.deadline or ""):
                issues.append(f"summary contains ungrounded date {d!r}")

        if issues:
            # The Worker produced a confident-but-wrong draft → overrule it.
            return VerifierResult(
                id=rec.id, verdict="fail", grounded=False, overruled=True,
                issues=issues, reason_code="AGENT_HALLUCINATION",
                prompt_version=PROMPT_VERSION)

        return VerifierResult(
            id=rec.id, verdict="pass", grounded=True, overruled=False,
            issues=[], reason_code=None, prompt_version=PROMPT_VERSION)
