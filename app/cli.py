"""Thin CLI behind the Makefile probe interface: `python -m app <cmd> [ID]`."""
from __future__ import annotations

import json
import sys

from .config import Config


def _load_audit(cfg: Config) -> dict:
    p = cfg.out_dir / "audit.json"
    if not p.exists():
        print("no out/audit.json — run `make demo` first")
        sys.exit(1)
    return json.loads(p.read_text(encoding="utf-8"))


def cmd_trace(cfg: Config, rid: str) -> int:
    """Full agent decision path for one record, reconstructed from the log alone."""
    audit = _load_audit(cfg)
    rec = next((r for r in audit["records"] if r["id"] == rid), None)
    if rec is None:
        print(f"record {rid} not found")
        return 1
    print(f"=== TRACE {rid} === status={rec['status']} "
          f"reason={rec.get('reason_code')} class={rec.get('reason_class')}")
    print(f"amendment: {audit['amendment']['role']} @ {audit['amendment']['threshold']}")
    print("\nagent_trace (ordered spans):")
    for i, s in enumerate(rec.get("agent_trace", [])):
        bits = [f"#{i}", s["agent"], f"status={s['status']}"]
        if s.get("model"):
            bits.append(f"model={s['model']}")
        if s.get("verdict"):
            bits.append(f"verdict={s['verdict']}")
        if s.get("cost_usd") is not None:
            bits.append(f"cost=${s['cost_usd']:.6f}")
        if s.get("retries"):
            bits.append(f"retries={s['retries']}")
        if s.get("transcript_hash"):
            bits.append(f"tx={s['transcript_hash'][:20]}..")
        print("  " + "  ".join(bits))
        if s.get("note"):
            print(f"       note: {s['note']}")
    print("\napproval_trail:")
    for t in rec.get("approval_trail", []):
        print(f"  {t['state']:18} by {t['actor']:14} @ {t['ts']}"
              + (f"  ({t['reason']})" if t.get("reason") else ""))
    if rec.get("delivered_fields"):
        print("\ndelivered_fields:")
        print("  " + json.dumps(rec["delivered_fields"], ensure_ascii=False))
    return 0


def cmd_replay(cfg: Config, rid: str) -> int:
    """Reconstruct one output's DATA lineage from the append-only log alone."""
    audit = _load_audit(cfg)
    rec = next((r for r in audit["records"] if r["id"] == rid), None)
    if rec is None:
        print(f"record {rid} not found")
        return 1
    print(f"=== LINEAGE {rid} ===")
    print(f"source_format      : {rec['source_format']}")
    print(f"source_version_hash: {rec.get('source_version_hash')}")
    print(f"status             : {rec['status']}  reason={rec.get('reason_code')}")
    print(f"worker transcript  : {rec.get('transcript_hash')}")
    print(f"delivered_fields#  : {rec.get('delivered_fields_hash')}")
    print("events touching this record (append-only log):")
    for e in audit["events"]:
        if e.get("record_id") == rid:
            print(f"  seq={e['seq']:>3} {e['ts']} {e['actor']:14} {e['action']}")
    return 0
