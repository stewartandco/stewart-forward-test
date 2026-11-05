# research-layer/pipeline/test_gauntlet_stats.py
import json
import math
import shutil
from datetime import date

import pytest

from . import gauntlet_stats as gs

# Ruling 35 (ii): --chain refuses until the amendment note is on the chain,
# so every fixture below that chains stats carries it (test anchors, not the
# real text; the refusal itself is tested at the end of this file).
V61_AMEND1 = gs.AMENDMENT_NOTE_PREFIX + " test anchor"
# Amendment 2 (per-cell effective trials, spec s6): --chain refuses without it
# as well, so every chaining fixture carries both notes.
V61_AMEND2 = gs.AMENDMENT2_NOTE_PREFIX + " test anchor"


@pytest.fixture(autouse=True)
def _commit_unknown_by_default(monkeypatch):
    """Memory spec s3.2: the job reads free commit at two checkpoints. Every
    test here sees it as unknown (None never blocks) unless it stubs a value,
    so no result depends on this box's commit headroom at test time."""
    monkeypatch.setattr(gs, "available_commit_mb", lambda: None)


def _amend(reg):
    """Both amendment notes, in order, on `reg`."""
    reg.append("note", {"text": V61_AMEND1})
    reg.append("note", {"text": V61_AMEND2})


def _setup(tmp_path, note=True):
    """test_gauntlet_worker's fixture (v6.1 note on the chain) plus the
    v6.1 amendment note."""
    from .test_gauntlet_worker import _setup as worker_setup
    reg, spec, data = worker_setup(tmp_path, note=note)
    _amend(reg)
    return reg, spec, data


def test_vintage_is_the_last_sunday():
    assert gs.vintage_date(date(2026, 9, 30)) == "2026-09-27"   # Wednesday
    assert gs.vintage_date(date(2026, 9, 27)) == "2026-09-27"   # Sunday itself


def test_without_chain_flag_nothing_is_written(tmp_path):
    from .test_gauntlet_worker import _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    n = sum(1 for _ in reg.entries())
    rc = gs.run(["--registry", str(reg.log_path), "--data-dir", str(data),
                 "--logs-dir", str(tmp_path / "logs")])
    assert rc == 0 and sum(1 for _ in reg.entries()) == n


def test_with_chain_flag_every_v61_verdict_gets_one_linked_entry(tmp_path):
    from .test_gauntlet import run_verifier
    from .test_gauntlet_worker import _run
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
    """Spec s10: the stats job calls gauntlet.cluster_registry_by_cell (the
    gauntlet's own per-cell clustering, not a copy) and reports its trials_n."""
    from . import gauntlet
    from .test_gauntlet_worker import _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    seen = {}
    # spec s10: the job's clustering call is cluster_registry_by_cell
    real = gauntlet.cluster_registry_by_cell
    def spy(*a, **k):
        out = real(*a, **k)
        seen.update(out)
        return out
    monkeypatch.setattr(gs, "cluster_registry_by_cell", spy)
    rep = tmp_path / "report.md"
    assert gs.run(["--registry", str(reg.log_path), "--data-dir", str(data),
                   "--logs-dir", str(tmp_path / "logs"), "--report", str(rep)]) == 0
    assert seen, "the stats job did not call gauntlet.cluster_registry_by_cell"
    assert f"| trials_n_raw | {seen['trials_n']} |" in rep.read_text(encoding="utf-8")


# ---------------- beyond the brief ----------------

STATUS_KEYS = {"ts_utc", "vintage", "verdicts_without_stats", "stats_written",
               "oldest_unstatted_verdict_age_hours", "trials_n_raw",
               "trials_n_effective", "registered_n", "trials_common_days",
               "stopped_at_deadline", "chained", "cluster_s", "pbo_null_mean_s",
               # step 7: T7 flush (B), PBO scope (C), SR* floor (D)
               "retried_written", "deferred_lock", "pbo_nulls_computed",
               "pbo_nulls_cached", "expected_max_sharpe_raw",
               "expected_max_sharpe_effective",
               # 2026-10-11 orphan incident: memory-aware cluster admission
               "cluster_n", "cluster_need_mb", "avail_phys_mb",
               # per-cell effective trials (spec s6)
               "cluster_max_cell_n", "cluster_sum_n2", "cut_late_merges",
               # train windows as views (memory spec s3.1)
               "train_view_fallbacks",
               # the gate on physical AND commit (memory spec s3.2)
               "avail_commit_mb", "series_mb", "series_n"}


def _status(tmp_path):
    return json.loads((tmp_path / "logs" / "gauntlet_stats_status.json")
                      .read_text(encoding="utf-8"))


def test_status_reports_progress_and_a_second_night_finds_nothing(tmp_path):
    from .test_gauntlet_worker import _run
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
    from .test_gauntlet_worker import _run
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
    from .test_gauntlet_worker import _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    v = next(e for e in reg.entries() if e["entry_type"] == "verdict"
             and e["payload"]["stage"] == "gauntlet")
    real = gauntlet.cluster_registry_by_cell
    def racing(*a, **k):
        out = real(*a, **k)
        reg.record_gauntlet_stats(spec["strategy_id"], entry_hash(v), {"trials_n": 1})
        return out
    monkeypatch.setattr(gs, "cluster_registry_by_cell", racing)
    assert gs.run(["--registry", str(reg.log_path), "--data-dir", str(data),
                   "--logs-dir", str(tmp_path / "logs"), "--chain"]) == 0
    stats = [e for e in reg.entries() if e["entry_type"] == "gauntlet_stats"]
    assert len(stats) == 1 and stats[0]["payload"] == {
        "strategy_id": spec["strategy_id"], "verdict_entry_hash": entry_hash(v),
        "trials_n": 1}
    assert run_verifier(reg.log_path).returncode == 0
    assert _status(tmp_path)["stats_written"] == 0


def test_a_deadline_stop_writes_nothing_and_exits_0(tmp_path):
    from .test_gauntlet_worker import _run
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
    reg, spec, data = _setup(tmp_path)
    assert gs.run(["--registry", str(reg.log_path), "--data-dir", str(data),
                   "--logs-dir", str(tmp_path / "logs"), "--chain"]) == 0
    st = _status(tmp_path)
    assert st["verdicts_without_stats"] == 0 and st["stats_written"] == 0


def test_the_weekly_vintage_truncates_the_bars(tmp_path, monkeypatch):
    """Bars after the vintage Sunday are not simulated: a week of data
    appended after it leaves every simcache key unchanged."""
    from .test_gauntlet_worker import _run
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


def _registry_wide_clustering(monkeypatch):
    """Hold the stats job's clustering to the v6 gauntlet's own registry-wide
    method (cluster_registry, one partition) so the tests below can still pin
    every figure OUTSIDE the clustering method against a v6 verdict. The
    per-cell method itself is pinned by the cluster_cells tests and the
    entry tests at the end of this file."""
    from . import gauntlet

    def wide(*a, **k):
        out = gauntlet.cluster_registry(*a, **k)
        n = len(out["returns_by_id"])
        return {**out, "partitions": 1, "largest_partition_n": n,
                "late_merges": 0, "sum_n2": n * n, "by_partition": {}}
    monkeypatch.setattr(gs, "cluster_registry_by_cell", wide)


def test_recorded_stats_match_the_v6_gauntlets_own_numbers(tmp_path, capsys, monkeypatch):
    """Same registry, same bars (the fixture ends before any vintage Sunday,
    so truncation is a no-op): with the clustering held to the registry-wide
    method, the stats job must record exactly what gauntlet.run recorded in a
    v6 verdict for every registry-wide figure -- effective trials, the
    deflated Sharpe, PBO, plateau_ok and the haircut."""
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
    _amend(r61)
    assert gw.run(["--registry", str(r61.log_path), "--data-dir", str(new / "data"),
                   "--artifacts-dir", str(new / "art"), "--logs-dir", str(new / "logs"),
                   "--cutoff", V4_CUTOFF, "--no-perturb", "--max-workers", "1"]) == 0
    _registry_wide_clustering(monkeypatch)
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
    from .test_gauntlet_worker import _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    def boom(*a, **k):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(gs, "cluster_registry_by_cell", boom)
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


def test_deflated_sharpe_uses_v6s_own_window_on_a_mixed_class_registry(tmp_path, capsys, monkeypatch):
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
    _amend(r61)
    assert gw.run(["--registry", str(r61.log_path), "--data-dir", str(new / "data"),
                   "--artifacts-dir", str(new / "art"), "--logs-dir", str(new / "logs"),
                   "--cutoff", cutoff, "--no-perturb", "--max-workers", "1"]) == 0
    _registry_wide_clustering(monkeypatch)
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

def _mixed_twin(tmp_path, name, drop_eur_row, report=None):
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
    _amend(reg)
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
                   "--logs-dir", str(d / "logs"), "--cutoff", cutoff, "--chain"]
                  + (["--report", str(report)] if report else [])) == 0
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
    from .test_gauntlet_worker import _run
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
    monkeypatch.setattr(gs, "cluster_registry_by_cell",
                        lambda *a, **k: called.append(1) or gauntlet.cluster_registry_by_cell(*a, **k))
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
    _amend(reg)
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


