"""Author / refresh the offline replay transcripts.

    python -m app.record                 # deterministic reference fixtures (no network)
    REPLAY_LLM=false python -m app.record # record against a REAL model via app.pipeline

The first form writes transcripts for every Worker call the seed run will make, using
the reference model. Data-layer-blocked records make no Worker call, so they get no
transcript (the fleet never spends tokens on them).
"""
from __future__ import annotations

import json

from .agents import judge as judge_mod
from .agents import worker as worker_mod
from .agents.orchestrator import difficulty_of
from .config import Config
from .llm.client import LLMClient
from .llm.router import ModelRouter
from .prep import prepare
from .reference_model import synth_worker, wants_abstain, wants_hallucinate
from .stages.exceptions import classify_data_layer


def _tokens(messages, response) -> tuple[int, int]:
    tin = sum(len(m["content"]) for m in messages) // 4
    tout = len(json.dumps(response)) // 4
    return tin, tout


def main():
    cfg = Config.from_env()
    client = LLMClient(cfg)
    router = ModelRouter(replay=False, cheap_model="gpt-4o-mini")
    prep = prepare(cfg)
    n = 0

    for rec in prep.active:
        # Only records that survive the data layer ever call the Worker.
        if classify_data_layer(rec, cfg.pipeline_now, prep.med, prep.scale) is not None:
            continue
        diff = difficulty_of(rec)
        abstain = wants_abstain(rec)
        hallucinate = wants_hallucinate(rec)

        # attempt 1 (cheap unless the record is hard)
        _write(client, router, rec, attempt=1, escalated=False, difficulty=diff,
               abstain=abstain, hallucinate=hallucinate)
        n += 1
        # a hallucination trap fails verification and is retried on the strong tier
        if hallucinate:
            _write(client, router, rec, attempt=2, escalated=True, difficulty=diff,
                   abstain=False, hallucinate=True)
            n += 1
        # delivered records also get an LLM-judge transcript for `make eval`
        if not abstain and not hallucinate:
            _write_judge(client, rec)
            n += 1

    print(f"recorded {n} transcripts into {cfg.transcripts_dir}")


def _write_judge(client, rec):
    fields = synth_worker(rec).get("parsed", {}).get("delivered_fields", {})
    summary = fields.get("summary", "")
    messages = judge_mod.build_messages(rec, summary)
    response = {"parsed": {"score": 1.0, "grounded": True, "issues": []}}
    tin, tout = _tokens(messages, response)
    key = LLMClient.make_key(judge_mod.NAME, judge_mod.PROMPT_VERSION,
                             rec.id, rec.source_version_hash, 1)
    client.record_synthetic(
        key=key, agent=judge_mod.NAME, prompt_version=judge_mod.PROMPT_VERSION,
        model="gpt-4o-mini", messages=messages, response=response,
        tokens_in=tin, tokens_out=tout, latency_ms=240.0)


def _write(client, router, rec, *, attempt, escalated, difficulty, abstain, hallucinate):
    model = router.pick(difficulty, escalated)
    messages = worker_mod.build_messages(rec)
    response = synth_worker(rec, hallucinate=hallucinate, abstain=abstain)
    tin, tout = _tokens(messages, response)
    latency = 700.0 if model != "gpt-4o-mini" else 260.0
    key = LLMClient.make_key(worker_mod.Worker.name, worker_mod.PROMPT_VERSION,
                             rec.id, rec.source_version_hash, attempt)
    client.record_synthetic(
        key=key, agent=worker_mod.Worker.name, prompt_version=worker_mod.PROMPT_VERSION,
        model=model, messages=messages, response=response,
        tokens_in=tin, tokens_out=tout, latency_ms=latency,
        derive_delivered_fields=lambda resp: (
            resp.get("parsed", {}).get("delivered_fields")
            if not resp.get("parsed", {}).get("abstain") else None),
    )


if __name__ == "__main__":
    main()
