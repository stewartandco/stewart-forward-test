# research-layer/pipeline/test_gauntlet_stats.py
import json
import shutil
from datetime import date

from . import gauntlet_stats as gs


def test_vintage_is_the_last_sunday():
    assert gs.vintage_date(date(2026, 9, 30)) == "2026-09-27"   # Wednesday
    assert gs.vintage_date(date(2026, 9, 27)) == "2026-09-27"   # Sunday itself


def test_without_chain_flag_nothing_is_written(tmp_path):
    from .test_gauntlet_worker import _setup, _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    n = sum(1 for _ in reg.entries())
    rc = gs.run(["--registry", str(reg.log_path), "--data-dir", str(data),
                 "--logs-dir", str(tmp_path / "logs")])
    assert rc == 0 and sum(1 for _ in reg.entries()) == n


def test_with_chain_flag_every_v61_verdict_gets_one_linked_entry(tmp_path):
    from .test_gauntlet import run_verifier
    from .test_gauntlet_worker import _setup, _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    args = ["--registry", str(reg.log_path), "--data-dir", str(data),
            "--logs-dir", str(tmp_path / "logs"), "--chain"]
    assert gs.run(args) == 0
    assert gs.run(args) == 0                          # idempotent second night
    stats = [e for e in reg.entries() if e["entry_type"] == "gauntlet_stats"]
    assert len(stats) == 1
    assert {"trials_n", "registered_n", "deflated_sharpe", "pbo", "pbo_percentile",
            "cluster_method", "data_vintage", "plateau_ok", "haircut"} <= set(stats[0]["payload"])
    assert run_verifier(reg.log_path).returncode == 0


def test_the_stats_job_uses_the_gauntlets_own_clustering(tmp_path, monkeypatch):
    """The method is unchanged: the stats job calls gauntlet.cluster_registry
    (the function gauntlet.run itself uses) and reports its trials_n."""
    from . import gauntlet
    from .test_gauntlet_worker import _setup, _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    seen = {}
    real = gauntlet.cluster_registry
    def spy(*a, **k):
        out = real(*a, **k)
        seen.update(out)
        return out
    monkeypatch.setattr(gs, "cluster_registry", spy)
    rep = tmp_path / "report.md"
    assert gs.run(["--registry", str(reg.log_path), "--data-dir", str(data),
                   "--logs-dir", str(tmp_path / "logs"), "--report", str(rep)]) == 0
    assert seen, "the stats job did not call gauntlet.cluster_registry"
    assert f"| trials_n | {seen['trials_n']} |" in rep.read_text(encoding="utf-8")


# ---------------- beyond the brief ----------------

STATUS_KEYS = {"ts_utc", "vintage", "verdicts_without_stats", "stats_written",
               "oldest_unstatted_verdict_age_hours", "trials_n", "registered_n",
               "trials_common_days", "stopped_at_deadline", "chained"}


def _status(tmp_path):
    return json.loads((tmp_path / "logs" / "gauntlet_stats_status.json")
                      .read_text(encoding="utf-8"))


def test_status_reports_progress_and_a_second_night_finds_nothing(tmp_path):
    from .test_gauntlet_worker import _setup, _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    args = ["--registry", str(reg.log_path), "--data-dir", str(data),
            "--logs-dir", str(tmp_path / "logs"), "--chain"]
    assert gs.run(args) == 0
    st = _status(tmp_path)
    assert STATUS_KEYS <= set(st)
    assert st["stats_written"] == 1 and st["verdicts_without_stats"] == 0
    assert st["chained"] is True and st["stopped_at_deadline"] is False
    assert st["vintage"] == gs.vintage_date(date.today())
    assert gs.run(args) == 0
    st = _status(tmp_path)
    assert st["stats_written"] == 0 and st["verdicts_without_stats"] == 0
    # a report-only night after it sees no backlog either: the job's own read
    # excludes statted verdicts, it does not lean on the writer's re-check
    assert gs.run(args[:-1]) == 0
    st = _status(tmp_path)
    assert st["verdicts_without_stats"] == 0 and st["exit_reason"] == "nothing_to_do"


