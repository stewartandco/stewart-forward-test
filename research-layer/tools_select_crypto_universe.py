"""One-time crypto universe selection (SP5 D1). Declared rule, pinned output.
Writes exactly one new file, data/crypto_universe_manifest.json, and REFUSES
to overwrite it (re-selection is a new declared event).
HONESTY LIMIT (carried into the manifest's "rule" text): today's top-100 is
selected by today's outcomes - survivorship bias is documented, not corrected;
forward quarantine is the honest arbiter."""

import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

STABLECOIN_IDS = frozenset({"tether","usd-coin","dai","binance-usd","true-usd",
    "first-digital-usd","ethena-usde","usds","paypal-usd","frax","usdd",
    "gemini-dollar","paxos-standard","liquity-usd","susds","usual-usd",
    # T7 re-selection finding (2026-08-28): two DEPEGGED USD stables (USDa
    # $0.967, USDA $0.964) sat outside the 2% peg band and collided on the
    # ticker USDA, tripping the duplicate guard. The peg heuristic stays
    # narrow on purpose; depegged stables get declared entries instead.
    "usda-2","usda-3"})
WRAPPED_MARKERS = ("wrapped","staked","bridged","restaked","wbeth","wsteth",
    "steth","reth","cbeth","cbbtc","weeth","rseth","ezeth","oseth","lseth",
    "tbtc","solvbtc")
PEG_BAND = 0.02

LAYER_ROOT = Path(__file__).resolve().parent
HISTORY_DAYS = 730
# A pair must have printed a daily bar within this many days of selection.
# T7 live finding (2026-08-28): four DELISTED pairs passed the >=2y-history
# rule - BTTUSDT (old BTT denomination), DAIUSDT (CoinGecko id
# "dai-on-pulsechain" colliding with real DAI's dead pair), LITUSDT (id
# "lighter" colliding with delisted Litentry), XMRUSDT (Monero delisted
# 2024-02). Two are ticker COLLISIONS that would map the wrong asset's
# history; all four would break crypto's same-day alignment forever.
ACTIVE_WITHIN_DAYS = 7
TARGET_SIZE = 100
INCUMBENTS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT")

RULE = (
    "Top-100 crypto assets by CoinGecko market cap on the selection date, walked in "
    "ascending market-cap-rank order. Excluded, with each exclusion recorded and "
    "reasoned: declared stablecoins (fixed id list), wrapped/staked/bridged/restaked "
    "derivatives (marker substrings of the CoinGecko id or symbol), and undeclared "
    "USD-peg suspects (price within {peg} of 1.0 with 'usd' in the id or symbol). "
    "Admission additionally requires a Binance USDT spot pair with at least {days} "
    "days ({years} years) of daily history at selection time, AND the pair must be "
    "actively trading: its latest daily bar must fall within {active} days of "
    "selection, which excludes delisted pairs and ticker collisions with dead "
    "pairs. HONESTY LIMIT: this "
    "universe is selected by today's outcomes - assets that died or fell out of the "
    "top-100 before today were never candidates, so any backtest over this universe "
    "inherits survivorship bias. That bias is documented here, not corrected; the "
    "forward quarantine period is the honest arbiter. Re-selection is a new declared "
    "event with its own manifest, never a silent refresh of this one."
).format(peg=PEG_BAND, days=HISTORY_DAYS, years=HISTORY_DAYS // 365,
         active=ACTIVE_WITHIN_DAYS)


class PinnedManifestError(RuntimeError):
    """Raised when a selection would overwrite an already-pinned manifest."""


def classify_exclusion(coin):
    """Return the declared exclusion reason for a CoinGecko coin dict, or None
    if the coin is admissible. Order is part of the declaration: declared
    stablecoin list first, then wrapped/staked markers (checked against BOTH
    the id and the symbol), then the USD-peg heuristic."""
    cid = str(coin.get("id", "")).lower()
    sym = str(coin.get("symbol", "")).lower()
    if cid in STABLECOIN_IDS:
        return "stablecoin (declared list)"
    for marker in WRAPPED_MARKERS:
        if marker in cid or marker in sym:
            return "wrapped/staked/bridged (pattern)"
    price = coin.get("current_price")
    if price is not None and abs(price - 1.0) < PEG_BAND and (
            "usd" in cid or "usd" in sym):
        return "stablecoin (peg heuristic)"
    return None


