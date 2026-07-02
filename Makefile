# Uniform probe interface — graders invoke THESE targets identically on every repo,
# whatever language you build in. Each is a thin wrapper over `python3 -m app <cmd>`.
# Exit codes matter.
SEED_DIR ?= seed
PY ?= python3

.PHONY: demo verify trace eval replay record probe-approval probe-agent-failure \
        probe-budget probe-append-only probe-idempotency probe-crash clean

# Full multi-agent pipeline, offline replay, on $(SEED_DIR). Writes out/<package>,
# out/audit.json (agents roster + per-record agent_trace + cost), out/exception_queue.json.
demo:
	REPLAY_LLM=true SEED_DIR=$(SEED_DIR) $(PY) -m app demo

# Run the PROVIDED gate on your audit bundle. Do not modify verify_audit.py.
verify:
	$(PY) verify_audit.py --audit out/audit.json --transcripts transcripts --schema audit.schema.json

# Print one record's FULL agent decision path from the log alone.
trace:
	$(PY) -m app trace $(ID)

# Agent eval harness: >=10 golden cases + an LLM-judge. Prints per-agent scores.
eval:
	$(PY) -m app eval

# Reconstruct one delivered output's DATA lineage from the append-only log alone.
replay:
	$(PY) -m app replay $(ID)

# (Re)author the offline replay transcripts. Set REPLAY_LLM=false + LLM_API_KEY to
# record against a real model instead of the reference fixtures.
record:
	$(PY) -m app record

probe-approval:
	$(PY) -m app probe-approval

probe-agent-failure:
	$(PY) -m app probe-agent-failure

probe-budget:
	$(PY) -m app probe-budget

probe-append-only:
	$(PY) -m app probe-append-only

probe-idempotency:
	$(PY) -m app probe-idempotency

# BONUS. Resume from the last completed stage after a crash.
probe-crash:
	$(PY) -m app probe-crash

clean:
	rm -rf out
