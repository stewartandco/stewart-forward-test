"""Phase 3 step 1 (2026-09-03): a chain-writing stage stops BEFORE starting
work it cannot finish by a deadline, and leaves the rest in a state the next
cycle resumes from.

Why: on 2026-09-01 the 21:30 loop cycle was killed by Task Scheduler at
exactly the PT4H ExecutionTimeLimit. Everything it had done was discarded,
its composer spend was not, and nothing on the chain or in the logs said why
-- a hard kill leaves no terminator. TRIAGE_LIMIT bounds triage only; the
gauntlet was ~150 of that cycle's 237 minutes and had no bound at all.

The shared DeadlineBudget is tested with an injected clock so nothing here
sleeps or races. The gauntlet is tested end to end through gauntlet.run().
"""
import json

from . import deadline as dl
from .gauntlet import run as gauntlet_run
from .registry import Registry
from .test_gauntlet import v4_sweep_registry
from .test_screen import write_data_dir, dated_target_hit_bars


# ---------------- DeadlineBudget -------------------------------------------

class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t
    def __call__(self):
        return self.t
    def advance(self, s):
        self.t += s


def test_no_deadline_is_inert():
    """No --deadline-utc means every stage behaves exactly as today."""
    b = dl.DeadlineBudget(None, clock=FakeClock())
    assert b.active is False
    assert b.remaining_s() is None
    assert b.fits(10_000, rate_s=3600.0) is True     # nothing is ever refused


def test_iso_deadline_parses_z_and_offset(monkeypatch):
    clock = FakeClock()
    # wall clock says 12:00:00Z; deadline 12:10:00Z -> 600 s remaining
    monkeypatch.setattr(dl, "_wall_now_utc",
                        lambda: dl._parse_iso("2026-09-03T12:00:00Z"))
    b = dl.DeadlineBudget("2026-09-03T12:10:00Z", clock=clock)
    assert b.active is True
    assert abs(b.remaining_s() - 600.0) < 1e-6
    b2 = dl.DeadlineBudget("2026-09-03T20:10:00+08:00", clock=clock)   # same instant
    assert abs(b2.remaining_s() - 600.0) < 1e-6


