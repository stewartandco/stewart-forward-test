from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.common import content_id
from pipeline.registry import Registry
from pipeline.test_gauntlet import gauntlet_registry
from tools import intraday_rehearsal as ir


def _v2_original(tmp_path):
    reg, spec = gauntlet_registry(tmp_path)
    two = json.loads(json.dumps(spec))
    two["version"] = 2
    two["strategy_id"] = None
    two["strategy_id"] = content_id(two, "strategy_id")
    reg.register_strategy(two)
    return reg, spec, two


def test_register_makes_a_1d_and_a_1h_twin_per_asset(tmp_path):
    reg, v1, orig = _v2_original(tmp_path)
    out = tmp_path / "twins.json"
    rc = ir.main(["register", "--registry", str(reg.log_path),
                  "--assets", "BTCUSDT,ETHUSDT", "--originals", "1",
                  "--seed", "1", "--out", str(out)])
    assert rc == 0
    twins = json.loads(out.read_text())
    assert len(twins) == 4                                  # 1 x 2 assets x 2 tfs
    assert {(t["asset"], t["tf"]) for t in twins.values()} == {
        ("BTCUSDT", "1d"), ("BTCUSDT", "1h"), ("ETHUSDT", "1d"), ("ETHUSDT", "1h")}
    assert {t["original"] for t in twins.values()} == {orig["strategy_id"]}
    specs = {e["payload"]["strategy_id"]: e["payload"]
             for e in Registry(reg.log_path).entries()
             if e["entry_type"] == "strategy_registered"}
    for sid, t in twins.items():
        s = specs[sid]
        assert sid not in (v1["strategy_id"], orig["strategy_id"])
        assert s["universe"] == dict(orig["universe"], assets=[t["asset"]],
                                     timeframe=t["tf"], session="24x7",
                                     asset_class="crypto")
        assert s["cost_model"] == orig["cost_model"]
        assert s["provenance"]["run_id"] == "rehearsal-2b1"
        assert s["provenance"]["sibling_group_id"] == (
            f"rehearsal-2b1:{orig['strategy_id']}:{t['asset']}_{t['tf']}")
        chk = dict(s, strategy_id=None)
        assert content_id(chk, "strategy_id") == sid        # fresh and honest
    assert len(set(twins)) == 4


def test_register_skips_version_1_originals(tmp_path):
    reg, v1 = gauntlet_registry(tmp_path)                   # version 1 only
    try:
        ir.register(reg.log_path, ["BTCUSDT"], 1, 1, tmp_path / "t.json")
    except SystemExit as exc:
        assert "eligible" in str(exc)
    else:
        raise AssertionError("a version-1 original must not be picked")


def test_report_reads_verdicts_and_config(tmp_path):
    reg, _, orig = _v2_original(tmp_path)
    out = tmp_path / "twins.json"
    ir.main(["register", "--registry", str(reg.log_path), "--assets", "BTCUSDT",
             "--originals", "1", "--seed", "1", "--out", str(out)])
    twins = json.loads(out.read_text())
    sid = next(s for s, t in twins.items() if t["tf"] == "1h")
    reg.record_state_change(sid, "screened", "t")
    reg.record_verdict(sid, "screened", "pass", {"trades": 77}, "0" * 64)
    reg.record_state_change(sid, "gauntlet", None)
    reg.record_verdict(sid, "gauntlet", "fail",
                       {"fail_reason": "mc_p05", "train_sharpe": 1.5}, "0" * 64)
    art = tmp_path / "art" / sid / "gauntlet"
    art.mkdir(parents=True)
    (art / "config.json").write_text(json.dumps(
        {"metrics": {"is_edge_per_trade": 0.01, "oos_edge_per_trade": 0.02,
                     "mc_p05_equity": 0.9, "p_ruin": 0.25}}))
    rep = tmp_path / "report.md"
    assert ir.main(["report", "--registry", str(reg.log_path), "--twins", str(out),
                    "--artifacts-dir", str(tmp_path / "art"), "--out", str(rep)]) == 0
    text = rep.read_text()
    row = next(l for l in text.splitlines() if sid in l)
    assert "pass (77)" in row and "fail" in row and "mc_p05" in row
    assert "1.500" in row and "0.01000" in row and "0.900" in row and "0.2500" in row
    assert "| 1d |" in text and "| 1h |" in text


def test_register_refuses_a_chain_beside_registry_log_d(tmp_path, capsys):
    """The live tree keeps registry_log.jsonl next to registry_log.d."""
    live = tmp_path / "live"
    live.mkdir()
    reg, _, orig = _v2_original(live)
    (live / "registry_log.d").mkdir()
    chain = live / "reg.jsonl"
    named = live / "registry_log.jsonl"
    chain.replace(named)
    before = named.read_bytes()
    out = tmp_path / "twins.json"
    with pytest.raises(SystemExit) as exc:
        ir.main(["register", "--registry", str(named), "--assets", "BTCUSDT",
                 "--originals", "1", "--seed", "1", "--out", str(out)])
    assert exc.value.code == 2
    assert "refusing" in capsys.readouterr().err
    assert named.read_bytes() == before
    assert not out.exists()


