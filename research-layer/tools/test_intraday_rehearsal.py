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
    assert "1h only: 12 date(s)" in text and "for 12 1h strategy-day(s)" in text
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
    # the same 1d twins a default (1d,1h) run makes on a fresh chain (a
    # second register on the SAME chain may pick a twin as an original)
    reg2, _, orig2 = _v2_original(tmp_path / "fresh")
    assert orig2["strategy_id"] == orig["strategy_id"]
    full = tmp_path / "full.json"
    assert ir.main(["register", "--registry", str(reg2.log_path),
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
    assert "1 1d twin(s)" in text and "1d only: 12 date(s)" in text
    exits = sum(1 for r in rows if r["action"] == "exit")   # daily rows: action
    assert exits > 0                                         # the gate check bites
    row = next(l for l in text.splitlines() if l.startswith(f"| `{sid1}`"))
    assert f"| {exits} | 2022-" in row