def test_report_only_status_counts_the_backlog(tmp_path):
    from .test_gauntlet_worker import _setup, _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    assert gs.run(["--registry", str(reg.log_path), "--data-dir", str(data),
                   "--logs-dir", str(tmp_path / "logs")]) == 0
    st = _status(tmp_path)
    assert st["chained"] is False and st["stats_written"] == 0
    assert st["verdicts_without_stats"] == 1
    assert st["oldest_unstatted_verdict_age_hours"] is not None


def test_stats_chained_by_someone_else_mid_run_are_not_duplicated(tmp_path, monkeypatch):
    """The chain is re-checked under chain.lock: a gauntlet_stats entry that
    lands between the job's read and its write is not written twice."""
    from . import gauntlet
    from .common import entry_hash
    from .test_gauntlet import run_verifier
    from .test_gauntlet_worker import _setup, _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    v = next(e for e in reg.entries() if e["entry_type"] == "verdict"
             and e["payload"]["stage"] == "gauntlet")
    real = gauntlet.cluster_registry
    def racing(*a, **k):
        out = real(*a, **k)
        reg.record_gauntlet_stats(spec["strategy_id"], entry_hash(v), {"trials_n": 1})
        return out
    monkeypatch.setattr(gs, "cluster_registry", racing)
    assert gs.run(["--registry", str(reg.log_path), "--data-dir", str(data),
                   "--logs-dir", str(tmp_path / "logs"), "--chain"]) == 0
    stats = [e for e in reg.entries() if e["entry_type"] == "gauntlet_stats"]
    assert len(stats) == 1 and stats[0]["payload"] == {
        "strategy_id": spec["strategy_id"], "verdict_entry_hash": entry_hash(v),
        "trials_n": 1}
    assert run_verifier(reg.log_path).returncode == 0
    assert _status(tmp_path)["stats_written"] == 0


def test_a_deadline_stop_writes_nothing_and_exits_0(tmp_path):
    from .test_gauntlet_worker import _setup, _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    n = sum(1 for _ in reg.entries())
    assert gs.run(["--registry", str(reg.log_path), "--data-dir", str(data),
                   "--logs-dir", str(tmp_path / "logs"), "--chain",
                   "--deadline-hours", "0"]) == 0
    assert sum(1 for _ in reg.entries()) == n
    st = _status(tmp_path)
    assert st["stopped_at_deadline"] is True and st["verdicts_without_stats"] == 1


def test_no_verdicts_writes_a_status_and_exits_0(tmp_path):
    from .test_gauntlet_worker import _setup
    reg, spec, data = _setup(tmp_path)
    assert gs.run(["--registry", str(reg.log_path), "--data-dir", str(data),
                   "--logs-dir", str(tmp_path / "logs"), "--chain"]) == 0
    st = _status(tmp_path)
    assert st["verdicts_without_stats"] == 0 and st["stats_written"] == 0


def test_the_weekly_vintage_truncates_the_bars(tmp_path, monkeypatch):
    """Bars after the vintage Sunday are not simulated: a week of data
    appended after it leaves every simcache key unchanged."""
    from .test_gauntlet_worker import _setup, _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    monkeypatch.setattr(gs, "vintage_date", lambda today: "2023-01-20")
    args = ["--registry", str(reg.log_path), "--data-dir", str(data),
            "--logs-dir", str(tmp_path / "logs")]
    assert gs.run(args) == 0
    keys = sorted(p.name for p in (tmp_path / "simcache").iterdir())
    with (data / "BTCUSD_1d.csv").open("a", encoding="utf-8", newline="") as f:
        f.write("2023-01-24,118,118,118,118,1.0\r\n")      # after the vintage
    assert gs.run(args) == 0
    assert sorted(p.name for p in (tmp_path / "simcache").iterdir()) == keys
    assert _status(tmp_path)["vintage"] == "2023-01-20"


