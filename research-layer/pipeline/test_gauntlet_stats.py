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
    assert f"| trials_n_raw | {seen['trials_n']} |" in rep.read_text(encoding="utf-8")


# ---------------- beyond the brief ----------------

STATUS_KEYS = {"ts_utc", "vintage", "verdicts_without_stats", "stats_written",
               "oldest_unstatted_verdict_age_hours", "trials_n_raw",
               "trials_n_effective", "registered_n", "trials_common_days",
               "stopped_at_deadline", "chained", "cluster_s", "pbo_null_mean_s"}


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


# ---------------- fix round (Ruling 14): pin the data, not just the date ----------------

def _mixed_twin(tmp_path, name, drop_eur_row):
    """The mixed-class registry judged by the worker, then statted with
    --chain, on bars that are identical except that one EUR row (a weekday
    long BEFORE any vintage Sunday) can be withheld -- an fx row that arrives
    late, as FRED's do. A mid-history row, not the last one: withholding the
    last would end EUR a day before GBP, and the same-class same-day rule
    (assert_cells_comparable) refuses that run outright."""
    import datetime as dt
    from .registry import Registry
    from .test_gauntlet_classes import (mixed_class_gauntlet_registry,
                                        _daily_bars, _weekday_bars)
    from .test_gauntlet_worker import V61
    from .test_screen import write_data_dir
    from . import gauntlet_worker as gw
    # ONE source registry, copied: the fixture's card_id hashes the clock,
    # so registries built separately can carry different strategy ids
    src = tmp_path / "src"
    if not (src / "reg.jsonl").exists():
        src.mkdir(exist_ok=True)
        _, c, g, e = mixed_class_gauntlet_registry(src)
        (src / "sids.json").write_text(json.dumps(
            {"crypto": c["strategy_id"], "gbp": g["strategy_id"],
             "eur": e["strategy_id"]}), encoding="utf-8")
    sids = json.loads((src / "sids.json").read_text(encoding="utf-8"))
    d = tmp_path / name
    d.mkdir()
    shutil.copyfile(src / "reg.jsonl", d / "reg.jsonl")
    reg = Registry(d / "reg.jsonl")
    reg.append("note", {"text": V61})
    eur = _weekday_bars(dt.date(2020, 1, 1), dt.date(2020, 12, 31))
    if drop_eur_row:
        eur = [b for b in eur if b["date"] != "2020-03-02"]
    data = write_data_dir(d, {
        "BTCUSD": _daily_bars(dt.date(2020, 1, 1), dt.date(2020, 12, 31)),
        "GBP": _walk_weekday_bars(dt.date(2015, 1, 1), dt.date(2020, 12, 31)),
        "EUR": eur})
    cutoff = "2020-06-30"
    assert gw.run(["--registry", str(reg.log_path), "--data-dir", str(data),
                   "--artifacts-dir", str(d / "art"), "--logs-dir", str(d / "logs"),
                   "--cutoff", cutoff, "--no-perturb", "--max-workers", "1"]) == 0
    assert gs.run(["--registry", str(reg.log_path), "--data-dir", str(data),
                   "--logs-dir", str(d / "logs"), "--cutoff", cutoff, "--chain"]) == 0
    stats = {e["payload"]["strategy_id"]: e["payload"]
             for e in Registry(reg.log_path).entries()
             if e["entry_type"] == "gauntlet_stats"}
    return stats, sids


def test_a_late_fx_row_changes_the_digest_not_the_vintage(tmp_path, capsys):
    late, sids = _mixed_twin(tmp_path, "late", drop_eur_row=True)
    full, _ = _mixed_twin(tmp_path, "full", drop_eur_row=False)
    again, _ = _mixed_twin(tmp_path, "again", drop_eur_row=False)
    capsys.readouterr()
    crypto = sids["crypto"]
    # (a) same vintage, different bars -> different digest, on EVERY entry,
    # including the crypto strategy whose own cell did not change
    assert late[crypto]["data_vintage"] == full[crypto]["data_vintage"]
    assert late[crypto]["data_digest"] != full[crypto]["data_digest"]
    # (b) identical bars -> identical digest
    assert full[crypto]["data_digest"] == again[crypto]["data_digest"]
    # one digest per run: identical across that run's entries
    for run_stats in (late, full):
        assert len({p["data_digest"] for p in run_stats.values()}) == 1
        assert all(len(p["data_digest"]) == 64 for p in run_stats.values())


def test_each_entry_records_its_own_cells_data_end(tmp_path, capsys):
    late, sids = _mixed_twin(tmp_path, "late", drop_eur_row=True)
    capsys.readouterr()
    # (c) the strategy's OWN cells only, at the truncated data end
    assert late[sids["crypto"]]["data_end_by_cell"] == {"BTCUSD_1d": "2020-12-31"}
    assert late[sids["gbp"]]["data_end_by_cell"] == {"GBP_1d": "2020-12-31"}
    assert late[sids["eur"]]["data_end_by_cell"] == {"EUR_1d": "2020-12-31"}