def test_register_refuses_this_layers_own_registry(tmp_path, monkeypatch, capsys):
    """Path (a): the layer's own registry_log.jsonl is refused wherever it is."""
    reg, _, orig = _v2_original(tmp_path)
    monkeypatch.setattr(ir, "LAYER_ROOT", tmp_path)
    own = tmp_path / "registry_log.jsonl"
    reg.log_path.replace(own)
    before = own.read_bytes()
    with pytest.raises(SystemExit) as exc:
        ir.main(["register", "--registry", str(own), "--assets", "BTCUSDT",
                 "--originals", "1", "--seed", "1", "--out", str(tmp_path / "t.json")])
    assert exc.value.code == 2
    assert own.read_bytes() == before


def test_pick_originals_excludes_non_crypto_and_non_1d(tmp_path):
    reg, v1, orig = _v2_original(tmp_path)
    base = {orig["strategy_id"]: orig}

    def variant(**uni):
        s = json.loads(json.dumps(orig))
        s["universe"] = dict(orig["universe"], **uni)
        s["strategy_id"] = None
        s["strategy_id"] = content_id(s, "strategy_id")
        return s
    fx, hourly = variant(asset_class="fx"), variant(timeframe="1h")
    specs = {**base, fx["strategy_id"]: fx, hourly["strategy_id"]: hourly}
    assert ir.pick_originals(specs, 3, 1) == [orig["strategy_id"]]


# ---- quarantine subcommand (2b-2 Task 10) ----

def _hourly_twin_setup(tmp_path):
    """A tmp chain with one registered 1h twin (via `register`), the twins
    file, an empty scratch data dir and the hourly bars a fake fetch writes."""
    from pipeline.test_intraday_statistics import _twin_bars
    from pipeline.test_quarantine_record_intraday import _write
    from pipeline.test_intraday_statistics import _twin_spec
    reg, spec = gauntlet_registry(tmp_path)
    orig = json.loads(json.dumps(spec))
    orig["version"] = 2                       # exit-rules-v7 blocks: simulable at v2
    orig["blocks"] = _twin_spec("1h")["blocks"]
    orig["strategy_id"] = None
    orig["strategy_id"] = content_id(orig, "strategy_id")
    reg.register_strategy(orig)
    twins_path = tmp_path / "twins.json"
    ir.main(["register", "--registry", str(reg.log_path), "--assets", "ETHUSD",
             "--originals", "1", "--seed", "1", "--out", str(twins_path)])
    twins = json.loads(twins_path.read_text())
    sid = next(s for s, t in twins.items() if t["tf"] == "1h")
    daily, hourly, _ = _twin_bars()
    end = daily[162]["date"]                  # 12 owed days: more than one catch-up run
    hourly = [b for b in hourly if b["date"][:10] <= end]
    data = tmp_path / "scratch-data"
    data.mkdir()
    calls = []

    def fake_fetch(symbol, tf, data_dir, now_ms=None):
        calls.append((symbol, tf))
        _write(Path(data_dir), f"{symbol}_{tf}.csv", hourly)
        return {}
    return reg, twins_path, sid, data, daily, fake_fetch, calls


def test_quarantine_refuses_a_chain_beside_registry_log_d(tmp_path, capsys):
    live = tmp_path / "live"
    live.mkdir()
    reg, twins_path, sid, data, daily, fake_fetch, calls = _hourly_twin_setup(live)
    (live / "registry_log.d").mkdir()
    named = live / "registry_log.jsonl"
    reg.log_path.replace(named)
    before = named.read_bytes()
    out = tmp_path / "report.md"
    with pytest.raises(SystemExit) as exc:
        ir.main(["quarantine", "--registry", str(named), "--data-dir", str(data),
                 "--twins", str(twins_path), "--entered", daily[150]["date"],
                 "--n", "1", "--out", str(out)])
    assert exc.value.code == 2
    assert "refusing" in capsys.readouterr().err
    assert named.read_bytes() == before
    assert not out.exists()
    assert not (live / "logs").exists()
    assert calls == []


def test_quarantine_records_the_owed_days_and_reports_them(tmp_path, monkeypatch):
    from pipeline import crypto_data
    reg, twins_path, sid, data, daily, fake_fetch, calls = _hourly_twin_setup(tmp_path)
    monkeypatch.setattr(crypto_data, "fetch_symbol", fake_fetch)
    entered = daily[150]["date"]
    owed = [daily[i]["date"] for i in range(151, 163)]
    out = tmp_path / "report.md"
    rc = ir.main(["quarantine", "--registry", str(reg.log_path),
                  "--data-dir", str(data), "--twins", str(twins_path),
                  "--entered", entered, "--n", "1", "--out", str(out)])
    assert rc == 0
    assert calls == [("ETHUSD", "1h")]                       # fetch-owed ran
    entries = list(Registry(reg.log_path).entries())
    trans = [(e["payload"]["to"], e["ts_utc"]) for e in entries
             if e["entry_type"] == "state_change"
             and e["payload"]["strategy_id"] == sid]
    assert [t for t, _ in trans] == ["screened", "gauntlet", "quarantine"]
    assert trans[-1][1] == f"{entered}T00:00:00Z"
    verdicts = [e["payload"] for e in entries if e["entry_type"] == "verdict"
                and e["payload"]["strategy_id"] == sid]
    assert [(v["stage"], v["verdict"], v["metrics"]) for v in verdicts] == [
        ("gauntlet", "pass", {"rehearsal": True})]
    rows = [e["payload"] for e in entries
            if e["entry_type"] == "quarantine_decision"
            and e["payload"]["strategy_id"] == sid]
    assert sorted(r["date"] for r in rows) == owed
    assert all(r["asset"] == "ETHUSD@1h" for r in rows)
    text = out.read_text(encoding="utf-8")
    assert sid in text
    for d in owed:
        assert d in text
    n_entries = sum(r["entries"] for r in rows)
    n_exits = sum(r["exits"] for r in rows)
    row = next(l for l in text.splitlines() if l.startswith(f"| `{sid}`"))
    assert f"| 12 | 12 | {n_entries} | {n_exits} |" in row
    assert "catch-up: nothing owed" in text
    assert "catch-up runs: 3 " in text                       # 10 + 2 + nothing owed
    assert "- 1h timed: 12 strategy-day(s)" in text
    for d in owed:                                           # timed per date
        assert any(l.startswith(f"- {d}: ") and l.endswith(", 1, 0")
                   for l in text.splitlines())
    assert "unrecorded bar dates in window: 0" in text      # the review lines
    assert "days=" in text                                   # the live gate read it
    assert "seconds per strategy-day" in text
    assert "verify before" in text.lower() and "verify after" in text.lower()
    assert "no new failure" in text.lower()
    assert "REGISTRY VALID" in text
    gate = next(l for l in text.splitlines() if l.startswith(sid) and "days=" in l)
    assert f"days=  12 trades={n_exits:>3}" in gate          # the gate read the record


