# ARCHITECTURE — Tiny CEDX Agent Fleet

**CASE_ID: `CEDX-A0CF47`** · Industry: Financial Services — Compliance Operations (invoice/payment approval) · Amendment: `legal_counsel` @ `54000`

## 1. Topology

```
                          ┌───────────────────────────────────────────────┐
                          │  ORCHESTRATOR  (role: orchestrator)            │
   seed (feed/eml/pdf)    │  app/agents/orchestrator.py                    │
        │                 │  owns the run · routes each record · enforces  │
        ▼                 │  step + cost budgets · retries w/ escalation   │
  Intake+Normalize        │  can_call = [Worker, Verifier]                 │
  (stages/intake.py,      └───────┬───────────────────────────┬───────────┘
   normalize.py)                  │ WorkerRequest             │ VerifierRequest
        │                         ▼                           ▼
  data-layer exception     ┌──────────────┐            ┌──────────────────┐
  queue (pre-LLM,          │   WORKER     │  WorkerResult │   VERIFIER      │
   stages/exceptions.py)   │ role: worker │──────────────▶│ role: verifier  │
        │                  │ agents/      │            │ agents/verifier.py │
        │                  │  worker.py   │◀───overrule──│ grounding critic │
        ▼                  │ LLM + router │  (retry)   │ can OVERRULE Worker│
  Review (approval SM +    │ can_call=[]  │            │ can_call = []      │
   amendment)              └──────────────┘            └──────────────────┘
   stages/review.py                 │                          │
        │                           └──── agent_trace spans ────┘
        ▼                                      │
  Delivery + append-only audit  ◀──────────────┘
   pipeline.py, audit_log.py
        │
        ▼
  out/package/…  ·  out/audit.json  ·  out/exception_queue.json
```

Plus an **LLM-judge** (`app/agents/judge.py`) used only by `make eval` — it is not a
pipeline agent and is deliberately absent from the audit roster.

## 2. The three agents + typed contracts

Contracts are Pydantic models in [`app/contracts.py`](app/contracts.py). Every
inter-agent boundary is one of these types — no free-form string passing.

| Agent | File | Role | Input → Output contract | Models | can_call |
|---|---|---|---|---|---|
| **Orchestrator** | [orchestrator.py](app/agents/orchestrator.py) | orchestrator | `NormalizedRecord → RecordOutcome` | none (control plane) | `Worker`, `Verifier` |
| **Worker** | [worker.py](app/agents/worker.py) | worker | `WorkerRequest → WorkerResult` | cheap↔strong via router | — |
| **Verifier** | [verifier.py](app/agents/verifier.py) | verifier | `VerifierRequest → VerifierResult` | none (deterministic) | — |

The `can_call` allow-list is **enforced at runtime** by `Fleet.guard()`
([base.py](app/agents/base.py)): if the Orchestrator tried to call an agent not on its
list it would raise `CallGuardError`. The roster (name/role/models/can_call) is emitted
verbatim into `out/audit.json → agents`.

## 3. Where the Verifier overrules the Worker

`Orchestrator.process()` hands every Worker draft to `Verifier.run()` **before** it can
be delivered. The Verifier re-derives each structured field from the source and checks
the generated `summary` is grounded (no invented numbers/dates). On a mismatch it
returns `verdict="fail", overruled=True` with both sides logged (the Worker's rationale
in the Worker span, the Verifier's `issues` in the Verifier span). The Orchestrator then
retries on the strong tier; if it still fails it routes the record to a human as
`AGENT_HALLUCINATION` / `AGENT_MALFORMED`. See it live: `make trace ID=REC-015`.

## 4. Where budget + router decisions live

- **Router** ([llm/router.py](app/llm/router.py)): `pick(difficulty, escalated)` →
  cheap model by default, strong only for hard/flagged records. Prices + cost accounting
  also live here.
- **Difficulty** (`orchestrator.difficulty_of`): pure function of record content
  (unknown category or ambiguity signals in notes) — never keyed to an id.
- **Budget ceilings** (`Orchestrator.process`): after each LLM call the per-record cost
  is checked against `MAX_COST_USD_PER_RECORD`; the step count against
  `MAX_STEPS_PER_RECORD`. Breaches raise `BUDGET_EXCEEDED` / `AGENT_LOOP` and route.

## 5. Observability

Every record accumulates an ordered `agent_trace` of `AgentSpan`s
(agent, model, prompt_version, tokens, cost, latency, retries, status, verdict). Blocked
data-layer records still emit an Orchestrator span so every non-superseded record is
traceable. `make trace ID=<id>` reconstructs the full decision path and `make replay
ID=<id>` reconstructs the data lineage — both **from `out/audit.json` alone**.

## 6. The 5 governed stages under the fleet

| Stage | Module | Output |
|---|---|---|
| Intake | `stages/intake.py` + `store.py` | raw records persisted under `out/store/raw/` |
| Orchestration | `stages/normalize.py` (+ `schema/field_mapping.json`, `schema/output_schema.v1.json`) + `stages/exceptions.py` | canonical records + exception queue |
| Assembly | `agents/worker.py` + `llm/` | branded draft (structured output, abstain path) |
| Review | `stages/review.py` | approval state machine + CASE_ID amendment |
| Delivery + Audit | `pipeline.py` + `audit_log.py` | package + append-only, hash-chained `audit.json` |