def test_recorded_stats_match_the_v6_gauntlets_own_numbers(tmp_path, capsys):
    """Same registry, same bars (the fixture ends before any vintage Sunday,
    so truncation is a no-op): the stats job must record exactly what
    gauntlet.run recorded in a v6 verdict for every registry-wide figure --
    effective trials, the deflated Sharpe, PBO, plateau_ok and the haircut."""
    from .gauntlet import run as gauntlet_run
    from .registry import Registry
    from .test_gauntlet import v4_sweep_registry, v4_bars, V4_CUTOFF, run_verifier
    from .test_gauntlet_worker import V61
    from .test_screen import write_data_dir
    from . import gauntlet_worker as gw
    source, by_lb = v4_sweep_registry(tmp_path)
    old, new = tmp_path / "v6", tmp_path / "v61"
    for d in (old, new):
        d.mkdir()
        shutil.copyfile(source.log_path, d / "reg.jsonl")
        write_data_dir(d, {"BTCUSD": v4_bars()})
    assert gauntlet_run(["--registry", str(old / "reg.jsonl"),
                         "--data-dir", str(old / "data"),
                         "--artifacts-dir", str(old / "art"),
                         "--cutoff", V4_CUTOFF, "--no-perturb"]) == 0
    r61 = Registry(new / "reg.jsonl")
    r61.append("note", {"text": V61})
    assert gw.run(["--registry", str(r61.log_path), "--data-dir", str(new / "data"),
                   "--artifacts-dir", str(new / "art"), "--logs-dir", str(new / "logs"),
                   "--cutoff", V4_CUTOFF, "--no-perturb", "--max-workers", "1"]) == 0
    assert gs.run(["--registry", str(r61.log_path), "--data-dir", str(new / "data"),
                   "--logs-dir", str(new / "logs"), "--cutoff", V4_CUTOFF,
                   "--chain"]) == 0
    capsys.readouterr()
    v6 = {e["payload"]["strategy_id"]: e["payload"]["metrics"]
          for e in Registry(old / "reg.jsonl").entries()
          if e["entry_type"] == "verdict" and e["payload"]["stage"] == "gauntlet"}
    st = {e["payload"]["strategy_id"]: e["payload"] for e in r61.entries()
          if e["entry_type"] == "gauntlet_stats"}
    assert set(st) == set(v6) == set(by_lb.values())
    passed = 0
    for sid, m in v6.items():
        s = st[sid]
        for k in ("trials_n", "registered_n", "deflated_sharpe", "plateau_ok",
                  "pbo", "pbo_percentile", "pbo_n_distinct", "pbo_null_p05",
                  "pbo_null_p95", "pbo_null_draws", "trials_sr_var",
                  "expected_max_sharpe", "trials_alignment", "trials_common_days"):
            assert s[k] == m[k], (sid, k, s[k], m[k])
        h = dict(m["haircut"])
        assert s["haircut"] == h, (sid, s["haircut"], h)
        assert "pbo_family_kill" not in s
        passed += m["haircut"]["sr_observed"] > 0
    assert passed, "the fixture must exercise a non-trivial haircut"
    r = run_verifier(r61.log_path)
    assert r.returncode == 0, r.stdout


def test_group_pbo_records_values_and_never_a_gate_label():
    import random
    rng = random.Random(7)
    fam = [{"sid": f"s{i}"} for i in range(6)]
    train = {f"s{i}": [rng.gauss(0.0005 * i, 0.01) for _ in range(400)]
             for i in range(6)}
    dead = gs.group_pbo("g", fam, train, live=False, draws=10)
    assert dead["pbo_status"] == "not_measured_dead_group"
    assert dead["pbo_percentile"] is None and dead["pbo_null_draws"] == 0
    live = gs.group_pbo("g", fam, train, live=True, draws=10)
    assert live["pbo_status"] == "measured" and live["pbo_null_draws"] == 10
    assert 0.0 <= live["pbo_percentile"] <= 1.0
    assert live["pbo"] == dead["pbo"] and live["pbo_n_distinct"] == 6
    # seeded off the group id, as gauntlet.run seeds it: same group, same null
    assert gs.group_pbo("g", fam, train, live=True, draws=10) == live
    same = {f"s{i}": train["s0"] for i in range(6)}
    assert gs.group_pbo("g", fam, same, live=True, draws=10)["pbo_status"] == "underpowered"
    for out in (dead, live):
        assert "pbo_family_kill" not in out and "pbo_verdict" not in out


def test_a_crash_leaves_a_status_saying_so(tmp_path, monkeypatch):
    import pytest
    from .test_gauntlet_worker import _setup, _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    def boom(*a, **k):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(gs, "cluster_registry", boom)
    with pytest.raises(RuntimeError):
        gs.run(["--registry", str(reg.log_path), "--data-dir", str(data),
                "--logs-dir", str(tmp_path / "logs"), "--chain"])
    st = _status(tmp_path)
    assert st["exit_reason"] == "crashed" and st["verdicts_without_stats"] == 1