def _parse_utc(stamp):
    """Parse an ISO date/datetime string to an aware UTC datetime."""
    dt = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _admission_walk(coins, binance_meta, cutoff, active_cutoff):
    """The single admission walk both build_manifest and main()'s page-2
    decision use: ascending market_cap_rank, stop once TARGET_SIZE assets are
    admitted. binance_meta values are (first_1d_utc, last_1d_utc) spans, or
    None for no pair. Returns (admitted, excluded). Raises RuntimeError LOUDLY
    on a duplicate coin id or duplicate Binance symbol among survivors -
    silent dedupe would change the declared rule. Pure: no network, no
    filesystem."""
    admitted = []
    excluded = []
    seen_ids = set()
    seen_binance_symbols = set()
    for coin in sorted(coins, key=lambda c: c["market_cap_rank"]):
        if len(admitted) >= TARGET_SIZE:
            break
        rank = coin["market_cap_rank"]
        cid = coin["id"]
        sym = str(coin["symbol"])
        if cid in seen_ids:
            raise RuntimeError(
                "duplicate coin id %r in the candidate list (rank %s) - "
                "page drift or a source defect; investigate before pinning "
                "anything" % (cid, rank))
        seen_ids.add(cid)
        reason = classify_exclusion(coin)
        if reason is not None:
            excluded.append({"rank": rank, "id": cid, "symbol": sym,
                             "reason": reason})
            continue
        binance_symbol = sym.upper() + "USDT"
        if binance_symbol in seen_binance_symbols:
            raise RuntimeError(
                "duplicate Binance symbol %s (coin id %r, rank %s) - two "
                "distinct coins share a ticker and would map to the same "
                "USDT pair; investigate before pinning anything"
                % (binance_symbol, cid, rank))
        seen_binance_symbols.add(binance_symbol)
        span = binance_meta.get(binance_symbol)
        if span is None:
            excluded.append({"rank": rank, "id": cid, "symbol": sym,
                             "binance_symbol": binance_symbol,
                             "reason": "no Binance USDT spot pair"})
            continue
        first_1d, last_1d = span
        if _parse_utc(first_1d) > cutoff:
            excluded.append({"rank": rank, "id": cid, "symbol": sym,
                             "binance_symbol": binance_symbol,
                             "first_1d_utc": first_1d,
                             "reason": "history < 2y"})
            continue
        if _parse_utc(last_1d) < active_cutoff:
            excluded.append({"rank": rank, "id": cid, "symbol": sym,
                             "binance_symbol": binance_symbol,
                             "first_1d_utc": first_1d,
                             "last_1d_utc": last_1d,
                             "reason": "Binance pair inactive (delisted)"})
            continue
        admitted.append({"rank": rank, "id": cid, "symbol": sym,
                         "binance_symbol": binance_symbol,
                         "market_cap": coin.get("market_cap"),
                         "first_1d_utc": first_1d,
                         "last_1d_utc": last_1d})
    return admitted, excluded


def build_manifest(coins, binance_meta, now_utc):
    """Walk coins by ascending market_cap_rank, applying the declared rule,
    until TARGET_SIZE assets are admitted. Only coins actually walked appear
    in the manifest. Pure function: no network, no filesystem."""
    now = _parse_utc(now_utc)
    cutoff = now - timedelta(days=HISTORY_DAYS)
    active_cutoff = now - timedelta(days=ACTIVE_WITHIN_DAYS)
    admitted, excluded = _admission_walk(coins, binance_meta, cutoff,
                                         active_cutoff)
    if len(admitted) < TARGET_SIZE:
        raise RuntimeError(
            "under-full universe: only %d of %d admitted (short %d) - a "
            "manifest claiming a top-%d rule must not pin short; investigate "
            "before pinning anything"
            % (len(admitted), TARGET_SIZE, TARGET_SIZE - len(admitted),
               TARGET_SIZE))
    admitted_symbols = {a["binance_symbol"] for a in admitted}
    missing = [s for s in INCUMBENTS if s not in admitted_symbols]
    if missing:
        raise RuntimeError(
            "incumbent(s) not admitted: %s - the declared rule must admit all "
            "five incumbents; investigate before pinning anything"
            % ", ".join(missing))
    return {
        "selected_utc": now_utc,
        "rule": RULE,
        "source": ("coingecko /coins/markets keyless + "
                   "binance /api/v3/klines first/last-bar probe"),
        "admitted": admitted,
        "excluded": excluded,
    }