def _spy_clustering(monkeypatch, trials_n=None, trials_var=None):
    """Capture cluster_registry_by_cell's output (the job's own call);
    optionally force its raw k and/or its trials_var (otherwise as that run's
    per-cell clustering computed them)."""
    from . import gauntlet
    seen = {}
    real = gauntlet.cluster_registry_by_cell

    def spy(*a, **k):
        out = real(*a, **k)
        if trials_n is not None:
            out = {**out, "trials_n": trials_n}
        if trials_var is not None:
            out = {**out, "trials_var": trials_var}
        seen.clear()
        seen.update(out)
        return out
    monkeypatch.setattr(gs, "cluster_registry_by_cell", spy)
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
        out = gauntlet.cluster_registry_by_cell(*a, **k)
        _FrozenClock.now += advance_s
        return out
    monkeypatch.setattr(gs, "cluster_registry_by_cell", spy)
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
    from .test_gauntlet_worker import _run
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
    from .test_gauntlet_worker import _run
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
    _amend(reg)
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
    from .test_gauntlet_worker import _run
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
    from .test_gauntlet_worker import _run
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
    from .test_gauntlet_worker import _run
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
    from .test_gauntlet_worker import _run
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
    from .test_gauntlet_worker import _run
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


# ------- step 7, Ruling 35 (Coen option a): SR* floored at the chain max -------

def _sr_parts(seen, p):
    """(sr_hat, T, skew, kurt) of an entry's DSR series, from the clustering
    the run itself produced."""
    from .stats import moments, sharpe
    r = list(seen["returns_by_id"][p["strategy_id"]])
    _, _, skew, kurt = moments(r)
    return sharpe(r), len(r), skew, kurt


def test_sr_star_is_floored_at_a_v6_verdicts_expected_max_sharpe(tmp_path, monkeypatch, capsys):
    """A v6 verdict on the chain recorded SR* = 0.9, far above this run's.
    Every entry records the raw SR* (v6.1's own figure), the floor and the
    entry that supplied it, and expected_max_sharpe = 0.9, which is what the
    deflated Sharpe uses. trials_n, its floor and trials_sr_var are as before."""
    from .stats import expected_max_sharpe, psr
    from .test_gauntlet import run_verifier
    reg, data, logs, cutoff = _v61_sweep(tmp_path)
    floor_hash = _chain_verdict(reg, _a_sid(reg), {"protocol": "gauntlet-protocol-v6",
                                                   "trials_n": 3,
                                                   "expected_max_sharpe": 0.9})
    seen = _spy_clustering(monkeypatch)
    assert gs.run(_stat_args(reg, data, logs, cutoff)) == 0
    capsys.readouterr()
    stats = _stats_payloads(reg)
    assert len(stats) > 1
    differs = 0
    for p in stats:
        n_eff = max(seen["trials_n"], 3)
        raw = expected_max_sharpe(n_eff, seen["trials_var"])
        assert 0 < raw < 0.9
        assert p["trials_n"] == n_eff and p["trials_sr_var"] == seen["trials_var"]
        assert p["expected_max_sharpe_raw"] == raw
        assert p["expected_max_sharpe_floor"] == 0.9
        assert p["expected_max_sharpe_floor_entry_hash"] == floor_hash
        assert p["expected_max_sharpe"] == 0.9
        sr_hat, T, skew, kurt = _sr_parts(seen, p)
        assert p["deflated_sharpe"] == psr(sr_hat, 0.9, T, skew, kurt)
        differs += p["deflated_sharpe"] != psr(sr_hat, raw, T, skew, kurt)
    assert differs, "the fixture must separate the floored DSR from the raw one"
    st = json.loads((logs / gs.STATUS_NAME).read_text(encoding="utf-8"))
    assert st["expected_max_sharpe_raw"] == stats[0]["expected_max_sharpe_raw"]
    assert st["expected_max_sharpe_effective"] == 0.9
    assert run_verifier(reg.log_path).returncode == 0


def test_a_raw_sr_star_above_the_floor_stands(tmp_path, monkeypatch, capsys):
    from .stats import psr
    reg, data, logs, cutoff = _v61_sweep(tmp_path)
    floor_hash = _chain_verdict(reg, _a_sid(reg), {"protocol": "gauntlet-protocol-v5",
                                                   "trials_n": 1,
                                                   "expected_max_sharpe": 1e-9})
    seen = _spy_clustering(monkeypatch)
    assert gs.run(_stat_args(reg, data, logs, cutoff)) == 0
    capsys.readouterr()
    for p in _stats_payloads(reg):
        assert p["expected_max_sharpe_raw"] > 1e-9
        assert p["expected_max_sharpe"] == p["expected_max_sharpe_raw"]
        assert p["expected_max_sharpe_floor"] == 1e-9
        assert p["expected_max_sharpe_floor_entry_hash"] == floor_hash
        sr_hat, T, skew, kurt = _sr_parts(seen, p)
        assert p["deflated_sharpe"] == psr(sr_hat, p["expected_max_sharpe_raw"],
                                           T, skew, kurt)


def test_what_is_never_an_sr_star_floor():
    """Same whitelist as trials_n (v3-v6 gauntlet verdicts, earlier stats
    entries' raw SR*); a bool, string, None, NaN, infinity, zero or negative
    value is no floor. The first of equal values supplies it."""
    from .common import entry_hash

    def verdict(m, stage="gauntlet"):
        return {"entry_type": "verdict", "ts_utc": "t",
                "payload": {"strategy_id": "s", "stage": stage, "metrics": m}}
    v = gs._View()
    junk = [verdict({"expected_max_sharpe": 5.0}),                          # no protocol
            verdict({"protocol": "gauntlet-protocol-v2", "expected_max_sharpe": 5.0}),
            verdict({"protocol": "gauntlet-protocol-v6.1", "expected_max_sharpe": 5.0}),
            verdict({"protocol": "gauntlet-protocol-v6", "expected_max_sharpe": 5.0},
                    stage="screened")]
    for bad in (True, "0.5", None, float("nan"), float("inf"), 0.0, -0.3, [0.4]):
        junk.append(verdict({"protocol": "gauntlet-protocol-v6",
                             "expected_max_sharpe": bad}))
        junk.append({"entry_type": "gauntlet_stats", "ts_utc": "t",
                     "payload": {"expected_max_sharpe_raw": bad,
                                 "expected_max_sharpe": 5.0}})
    for e in junk:
        v.on_entry(e)
    assert v.sr_floor is None and v.sr_floor_hash is None
    first = verdict({"protocol": "gauntlet-protocol-v4", "expected_max_sharpe": 0.25})
    v.on_entry(first)
    v.on_entry({"entry_type": "gauntlet_stats", "ts_utc": "u",
                "payload": {"expected_max_sharpe_raw": 0.25}})
    v.on_entry({"entry_type": "gauntlet_stats", "ts_utc": "v",
                "payload": {"expected_max_sharpe_raw": 0.2}})
    assert v.sr_floor == 0.25 and v.sr_floor_hash == entry_hash(first)
    higher = {"entry_type": "gauntlet_stats", "ts_utc": "w",
              "payload": {"expected_max_sharpe_raw": 0.3, "expected_max_sharpe": 0.31}}
    v.on_entry(higher)
    assert v.sr_floor == 0.3 and v.sr_floor_hash == entry_hash(higher)


def test_the_sr_star_floor_is_monotone_across_runs(tmp_path, monkeypatch, capsys):
    """Run 1's raw SR* is chained as expected_max_sharpe_raw; run 2's smaller
    variance would give a smaller SR*, so the floor (run 1's entry) applies."""
    from .common import entry_hash
    from .stats import expected_max_sharpe
    from .test_gauntlet import run_verifier
    reg, data, logs, cutoff = _v61_sweep(tmp_path)
    seen1 = _spy_clustering(monkeypatch, trials_var=4e-4)
    assert gs.run(_stat_args(reg, data, logs, cutoff)) == 0
    first = [e for e in reg.entries() if e["entry_type"] == "gauntlet_stats"]
    raw1 = expected_max_sharpe(seen1["trials_n"], 4e-4)
    assert first and all(e["payload"]["expected_max_sharpe_raw"] == raw1 for e in first)
    assert all(e["payload"]["expected_max_sharpe_floor"] is None for e in first)
    _chain_verdict(reg, _a_sid(reg), {"protocol": "gauntlet-protocol-v6.1"})
    seen2 = _spy_clustering(monkeypatch, trials_var=4e-7)
    assert gs.run(_stat_args(reg, data, logs, cutoff)) == 0
    capsys.readouterr()
    new = _stats_payloads(reg)[len(first):]
    assert len(new) == 1
    p = new[0]
    assert p["expected_max_sharpe_raw"] == expected_max_sharpe(p["trials_n"], 4e-7) < raw1
    assert p["expected_max_sharpe_floor"] == raw1 == p["expected_max_sharpe"]
    assert p["expected_max_sharpe_floor_entry_hash"] == entry_hash(first[0])
    assert p["trials_sr_var"] == 4e-7                     # still not floored itself
    assert run_verifier(reg.log_path).returncode == 0