def test_no_clustering_starts_after_the_deadline(tmp_path, monkeypatch):
    """(d) the deadline passes during the LAST series chunk: the job must
    stop with a status before cluster_registry, never start a pass the PT6H
    wall would kill with no status written."""
    import time as real_time
    from . import gauntlet
    from .test_gauntlet_worker import _setup, _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    expired = {"now": False}
    real_series = gauntlet.registry_series

    def series_then_expire(*a, **k):
        out = real_series(*a, **k)
        expired["now"] = True                 # the chunk overran the deadline
        return out

    class _Clock:
        @staticmethod
        def time():
            return real_time.time() + (10 ** 9 if expired["now"] else 0)

    called = []
    monkeypatch.setattr(gs, "registry_series", series_then_expire)
    monkeypatch.setattr(gs, "time", _Clock)
    monkeypatch.setattr(gs, "cluster_registry",
                        lambda *a, **k: called.append(1) or gauntlet.cluster_registry(*a, **k))
    n = sum(1 for _ in reg.entries())
    assert gs.run(["--registry", str(reg.log_path), "--data-dir", str(data),
                   "--logs-dir", str(tmp_path / "logs"), "--chain"]) == 0
    assert expired["now"] and not called
    st = _status(tmp_path)
    assert st["stopped_at_deadline"] is True and st["exit_reason"] == "deadline"
    assert st["verdicts_without_stats"] == 1
    assert sum(1 for _ in reg.entries()) == n


# ------- Ruling 20 (Coen, option 1): effective trials floored at the chain max -------

def _v61_sweep(tmp_path):
    """The v4 sweep registry judged by the v6.1 worker; NOT yet statted.
    Returns (registry, data dir, logs dir, cutoff)."""
    from .registry import Registry
    from .test_gauntlet import v4_sweep_registry, v4_bars, V4_CUTOFF
    from .test_gauntlet_worker import V61
    from .test_screen import write_data_dir
    from . import gauntlet_worker as gw
    source, _ = v4_sweep_registry(tmp_path)
    d = tmp_path / "floor"
    d.mkdir()
    shutil.copyfile(source.log_path, d / "reg.jsonl")
    write_data_dir(d, {"BTCUSD": v4_bars()})
    reg = Registry(d / "reg.jsonl")
    reg.append("note", {"text": V61})
    assert gw.run(["--registry", str(reg.log_path), "--data-dir", str(d / "data"),
                   "--artifacts-dir", str(d / "art"), "--logs-dir", str(d / "logs"),
                   "--cutoff", V4_CUTOFF, "--no-perturb", "--max-workers", "1"]) == 0
    return reg, d / "data", d / "logs", V4_CUTOFF


def _stat_args(reg, data, logs, cutoff):
    return ["--registry", str(reg.log_path), "--data-dir", str(data),
            "--logs-dir", str(logs), "--cutoff", cutoff, "--chain"]


def _chain_verdict(reg, sid, metrics, stage="gauntlet"):
    """Append a gauntlet-stage verdict with `metrics`; returns its entry hash."""
    from .common import entry_hash
    e = reg.append("verdict", {"strategy_id": sid, "stage": stage, "verdict": "fail",
                               "metrics": metrics, "artifacts_hash": "0" * 64})
    return entry_hash(e)


def _a_sid(reg):
    return next(e["payload"]["strategy_id"] for e in reg.entries()
                if e["entry_type"] == "strategy_registered")


def _stats_payloads(reg):
    return [e["payload"] for e in reg.entries() if e["entry_type"] == "gauntlet_stats"]


def _spy_clustering(monkeypatch, trials_n=None):
    """Capture cluster_registry's output (the job's own call); optionally force
    its raw k, leaving trials_var as that run's clustering computed it."""
    from . import gauntlet
    seen = {}
    real = gauntlet.cluster_registry

    def spy(*a, **k):
        out = real(*a, **k)
        if trials_n is not None:
            out = {**out, "trials_n": trials_n}
        seen.clear()
        seen.update(out)
        return out
    monkeypatch.setattr(gs, "cluster_registry", spy)
    return seen


