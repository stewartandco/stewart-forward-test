"""Tests for SP4 Task P1's content-addressed sim cache.

Unit tests exercise pipeline/simcache.py directly: key identity, hit/miss,
poison detection and self-heal, and ENGINE_REV-bump invalidation. The
integration test at the bottom drives pipeline/gauntlet.py's real CLI
entrypoint twice against independent copies of one registry -- the same
pattern test_gen6.py's test_perturbation_does_not_change_any_verdict uses --
to prove the cache changes nothing about a recorded verdict except its own
sim_cache counters.

Run: python -m pytest pipeline/test_simcache.py -q
"""
from __future__ import annotations

import json
import shutil

import numpy as np

from .simcache import SimCache, Series, cache_key, date_ordinal
from .engine import ENGINE_REV
from .registry import Registry
from .gauntlet import run as gauntlet_run
from .test_gauntlet import v4_sweep_registry, v4_bars, V4_CUTOFF, write_data_dir

SERIES = [("2024-01-01", 0.01), ("2024-01-02", -0.005), ("2024-01-03", 0.02)]


# ---------------- cache_key ----------------

def test_cache_key_ignores_dict_insertion_order():
    a = cache_key("sid1", {"BTCUSD": "aaa", "ETHUSD": "bbb"}, "e2", 365)
    b = cache_key("sid1", {"ETHUSD": "bbb", "BTCUSD": "aaa"}, "e2", 365)
    assert a == b


def test_cache_key_changes_with_sid():
    assert (cache_key("sid1", {"BTCUSD": "aaa"}, "e2", 365)
           != cache_key("sid2", {"BTCUSD": "aaa"}, "e2", 365))


def test_cache_key_changes_with_data_sha():
    assert (cache_key("sid1", {"BTCUSD": "aaa"}, "e2", 365)
           != cache_key("sid1", {"BTCUSD": "zzz"}, "e2", 365))


def test_cache_key_changes_with_engine_rev():
    """The ENGINE_REV-bump-invalidates contract at the key level: bumping
    the engine revision produces a completely different key, so an entry
    cached under a prior revision is never looked up under the new one --
    it is not detected-and-rejected, it is simply a different, absent key."""
    assert (cache_key("sid1", {"BTCUSD": "aaa"}, "e1", 365)
           != cache_key("sid1", {"BTCUSD": "aaa"}, "e2", 365))


def test_cache_key_changes_with_periods_per_year():
    """Batch review rider (latent staleness): a cached series depends on the
    RESOLVED periods_per_year too -- it feeds vol_target's realized-vol
    sizing (cells.SESSION_PERIODS -> engine.realized_ann_vol), a mapping that
    lives outside sid/data/engine_rev entirely. Same sid, same data, same
    engine revision, different periods_per_year (e.g. a fx spec at 261 vs a
    crypto spec's 365, or the SAME session's mapping edited) must be a
    DIFFERENT key -- never the same one silently serving a series simulated
    under a different annualization."""
    assert (cache_key("sid1", {"BTCUSD": "aaa"}, "e2", 365)
           != cache_key("sid1", {"BTCUSD": "aaa"}, "e2", 261))


# ---------------- SimCache: miss / hit / poison ----------------

def test_miss_on_empty_cache(tmp_path):
    cache = SimCache(tmp_path / "simcache")
    key = cache_key("sid1", {"BTCUSD": "aaa"}, ENGINE_REV, 365)
    assert cache.get(key) is None


def test_put_then_get_round_trips(tmp_path):
    cache = SimCache(tmp_path / "simcache")
    key = cache_key("sid1", {"BTCUSD": "aaa"}, ENGINE_REV, 365)
    cache.put(key, SERIES, equity_len=len(SERIES) + 1)

    hit = cache.get(key)
    assert hit is not None
    assert [tuple(row) for row in hit["series"]] == SERIES
    assert hit["equity_len"] == len(SERIES) + 1