def test_chain_refuses_until_the_amendment_note_is_chained(tmp_path, capsys):
    """Ruling 35 (ii): --chain exits 1 with refused_no_amendment_note and
    writes nothing; neither the v6.1 note nor a note that merely mentions the
    amendment counts. A report-only run is unaffected. Once the note is on
    the chain the same --chain run statts every verdict."""
    from .test_gauntlet_worker import _setup as worker_setup, _run
    reg, spec, data = worker_setup(tmp_path)           # the v6.1 note only
    assert _run(reg, data, tmp_path) == 0
    reg.append("note", {"text": "incident: see " + gs.AMENDMENT_NOTE_PREFIX + " later"})
    reg.append("note", {"text": gs.AMENDMENT_NOTE_PREFIX.upper() + " shouting"})
    n = sum(1 for _ in reg.entries())
    args = ["--registry", str(reg.log_path), "--data-dir", str(data),
            "--logs-dir", str(tmp_path / "logs")]
    assert gs.run(args + ["--chain"]) == 1
    assert "REFUSED" in capsys.readouterr().out
    st = _status(tmp_path)
    assert st["exit_reason"] == "refused_no_amendment_note"
    assert st["verdicts_without_stats"] == 1 and st["stats_written"] == 0
    assert sum(1 for _ in reg.entries()) == n
    assert gs.run(args) == 0                             # report only: unaffected
    assert _status(tmp_path)["exit_reason"] == "done"
    _amend(reg)
    assert gs.run(args + ["--chain"]) == 0
    assert _status(tmp_path)["stats_written"] == 1


def test_the_v61_note_is_not_the_amendment_and_vice_versa():
    from .gauntlet_core import PROTOCOL_V61
    assert not gs.is_amendment_note("gauntlet-protocol-v6.1: anything")
    assert gs.is_amendment_note(V61_AMEND1)
    assert not V61_AMEND1.startswith(PROTOCOL_V61 + ":")
    assert not gs.is_amendment_note(None) and not gs.is_amendment_note(42)


# ------- per-cell effective trials (spec s2, s4 step 8, s6) -------

def _amend1_only(tmp_path):
    """A judged registry carrying amendment 1 but NOT amendment 2."""
    from .test_gauntlet_worker import _setup as worker_setup, _run
    reg, spec, data = worker_setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    reg.append("note", {"text": V61_AMEND1})
    return reg, data


def test_chain_refuses_without_amendment2_note(tmp_path, capsys):
    reg, data = _amend1_only(tmp_path)
    reg.append("note", {"text": "see " + gs.AMENDMENT2_NOTE_PREFIX + " later"})
    n = sum(1 for _ in reg.entries())
    args = ["--registry", str(reg.log_path), "--data-dir", str(data),
            "--logs-dir", str(tmp_path / "logs"), "--chain"]
    assert gs.run(args) == 1
    assert "REFUSED" in capsys.readouterr().out
    st = _status(tmp_path)
    assert st["exit_reason"] == "refused_no_amendment2_note"
    assert st["stats_written"] == 0
    assert sum(1 for _ in reg.entries()) == n
    reg.append("note", {"text": V61_AMEND2})
    assert gs.run(args) == 0
    assert _status(tmp_path)["stats_written"] == 1


def test_report_only_runs_without_amendment2_note(tmp_path):
    reg, data = _amend1_only(tmp_path)
    n = sum(1 for _ in reg.entries())
    assert gs.run(["--registry", str(reg.log_path), "--data-dir", str(data),
                   "--logs-dir", str(tmp_path / "logs")]) == 0
    st = _status(tmp_path)
    assert st["exit_reason"] == "done" and st["chained"] is False
    assert sum(1 for _ in reg.entries()) == n


def test_the_amendment2_note_is_its_own_prefix():
    assert gs.is_amendment2_note(V61_AMEND2)
    assert not gs.is_amendment2_note(V61_AMEND1)
    assert not gs.is_amendment_note(V61_AMEND2)
    assert not gs.is_amendment2_note(None) and not gs.is_amendment2_note(7)


def test_entries_carry_partition_fields_and_method(tmp_path, capsys):
    """Multi-partition fixture (BTCUSD + GBP + EUR, one strategy each = 3
    partitions of 1): literal values, so a constant or a len(registry) in
    place of the real figure fails."""
    stats, sids = _mixed_twin(tmp_path, "parts", drop_eur_row=False)
    capsys.readouterr()
    assert len(stats) == 3
    for p in stats.values():
        assert p["cluster_method"] == "effective_trials/v4 per-cell average-linkage cut rho>=0.9"
        assert p["trials_cut_rho"] == 0.9
        assert p["trials_partitions"] == 3
        assert p["trials_largest_partition_n"] == 1
        assert "by_partition" not in p
    st = json.loads((tmp_path / "parts" / "logs" / "gauntlet_stats_status.json")
                    .read_text(encoding="utf-8"))
    assert st["cluster_max_cell_n"] == 1
    assert st["cluster_sum_n2"] == 3
    assert st["cut_late_merges"] == 0


def test_largest_partition_n_is_the_real_multi_row_count(tmp_path, capsys):
    """Task 7 mutation (9): the sweep fixture registers 5 BTCUSD strategies,
    ONE partition of 5 rows, so a hard-coded 1 (or the partition count) in
    place of the largest partition's n fails here, where the 1-member
    fixture above cannot tell them apart."""
    reg, data, logs, cutoff = _v61_sweep(tmp_path)
    assert gs.run(_stat_args(reg, data, logs, cutoff)) == 0
    capsys.readouterr()
    stats = _stats_payloads(reg)
    assert len(stats) == 5
    for p in stats:
        assert p["trials_partitions"] == 1
        assert p["trials_largest_partition_n"] == 5
    st = json.loads((logs / "gauntlet_stats_status.json").read_text(encoding="utf-8"))
    assert st["cluster_max_cell_n"] == 5
    assert st["cluster_sum_n2"] == 25


def test_report_holds_one_row_per_partition(tmp_path, capsys):
    rep = tmp_path / "rep.md"
    _mixed_twin(tmp_path, "rep", drop_eur_row=False, report=rep)
    out = capsys.readouterr().out
    lines = rep.read_text(encoding="utf-8").splitlines()
    i = lines.index("| partition | n | k | never_traded |")
    rows = [l for l in lines[i + 2:] if l.startswith("|")]
    keys = [r.split("|")[1].strip() for r in rows]
    assert len(rows) == 3 and keys == sorted(keys) and len(set(keys)) == 3
    assert [r.split("|")[1:5] for r in rows] == [
        [" BTCUSD ", " 1 ", " 1 ", " 1 "], [" EUR ", " 1 ", " 1 ", " 1 "],
        [" GBP ", " 1 ", " 1 ", " 0 "]]
    assert not any(l.startswith("| by_partition") for l in lines)
    assert f"full per-partition table in {rep}" in out


def test_floor_binds_when_the_cell_sum_is_below_it(tmp_path, monkeypatch, capsys):
    reg, data, logs, cutoff = _v61_sweep(tmp_path)
    fh = _chain_verdict(reg, _a_sid(reg),
                        {"protocol": "gauntlet-protocol-v3", "trials_n": 10 ** 6})
    seen = _spy_clustering(monkeypatch)
    assert gs.run(_stat_args(reg, data, logs, cutoff)) == 0
    capsys.readouterr()
    assert seen["trials_n"] < 10 ** 6
    for p in _stats_payloads(reg):
        assert p["trials_n_raw"] == seen["trials_n"]
        assert p["trials_n"] == p["trials_n_floor"] == 10 ** 6
        assert p["trials_n_floor_entry_hash"] == fh


def test_cell_sum_above_the_floor_is_recorded(tmp_path, monkeypatch, capsys):
    reg, data, logs, cutoff = _v61_sweep(tmp_path)
    _chain_verdict(reg, _a_sid(reg), {"protocol": "gauntlet-protocol-v3", "trials_n": 1})
    seen = _spy_clustering(monkeypatch, trials_n=6082)
    assert gs.run(_stat_args(reg, data, logs, cutoff)) == 0
    capsys.readouterr()
    assert seen["trials_n"] == 6082
    for p in _stats_payloads(reg):
        assert p["trials_n"] == p["trials_n_raw"] == 6082
        assert p["trials_n_floor"] == 1