def test_trials_n_is_floored_at_a_v6_verdicts_cluster_count(tmp_path, monkeypatch, capsys):
    """A v6 verdict on the chain recorded trials_n = 302; this run's argmax is
    smaller. The entry records raw, floor, the floor entry's hash and
    trials_n = 302, and the deflated Sharpe AND the haircut use 302 -- not
    the raw k."""
    import pytest
    from .stats import (moments, sharpe, psr, expected_max_sharpe,
                        harvey_liu_haircut, inv_normal_cdf)
    from .test_gauntlet import run_verifier
    reg, data, logs, cutoff = _v61_sweep(tmp_path)
    floor_hash = _chain_verdict(reg, _a_sid(reg),
                                {"protocol": "gauntlet-protocol-v6", "trials_n": 302})
    seen = _spy_clustering(monkeypatch)
    assert gs.run(_stat_args(reg, data, logs, cutoff)) == 0
    capsys.readouterr()
    raw = seen["trials_n"]
    assert 1 < raw < 302 and seen["trials_var"] > 0
    stats = _stats_payloads(reg)
    assert len(stats) > 1
    differs_dsr = differs_haircut = 0
    for p in stats:
        assert p["trials_n_raw"] == raw
        assert p["trials_n_floor"] == 302 and p["trials_n_floor_entry_hash"] == floor_hash
        assert p["trials_n"] == 302
        assert p["trials_sr_var"] == seen["trials_var"]          # this run's clustering
        r = list(seen["returns_by_id"][p["strategy_id"]])
        _, _, skew, kurt = moments(r)
        sr_hat = sharpe(r)
        floored = psr(sr_hat, expected_max_sharpe(302, seen["trials_var"]),
                      len(r), skew, kurt)
        at_raw = psr(sr_hat, expected_max_sharpe(raw, seen["trials_var"]),
                     len(r), skew, kurt)
        assert p["expected_max_sharpe"] == expected_max_sharpe(302, seen["trials_var"])
        assert p["deflated_sharpe"] == floored
        differs_dsr += floored != at_raw
        h = p["haircut"]
        if h["sr_observed"] > 0 and h["p_raw"] is not None:
            # recover the train t_years from the recorded p_raw, then redo the
            # haircut at the floor and at the raw k
            t_stat = inv_normal_cdf(1.0 - h["p_raw"] / 2.0)
            t_years = (t_stat / h["sr_observed"]) ** 2
            at_floor = harvey_liu_haircut(h["sr_observed"], t_years, 302)
            at_raw_h = harvey_liu_haircut(h["sr_observed"], t_years, raw)
            assert h["p_adjusted"] == min(1.0, h["p_raw"] * 302)
            assert h["sr_haircut"] == pytest.approx(at_floor["sr_haircut"], abs=1e-6)
            differs_haircut += h["sr_haircut"] != pytest.approx(
                at_raw_h["sr_haircut"], abs=1e-6)
    assert differs_dsr, "the fixture must separate the floored DSR from the raw-k DSR"
    assert differs_haircut, "the fixture must separate the floored haircut from the raw-k one"
    assert run_verifier(reg.log_path).returncode == 0


def test_a_verdict_without_a_cluster_count_protocol_is_no_floor(tmp_path, monkeypatch, capsys):
    """Protocol-None and protocol-v2 verdicts hold REGISTRATION counts (and a
    v6.1 verdict holds no trials_n): all excluded, so with no v3+ verdict and
    no earlier stats entry the floor is None and trials_n is the raw k. A bool
    or a string under a qualifying protocol is not an int and is ignored."""
    reg, data, logs, cutoff = _v61_sweep(tmp_path)
    sid = _a_sid(reg)
    _chain_verdict(reg, sid, {"trials_n": 56})                         # no protocol
    _chain_verdict(reg, sid, {"protocol": "gauntlet-protocol-v2", "trials_n": 99})
    _chain_verdict(reg, sid, {"protocol": "gauntlet-protocol-v6.1", "trials_n": 400})
    _chain_verdict(reg, sid, {"protocol": "gauntlet-protocol-v6", "trials_n": True})
    _chain_verdict(reg, sid, {"protocol": "gauntlet-protocol-v5", "trials_n": "302"})
    _chain_verdict(reg, sid, {"protocol": "gauntlet-protocol-v6", "trials_n": 777},
                   stage="screened")                                   # not gauntlet-stage
    seen = _spy_clustering(monkeypatch)
    assert gs.run(_stat_args(reg, data, logs, cutoff)) == 0
    capsys.readouterr()
    stats = _stats_payloads(reg)
    assert stats
    for p in stats:
        assert p["trials_n_raw"] == p["trials_n"] == seen["trials_n"]
        assert p["trials_n_floor"] is None and p["trials_n_floor_entry_hash"] is None


def test_a_raw_k_above_the_floor_stands(tmp_path, monkeypatch, capsys):
    reg, data, logs, cutoff = _v61_sweep(tmp_path)
    floor_hash = _chain_verdict(reg, _a_sid(reg),
                                {"protocol": "gauntlet-protocol-v3", "trials_n": 1})
    seen = _spy_clustering(monkeypatch)
    assert gs.run(_stat_args(reg, data, logs, cutoff)) == 0
    capsys.readouterr()
    assert seen["trials_n"] > 1
    for p in _stats_payloads(reg):
        assert p["trials_n_raw"] == p["trials_n"] == seen["trials_n"]
        assert p["trials_n_floor"] == 1 and p["trials_n_floor_entry_hash"] == floor_hash


def test_the_floor_is_monotone_across_runs(tmp_path, monkeypatch, capsys):
    """A second run's floor includes the first run's trials_n_raw (no v3+
    verdict at all on this chain), so a smaller second argmax cannot lower N."""
    from .common import entry_hash
    from .test_gauntlet import run_verifier
    reg, data, logs, cutoff = _v61_sweep(tmp_path)
    _spy_clustering(monkeypatch, trials_n=77)            # run 1's raw k
    assert gs.run(_stat_args(reg, data, logs, cutoff)) == 0
    first = [e for e in reg.entries() if e["entry_type"] == "gauntlet_stats"]
    assert first and all(e["payload"]["trials_n_raw"] == 77 for e in first)
    assert all(e["payload"]["trials_n_floor"] is None for e in first)
    # a new v6.1 verdict to stat on the second night
    _chain_verdict(reg, _a_sid(reg), {"protocol": "gauntlet-protocol-v6.1"})
    seen = _spy_clustering(monkeypatch)                  # run 2: the real, smaller k
    assert gs.run(_stat_args(reg, data, logs, cutoff)) == 0
    capsys.readouterr()
    assert seen["trials_n"] < 77
    new = _stats_payloads(reg)[len(first):]
    assert len(new) == 1
    p = new[0]
    assert p["trials_n_raw"] == seen["trials_n"]
    assert p["trials_n_floor"] == 77 and p["trials_n"] == 77
    assert p["trials_n_floor_entry_hash"] == entry_hash(first[0])   # first to supply it
    assert run_verifier(reg.log_path).returncode == 0


