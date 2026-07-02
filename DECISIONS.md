# DECISIONS — Tiny CEDX Agent Fleet (`CEDX-A0CF47`)

## What I deliberately did NOT automate (and why)
- **Final delivery of high-value / anomalous records.** Anything the Verifier can't fully
  ground, any robust outlier, and any record ≥ the amendment threshold is *held for a
  human*, never auto-shipped. The fleet's job is to be trustworthy, not maximally
  autonomous.
- **The operator's judgment.** Approve / reject / request-changes / edit-resolve are
  human actions; the demo scripts them so the run is unattended, but the state machine
  and the server-side delivery gate are what actually enforce sign-off.
- **Category disambiguation on contradictory records.** The Worker abstains rather than
  guess (→ `LOW_CONFIDENCE`), because a wrong guess that looks confident is worse than an
  honest "needs a human".

## Outlier threshold + why it generalizes
Detector = **Iglewicz–Hoaglin modified z-score** on the batch's amounts:
`M = 0.6745·(x − median) / MAD`, flag `|M| > 3.5` (`stages/exceptions.py`). Median + MAD
are robust — one huge value barely moves them, so the detector isn't fooled by the very
outlier it hunts. It is **batch-relative, not an absolute cutoff**, which is exactly why
it generalizes: if the held-out seed scales amounts up 10×, the fence scales with them.
Crucially it does **not** collide with the amendment: the amendment threshold (54000) is
absolute, so a legitimately large record in a large-magnitude batch is *not* flagged as
an outlier and instead flows to the `legal_counsel` gate.

## Abstain threshold
The Worker abstains (`confidence ≤ 0.4`, `delivered_fields=null`) when it cannot fill the
schema from the source (unresolved category, value referenced but not attached). Abstain
→ `LOW_CONFIDENCE` → human. Chosen over a numeric-only rule because ambiguity is
semantic; the prompt instructs abstention and the deterministic Verifier is the backstop.

## Router policy + the cost numbers
Cheap model (`gpt-4o-mini`) by default; escalate to the strong tier only when the record
is `hard` (unknown category / ambiguity signals) or the Verifier rejected the cheap draft
(`llm/router.py`). Blocked data-layer records **never call an LLM** (0 tokens). On the
seed:

| metric | value |
|---|---|
| avg cost / record | **$0.000264** |
| total run cost (22 records) | **$0.0058** |
| p95 latency / record | **700 ms** |
| projected @ 10,000 records/day | **≈ $2.64 / day** |

Only the 2 genuinely-hard records escalated to the strong model; the other 20 stayed
cheap or were blocked pre-LLM. That is the whole cost story: spend nothing on the
obviously-bad, spend little on the easy, spend more only where judgment is needed.

## How provenance survives a re-run
- `audit.json` events are **hash-chained** (`audit_log.py`): each event carries `prev` =
  hash of the previous event, so any mutation/deletion of a past entry is detectable
  (`make probe-append-only`).
- Delivered fields hash to a **committed Worker transcript** tagged with the calling
  agent, so every delivered value is provably model-produced (`verify_audit.py` #8/#14).
- A **deterministic run clock** + deterministic routing make the whole bundle
  byte-identical across runs → idempotent and crash-resumable (`make probe-idempotency`,
  `make probe-crash`).

## What breaks first at 10k records/day
1. **Single-process, single-batch.** The 5-minute wall-clock budget is fine at 25 records
   but not at 10k. First change: shard intake and run the fleet as a queue of
   per-record jobs (the agents are already per-record and stateless).
2. **`audit.json` as one file.** Rewriting a growing JSON each run is O(n). Move events to
   an append-only log file (JSONL) / a real append-only table; keep the hash chain.
3. **Router calibration drift.** The difficulty heuristic is hand-tuned; at scale I'd log
   Verifier reject-rate per difficulty bucket and auto-tune the escalation boundary.

## CASE_ID
`CEDX-A0CF47` → `H=sha256(CASE_ID)`, `role = ROLES[int(H[0],16)%4] = legal_counsel`,
`threshold = 10000 + (int(H[1:3],16)%50)*1000 = 54000`. Computed at runtime in
`app/config.py`; printed at startup; recorded under `amendment` in `audit.json`.