def test_get_is_atomic_write_safe_tmp_file_left_behind_is_ignored(tmp_path):
    """put() writes tmp-then-rename; a stray .tmp file (e.g. from a crash
    mid-write) must never be read as if it were the real entry."""
    cache = SimCache(tmp_path / "simcache")
    key = cache_key("sid1", {"BTCUSD": "aaa"}, ENGINE_REV, 365)
    cache.put(key, SERIES, equity_len=4)
    stray = cache._path(key).with_name(cache._path(key).name + ".tmp")
    stray.write_text("not json at all", encoding="utf-8")
    hit = cache.get(key)                # reads the real file, not the stray
    assert hit is not None
    assert [tuple(row) for row in hit["series"]] == SERIES


def test_poisoned_entry_is_a_miss_and_self_heals(tmp_path):
    """A mismatched series/sha (disk corruption, a hand edit, a truncated
    write) must be treated as a miss, not served -- and the bad file must be
    deleted so the very next put() starts clean rather than the poison
    persisting forever."""
    cache = SimCache(tmp_path / "simcache")
    key = cache_key("sid1", {"BTCUSD": "aaa"}, ENGINE_REV, 365)
    cache.put(key, SERIES, equity_len=4)
    path = cache._path(key)
    assert path.exists()

    with np.load(path) as z:
        parts = {k: z[k] for k in z.files}
    parts["rets"] = np.array([999.0, 999.0, 999.0])   # sha no longer matches
    with open(path, "wb") as f:
        np.savez(f, **parts)

    assert cache.get(key) is None       # poisoned -> treated as a miss
    assert not path.exists()            # self-healed: the bad file is gone

    cache.put(key, SERIES, equity_len=4)
    hit = cache.get(key)
    assert hit is not None
    assert [tuple(row) for row in hit["series"]] == SERIES


def test_malformed_file_is_also_a_miss_and_self_heals(tmp_path):
    cache = SimCache(tmp_path / "simcache")
    key = cache_key("sid1", {"BTCUSD": "aaa"}, ENGINE_REV, 365)
    path = cache._path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"{not an npz at all")

    assert cache.get(key) is None
    assert not path.exists()


def test_missing_expected_field_is_also_a_miss(tmp_path):
    cache = SimCache(tmp_path / "simcache")
    key = cache_key("sid1", {"BTCUSD": "aaa"}, ENGINE_REV, 365)
    path = cache._path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    ser = Series.from_pairs(SERIES)
    with open(path, "wb") as f:
        np.savez(f, dates=ser.dates, rets=ser.rets, equity_len=np.int64(4))  # no sha

    assert cache.get(key) is None
    assert not path.exists()


def test_engine_rev_bump_invalidates_through_the_cache(tmp_path):
    """End-to-end version of the key-level test above: an entry written
    under one engine revision is never served under a different one, even
    though it is still sitting on disk (nothing proactively sweeps stale
    revisions -- they are simply never looked up again)."""
    cache = SimCache(tmp_path / "simcache")
    old_key = cache_key("sid1", {"BTCUSD": "aaa"}, "e1", 365)
    cache.put(old_key, SERIES, equity_len=4)

    new_key = cache_key("sid1", {"BTCUSD": "aaa"}, "e2", 365)
    assert cache.get(new_key) is None
    assert cache.get(old_key) is not None      # the old entry is untouched


# ---------------- integration: hit vs miss produce the same verdicts -------