# ------- I5 (final review): estimate-based clustering / PBO-null deadline -------

class _FrozenClock:
    """gauntlet_stats' only clock read is time.time(); frozen unless a test
    advances it, so 'time left' is exactly --deadline-hours."""
    now = 1_000_000_000.0

    @classmethod
    def time(cls):
        return cls.now


def _frozen(monkeypatch):
    _FrozenClock.now = 1_000_000_000.0
    monkeypatch.setattr(gs, "time", _FrozenClock)
    return _FrozenClock


def _count_clustering(monkeypatch, advance_s=0.0):
    from . import gauntlet
    calls = []

    def spy(*a, **k):
        calls.append(1)
        out = gauntlet.cluster_registry(*a, **k)
        _FrozenClock.now += advance_s
        return out
    monkeypatch.setattr(gs, "cluster_registry", spy)
    return calls


def _seed_status(tmp_path, **kv):
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    (logs / gs.STATUS_NAME).write_text(json.dumps(kv), encoding="utf-8")


def _stats_run(reg, data, tmp_path, hours):
    return gs.run(["--registry", str(reg.log_path), "--data-dir", str(data),
                   "--logs-dir", str(tmp_path / "logs"), "--chain",
                   "--deadline-hours", str(hours)])


def test_clustering_is_skipped_when_time_left_is_under_the_prior(tmp_path, monkeypatch):
    """No measured duration yet: the 2 h prior applies. 1.9 h left is a
    deadline stop (exit 0, status written) with no clustering pass started;
    2.1 h left clusters."""
    from .test_gauntlet_worker import _setup, _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    n = sum(1 for _ in reg.entries())
    _frozen(monkeypatch)
    calls = _count_clustering(monkeypatch)
    assert gs.CLUSTER_PRIOR_S == 7200.0
    assert _stats_run(reg, data, tmp_path, 1.9) == 0
    assert not calls
    st = _status(tmp_path)
    assert st["stopped_at_deadline"] is True and st["exit_reason"] == "deadline"
    assert st["verdicts_without_stats"] == 1 and st["cluster_s"] is None
    assert sum(1 for _ in reg.entries()) == n
    assert _stats_run(reg, data, tmp_path, 2.1) == 0
    assert calls == [1]
    assert _status(tmp_path)["exit_reason"] == "done"


def test_the_last_measured_clustering_time_is_the_estimate(tmp_path, monkeypatch):
    """A persisted cluster_s replaces the prior in both directions, is carried
    forward by a run that does not cluster, and is re-measured by one that does."""
    from .test_gauntlet_worker import _setup, _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    _frozen(monkeypatch)
    calls = _count_clustering(monkeypatch, advance_s=1234.0)
    _seed_status(tmp_path, cluster_s=10000.0)          # longer than the prior
    assert _stats_run(reg, data, tmp_path, 2.5) == 0   # 9000 s left < 10000 s
    assert not calls
    st = _status(tmp_path)
    assert st["exit_reason"] == "deadline" and st["cluster_s"] == 10000.0
    _seed_status(tmp_path, cluster_s=600.0)            # shorter than the prior
    assert _stats_run(reg, data, tmp_path, 0.5) == 0   # 1800 s left >= 600 s
    assert calls == [1]
    st = _status(tmp_path)
    assert st["exit_reason"] == "done" and st["cluster_s"] == 1234.0


def _live_family_v61(tmp_path):
    """test_gauntlet_classes' live family (five DISTINCT r-multiple series on
    BTCUSD, at least one gate-passer, so the stats job builds a real
    permutation null for it) plus its dead ETHUSD family, judged by the v6.1
    worker and not yet statted."""
    from .test_gauntlet import v4_bars, V4_CUTOFF
    from .test_gauntlet_classes import dead_and_live_family_registry, _flat_like
    from .test_gauntlet_worker import V61
    from .test_screen import write_data_dir
    from . import gauntlet_worker as gw
    reg, live_sids, _, _, _ = dead_and_live_family_registry(tmp_path)
    reg.append("note", {"text": V61})
    data = write_data_dir(tmp_path, {"BTCUSD": v4_bars(),
                                     "ETHUSD": _flat_like(v4_bars())})
    logs = tmp_path / "logs"
    assert gw.run(["--registry", str(reg.log_path), "--data-dir", str(data),
                   "--artifacts-dir", str(tmp_path / "art"), "--logs-dir", str(logs),
                   "--cutoff", V4_CUTOFF, "--no-perturb", "--max-workers", "1"]) == 0
    return reg, data, logs, V4_CUTOFF