def test_quarantine_refuses_a_twin_that_is_not_proposed(tmp_path, monkeypatch):
    from pipeline import crypto_data
    reg, twins_path, sid, data, daily, fake_fetch, calls = _hourly_twin_setup(tmp_path)
    monkeypatch.setattr(crypto_data, "fetch_symbol", fake_fetch)
    reg.record_state_change(sid, "screened", "t")
    before = reg.log_path.read_bytes()
    with pytest.raises(SystemExit) as exc:
        ir.main(["quarantine", "--registry", str(reg.log_path),
                 "--data-dir", str(data), "--twins", str(twins_path),
                 "--entered", daily[150]["date"], "--n", "1",
                 "--out", str(tmp_path / "r.md")])
    assert "proposed" in str(exc.value.code)
    assert reg.log_path.read_bytes() == before
    assert calls == []


def _run_quarantine(tmp_path, monkeypatch, reg, twins_path, data, daily, fake_fetch):
    from pipeline import crypto_data
    monkeypatch.setattr(crypto_data, "fetch_symbol", fake_fetch)
    out = tmp_path / "report.md"
    rc = ir.main(["quarantine", "--registry", str(reg.log_path),
                  "--data-dir", str(data), "--twins", str(twins_path),
                  "--entered", daily[150]["date"], "--n", "1", "--out", str(out)])
    return rc, out.read_text(encoding="utf-8")


@pytest.mark.parametrize("which,result", [
    ("after", (1, [], "(no verdict line)")),                    # verify crashed
    ("after", (0, [], "Empty log.")),                           # not a VALID verdict
    ("before", (1, ["  line 3: x"], "REGISTRY INVALID - 1/9 entries fail.")),
])
def test_quarantine_fails_unless_verify_says_valid(tmp_path, monkeypatch, which, result):
    reg, twins_path, sid, data, daily, fake_fetch, calls = _hourly_twin_setup(tmp_path)
    real = ir.verify_failures
    seen = []

    def fake_verify(registry, data_dir):
        seen.append(1)
        if (which == "before") == (len(seen) == 1):
            return result
        return real(registry, data_dir)
    monkeypatch.setattr(ir, "verify_failures", fake_verify)
    rc, text = _run_quarantine(tmp_path, monkeypatch, reg, twins_path, data,
                               daily, fake_fetch)
    assert len(seen) == 2
    assert rc == 1
    assert "Status: **FAIL**" in text


@pytest.mark.parametrize("line", [
    "{sid}  days=   0 trades=  0 terminal=1.0000 cone_p01=    n/a psr=0.5000  -> HOLD",
    "{sid}  days=  12 trades= 99 terminal=1.0000 cone_p01=    n/a psr=0.5000  -> HOLD",
    "{sid}  days=  11 trades={exits:>3} terminal=1.0000 cone_p01=    n/a psr=0.5000  -> HOLD",
])
def test_quarantine_fails_when_the_gate_misreads_the_record(tmp_path, monkeypatch, line):
    from pipeline import livegate
    reg, twins_path, sid, data, daily, fake_fetch, calls = _hourly_twin_setup(tmp_path)

    def fake_gate(argv):
        exits = sum(e["payload"]["exits"] for e in Registry(reg.log_path).entries()
                    if e["entry_type"] == "quarantine_decision"
                    and e["payload"]["strategy_id"] == sid)
        print(line.format(sid=sid, exits=exits))
        return 0
    monkeypatch.setattr(livegate, "run", fake_gate)
    rc, text = _run_quarantine(tmp_path, monkeypatch, reg, twins_path, data,
                               daily, fake_fetch)
    assert rc == 1
    assert "Status: **FAIL**" in text


@pytest.mark.parametrize("layout", ["own", "beside_d"])
def test_quarantine_refuses_the_live_data_dir(tmp_path, monkeypatch, capsys, layout):
    from pipeline import crypto_data
    reg, twins_path, sid, data, daily, fake_fetch, calls = _hourly_twin_setup(tmp_path)
    monkeypatch.setattr(crypto_data, "fetch_symbol", fake_fetch)
    live = tmp_path / "livetree"
    live.mkdir()
    if layout == "own":
        monkeypatch.setattr(ir, "LAYER_ROOT", live)
    else:
        (live / "registry_log.d").mkdir()
    live_data = live / "data"
    live_data.mkdir()
    before = reg.log_path.read_bytes()
    out = tmp_path / "report.md"
    with pytest.raises(SystemExit) as exc:
        ir.main(["quarantine", "--registry", str(reg.log_path),
                 "--data-dir", str(live_data), "--twins", str(twins_path),
                 "--entered", daily[150]["date"], "--n", "1", "--out", str(out)])
    assert exc.value.code == 2
    assert "refusing" in capsys.readouterr().err
    assert reg.log_path.read_bytes() == before
    assert list(live_data.iterdir()) == []
    assert calls == [] and not out.exists()


