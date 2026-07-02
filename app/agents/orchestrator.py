"""Orchestrator / Planner agent — the control plane.

It owns the run and DELEGATES: no business logic lives here beyond routing. Per
record it (a) runs the cheap deterministic data-layer exception checks first (blocked
records never touch an LLM), (b) picks a difficulty and dispatches to the Worker via
the router, (c) hands the draft to the Verifier, (d) on a Verifier rejection retries
with escalation up to a cap, then routes to a human, and (e) enforces the per-record
step + cost ceilings (AGENT_LOOP / BUDGET_EXCEEDED). Every step appends a span.

can_call = [Worker, Verifier].
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from ..contracts import (AgentSpan, NormalizedRecord, VerifierResult, WorkerResult,
                         reason_class)
from ..llm.router import ModelRouter
from ..stages.exceptions import classify_data_layer
from .base import Fleet
from .verifier import Verifier
from .worker import Worker

KNOWN_CATEGORIES = {"ONBOARDING", "RENEWAL", "REVIEW", "REPORT", "INTAKE"}
_AMBIGUOUS = re.compile(
    r"unclear|figure out|could be|not attached|tbd|ambiguous|\bboth\b|either|"
    r"renewal and|and a report|describes a", re.IGNORECASE)

MAX_ATTEMPTS = 2


def difficulty_of(rec: NormalizedRecord) -> str:
    """Router hint. Hard = unknown/blank category or ambiguity signals in the notes.
    Pure function of record content so it generalises (never keyed to an id)."""
    if rec.category is None or rec.category.upper() not in KNOWN_CATEGORIES:
        return "hard"
    if _AMBIGUOUS.search(rec.notes or ""):
        return "hard"
    return "easy"


@dataclass
class RecordOutcome:
    rec: NormalizedRecord
    status: str                       # "candidate" | "exception"
    reason_code: Optional[str] = None
    reason_class: Optional[str] = None
    detail: str = ""
    worker: Optional[WorkerResult] = None
    verifier: Optional[VerifierResult] = None
    spans: list[AgentSpan] = field(default_factory=list)
    schema_drift: bool = False
    difficulty: str = "easy"
    record_cost: float = 0.0


class Orchestrator:
    name = "Orchestrator"

    def __init__(self, fleet: Fleet, worker: Worker, verifier: Verifier,
                 router: ModelRouter, now: date, max_cost: float, max_steps: int):
        self.fleet = fleet
        self.worker = worker
        self.verifier = verifier
        self.router = router
        self.now = now
        self.max_cost = max_cost
        self.max_steps = max_steps

    def difficulty(self, rec: NormalizedRecord) -> str:
        return difficulty_of(rec)

    def process(self, rec: NormalizedRecord, med: float, scale: float,
                schema_drift: bool) -> RecordOutcome:
        out = RecordOutcome(rec=rec, status="candidate", schema_drift=schema_drift)

        # (a) data-layer exceptions — deterministic, pre-LLM, zero token cost.
        info = classify_data_layer(rec, self.now, med, scale)
        if info is not None:
            out.status = "exception"
            out.reason_code = info.reason_code
            out.reason_class = info.reason_class
            out.detail = info.detail
            out.spans.append(AgentSpan(agent=self.name, status="routed",
                                       note=f"data-layer {info.reason_code}: {info.detail}"))
            return out

        # (b-d) assembly + verify loop with escalation.
        out.difficulty = self.difficulty(rec)
        out.spans.append(AgentSpan(agent=self.name, status="ok",
                                   note=f"dispatch difficulty={out.difficulty}"))
        escalated = False
        steps = 0
        for attempt in range(1, MAX_ATTEMPTS + 1):
            steps += 1
            if steps > self.max_steps:
                return self._kill(out, "AGENT_LOOP", f"exceeded {self.max_steps} steps")

            model = self.router.pick(out.difficulty, escalated)
            self.fleet.guard(self.name, self.worker.name)
            wres, llm = self.worker.run(rec, model, attempt)
            out.worker = wres
            out.record_cost += llm.cost_usd
            out.spans.append(AgentSpan(
                agent=self.worker.name,
                status="retried" if attempt > 1 else ("abstained" if wres.abstained else "ok"),
                model=llm.model, prompt_version=wres.prompt_version,
                tokens_in=llm.tokens_in, tokens_out=llm.tokens_out,
                cost_usd=llm.cost_usd, latency_ms=llm.latency_ms,
                retries=attempt - 1, transcript_hash=wres.transcript_hash))

            # (e) budget ceiling — never silently overspend.
            if out.record_cost > self.max_cost:
                return self._route(out, "BUDGET_EXCEEDED",
                                   f"record cost ${out.record_cost:.5f} > ceiling ${self.max_cost}")

            steps += 1
            self.fleet.guard(self.name, self.verifier.name)
            vres: VerifierResult = self.verifier.run(rec, wres)
            out.verifier = vres
            vstatus = {"pass": "ok", "fail": "overruled", "needs_human": "routed"}[vres.verdict]
            out.spans.append(AgentSpan(
                agent=self.verifier.name, status=vstatus,
                prompt_version=vres.prompt_version, verdict=vres.verdict,
                note="; ".join(vres.issues) or None))

            if vres.verdict == "pass":
                return out  # clean candidate → goes to Review
            if vres.verdict == "needs_human":
                return self._route(out, "LOW_CONFIDENCE",
                                   "worker abstained on ambiguous record")
            # verdict == "fail": Verifier overruled the Worker → escalate + retry.
            escalated = True

        # attempts exhausted and still failing → route the agent failure to a human.
        return self._route(out, out.verifier.reason_code or "AGENT_MALFORMED",
                           "; ".join(out.verifier.issues))

    def _route(self, out: RecordOutcome, code: str, detail: str) -> RecordOutcome:
        out.status = "exception"
        out.reason_code = code
        out.reason_class = reason_class(code)
        out.detail = detail
        out.spans.append(AgentSpan(agent=self.name, status="routed",
                                   note=f"route {code}: {detail}"))
        return out

    def _kill(self, out: RecordOutcome, code: str, detail: str) -> RecordOutcome:
        out.status = "exception"
        out.reason_code = code
        out.reason_class = reason_class(code)
        out.detail = detail
        out.spans.append(AgentSpan(agent=self.name, status="killed",
                                   note=f"kill {code}: {detail}"))
        return out
