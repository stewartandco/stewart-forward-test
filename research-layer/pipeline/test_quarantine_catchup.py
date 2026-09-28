"""Automatic catch-up of owed quarantine days (2026-09-28).

The daily records ONE date and DEFERS any class whose bar is not published
yet (equity ETFs lag a day, FRED fx about a week). Before this, a deferred
date was recorded only by a hand `--date` run, which never happened: 253
equity_etf + 19 fx strategies sat at zero forward days for weeks. `--catch-up`
re-runs the existing per-date path for every date a quarantined strategy is
owed, so the per-date refusals and provenance checks all still apply.
"""
import pytest

from . import quarantine
from .quarantine import recordable_owed_dates, catch_up, MAX_CATCHUP_DATES

A, B = "strat-a", "strat-b"


def test_a_date_with_no_decision_is_owed():
    owed = {A: {"2026-09-01": {"QQQ"}, "2026-09-02": {"QQQ"}}}
    recorded = {(A, "2026-09-01", "QQQ")}
    assert recordable_owed_dates(owed, {A: {"QQQ"}}, recorded) == ["2026-09-02"]


def test_a_partially_recorded_date_is_owed():
    """One asset in, one missing: the runner records only the missing row
    (idempotent per strategy/date/asset), so the date is still owed."""
    owed = {A: {"2026-09-01": {"SPY", "QQQ"}}}
    recorded = {(A, "2026-09-01", "SPY")}
    assert recordable_owed_dates(owed, {A: {"SPY", "QQQ"}}, recorded) == ["2026-09-01"]


def test_a_calendar_gap_date_is_never_owed():
    """The per-date runner DEFERS a strategy when any of its assets has no bar
    on the date. Counting such a date as owed would retry it every day and
    fail the Sentinel forever, so it is not owed by the runner's own rule."""
    owed = {A: {"2026-09-01": {"EURUSD"}}}                 # GBPUSD has no bar
    assert recordable_owed_dates(owed, {A: {"EURUSD", "GBPUSD"}}, set()) == []


def test_dates_are_unioned_across_strategies_sorted_and_deduplicated():
    owed = {A: {"2026-09-03": {"QQQ"}, "2026-09-01": {"QQQ"}},
            B: {"2026-09-01": {"BTCUSD"}, "2026-09-02": {"BTCUSD"}}}
    uni = {A: {"QQQ"}, B: {"BTCUSD"}}
    assert recordable_owed_dates(owed, uni, set()) == [
        "2026-09-01", "2026-09-02", "2026-09-03"]


def test_nothing_owed_is_an_empty_list():
    owed = {A: {"2026-09-01": {"QQQ"}}}
    assert recordable_owed_dates(owed, {A: {"QQQ"}}, {(A, "2026-09-01", "QQQ")}) == []


def test_catch_up_runs_every_date_oldest_first():
    ran = []
    assert catch_up(["2026-09-01", "2026-09-02"], lambda d: ran.append(d) or 0) == 0
    assert ran == ["2026-09-01", "2026-09-02"]


def test_one_failing_date_does_not_stop_the_rest_but_fails_the_run(capsys):
    """A refused date (e.g. a bar restatement) must not strand every later
    date behind it, and must still exit non-zero so the Sentinel alarms."""
    ran = []

    def run_date(d):
        ran.append(d)
        return 1 if d == "2026-09-02" else 0

    rc = catch_up(["2026-09-01", "2026-09-02", "2026-09-03"], run_date)
    assert rc == 1
    assert ran == ["2026-09-01", "2026-09-02", "2026-09-03"]
    assert "2026-09-02" in capsys.readouterr().out


def test_catch_up_is_bounded_per_run_and_says_what_it_left(capsys):
    dates = [f"2026-07-{d:02d}" for d in range(1, 32)] + \
            [f"2026-08-{d:02d}" for d in range(1, 32)]
    assert len(dates) > MAX_CATCHUP_DATES
    ran = []
    assert catch_up(dates, lambda d: ran.append(d) or 0) == 0
    assert ran == dates[:MAX_CATCHUP_DATES]
    assert f"{len(dates) - MAX_CATCHUP_DATES} owed date(s) left" in capsys.readouterr().out


