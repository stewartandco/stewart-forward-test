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

from .simcache import SimCache, cache_key
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

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["series"] = [["2099-01-01", 999.0]]      # sha no longer matches
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert cache.get(key) is None       # poisoned -> treated as a miss
    assert not path.exists()            # self-healed: the bad file is gone

    cache.put(key, SERIES, equity_len=4)
    hit = cache.get(key)
    assert hit is not None
    assert [tuple(row) for row in hit["series"]] == SERIES


def test_malformed_json_is_also_a_miss_and_self_heals(tmp_path):
    cache = SimCache(tmp_path / "simcache")
    key = cache_key("sid1", {"BTCUSD": "aaa"}, ENGINE_REV, 365)
    path = cache._path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")

    assert cache.get(key) is None
    assert not path.exists()


def test_missing_expected_field_is_also_a_miss(tmp_path):
    cache = SimCache(tmp_path / "simcache")
    key = cache_key("sid1", {"BTCUSD": "aaa"}, ENGINE_REV, 365)
    path = cache._path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"series": SERIES}), encoding="utf-8")  # no sha

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
