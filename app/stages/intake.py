"""Stage 1 — Intake. Parse BOTH formats (feed.json + inbox .eml/.pdf), persist each
raw record keyed by (id, source_version_hash). No hardcoded in-memory arrays: the
downstream stages read from the store.
"""
from __future__ import annotations

import email
import json
import re
from pathlib import Path

from ..contracts import RawRecord
from ..store import JsonStore
from ..util import sha

# Lines look like "Key: value" in both .eml bodies and the PDF text layer.
_KV = re.compile(r"^\s*([A-Za-z][A-Za-z _]*?)\s*:\s*(.*?)\s*$")


def _coerce(v: str):
    s = v.strip()
    if s == "" or s.lower() in {"null", "none", "n/a"}:
        return None
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if re.fullmatch(r"-?\d+\.\d+", s):
        return float(s)
    return s


def _parse_kv_block(text: str) -> dict:
    """Parse a 'Key: value' block into a dict with lowercased keys. Keys are kept
    verbatim (lowercased) so normalization can detect which *alias* was used."""
    fields: dict = {}
    for line in text.splitlines():
        m = _KV.match(line)
        if not m:
            continue
        key = m.group(1).strip().lower().replace(" ", "_")
        fields[key] = _coerce(m.group(2))
    return fields


def _raw_from_fields(fields: dict, source_format: str) -> RawRecord:
    rid = str(fields.get("id") or fields.get("record") or fields.get("ref") or "UNKNOWN")
    version = fields.get("version") or fields.get("ver") or 1
    try:
        version = int(version)
    except (TypeError, ValueError):
        version = 1
    return RawRecord(
        id=rid,
        source_format=source_format,  # type: ignore[arg-type]
        source_version_hash=sha(fields),
        version=version,
        fields=fields,
    )


def parse_feed(path: Path) -> list[RawRecord]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for row in rows:
        fields = {str(k).lower(): v for k, v in row.items()}
        out.append(_raw_from_fields(fields, "feed"))
    return out


def parse_eml(path: Path) -> RawRecord:
    msg = email.message_from_string(path.read_text(encoding="utf-8"))
    body = msg.get_payload()
    if isinstance(body, list):  # multipart — take first text part
        body = body[0].get_payload()
    return _raw_from_fields(_parse_kv_block(body or ""), "eml")


def parse_pdf(path: Path) -> RawRecord:
    from pypdf import PdfReader
    text = "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)
    return _raw_from_fields(_parse_kv_block(text), "pdf")


def run_intake(seed_dir: Path, store: JsonStore) -> list[RawRecord]:
    records: list[RawRecord] = []
    feed = seed_dir / "feed.json"
    if feed.exists():
        records.extend(parse_feed(feed))
    inbox = seed_dir / "inbox"
    if inbox.exists():
        for f in sorted(inbox.iterdir()):
            if f.suffix.lower() == ".eml":
                records.append(parse_eml(f))
            elif f.suffix.lower() == ".pdf":
                records.append(parse_pdf(f))
    # Persist each raw record. Key includes the source hash so a byte-identical
    # re-ingest is a no-op (idempotency).
    for r in records:
        store.put(f"{r.id}__{r.source_version_hash[7:19]}", r.model_dump())
    return records
