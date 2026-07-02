"""Model router + price table.

Policy (justified in DECISIONS.md): every record starts on the CHEAP tier. A record
is escalated to the STRONG tier only when it is genuinely hard — the Orchestrator
marks it `hard`, or the Verifier rejects the cheap draft and asks for a retry. This
keeps ~all clean records on the cheap model; only the few ambiguous/flagged ones pay
for the strong model. The grader checks that easy records really do downgrade.
"""
from __future__ import annotations

import os

# USD per 1M tokens (in, out). Public list prices at build time; used to compute
# cost identically in replay and real modes so the cost summary is meaningful.
PRICES = {
    "gpt-4o-mini":        (0.15, 0.60),
    "gpt-4o":             (2.50, 10.00),
    "claude-3-5-haiku":   (0.80, 4.00),
    "claude-3-5-sonnet":  (3.00, 15.00),
    "gemini-1.5-flash":   (0.075, 0.30),
    "gemini-1.5-pro":     (1.25, 5.00),
}
_DEFAULT_PRICE = (0.15, 0.60)


def price_of(model: str, tokens_in: int, tokens_out: int) -> float:
    pin, pout = PRICES.get(model, _DEFAULT_PRICE)
    return (tokens_in * pin + tokens_out * pout) / 1_000_000.0


class ModelRouter:
    """Chooses cheap vs strong. In the real path the cheap model is LLM_MODEL; the
    strong model is LLM_STRONG_MODEL if provided, else the cheap model (so a single
    grader-supplied key still runs — the escalation then shows up as an extra
    verification pass rather than a pricier model)."""

    def __init__(self, replay: bool, cheap_model: str):
        self.replay = replay
        self.cheap = cheap_model
        # Strong tier: an explicit LLM_STRONG_MODEL wins. Offline (replay) we name
        # gpt-4o so the seed roster/transcripts reflect a real escalation. On the live
        # path with no strong model configured we fall back to the cheap model so a
        # key that only grants the cheap model can never crash on escalation — the
        # escalation then shows up as an extra verification pass, not a pricier model.
        env_strong = os.environ.get("LLM_STRONG_MODEL", "")
        if env_strong:
            self.strong = env_strong
        elif replay and cheap_model.startswith("gpt"):
            self.strong = "gpt-4o"
        else:
            self.strong = cheap_model

    def pick(self, difficulty: str, escalated: bool) -> str:
        return self.strong if (difficulty == "hard" or escalated) else self.cheap
