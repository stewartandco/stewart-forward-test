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
# R4, 2026-11-08: swap date = the successor's first daily bar in the archive;
# every date and ratio matches the Binance announcement (old pair removed at
# 03:00 UTC on the old pair's last archive day, new pair opened on the
# successor's first archive day).
RENAMES: dict[str, tuple] = {
    "MATICUSDT": ("POLUSDT", "2024-09-13", 1.0,
                  "https://www.binance.com/en/support/articles/6a6de383727f4659a3050f7982e1620f"),
    "RNDRUSDT": ("RENDERUSDT", "2024-07-26", 1.0,
                 "https://www.binance.com/en/support/announcement/d1f2ae8d99b24439a7a900caa9bb6b3b"),
    "FTMUSDT": ("SUSDT", "2025-01-16", 1.0,
                "https://www.binance.com/en/support/announcement/aec6fcbc84b749eeab6690e6bcac2f3d"),
    "MKRUSDT": ("SKYUSDT", "2025-09-17", 24000.0,
                "https://www.binance.com/en/support/announcement/detail/bf29506a33a94c17a5d5a01e74e646d2"),
    "EOSUSDT": ("AUSDT", "2025-05-28", 1.0,
                "https://www.binance.com/en/support/announcement/1e89a9ca957c4b0ca7502e60b993e201"),
    "KLAYUSDT": ("KAIAUSDT", "2024-10-31", 1.0,
                 "https://www.binance.com/en/support/articles/f75f933759ee49d0af1dfbce7e32144c"),
    "FXSUSDT": ("FRAXUSDT", "2026-01-15", 1.0,
                "https://www.binance.com/en/support/announcement/detail/a17352ba70414c96882cd881591dd36d"),
}

# pair -> Binance delisting announcement (R4, 2026-11-08); each a genuine
# delisting with no successor token; the date matches the archive's last bar.
DELISTED: dict[str, str | None] = {
    "XMRUSDT": "https://www.binance.com/en/support/announcement/f73b083ba6834771b07dbe5319917ae5",
    "XEMUSDT": "https://www.binance.com/en/support/announcement/binance-will-delist-omg-waves-wnxm-xem-on-2024-06-17-f65faeeefe07417a8b6dd2900ba8da7e",
    "ELFUSDT": "https://www.binance.com/en/support/articles/fac9c3e401da4cc8b604566fd261d70c",
    "KDAUSDT": "https://www.binance.com/en/support/announcement/detail/a6c810fc36424548917e146dbe21469a",
    "LRCUSDT": "https://www.binance.com/en/support/announcement/detail/ddac99c0e5044c13a13c7c113e530e2b",
}

# pair -> first usable date. LUNAUSDT carries Terra Classic until its
# 2022-05-13 bar, then Terra 2.0 from 2022-05-31 (Binance listing announcement
# binance-will-list-terra-2-0-luna-in-the-innovation-zone-luna-old-renamed-as-lunc-d044a6742e484b77a170111460b0eed3);
# confirmed in the archive (18-day gap, then a new price series). R4, 2026-11-08.
CUTS: dict[str, str] = {"LUNAUSDT": "2022-05-31"}

# pair -> (last bar date before the halt, first bar date after it): a declared
# trading halt that P4's gap check (more than 7 days between daily bars) passes
# ONLY when both bar dates match exactly. FTTUSDT halted 311 days: last bar
# 2022-11-15 (close 1.4322), next bar 2023-09-22 (open 0.9752), the FTX collapse
# (Binance archive, confirmed in the preflight_1d run of 2026-11-08). Coen
# 2026-11-08: "Keep FTT, declare the halt". The x10 close-jump check still
# applies across it. Hashed into the manifest like CUTS/EXCLUDED and listed in
# the note.
HALTS: dict[str, tuple[str, str]] = {"FTTUSDT": ("2022-11-15", "2023-09-22")}

# pair -> reason: a declared exclusion (design P4; Coen's call, declared BEFORE
# the pre-registration note). The walk records the pair as excluded and it
# takes NO slot: the walk continues to TARGET. Hashed into the manifest like
# RENAMES/DELISTED/CUTS/HALTS and listed in the note. A change after the note needs
# a superseding chained note AND a code change (design s4 step 3).
EXCLUDED: dict[str, str] = {}


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
             cuts: dict | None = None,
             excluded_pairs: dict | None = None) -> tuple[list[str], list[dict]]:
    """Walk CMC ranks ascending until `target` pairs are admitted. Admission
    order: stable/classifier, declared exclusion (EXCLUDED), no pair, first
    bar > HISTORY_FLOOR, last bar < ACTIVE_FROM, else admit. An excluded row
    takes no slot. Raises RuntimeError on a duplicate pair among survivors
    (the selector's rule). Returns (admitted pairs, excluded rows)."""
    excluded_pairs = EXCLUDED if excluded_pairs is None else excluded_pairs
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
        if pair in excluded_pairs:
            drop(f"declared exclusion: {excluded_pairs[pair]}")
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
