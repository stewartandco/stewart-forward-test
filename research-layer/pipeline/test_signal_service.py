"""Build 4a task 7: the read-only next-bar signal service
(pipeline/signal_service.py; renamed from signal.py in the fix wave, C1).

SPEC is a verbatim copy of ONE real strategy_registered payload (strategy
9b6753a48c4d0ccd, crypto 1d, state quarantine on a 2026-10-16 copy of the
live chain; Ruling R6), so the tests are deterministic and exercise a spec the
service really serves. Its entry is a 55-bar channel breakout (both sides),
so the fixture series below is a bounded oscillation that never breaks its own
channel, then one close far above it.
"""
import copy
import datetime as dt
import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pipeline import signal_service as S
from pipeline.engine import simulate_asset
from pipeline.registry import Registry
from pipeline.screen import load_bars


def _bars(closes, start="2026-01-01"):
    d0 = dt.date.fromisoformat(start)
    return [{"date": (d0 + dt.timedelta(days=i)).isoformat(), "open": c, "high": c * 1.01,
             "low": c * 0.99, "close": c, "volume": 1.0} for i, c in enumerate(closes)]


def _next(bars):
    return (dt.date.fromisoformat(bars[-1]["date"][:10]) + dt.timedelta(days=1)).isoformat()


SPEC = {
    "blocks": [
        {"params": {"direction": "both", "lookback": 55}, "role": "entry",
         "type": "channel_breakout"},
        {"params": {"atr_len": 14, "mult": 2.0}, "role": "stop", "type": "atr_stop"},
        {"params": {"max_bars": 40}, "role": "exit", "type": "time_stop"},
        {"params": {"f": 0.02}, "role": "risk", "type": "fixed_fraction"},
    ],
    "cost_model": {"commission_per_side": 0.001, "slippage_ticks": 0.0005},
    "created_utc": "2026-08-16T21:03:35Z",
    "family": "channel_breakout_both_fixedfrac",
    "generator": {"agent": "composer", "model": "claude-opus-5",
                  "pipeline_version": "g1.0.0", "run_id": "2026-08-17-gen3"},
    "name": "BTC+ETH 1d channel_breakout_both_fixedfrac dboth l55 al14 m2.0 mb40 f0.02",
    "provenance": {
        "card_ids": ["3d3ec8dc2fe6799c", "6078cc70ec7b8303", "80a41669681d747b",
                     "9a6abe19343ae46f", "e519d19950313468", "e818d7f15744fa91"],
        "generation": 0,
        "parent_strategy_id": None,
        "sibling_group_id": "channel_breakout_both_fixedfrac-2026-08-17-gen3",
    },
    "strategy_id": "9b6753a48c4d0ccd",
    "universe": {"asset_class": "crypto", "assets": ["BTCUSD", "ETHUSD"],
                 "session": "24x7", "timeframe": "1d"},
    "version": 1,
}


def _oscillation(n):
    # closes in [98, 102]; with high/low = close +-1% no close ever leaves the
    # trailing 55-bar channel, so SPEC never signals inside it
    return [100.0 + 2.0 * math.sin(i / 5.0) for i in range(n)]


def _fixture_series():
    """SPEC's entry fires on the LAST close: 110 > every prior high (<= 103.02)."""
    return _oscillation(199) + [110.0]


def test_a_pending_entry_on_the_last_close_shows_as_the_next_bar_position():
    """P1: the entry decided on bar k's close appears for bar k+1."""
    bars = _bars(_fixture_series())          # a series whose last close fires SPEC's entry
    # the engine alone cannot see it: entries fill at the next open
    assert simulate_asset(SPEC["blocks"], bars, SPEC["cost_model"], 365,
                          version=SPEC.get("version", 1))["position"] is None
    pos = S.next_bar_position(SPEC, bars)
    real = simulate_asset(SPEC["blocks"], bars + _bars([bars[-1]["close"]], start=_next(bars)),
                          SPEC["cost_model"], 365, version=SPEC.get("version", 1))["position"]
    assert real is not None and real["entry_i"] == len(bars)   # entered at the next bar
    assert pos["side"] == ("long" if real["side"] == 1 else "short")
    assert pos["side"] == "long"
    assert pos["frac"] == pytest.approx(real["notional_frac"])
    assert 0.0 < pos["frac"] <= 1.0


def test_a_pending_exit_on_the_last_close_shows_flat():
    """P1, exit side: SPEC's 40-bar time stop falls due at the next open, so
    the position still open at the last close is NOT the next bar's target."""
    closes = _oscillation(160) + [110.0] * 41     # breakout close at index 160
    bars = _bars(closes)
    # entered at 161's open, deadline 161 + 40 = 201 = the next bar
    held = simulate_asset(SPEC["blocks"], bars, SPEC["cost_model"], 365,
                          version=SPEC.get("version", 1))["position"]
    assert held is not None and held["side"] == 1 and held["deadline"] == len(bars)
    assert S.next_bar_position(SPEC, bars) == {"side": "flat", "frac": 0.0}