def test_next_night_floor_reads_the_per_cell_raw(tmp_path, monkeypatch, capsys):
    """Review Focus 4: run 1 chains raw R; run 2 whose raw dips by 3 records
    trials_n R and trials_n_floor R (never lower)."""
    from .common import entry_hash
    R = 6082
    reg, data, logs, cutoff = _v61_sweep(tmp_path)
    _spy_clustering(monkeypatch, trials_n=R)
    assert gs.run(_stat_args(reg, data, logs, cutoff)) == 0
    first = _stats_payloads(reg)
    assert first and all(p["trials_n_raw"] == R for p in first)
    _chain_verdict(reg, _a_sid(reg), {"protocol": "gauntlet-protocol-v6.1"})
    _spy_clustering(monkeypatch, trials_n=R - 3)
    assert gs.run(_stat_args(reg, data, logs, cutoff)) == 0
    capsys.readouterr()
    new = _stats_payloads(reg)[len(first):]
    assert len(new) == 1
    p = new[0]
    assert p["trials_n_raw"] == R - 3
    assert p["trials_n"] == R and p["trials_n_floor"] == R
    stats = [e for e in reg.entries() if e["entry_type"] == "gauntlet_stats"]
    assert p["trials_n_floor_entry_hash"] == entry_hash(stats[0])


def test_constant_nonzero_row_is_cluster_refused(tmp_path, monkeypatch, capsys):
    from .cluster_cells import ConstantNonzeroRow
    from .test_gauntlet_worker import _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    n = sum(1 for _ in reg.entries())

    def boom(*a, **k):
        raise ConstantNonzeroRow("row s1 is constant 0.001")
    monkeypatch.setattr(gs, "cluster_registry_by_cell", boom)
    assert gs.run(["--registry", str(reg.log_path), "--data-dir", str(data),
                   "--logs-dir", str(tmp_path / "logs"), "--chain"]) == 1
    assert "REFUSED" in capsys.readouterr().out
    assert _status(tmp_path)["exit_reason"] == "cluster_refused"
    assert sum(1 for _ in reg.entries()) == n


# ------- step 7 fix round 1 (review M2, M3, M4) -------

def test_the_pbo_cache_is_keyed_on_the_pbo_code_and_constants(tmp_path, monkeypatch):
    """M2: a result cached under one code/constant revision is never served
    under another, so a mid-vintage change to the PBO code or constants
    cannot reuse a stale null. pbo_code_sha() is stable for unchanged code
    and moves with PBO_CACHE_REV and with each PBO constant."""
    fam = [{"sid": "a"}, {"sid": "b"}]
    sha = gs.pbo_inputs_sha("g", fam, {"a": [0.1, 0.2], "b": [0.3, -0.1]}, 50)
    gs.PboCache(tmp_path, "2026-09-27", "d1", code_sha="rev-a").put(
        "g", sha, {"pbo": 0.4, "pbo_null_draws": 50})
    assert gs.PboCache(tmp_path, "2026-09-27", "d1", code_sha="rev-a").get("g", sha)
    assert gs.PboCache(tmp_path, "2026-09-27", "d1", code_sha="rev-b").get("g", sha) is None
    base = gs.pbo_code_sha()
    assert base == gs.pbo_code_sha() and len(base) == 64
    assert gs.PboCache(tmp_path, "v", "d").code_sha == base      # the default
    for name, value in (("PBO_CACHE_REV", gs.PBO_CACHE_REV + 1),
                        ("PBO_MIN_DISTINCT", gs.PBO_MIN_DISTINCT + 1),
                        ("PBO_PASS_PCTILE", 0.06), ("PBO_KILL_PCTILE", 0.94),
                        ("CSCV_SPLITS", gs.CSCV_SPLITS + 2)):
        with monkeypatch.context() as m:
            m.setattr(gs, name, value)
            assert gs.pbo_code_sha() != base, name


def test_a_failed_pbo_cache_write_does_not_crash_the_run(tmp_path, monkeypatch, capsys):
    """M3: the cache file cannot be written (its temp path is a directory):
    the run logs it, records the null it paid for, chains every entry and
    ends done, exit 0."""
    from .test_gauntlet import run_verifier
    reg, data, logs, cutoff = _live_family_v61(tmp_path)
    (logs / gs.PBO_CACHE_NAME).with_suffix(".json.tmp").mkdir(parents=True)
    assert gs.run(_stat_args(reg, data, logs, cutoff)) == 0
    out = capsys.readouterr().out
    assert "PBO cache not written" in out
    st = json.loads((logs / gs.STATUS_NAME).read_text(encoding="utf-8"))
    assert st["exit_reason"] == "done" and st["verdicts_without_stats"] == 0
    assert st["pbo_nulls_computed"] == 1
    assert any(p["pbo_null_draws"] > 0 for p in _stats_payloads(reg))
    assert not (logs / gs.PBO_CACHE_NAME).exists()
    assert run_verifier(reg.log_path).returncode == 0


def test_a_huge_integer_is_no_sr_star_floor_and_does_not_raise():
    """M4: math.isfinite raises OverflowError on an int beyond float range;
    the guard treats it as an invalid candidate instead of crashing the
    single snapshot read."""
    assert gs._positive_number(10 ** 400) is False
    assert gs._positive_number(-(10 ** 400)) is False
    assert gs._positive_number(7) is True
    v = gs._View()
    v.on_entry({"entry_type": "verdict", "ts_utc": "t",
                "payload": {"strategy_id": "s", "stage": "gauntlet",
                            "metrics": {"protocol": "gauntlet-protocol-v6",
                                        "expected_max_sharpe": 10 ** 400}}})
    v.on_entry({"entry_type": "gauntlet_stats", "ts_utc": "u",
                "payload": {"expected_max_sharpe_raw": 10 ** 400}})
    assert v.sr_floor is None and v.sr_floor_hash is None



# ---- 2026-10-11 orphan incident: cluster admission ----

def test_estimate_scales_by_sum_n2():
    """Spec s5: the pass is a sum of per-partition n^2 sweeps, so the last
    measured duration scales LINEARLY in the ratio of the sums."""
    assert gs.cluster_estimate_s(None, None, 2_000_000) == gs.CLUSTER_PRIOR_S
    assert gs.cluster_estimate_s(100.0, 1_000_000, 2_000_000) == 200.0
    assert gs.cluster_estimate_s(100.0, 2_000_000, 1_000_000) == 50.0


def test_estimate_unscaled_without_prior_sum_n2():
    """Review Focus 1: the first run after cutover reads a status written by
    the old method (cluster_s, cluster_n, no cluster_sum_n2): the prior is
    used unscaled, never scaled by a missing value."""
    assert gs.cluster_estimate_s(129.1, None, 5_000_000) == 129.1
    assert gs.cluster_estimate_s(129.1, 0, 5_000_000) == 129.1


def test_prior_cluster_sum_n2_reads_only_a_positive_int(tmp_path):
    assert gs._prior_cluster_sum_n2(tmp_path / "logs") is None          # no status
    for bad in (None, 0, -5, True, 1.5, "9"):
        _seed_status(tmp_path, cluster_sum_n2=bad)
        assert gs._prior_cluster_sum_n2(tmp_path / "logs") is None, bad
    _seed_status(tmp_path, cluster_s=129.1, cluster_n=26835)  # old-method status
    assert gs._prior_cluster_sum_n2(tmp_path / "logs") is None
    _seed_status(tmp_path, cluster_sum_n2=123456)
    assert gs._prior_cluster_sum_n2(tmp_path / "logs") == 123456


def _cell_spec(sid, *assets):
    return {"strategy_id": sid,
            "universe": {"assets": list(assets), "asset_class": "crypto",
                         "timeframe": "1d", "session": "24x7"}}


def test_partition_helpers_count_the_series_that_exist():
    specs = [_cell_spec("a", "BTCUSDT"), _cell_spec("b", "BTCUSDT"),
             _cell_spec("c", "BTCUSDT"), _cell_spec("d", "ETHUSDT"),
             _cell_spec("e", "ETHUSDT"), _cell_spec("z", "SOLUSDT")]
    sids = {"a", "b", "c", "d", "e"}                   # z has no series
    assert gs.largest_partition_n(specs, sids) == 3
    assert gs.partition_sum_n2(specs, sids) == 3 * 3 + 2 * 2
    assert gs.largest_partition_n(specs, set()) == 0
    assert gs.partition_sum_n2(specs, set()) == 0


def test_need_uses_the_largest_partition():
    """Spec s5: one n_max x n_max matrix at a time, plus the aligned copies
    of ALL n series (11.5 B per n L, re-measured by the Task 6 rehearsal on
    2026-11-05). 29,180 series, largest partition 1,068, 1,308 aligned days:
    493 MiB."""
    assert gs.CLUSTER_BYTES_PER_ALIGNED_NL == 11.5
    assert gs.cluster_need_mb(29180, 1068, 1308) == 493
    assert gs.cluster_need_mb(29180, 1068, 1308) == math.ceil(
        (9.0 * 1068 ** 2 + 48.0 * 1068 * 1308 + 11.5 * 29180 * 1308) / 2**20)
    assert gs.cluster_need_mb(0, 0, 1303) == 0


