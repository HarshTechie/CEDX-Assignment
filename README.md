# Tiny CEDX Agent Fleet — Financial Services · Compliance Operations (invoice/payment approval)

**CASE_ID: `CEDX-A0CF47`** · one-command run: `docker compose up`

A small but genuinely-working multi-agent fleet (Orchestrator + Worker + Verifier) that
ingests messy work-requests, catches every planted data- and agent-failure, drafts a
branded approval package with a cheap-by-default model router, has an independent
Verifier check the Worker before anything ships, enforces an approval state machine + a
CASE_ID-bound second approver, and writes an append-only, hash-chained audit with
per-agent traces and replay.

---

## 1. Industry & Scope
- **Industry:** Financial Services — **Compliance Operations** (from cedxsystems.com/workflows), implemented as an invoice / payment-approval pipeline: extract → validate → exception-route → approve → archive.
- **Tier:** Tiny (single batch, ~25 records).
- **CASE_ID:** `CEDX-A0CF47`. **Amendment:** `legal_counsel` @ `54000` (computed at runtime).
- Domain is intentionally thin — depth is in the architecture, not the domain.

## 2. Agent topology
Three agents with Pydantic typed contracts ([`app/contracts.py`](app/contracts.py)) and an
enforced `can_call` allow-list ([`app/agents/base.py`](app/agents/base.py)). Full diagram
in [ARCHITECTURE.md](ARCHITECTURE.md).

| Agent | Role | Contract | Models | can_call | File |
|---|---|---|---|---|---|
| Orchestrator | orchestrator | `NormalizedRecord → RecordOutcome` | — | Worker, Verifier | [orchestrator.py](app/agents/orchestrator.py) |
| Worker | worker | `WorkerRequest → WorkerResult` | gpt-4o-mini ↔ gpt-4o (router) | — | [worker.py](app/agents/worker.py) |
| Verifier | verifier | `VerifierRequest → VerifierResult` | — (deterministic critic) | — | [verifier.py](app/agents/verifier.py) |

The Verifier independently re-derives every field and can **overrule** the Worker; the
disagreement is logged with both sides (`make trace ID=REC-015`).

## 3. How to Run
**One command (offline, no key):**
```
docker compose up          # builds linux/amd64, runs `make demo && make verify`
```
Or locally with Python 3.11+:
```
pip install -r requirements.txt
make demo                  # writes out/audit.json, out/exception_queue.json, out/package/
make verify                # runs the provided grading gate → prints PASS
make eval                  # per-agent eval scores
make trace ID=REC-015      # the Verifier catching a hallucinating Worker
make trace ID=REC-001      # a clean delivery + approval state machine
make replay ID=REC-016     # data lineage for the schema-drift record
make probe-approval  make probe-agent-failure  make probe-budget
make probe-append-only  make probe-idempotency  make probe-crash
```
**Real-LLM path (held-out grading):**
```
REPLAY_LLM=false LLM_API_KEY=… LLM_MODEL=gpt-4o-mini SEED_DIR=/path/to/heldout make demo
```
Env vars: `REPLAY_LLM`, `SEED_DIR`, `CASE_ID`, `PIPELINE_NOW`,
`MAX_COST_USD_PER_RECORD`, `MAX_STEPS_PER_RECORD`, `LLM_API_KEY/LLM_MODEL/LLM_BASE_URL`,
`LLM_STRONG_MODEL` (optional escalation tier).

## 4. Controls (probe → what it proves)
| Probe | Proves | Result |
|---|---|---|
| `make verify` | audit integrity, ≥3 agents, delivered fields hash to a worker transcript | PASS |
| `probe-approval` | non-approved + amendment-scoped items refused server-side; countersign releases | exit 0 |
| `probe-agent-failure` | hallucinated / malformed / tampered Worker output caught + routed | exit 0 |
| `probe-budget` | per-record cost ceiling breach → `BUDGET_EXCEEDED` + routed | exit 0 |
| `probe-append-only` | hash-chained audit; tamper/deletion of a past entry detected | exit 0 |
| `probe-idempotency` | run twice → identical package + records, no dupes | exit 0 |
| `probe-crash` (bonus) | audit lost mid-run → re-run resumes identical from persisted store | exit 0 |

## 5. Planted-problem handling
**Data layer** (`stages/exceptions.py`, all rule-based):

