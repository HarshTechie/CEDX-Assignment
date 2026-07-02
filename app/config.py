"""Runtime configuration + the CASE_ID-bound amendment.

Everything the fleet needs to know about *this* run is centralised here so it can
be read once and passed as an immutable object. No module reaches into os.environ
directly except this one.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

# Repo root = parent of this file's package.
ROOT = Path(__file__).resolve().parent.parent

AMENDMENT_ROLES = ["risk_officer", "legal_counsel", "compliance", "finance_controller"]


def compute_amendment(case_id: str) -> tuple[str, int]:
    """Derive (role, threshold) from the CASE_ID exactly as TASK.md §8 specifies.

    H = sha256(CASE_ID) lowercase hex
    R = ROLES[int(H[0],16) % 4]
    T = 10000 + (int(H[1:3],16) % 50) * 1000
    """
    h = hashlib.sha256(case_id.encode("utf-8")).hexdigest()
    role = AMENDMENT_ROLES[int(h[0], 16) % 4]
    threshold = 10000 + (int(h[1:3], 16) % 50) * 1000
    return role, threshold


def _parse_date(s: str) -> date:
    return datetime.strptime(s.strip(), "%Y-%m-%d").date()


@dataclass(frozen=True)
class Config:
    case_id: str
    seed_dir: Path
    out_dir: Path
    transcripts_dir: Path
    schema_dir: Path
    replay_llm: bool
    pipeline_now: date
    amendment_role: str
    amendment_threshold: int
    # budget ceilings (agent-layer)
    max_cost_usd_per_record: float
    max_steps_per_record: int
    # real-LLM path
    llm_api_key: str
    llm_model: str
    llm_base_url: str

    @staticmethod
    def from_env() -> "Config":
        case_id = os.environ.get("CASE_ID", "CEDX-A0CF47").strip()
        role, threshold = compute_amendment(case_id)
        seed_dir = Path(os.environ.get("SEED_DIR", str(ROOT / "seed")))
        return Config(
            case_id=case_id,
            seed_dir=seed_dir,
            out_dir=ROOT / "out",
            transcripts_dir=ROOT / "transcripts",
            schema_dir=ROOT / "schema",
            replay_llm=os.environ.get("REPLAY_LLM", "true").lower() != "false",
            pipeline_now=_parse_date(os.environ.get("PIPELINE_NOW", "2026-06-26")),
            amendment_role=role,
            amendment_threshold=threshold,
            max_cost_usd_per_record=float(os.environ.get("MAX_COST_USD_PER_RECORD", "0.02")),
            max_steps_per_record=int(os.environ.get("MAX_STEPS_PER_RECORD", "6")),
            llm_api_key=os.environ.get("LLM_API_KEY", ""),
            llm_model=os.environ.get("LLM_MODEL", "gpt-4o-mini"),
            llm_base_url=os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"),
        )

    def banner(self) -> str:
        mode = "REPLAY" if self.replay_llm else "REAL-LLM"
        return (
            f"CASE_ID={self.case_id} | mode={mode} | seed={self.seed_dir.name} | "
            f"now={self.pipeline_now.isoformat()}\n"
            f"AMENDMENT: role={self.amendment_role} threshold={self.amendment_threshold}"
        )