def test_need_grows_with_n_max_squared_not_n():
    """Doubling n at a fixed n_max changes only the aligned term."""
    base = gs.cluster_need_mb(10000, 1000, 1300)
    doubled = gs.cluster_need_mb(20000, 1000, 1300)
    assert doubled - base in (
        math.ceil(11.5 * 10000 * 1300 / 2**20) - 1,
        math.ceil(11.5 * 10000 * 1300 / 2**20),
        math.ceil(11.5 * 10000 * 1300 / 2**20) + 1)
    # doubling n_max at a fixed n quadruples the matrix term
    quad = gs.cluster_need_mb(10000, 2000, 1300) - gs.cluster_need_mb(10000, 0, 1300)
    one = gs.cluster_need_mb(10000, 1000, 1300) - gs.cluster_need_mb(10000, 0, 1300)
    assert quad > 2 * one
    # the registry-wide n^2 of the old model is gone: 26,835 in one partition
    # still costs what the 2026-11-01 measurement said (6,896 MB), with margin
    one_big = gs.cluster_need_mb(26835, 26835, 1303)
    assert 6896 * 1.05 < one_big < 6896 * 1.30
    assert gs.cluster_need_mb(26835, 1068, 1303) < one_big / 10
    # the n x L term counts: more aligned days, more memory
    assert gs.cluster_need_mb(20000, 800, 2000) > gs.cluster_need_mb(20000, 800, 1000)


def test_cluster_days_bound_is_the_shortest_series():
    """The aligned length is not known before cluster_registry runs; the
    shortest series bounds it from above (native: all equal; intersection:
    at most the shortest member's dates)."""
    from .simcache import Series
    long_ = Series.from_pairs([(f"2024-01-{d:02d}", 0.01) for d in range(1, 31)])
    short = Series.from_pairs([(f"2024-01-{d:02d}", 0.01) for d in range(1, 11)])
    assert gs.cluster_days_bound({"a": long_, "b": short}) == 10
    assert gs.cluster_days_bound({}) == 0


def test_clustering_is_not_started_without_the_physical_memory(tmp_path, monkeypatch, capsys):
    """Free RAM under the n^2 footprint: no pass starts (it would page and
    thrash), exit 3 deferred_memory (nonzero: the Sentinel FAILs the
    digest, Coen 2026-10-11), nothing chained, the series stay cached, the
    numbers are in the status. With the memory, it clusters (exit 0)."""
    from .test_gauntlet_worker import _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    n = sum(1 for _ in reg.entries())
    calls = _count_clustering(monkeypatch)
    monkeypatch.setattr(gs, "cluster_need_mb", lambda n_series, n_max, days: 4500)
    monkeypatch.setattr(gs, "available_phys_mb", lambda: 1400)
    assert gs.DEFERRED_MEMORY_EXIT == 3
    assert _stats_run(reg, data, tmp_path, 5.75) == 3
    assert not calls
    st = _status(tmp_path)
    assert (st["exit_reason"], st["cluster_need_mb"], st["avail_phys_mb"]) ==         ("deferred_memory", 4500, 1400)
    assert st["stopped_at_deadline"] is False and st["verdicts_without_stats"] == 1
    assert sum(1 for _ in reg.entries()) == n
    assert "MEMORY: clustering" in capsys.readouterr().out
    monkeypatch.setattr(gs, "available_phys_mb", lambda: 8000)
    assert _stats_run(reg, data, tmp_path, 5.75) == 0
    assert calls == [1] and _status(tmp_path)["exit_reason"] == "done"


def test_the_memory_gate_charges_the_series_count_and_the_days_bound(tmp_path, monkeypatch):
    """The gate's need is cluster_need_mb(len(dated), n_max, cluster_days_bound
    (dated)), computed on the series the clustering is about to run on."""
    from .test_gauntlet_worker import _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    seen = {}

    def bound(dated):
        seen["dated"] = (len(dated), min(len(s) for s in dated.values()))
        return 4242

    def need(n_series, n_max, days):
        seen["need"] = (n_series, days)
        seen["n_max"] = n_max
        return 1

    monkeypatch.setattr(gs, "cluster_days_bound", bound)
    monkeypatch.setattr(gs, "cluster_need_mb", need)
    monkeypatch.setattr(gs, "available_phys_mb", lambda: 8000)
    assert _stats_run(reg, data, tmp_path, 5.75) == 0
    assert seen["need"] == (seen["dated"][0], 4242) and seen["dated"][1] > 0
    assert seen["n_max"] == 1                    # one spec, one partition
    assert _status(tmp_path)["cluster_need_mb"] == 1


def test_gate_reads_n_max_before_clustering(tmp_path, monkeypatch):
    """The gate's n_max is the PRE-clustering largest partition from
    largest_partition_n: a need that is high for a whole-registry partition
    but low for a smaller n_max clusters (exit 0); with the real helper's
    n_max (== n here) the same stub defers (exit 3, deferred_memory)."""
    from .test_gauntlet_worker import _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    calls = _count_clustering(monkeypatch)
    seen = []

    def need(n_series, n_max, days):
        seen.append((n_series, n_max))
        return 4000 if n_max >= n_series else 300

    monkeypatch.setattr(gs, "cluster_need_mb", need)
    monkeypatch.setattr(gs, "available_phys_mb", lambda: 1000)
    assert _stats_run(reg, data, tmp_path, 5.75) == 3          # real n_max == n
    assert not calls and seen == [(1, 1)]
    monkeypatch.setattr(gs, "largest_partition_n", lambda specs, sids: 0)
    assert _stats_run(reg, data, tmp_path, 5.75) == 0           # per-cell fits
    assert calls == [1] and seen[-1] == (1, 0)
    assert _status(tmp_path)["exit_reason"] == "done"


def test_deferred_memory_still_exit_3(tmp_path, monkeypatch, capsys):
    """A per-cell need above the free memory is exit 3 deferred_memory, and
    the message names the largest partition."""
    from .test_gauntlet_worker import _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    calls = _count_clustering(monkeypatch)
    monkeypatch.setattr(gs, "available_phys_mb", lambda: 0)
    assert gs.DEFERRED_MEMORY_EXIT == 3
    assert _stats_run(reg, data, tmp_path, 5.75) == 3
    assert not calls
    st = _status(tmp_path)
    assert st["exit_reason"] == "deferred_memory"
    assert st["cluster_need_mb"] > 0 and st["avail_phys_mb"] == 0
    out = capsys.readouterr().out
    assert "MEMORY: clustering" in out and "largest partition" in out


def test_unknown_free_memory_does_not_block(tmp_path, monkeypatch):
    from .test_gauntlet_worker import _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    calls = _count_clustering(monkeypatch)
    monkeypatch.setattr(gs, "available_phys_mb", lambda: None)
    assert _stats_run(reg, data, tmp_path, 5.75) == 0
    assert calls == [1]


def test_cluster_sum_n2_is_carried_and_scales_the_estimate(tmp_path, monkeypatch):
    """The estimate is the measured cluster_s scaled by sum_n2 / cluster_sum_n2.
    The fixture has one series (sum_n2 1): 600 s measured at sum_n2 100 scales
    to 6 s, which fits in 100 s left where the unscaled 600 s would not.
    Without a cluster_sum_n2 (an old-method status) the same seed is a
    deadline stop, and cluster_s / cluster_n / cluster_sum_n2 are carried
    forward unchanged by a run that does not cluster."""
    from .test_gauntlet_worker import _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    _frozen(monkeypatch)
    calls = _count_clustering(monkeypatch, advance_s=5.0)
    hours = 100.0 / 3600
    _seed_status(tmp_path, cluster_s=600.0, cluster_n=10)   # old method: unscaled
    assert _stats_run(reg, data, tmp_path, hours) == 0
    st = _status(tmp_path)
    assert not calls and (st["exit_reason"], st["cluster_n"]) == ("deadline", 10)
    assert st["cluster_s"] == 600.0 and st["cluster_sum_n2"] is None
    _seed_status(tmp_path, cluster_s=600.0, cluster_n=10, cluster_sum_n2=100)
    assert _stats_run(reg, data, tmp_path, 100.0 / 3600 / 100) == 0   # 1 s left
    st = _status(tmp_path)                       # a non-clustering night carries it
    assert not calls and st["exit_reason"] == "deadline"
    assert (st["cluster_s"], st["cluster_n"], st["cluster_sum_n2"]) == (600.0, 10, 100)
    assert _stats_run(reg, data, tmp_path, hours) == 0
    st = _status(tmp_path)
    assert calls == [1] and st["exit_reason"] == "done"
    # re-measured at n = 1, sum_n2 = 1
    assert (st["cluster_s"], st["cluster_n"], st["cluster_sum_n2"]) == (5.0, 1, 1)