| Reason | Detector | Seed record | Class |
|---|---|---|---|
| `STALE` | deadline < `PIPELINE_NOW` | REC-011 | A |
| `MISSING_INPUT` | required field null | REC-012 | A |
| `OUTLIER` | modified z-score > 3.5 (robust) | REC-013 | A |
| `INJECTION_BLOCKED` | intent-pattern regex on notes | REC-014, REC-022 | A |
| `LOW_CONFIDENCE` | Worker abstains on ambiguity | REC-021 | A |
| `UNVERIFIED_ANOMALY` | validation fails, no rule matched (held-out catch-all) | — | A |
| `SCHEMA_DRIFT` | non-primary alias (`value`→`amount`), mapped + logged | REC-016 | B |
| `SUPERSEDED_VERSION` | same id twice → keep latest, log prior | REC-017 (v1) | B |

**Agent layer** (Verifier / Orchestrator):

| Reason | Caught by | Seed / probe |
|---|---|---|
| `AGENT_HALLUCINATION` | Verifier grounding → overrule → retry → route | REC-015 (seed) |
| `AGENT_MALFORMED` | Verifier: unrepairable structured output | `probe-agent-failure` |
| `AGENT_LOOP` | Orchestrator step ceiling | (step cap) |
| `BUDGET_EXCEEDED` | Orchestrator cost ceiling | `probe-budget` |

**Reached delivery (15):** REC-001–010, 016 (SCHEMA_DRIFT), 017(v2), 018, 019, 020.
**Held as exceptions (7 + 1 superseded):** 011, 012, 013, 014, 015, 021, 022, and REC-017 v1 superseded.

## 6. Generalization
Nothing is keyed to an id or a literal value. Outliers use a batch-relative robust
statistic; injection uses intent patterns; schema drift is a data-driven alias map
(`schema/field_mapping.json` — a new rename is a one-line data change, not code); the
catch-all `UNVERIFIED_ANOMALY` snares undocumented held-out anomalies; the Verifier's
grounding check catches *any* invented value, not a known one.

## 7. LLM / agent contract & eval
- **Offline (`REPLAY_LLM=true`, default):** only model calls are replaced, by committed
  `transcripts/*.json` tagged with the calling agent; every other stage runs for real.
  Delivered fields hash back to a Worker transcript (`verify_audit.py` #8/#14).
- **Real (`REPLAY_LLM=false`):** OpenAI-compatible client; supports `gpt-4o-mini` /
  `claude-3-5-haiku` / `gemini-1.5-flash` via `LLM_MODEL`.
- **Eval (`make eval`):** 12 golden cases; Orchestrator routing accuracy, Verifier
  catch-rate, and an **LLM-judge** scoring Worker groundedness. Current: 1.00 / 1.00 / 1.00.

## 8. Cost & scale
avg **$0.000264/record** · total **$0.0058** (22 records) · p95 **700 ms/record** ·
projected **≈ $2.64 / 10,000 records/day**. Blocked records cost $0 (never touch an LLM);
only 2 hard records escalated to the strong model. What breaks first at 10k: see
[DECISIONS.md](DECISIONS.md).

## 9. Amendment
`H=sha256("CEDX-A0CF47")` → `role=legal_counsel`, `threshold=54000`. Printed at startup
(`AMENDMENT: role=legal_counsel threshold=54000`), recorded under `amendment` in
`audit.json`, and enforced by the server-side delivery gate: any record with amount ≥
54000 needs a recorded `legal_counsel` approval **in addition** to the operator's, or
delivery is refused and the refusal is logged (`make probe-approval`).

## 10. AI usage / real-vs-faked
AI assistants were used to write the code (as expected). What is **real** and
load-bearing: intake/normalize/exception detection/router/state-machine/audit all run for
real; the Worker LLM produces the delivered summaries and delivered fields hash to its
transcript. The offline `transcripts/` are deterministic **reference fixtures**
(`app/reference_model.py`) that mirror the model's structured output so the demo runs with
no key; regenerate them against a real model any time with
`REPLAY_LLM=false python -m app record`. The held-out grading run uses the real model
path, so the LLM is verifiably load-bearing and generalizing.

## 11. Tradeoffs & next week
- **Deterministic Verifier over an LLM Verifier** — chosen for reliability (a grounding
  check can't itself hallucinate); the LLM-judge lives in eval instead. Next: add an LLM
  semantic pass as a *second* Verifier opinion behind the deterministic gate.
- **In-process agents, not a bus** — right at this tier; next week I'd move to a
  per-record job queue for the 10k/day path (agents are already stateless per record).
- **Single-file audit** — fine for 25 records; next: JSONL append-only log keeping the
  hash chain.
- **Live-extension ready:** adding a 4th agent (e.g. a PII Redactor), a new reason
  code + detector, or a 2-pass high-value Verifier are all localized changes — see
  ARCHITECTURE.md for exactly where each would slot in.