def test_a_pbo_null_that_does_not_fit_is_a_deadline_stop(tmp_path, monkeypatch, capsys):
    """A live family that builds a real permutation null. Clustering is
    estimated at 1 s (persisted); the null at gauntlet's PBO_NULL_PRIOR_S
    (60 s). 36 s left: the null does not fit -> deadline stop, exit 0, nothing
    chained for that family. 72 s left: it fits and every verdict is statted."""
    from .gauntlet import PBO_NULL_PRIOR_S
    reg, data, logs, cutoff = _live_family_v61(tmp_path)
    assert PBO_NULL_PRIOR_S == 60.0
    clock = _frozen(monkeypatch)
    _count_clustering(monkeypatch)
    args = _stat_args(reg, data, logs, cutoff)
    (logs / gs.STATUS_NAME).write_text(json.dumps({"cluster_s": 1.0}), encoding="utf-8")
    capsys.readouterr()
    assert gs.run(args + ["--deadline-hours", "0.01"]) == 0
    st = json.loads((logs / gs.STATUS_NAME).read_text(encoding="utf-8"))
    assert st["stopped_at_deadline"] is True and st["exit_reason"] == "deadline"
    assert st["verdicts_without_stats"] > 0
    assert st["pbo_null_mean_s"] is None
    # the dead family (no null needed) may be statted; the live one is not
    assert all(p["pbo_null_draws"] == 0 for p in _stats_payloads(reg))
    assert "PBO null" in capsys.readouterr().out
    clock.now = 1_000_000_000.0
    # the frozen clock measured clustering at 0 s, which is no estimate;
    # restore the 1 s one so only the null's budget is under test
    (logs / gs.STATUS_NAME).write_text(json.dumps({"cluster_s": 1.0}), encoding="utf-8")
    assert gs.run(args + ["--deadline-hours", "0.02"]) == 0
    capsys.readouterr()
    st = json.loads((logs / gs.STATUS_NAME).read_text(encoding="utf-8"))
    assert st["exit_reason"] == "done" and st["verdicts_without_stats"] == 0
    stats = _stats_payloads(reg)
    assert stats and any(p["pbo_null_draws"] > 0 for p in stats), \
        "fixture bug: no family built a null"
    assert st["pbo_null_mean_s"] is not None


def test_group_pbo_asks_before_the_null_and_times_it():
    import random
    rng = random.Random(7)
    fam = [{"sid": f"s{i}"} for i in range(6)]
    train = {f"s{i}": [rng.gauss(0.0005 * i, 0.01) for _ in range(400)]
             for i in range(6)}
    assert gs.group_pbo("g", fam, train, live=True, draws=10,
                        null_fits=lambda: False) is None
    times = []
    out = gs.group_pbo("g", fam, train, live=True, draws=10,
                       null_fits=lambda: True, null_times=times)
    assert out["pbo_status"] == "measured" and len(times) == 1
    # no null is built for a dead family, so nothing is asked
    asked = []
    gs.group_pbo("g", fam, train, live=False, draws=10,
                 null_fits=lambda: asked.append(1) or False)
    assert not asked


# ------- M2 (final review): one stats job at a time -------

def test_a_second_stats_instance_defers_and_leaves_the_status_alone(tmp_path, capsys):
    from .chainlock import ChainLock
    from .test_gauntlet_worker import _setup, _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    n = sum(1 for _ in reg.entries())
    logs = tmp_path / "logs"
    status_path = logs / gs.STATUS_NAME
    status_path.write_bytes(b'{"exit_reason": "done", "ts_utc": "owner"}')
    before = status_path.read_bytes()
    rep = tmp_path / "report.md"
    owner = ChainLock(logs, "gauntlet-stats", "other instance", name=gs.INSTANCE_LOCK)
    owner.acquire()
    try:
        assert gs.run(["--registry", str(reg.log_path), "--data-dir", str(data),
                       "--logs-dir", str(logs), "--chain", "--report", str(rep)]) == 0
    finally:
        owner.release()
    assert status_path.read_bytes() == before
    assert not rep.exists()
    assert sum(1 for _ in reg.entries()) == n
    assert "deferred_instance" in capsys.readouterr().out
    # the lock is released after a real run, so the next one is not deferred
    assert gs.run(["--registry", str(reg.log_path), "--data-dir", str(data),
                   "--logs-dir", str(logs), "--chain"]) == 0
    assert _status(tmp_path)["stats_written"] == 1
    assert not (logs / gs.INSTANCE_LOCK).exists()


def test_a_stale_stats_lock_with_a_dead_holder_is_broken(tmp_path):
    import os
    import time as real_time
    from .test_gauntlet_worker import _setup, _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    logs = tmp_path / "logs"
    lock = logs / gs.INSTANCE_LOCK
    lock.write_text(json.dumps({"holder": "gauntlet-stats", "pid": 2 ** 30,
                                "ts_utc": "x", "purpose": "crashed run"}))
    old = real_time.time() - gs.INSTANCE_STALE_AFTER_S - 60
    os.utime(lock, (old, old))
    assert gs.run(["--registry", str(reg.log_path), "--data-dir", str(data),
                   "--logs-dir", str(logs), "--chain"]) == 0
    assert _status(tmp_path)["stats_written"] == 1


# ------- T7b minors: positive floor only; unambiguous status fields -------

def test_a_non_positive_cluster_count_is_never_a_floor(tmp_path, monkeypatch, capsys):
    reg, data, logs, cutoff = _v61_sweep(tmp_path)
    sid = _a_sid(reg)
    _chain_verdict(reg, sid, {"protocol": "gauntlet-protocol-v6", "trials_n": 0})
    _chain_verdict(reg, sid, {"protocol": "gauntlet-protocol-v4", "trials_n": -5})
    seen = _spy_clustering(monkeypatch)
    assert gs.run(_stat_args(reg, data, logs, cutoff)) == 0
    capsys.readouterr()
    stats = _stats_payloads(reg)
    assert stats
    for p in stats:
        assert p["trials_n_floor"] is None and p["trials_n_floor_entry_hash"] is None
        assert p["trials_n"] == p["trials_n_raw"] == seen["trials_n"]