def test_estimate_scales_by_the_partition_sum_not_n_squared(tmp_path, monkeypatch):
    """Task 7 mutation (8): two partitions of 5 (BTCUSD, ETHUSD), so n = 10
    and sum_n2 = 50, not n^2 = 100. 600 s measured at cluster_sum_n2 50
    estimates 600 s and fits in 900 s left; an estimate scaled by n^2 would
    be 1,200 s and stop the run before clustering. The fixture above has one
    series, where the two scalings agree."""
    reg, data, logs, cutoff = _live_family_v61(tmp_path)
    _frozen(monkeypatch)
    calls = _count_clustering(monkeypatch, advance_s=7.0)
    _seed_status(tmp_path, cluster_s=600.0, cluster_n=10, cluster_sum_n2=50)
    assert gs.run(["--registry", str(reg.log_path), "--data-dir", str(data),
                   "--logs-dir", str(logs), "--cutoff", cutoff,
                   "--deadline-hours", str(900.0 / 3600)]) == 0
    st = _status(tmp_path)
    assert calls == [1] and st["stopped_at_deadline"] is False
    assert (st["cluster_s"], st["cluster_n"], st["cluster_sum_n2"]) == (7.0, 10, 50)
    assert st["cluster_max_cell_n"] == 5


def test_the_deadline_counts_from_the_task_start(tmp_path, monkeypatch):
    """The guard's stamp says the task began 6 h ago: a 5.75 h deadline is
    already spent before the series pass, so nothing is computed."""
    from datetime import datetime, timedelta, timezone
    from .taskguard import STARTED_ENV
    from .test_gauntlet_worker import _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    calls = _count_clustering(monkeypatch)
    monkeypatch.setenv(STARTED_ENV, (datetime.now(timezone.utc)
                                     - timedelta(hours=6)).isoformat())
    assert _stats_run(reg, data, tmp_path, 5.75) == 0
    assert not calls and _status(tmp_path)["exit_reason"] == "deadline"



def _late_merges_run(tmp_path, monkeypatch, capsys, late):
    from . import gauntlet
    real = gauntlet.cluster_registry_by_cell

    def stub(*a, **k):
        return {**real(*a, **k), "late_merges": late}
    monkeypatch.setattr(gs, "cluster_registry_by_cell", stub)
    reg, data, logs, cutoff = _v61_sweep(tmp_path)
    capsys.readouterr()
    assert gs.run(_stat_args(reg, data, logs, cutoff)) == 0
    out = capsys.readouterr().out
    st = json.loads((logs / "gauntlet_stats_status.json").read_text(encoding="utf-8"))
    return out, st


def test_late_merges_print_one_warn_line(tmp_path, monkeypatch, capsys):
    """Final review M4: a run whose cut found late merges says so in the run
    log, once; a run with none prints no such line."""
    out, st = _late_merges_run(tmp_path / "one", monkeypatch, capsys, 1)
    line = ("WARN cut_late_merges 1: merges at or under the cut after the "
            "first over it (not counted)")
    assert out.count(line) == 1 and st["cut_late_merges"] == 1
    out, st = _late_merges_run(tmp_path / "zero", monkeypatch, capsys, 0)
    assert "WARN cut_late_merges" not in out and st["cut_late_merges"] == 0


# ------- train windows as views (memory spec s3.1, Task 2) -------

# Pinned on BASE 49e8e0fb2 (gauntlet_stats.py, pbo.py and stats.py unchanged
# since): the cache key of this fixture family, and the PBO code part of the
# cache key. A deliberate PBO code change re-pins the second, never this task.
BASE_PBO_INPUTS_SHA = "b62488ab5e5ad29c9d76e9190565446769e94240412578767f23514752558788"
BASE_PBO_CODE_SHA = "d4f980d9591d04869277554368f420bf78472e72f5ffe9d3c43dff4bb2f9eb59"
_SHA_FAM = [{"sid": "s2"}, {"sid": "s1"}, {"sid": "s3"}]
_SHA_TRAIN = {"s1": [0.01, -0.02, 0.5e-3, 1 / 3],
              "s2": [-0.25, 0.125, 1e-17, 2.0], "s3": []}


def _as_views(train):
    """Each list as a real prefix view of a longer parent, as train_view
    returns it (the parent's tail is past the cutoff)."""
    import numpy as np
    out = {}
    for sid, r in train.items():
        parent = np.asarray(list(r) + [9.0, -9.0], dtype=np.float64)
        out[sid] = parent[:len(r)]
        assert out[sid].base is parent
    return out


def test_pbo_inputs_sha_is_unchanged_for_views():
    """Spec s2 anti-goal: the PBO cache key is byte-identical for the same
    family whether its train windows arrive as lists or as views."""
    import numpy as np
    lists = {k: list(v) for k, v in _SHA_TRAIN.items()}
    arrays = {k: np.asarray(v, dtype=np.float64) for k, v in _SHA_TRAIN.items()}
    assert gs.pbo_inputs_sha("g-fixture", _SHA_FAM, lists, 50) == BASE_PBO_INPUTS_SHA
    assert gs.pbo_inputs_sha("g-fixture", _SHA_FAM, arrays, 50) == BASE_PBO_INPUTS_SHA
    assert (gs.pbo_inputs_sha("g-fixture", _SHA_FAM, _as_views(_SHA_TRAIN), 50)
            == BASE_PBO_INPUTS_SHA)


def test_group_pbo_is_unchanged_for_views():
    """The run hands group_pbo one family's lists built from its views
    (family_train); the result equals the list input's, null included.
    group_pbo's own source is untouched: pbo_code_sha() hashes it, so an edit
    there would re-key every cached null (spec s2 anti-goal)."""
    import random
    rng = random.Random(7)
    fam = [{"sid": f"m{i}"} for i in range(6)]
    lists = {s["sid"]: [rng.gauss(0.0, 0.01) for _ in range(160)] for s in fam}
    views = _as_views(lists)
    fam_lists = gs.family_train(fam, views)
    assert fam_lists == lists
    assert all(type(x) is float for r in fam_lists.values() for x in r)
    want = gs.group_pbo("g-views", fam, lists, True, 3)
    got = gs.group_pbo("g-views", fam, fam_lists, True, 3)
    assert want["pbo_status"] == "measured" and want["pbo_null_draws"] == 3
    assert json.dumps(got, sort_keys=True) == json.dumps(want, sort_keys=True)
    assert gs.pbo_code_sha() == BASE_PBO_CODE_SHA


def _spy_payloads(monkeypatch):
    """verdict_stats' return (the chained payload less strategy_id and
    verdict_entry_hash) per strategy, as canonical JSON."""
    got = {}
    real = gs.verdict_stats

    def spy(sid, *a, **k):
        out = real(sid, *a, **k)
        got[sid] = json.dumps(out, sort_keys=True)
        return out
    monkeypatch.setattr(gs, "verdict_stats", spy)
    return got


def _list_path(monkeypatch):
    """train_view replaced by today's list path (mask copy, as an array)."""
    import numpy as np
    from . import simcache
    monkeypatch.setattr(simcache.Series, "train_view",
                        lambda self, c: (np.asarray(self.train(c)), False))


def _count_views(monkeypatch):
    from . import simcache
    calls = []
    real = simcache.Series.train_view

    def spy(self, c):
        calls.append(c)
        return real(self, c)
    monkeypatch.setattr(simcache.Series, "train_view", spy)
    return calls


def _report_only(reg, data, logs, cutoff):
    return gs.run(["--registry", str(reg.log_path), "--data-dir", str(data),
                   "--logs-dir", str(logs), "--cutoff", cutoff])


def test_entries_are_identical_with_views(tmp_path, monkeypatch, capsys):
    """Every payload the job computes is identical, as canonical JSON, to the
    one computed when train_view is today's list path: the v4 sweep (report
    only, same registry, fresh logs and PBO cache each run) and the mixed
    class twin (crypto + fx)."""
    reg, data, logs, cutoff = _v61_sweep(tmp_path)
    with monkeypatch.context() as m:
        calls = _count_views(m)
        views = _spy_payloads(m)
        assert _report_only(reg, data, tmp_path / "logs_views", cutoff) == 0
    n_registered = sum(1 for e in reg.entries()
                       if e["entry_type"] == "strategy_registered")
    assert len(calls) == n_registered        # the run took the view path
    with monkeypatch.context() as m:
        _list_path(m)
        lists = _spy_payloads(m)
        assert _report_only(reg, data, tmp_path / "logs_lists", cutoff) == 0
    assert views and views == lists
    with monkeypatch.context() as m:
        calls = _count_views(m)
        views = _spy_payloads(m)
        _mixed_twin(tmp_path, "views", drop_eur_row=False)
    assert calls
    with monkeypatch.context() as m:
        _list_path(m)
        lists = _spy_payloads(m)
        _mixed_twin(tmp_path, "lists", drop_eur_row=False)
    capsys.readouterr()
    assert len(views) == 3 and views == lists