def test_cache_hit_vs_miss_verdicts_are_byte_identical(tmp_path):
    """Same-answer proof (SP4 Task P1). One sibling is advanced past
    'gauntlet' before this test's own gauntlet.run() calls -- simulating a
    strategy a PRIOR generation already resolved -- so it is never a
    candidate; the other four stay in 'gauntlet' state. That resolved
    sibling is exactly the registry-wide re-simulation the cache exists for.

    The identical pre-run registry is copied into two independent run
    directories (test_gen6.test_perturbation_does_not_change_any_verdict's
    pattern) and run through the real CLI entrypoint twice against the SAME
    simcache dir: empty on the first run (a miss populates it), already
    populated on the second (a hit serves it). Every verdict field must
    match between the two runs except the metrics' own `sim_cache` counters,
    which must themselves flip from (0 hits, 1 miss) to (1 hit, 0 misses).

    artifacts_hash is deliberately excluded from the comparison: it covers
    config.json, which embeds `metrics` verbatim, so it honestly differs by
    exactly as much as sim_cache does -- comparing it would just re-assert
    the same fact under a different name.
    """
    source, by_lb = v4_sweep_registry(tmp_path)
    resolved_sid = by_lb[100]        # dead lookback: fails oos_negative either way
    source.record_verdict(resolved_sid, "gauntlet", "fail",
                          {"note": "resolved by a prior generation"},
                          "0" * 64)
    source.record_state_change(resolved_sid, "graveyard", "prior_generation")
    candidate_sids = {by_lb[lb] for lb in (20, 35, 55, 75)}

    shared_cache = tmp_path / "shared_simcache"
    outcomes = []
    for i in range(2):
        run_dir = tmp_path / f"run{i}"
        run_dir.mkdir()
        log = run_dir / "reg.jsonl"
        shutil.copyfile(source.log_path, log)
        data = write_data_dir(run_dir, {"BTCUSD": v4_bars()})
        rc = gauntlet_run(["--registry", str(log), "--data-dir", str(data),
                           "--artifacts-dir", str(run_dir / "art"),
                           "--simcache-dir", str(shared_cache),
                           "--cutoff", V4_CUTOFF])
        assert rc == 0
        # candidate_sids only: `log` also carries the manual verdict this
        # test itself chained onto `resolved_sid` above (copied in with the
        # rest of `source`'s pre-run state), which is not one of THIS run's
        # own outputs.
        verdicts = {e["payload"]["strategy_id"]: e["payload"]
                   for e in Registry(log).entries()
                   if e["entry_type"] == "verdict"
                   and e["payload"].get("stage") == "gauntlet"
                   and e["payload"]["strategy_id"] in candidate_sids}
        assert set(verdicts) == candidate_sids
        outcomes.append(verdicts)

    run0, run1 = outcomes
    for sid in candidate_sids:
        m0 = dict(run0[sid]["metrics"])
        m1 = dict(run1[sid]["metrics"])
        cache0 = m0.pop("sim_cache")
        cache1 = m1.pop("sim_cache")
        assert m0 == m1, f"{sid}: a recorded metric changed between runs"
        assert run0[sid]["verdict"] == run1[sid]["verdict"] == (
            "fail" if sid == by_lb[75] else "pass")
        assert cache0 == {"hits": 0, "misses": 1}
        assert cache1 == {"hits": 1, "misses": 0}


# ---------------- arrays (2026-09-03): Series, legacy migration ------------

def _legacy_write(cache: SimCache, key: str, series, equity_len: int) -> None:
    """Write an entry exactly the way the pre-2026-09-03 module did."""
    import hashlib
    cache.cache_dir.mkdir(parents=True, exist_ok=True)
    rows = [list(r) for r in series]
    sha = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":"))
                         .encode("utf-8")).hexdigest()
    cache._legacy_path(key).write_text(
        json.dumps({"series": rows, "series_sha256": sha, "equity_len": equity_len},
                   sort_keys=True, separators=(",", ":")), encoding="utf-8")


def test_series_iterates_as_the_pairs_it_replaced_and_keeps_every_float():
    ser = Series.from_pairs(SERIES)
    assert len(ser) == 3
    assert list(ser) == SERIES
    assert ser.rets.dtype == np.float64 and ser.dates.dtype == np.int32
    assert ser.rets.tolist() == [r for _, r in SERIES]      # bit-identical floats


def test_series_train_matches_the_date_only_slice():
    from .gauntlet import _date_le
    pairs = [("2024-01-01", 0.01), ("2024-01-02 00:00:00", -0.005),
             ("2024-01-03", 0.02), ("2024-01-03", 0.03), ("2024-01-04", 0.1)]
    ser = Series.from_pairs(pairs)
    for cutoff in ("2023-12-31", "2024-01-02", "2024-01-03 12:00:00", "2024-01-09"):
        assert ser.train(cutoff) == [r for d, r in pairs if _date_le(d, cutoff)], cutoff