def test_the_status_reports_raw_and_effective_trials(tmp_path, monkeypatch, capsys):
    reg, data, logs, cutoff = _v61_sweep(tmp_path)
    _chain_verdict(reg, _a_sid(reg), {"protocol": "gauntlet-protocol-v6", "trials_n": 302})
    seen = _spy_clustering(monkeypatch)
    assert gs.run(_stat_args(reg, data, logs, cutoff)) == 0
    capsys.readouterr()
    st = json.loads((logs / gs.STATUS_NAME).read_text(encoding="utf-8"))
    assert "trials_n" not in st
    assert st["trials_n_raw"] == seen["trials_n"] < 302
    assert st["trials_n_effective"] == 302


# ------- step 7, T7 flush: chain.lock held at flush keeps the stats -------

def _held_chain_lock(logs):
    """chain.lock held by ANOTHER writer (a real lock file, as the scanner
    or the loop would leave it). Returns the owner, already acquired."""
    from .chainlock import ChainLock
    owner = ChainLock(logs, "scanner", "card batch")
    owner.acquire()
    return owner


def _sleeps(monkeypatch, clock, on_sleep=None):
    """Replace the drain's sleep: advance the frozen clock instead, record
    each call, and run `on_sleep(n)` (n = calls so far) if given."""
    calls = []

    def fake(s):
        calls.append(s)
        clock.now += s
        if on_sleep is not None:
            on_sleep(len(calls))
    monkeypatch.setattr(gs, "_sleep", fake)
    return calls


def test_a_held_chain_lock_at_flush_keeps_the_stats_and_retries(tmp_path, monkeypatch, capsys):
    """chain.lock is held when the computed entry is first flushed: the entry
    is KEPT, and once the holder releases (between two drain attempts) the
    same run chains it. retried_written counts it; exit 0, reason done."""
    from .test_gauntlet import run_verifier
    from .test_gauntlet_worker import _setup, _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    logs = tmp_path / "logs"
    clock = _frozen(monkeypatch)
    _seed_status(tmp_path, cluster_s=1.0)
    owner = _held_chain_lock(logs)
    sleeps = _sleeps(monkeypatch, clock, on_sleep=lambda n: n == 2 and owner.release())
    assert _stats_run(reg, data, tmp_path, 1.0) == 0
    out = capsys.readouterr().out
    assert "kept for a retry this run" in out
    st = _status(tmp_path)
    assert st["exit_reason"] == "done" and st["stats_written"] == 1
    assert st["retried_written"] == 1 and st["deferred_lock"] == 0
    assert st["verdicts_without_stats"] == 0
    assert sleeps == [gs.DRAIN_INTERVAL_S] * 2           # attempts at 0 s, 5 s, 10 s
    assert len(_stats_payloads(reg)) == 1
    assert run_verifier(reg.log_path).returncode == 0