def test_consumers_never_write_into_a_train_view(tmp_path, monkeypatch, capsys):
    """Review Focus 3: every train view and its parent `rets` are made
    read-only; the whole run (train Sharpe, sibling families, PBO key and
    null, the verdict loop) completes, so no consumer writes into either."""
    from . import simcache
    reg, data, logs, cutoff = _v61_sweep(tmp_path)
    seen = []
    real = simcache.Series.train_view

    def read_only(self, c):
        v, fell_back = real(self, c)
        self.rets.setflags(write=False)
        v.setflags(write=False)
        seen.append(v)
        return v, fell_back
    monkeypatch.setattr(simcache.Series, "train_view", read_only)
    got = _spy_payloads(monkeypatch)
    assert _report_only(reg, data, tmp_path / "logs_ro", cutoff) == 0
    capsys.readouterr()
    assert seen and got
    assert all(not v.flags.writeable for v in seen)


def test_train_view_fallbacks_counted(tmp_path, monkeypatch, capsys):
    """Review Focus 1: a series whose dates are not non-decreasing (its last
    two dates swapped, both past the cutoff) takes train_view's mask-copy
    fallback; the status counts it (0 on a clean run) and every payload is
    unchanged."""
    from . import simcache
    reg, data, logs, cutoff = _v61_sweep(tmp_path)
    clean = _spy_payloads(monkeypatch)
    assert _report_only(reg, data, tmp_path / "logs_clean", cutoff) == 0
    st = json.loads((tmp_path / "logs_clean" / gs.STATUS_NAME)
                    .read_text(encoding="utf-8"))
    assert st["train_view_fallbacks"] == 0
    real = gs.cluster_registry_by_cell
    swapped = []

    def unsort_one(dated, *a, **k):
        out = real(dated, *a, **k)
        sid = sorted(dated)[0]
        s = dated[sid]
        d = s.dates.copy()
        assert len(d) >= 2 and d[-2] > simcache.date_ordinal(cutoff)
        d[-1], d[-2] = d[-2], d[-1]
        dated[sid] = simcache.Series(d, s.rets)
        swapped.append(sid)
        return out
    monkeypatch.setattr(gs, "cluster_registry_by_cell", unsort_one)
    fell = _spy_payloads(monkeypatch)
    assert _report_only(reg, data, tmp_path / "logs_unsorted", cutoff) == 0
    capsys.readouterr()
    st = json.loads((tmp_path / "logs_unsorted" / gs.STATUS_NAME)
                    .read_text(encoding="utf-8"))
    assert swapped and st["train_view_fallbacks"] == 1
    assert clean and fell == clean


# Fix round 1 (Ruling 4): the two-arm test above runs the same consumer code
# in both arms. These digests pin the payloads to the BASE code (49e8e0fb2),
# computed once there on the same fixtures (report-only v4 sweep; the mixed
# twin keyed by role, its sids hash the clock). Only `data_vintage` is
# excluded: it is date.today()'s vintage Sunday, so it moves weekly.
BASE_PAYLOADS_SHA_V4 = "7eac768d727bde59d6eea523de84b7df62cdef46915103b505f549b3c692e439"
BASE_PAYLOADS_SHA_MIXED = "c35ca38971aecba462759cf8d6cbf68c65377ce6d65bac35187847e42f908db4"


def _payloads_sha(by_key: dict) -> str:
    import hashlib
    body = {k: {f: v for f, v in json.loads(p).items() if f != "data_vintage"}
            for k, p in by_key.items()}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()


def test_payloads_match_the_base_codes_digests(tmp_path, monkeypatch, capsys):
    reg, data, logs, cutoff = _v61_sweep(tmp_path)
    with monkeypatch.context() as m:
        v4 = _spy_payloads(m)
        assert _report_only(reg, data, tmp_path / "logs_pin", cutoff) == 0
    with monkeypatch.context() as m:
        mixed = _spy_payloads(m)
        _, sids = _mixed_twin(tmp_path, "pin", drop_eur_row=False)
    capsys.readouterr()
    assert len(v4) == 5 and len(mixed) == 3
    assert _payloads_sha(v4) == BASE_PAYLOADS_SHA_V4
    assert (_payloads_sha({role: mixed[sid] for role, sid in sids.items()})
            == BASE_PAYLOADS_SHA_MIXED)



# ---- memory spec s3.2: the gate on physical AND commit; series checkpoint ----

def _two_readings(monkeypatch, phys, commit):
    monkeypatch.setattr(gs, "available_phys_mb", lambda: phys)
    monkeypatch.setattr(gs, "available_commit_mb", lambda: commit)


def _memory_line(capsys):
    return next(ln for ln in capsys.readouterr().out.splitlines()
                if ln.startswith("MEMORY:"))


def test_short_commit_defers_naming_commit(tmp_path, monkeypatch, capsys):
    """Plenty of RAM, short of commit at the clustering checkpoint: exit 3
    deferred_memory (an allocation would fail), the MEMORY line names commit
    and the clustering, not physical memory. The series pass ran (its charge,
    the fallback for one strategy, fits), so the shortfall is the later one."""
    from .test_gauntlet_worker import _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    n = sum(1 for _ in reg.entries())
    calls = _count_clustering(monkeypatch)
    monkeypatch.setattr(gs, "cluster_need_mb", lambda n_series, n_max, days: 4500)
    _two_readings(monkeypatch, 100_000, 1000)
    assert _stats_run(reg, data, tmp_path, 5.75) == 3
    assert not calls and sum(1 for _ in reg.entries()) == n
    st = _status(tmp_path)
    assert (st["exit_reason"], st["cluster_need_mb"], st["avail_phys_mb"],
            st["avail_commit_mb"]) == ("deferred_memory", 4500, 100_000, 1000)
    line = _memory_line(capsys)
    assert line.startswith("MEMORY: clustering") and "commit" in line
    assert "physical" not in line


def test_short_phys_defers_naming_physical(tmp_path, monkeypatch, capsys):
    from .test_gauntlet_worker import _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    calls = _count_clustering(monkeypatch)
    monkeypatch.setattr(gs, "cluster_need_mb", lambda n_series, n_max, days: 4500)
    _two_readings(monkeypatch, 1400, 100_000)
    assert _stats_run(reg, data, tmp_path, 5.75) == 3
    assert not calls
    st = _status(tmp_path)
    assert (st["exit_reason"], st["avail_phys_mb"], st["avail_commit_mb"]) == (
        "deferred_memory", 1400, 100_000)
    line = _memory_line(capsys)
    assert line.startswith("MEMORY: clustering") and "physical" in line
    assert "commit" not in line


def test_both_short_names_both(tmp_path, monkeypatch, capsys):
    from .test_gauntlet_worker import _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    monkeypatch.setattr(gs, "cluster_need_mb", lambda n_series, n_max, days: 4500)
    _two_readings(monkeypatch, 1400, 1000)
    assert _stats_run(reg, data, tmp_path, 5.75) == 3
    line = _memory_line(capsys)
    assert "physical" in line and "commit" in line


def test_unknown_commit_never_blocks(tmp_path, monkeypatch):
    """Review Focus 5: both readings unknown (a non-Windows host, a failed
    call): neither checkpoint blocks, however large the charge; the run
    clusters and finishes, exit 0, and the status says unknown (null)."""
    from .test_gauntlet_worker import _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    calls = _count_clustering(monkeypatch)
    monkeypatch.setattr(gs, "cluster_need_mb", lambda n_series, n_max, days: 10 ** 9)
    _seed_status(tmp_path, series_mb=10 ** 9, series_n=1)
    _two_readings(monkeypatch, None, None)
    assert _stats_run(reg, data, tmp_path, 5.75) == 0
    st = _status(tmp_path)
    assert calls == [1] and st["exit_reason"] == "done"
    assert st["avail_phys_mb"] is None and st["avail_commit_mb"] is None


def test_one_unknown_reading_leaves_the_other_limit(tmp_path, monkeypatch):
    """One reading unknown: the other limit still applies."""
    from .test_gauntlet_worker import _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    monkeypatch.setattr(gs, "cluster_need_mb", lambda n_series, n_max, days: 4500)
    _two_readings(monkeypatch, 1400, None)
    assert _stats_run(reg, data, tmp_path, 5.75) == 3
    _two_readings(monkeypatch, None, 1000)
    assert _stats_run(reg, data, tmp_path, 5.75) == 3
    _two_readings(monkeypatch, 100_000, None)
    assert _stats_run(reg, data, tmp_path, 5.75) == 0


def test_need_includes_post_cluster_margin(tmp_path, monkeypatch):
    """Spec s3.2 item 2: the need compared at the clustering checkpoint is
    cluster_need_mb(...) + POST_CLUSTER_NEED_MB, on BOTH limits. The status's
    cluster_need_mb stays the clustering figure (the Sentinel's ratio)."""
    from .test_gauntlet_worker import _run
    assert gs.POST_CLUSTER_NEED_MB == 512
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    calls = _count_clustering(monkeypatch)
    monkeypatch.setattr(gs, "cluster_need_mb", lambda n_series, n_max, days: 1000)
    edge = 1000 + gs.POST_CLUSTER_NEED_MB
    for phys, commit in ((edge - 1, 100_000), (100_000, edge - 1)):
        _two_readings(monkeypatch, phys, commit)
        assert _stats_run(reg, data, tmp_path, 5.75) == 3, (phys, commit)
        assert _status(tmp_path)["cluster_need_mb"] == 1000
    assert not calls
    _two_readings(monkeypatch, edge, edge)
    assert _stats_run(reg, data, tmp_path, 5.75) == 0
    assert calls == [1]