def test_fits_uses_prior_then_measured_rate(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(dl, "_wall_now_utc",
                        lambda: dl._parse_iso("2026-09-03T12:00:00Z"))
    b = dl.DeadlineBudget("2026-09-03T12:05:00Z", clock=clock)     # 300 s
    # nothing measured yet: the prior decides
    assert b.rate_s(prior_s=20.0) == 20.0
    assert b.fits(10, rate_s=20.0) is True          # 200 s <= 300 s
    assert b.fits(20, rate_s=20.0) is False         # 400 s  > 300 s
    # a chunk of 10 took 100 s (10 s each); the measured rate replaces the prior
    clock.advance(100.0)
    b.record(10, elapsed_s=100.0)
    assert b.rate_s(prior_s=20.0) == 10.0
    assert b.fits(20, rate_s=b.rate_s(20.0)) is True    # 200 s <= 200 s left
    assert b.fits(21, rate_s=b.rate_s(20.0)) is False


def test_reserve_is_held_back(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(dl, "_wall_now_utc",
                        lambda: dl._parse_iso("2026-09-03T12:00:00Z"))
    b = dl.DeadlineBudget("2026-09-03T12:05:00Z", reserve_s=100.0, clock=clock)
    assert b.fits(10, rate_s=20.0) is True          # 200 + 100 <= 300
    assert b.fits(11, rate_s=20.0) is False         # 220 + 100  > 300


# ---------------- gauntlet end to end --------------------------------------

def _chain(reg):
    return [json.dumps(e, sort_keys=True) for e in reg.entries()]


def _gauntlet_states(reg):
    return {sid: st for sid, st in reg.strategy_states().items()}


def test_gauntlet_defers_everything_when_the_deadline_has_passed(tmp_path):
    """A deadline already behind us: not one candidate may be STARTED. The
    chain is untouched, the result file says so, and -- the part that makes
    this safe -- a plain run afterwards picks up every deferred candidate,
    because 'in gauntlet state with no verdict' is simply 'not gauntleted
    yet'. The orphan preflight must stay green throughout."""
    reg, by_lb = v4_sweep_registry(tmp_path)
    data = write_data_dir(tmp_path, {"BTCUSD": dated_target_hit_bars()})
    art = tmp_path / "art"
    before = _chain(reg)
    n = len(by_lb)
    assert n >= 3

    rc = gauntlet_run(["--registry", str(reg.log_path), "--data-dir", str(data),
                       "--artifacts-dir", str(art),
                       "--deadline-utc", "2000-01-01T00:00:00Z"])
    assert rc == 0
    reg2 = Registry(reg.log_path)
    assert _chain(reg2) == before                              # nothing written
    assert all(st == "gauntlet" for st in _gauntlet_states(reg2).values())
    result = json.loads((reg.log_path.parent / "logs" / "gauntlet_result.json")
                        .read_text(encoding="utf-8"))
    assert result["evaluated"] == 0
    assert result["deferred"] == n
    assert result["stopped_at_deadline"] is True
    assert not art.exists() or not any(art.iterdir())          # no bundles either

    # Resume: the next cycle, with no deadline, gauntlets all of them.
    rc = gauntlet_run(["--registry", str(reg.log_path), "--data-dir", str(data),
                       "--artifacts-dir", str(art)])
    assert rc == 0
    reg3 = Registry(reg.log_path)
    verdicts = [e for e in reg3.entries() if e["entry_type"] == "verdict"
                and e["payload"]["stage"] == "gauntlet"]
    assert len(verdicts) == n
    assert not any(st == "gauntlet" for st in _gauntlet_states(reg3).values())
    result = json.loads((reg.log_path.parent / "logs" / "gauntlet_result.json")
                        .read_text(encoding="utf-8"))
    assert result == {**result, "evaluated": n, "deferred": 0,
                      "stopped_at_deadline": False}


def test_gauntlet_with_a_far_deadline_is_byte_identical_to_no_deadline(tmp_path):
    """Opt-in and inert when there is room: the same registry gauntleted with
    a deadline a year out must produce exactly the chain a run without the
    flag produces. Two isolated copies of the same fixture, compared entry
    for entry (timestamps excluded)."""
    def _run(sub, extra):
        base = tmp_path / sub
        base.mkdir()
        reg, _ = v4_sweep_registry(base)
        data = write_data_dir(base, {"BTCUSD": dated_target_hit_bars()})
        n_before = sum(1 for _ in reg.entries())
        rc = gauntlet_run(["--registry", str(reg.log_path), "--data-dir", str(data),
                           "--artifacts-dir", str(base / "art"), *extra])
        assert rc == 0
        out = []
        for e in list(Registry(reg.log_path).entries())[n_before:]:   # appended only
            e = dict(e); e.pop("ts_utc", None); e.pop("prev_entry_hash", None)
            out.append(json.dumps(e, sort_keys=True))
        assert out, "the run appended nothing -- the comparison would be vacuous"
        return out, base

    plain, _ = _run("plain", [])
    dated, base = _run("dated", ["--deadline-utc", "2099-01-01T00:00:00Z"])
    assert dated == plain
    result = json.loads((base / "reg.jsonl").parent.joinpath("logs", "gauntlet_result.json")
                        .read_text(encoding="utf-8"))
    assert result["stopped_at_deadline"] is False
    assert result["deferred"] == 0


# ---------------- screen end to end ----------------------------------------

from .screen import run as screen_run
from .test_screen import screening_registry, chain_protocol_note
from .common import content_id


def _proposed_registry(base, lookbacks=(20, 25, 30)):
    """screening_registry gives one proposed spec; clone it into a small
    family so deferral has something to defer. Same grammar, same card, a
    different lookback each so every strategy_id is distinct."""
    reg, spec = screening_registry(base)
    for lb in lookbacks[1:]:
        clone = json.loads(json.dumps(spec))
        clone["strategy_id"] = None
        clone["blocks"][0]["params"]["lookback"] = lb
        clone["strategy_id"] = content_id(clone, "strategy_id")
        reg.register_strategy(clone)
    chain_protocol_note(reg)
    return reg


def test_screen_defers_everything_when_the_deadline_has_passed(tmp_path):
    """Mirror of the gauntlet case one stage earlier. Not started means
    still 'proposed', which is exactly what the next screen run selects;
    screen's own orphan rule fires only on 'screened', so it stays green."""
    reg = _proposed_registry(tmp_path)
    data = write_data_dir(tmp_path, {"BTCUSD": dated_target_hit_bars()})
    art = tmp_path / "art"
    before = _chain(reg)
    n = sum(1 for st in reg.strategy_states().values() if st == "proposed")
    assert n == 3

    rc = screen_run(["--registry", str(reg.log_path), "--data-dir", str(data),
                     "--artifacts-dir", str(art),
                     "--deadline-utc", "2000-01-01T00:00:00Z"])
    assert rc == 0
    reg2 = Registry(reg.log_path)
    assert _chain(reg2) == before
    assert all(st == "proposed" for st in reg2.strategy_states().values())
    result = json.loads((reg.log_path.parent / "logs" / "screen_result.json")
                        .read_text(encoding="utf-8"))
    assert (result["evaluated"], result["deferred"], result["stopped_at_deadline"]) == (0, n, True)
    assert not art.exists() or not any(art.iterdir())

    rc = screen_run(["--registry", str(reg.log_path), "--data-dir", str(data),
                     "--artifacts-dir", str(art)])
    assert rc == 0
    reg3 = Registry(reg.log_path)
    verdicts = [e for e in reg3.entries() if e["entry_type"] == "verdict"
                and e["payload"]["stage"] == "screened"]
    assert len(verdicts) == n
    assert not any(st == "proposed" for st in reg3.strategy_states().values())
    result = json.loads((reg.log_path.parent / "logs" / "screen_result.json")
                        .read_text(encoding="utf-8"))
    assert (result["evaluated"], result["deferred"], result["stopped_at_deadline"]) == (n, 0, False)


def test_screen_with_a_far_deadline_is_byte_identical_to_no_deadline(tmp_path):
    """One seeded registry, copied to two dirs, so the only variable is the
    flag. (Building two fixtures independently is not an identity test:
    make_card() stamps the wall clock into the card id, which flows into
    every strategy_id.)"""
    import shutil
    seed = tmp_path / "seed"
    seed.mkdir()
    _proposed_registry(seed)
    write_data_dir(seed, {"BTCUSD": dated_target_hit_bars()})

    def _run(sub, extra):
        base = tmp_path / sub
        shutil.copytree(seed, base)
        reg = Registry(base / "reg.jsonl")
        data = base / "data"
        n_before = sum(1 for _ in reg.entries())
        rc = screen_run(["--registry", str(reg.log_path), "--data-dir", str(data),
                         "--artifacts-dir", str(base / "art"), *extra])
        assert rc == 0
        out = []
        for e in list(Registry(reg.log_path).entries())[n_before:]:   # appended only
            e = dict(e); e.pop("ts_utc", None); e.pop("prev_entry_hash", None)
            out.append(json.dumps(e, sort_keys=True))
        assert out, "the run appended nothing -- the comparison would be vacuous"
        return out, base

    plain, _ = _run("plain", [])
    dated, base = _run("dated", ["--deadline-utc", "2099-01-01T00:00:00Z"])
    assert dated == plain
    result = json.loads((base / "logs" / "screen_result.json").read_text(encoding="utf-8"))
    assert result["stopped_at_deadline"] is False and result["deferred"] == 0
