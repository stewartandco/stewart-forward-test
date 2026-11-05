import json

from pipeline import cluster_cells, gauntlet, gauntlet_stats, simcache
from pipeline.test_gauntlet_stats import _setup
from tools import stats_rehearsal as sr


def _judged(tmp_path):
    from pipeline.test_gauntlet_worker import _run
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    return reg, data


def _originals():
    return ({n: getattr(simcache.SimCache, n) for n in sr.WRITE_PATHS},
            gauntlet_stats.cluster_registry_by_cell, gauntlet_stats.verdict_stats,
            gauntlet._align_for_trials, cluster_cells.cut_partition)


def test_a_cache_miss_aborts_and_writes_nothing_to_the_cache(tmp_path):
    reg, data = _judged(tmp_path)
    cache = tmp_path / "empty_cache"
    cache.mkdir()
    before = _originals()
    rc = sr.main(["--registry", str(reg.log_path), "--data-dir", str(data),
                  "--simcache-dir", str(cache), "--scratch", str(tmp_path / "s")])
    assert rc == 2
    assert list(cache.iterdir()) == []
    summary = json.loads((tmp_path / "s" / "rehearsal.json").read_text())
    assert summary["simcache_write_attempts"][0] == "put"
    assert _originals() == before                 # every patch restored


def test_a_warm_cache_runs_report_only_and_captures_the_payloads(tmp_path):
    reg, data = _judged(tmp_path)
    logs = tmp_path / "live_logs"
    # a normal report-only run fills the cache beside the registry
    assert gauntlet_stats.run(["--registry", str(reg.log_path), "--data-dir",
                               str(data), "--logs-dir", str(logs)]) == 0
    cache = reg.log_path.parent / "simcache"
    n_entries = sum(1 for _ in reg.entries())
    rc = sr.main(["--registry", str(reg.log_path), "--data-dir", str(data),
                  "--simcache-dir", str(cache), "--scratch", str(tmp_path / "s"),
                  "--dump-aligned", str(tmp_path / "s" / "aligned.npz")])
    assert rc == 0
    assert sum(1 for _ in reg.entries()) == n_entries     # nothing chained
    s = json.loads((tmp_path / "s" / "rehearsal.json").read_text())
    assert s["simcache_write_attempts"] == [] and s["fingerprint_match"]
    assert s["status"]["exit_reason"] == "done" and s["status"]["chained"] is False
    assert len(s["payloads"]) == 1
    assert s["cluster"]["trials_n_raw"] == s["payloads"][0]["trials_n_raw"]
    assert len(s["moments"]) == s["cluster"]["n"]
    assert (tmp_path / "s" / "aligned.npz").exists()
    assert (tmp_path / "s" / "report.md").exists()


def test_refusals(tmp_path):
    live = tmp_path / "layer"
    (live / "registry_log.d").mkdir(parents=True)
    (live / "registry_log.jsonl").write_text("")
    ok = tmp_path / "copy.jsonl"
    ok.write_text("")
    base = ["--data-dir", str(tmp_path), "--simcache-dir", str(tmp_path)]
    assert sr.main(["--registry", str(live / "registry_log.jsonl"),
                    "--scratch", str(tmp_path / "s"), *base]) == 2
    assert sr.main(["--registry", str(ok), "--scratch", str(live / "logs"),
                    *base]) == 2
    assert sr.main(["--registry", str(ok), "--scratch", str(tmp_path / "s"),
                    *base, "--", "--chain"]) == 2
    assert not (live / "logs").exists() and not (tmp_path / "s").exists()


def _refused(tmp_path, *passthrough):
    ok = tmp_path / "copy.jsonl"
    ok.write_text("")
    return sr.main(["--registry", str(ok), "--data-dir", str(tmp_path),
                    "--simcache-dir", str(tmp_path), "--scratch",
                    str(tmp_path / "s"), "--", *passthrough])


def test_passthrough_is_an_allow_list(tmp_path, capsys):
    """gauntlet_stats' argparse takes `--chai` as --chain and lets a repeated
    --registry / --logs-dir win, so only --cutoff, --deadline-hours and
    --pbo-null-draws pass through; everything else refuses (exit 2) before
    anything runs."""
    for tokens in (["--chai"], ["--ch"], ["--chain"],
                   ["--registry", "X"], ["--reg=X"], ["--logs-dir=X"],
                   ["--logs", "X"], ["--report", "X"], ["--data-dir", "X"],
                   ["--simcache-dir=X"], ["--cutoff"],
                   ["--cutoff", "--chain"], ["--cutoff=2026-01-01", "--chai"],
                   ["--cutoff=2026-01-01", "stray"], ["-c", "2026-01-01"],
                   ["--cutoff="]):
        assert _refused(tmp_path, *tokens) == 2, tokens
        assert "REFUSED: passthrough" in capsys.readouterr().out, tokens
    assert not (tmp_path / "s").exists()


def test_check_passthrough_accepts_the_allowed_options():
    ok, why = sr.check_passthrough(["--cutoff", "2026-01-01", "--deadline-hours=2",
                                    "--pbo-null-draws", "10",
                                    "--deadline-hours", "-1"])
    assert why is None
    assert ok == ["--cutoff=2026-01-01", "--deadline-hours=2",
                  "--pbo-null-draws=10", "--deadline-hours=-1"]
    assert sr.check_passthrough([]) == ([], None)


def test_an_allowed_passthrough_reaches_the_job(tmp_path, monkeypatch):
    """`--cutoff 2026-01-01` is accepted and handed to gauntlet_stats.run in
    `--opt=value` form after the wrapper's own paths."""
    seen = {}

    def fake_run(argv):
        seen["argv"] = argv
        return 0
    monkeypatch.setattr(gauntlet_stats, "run", fake_run)
    ok = tmp_path / "copy.jsonl"
    ok.write_text("")
    rc = sr.main(["--registry", str(ok), "--data-dir", str(tmp_path),
                  "--simcache-dir", str(tmp_path), "--scratch",
                  str(tmp_path / "s"), "--no-fingerprint",
                  "--", "--cutoff", "2026-01-01"])
    assert rc == 0
    assert seen["argv"][-1] == "--cutoff=2026-01-01"
    assert "--chain" not in seen["argv"]
    assert seen["argv"].count("--registry") == 1
    assert seen["argv"].count("--logs-dir") == 1
