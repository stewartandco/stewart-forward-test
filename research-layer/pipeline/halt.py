"""D35 pipeline kill switch: the `logs/pipeline.halted` artifact.

Contract (stewartandco-agents hubs/intelligence/agents/pipeline/CONTRACT.md s11):
the pipeline reads `research-layer/logs/pipeline.halted` before each stage and
between batches and stops on its PRESENCE alone, touching no credential. It is
Coen's artifact: code here only ever reads it, never writes or deletes it.
Lifting a halt is Coen deleting the file (Tier 3).

Readers: pipeline.loop (run start + before every stage subprocess),
pipeline.gauntlet_worker (run start + between chunks), pipeline.livegate (run
start). A halted run exits 0 with outcome/exit_reason `halted`: a halt is
Coen's deliberate act, not a defect, so the scheduled tasks must not retry it.

Until 2026-11-06 the contract carried "the halt-artifact check is not yet in the
code" (since 2026-08-17); harness audit 2026-11-06, Tier 1 #3.
"""
from __future__ import annotations

from pathlib import Path

HALT_NAME = "pipeline.halted"


class PipelineHalted(Exception):
    """Raised between stages when the halt artifact appears mid-run."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def halt_path(logs_dir: str | Path) -> Path:
    return Path(logs_dir) / HALT_NAME


def halt_reason(logs_dir: str | Path) -> str | None:
    """None when the artifact is absent; otherwise its first line (maybe '').

    Presence alone halts: an empty or unreadable file still halts. Read
    BOM-safe so a flag saved from Notepad/PowerShell still shows its reason.
    """
    p = halt_path(logs_dir)
    if not p.exists():
        return None
    try:
        text = p.read_text(encoding="utf-8-sig", errors="replace").strip()
    except OSError:
        return "(halt artifact present, unreadable)"
    first = text.splitlines()[0] if text else ""
    return first[:200]


def check(logs_dir: str | Path) -> None:
    """Raise PipelineHalted when the artifact is present."""
    reason = halt_reason(logs_dir)
    if reason is not None:
        raise PipelineHalted(reason)


def describe(reason: str) -> str:
    return f"halted by {HALT_NAME}" + (f": {reason}" if reason else "")