# ---- --timeframes (2b-3 Switch 1, Task 8) ----

def test_register_honours_timeframes(tmp_path, capsys):
    reg, v1, orig = _v2_original(tmp_path)
    fresh = tmp_path / "fresh.jsonl"                         # same chain, before any twin
    fresh.write_bytes(reg.log_path.read_bytes())
    out = tmp_path / "twins.json"
    rc = ir.main(["register", "--registry", str(reg.log_path),
                  "--assets", "BTCUSDT,ETHUSDT", "--originals", "1",
                  "--seed", "1", "--timeframes", "1d", "--out", str(out)])
    assert rc == 0
    twins = json.loads(out.read_text())
    assert {(t["asset"], t["tf"]) for t in twins.values()} == {
        ("BTCUSDT", "1d"), ("ETHUSDT", "1d")}
    specs = {e["payload"]["strategy_id"]: e["payload"]
             for e in Registry(reg.log_path).entries()
             if e["entry_type"] == "strategy_registered"}
    assert all(specs[s]["universe"]["timeframe"] == "1d" for s in twins)
    assert not any(p["universe"].get("timeframe") == "1h" for p in specs.values())
    # the same 1d twins a default (1d,1h) run makes on a byte copy of the
    # chain taken before the first register (a second register on the SAME
    # chain may pick a twin as an original)
    full = tmp_path / "full.json"
    assert ir.main(["register", "--registry", str(fresh),
                    "--assets", "BTCUSDT,ETHUSDT", "--originals", "1",
                    "--seed", "1", "--out", str(full)]) == 0
    full_twins = json.loads(full.read_text())
    assert {s for s, t in full_twins.items() if t["tf"] == "1d"} == set(twins)
    assert len(full_twins) == 4
    rep = tmp_path / "report.md"
    assert ir.main(["report", "--registry", str(reg.log_path), "--twins", str(out),
                    "--artifacts-dir", str(tmp_path / "art"), "--out", str(rep)]) == 0
    text = rep.read_text()
    assert all(s in text for s in twins) and "| 1h |" not in text
    for bad in ("1d,1d", "2h", ""):
        with pytest.raises(SystemExit) as exc:
            ir.main(["register", "--registry", str(reg.log_path),
                     "--assets", "BTCUSDT", "--timeframes", bad,
                     "--out", str(tmp_path / "bad.json")])
        assert exc.value.code == 2
    assert not (tmp_path / "bad.json").exists()


# ---- register skips a twin whose composition is registered (2b-3 plan 5 Task 6) ----

def _covered_chain(tmp_path, monkeypatch):
    """A chain holding a version-2 original plus the SAME composition already
    registered on BTCUSDT and ETHUSDT at 1d under another run (what the live
    composer does once crypto is per-cell). `pick_originals` is pinned to the
    original: the covering specs are themselves eligible originals."""
    reg, v1, orig = _v2_original(tmp_path)
    covering = {}
    for asset in ("BTCUSDT", "ETHUSDT"):
        cov = ir.make_twin(orig, asset, "1d")
        cov["provenance"] = dict(cov["provenance"], run_id="composer-live",
                                 sibling_group_id=f"composer-live:{asset}")
        cov["strategy_id"] = None
        cov["strategy_id"] = content_id(cov, "strategy_id")
        reg.register_strategy(cov)
        covering[asset] = cov["strategy_id"]
    monkeypatch.setattr(ir, "pick_originals",
                        lambda specs, n, seed: [orig["strategy_id"]])
    return reg, orig, covering


def test_register_skips_a_twin_whose_composition_is_registered(
        tmp_path, monkeypatch, capsys):
    reg, orig, covering = _covered_chain(tmp_path, monkeypatch)
    out = tmp_path / "twins.json"
    rc = ir.main(["register", "--registry", str(reg.log_path),
                  "--assets", "BTCUSDT,ETHUSDT", "--originals", "1",
                  "--seed", "1", "--out", str(out)])
    assert rc == 0
    text = capsys.readouterr().out
    twins = json.loads(out.read_text())
    # only the two 1h twins were registered; both 1d twins ARE the live ones
    assert {(t["asset"], t["tf"]) for t in twins.values()} == {
        ("BTCUSDT", "1h"), ("ETHUSDT", "1h")}
    skipped_lines = [l for l in text.splitlines() if l.startswith("skipped twin")]
    assert len(skipped_lines) == 2
    eth = next(l for l in skipped_lines if "ETHUSDT" in l)
    assert covering["ETHUSDT"] in eth and "1d" in eth
    specs = {e["payload"]["strategy_id"]: e["payload"]
             for e in Registry(reg.log_path).entries()
             if e["entry_type"] == "strategy_registered"}
    assert not any(p["provenance"].get("run_id") == "rehearsal-2b1"
                   and p["universe"]["timeframe"] == "1d" for p in specs.values())
    assert {s for s, p in specs.items()
            if p["provenance"].get("run_id") == "rehearsal-2b1"} == set(twins)
    rc, fails, verdict = ir.verify_failures(reg.log_path, tmp_path / "data")
    assert rc == 0 and not fails and "INVALID" not in verdict, (fails, verdict)


