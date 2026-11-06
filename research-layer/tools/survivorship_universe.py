"""Point-in-time universe, declared renames/delistings/cuts, and strata for the
crypto survivorship study (design doc 2026-11-06, SV9 and s1.2).

Study-wide constants live here; other tasks import them. Pure: no network.
`spans` maps a Binance pair to (first_bar_date, last_bar_date) as YYYY-MM-DD
strings, or None when no pair exists.
"""
from __future__ import annotations

import json
from pathlib import Path

from tools_select_crypto_universe import classify_exclusion

CUTOFF = "2023-12-31"
END = "2026-10-31"
HISTORY_FLOOR = "2022-12-31"   # first daily bar must be on or before this
ACTIVE_FROM = "2023-12-24"     # last daily bar must be on or after this
TARGET = 100
STRATA = ("S", "R", "F", "N", "D")

CMC_URL = ("https://api.coinmarketcap.com/data-api/v3/cryptocurrency/listings/"
           "historical?convertId=2781&date=2023-12-31&limit=200&start=1")

SYMBOL_MAP = {"ETHDYDX": "DYDX"}

# predecessor pair -> (successor pair, swap date, ratio successor per
# predecessor unit, announcement URL). Dates/URLs are filled from Binance
# announcements at G2; `freeze` (Task 7) refuses any None.
RENAMES: dict[str, tuple] = {
    "MATICUSDT": ("POLUSDT", None, 1.0, None),
    "RNDRUSDT": ("RENDERUSDT", None, 1.0, None),
    "FTMUSDT": ("SUSDT", None, 1.0, None),
    "MKRUSDT": ("SKYUSDT", None, 24000.0, None),
    "EOSUSDT": ("AUSDT", None, 1.0, None),
    "KLAYUSDT": ("KAIAUSDT", None, 1.0, None),
    "FXSUSDT": ("FRAXUSDT", None, 1.0, None),
}

# pair -> announcement URL (filled at G2)
DELISTED: dict[str, str | None] = {
    "XMRUSDT": None, "XEMUSDT": None, "ELFUSDT": None,
    "KDAUSDT": None, "LRCUSDT": None,
}

# pair -> first usable date (confirmed at G2)
CUTS: dict[str, str] = {"LUNAUSDT": "2022-05-31"}


def pair_for(symbol: str) -> str:
    return SYMBOL_MAP.get(symbol, symbol).upper() + "USDT"


def declared_assets() -> list[str]:
    from pipeline.cells import ACTIVE_CELLS
    return list(ACTIVE_CELLS["crypto"]["assets"])


STABLE_PROBES = ("USDT", "USDC")


def load_cmc(path: Path) -> list[dict]:
    """Rows {"rank","symbol","slug","stable","price"} from a CMC listings
    response, in rank order. Price is the USD quote (else the first quote,
    else None). Raises ValueError when no row carries tags, or when USDT or
    USDC is not tagged `stablecoin` (final review M-d): the walk's stablecoin
    rule reads the tags, so a response without them would admit stablecoins."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))["data"]
    if not any(r.get("tags") for r in data):
        raise ValueError(f"{path}: no row carries CMC tags; the stablecoin rule "
                         f"cannot be applied")
    out = []
    for r in data:
        quotes = r.get("quotes") or []
        q = next((x for x in quotes if x.get("name") == "USD"),
                 quotes[0] if quotes else None)
        out.append({
            "rank": int(r["cmcRank"]),
            "symbol": r["symbol"],
            "slug": r["slug"],
            "stable": "stablecoin" in (r.get("tags") or []),
            "price": None if q is None else q.get("price"),
        })
    out.sort(key=lambda x: x["rank"])
    for sym in STABLE_PROBES:
        if not any(r["symbol"] == sym and r["stable"] for r in out):
            raise ValueError(f"{path}: {sym} is missing or not tagged stablecoin; "
                             f"the listing's tags cannot be trusted")
    return out


def span_for(pair: str, spans: dict, cuts: dict | None = None):
    """The pair's (first, last) span with CUTS applied to the first date, or
    None when no pair exists."""
    cuts = CUTS if cuts is None else cuts
    sp = spans.get(pair)
    if sp is None:
        return None
    first, last = sp
    if pair in cuts:
        first = cuts[pair]
    return (first, last)


def pit_walk(cmc: list[dict], spans: dict, target: int = TARGET,
             cuts: dict | None = None) -> tuple[list[str], list[dict]]:
    """Walk CMC ranks ascending until `target` pairs are admitted. Admission
    order: stable/classifier, no pair, first bar > HISTORY_FLOOR, last bar <
    ACTIVE_FROM, else admit. Raises RuntimeError on a duplicate pair among
    survivors (the selector's rule). Returns (admitted pairs, excluded rows)."""
    admitted: list[str] = []
    excluded: list[dict] = []
    seen: dict[str, dict] = {}
    for row in sorted(cmc, key=lambda r: r["rank"]):
        if len(admitted) >= target:
            break
        pair = pair_for(row["symbol"])

        def drop(reason):
            excluded.append({"rank": row["rank"], "symbol": row["symbol"],
                             "pair": pair, "reason": reason})

        if row["stable"] or classify_exclusion({
                "id": row["slug"], "symbol": row["symbol"].lower(),
                "current_price": row["price"]}) is not None:
            drop("stable")
            continue
        sp = span_for(pair, spans, cuts)
        if sp is None:
            drop("no_pair")
            continue
        if sp[0] > HISTORY_FLOOR:
            drop("history")
            continue
        if sp[1] < ACTIVE_FROM:
            drop("inactive")
            continue
        if pair in seen:
            raise RuntimeError(
                f"duplicate Binance pair {pair}: rank {seen[pair]['rank']} "
                f"({seen[pair]['slug']}) and rank {row['rank']} ({row['slug']})")
        seen[pair] = row
        admitted.append(pair)
    return admitted, excluded


def strata(A: list[str], B: list[str]) -> dict[str, str]:
    """pair -> stratum. S = A and B; R = A only; of B only: N if a declared
    rename predecessor, D if declared delisted, else F."""
    a, b = set(A), set(B)
    out: dict[str, str] = {}
    for p in a & b:
        out[p] = "S"
    for p in a - b:
        out[p] = "R"
    for p in b - a:
        out[p] = "N" if p in RENAMES else "D" if p in DELISTED else "F"
    return out