def test_flat_when_no_position():
    pos = S.next_bar_position(SPEC, _bars([100.0] * 300))
    assert pos == {"side": "flat", "frac": 0.0}


def test_no_bars_is_flat():
    assert S.next_bar_position(SPEC, []) == {"side": "flat", "frac": 0.0}


# ---------------- run(): read-only, one output file ------------------------

def _write_csv(path: Path, bars):
    lines = ["date,open,high,low,close,volume"]
    lines += [f"{b['date']},{b['open']},{b['high']},{b['low']},{b['close']},{b['volume']}"
              for b in bars]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _walk(reg, sid, to):
    path = {"quarantine": ("screened", "gauntlet", "quarantine"),
            "live": ("screened", "gauntlet", "quarantine", "live"),
            "graveyard": ("graveyard",)}[to]
    for state in path:
        reg.record_state_change(sid, state, "test")


def _variant(sid, **universe):
    s = copy.deepcopy(SPEC)
    s["strategy_id"] = sid
    s["universe"].update(universe)
    return s


def _fixture_layer(tmp_path):
    """A small chain + data dir: four crypto 1d strategies (two served, one
    buried, one still proposed) and one quarantined fx strategy whose price
    file does not exist (serving it would crash)."""
    reg = Registry(tmp_path / "registry_log.jsonl")
    served_q, served_live = SPEC, _variant("live0000000000001")
    buried, proposed = _variant("dead0000000000001"), _variant("prop0000000000001")
    fx = _variant("fx00000000000001", asset_class="fx", assets=["EURUSD"], session="fx_5d")
    for s in (served_q, served_live, buried, proposed, fx):
        reg.append("strategy_registered", s)
    reg.record_verdict(SPEC["strategy_id"], "screened", "pass",
                       {"max_dd": -0.0955, "trades": 40}, "0" * 64)
    _walk(reg, served_q["strategy_id"], "quarantine")
    _walk(reg, served_live["strategy_id"], "live")
    _walk(reg, buried["strategy_id"], "graveyard")
    _walk(reg, fx["strategy_id"], "quarantine")
    data = tmp_path / "data"
    data.mkdir()
    bars = _bars(_fixture_series())
    _write_csv(data / "BTCUSD_1d.csv", bars)
    _write_csv(data / "ETHUSD_1d.csv", _bars([100.0] * 200))
    return reg, data, bars


