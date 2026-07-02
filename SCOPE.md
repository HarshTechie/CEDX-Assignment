# SCOPE — Tiny CEDX Agent Fleet (tracer checkpoint)

- **Candidate name:** Harsh Tak
- **CASE_ID (assigned live):** CEDX-A0CF47
- **Industry chosen (from cedxsystems.com/workflows):** Financial Services — Compliance Operations (invoice / payment-approval pipeline)
- **Tier:** Tiny (single-batch, ~25 records)
- **Stack / language:** Python 3.11+, Pydantic typed contracts, OpenAI-compatible LLM client with offline transcript replay

## Amendment (computed from CASE_ID)
```
H = sha256("CEDX-A0CF47")
  = 95e3638d6d08b4753864576d5448249956c9f39070db55a2ba1333f69f64268a
role R      = ["risk_officer","legal_counsel","compliance","finance_controller"][ int(H[0],16) % 4 ]
threshold T = 10000 + (int(H[1:3],16) % 50) * 1000
```
- **My role R:** `legal_counsel`
- **My threshold T:** `54000`
- **Rule:** any record whose normalized `amount` >= 54000 needs a recorded approval by
  `legal_counsel`, *in addition to* the normal operator approval, before delivery.

## Agent fleet (>=3 agents, typed contracts)
| Agent | Role | Model policy | can_call |
|---|---|---|---|
| `Orchestrator` | orchestrator | none (control plane) | `Worker`, `Verifier` |
| `Worker` | worker | router: cheap default -> strong on escalation | (none) |
| `Verifier` | verifier | strong (grounding + LLM cross-check) | (none) |

## The 5 governed stages
- [x] Sources/Intake — parse `feed.json` + inbox `.eml` / `.pdf`, persist each record
- [x] Orchestration — declarative normalize (versioned schema + field-map file) + exception queue, all reason codes
- [x] Assembly — Worker drafts branded output via model router; abstain on ambiguity
- [x] Review — approval state machine + Verifier overrule + CASE_ID amendment (legal_counsel @ 54000)
- [x] Delivery — branded package + append-only audit + replay/trace

## What I will deliberately NOT build (and why)
- **No web UI / auth** — the operator surface is a CLI. The graded skill is the fleet + controls, not front-end.
- **No real message bus / multi-process** — agents are in-process typed calls with an explicit `can_call`
  allow-list. A bus adds ops complexity without changing the graded architecture at this tier.
- **No fine-tuning / embeddings** — normalization + detection are rule-based on purpose so they generalize
  to the held-out seed; LLMs are load-bearing only where judgment is genuinely needed (Assembly + Verify).
