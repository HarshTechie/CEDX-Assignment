"""Canonical hashing + small shared helpers.

The canonicalisation here MUST match verify_audit.py byte-for-byte (sorted keys,
compact separators, ensure_ascii=False) so that hashes we write reproduce on the
grader's side.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def canon(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def sha(obj: Any) -> str:
    """sha256 over the canonical JSON encoding, prefixed like verify_audit.py."""
    return "sha256:" + hashlib.sha256(canon(obj)).hexdigest()


def sha_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def hexstem(prefixed: str) -> str:
    """'sha256:abcd...' -> 'abcd...' (the transcript filename stem)."""
    return prefixed.split(":")[-1]
