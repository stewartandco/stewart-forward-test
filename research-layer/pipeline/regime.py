"""The protocol's single declared regime ruler.

One ruler for the whole protocol, declared before any verdict, so a
regime-conditional report means the same thing across every strategy: BTC close
versus its 200-day moving average, with a band around the average that counts
as chop rather than a weak trend.

Recorded, not gating. The SOP asks for the split so that a strategy whose
losses cluster in one regime is visible at the gate.
"""
from __future__ import annotations

CHOP_BAND = 0.05
BUCKETS = ("trend_up", "trend_down", "chop", "unlabelled")


def regime_by_date(bars: list[dict], ma_len: int = 200) -> dict[str, str]:
    """date -> regime label. Bars before the average exists are omitted
    entirely rather than given a fabricated label."""
    labels = {}
    closes = [b["close"] for b in bars]
    running = 0.0
    for i, b in enumerate(bars):
        running += closes[i]
        if i >= ma_len:
            running -= closes[i - ma_len]
        if i < ma_len - 1:
            continue
        ma = running / ma_len
        if ma <= 0:
            continue
        spread = (closes[i] - ma) / ma
        if abs(spread) <= CHOP_BAND:
            labels[b["date"]] = "chop"
        else:
            labels[b["date"]] = "trend_up" if spread > 0 else "trend_down"
    return labels


def regime_split(trades: list[dict], labels: dict[str, str]) -> dict:
    """Per-bucket trade count, net contribution and win rate."""
    out = {b: {"n": 0, "net": 0.0, "wins": 0} for b in BUCKETS}
    for t in trades:
        bucket = labels.get(t["entry_date"], "unlabelled")
        c = t["return_net"] * t.get("notional_frac", 1.0)
        out[bucket]["n"] += 1
        out[bucket]["net"] += c
        out[bucket]["wins"] += 1 if c > 0 else 0
    for b in out.values():
        b["win_rate"] = (b["wins"] / b["n"]) if b["n"] else None
    return out