def write_manifest(manifest, path):
    """Write the pinned manifest. Refuses to overwrite: a re-selection is a
    new declared event, never a silent refresh."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Exclusive create: the existence check and the write are one atomic
        # operation, so a concurrent writer cannot slip between them.
        with path.open("x", encoding="utf-8") as fh:
            fh.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    except FileExistsError:
        raise PinnedManifestError(
            "%s already exists - the universe manifest is PINNED. Re-selection "
            "is a new declared event: write a new manifest, do not overwrite "
            "this one." % path) from None


# --------------------------------------------------------------------------
# Real-network path below. Run supervised, once. Tests never touch this.
# --------------------------------------------------------------------------

# Bare product/version ONLY: Binance's WAF 403s a UA carrying a parenthesized
# comment (reproduced deterministically 2026-08-28: with "(...)" -> 403,
# without -> 200, same URL, seconds apart). CoinGecko accepts either.
_UA = {"User-Agent": "stewart-research-layer/1.0"}
_COINGECKO_URL = ("https://api.coingecko.com/api/v3/coins/markets"
                  "?vs_currency=usd&order=market_cap_desc&per_page=250&page=%d")
# With startTime=0 klines returns the OLDEST daily bar; without startTime it
# returns the NEWEST. One request each = the pair's full (first, last) span.
_BINANCE_FIRST_URL = ("https://api.binance.com/api/v3/klines"
                      "?symbol=%s&interval=1d&limit=1&startTime=0")
_BINANCE_LAST_URL = ("https://api.binance.com/api/v3/klines"
                     "?symbol=%s&interval=1d&limit=1")


def _get_json(url):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _valid_binance_symbol(binance_symbol):
    """Binance spot symbols are strictly uppercase ASCII alphanumerics. A
    CoinGecko ticker outside that alphabet (unicode meme symbols exist in the
    real top-250; hit live 2026-08-28 as a UnicodeEncodeError in the URL)
    cannot be a Binance pair, so it is a no-pair exclusion WITHOUT a probe."""
    return bool(re.fullmatch(r"[A-Z0-9]+", binance_symbol))


def _kline_open_iso(klines):
    """ISO date of the single kline's open time, or None for an empty list."""
    if not klines:
        return None
    open_ms = klines[0][0]
    return datetime.fromtimestamp(open_ms / 1000.0, tz=timezone.utc).strftime(
        "%Y-%m-%d")


def _fetch_1d_span(binance_symbol):
    """(first_iso, last_iso) daily-bar span for a Binance symbol, or None
    when the pair does not exist (HTTP 400 or empty klines), or when the
    symbol is not even a valid Binance symbol shape (no probe sent). Two
    requests with 0.25s pacing between them; the latest bar is required by
    the active-trading rule (T7 delisted-pair finding)."""
    if not _valid_binance_symbol(binance_symbol):
        return None
    try:
        first_iso = _kline_open_iso(_get_json(_BINANCE_FIRST_URL % binance_symbol))
    except urllib.error.HTTPError as exc:
        if exc.code == 400:
            return None
        raise
    if first_iso is None:
        return None
    time.sleep(0.25)
    last_iso = _kline_open_iso(_get_json(_BINANCE_LAST_URL % binance_symbol))
    if last_iso is None:
        # A pair with a first bar but no latest bar is a source contradiction,
        # not a no-pair: stop loudly rather than guess.
        raise RuntimeError(
            "Binance returned a first 1d bar but no latest 1d bar for %s - "
            "inconsistent klines responses; investigate before pinning "
            "anything" % binance_symbol)
    return first_iso, last_iso


def main():
    out_path = LAYER_ROOT / "data" / "crypto_universe_manifest.json"
    if out_path.exists():
        raise PinnedManifestError(
            "%s already exists - refusing before any network call. "
            "Re-selection is a new declared event." % out_path)
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    coins = []
    binance_meta = {}

    def _probe_candidates():
        """Probe the Binance first/last-bar span for every not-yet-probed
        candidate that survives classify_exclusion."""
        for coin in sorted(coins, key=lambda c: c["market_cap_rank"]):
            if classify_exclusion(coin) is not None:
                continue
            binance_symbol = str(coin["symbol"]).upper() + "USDT"
            if binance_symbol in binance_meta:
                continue
            binance_meta[binance_symbol] = _fetch_1d_span(binance_symbol)
            time.sleep(0.25)

    def _admitted_count():
        """Count admissions via the SAME walk build_manifest uses (shared
        helper, so the two can never drift), without the shortfall/incumbent
        asserts - pages may be incomplete mid-run."""
        now = _parse_utc(now_utc)
        cutoff = now - timedelta(days=HISTORY_DAYS)
        active_cutoff = now - timedelta(days=ACTIVE_WITHIN_DAYS)
        admitted, _ = _admission_walk(coins, binance_meta, cutoff,
                                      active_cutoff)
        return len(admitted)

    print("fetching CoinGecko page 1 ...")
    batch = _get_json(_COINGECKO_URL % 1)
    coins.extend(c for c in batch if c.get("market_cap_rank") is not None)
    _probe_candidates()
    if _admitted_count() < TARGET_SIZE:
        time.sleep(3)  # keyless CoinGecko tier is ~5-15 req/min
        print("fetching CoinGecko page 2 ...")
        batch = _get_json(_COINGECKO_URL % 2)
        coins.extend(c for c in batch if c.get("market_cap_rank") is not None)
        _probe_candidates()

    manifest = build_manifest(coins, binance_meta, now_utc)
    write_manifest(manifest, out_path)
    by_reason = {}
    for entry in manifest["excluded"]:
        by_reason[entry["reason"]] = by_reason.get(entry["reason"], 0) + 1
    print("pinned %s" % out_path)
    print("admitted: %d" % len(manifest["admitted"]))
    print("excluded: %d" % len(manifest["excluded"]))
    for reason in sorted(by_reason):
        print("  %-40s %d" % (reason, by_reason[reason]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
