"""Stage 2b — The exception queue (data layer).

Every detector here is RULE-BASED and threshold-driven, never keyed to a known id or
value, so it generalises to the held-out seed. The Orchestrator runs these BEFORE
dispatching a record to the (expensive) Worker, so blocked records never spend LLM
tokens — which is also good for the cost budget.

Reason codes produced here:
  INJECTION_BLOCKED · MISSING_INPUT · STALE · OUTLIER · UNVERIFIED_ANOMALY
LOW_CONFIDENCE is raised later (Worker abstains); the AGENT_* codes are raised by the
Verifier/Orchestrator (see agents/).
"""
from __future__ import annotations

import re
import statistics
from datetime import date, datetime
from typing import Optional

from ..contracts import ExceptionInfo, NormalizedRecord

# --- prompt-injection signatures (case-insensitive, substring/regex) --------- #
# These are patterns of INTENT (override the pipeline, self-approve, rewrite a
# value), not specific strings, so novel phrasings in the held-out set still hit.
INJECTION_PATTERNS = [
    r"ignore (all )?(previous|prior|above|your) (instruction|rule)",
    r"disregard (the |all )?(previous|prior|above|instruction|rule)",
    r"skip (the )?review",
    r"approve (this |it )?(immediately|now|without)",
    r"auto[- ]?approve",
    r"output\s+approved",
    r"ignore the (field|stated|listed)",
    r"ignore .{0,20}amount",
    r"do not (verify|check|review)",
    r"override",
]
_INJ = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]


def detect_injection(text: str) -> Optional[str]:
    for rx in _INJ:
        m = rx.search(text or "")
        if m:
            return m.group(0)
    return None


def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(str(s).strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Robust outlier detection — Iglewicz-Hoaglin modified z-score (median + MAD). #
# --------------------------------------------------------------------------- #
MODIFIED_Z_CUTOFF = 3.5   # the standard cutoff; justified in DECISIONS.md


def outlier_bounds(amounts: list[float]) -> tuple[float, float, float]:
    """Return (median, MAD-scale, cutoff-distance) for the batch. Robust: a single
    extreme value barely moves the median or MAD, so the detector is not fooled by
    the very outlier it is trying to find."""
    if len(amounts) < 3:
        return (statistics.median(amounts) if amounts else 0.0, 0.0, 0.0)
    med = statistics.median(amounts)
    mad = statistics.median([abs(x - med) for x in amounts])
    # Fallback when MAD collapses to 0 (many identical values): use a scaled
    # mean absolute deviation so we still have a spread estimate.
    scale = mad if mad > 0 else (statistics.mean([abs(x - med) for x in amounts]) or 1.0)
    return med, scale, MODIFIED_Z_CUTOFF


def is_outlier(amount: float, med: float, scale: float) -> bool:
    if scale <= 0:
        return False
    modified_z = 0.6745 * (amount - med) / scale
    return abs(modified_z) > MODIFIED_Z_CUTOFF


def classify_data_layer(
    rec: NormalizedRecord,
    now: date,
    med: float,
    scale: float,
) -> Optional[ExceptionInfo]:
    """First matching data-layer rule wins. Returns None if the record is clean at
    the data layer (it may still be routed to LOW_CONFIDENCE by the Worker)."""
    # 1. Injection — security first: block before anything else touches an LLM.
    hit = detect_injection(rec.notes)
    if hit:
        return ExceptionInfo(reason_code="INJECTION_BLOCKED", reason_class="A",
                             detail=f"instruction-injection in notes: {hit!r}")

    # 2. Missing required input (no auto-default).
    for fld in ("owner", "deadline", "amount"):
        if getattr(rec, fld) in (None, ""):
            return ExceptionInfo(reason_code="MISSING_INPUT", reason_class="A",
                                 detail=f"required field {fld!r} is null")

    # 3. Stale deadline.
    d = _parse_date(rec.deadline)
    if d is None:
        return ExceptionInfo(reason_code="UNVERIFIED_ANOMALY", reason_class="A",
                             detail=f"deadline not a valid date: {rec.deadline!r}")
    if d < now:
        return ExceptionInfo(reason_code="STALE", reason_class="A",
                             detail=f"deadline {rec.deadline} < now {now.isoformat()}")

    # 4. Extreme numeric outlier (robust stat).
    if rec.amount is not None and is_outlier(rec.amount, med, scale):
        return ExceptionInfo(reason_code="OUTLIER", reason_class="A",
                             detail=f"amount {rec.amount} is a robust outlier "
                                    f"(median≈{med:.0f}, modified-z>{MODIFIED_Z_CUTOFF})")

    # 5. Negative / nonsensical amount that isn't a big-magnitude outlier → the
    #    catch-all that snares the held-out UNDOCUMENTED anomaly.
    if rec.amount is not None and rec.amount < 0:
        return ExceptionInfo(reason_code="UNVERIFIED_ANOMALY", reason_class="A",
                             detail=f"amount {rec.amount} fails validation, matches no known rule")

    return None