def test_skipped_twins_go_to_the_sidecar_not_the_map(tmp_path, monkeypatch):
    reg, orig, covering = _covered_chain(tmp_path, monkeypatch)
    out = tmp_path / "twins.json"
    twins = ir.register(reg.log_path, ["BTCUSDT", "ETHUSDT"], 1, 1, out)
    side = tmp_path / "twins.skipped.json"
    skipped = json.loads(side.read_text())
    assert json.loads(out.read_text()) == twins
    assert not set(skipped) & set(twins)
    assert len(skipped) == 2 and all(t["tf"] == "1d" for t in skipped.values())
    for sid, t in skipped.items():
        assert set(t) == {"original", "asset", "tf", "existing"}
        assert t["original"] == orig["strategy_id"]
        assert t["existing"] == covering[t["asset"]]
        assert sid not in covering.values()                  # the twin's own id
    for t in twins.values():                                 # map keeps its shape
        assert set(t) == {"original", "asset", "tf"}


def test_pick_twins_and_render_read_an_output_with_skips(tmp_path, monkeypatch):
    reg, orig, covering = _covered_chain(tmp_path, monkeypatch)
    out = tmp_path / "twins.json"
    ir.main(["register", "--registry", str(reg.log_path),
             "--assets", "BTCUSDT,ETHUSDT", "--originals", "1", "--seed", "1",
             "--out", str(out)])
    twins = json.loads(out.read_text())
    picked, tfs = ir.pick_twins(twins, out, 2, None)         # default: non-1d
    assert tfs == ["1h"] and len(picked) == 2 and set(picked) <= set(twins)
    with pytest.raises(SystemExit) as exc:                   # the 1d twins are gone
        ir.pick_twins(twins, out, 1, ("1d",))
    assert "no such twin" in str(exc.value)
    rows = ir.collect(reg.log_path, tmp_path / "art", twins)
    text = ir.render(twins, rows)
    assert "2 twins, 2 (original, asset) pairs." in text
    assert all(s in text for s in twins)
    assert "| 1h |" in text and "| 1d |" not in text


def test_quarantine_takes_1d_twins_from_a_1d_only_file(tmp_path, monkeypatch):
    from pipeline import crypto_data
    from pipeline.test_intraday_statistics import _twin_bars
    from pipeline.test_quarantine_record_intraday import _write
    reg, twins_path, sid, data, daily, fake_fetch, calls = _hourly_twin_setup(tmp_path)
    monkeypatch.setattr(crypto_data, "fetch_symbol", fake_fetch)
    twins = json.loads(twins_path.read_text())
    d1 = {s: t for s, t in twins.items() if t["tf"] == "1d"}
    (sid1,) = d1
    only = tmp_path / "twins-1d.json"
    only.write_text(json.dumps(d1))
    end = daily[162]["date"]
    _write(data, "ETHUSD_1d.csv", [b for b in daily if b["date"][:10] <= end])
    entered = daily[150]["date"]
    out = tmp_path / "report.md"
    rc = ir.main(["quarantine", "--registry", str(reg.log_path),
                  "--data-dir", str(data), "--twins", str(only),
                  "--entered", entered, "--n", "1", "--out", str(out)])
    text = out.read_text(encoding="utf-8")
    assert rc == 0, text
    assert calls == []                                       # nothing to fetch at 1d
    rows = [e["payload"] for e in Registry(reg.log_path).entries()
            if e["entry_type"] == "quarantine_decision"]
    assert {r["strategy_id"] for r in rows} == {sid1}
    assert sorted(r["date"] for r in rows) == [daily[i]["date"] for i in range(151, 163)]
    assert all(r["asset"] == "ETHUSD" for r in rows)
    assert "1 1d twin(s)" in text and "- 1d timed: 12 strategy-day(s)" in text
    exits = sum(1 for r in rows if r["action"] == "exit")   # daily rows: action
    assert exits > 0                                         # the gate check bites
    entries = sum(1 for r in rows if r["action"] in ("enter_long", "enter_short"))
    assert entries > 0                                       # the entries column bites
    row = next(l for l in text.splitlines() if l.startswith(f"| `{sid1}`"))
    assert f"| 12 | 12 | {entries} | {exits} | 2022-" in row


# ---- pick_originals (CLAUDE.md Switch 2 gate 2, 2b-3 plan 4 Task 7) ----

def _spec(sid, family, *block_types, version=2, **uni):
    return {"strategy_id": sid, "version": version, "family": family,
            "universe": dict({"assets": ["BTCUSDT"], "asset_class": "crypto",
                              "timeframe": "1d", "session": "24x7"}, **uni),
            "blocks": [{"role": "entry", "type": t, "params": {}}
                       for t in block_types] or
                      [{"role": "entry", "type": "ma_cross_ds", "params": {}}]}


def _plain_pool(families=("a", "b", "c", "d"), per=3):
    return {f"p-{f}{k}": _spec(f"p-{f}{k}", f)
            for f in families for k in range(per)}