def test_nothing_owed_runs_nothing(capsys):
    assert catch_up([], lambda d: pytest.fail("ran a date")) == 0
    assert "nothing owed" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [
    ["--catch-up", "--review"],
    ["--catch-up", "--date", "2026-09-01"],
    [],
])
def test_exactly_one_mode_is_required(argv, tmp_path, capsys):
    rc = quarantine.run(argv + ["--registry", str(tmp_path / "r.jsonl"),
                                "--data-dir", str(tmp_path)])
    assert rc == 1
    assert "exactly one of" in capsys.readouterr().err


def test_an_exception_on_one_date_does_not_strand_the_rest(capsys):
    """The per-date path RAISES on some refusals (a non-identical duplicate,
    a writer ValueError). That must count as a failed date, not end the run."""
    ran = []

    def run_date(d):
        ran.append(d)
        if d == "2026-09-01":
            raise ValueError("boom")
        return 0

    assert catch_up(["2026-09-01", "2026-09-02"], run_date) == 1
    assert ran == ["2026-09-01", "2026-09-02"]
    out = capsys.readouterr().out
    assert "boom" in out and "FAILED on 2026-09-01" in out


# ---------------- the live inputs: price files + chain entries ----------------

class _StubChain:
    """Just enough of Registry for existing_decisions()."""
    def __init__(self, decisions=()):
        self._d = [{"entry_type": "quarantine_decision", "ts_utc": "2026-09-28T00:20:00+00:00",
                    "payload": {"strategy_id": s, "date": d, "asset": a}}
                   for s, d, a in decisions]

    def entries(self):
        return list(self._d)


def _csv(data_dir, asset, dates, stamp=" 00:00:00"):
    rows = ["date,open,high,low,close,volume"] + [
        f"{d}{stamp},1,1,1,1,1" for d in dates]
    (data_dir / f"{asset}_1d.csv").write_text("\n".join(rows) + "\n")


def _spec(*assets):
    return {"universe": {"assets": list(assets)}}


def test_live_owed_dates_start_after_entry_and_stop_at_the_last_bar(tmp_path):
    from .quarantine import _owed_dates_for_catch_up
    _csv(tmp_path, "QQQ", ["2026-09-01", "2026-09-02", "2026-09-03"])
    got = _owed_dates_for_catch_up(_StubChain(), [A], {A: "2026-09-01"},
                                   {A: _spec("QQQ")}, tmp_path)
    assert got == ["2026-09-02", "2026-09-03"]      # entry day itself is never owed


def test_a_recorded_date_is_no_longer_owed_so_nothing_retries_forever(tmp_path):
    from .quarantine import _owed_dates_for_catch_up
    _csv(tmp_path, "QQQ", ["2026-09-01", "2026-09-02"])
    chain = _StubChain([(A, "2026-09-02", "QQQ")])
    assert _owed_dates_for_catch_up(chain, [A], {A: "2026-09-01"},
                                    {A: _spec("QQQ")}, tmp_path) == []


def test_a_multi_asset_calendar_gap_is_not_owed_live(tmp_path):
    from .quarantine import _owed_dates_for_catch_up
    _csv(tmp_path, "EURUSD", ["2026-09-01", "2026-09-02", "2026-09-03"])
    _csv(tmp_path, "GBPUSD", ["2026-09-01", "2026-09-03"])     # no 09-02 bar
    got = _owed_dates_for_catch_up(_StubChain(), [A], {A: "2026-09-01"},
                                   {A: _spec("EURUSD", "GBPUSD")}, tmp_path)
    assert got == ["2026-09-03"]


def test_a_strategy_with_a_missing_price_file_is_skipped_and_named(tmp_path, capsys):
    from .quarantine import _owed_dates_for_catch_up
    _csv(tmp_path, "QQQ", ["2026-09-01", "2026-09-02"])
    got = _owed_dates_for_catch_up(
        _StubChain(), [A, B], {A: "2026-09-01", B: "2026-09-01"},
        {A: _spec("QQQ"), B: _spec("NOPE")}, tmp_path)
    assert got == ["2026-09-02"]
    assert B in capsys.readouterr().out
