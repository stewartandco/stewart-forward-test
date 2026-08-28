import pytest

from tools_select_crypto_universe import classify_exclusion, build_manifest, PinnedManifestError, write_manifest


def _coin(rank, cid, sym, mcap=1e9, price=100.0):
    return {"market_cap_rank": rank, "id": cid, "symbol": sym,
            "current_price": price, "market_cap": mcap}


def _top130():
    """130 clean candidates with the five incumbents in their real ranks and
    tether at rank 3, mirroring the live top of the table."""
    coins = [_coin(i, f"coin{i}", f"c{i}") for i in range(1, 131)]
    coins[0] = _coin(1, "bitcoin", "btc")
    coins[1] = _coin(2, "ethereum", "eth")
    coins[2] = _coin(3, "tether", "usdt")
    coins[3] = _coin(4, "solana", "sol")
    coins[4] = _coin(5, "ripple", "xrp")
    coins[5] = _coin(6, "binancecoin", "bnb")
    meta = {c["symbol"].upper() + "USDT": "2020-01-01" for c in coins}
    return coins, meta


def test_stablecoins_excluded_by_declared_list():
    assert classify_exclusion(_coin(3, "tether", "usdt")) == "stablecoin (declared list)"


def test_wrapped_and_staked_excluded_by_pattern():
    assert classify_exclusion(_coin(5, "wrapped-bitcoin", "wbtc")) == "wrapped/staked/bridged (pattern)"
    assert classify_exclusion(_coin(6, "lido-staked-ether", "steth")) == "wrapped/staked/bridged (pattern)"


def test_peg_heuristic_flags_unlisted_usd_peg():
    assert classify_exclusion(_coin(40, "some-new-usd", "xusd", price=1.001)) == "stablecoin (peg heuristic)"


def test_normal_coin_not_excluded():
    assert classify_exclusion(_coin(1, "bitcoin", "btc")) is None


def test_build_manifest_admits_exactly_100_with_reasons_and_incumbents():
    coins, meta = _top130()
    meta["C9USDT"] = None                       # no pair -> excluded
    meta["C10USDT"] = "2025-06-01"              # <2y history -> excluded
    m = build_manifest(coins, meta, now_utc="2026-08-28T00:00:00Z")
    assert len(m["admitted"]) == 100
    assert {a["binance_symbol"] for a in m["admitted"]} >= {
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"}
    reasons = {e["reason"] for e in m["excluded"]}
    assert "no Binance USDT spot pair" in reasons
    assert "history < 2y" in reasons
    assert all(e.get("reason") for e in m["excluded"])
    ranks = [a["rank"] for a in m["admitted"]]
    assert ranks == sorted(ranks)               # manifest order = mcap order


def test_build_manifest_raises_on_under_full_walk():
    """A manifest claiming a top-100 rule must never pin short."""
    coins, meta = _top130()
    coins = coins[:50]                          # only 50 candidates walked
    with pytest.raises(RuntimeError, match="under-full"):
        build_manifest(coins, meta, now_utc="2026-08-28T00:00:00Z")


def test_build_manifest_raises_on_duplicate_binance_symbol():
    """Two distinct coins sharing a ticker map to one USDT pair: loud stop,
    never a silent dedupe (that would change the declared rule)."""
    coins, meta = _top130()
    coins[59] = _coin(60, "impostor-c50", "c50")     # same ticker as rank 50
    with pytest.raises(RuntimeError, match="duplicate Binance symbol"):
        build_manifest(coins, meta, now_utc="2026-08-28T00:00:00Z")


def test_build_manifest_raises_on_duplicate_coin_id():
    """Page drift can hand the same coin id twice: loud stop."""
    coins, meta = _top130()
    coins[59] = _coin(60, "coin50", "again")         # same id as rank 50
    with pytest.raises(RuntimeError, match="duplicate coin id"):
        build_manifest(coins, meta, now_utc="2026-08-28T00:00:00Z")


def test_build_manifest_raises_when_an_incumbent_is_missing():
    coins, meta = _top130()
    meta["BTCUSDT"] = None                      # bitcoin loses its pair
    with pytest.raises(RuntimeError, match="incumbent"):
        build_manifest(coins, meta, now_utc="2026-08-28T00:00:00Z")


def test_history_cutoff_boundary_is_inclusive_of_exactly_two_years():
    """now - 730 days: a first bar ON the cutoff date is admitted; one day
    inside the window is excluded."""
    coins, meta = _top130()
    meta["C11USDT"] = "2024-08-28"              # exactly 730 days before now
    meta["C12USDT"] = "2024-08-29"              # 729 days -> too young
    m = build_manifest(coins, meta, now_utc="2026-08-28T00:00:00Z")
    admitted = {a["binance_symbol"] for a in m["admitted"]}
    excluded = {e["binance_symbol"]: e["reason"]
                for e in m["excluded"] if "binance_symbol" in e}
    assert "C11USDT" in admitted
    assert excluded.get("C12USDT") == "history < 2y"


def test_write_manifest_refuses_overwrite(tmp_path):
    p = tmp_path / "u.json"
    p.write_text("{}")
    with pytest.raises(PinnedManifestError):
        write_manifest({"admitted": []}, p)


def test_non_ascii_symbol_is_no_pair_without_a_probe():
    # Binance symbols are strictly [A-Z0-9]+; a unicode CoinGecko ticker can
    # never be a pair, and probing it would crash URL encoding (live finding
    # 2026-08-28). The guard answers None with no network call.
    from tools_select_crypto_universe import _fetch_first_1d_utc, _valid_binance_symbol
    assert not _valid_binance_symbol("\u0e3f\u00c9USDT")
    assert _valid_binance_symbol("BTCUSDT")
    assert _fetch_first_1d_utc("\u0e3f\u00c9USDT") is None
