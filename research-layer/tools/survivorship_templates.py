"""Strategy templates and template-cell specs read from a frozen COPY of the
registry (off-chain survivorship study). Never reads the live chain."""
from __future__ import annotations

import copy as _copy
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from pipeline.common import content_id
from pipeline.composer import _build_name

_LAYER = Path(__file__).resolve().parent.parent


class LiveChainRefused(RuntimeError):
    pass


def _norm(p: Path) -> str:
    return str(Path(p).resolve()).lower()


def refuse_live(registry: Path) -> None:
    registry = Path(registry)
    if (registry.resolve().parent / "registry_log.d").is_dir():
        raise LiveChainRefused(f"{registry}: directory holds registry_log.d (live chain)")
    if _norm(registry) == _norm(_LAYER / "registry_log.jsonl"):
        raise LiveChainRefused(f"{registry} is the live research-layer chain")


@dataclass
class Copy:
    specs: dict = field(default_factory=dict)
    screen: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    n_entries: int = 0


def read_copy(registry: Path, upto: int | None) -> Copy:
    out = Copy()
    with open(registry, "r", encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            if not line.strip():
                continue
            out.n_entries = i + 1
            e = json.loads(line)
            et, p = e.get("entry_type"), e.get("payload", {})
            if et == "note":
                out.notes.append((i, p.get("text", "")))
                continue
            if upto is not None and i >= upto:
                continue
            if et == "strategy_registered":
                u = p.get("universe", {})
                if u.get("asset_class") == "crypto" and len(u.get("assets", [])) == 1:
                    out.specs[p["strategy_id"]] = p
            elif et == "verdict" and p.get("stage") == "screened":
                out.screen[p["strategy_id"]] = p
    return out


def _tkey(blocks) -> str:
    return hashlib.sha256(
        json.dumps(blocks, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def templates(copy: Copy, tf: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for sid, s in copy.specs.items():
        if s["universe"].get("timeframe", "1d") == tf:
            out.setdefault(_tkey(s["blocks"]), []).append(sid)
    return {k: sorted(v) for k, v in out.items()}


def cell_spec(copy: Copy, tkey: str, sids: list[str], pair: str, tf: str) -> dict:
    for sid in sorted(sids):
        if copy.specs[sid]["universe"]["assets"] == [pair]:
            return _copy.deepcopy(copy.specs[sid])
    spec = _copy.deepcopy(copy.specs[min(sids)])
    spec["universe"]["assets"] = [pair]
    spec["name"] = _build_name([pair], spec["family"], spec["blocks"], tf)
    spec["strategy_id"] = content_id(spec, "strategy_id")
    return spec