def test_legacy_json_entry_is_served_once_then_lives_on_as_npz(tmp_path):
    cache = SimCache(tmp_path / "simcache")
    key = cache_key("sid1", {"BTCUSD": "aaa"}, ENGINE_REV, 365)
    _legacy_write(cache, key, SERIES, equity_len=4)
    assert cache._legacy_path(key).exists() and not cache._path(key).exists()

    hit = cache.get(key)
    assert hit is not None
    assert list(hit["series"]) == SERIES and hit["equity_len"] == 4
    assert cache._path(key).exists() and not cache._legacy_path(key).exists()

    again = cache.get(key)                       # now from the npz
    assert again["series"] == hit["series"] and again["equity_len"] == 4


def test_legacy_entry_failing_its_own_self_check_is_poisoned_not_converted(tmp_path):
    cache = SimCache(tmp_path / "simcache")
    key = cache_key("sid1", {"BTCUSD": "aaa"}, ENGINE_REV, 365)
    _legacy_write(cache, key, SERIES, equity_len=4)
    legacy = cache._legacy_path(key)
    payload = json.loads(legacy.read_text(encoding="utf-8"))
    payload["series"] = [["2099-01-01", 999.0]]
    legacy.write_text(json.dumps(payload), encoding="utf-8")

    assert cache.get(key) is None
    assert not legacy.exists() and not cache._path(key).exists()


def test_bulk_migrate_converts_every_legacy_entry_and_reports(tmp_path):
    cache = SimCache(tmp_path / "simcache")
    keys = [cache_key(f"sid{i}", {"BTCUSD": "aaa"}, ENGINE_REV, 365) for i in range(3)]
    for k in keys:
        _legacy_write(cache, k, SERIES, equity_len=4)
    cache.put("already", SERIES, equity_len=4)
    counts = cache.migrate(verbose=False)
    assert counts == {"migrated": 3, "poisoned": 0, "already": 1}
    assert not list(cache.cache_dir.glob("*.json"))
    for k in keys:
        assert list(cache.get(k)["series"]) == SERIES


def test_put_of_a_key_supersedes_its_legacy_entry(tmp_path):
    cache = SimCache(tmp_path / "simcache")
    key = cache_key("sid1", {"BTCUSD": "aaa"}, ENGINE_REV, 365)
    _legacy_write(cache, key, [("2000-01-01", 0.5)], equity_len=2)
    cache.put(key, SERIES, equity_len=4)
    assert not cache._legacy_path(key).exists()
    assert list(cache.get(key)["series"]) == SERIES


def test_intersect_returns_on_arrays_matches_the_dict_gather_it_replaced():
    """The pre-2026-09-03 gather was `dict(rows)[d]` over the sorted common
    date strings: a date repeated within a series resolved to its LAST row.
    The array version must give the same values, in the same order,
    including on unsorted input."""
    from .gauntlet import intersect_returns
    a = [("2024-01-01", 0.1), ("2024-01-02", 0.2), ("2024-01-02", 0.25),
         ("2024-01-03", 0.3), ("2024-01-05", 0.5)]
    b = [("2024-01-05", 1.5), ("2024-01-02", 1.2), ("2024-01-03", 1.3),
         ("2024-01-04", 1.4)]
    dated = {"a": Series.from_pairs(a), "b": Series.from_pairs(b)}

    def reference(dated_by_id):
        common = None
        for rows in dated_by_id.values():
            dates = {d for d, _ in rows}
            common = dates if common is None else (common & dates)
        common_sorted = sorted(common or set())
        return ({sid: [dict(rows)[d] for d in common_sorted]
                 for sid, rows in dated_by_id.items()}, common_sorted)

    got, common = intersect_returns(dated)
    want, want_common = reference({"a": a, "b": b})
    assert common == want_common == ["2024-01-02", "2024-01-03", "2024-01-05"]
    assert {k: v.tolist() for k, v in got.items()} == want
    assert want["a"] == [0.25, 0.3, 0.5]            # the duplicate's LAST row


def test_date_ordinal_ignores_a_time_suffix():
    assert date_ordinal("2024-01-02 23:59:59") == date_ordinal("2024-01-02")
