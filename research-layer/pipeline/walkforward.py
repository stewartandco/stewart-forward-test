"""Purged walk-forward, used as CORROBORATION only.

The literature this pipeline follows is explicit that walk-forward is the worst
out-of-sample scheme for preventing false discoveries and that CSCV is the
primary tool (see pipeline/pbo.py). It is computed here because the SOP asks
for it as a corroborating view, never as a selector.

Purging removes train bars adjacent to each test slice so an indicator's
lookback cannot straddle the boundary. The gap must be at least the longest
lookback in the grammar, which is 200 (ma_cross.slow, regime_ma.ma_len).
"""
from __future__ import annotations

RUIN_LEVEL = 0.5   # same constant the gauntlet's p_ruin gate uses


def purged_folds(dates: list[str], n_folds: int,
                 purge_bars: int) -> list[dict]:
    """Contiguous test slices with a purge gap carved out of train on both
    sides. Returns [{'test': [...], 'train': [...]}]."""
    n = len(dates)
    size = n // n_folds
    folds = []
    for k in range(n_folds):
        lo = k * size
        hi = n if k == n_folds - 1 else (k + 1) * size
        test = dates[lo:hi]
        keep_lo = max(0, lo - purge_bars)
        keep_hi = min(n, hi + purge_bars)
        train = dates[:keep_lo] + dates[keep_hi:]
        folds.append({"test": test, "train": train})
    return folds


def walkforward_report(trades: list[dict], dates: list[str], n_folds: int = 3,
                       purge_bars: int = 200) -> dict:
    """Per-fold net contribution and worst equity, plus the SOP's two summary
    flags. Both flags are RECORDED; neither gates under protocol-v4."""
    folds = purged_folds(dates, n_folds, purge_bars)
    out = []
    catastrophic = False
    for f in folds:
        window = set(f["test"])
        picked = [t for t in trades if t["entry_date"] in window]
        equity, min_equity, net = 1.0, 1.0, 0.0
        for t in picked:
            c = t["return_net"] * t.get("notional_frac", 1.0)
            net += c
            equity *= (1.0 + c)
            min_equity = min(min_equity, equity)
        if min_equity < RUIN_LEVEL:
            catastrophic = True
        out.append({"n_trades": len(picked), "net": net,
                    "min_equity": min_equity})
    positive = sum(1 for f in out if f["net"] > 0)
    return {"folds": out, "folds_positive": positive,
            "majority_pass": positive >= (n_folds // 2 + 1),
            "catastrophic": catastrophic,
            "purge_bars": purge_bars, "n_folds": n_folds}