def test_a_lock_held_through_the_reserve_writes_nothing_and_never_breaks_it(
        tmp_path, monkeypatch, capsys):
    """Held for the whole reserve: bounded non-blocking attempts, nothing
    written, exit 0 deferred_lock, and the holder's lock file untouched (never
    waited on past the reserve, never broken)."""
    from .test_gauntlet_worker import _setup, _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    n = sum(1 for _ in reg.entries())
    logs = tmp_path / "logs"
    clock = _frozen(monkeypatch)
    _seed_status(tmp_path, cluster_s=1.0)
    owner = _held_chain_lock(logs)
    lock_bytes = (logs / "chain.lock").read_bytes()
    t0 = clock.now
    sleeps = _sleeps(monkeypatch, clock)
    try:
        assert _stats_run(reg, data, tmp_path, 1.0) == 0
    finally:
        assert (logs / "chain.lock").read_bytes() == lock_bytes
        owner.release()
    st = _status(tmp_path)
    assert st["exit_reason"] == "deferred_lock" and st["deferred_lock"] == 1
    assert st["stats_written"] == 0 and st["retried_written"] == 0
    assert st["verdicts_without_stats"] == 1
    assert sum(1 for _ in reg.entries()) == n
    assert clock.now - t0 <= gs.DRAIN_RESERVE_S
    assert len(sleeps) == int(gs.DRAIN_RESERVE_S // gs.DRAIN_INTERVAL_S) - 1
    assert "not written; the next run recomputes them" in capsys.readouterr().out


def test_the_drain_never_runs_past_the_deadline(tmp_path, monkeypatch):
    """18 s left at the first flush: the drain stops before the deadline,
    far inside its 75 s reserve."""
    from .test_gauntlet_worker import _setup, _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    logs = tmp_path / "logs"
    clock = _frozen(monkeypatch)
    _seed_status(tmp_path, cluster_s=1.0)
    owner = _held_chain_lock(logs)
    t_end = clock.now + 0.005 * 3600
    sleeps = _sleeps(monkeypatch, clock)
    try:
        assert _stats_run(reg, data, tmp_path, 0.005) == 0
    finally:
        owner.release()
    assert clock.now <= t_end
    assert sleeps == [gs.DRAIN_INTERVAL_S] * 3           # attempts at 0, 5, 10, 15 s
    assert _status(tmp_path)["exit_reason"] == "deferred_lock"


def test_a_mid_run_hold_is_retried_after_the_next_family(tmp_path, monkeypatch, capsys):
    """Two families, one entry per flush batch. chain.lock is held at the
    first family's flush and released while the second family is computed:
    the retry after that family chains both, with no drain sleep at all."""
    from .test_gauntlet import run_verifier
    reg, data, logs, cutoff = _live_family_v61(tmp_path)
    monkeypatch.setattr(gs, "STATS_BATCH_MAX", 1)
    owner = _held_chain_lock(logs)
    calls = []
    real = gs.group_pbo

    def spy(*a, **k):
        calls.append(a[0])
        if len(calls) == 2:
            owner.release()
        return real(*a, **k)
    monkeypatch.setattr(gs, "group_pbo", spy)
    sleeps = []
    monkeypatch.setattr(gs, "_sleep", lambda s: sleeps.append(s))
    assert gs.run(_stat_args(reg, data, logs, cutoff)) == 0
    capsys.readouterr()
    assert len(calls) == 2, "fixture: two families with verdicts"
    st = json.loads((logs / gs.STATUS_NAME).read_text(encoding="utf-8"))
    assert st["exit_reason"] == "done" and st["verdicts_without_stats"] == 0
    assert st["retried_written"] >= 1 and st["deferred_lock"] == 0
    assert st["stats_written"] == len(_stats_payloads(reg)) > 1
    assert sleeps == []
    assert run_verifier(reg.log_path).returncode == 0


def _dead_family_first(monkeypatch):
    """Order the families so the dead ETHUSD family (no null needed) is
    statted before the live BTCUSD one."""
    real = gs.sibling_families

    def ordered(all_specs, *a, **k):
        fb, gb = real(all_specs, *a, **k)
        dead = {s["provenance"]["sibling_group_id"] for s in all_specs
                if "ETHUSD" in s["universe"]["assets"]}
        return dict(sorted(fb.items(), key=lambda kv: kv[0] not in dead)), gb
    monkeypatch.setattr(gs, "sibling_families", ordered)


def test_a_held_flush_holds_the_reserve_back_from_the_pbo_nulls(tmp_path, monkeypatch, capsys):
    """After a held flush the family loop leaves DRAIN_RESERVE_S for the
    drain. 120 s left: with no hold the live family's 60 s null fits and is
    built; with the dead family's entry held first, 120 s minus the 75 s
    reserve does not fit, the null is not started (deadline stop), and the
    kept entry is still chained by the drain."""
    results = {}
    for case in ("free", "held"):
        d = tmp_path / case
        d.mkdir()
        reg, data, logs, cutoff = _live_family_v61(d)
        clock = _frozen(monkeypatch)
        _count_clustering(monkeypatch)
        _dead_family_first(monkeypatch)
        monkeypatch.setattr(gs, "STATS_BATCH_MAX", 1)
        (logs / gs.STATUS_NAME).write_text(json.dumps({"cluster_s": 1.0}),
                                           encoding="utf-8")
        owner = _held_chain_lock(logs) if case == "held" else None
        _sleeps(monkeypatch, clock,
                on_sleep=lambda n, o=owner: o is not None and n == 1 and o.release())
        assert gs.run(_stat_args(reg, data, logs, cutoff)
                      + ["--deadline-hours", str(120 / 3600)]) == 0
        capsys.readouterr()
        assert not (logs / "chain.lock").exists()
        results[case] = (json.loads((logs / gs.STATUS_NAME).read_text(encoding="utf-8")),
                         _stats_payloads(reg))
    st, stats = results["free"]
    assert st["exit_reason"] == "done" and st["pbo_null_mean_s"] is not None
    assert any(p["pbo_null_draws"] > 0 for p in stats)
    st, stats = results["held"]
    assert st["exit_reason"] == "deadline" and st["stopped_at_deadline"] is True
    assert st["pbo_null_mean_s"] is None and st["verdicts_without_stats"] > 0
    assert stats and all(p["pbo_null_draws"] == 0 for p in stats)
    assert st["retried_written"] == len(stats) and st["deferred_lock"] == 0


# ------- step 7 part C: PBO null for EVERY family with a verdict needing stats -------

PBO_FIELDS = ("pbo", "pbo_n_distinct", "pbo_percentile", "pbo_null_p05",
              "pbo_null_p95", "pbo_null_draws", "pbo_status")


def _only_verdicts(monkeypatch, keep, label=None):
    """This run sees only the v6.1 verdicts of the strategies in `keep` (the
    rest are left for a later night, as a staggered backlog would), and, if
    `label` is given, sees each as that label: the label's only use in the
    job was the pre-step-7 "passing verdict in this batch" rule."""
    real = gs._View

    class Partial(real):
        def on_entry(self, e):
            n = len(self.verdicts)
            super().on_entry(e)
            if len(self.verdicts) > n:
                if self.verdicts[-1]["sid"] not in keep:
                    self.verdicts.pop()
                elif label is not None:
                    self.verdicts[-1]["verdict"] = label
    monkeypatch.setattr(gs, "_View", Partial)


def test_siblings_statted_on_different_nights_record_the_same_pbo(tmp_path, monkeypatch, capsys):
    """Night 1 stats two of the live family's verdicts, night 2 the other
    three in a batch with NO passing verdict. Under the pre-step-7 rule night
    2 recorded not_measured_dead_group with no null; now every family with a
    verdict needing stats gets its null, so both nights record identical
    PBO fields, and night 2 serves the null from the cache."""
    from .test_gauntlet import run_verifier
    reg, data, logs, cutoff = _live_family_v61(tmp_path)
    group_of = {e["payload"]["strategy_id"]: e["payload"]["provenance"]["sibling_group_id"]
                for e in reg.entries() if e["entry_type"] == "strategy_registered"}
    judged = [e["payload"]["strategy_id"] for e in reg.entries()
              if e["entry_type"] == "verdict" and e["payload"].get("stage") == "gauntlet"]
    live_g = "p3-live-test-group"
    fam = [s for s in judged if group_of[s] == live_g]
    assert len(fam) == 5
    built = []
    real_null = gs.permutation_null
    monkeypatch.setattr(gs, "permutation_null",
                        lambda *a, **k: built.append(1) or real_null(*a, **k))
    with monkeypatch.context() as m:
        _only_verdicts(m, set(fam[:2]))
        assert gs.run(_stat_args(reg, data, logs, cutoff)) == 0
    night1 = {p["strategy_id"]: p for p in _stats_payloads(reg)}
    assert set(night1) == set(fam[:2])
    st1 = json.loads((logs / gs.STATUS_NAME).read_text(encoding="utf-8"))
    assert st1["pbo_nulls_computed"] == len(built) == 1
    with monkeypatch.context() as m:
        _only_verdicts(m, set(fam[2:]), label="fail")
        assert gs.run(_stat_args(reg, data, logs, cutoff)) == 0
    capsys.readouterr()
    st2 = json.loads((logs / gs.STATUS_NAME).read_text(encoding="utf-8"))
    night2 = [p for p in _stats_payloads(reg) if p["strategy_id"] not in night1]
    assert {p["strategy_id"] for p in night2} == set(fam[2:])
    ref = next(iter(night1.values()))
    assert ref["pbo_status"] == "measured" and ref["pbo_null_draws"] > 0
    for p in night2:
        assert {k: p[k] for k in PBO_FIELDS} == {k: ref[k] for k in PBO_FIELDS}
    assert st2["pbo_nulls_cached"] == 1 and st2["pbo_nulls_computed"] == 0
    assert len(built) == 1                         # night 2 built no null
    assert run_verifier(reg.log_path).returncode == 0


def test_a_family_with_no_passing_verdict_gets_its_null(tmp_path, monkeypatch, capsys):
    """The whole live family seen as failing (and no cache): its null is
    built and recorded, where the pre-step-7 rule recorded no null."""
    reg, data, logs, cutoff = _live_family_v61(tmp_path)
    _only_verdicts(monkeypatch, {e["payload"]["strategy_id"] for e in reg.entries()
                                 if e["entry_type"] == "verdict"}, label="fail")
    assert gs.run(_stat_args(reg, data, logs, cutoff)) == 0
    capsys.readouterr()
    st = json.loads((logs / gs.STATUS_NAME).read_text(encoding="utf-8"))
    assert st["pbo_nulls_computed"] == 1 and st["pbo_nulls_cached"] == 0
    statuses = {p["pbo_status"] for p in _stats_payloads(reg)}
    assert "measured" in statuses and "not_measured_dead_group" not in statuses


def test_the_pbo_cache_key_and_file(tmp_path):
    """The cache serves a result only for the same family, vintage, digest
    and inputs (members, train series, draws); member order does not matter;
    an unreadable or other-vintage file is an empty cache."""
    fam = [{"sid": "a"}, {"sid": "b"}]
    train = {"a": [0.1, 0.2], "b": [0.3, -0.1]}
    sha = gs.pbo_inputs_sha("g", fam, train, 50)
    assert sha == gs.pbo_inputs_sha("g", fam[::-1], train, 50)
    assert sha != gs.pbo_inputs_sha("h", fam, train, 50)
    assert sha != gs.pbo_inputs_sha("g", fam, train, 49)
    assert sha != gs.pbo_inputs_sha("g", fam, {"a": [0.1, 0.2], "b": [0.3, -0.10001]}, 50)
    assert sha != gs.pbo_inputs_sha("g", fam + [{"sid": "c"}],
                                    {**train, "c": [0.0, 0.0]}, 50)
    c = gs.PboCache(tmp_path, "2026-09-27", "d1")
    assert c.get("g", sha) is None
    c.put("g", sha, {"pbo": 0.4, "pbo_null_draws": 50})
    assert gs.PboCache(tmp_path, "2026-09-27", "d1").get("g", sha) == {
        "pbo": 0.4, "pbo_null_draws": 50}
    assert gs.PboCache(tmp_path, "2026-09-27", "d1").get("g", "other") is None
    assert gs.PboCache(tmp_path, "2026-10-04", "d1").get("g", sha) is None
    assert gs.PboCache(tmp_path, "2026-09-27", "d2").get("g", sha) is None
    (tmp_path / gs.PBO_CACHE_NAME).write_text("{not json", encoding="utf-8")
    assert gs.PboCache(tmp_path, "2026-09-27", "d1").get("g", sha) is None
