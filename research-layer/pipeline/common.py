"""Shared primitives for the research layer: canonical JSON, hashing, content ids.

Must stay byte-compatible with the root verify.py and research-layer/verify_registry.py.
"""
from __future__ import annotations

import copy
import json
import hashlib

GENESIS_HASH = "0" * 64


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def entry_hash(entry: dict) -> str:
    return hashlib.sha256(canonical_json(entry).encode("utf-8")).hexdigest()


def content_id(obj: dict, id_field: str) -> str:
    """First 16 hex chars of the SHA-256 of the object minus its own id field."""
    o = copy.deepcopy(obj)
    o.pop(id_field, None)
    return entry_hash(o)[:16]


def normalize_ws(text: str) -> str:
    """Collapse all whitespace runs to single spaces for quote matching."""
    return " ".join(text.split())


def quote_in_source(quote: str, source_text: str) -> bool:
    """The honesty guard: the quote must appear verbatim in the source,
    tolerating only whitespace differences (line wraps, PDF extraction)."""
    return normalize_ws(quote).casefold() in normalize_ws(source_text).casefold()
