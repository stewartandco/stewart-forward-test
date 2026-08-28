import json, pytest
from tools_select_crypto_universe import classify_exclusion, build_manifest, PinnedManifestError, write_manifest

def _coin(rank, cid, sym, mcap=1e9, price=100.0):
    return {"market_cap_rank": rank, "id": cid, "symbol": sym,
            "current_price": price, "market_cap": mcap}

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
    coins = [_coin(i, f"coin{i}", f"c{i}") for i in range(1, 131)]
    coins[0] = _coin(1, "bitcoin", "btc"); coins[1] = _coin(2, "ethereum", "eth")
    coins[2] = _coin(3, "tether", "usdt")
    coins[3] = _coin(4, "solana", "sol"); coins[4] = _coin(5, "ripple", "xrp")
    coins[5] = _coin(6, "binancecoin", "bnb")
    meta = {c["symbol"].upper() + "USDT": "2020-01-01" for c in coins}
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

def test_write_manifest_refuses_overwrite(tmp_path):
    p = tmp_path / "u.json"; p.write_text("{}")
    with pytest.raises(PinnedManifestError):
        write_manifest({"admitted": []}, p)