def test_series_charge_scales_with_n():
    """Spec s3.2 item 3: series_mb x n / series_n, plus 10%."""
    assert gs.series_charge_mb(3000, 100, 200, 0) == math.ceil(3000 * 2 * 1.10)
    assert gs.series_charge_mb(3000, 100, 100, 0) == math.ceil(3000 * 1.10)
    # no prior (or half of one): the fallback, unscaled
    assert gs.series_charge_mb(None, None, 200, 777) == 777
    assert gs.series_charge_mb(3000, None, 200, 777) == 777
    assert gs.series_charge_mb(None, 100, 200, 777) == 777


def test_series_fallback_is_points_plus_csv_bytes_plus_25pct():
    """No measurement yet: 12 B x registered n x 7,000 points, plus the cell
    CSVs' bytes, x 1.25, in MiB (the constraints' fallback)."""
    assert (gs.SERIES_FALLBACK_BYTES_PER_POINT, gs.SERIES_FALLBACK_POINTS,
            gs.SERIES_FALLBACK_MARGIN) == (12, 7000, 1.25)
    assert gs.series_fallback_mb(30140, 2 * 2**30) == math.ceil(
        (12 * 30140 * 7000 + 2 * 2**30) * 1.25 / 2**20)
    assert gs.series_fallback_mb(0, 0) == 0


def test_prior_series_reads_only_positive_ints(tmp_path):
    assert gs._prior_series(tmp_path / "logs") == (None, None)       # no status
    for bad in (None, 0, -5, True, 1.5, "9"):
        _seed_status(tmp_path, series_mb=bad, series_n=bad)
        assert gs._prior_series(tmp_path / "logs") == (None, None), bad
    _seed_status(tmp_path, series_mb=3000, series_n=100)
    assert gs._prior_series(tmp_path / "logs") == (3000, 100)


def _spy_load_cell_data(monkeypatch):
    calls = []
    real = gs.load_cell_data

    def spy(*a, **k):
        calls.append(1)
        return real(*a, **k)
    monkeypatch.setattr(gs, "load_cell_data", spy)
    return calls


def test_series_check_defers_before_the_pass(tmp_path, monkeypatch, capsys):
    """Spec s3.2 item 3: a prior series_mb of 3,000 measured at half of
    tonight's registered n charges ceil(3,000 x 2 x 1.10) = 6,601 MB of
    commit; with 5,000 available the run defers (exit 3 deferred_memory)
    BEFORE load_cell_data, and the MEMORY line names the series pass and
    commit. With 7,000 it runs. (The brief's 100 -> 200 is 5 -> 10 here: the
    fixture registers 10 strategies; the ratio is the same.)"""
    reg, data, logs, cutoff = _live_family_v61(tmp_path)
    args = ["--registry", str(reg.log_path), "--data-dir", str(data),
            "--logs-dir", str(logs), "--cutoff", cutoff]
    loads = _spy_load_cell_data(monkeypatch)
    n = sum(1 for _ in reg.entries())
    _seed_status(tmp_path, series_mb=3000, series_n=5)
    _two_readings(monkeypatch, 100_000, 5000)
    assert gs.run(args) == 3
    assert not loads and sum(1 for _ in reg.entries()) == n
    st = _status(tmp_path)
    assert (st["exit_reason"], st["avail_commit_mb"]) == ("deferred_memory", 5000)
    assert (st["series_mb"], st["series_n"]) == (3000, 5)         # carried
    assert st["cluster_need_mb"] is None and st["avail_phys_mb"] is None
    line = _memory_line(capsys)
    assert line.startswith("MEMORY: series pass") and "commit" in line
    assert "6601" in line and "physical" not in line
    _two_readings(monkeypatch, 100_000, 7000)
    assert gs.run(args) == 0 and loads == [1]


def test_series_check_uses_the_fallback_without_a_prior(tmp_path, monkeypatch, capsys):
    """Review Focus 4: the first night after cutover reads a status with no
    series_mb / series_n. The early checkpoint charges the fallback (12 B x
    n x 7,000 + the cell CSVs' bytes, x 1.25) and never blocks on the
    missing value: at exactly the fallback it runs; one MB under it defers."""
    from .test_gauntlet_worker import _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    _seed_status(tmp_path, cluster_s=1.0, cluster_n=1)        # an old status
    csv_bytes = sum(p.stat().st_size for p in data.glob("*.csv"))
    fallback = gs.series_fallback_mb(1, csv_bytes)
    assert fallback == math.ceil((12 * 7000 + csv_bytes) * 1.25 / 2**20)
    loads = _spy_load_cell_data(monkeypatch)
    _two_readings(monkeypatch, 100_000, fallback - 1)
    assert _stats_run(reg, data, tmp_path, 5.75) == 3 and not loads
    line = _memory_line(capsys)
    assert line.startswith("MEMORY: series pass") and "fallback" in line
    # exactly the fallback at the series checkpoint, plenty at clustering's
    commit = iter([fallback, 100_000])
    monkeypatch.setattr(gs, "available_commit_mb", lambda: next(commit))
    assert _stats_run(reg, data, tmp_path, 5.75) == 0 and loads == [1]
    assert _status(tmp_path)["avail_commit_mb"] == 100_000    # the last reading


def test_series_fallback_counts_only_the_cells_loaded(tmp_path, monkeypatch):
    """The CSV term is the files load_cell_data will read (the registered
    cells), not every file in the data dir."""
    from .test_gauntlet_worker import _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    csv_bytes = sum(p.stat().st_size for p in data.glob("*.csv"))
    (data / "ZZZUSDT_1d.csv").write_bytes(b"x" * (64 * 2**20))  # not registered
    seen = []
    real = gs.series_fallback_mb
    monkeypatch.setattr(gs, "series_fallback_mb",
                        lambda n, b: seen.append((n, b)) or real(n, b))
    _two_readings(monkeypatch, 100_000, 100_000)
    assert _stats_run(reg, data, tmp_path, 5.75) == 0
    assert seen == [(1, csv_bytes)]


def test_series_mb_recorded_and_carried_forward(tmp_path, monkeypatch):
    """Spec s3.2 item 3: series_mb = PeakPagefileUsage after the series pass
    minus PrivateUsage just before load_cell_data (MiB), recorded with the
    registered n. A deadline stop INSIDE the pass records nothing and keeps
    the prior pair; a run that cannot read the counters keeps it too."""
    import time as real_time
    reg, data, logs, cutoff = _live_family_v61(tmp_path)
    args = ["--registry", str(reg.log_path), "--data-dir", str(data),
            "--logs-dir", str(logs), "--cutoff", cutoff]
    _two_readings(monkeypatch, 100_000, 100_000)
    order = []
    real_load = gs.load_cell_data
    monkeypatch.setattr(gs, "load_cell_data",
                        lambda *a, **k: order.append("load") or real_load(*a, **k))
    readings = iter([(100, 150), (400, 900)])
    monkeypatch.setattr(gs, "process_memory_mb",
                        lambda: order.append("mem") or next(readings))
    _seed_status(tmp_path, series_mb=777, series_n=3)
    assert gs.run(args) == 0
    st = _status(tmp_path)
    assert st["exit_reason"] == "done" and order == ["mem", "load", "mem"]
    assert (st["series_mb"], st["series_n"]) == (900 - 100, 10)
    # a deadline stop inside the pass: the second chunk finds the clock spent;
    # the pair measured above stays
    clock = _frozen(monkeypatch)
    monkeypatch.setattr(gs, "SERIES_CHUNK", 1)
    real_series = gs.registry_series

    def series(*a, **k):
        out = real_series(*a, **k)
        clock.now += 10 ** 6
        return out
    monkeypatch.setattr(gs, "registry_series", series)
    readings = iter([(100, 150), (5000, 99_000)])
    assert gs.run(args + ["--deadline-hours", "1"]) == 0
    st = _status(tmp_path)
    assert st["exit_reason"] == "deadline" and st["stopped_at_deadline"] is True
    assert (st["series_mb"], st["series_n"]) == (800, 10)
    # counters unknown: nothing recorded, the prior stays
    monkeypatch.setattr(gs, "registry_series", real_series)
    monkeypatch.setattr(gs, "time", real_time)
    monkeypatch.setattr(gs, "process_memory_mb", lambda: None)
    _seed_status(tmp_path, series_mb=800, series_n=10)
    assert gs.run(args) == 0
    st = _status(tmp_path)
    assert st["exit_reason"] == "done"
    assert (st["series_mb"], st["series_n"]) == (800, 10)
