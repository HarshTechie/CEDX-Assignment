"""Tiny durable JSON store.

Intake persists every raw record here (the brief forbids hardcoded in-memory
arrays), and the store doubles as the idempotency + resumability substrate: the
key is (id, source_version_hash), so re-ingesting the identical source is a no-op.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator


class JsonStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        safe = key.replace("/", "_").replace(":", "_")
        return self.root / f"{safe}.json"

    def put(self, key: str, doc: dict) -> bool:
        """Write doc. Returns True if new, False if an identical doc already existed
        (the idempotency signal)."""
        p = self._path(key)
        payload = json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False)
        if p.exists() and p.read_text(encoding="utf-8") == payload:
            return False
        p.write_text(payload, encoding="utf-8")
        return True

    def get(self, key: str) -> dict | None:
        p = self._path(key)
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def all(self) -> Iterator[dict]:
        for p in sorted(self.root.glob("*.json")):
            yield json.loads(p.read_text(encoding="utf-8"))