def _walk_weekday_bars(start, end, seed=11, price=1.30):
    """Weekday fx bars with a seeded random walk, so ma_cross trades and the
    strategy's returns differ between its full history and the window it
    shares with the other classes."""
    import datetime as dt
    import random
    rng = random.Random(seed)
    bars, d = [], start
    while d <= end:
        if d.weekday() < 5:
            o = price
            price = max(0.5, price * (1 + rng.gauss(0.0002, 0.006)))
            bars.append({"date": d.isoformat(), "open": o,
                         "high": max(o, price) * 1.001, "low": min(o, price) * 0.999,
                         "close": price, "volume": 0.0})
        d += dt.timedelta(days=1)
    return bars


def test_deflated_sharpe_uses_v6s_own_window_on_a_mixed_class_registry(tmp_path, capsys):
    """Ruling 11: crypto + two fx strategies with unequal history starts, so
    clustering intersects and the DSR input is the intersection-trimmed
    series, not the strategy's own full history. The job must record exactly
    the deflated Sharpe gauntlet.run recorded for every strategy, and the
    alignment it was computed on."""
    import datetime as dt
    from .gauntlet import run as gauntlet_run
    from .registry import Registry
    from .test_gauntlet import run_verifier
    from .test_gauntlet_classes import (mixed_class_gauntlet_registry,
                                        _daily_bars, _weekday_bars)
    from .test_gauntlet_worker import V61
    from .test_screen import write_data_dir
    from . import gauntlet_worker as gw
    src = tmp_path / "src"
    src.mkdir()
    source, crypto_spec, gbp_spec, eur_spec = mixed_class_gauntlet_registry(src)
    bars = {"BTCUSD": _daily_bars(dt.date(2020, 1, 1), dt.date(2020, 12, 31)),
            "GBP": _walk_weekday_bars(dt.date(2015, 1, 1), dt.date(2020, 12, 31)),
            "EUR": _weekday_bars(dt.date(2020, 1, 1), dt.date(2020, 12, 31))}
    cutoff = "2020-06-30"
    old, new = tmp_path / "v6", tmp_path / "v61"
    for d in (old, new):
        d.mkdir()
        shutil.copyfile(source.log_path, d / "reg.jsonl")
        write_data_dir(d, bars)
    assert gauntlet_run(["--registry", str(old / "reg.jsonl"),
                         "--data-dir", str(old / "data"),
                         "--artifacts-dir", str(old / "art"),
                         "--cutoff", cutoff, "--no-perturb"]) == 0
    r61 = Registry(new / "reg.jsonl")
    r61.append("note", {"text": V61})
    assert gw.run(["--registry", str(r61.log_path), "--data-dir", str(new / "data"),
                   "--artifacts-dir", str(new / "art"), "--logs-dir", str(new / "logs"),
                   "--cutoff", cutoff, "--no-perturb", "--max-workers", "1"]) == 0
    assert gs.run(["--registry", str(r61.log_path), "--data-dir", str(new / "data"),
                   "--logs-dir", str(new / "logs"), "--cutoff", cutoff,
                   "--chain"]) == 0
    capsys.readouterr()
    v6 = {e["payload"]["strategy_id"]: e["payload"]["metrics"]
          for e in Registry(old / "reg.jsonl").entries()
          if e["entry_type"] == "verdict" and e["payload"]["stage"] == "gauntlet"}
    st = {e["payload"]["strategy_id"]: e["payload"] for e in r61.entries()
          if e["entry_type"] == "gauntlet_stats"}
    assert set(st) == set(v6) and len(st) == 3
    gbp = gbp_spec["strategy_id"]
    assert v6[gbp]["trials_alignment"] == "intersection"
    for sid, m in v6.items():
        for k in ("deflated_sharpe", "trials_n", "trials_sr_var",
                  "expected_max_sharpe", "trials_alignment", "trials_common_days"):
            assert st[sid][k] == m[k], (sid, k, st[sid][k], m[k])
    r = run_verifier(r61.log_path)
    assert r.returncode == 0, r.stdout
