"""Append-only audit log with a hash chain (tamper-evidence).

Events are only ever appended; each event carries `prev`, the hash of the previous
event, so mutating or deleting any past entry breaks the chain and is detectable
(verify_chain). This is what `make probe-append-only` exercises. `seq` is a strict
0..n-1 sequence (verify_audit.py check #9).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .util import sha


class AppendOnlyViolation(RuntimeError):
    pass


class RunClock:
    """Deterministic clock so a re-run produces a byte-identical audit (idempotency)."""
    def __init__(self, base: datetime):
        self._t = base

    def tick(self) -> str:
        self._t = self._t + timedelta(seconds=1)
        return self._t.isoformat()


class AuditLog:
    def __init__(self, clock: RunClock):
        self.clock = clock
        self.events: list[dict] = []

    def append(self, actor: str, action: str, record_id: str | None = None) -> dict:
        prev = self.events[-1]["_hash"] if self.events else "sha256:" + "0" * 64
        ev = {
            "seq": len(self.events),
            "ts": self.clock.tick(),
            "actor": actor,
            "action": action,
            "record_id": record_id,
            "prev": prev,
        }
        ev["_hash"] = sha({k: v for k, v in ev.items() if k != "_hash"})
        self.events.append(ev)
        return ev

    def public_events(self) -> list[dict]:
        # `_hash` is working state; keep `prev` (schema allows extra props) so the
        # chain remains verifiable from the emitted audit.json.
        return [{k: v for k, v in e.items() if k != "_hash"} for e in self.events]

    @staticmethod
    def verify_chain(events: list[dict]) -> bool:
        """Recompute the chain over emitted events. Any tamper -> False."""
        prev = "sha256:" + "0" * 64
        for i, e in enumerate(events):
            if e.get("seq") != i:
                return False
            if e.get("prev") != prev:
                return False
            recomputed = sha({k: v for k, v in e.items() if k not in ("_hash",)})
            prev = recomputed
        return True
