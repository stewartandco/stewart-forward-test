"""Prompt + served-model stamps for every LLM output (harness audit 2026-11-07, Tier 1 #4).

Every card, spec, review and screen decision recorded the model it ASKED for and the
pipeline version (never bumped since the first commit), but not the prompt it was
asked with nor the model the API actually SERVED. A prompt edit or a model alias
moving under the pipeline was therefore invisible in the record: a triage-quality
change could not be traced to its cause. Each call site now stamps:

  prompt_sha    16 hex of sha256 over the exact text/schema that shapes the answer
                (system prompt, structured-output schema, fixed user framing)
  served_model  message.model from the API response (falls back to the requested
                model only when the response carries none)

The per-prompt values are pinned in pipeline/test_prompt_stamps.py, so a prompt edit
fails a test until the pin is bumped deliberately.
"""
from __future__ import annotations

import hashlib
import json


def prompt_sha(*parts) -> str:
    # insertion order kept on purpose: the model emits structured-output fields in schema
    # order (reader.py EXTRACTION_SCHEMA relies on it), so a reorder must move the sha
    blob = json.dumps(list(parts), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def served_model(message, requested: str) -> str:
    m = getattr(message, "model", None)
    return m if isinstance(m, str) and m else requested
