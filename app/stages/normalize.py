"""Stage 2a — Declarative normalization.

The mapping from source aliases to canonical fields lives in schema/field_mapping.json
(DATA, not code). A field rename in the held-out seed is absorbed by adding an alias
there — the pipeline code never changes. When a record supplies a non-primary alias
(e.g. `value` instead of `amount`) we still map it, but we record SCHEMA_DRIFT so the
rename is auditable.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..contracts import NormalizedRecord, RawRecord


class Normalizer:
    def __init__(self, schema_dir: Path):
        self.mapping = json.loads((schema_dir / "field_mapping.json").read_text(encoding="utf-8"))
        out_schema = json.loads((schema_dir / "output_schema.v1.json").read_text(encoding="utf-8"))
        self.schema_version = out_schema["schema_version"]

    def normalize(self, raw: RawRecord) -> NormalizedRecord:
        canonical = self.mapping["canonical"]
        primary = self.mapping["primary_alias"]
        fields = raw.fields
        resolved: dict = {}
        drift_notes: list[str] = []

        for cname, aliases in canonical.items():
            found_alias = None
            for alias in aliases:
                if alias in fields and fields[alias] is not None:
                    found_alias = alias
                    resolved[cname] = fields[alias]
                    break
            # If the value came from a non-primary alias, that is a schema drift.
            if found_alias is not None and found_alias != primary[cname]:
                drift_notes.append(f"{cname}<-{found_alias}")
            if cname not in resolved:
                resolved[cname] = None

        amount = resolved.get("amount")
        try:
            amount = float(amount) if amount is not None else None
        except (TypeError, ValueError):
            amount = None

        return NormalizedRecord(
            id=raw.id,
            schema_version=self.schema_version,
            source_format=raw.source_format,
            source_version_hash=raw.source_version_hash,
            version=raw.version,
            owner=resolved.get("owner"),
            deadline=str(resolved["deadline"]) if resolved.get("deadline") else None,
            amount=amount,
            category=str(resolved["category"]) if resolved.get("category") else None,
            notes=str(resolved.get("notes") or ""),
            drift_notes=drift_notes,
        )