def _tree(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _argv(tmp_path, reg, data, as_of=None):
    # default: the run's UTC day is the day after the fixture's last bar,
    # i.e. that bar is the one that closed at the start of the day (fresh)
    as_of = as_of or _next(_bars(_fixture_series()))
    return ["--registry", str(reg.log_path), "--data-dir", str(data),
            "--out-dir", str(tmp_path / "logs" / "signals"), "--as-of", as_of]


def test_the_service_never_writes_the_chain(tmp_path, capsys):
    """Read-only: run() on a chain COPY leaves its bytes identical and writes only logs/signals."""
    reg, data, bars = _fixture_layer(tmp_path)
    before = _tree(tmp_path)
    chain_sha = hashlib.sha256(reg.log_path.read_bytes()).hexdigest()

    assert S.run(_argv(tmp_path, reg, data)) == 0

    assert hashlib.sha256(reg.log_path.read_bytes()).hexdigest() == chain_sha
    after = _tree(tmp_path)
    out_name = f"logs/signals/targets-{bars[-1]['date']}.json"
    assert set(after) - set(before) == {out_name}         # no lock file, no tmp, nothing else
    assert {k: v for k, v in after.items() if k != out_name} == before

    doc = json.loads((tmp_path / out_name).read_text(encoding="utf-8"))
    assert doc["bar_end"] == bars[-1]["date"]
    assert doc["engine_rev"] == S.ENGINE_REV
    assert doc["data_digest"] == {
        a: hashlib.sha256((data / f"{a}_1d.csv").read_bytes()).hexdigest()
        for a in ("BTCUSD", "ETHUSD")}
    dt.datetime.strptime(doc["written_utc"], "%Y-%m-%dT%H:%M:%SZ")
    # quarantine AND live are served; graveyard, proposed and non-crypto are not
    assert set(doc["strategies"]) == {SPEC["strategy_id"], "live0000000000001"}
    mine = doc["strategies"][SPEC["strategy_id"]]
    assert mine["max_dd"] == -0.0955
    assert doc["strategies"]["live0000000000001"]["max_dd"] is None
    assert mine["spec_sha"] == hashlib.sha256(json.dumps(
        SPEC, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert mine["assets"]["BTCUSD"] == S.next_bar_position(SPEC, bars)
    assert mine["assets"]["BTCUSD"]["side"] == "long"
    assert mine["assets"]["ETHUSD"] == {"side": "flat", "frac": 0.0}
    assert doc["deferred"] == {}                              # minors item 1: always present


def test_a_failed_write_leaves_the_previous_targets_file_whole(tmp_path, monkeypatch):
    """The output is published by os.replace: a write that dies part-way never
    leaves a torn targets file where the executor reads."""
    reg, data, bars = _fixture_layer(tmp_path)
    out = tmp_path / "logs" / "signals" / f"targets-{bars[-1]['date']}.json"
    out.parent.mkdir(parents=True)
    out.write_text('{"previous": true}', encoding="utf-8")
    real_write = Path.write_text

    def torn_write(self, text, *a, **k):
        real_write(self, text[: len(text) // 2], *a, **k)
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", torn_write)
    with pytest.raises(OSError):
        S.run(_argv(tmp_path, reg, data))
    monkeypatch.undo()
    assert json.loads(out.read_text(encoding="utf-8")) == {"previous": True}


def test_nothing_to_serve_writes_nothing(tmp_path):
    reg = Registry(tmp_path / "registry_log.jsonl")
    reg.append("strategy_registered", SPEC)                  # proposed only
    data = tmp_path / "data"
    data.mkdir()
    assert S.run(["--registry", str(reg.log_path), "--data-dir", str(data),
                  "--out-dir", str(tmp_path / "logs" / "signals")]) == 0
    assert not (tmp_path / "logs").exists()


# ---------------- fix round 1: freshness, one read per file ----------------

def _refused(tmp_path, reg, data, capsys, as_of=None):
    before = _tree(tmp_path)
    rc = S.run(_argv(tmp_path, reg, data, as_of))
    out = capsys.readouterr().out
    assert rc == S.REFUSED_EXIT == 2                         # M8: a refusal is 2, a crash 1
    assert _tree(tmp_path) == before                         # nothing written, anywhere
    assert not (tmp_path / "logs").exists()
    return out


def test_a_lagging_asset_refuses_and_writes_nothing(tmp_path, capsys):
    """I-1: BTCUSD at D, ETHUSD at D-1 -> no targets file (never a fresh
    bar_end over a stale target), every asset's last date printed. Minors
    item 1 pin: every served strategy today trades BTCUSD+ETHUSD, so one
    lagging asset defers ALL of them and nothing is publishable -> exit 2."""
    reg, data, bars = _fixture_layer(tmp_path)
    _write_csv(data / "ETHUSD_1d.csv", _bars([100.0] * 199))
    out = _refused(tmp_path, reg, data, capsys)
    assert "ETHUSD" in out and bars[-1]["date"] in out and bars[-2]["date"] in out
    assert "no strategy can be published" in out
    assert SPEC["strategy_id"] in out and "live0000000000001" in out


def test_an_empty_price_file_refuses_and_writes_nothing(tmp_path, capsys):
    reg, data, _ = _fixture_layer(tmp_path)
    _write_csv(data / "ETHUSD_1d.csv", [])                  # header only
    out = _refused(tmp_path, reg, data, capsys)
    assert "ETHUSD EMPTY" in out


def test_a_missing_price_file_names_the_strategy_and_asset(tmp_path, capsys):
    """M-2: a clear error, not a traceback; still nonzero, nothing written."""
    reg, data, _ = _fixture_layer(tmp_path)
    (data / "ETHUSD_1d.csv").unlink()
    out = _refused(tmp_path, reg, data, capsys)
    assert "ETHUSD" in out and SPEC["strategy_id"] in out and "live0000000000001" in out


def test_the_digest_and_the_bars_come_from_the_same_bytes(tmp_path, monkeypatch):
    """M-1: the file is replaced between the service's read and any later read;
    data_digest and the targets must both describe the bytes first read."""
    reg, data, bars = _fixture_layer(tmp_path)
    btc = data / "BTCUSD_1d.csv"
    first = (data / "ETHUSD_1d.csv").read_bytes()           # flat series, same dates
    real_read = Path.read_bytes
    calls = []

    def read_once_then_disk(self):
        if self == btc and not calls:
            calls.append(self)
            return first
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", read_once_then_disk)
    assert S.run(_argv(tmp_path, reg, data)) == 0
    monkeypatch.undo()
    doc = json.loads((tmp_path / "logs" / "signals" / f"targets-{bars[-1]['date']}.json")
                     .read_text(encoding="utf-8"))
    assert doc["data_digest"]["BTCUSD"] == hashlib.sha256(first).hexdigest()
    mine = doc["strategies"][SPEC["strategy_id"]]["assets"]["BTCUSD"]
    assert mine == S.next_bar_position(SPEC, S.parse_daily_csv(first)) == {"side": "flat", "frac": 0.0}
    assert S.next_bar_position(SPEC, bars)["side"] == "long"   # what the disk bytes would say


def test_parse_daily_csv_matches_load_bars(tmp_path):
    """The service's one-read parser is screen.load_bars' parse (dates cut to
    the date part, as the quarantine recorder does)."""
    rows = ["date,open,high,low,close,volume",
            "2026-01-03,3.0,3.5,2.5,3.25,30",
            "2026-01-01 00:00:00,1.0,1.5,0.5,1.25,10",
            "2026-01-02,2.0,2.5,1.5,2.25,20"]
    crlf = chr(13) + chr(10)
    raw = (crlf.join(rows) + crlf).encode("utf-8")
    (tmp_path / "XUSD_1d.csv").write_bytes(raw)
    ref = load_bars(tmp_path, "XUSD", "9999-12-31")
    for b in ref:
        b["date"] = b["date"][:10]
    got = S.parse_daily_csv(raw)
    assert got == ref
    assert [b["date"] for b in got] == ["2026-01-01", "2026-01-02", "2026-01-03"]


def test_parse_daily_csv_matches_load_bars_with_a_blank_line_and_reordered_columns(tmp_path):
    """Columns are read by NAME (an extra column is ignored, order does not
    matter) and a blank line is skipped, exactly as load_bars does."""
    rows = ["volume,close,extra,date,low,high,open",
            "20,2.25,x,2026-01-02,1.5,2.5,2.0",
            "",
            "10,1.25,y,2026-01-01,0.5,1.5,1.0"]
    raw = ("\n".join(rows) + "\n").encode("utf-8")
    (tmp_path / "XUSD_1d.csv").write_bytes(raw)
    ref = load_bars(tmp_path, "XUSD", "9999-12-31")
    got = S.parse_daily_csv(raw)
    assert got == ref
    assert got == [
        {"date": "2026-01-01", "open": 1.0, "high": 1.5, "low": 0.5, "close": 1.25, "volume": 10.0},
        {"date": "2026-01-02", "open": 2.0, "high": 2.5, "low": 1.5, "close": 2.25, "volume": 20.0}]


# ---------------- fix wave: C1 (stdlib shadowing) --------------------------

PIPELINE = Path(__file__).resolve().parent


def test_no_pipeline_module_shadows_a_stdlib_module():
    """C1: run by path, a pipeline/ script puts pipeline/ first on sys.path;
    pipeline/signal.py shadowed the stdlib `signal` that subprocess imports."""
    clash = sorted(p.name for p in PIPELINE.glob("*.py") if p.stem in sys.stdlib_module_names)
    assert clash == []


def test_taskguard_runs_by_path():
    """C1: trading-systems' run_executor.bat runs pipeline/taskguard.py BY
    PATH; with a pipeline/signal.py present it died importing subprocess."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    env.pop("PYTHONPATH", None)
    r = subprocess.run([sys.executable, str(PIPELINE / "taskguard.py"), "--help"],
                       cwd=str(PIPELINE.parent.parent), env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert "usage" in r.stdout, (r.stdout, r.stderr)


# ---------------- fix wave: I2 (nothing fresh = refused), M8, M6 -----------

def test_uniformly_stale_data_refuses_and_writes_nothing(tmp_path, capsys):
    """I2(a): every asset ends on the same, but old, bar: never rewrite an old
    targets file and exit 0."""
    reg, data, bars = _fixture_layer(tmp_path)
    last = dt.date.fromisoformat(bars[-1]["date"])
    out = _refused(tmp_path, reg, data, capsys, as_of=(last + dt.timedelta(days=2)).isoformat())
    assert "stale" in out and bars[-1]["date"] in out


def test_the_bar_that_closed_at_the_start_of_the_day_is_fresh(tmp_path):
    """I2 boundary: as_of = bar_end + 1 day publishes (and only that day, minors item 2)."""
    reg, data, bars = _fixture_layer(tmp_path)
    last = dt.date.fromisoformat(bars[-1]["date"])
    assert S.run(_argv(tmp_path, reg, data, (last + dt.timedelta(days=1)).isoformat())) == 0
    assert (tmp_path / "logs" / "signals" / f"targets-{bars[-1]['date']}.json").exists()


def test_a_bar_dated_the_run_day_refuses_and_writes_nothing(tmp_path, capsys):
    """Minors item 2: bar_end > as_of - 1 day is today's still-open bar (or
    later). data_fetch drops the open kline, so this is bad data: refuse."""
    reg, data, bars = _fixture_layer(tmp_path)
    last = dt.date.fromisoformat(bars[-1]["date"])
    for days in (0, -3):
        out = _refused(tmp_path, reg, data, capsys, as_of=(last + dt.timedelta(days=days)).isoformat())
        assert "partial" in out and bars[-1]["date"] in out


def test_as_of_defaults_to_the_utc_date(tmp_path, monkeypatch):
    reg, data, bars = _fixture_layer(tmp_path)
    last = dt.date.fromisoformat(bars[-1]["date"])
    argv = ["--registry", str(reg.log_path), "--data-dir", str(data),
            "--out-dir", str(tmp_path / "logs" / "signals")]
    monkeypatch.setattr(S, "_utc_today", lambda: last + dt.timedelta(days=2))
    assert S.run(argv) == 2
    assert not (tmp_path / "logs").exists()
    monkeypatch.setattr(S, "_utc_today", lambda: last + dt.timedelta(days=1))
    assert S.run(argv) == 0


def test_utc_today_is_the_utc_date():
    assert S._utc_today() == dt.datetime.now(dt.timezone.utc).date()


def test_a_missing_registry_refuses_and_writes_nothing(tmp_path, capsys):
    """I2(b): a missing registry_log.jsonl is not an empty chain."""
    _, data, _ = _fixture_layer(tmp_path)
    missing = tmp_path / "nope" / "registry_log.jsonl"
    before = _tree(tmp_path)
    rc = S.run(["--registry", str(missing), "--data-dir", str(data),
                "--out-dir", str(tmp_path / "logs" / "signals"), "--as-of", "2026-07-20"])
    assert rc == 2
    assert "no registry file" in capsys.readouterr().out
    assert _tree(tmp_path) == before and not (tmp_path / "logs").exists()


def test_the_module_exits_2_on_a_refusal(tmp_path):
    """M8 end to end, as the wrapper runs it: `python -m pipeline.signal_service`."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, "-m", "pipeline.signal_service",
                        "--registry", str(tmp_path / "registry_log.jsonl"),
                        "--data-dir", str(tmp_path), "--out-dir", str(tmp_path / "out")],
                       cwd=str(PIPELINE.parent), env=env, capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "REFUSED" in r.stdout and not (tmp_path / "out").exists()


def _replace_failing(n, calls):
    real = os.replace

    def fake(src, dst):
        calls.append(dst)
        if len(calls) <= n:
            raise PermissionError(13, "in use by another process")
        return real(src, dst)
    return fake


def test_publish_retries_a_held_target(tmp_path, monkeypatch):
    """M6: Windows refuses the replace while the executor reads the file; the
    publish retries (short sleeps) and succeeds."""
    reg, data, bars = _fixture_layer(tmp_path)
    calls, sleeps = [], []
    monkeypatch.setattr(S.os, "replace", _replace_failing(S.REPLACE_RETRIES, calls))
    monkeypatch.setattr(S.time, "sleep", sleeps.append)
    assert S.run(_argv(tmp_path, reg, data)) == 0
    monkeypatch.undo()
    assert len(calls) == S.REPLACE_RETRIES + 1 == 9
    assert sleeps == [S.REPLACE_SLEEP_S] * 8 and 0 < S.REPLACE_SLEEP_S <= 1
    out = tmp_path / "logs" / "signals" / f"targets-{bars[-1]['date']}.json"
    assert json.loads(out.read_text(encoding="utf-8"))["bar_end"] == bars[-1]["date"]
    assert sorted(p.name for p in out.parent.iterdir()) == [out.name]


def test_publish_gives_up_after_the_retries(tmp_path, monkeypatch):
    """M6: past the retries it raises (a crash, exit 1) and leaves the previous
    targets file whole and no tmp behind."""
    reg, data, bars = _fixture_layer(tmp_path)
    out = tmp_path / "logs" / "signals" / f"targets-{bars[-1]['date']}.json"
    out.parent.mkdir(parents=True)
    out.write_text('{"previous": true}', encoding="utf-8")
    calls = []
    monkeypatch.setattr(S.os, "replace", _replace_failing(10 ** 6, calls))
    monkeypatch.setattr(S.time, "sleep", lambda s: None)
    with pytest.raises(PermissionError):
        S.run(_argv(tmp_path, reg, data))
    monkeypatch.undo()
    assert len(calls) == S.REPLACE_RETRIES + 1
    assert json.loads(out.read_text(encoding="utf-8")) == {"previous": True}
    assert sorted(p.name for p in out.parent.iterdir()) == [out.name]


# ---------------- minors item 1: per-strategy deferral ---------------------

BTC_ONLY = "btc0000000000001"


def _with_btc_only(tmp_path):
    """_fixture_layer plus a third served (quarantined) strategy that trades
    BTCUSD alone, so one lagging ETHUSD leaves something publishable."""
    reg, data, bars = _fixture_layer(tmp_path)
    reg.append("strategy_registered", _variant(BTC_ONLY, assets=["BTCUSD"]))
    _walk(reg, BTC_ONLY, "quarantine")
    return reg, data, bars


def _lag_eth(data):
    _write_csv(data / "ETHUSD_1d.csv", _bars([100.0] * 199))


def _empty_eth(data):
    _write_csv(data / "ETHUSD_1d.csv", [])


def _drop_eth(data):
    (data / "ETHUSD_1d.csv").unlink()


@pytest.mark.parametrize("break_eth,why", [
    (_lag_eth, "ETHUSD behind"), (_empty_eth, "ETHUSD EMPTY"), (_drop_eth, "no price file"),
])
def test_a_strategy_whose_asset_lags_is_deferred_the_rest_publish(tmp_path, capsys, break_eth, why):
    """Minors item 1: a strategy is published only if EVERY asset it trades
    is present, non-empty and ends on bar_end; the others are listed under
    `deferred` with the reason and are absent from `strategies` (the executor
    reads an absent strategy in a fresh file as "signal stale, holding")."""
    reg, data, bars = _with_btc_only(tmp_path)
    break_eth(data)
    assert S.run(_argv(tmp_path, reg, data)) == 0
    out = capsys.readouterr().out
    doc = json.loads((tmp_path / "logs" / "signals" / f"targets-{bars[-1]['date']}.json")
                     .read_text(encoding="utf-8"))
    assert doc["bar_end"] == bars[-1]["date"]
    assert set(doc["strategies"]) == {BTC_ONLY}
    assert doc["strategies"][BTC_ONLY]["assets"] == {"BTCUSD": S.next_bar_position(SPEC, bars)}
    assert set(doc["deferred"]) == {SPEC["strategy_id"], "live0000000000001"}
    assert all(why in r for r in doc["deferred"].values()), doc["deferred"]
    # the digest describes exactly the bytes the published targets came from
    assert doc["data_digest"] == {
        "BTCUSD": hashlib.sha256((data / "BTCUSD_1d.csv").read_bytes()).hexdigest()}
    assert f"deferred {SPEC['strategy_id']}" in out and why in out


def test_bar_end_is_the_latest_last_date_even_when_that_asset_defers_everything(tmp_path, capsys):
    """bar_end stays the max last date across served assets: ETHUSD one bar
    AHEAD makes BTCUSD the laggard, so the BTCUSD-only strategy defers too and
    nothing is publishable -> exit 2."""
    reg, data, bars = _with_btc_only(tmp_path)
    ahead = _bars([100.0] * 201)
    _write_csv(data / "ETHUSD_1d.csv", ahead)
    out = _refused(tmp_path, reg, data, capsys, as_of=_next(ahead))
    assert "no strategy can be published" in out and f"bar_end {ahead[-1]['date']}" in out
    assert BTC_ONLY in out and "BTCUSD behind" in out


def test_no_served_asset_with_a_bar_refuses(tmp_path, capsys):
    """Every served price file missing (or empty): there is no bar_end at all."""
    reg, data, _ = _fixture_layer(tmp_path)
    (data / "BTCUSD_1d.csv").unlink()
    _write_csv(data / "ETHUSD_1d.csv", [])
    out = _refused(tmp_path, reg, data, capsys)
    assert "no served asset has a bar" in out and "BTCUSD MISSING" in out and "ETHUSD EMPTY" in out


# ---------------- minors item 3: a malformed CSV row refuses ---------------

GOOD_HEAD = "date,open,high,low,close,volume"


@pytest.mark.parametrize("lines,row", [
    ([GOOD_HEAD, "2026-01-01,1,1,1,1,1", "2026-01-02,1,1,1"], 3),                 # short row
    (["\ufeff" + GOOD_HEAD, "2026-01-01,1,1,1,1,1"], 1),                          # BOM header
    ([GOOD_HEAD, "2026-01-01,1,1,1,1,1", "2026-01-02,1,abc,1,1,1"], 3),           # non-numeric
    ([GOOD_HEAD, "2026-01-01,1,1,1,1,1", "", "2026-01-03,1,1,1,nan,1"], 4),       # non-finite
    ([GOOD_HEAD, "2026-13-01,1,1,1,1,1"], 2),                                     # not a date
    ([GOOD_HEAD, "2026-01-01,1,1,1,1,1", "20260102,1,1,1,1,1"], 3),               # basic ISO form
])
def test_a_malformed_csv_row_refuses_naming_the_file_and_row(tmp_path, capsys, lines, row):
    """Minors item 3: DataRefused naming the file and the row, exit 2, nothing
    written -- never a traceback (exit 1)."""
    reg, data, _ = _fixture_layer(tmp_path)
    (data / "ETHUSD_1d.csv").write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    out = _refused(tmp_path, reg, data, capsys)
    assert "ETHUSD_1d.csv" in out and f"row {row}" in out, out


@pytest.mark.parametrize("lines,row,why", [
    ([GOOD_HEAD, "2026-01-01,1,1,1,1,1", "2026-01-02,1,1,1,1,1,9"], 3, "long row"),
    ([GOOD_HEAD, "2026-01-01,1,1,1,1,1", "2026-01-02,1,1,1,1,1",
      "2026-01-01 00:00:00,1,1,1,1,1"], 4, "duplicate date 2026-01-01 (first on row 2)"),
    ([GOOD_HEAD, "2026-01-01,1,1,1,1,1", "2026-01-02,1,1,1,0,1"], 3, "close '0' is not positive"),
    ([GOOD_HEAD, "2026-01-01,1,1,-1,1,1"], 2, "low '-1' is not positive"),
])
def test_a_long_duplicate_or_non_positive_row_refuses(tmp_path, capsys, lines, row, why):
    """Review follow-up: a row longer than the header (DictReader's None key),
    a second row for the same day, or a non-positive open/high/low/close
    refuses the run naming the file and the physical row."""
    reg, data, _ = _fixture_layer(tmp_path)
    (data / "ETHUSD_1d.csv").write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    out = _refused(tmp_path, reg, data, capsys)
    assert f"ETHUSD_1d.csv: row {row}: {why}" in out, out


def test_a_zero_volume_bar_still_parses():
    """Volume may be 0 (a quiet bar); only the prices must be positive."""
    raw = (GOOD_HEAD + "\n2026-01-01,1,1,1,1,0\n").encode("utf-8")
    assert S.parse_daily_csv(raw, "X_1d.csv")[0]["volume"] == 0.0


def test_parse_daily_csv_refuses_undecodable_bytes():
    with pytest.raises(S.DataRefused, match="X_1d.csv"):
        S.parse_daily_csv(b"date,open\n\xff\xfe\n", "X_1d.csv")


# ---------------- minors item 4: prune old targets files -------------------

def _old_targets(out_dir: Path, start: dt.date, n: int) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(n):
        p = out_dir / f"targets-{(start + dt.timedelta(days=i)).isoformat()}.json"
        p.write_text("{}", encoding="utf-8")
        paths.append(p)
    return paths


def test_publish_prunes_targets_beyond_the_newest_30(tmp_path, capsys):
    """Minors item 4: after a publish only the newest KEEP_TARGETS files by the
    bar_end in the name stay; a name without a date is not touched."""
    reg, data, bars = _fixture_layer(tmp_path)
    out_dir = tmp_path / "logs" / "signals"
    last = dt.date.fromisoformat(bars[-1]["date"])
    old = _old_targets(out_dir, last - dt.timedelta(days=33), 33)    # ends the day before
    other = out_dir / "targets-notes.json"
    other.write_text("{}", encoding="utf-8")
    assert S.run(_argv(tmp_path, reg, data)) == 0
    assert S.KEEP_TARGETS == 30
    kept = sorted(p.name for p in out_dir.glob("targets-*.json") if p != other)
    want = sorted([p.name for p in old[-29:]] + [f"targets-{bars[-1]['date']}.json"])
    assert kept == want
    assert other.exists()
    assert "pruned 4" in capsys.readouterr().out


def test_prune_never_removes_the_file_just_written(tmp_path):
    """Leftover files dated AFTER this bar_end (a re-run for an older day) are
    newer by name; the just-written file is kept anyway."""
    reg, data, bars = _fixture_layer(tmp_path)
    out_dir = tmp_path / "logs" / "signals"
    last = dt.date.fromisoformat(bars[-1]["date"])
    later = _old_targets(out_dir, last + dt.timedelta(days=1), 31)
    assert S.run(_argv(tmp_path, reg, data)) == 0
    assert (out_dir / f"targets-{bars[-1]['date']}.json").exists()
    assert not later[0].exists() and all(p.exists() for p in later[1:])


def test_prune_logs_and_skips_a_file_it_cannot_delete(tmp_path, monkeypatch, capsys):
    reg, data, bars = _fixture_layer(tmp_path)
    out_dir = tmp_path / "logs" / "signals"
    last = dt.date.fromisoformat(bars[-1]["date"])
    old = _old_targets(out_dir, last - dt.timedelta(days=32), 32)    # 33 with the new one
    held = old[0]
    real_unlink = Path.unlink

    def unlink(self, *a, **k):
        if self == held:
            raise PermissionError(13, "in use by another process")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", unlink)
    assert S.run(_argv(tmp_path, reg, data)) == 0
    monkeypatch.undo()
    assert held.exists() and not old[1].exists() and not old[2].exists()
    assert all(p.exists() for p in old[3:])
    out = capsys.readouterr().out
    assert f"could not prune {held.name}" in out


def test_a_refusal_prunes_nothing(tmp_path):
    reg, data, bars = _fixture_layer(tmp_path)
    out_dir = tmp_path / "logs" / "signals"
    last = dt.date.fromisoformat(bars[-1]["date"])
    old = _old_targets(out_dir, last - dt.timedelta(days=40), 35)
    _lag_eth(data)
    assert S.run(_argv(tmp_path, reg, data)) == 2
    assert all(p.exists() for p in old)


# ---------------- minors item 5: the wrapper runs under the task guard -----

TASKS = PIPELINE.parent / "tasks"
WRAPPER = TASKS / "run_signal_service.bat"
LIVE_LAYER = r"E:\Users\Coen\Claude\stewart-forward-test\research-layer"


def test_the_wrapper_is_crlf_guarded_and_gitless():
    raw = WRAPPER.read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n") > 0                # CRLF throughout
    text = raw.decode("ascii")
    lines = [ln for ln in text.splitlines() if not ln.lower().startswith("rem")]
    body = "\n".join(lines)
    assert r"set PY=C:\Users\Coen\AppData\Local\Python\bin\python.exe" in body
    assert 'if "%RL_TASKGUARD%"=="1" goto :guarded' in body
    guard = [ln for ln in lines if "pipeline.taskguard" in ln]
    assert len(guard) == 1 and guard[0].startswith('"%PY%" -m pipeline.taskguard --wall-s 1500 ')
    assert guard[0].rstrip().endswith('-- cmd /d /c "%~f0"')
    assert lines[lines.index(guard[0]) + 1].strip() == "exit /b %ERRORLEVEL%"
    assert '"%PY%" -m pipeline.signal_service' in body
    assert "git" not in body.lower()
    assert " python " not in f" {body} "                                  # never bare python


def _wrapper_scratch(tmp_path, stub: str) -> tuple[Path, Path]:
    layer = tmp_path / "layer"
    pkg = layer / "pipeline"
    pkg.mkdir(parents=True)
    (layer / "tasks").mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "taskguard.py").write_bytes((PIPELINE / "taskguard.py").read_bytes())
    (pkg / "signal_service.py").write_text(stub, encoding="utf-8")
    text = WRAPPER.read_bytes().decode("ascii")
    assert text.count(LIVE_LAYER) == 1
    bat = layer / "tasks" / WRAPPER.name
    bat.write_bytes(text.replace(LIVE_LAYER, str(layer)).encode("ascii"))
    return layer, bat


def _bat_env():
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    env.pop("RL_TASKGUARD", None)
    return env


@pytest.mark.skipif(os.name != "nt", reason="runs the .bat under cmd.exe")
def test_the_wrapper_passes_the_services_exit_code_through_the_guard(tmp_path):
    stub = "import os\nprint('stub', os.environ.get('RL_TASKGUARD'))\nraise SystemExit(2)\n"
    layer, bat = _wrapper_scratch(tmp_path, stub)
    r = subprocess.run(["cmd", "/c", str(bat)], env=_bat_env(), capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 2, r.stdout + r.stderr
    log = (layer / "logs" / "signal-run.log").read_text(encoding="utf-8")
    assert "stub 1" in log                                   # ran as the guard's inner copy
    assert log.splitlines()[-1].strip().endswith("exit 2 ====")


@pytest.mark.skipif(os.name != "nt", reason="runs the .bat under cmd.exe")
def test_the_wall_ends_an_overrunning_service_and_the_next_fire_logs(tmp_path):
    """An overrun is killed at the wall (exit 124, its grandchild too) and its
    log handle is released: the next fire opens the log and runs."""
    import re
    from .test_taskguard import _alive, _kill, _wait_dead
    sleeper = (
        "import os, subprocess, sys, time\n"
        f"d = {str(tmp_path)!r}\n"
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])\n"
        "open(os.path.join(d, 'grandchild.pid'), 'w').write(str(g.pid))\n"
        "time.sleep(300)\n")
    layer, bat = _wrapper_scratch(tmp_path, sleeper)
    text, n = re.subn(r"--wall-s 1500 ", "--wall-s 4 ", bat.read_bytes().decode("ascii"))
    assert n == 1
    bat.write_bytes(text.encode("ascii"))
    r = subprocess.run(["cmd", "/c", str(bat)], env=_bat_env(), timeout=120)
    g = int((tmp_path / "grandchild.pid").read_text())
    try:
        assert r.returncode == 124
        assert _wait_dead(g)
    finally:
        if _alive(g):
            _kill(g)
    log = layer / "logs" / "signal-run.log"
    assert "taskguard: wall 4s reached" in log.read_text(encoding="utf-8").splitlines()[-1]
    (layer / "pipeline" / "signal_service.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    r = subprocess.run(["cmd", "/c", str(bat)], env=_bat_env(), timeout=120)
    assert r.returncode == 0
    assert log.read_text(encoding="utf-8").splitlines()[-1].strip().endswith("exit 0 ====")
