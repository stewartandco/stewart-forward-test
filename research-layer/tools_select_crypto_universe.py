"""One-time crypto universe selection (SP5 D1). Declared rule, pinned output.
Writes exactly one new file, data/crypto_universe_manifest.json, and REFUSES
to overwrite it (re-selection is a new declared event).
HONESTY LIMIT (carried into the manifest's "rule" text): today's top-100 is
selected by today's outcomes - survivorship bias is documented, not corrected;
forward quarantine is the honest arbiter."""

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

STABLECOIN_IDS = frozenset({"tether","usd-coin","dai","binance-usd","true-usd",
    "first-digital-usd","ethena-usde","usds","paypal-usd","frax","usdd",
    "gemini-dollar","paxos-standard","liquity-usd","susds","usual-usd"})
WRAPPED_MARKERS = ("wrapped","staked","bridged","restaked","wbeth","wsteth",
    "steth","reth","cbeth","cbbtc","weeth","rseth","ezeth","oseth","lseth",
    "tbtc","solvbtc")
PEG_BAND = 0.02

HISTORY_DAYS = 730
TARGET_SIZE = 100
INCUMBENTS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT")

RULE = (
    "Top-100 crypto assets by CoinGecko market cap on the selection date, walked in "
    "ascending market-cap-rank order. Excluded, with each exclusion recorded and "
    "reasoned: declared stablecoins (fixed id list), wrapped/staked/bridged/restaked "
    "derivatives (marker substrings of the CoinGecko id or symbol), and undeclared "
    "USD-peg suspects (price within {peg} of 1.0 with 'usd' in the id or symbol). "
    "Admission additionally requires a Binance USDT spot pair with at least {days} "
    "days ({years} years) of daily history at selection time. HONESTY LIMIT: this "
    "universe is selected by today's outcomes - assets that died or fell out of the "
    "top-100 before today were never candidates, so any backtest over this universe "
    "inherits survivorship bias. That bias is documented here, not corrected; the "
    "forward quarantine period is the honest arbiter. Re-selection is a new declared "
    "event with its own manifest, never a silent refresh of this one."
).format(peg=PEG_BAND, days=HISTORY_DAYS, years=HISTORY_DAYS // 365)


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


def build_manifest(coins, binance_meta, now_utc):
    """Walk coins by ascending market_cap_rank, applying the declared rule,
    until TARGET_SIZE assets are admitted. Only coins actually walked appear
    in the manifest. Pure function: no network, no filesystem."""
    cutoff = _parse_utc(now_utc) - timedelta(days=HISTORY_DAYS)
    admitted = []
    excluded = []
    for coin in sorted(coins, key=lambda c: c["market_cap_rank"]):
        if len(admitted) >= TARGET_SIZE:
            break
        rank = coin["market_cap_rank"]
        cid = coin["id"]
        sym = str(coin["symbol"])
        reason = classify_exclusion(coin)
        if reason is not None:
            excluded.append({"rank": rank, "id": cid, "symbol": sym,
                             "reason": reason})
            continue
        binance_symbol = sym.upper() + "USDT"
        first_1d = binance_meta.get(binance_symbol)
        if first_1d is None:
            excluded.append({"rank": rank, "id": cid, "symbol": sym,
                             "binance_symbol": binance_symbol,
                             "reason": "no Binance USDT spot pair"})
            continue
        if _parse_utc(first_1d) > cutoff:
            excluded.append({"rank": rank, "id": cid, "symbol": sym,
                             "binance_symbol": binance_symbol,
                             "first_1d_utc": first_1d,
                             "reason": "history < 2y"})
            continue
        admitted.append({"rank": rank, "id": cid, "symbol": sym,
                         "binance_symbol": binance_symbol,
                         "market_cap": coin.get("market_cap"),
                         "first_1d_utc": first_1d})
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
                   "binance /api/v3/klines first-bar probe"),
        "admitted": admitted,
        "excluded": excluded,
    }


def write_manifest(manifest, path):
    """Write the pinned manifest. Refuses to overwrite: a re-selection is a
    new declared event, never a silent refresh."""
    path = Path(path)
    if path.exists():
        raise PinnedManifestError(
            "%s already exists - the universe manifest is PINNED. Re-selection "
            "is a new declared event: write a new manifest, do not overwrite "
            "this one." % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


# --------------------------------------------------------------------------
# Real-network path below. Run supervised, once. Tests never touch this.
# --------------------------------------------------------------------------

_UA = {"User-Agent": "stewart-research-layer/1.0 (universe selection, one-shot)"}
_COINGECKO_URL = ("https://api.coingecko.com/api/v3/coins/markets"
                  "?vs_currency=usd&order=market_cap_desc&per_page=250&page=%d")
_BINANCE_URL = ("https://api.binance.com/api/v3/klines"
                "?symbol=%s&interval=1d&limit=1&startTime=0")


def _get_json(url):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_first_1d_utc(binance_symbol):
    """First daily bar open time for a Binance symbol, ISO date string, or
    None when the pair does not exist (HTTP 400 or empty klines)."""
    try:
        klines = _get_json(_BINANCE_URL % binance_symbol)
    except urllib.error.HTTPError as exc:
        if exc.code == 400:
            return None
        raise
    if not klines:
        return None
    open_ms = klines[0][0]
    return datetime.fromtimestamp(open_ms / 1000.0, tz=timezone.utc).strftime(
        "%Y-%m-%d")


def main():
    out_path = Path("data") / "crypto_universe_manifest.json"
    if out_path.exists():
        raise PinnedManifestError(
            "%s already exists - refusing before any network call. "
            "Re-selection is a new declared event." % out_path)
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    coins = []
    binance_meta = {}

    def _probe_candidates():
        """Probe Binance first-bar for every not-yet-probed candidate that
        survives classify_exclusion."""
        for coin in sorted(coins, key=lambda c: c["market_cap_rank"]):
            if classify_exclusion(coin) is not None:
                continue
            binance_symbol = str(coin["symbol"]).upper() + "USDT"
            if binance_symbol in binance_meta:
                continue
            binance_meta[binance_symbol] = _fetch_first_1d_utc(binance_symbol)
            time.sleep(0.25)

    def _admitted_count():
        """Count admissions without the incumbent assert (pages may be
        incomplete mid-run)."""
        cutoff = _parse_utc(now_utc) - timedelta(days=HISTORY_DAYS)
        n = 0
        for coin in sorted(coins, key=lambda c: c["market_cap_rank"]):
            if n >= TARGET_SIZE:
                break
            if classify_exclusion(coin) is not None:
                continue
            first_1d = binance_meta.get(str(coin["symbol"]).upper() + "USDT")
            if first_1d is None or _parse_utc(first_1d) > cutoff:
                continue
            n += 1
        return n

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


if __name__ == "__main__":
    sys.exit(main())