def test_vol_target_and_vol_percentile_are_picked_first():
    for seed in range(12):
        specs = _plain_pool()
        specs["vt"] = _spec("vt", "a", "vol_target")
        specs["vp"] = _spec("vp", "b", "vol_percentile_gate")
        got = ir.pick_originals(specs, 2, seed)
        assert got == ["vt", "vp"], (seed, got)       # vol_target, then vol_percentile
        got4 = ir.pick_originals(specs, 4, seed)
        assert got4[:2] == ["vt", "vp"] and len(got4) == 4


def test_one_spec_with_both_vol_kinds_is_picked_once():
    specs = _plain_pool()
    specs["both"] = _spec("both", "a", "vol_target", "vol_percentile_gate")
    for seed in range(6):
        got = ir.pick_originals(specs, 3, seed)
        assert got[0] == "both" and got.count("both") == 1 and len(got) == 3


def test_one_per_family_before_any_repeat():
    for seed in range(25):
        specs = _plain_pool(("a", "b", "c"), per=4)
        got = ir.pick_originals(specs, 3, seed)
        assert sorted(specs[s]["family"] for s in got) == ["a", "b", "c"], (seed, got)
        got5 = ir.pick_originals(specs, 5, seed)
        fams = [specs[s]["family"] for s in got5]
        assert set(fams[:3]) == {"a", "b", "c"} and len(set(got5)) == 5
    # the vol picks count toward their family: vol_target in family a means
    # the next picks are b and c, not a second a
    for seed in range(25):
        specs = _plain_pool(("a", "b", "c"), per=4)
        specs["vt"] = _spec("vt", "a", "vol_target")
        got = ir.pick_originals(specs, 3, seed)
        assert got[0] == "vt"
        assert sorted(specs[s]["family"] for s in got) == ["a", "b", "c"], (seed, got)


def test_same_seed_same_pick_and_different_seed_differs():
    specs = _plain_pool(("a", "b", "c", "d", "e"), per=4)
    for seed in (1, 7, 20261010):
        assert ir.pick_originals(specs, 4, seed) == ir.pick_originals(specs, 4, seed)
    picks = {tuple(ir.pick_originals(specs, 4, seed)) for seed in range(12)}
    assert len(picks) > 1
    # the vol choice itself is seeded too
    many = dict(specs)
    for k in range(6):
        many[f"vt{k}"] = _spec(f"vt{k}", f"v{k}", "vol_target")
    firsts = {ir.pick_originals(many, 1, seed)[0] for seed in range(20)}
    assert len(firsts) > 1 and all(s.startswith("vt") for s in firsts)


def test_non_crypto_non_v2_and_intraday_originals_are_skipped():
    specs = {
        "ok": _spec("ok", "a"),
        "v1": _spec("v1", "b", version=1),
        "fx": _spec("fx", "c", asset_class="fx"),
        "etf": _spec("etf", "d", asset_class="equity_etf"),
        "h1": _spec("h1", "e", timeframe="1h"),
        "h4": _spec("h4", "f", timeframe="4h"),
        "vt1": _spec("vt1", "g", "vol_target", version=1),
        "vtfx": _spec("vtfx", "h", "vol_target", asset_class="fx"),
        "vth": _spec("vth", "i", "vol_target", timeframe="1h"),
    }
    assert ir.pick_originals(specs, 5, 3) == ["ok"]
    # an omitted asset_class / timeframe means crypto / 1d (the schema default)
    bare = _spec("bare", "z")
    del bare["universe"]["asset_class"], bare["universe"]["timeframe"]
    assert ir.pick_originals({"bare": bare}, 1, 3) == ["bare"]


def test_n_below_two_takes_only_the_first_kind():
    specs = _plain_pool()
    specs["vt"] = _spec("vt", "a", "vol_target")
    specs["vp"] = _spec("vp", "b", "vol_percentile_gate")
    for seed in range(8):
        assert ir.pick_originals(specs, 1, seed) == ["vt"]
        assert ir.pick_originals(specs, 0, seed) == []
    only_vp = _plain_pool()
    only_vp["vp"] = _spec("vp", "b", "vol_percentile_gate")
    assert ir.pick_originals(only_vp, 1, 1) == ["vp"]


def test_a_chain_without_vol_specs_still_fills_n():
    specs = _plain_pool(("a", "b"), per=3)               # 6 specs, 2 families
    for seed in range(10):
        got = ir.pick_originals(specs, 4, seed)
        assert len(got) == 4 and len(set(got)) == 4 and set(got) <= set(specs)
        assert {specs[s]["family"] for s in got} == {"a", "b"}
    assert len(ir.pick_originals(specs, 6, 1)) == 6
    # asked for more than exist: everything eligible, no more (register then
    # refuses with "only N eligible originals")
    assert sorted(ir.pick_originals(specs, 9, 1)) == sorted(specs)


# ---- every phase-2 timeframe; --transition-only (2b-3 plan 4 Task 7) ----

def _agg(hourly, n):
    """n-hour bars from the hourly rows (open of the first, extremes, close of
    the last): the day's last bar keeps the day's move, so daily marks agree."""
    out = []
    for i in range(0, len(hourly), n):
        g = hourly[i:i + n]
        out.append({"date": g[0]["date"], "open": g[0]["open"],
                    "high": max(b["high"] for b in g), "low": min(b["low"] for b in g),
                    "close": g[-1]["close"], "volume": sum(b["volume"] for b in g)})
    return out


