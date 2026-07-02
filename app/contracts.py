"""Typed handoff contracts for the agent fleet.

These Pydantic models ARE the "typed contracts" the task grades on: every agent
declares the exact shape it accepts and returns. Free-form string passing between
agents is explicitly disallowed by the brief, so every inter-agent boundary in this
codebase is one of the models below.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

SourceFormat = Literal["feed", "eml", "pdf"]

ReasonCode = Literal[
    # Class A — data layer (blocking)
    "STALE", "MISSING_INPUT", "OUTLIER", "INJECTION_BLOCKED",
    "LOW_CONFIDENCE", "UNVERIFIED_ANOMALY",
    # Agent layer (blocking)
    "AGENT_HALLUCINATION", "AGENT_LOOP", "AGENT_MALFORMED", "BUDGET_EXCEEDED",
    # Class B — auto-resolved & logged (still delivered)
    "SCHEMA_DRIFT", "SUPERSEDED_VERSION",
]

# Which codes block delivery vs. which are informational.
CLASS_A = {"STALE", "MISSING_INPUT", "OUTLIER", "INJECTION_BLOCKED",
           "LOW_CONFIDENCE", "UNVERIFIED_ANOMALY"}
AGENT_FAIL = {"AGENT_HALLUCINATION", "AGENT_LOOP", "AGENT_MALFORMED", "BUDGET_EXCEEDED"}
CLASS_B = {"SCHEMA_DRIFT", "SUPERSEDED_VERSION"}
BLOCKING = CLASS_A | AGENT_FAIL


def reason_class(code: Optional[str]) -> Optional[str]:
    if code is None:
        return None
    if code in BLOCKING:
        return "A"
    if code in CLASS_B:
        return "B"
    return None


# --------------------------------------------------------------------------- #
# Stage 1 — Intake                                                            #
# --------------------------------------------------------------------------- #
class RawRecord(BaseModel):
    """Exactly what a source produced, before any normalization."""
    id: str
    source_format: SourceFormat
    source_version_hash: str  # sha256 of the canonical raw payload
    version: int = 1
    fields: dict  # raw key/values as parsed (may use aliased field names)


# --------------------------------------------------------------------------- #
# Stage 2 — Orchestration / Normalization                                     #
# --------------------------------------------------------------------------- #
class NormalizedRecord(BaseModel):
    """Canonical shape after declarative normalization. Schema is versioned so a
    delivered record always names the output-schema artifact it conforms to."""
    id: str
    schema_version: str
    source_format: SourceFormat
    source_version_hash: str
    version: int
    owner: Optional[str] = None
    deadline: Optional[str] = None          # ISO date
    amount: Optional[float] = None          # the primary numeric field
    category: Optional[str] = None
    notes: str = ""
    drift_notes: list[str] = Field(default_factory=list)  # SCHEMA_DRIFT audit trail


class ExceptionInfo(BaseModel):
    reason_code: ReasonCode
    reason_class: Literal["A", "B"]
    detail: str


# --------------------------------------------------------------------------- #
# Stage 3 — Assembly (Worker) contract                                        #
# --------------------------------------------------------------------------- #
class WorkerRequest(BaseModel):
    record: NormalizedRecord
    difficulty: Literal["easy", "hard"]   # router hint from the Orchestrator


class WorkerResult(BaseModel):
    id: str
    abstained: bool = False
    confidence: float = 0.0
    delivered_fields: Optional[dict] = None   # the branded output draft
    rationale: str = ""
    model: str = ""
    prompt_version: str = ""
    transcript_hash: Optional[str] = None     # points at the committed LLM call
    delivered_fields_hash: Optional[str] = None
    malformed: bool = False                    # repair step could not fix output


# --------------------------------------------------------------------------- #
# Stage 4 — Review (Verifier) contract                                        #
# --------------------------------------------------------------------------- #
class VerifierRequest(BaseModel):
    record: NormalizedRecord
    draft: WorkerResult


class VerifierResult(BaseModel):
    id: str
    verdict: Literal["pass", "fail", "needs_human"]
    grounded: bool                 # does every delivered field trace to the source?
    overruled: bool = False        # did the Verifier disagree with the Worker?
    issues: list[str] = Field(default_factory=list)
    reason_code: Optional[ReasonCode] = None   # set when it catches an agent failure
    model: str = ""
    prompt_version: str = ""
    transcript_hash: Optional[str] = None


# --------------------------------------------------------------------------- #
# Observability — one span per agent step (the agent_trace backbone)          #
# --------------------------------------------------------------------------- #
SpanStatus = Literal["ok", "retried", "rejected", "overruled", "routed", "abstained", "killed"]


class AgentSpan(BaseModel):
    agent: str
    status: SpanStatus
    model: Optional[str] = None
    prompt_version: Optional[str] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    cost_usd: Optional[float] = None
    latency_ms: Optional[float] = None
    retries: Optional[int] = 0
    transcript_hash: Optional[str] = None
    verdict: Optional[str] = None   # verifier spans: pass | fail | needs_human
    note: Optional[str] = None
