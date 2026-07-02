"""LLM-judge used by the eval harness (not a pipeline agent, so it is not in the audit
roster). It scores the Worker's generated `summary` for groundedness on a 0..1 scale.
In replay it reads committed judge transcripts; in real mode it calls the model.
"""
from __future__ import annotations

from ..contracts import NormalizedRecord
from ..llm.client import LLMClient, TranscriptMissing

NAME = "Judge"
PROMPT_VERSION = "judge.v1"

SYSTEM = (
    "You are an impartial LLM judge for the CEDX Financial Services Compliance-Operations "
    "lane. Score how well an "
    "invoice summary is GROUNDED in the source record on a 0..1 scale (1 = every "
    "number/name/date in the summary is supported by the source and nothing is "
    "invented). Return JSON: {\"score\": 0..1, \"grounded\": bool, \"issues\": [..]}."
)


def build_messages(rec: NormalizedRecord, summary: str) -> list[dict]:
    user = (f"SOURCE: id={rec.id} owner={rec.owner} amount={rec.amount} "
            f"deadline={rec.deadline} category={rec.category}\nSUMMARY: {summary}")
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def score(client: LLMClient, rec: NormalizedRecord, summary: str,
          cheap_model: str) -> float:
    """Returns the judge's groundedness score, with a deterministic fallback if no
    judge transcript is committed (so eval never crashes offline)."""
    try:
        res = client.call(
            agent=NAME, prompt_version=PROMPT_VERSION, model=cheap_model,
            messages=build_messages(rec, summary), record_id=rec.id,
            source_version_hash=rec.source_version_hash, attempt=1)
        return float(res.response.get("parsed", {}).get("score", 0.0))
    except TranscriptMissing:
        # fallback: reuse the Verifier's grounding check as an objective score
        from .verifier import _NUM, _grounded_numbers, _num_norm
        allowed = _grounded_numbers(rec, {})
        for tok in _NUM.findall(summary or ""):
            if _num_norm(tok) not in allowed and tok not in allowed:
                return 0.0
        return 1.0