def _multi_tf_setup(tmp_path, timeframes=("1d", "1h", "4h", "12h"), owed_days=4):
    """A tmp chain with the original and its ETHUSD twins at `timeframes`
    (via `register`), the twins file, a scratch data dir and the bars a fake
    fetch writes per timeframe."""
    from pipeline.test_intraday_statistics import _twin_bars, _twin_spec
    from pipeline.test_quarantine_record_intraday import _write
    reg, spec = gauntlet_registry(tmp_path)
    orig = json.loads(json.dumps(spec))
    orig["version"] = 2
    orig["blocks"] = _twin_spec("1h")["blocks"]
    orig["strategy_id"] = None
    orig["strategy_id"] = content_id(orig, "strategy_id")
    reg.register_strategy(orig)
    twins_path = tmp_path / "twins.json"
    ir.main(["register", "--registry", str(reg.log_path), "--assets", "ETHUSD",
             "--originals", "1", "--seed", "1",
             "--timeframes", ",".join(timeframes), "--out", str(twins_path)])
    daily, hourly, _ = _twin_bars()
    entered = daily[150]["date"]
    end = daily[150 + owed_days]["date"]
    hourly = [b for b in hourly if b["date"][:10] <= end]
    daily = [b for b in daily if b["date"][:10] <= end]
    files = {"1h": hourly, "4h": _agg(hourly, 4), "12h": _agg(hourly, 12), "1d": daily}
    data = tmp_path / "scratch-data"
    data.mkdir()
    calls = []

    def fake_fetch(symbol, tf, data_dir, now_ms=None):
        calls.append((symbol, tf))
        _write(Path(data_dir), f"{symbol}_{tf}.csv", files[tf])
        return {}

    def freeze(*tfs):
        for tf in tfs:
            _write(data, f"ETHUSD_{tf}.csv", files[tf])
    owed = [daily[150 + i]["date"] for i in range(1, owed_days + 1)]
    return reg, twins_path, data, entered, owed, fake_fetch, calls, freeze


def _states(reg_path):
    out: dict[str, str] = {}
    for e in Registry(reg_path).entries():
        if e["entry_type"] == "state_change":
            out[e["payload"]["strategy_id"]] = e["payload"]["to"]
    return out


def test_quarantine_moves_twins_of_every_requested_timeframe(tmp_path, monkeypatch):
    from pipeline import crypto_data
    reg, twins_path, data, entered, owed, fake_fetch, calls, _ = _multi_tf_setup(tmp_path)
    monkeypatch.setattr(crypto_data, "fetch_symbol", fake_fetch)
    twins = json.loads(twins_path.read_text())
    assert {t["tf"] for t in twins.values()} == {"1d", "1h", "4h", "12h"}
    by_tf = {t["tf"]: s for s, t in twins.items()}
    out = tmp_path / "report.md"
    rc = ir.main(["quarantine", "--registry", str(reg.log_path),
                  "--data-dir", str(data), "--twins", str(twins_path),
                  "--entered", entered, "--n", "1", "--out", str(out)])
    text = out.read_text(encoding="utf-8")
    assert rc == 0, text
    states = _states(reg.log_path)
    # default = every non-1d timeframe in the file, --n twins each; 1d is left
    for tf in ("1h", "4h", "12h"):
        assert states[by_tf[tf]] == "quarantine", tf
    assert by_tf["1d"] not in states
    assert sorted(calls) == [("ETHUSD", "12h"), ("ETHUSD", "1h"), ("ETHUSD", "4h")]
    rows = [e["payload"] for e in Registry(reg.log_path).entries()
            if e["entry_type"] == "quarantine_decision"]
    for tf in ("1h", "4h", "12h"):
        mine = [r for r in rows if r["strategy_id"] == by_tf[tf]]
        assert sorted(r["date"] for r in mine) == owed, tf
        assert {r["asset"] for r in mine} == {f"ETHUSD@{tf}"}
    assert not [r for r in rows if r["strategy_id"] == by_tf["1d"]]
    assert "3 12h/4h/1h twin(s)" in text
    for tf in ("1h", "4h", "12h"):
        assert f"| `{by_tf[tf]}`" in text
    assert f"| `{by_tf['1d']}`" not in text


def test_quarantine_report_times_each_timeframe_separately(tmp_path, monkeypatch):
    """Final review I1: seconds per strategy-day come out PER TIMEFRAME, each
    measured around that timeframe's own simulations (never one pooled or
    apportioned figure). A slow 4h simulation must not leak into the 1h line."""
    import re
    import time
    from pipeline import crypto_data
    from pipeline import quarantine as q
    reg, twins_path, data, entered, owed, fake_fetch, calls, _ = _multi_tf_setup(tmp_path)
    monkeypatch.setattr(crypto_data, "fetch_symbol", fake_fetch)
    real = q.observe_day

    def slow_4h(spec, *a, **kw):
        if spec["universe"]["timeframe"] == "4h":
            time.sleep(0.2)
        return real(spec, *a, **kw)
    monkeypatch.setattr(q, "observe_day", slow_4h)
    twins = json.loads(twins_path.read_text())
    by_tf = {t["tf"]: s for s, t in twins.items()}
    out = tmp_path / "report.md"
    rc = ir.main(["quarantine", "--registry", str(reg.log_path),
                  "--data-dir", str(data), "--twins", str(twins_path),
                  "--entered", entered, "--n", "1", "--timeframes", "1h,4h",
                  "--out", str(out)])
    text = out.read_text(encoding="utf-8")
    assert rc == 0, text
    assert q.observe_day is slow_4h                          # the timing hook is undone
    line = re.compile(r"^- (\S+) timed: (\d+) strategy-day\(s\), ([\d.]+)s total, "
                      r"([\d.]+)s mean per strategy-day", re.M)
    got = {m.group(1): (int(m.group(2)), float(m.group(3)), float(m.group(4)))
           for m in line.finditer(text)}
    assert set(got) == {"1h", "4h"}                          # one line per timeframe
    rows = [e["payload"] for e in Registry(reg.log_path).entries()
            if e["entry_type"] == "quarantine_decision"]
    for tf in ("1h", "4h"):
        n_rows = len([r for r in rows if r["strategy_id"] == by_tf[tf]])
        assert got[tf][0] == n_rows == len(owed), tf         # counts add up
    assert got["4h"][2] >= 0.2 and got["4h"][1] >= 0.2 * len(owed)
    assert got["1h"][2] < got["4h"][2]                       # measured, not apportioned
    assert "12h/4h/1h only" not in text and " only: " not in text   # pooled line gone


