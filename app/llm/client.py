"""LLM client with deterministic offline replay.

Two modes, selected by REPLAY_LLM:

* replay=true (default, offline, no network): the model call is replaced by a
  committed transcript in transcripts/. Lookup is by a stable `key` derived from
  (agent, prompt_version, record_id, source_version_hash, attempt) — NOT by model,
  so the recorded call replays exactly as it happened. Only the model call is
  stubbed; every other stage runs for real.

* replay=false (real): calls an OpenAI-compatible chat endpoint, then writes the
  transcript so a later offline run is deterministic. Each transcript is tagged with
  the AGENT that made the call (verify_audit.py check #14 requires this).

Transcript filename is the hex of the response hash, and the file records
response_hash = sha(response) and (for Worker calls) delivered_fields_hash — exactly
the chain verify_audit.py validates.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from ..util import canon, hexstem, sha
from .router import price_of


@dataclass
class LLMResult:
    response: dict
    agent: str
    model: str
    prompt_version: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: float
    transcript_hash: str
    delivered_fields_hash: Optional[str]
    from_replay: bool


class TranscriptMissing(Exception):
    pass


class LLMClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self.dir = cfg.transcripts_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.replay = cfg.replay_llm
        self._index: dict[str, Path] = {}
        if self.replay:
            self._build_index()

    def _build_index(self):
        for tf in self.dir.glob("*.json"):
            try:
                t = json.loads(tf.read_text(encoding="utf-8"))
            except Exception:
                continue
            if "key" in t:
                self._index[t["key"]] = tf

    @staticmethod
    def make_key(agent: str, prompt_version: str, record_id: str,
                 source_version_hash: str, attempt: int) -> str:
        return sha({
            "agent": agent, "prompt_version": prompt_version,
            "record_id": record_id, "source_version_hash": source_version_hash,
            "attempt": attempt,
        })

    def call(
        self,
        *,
        agent: str,
        prompt_version: str,
        model: str,
        messages: list[dict],
        record_id: str,
        source_version_hash: str,
        attempt: int = 1,
        derive_delivered_fields: Optional[Callable[[dict], Optional[dict]]] = None,
    ) -> LLMResult:
        key = self.make_key(agent, prompt_version, record_id, source_version_hash, attempt)
        if self.replay:
            return self._replay(key, agent, record_id)
        return self._real(key, agent, prompt_version, model, messages,
                          record_id, source_version_hash, attempt, derive_delivered_fields)

    # ----------------------------- replay ---------------------------------- #
    def _replay(self, key: str, agent: str, record_id: str) -> LLMResult:
        tf = self._index.get(key)
        if tf is None:
            raise TranscriptMissing(
                f"no committed transcript for agent={agent} record={record_id} "
                f"(key={key[:22]}...). Record it with REPLAY_LLM=false, or run "
                f"`python -m app.record`.")
        t = json.loads(tf.read_text(encoding="utf-8"))
        return LLMResult(
            response=t["response"],
            agent=t.get("agent", agent),
            model=t.get("model", ""),
            prompt_version=t.get("prompt_version", ""),
            tokens_in=t.get("tokens_in", 0),
            tokens_out=t.get("tokens_out", 0),
            cost_usd=t.get("cost_usd", 0.0),
            latency_ms=t.get("latency_ms", 0.0),
            transcript_hash=t["response_hash"],
            delivered_fields_hash=t.get("delivered_fields_hash"),
            from_replay=True,
        )

    # ------------------------------ real ----------------------------------- #
    def _real(self, key, agent, prompt_version, model, messages, record_id,
              source_version_hash, attempt, derive_delivered_fields) -> LLMResult:
        import httpx
        t0 = time.time()
        resp = httpx.post(
            f"{self.cfg.llm_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.cfg.llm_api_key}"},
            json={"model": model, "messages": messages,
                  "temperature": 0, "response_format": {"type": "json_object"}},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        latency_ms = (time.time() - t0) * 1000.0
        content = data["choices"][0]["message"]["content"]
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            parsed = {"_raw": content, "_malformed": True}
        usage = data.get("usage", {})
        tokens_in = int(usage.get("prompt_tokens", 0))
        tokens_out = int(usage.get("completion_tokens", 0))
        cost = price_of(model, tokens_in, tokens_out)
        response = {"parsed": parsed}
        return self._persist(key, agent, prompt_version, model, messages, response,
                             tokens_in, tokens_out, cost, latency_ms,
                             derive_delivered_fields)

    def record_synthetic(self, *, key, agent, prompt_version, model, messages,
                         response, tokens_in, tokens_out, latency_ms,
                         derive_delivered_fields=None) -> LLMResult:
        """Used by app.record to author the offline replay fixtures deterministically
        (see DECISIONS.md — these mirror the reference model's structured output)."""
        cost = price_of(model, tokens_in, tokens_out)
        return self._persist(key, agent, prompt_version, model, messages, response,
                             tokens_in, tokens_out, cost, latency_ms,
                             derive_delivered_fields)

    def _persist(self, key, agent, prompt_version, model, messages, response,
                 tokens_in, tokens_out, cost, latency_ms, derive_delivered_fields) -> LLMResult:
        response_hash = sha(response)
        df = derive_delivered_fields(response) if derive_delivered_fields else None
        df_hash = sha(df) if df is not None else None
        transcript = {
            "key": key,
            "agent": agent,
            "model": model,
            "prompt_version": prompt_version,
            "request": {"messages": messages},
            "response": response,
            "response_hash": response_hash,
            "delivered_fields_hash": df_hash,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": cost,
            "latency_ms": latency_ms,
        }
        # Filename is the response-hash hex (the contract verify_audit.py checks for
        # delivered records). Delivered drafts are unique per record so never collide.
        # A retry that reproduces an identical (e.g. hallucinated) response would
        # collide on the same hash under a DIFFERENT key — disambiguate those so both
        # attempts stay individually replayable.
        base = hexstem(response_hash)
        path = self.dir / f"{base}.json"
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing.get("key") != key:
                path = self.dir / f"{base}.{hexstem(key)[:10]}.json"
        path.write_text(json.dumps(transcript, indent=2, ensure_ascii=False), encoding="utf-8")
        return LLMResult(
            response=response, agent=agent, model=model, prompt_version=prompt_version,
            tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost,
            latency_ms=latency_ms, transcript_hash=response_hash,
            delivered_fields_hash=df_hash, from_replay=False,
        )