def test_quarantine_timeframes_flag_picks_just_those(tmp_path, monkeypatch):
    from pipeline import crypto_data
    reg, twins_path, data, entered, owed, fake_fetch, calls, _ = _multi_tf_setup(tmp_path)
    monkeypatch.setattr(crypto_data, "fetch_symbol", fake_fetch)
    twins = json.loads(twins_path.read_text())
    by_tf = {t["tf"]: s for s, t in twins.items()}
    out = tmp_path / "report.md"
    rc = ir.main(["quarantine", "--registry", str(reg.log_path),
                  "--data-dir", str(data), "--twins", str(twins_path),
                  "--entered", entered, "--n", "1", "--timeframes", "4h,12h",
                  "--out", str(out)])
    assert rc == 0, out.read_text(encoding="utf-8")
    assert sorted(calls) == [("ETHUSD", "12h"), ("ETHUSD", "4h")]
    assert {s for s, st in _states(reg.log_path).items() if st == "quarantine"} == {
        by_tf["4h"], by_tf["12h"]}


def test_quarantine_refuses_a_timeframe_the_twins_file_lacks_or_too_few(
        tmp_path, monkeypatch):
    from pipeline import crypto_data
    reg, twins_path, data, entered, owed, fake_fetch, calls, _ = _multi_tf_setup(
        tmp_path, timeframes=("1d", "1h"))
    monkeypatch.setattr(crypto_data, "fetch_symbol", fake_fetch)
    before = reg.log_path.read_bytes()
    common = ["quarantine", "--registry", str(reg.log_path), "--data-dir", str(data),
              "--twins", str(twins_path), "--entered", entered,
              "--out", str(tmp_path / "r.md")]
    with pytest.raises(SystemExit) as exc:
        ir.main(common + ["--n", "1", "--timeframes", "4h"])
    assert "4h" in str(exc.value.code)
    with pytest.raises(SystemExit) as exc:
        ir.main(common + ["--n", "2"])                     # one 1h twin only
    assert "only 1 1h twins" in str(exc.value.code)
    with pytest.raises(SystemExit) as exc:
        ir.main(common + ["--n", "1", "--timeframes", "2h"])   # not a timeframe
    assert exc.value.code == 2
    assert reg.log_path.read_bytes() == before and calls == []


def test_transition_only_leaves_dates_owed_and_fetches_nothing(tmp_path, monkeypatch):
    from pipeline import crypto_data
    from pipeline import quarantine as q
    reg, twins_path, data, entered, owed, fake_fetch, calls, freeze = _multi_tf_setup(
        tmp_path, timeframes=("1d", "1h", "4h", "12h"))
    freeze("1h", "4h", "12h")                              # the frozen data on disk
    monkeypatch.setattr(crypto_data, "fetch_symbol", fake_fetch)

    def _no_recorder(*a, **k):                             # --fetch-owed / --catch-up
        raise AssertionError("transition-only must not run the recorder")
    monkeypatch.setattr(q, "run", _no_recorder)
    frozen = {p.name: p.read_bytes() for p in data.iterdir()}
    twins = json.loads(twins_path.read_text())
    by_tf = {t["tf"]: s for s, t in twins.items()}
    out = tmp_path / "report.md"
    rc = ir.main(["quarantine", "--registry", str(reg.log_path),
                  "--data-dir", str(data), "--twins", str(twins_path),
                  "--entered", entered, "--n", "1", "--transition-only",
                  "--out", str(out)])
    text = out.read_text(encoding="utf-8")
    assert rc == 0, text
    assert calls == []                                     # no fetch
    assert {p.name: p.read_bytes() for p in data.iterdir()} == frozen
    states = _states(reg.log_path)
    for tf in ("1h", "4h", "12h"):
        assert states[by_tf[tf]] == "quarantine"           # the hand transition ran
    entries = list(Registry(reg.log_path).entries())
    assert not [e for e in entries if e["entry_type"] == "quarantine_decision"]
    assert not (tmp_path / "logs").exists()                # no ledger, no catch-up
    # ... and the dates are still owed, for every moved twin, on the frozen data
    specs = {e["payload"]["strategy_id"]: e["payload"] for e in entries
             if e["entry_type"] == "strategy_registered"}
    for tf in ("1h", "4h", "12h"):
        spec = specs[by_tf[tf]]
        last = q._last_bar_date(data, spec)
        assert sorted(q._owed_by_date(data, spec, entered, last)) == owed, tf
    assert "transition-only" in text.lower()
    assert "REGISTRY VALID" in text
    for tf in ("1h", "4h", "12h"):
        assert by_tf[tf] in text
