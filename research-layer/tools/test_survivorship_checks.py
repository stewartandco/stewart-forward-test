import math

import pytest

from tools.survivorship_checks import (
    GAP_DAYS, JUMP_RATIO, SPLICE_TOL, SpliceBreak,
    apply_cut, first_bar, hidden_splices, parity, splice,
)


def bar(date, close=1.0, volume=10.0):
    return {"date": f"{date} 00:00:00", "open": close, "high": close,
            "low": close, "close": close, "volume": volume}


def test_constants():
    assert (GAP_DAYS, JUMP_RATIO, SPLICE_TOL) == (7, 10.0, 0.05)


def test_parity_pass_and_mismatch():
    a = [bar("2021-01-01", 5.0), bar("2021-01-02", 6.0)]
    b = [dict(x) for x in a]
    assert parity(a, b) == []
    b[1]["high"] = 6.0 + 1e-9
    out = parity(a, b)
    assert len(out) == 1 and "2021-01-02" in out[0] and "high" in out[0]


def test_parity_duplicate_dates_reported():
    a = [bar("2021-01-01"), bar("2021-01-01")]
    b = [bar("2021-01-01")]
    out = parity(a, b)
    assert any("duplicate" in s and "2021-01-01" in s for s in out)


def test_parity_ignores_unshared_dates():
    pipe = [bar(f"2021-02-{d:02d}") for d in range(1, 6)]
    arch = [bar(f"2021-01-{d:02d}") for d in range(1, 32)] + pipe
    assert parity(arch, pipe) == []


def test_first_bar_takes_the_later():
    assert first_bar("2020-01-01", "2021-06-18") == "2021-06-18"
    assert first_bar("2021-06-18", "2020-01-01") == "2021-06-18"
    assert first_bar("2020-01-01", None) == "2020-01-01"


def test_splice_ratio_and_continuity():
    pred = [bar("2024-09-01", 1190.0), bar("2024-09-02", 1200.0),
            bar("2024-09-03", 1210.0)]
    succ = [bar("2024-09-01", 0.04), bar("2024-09-03", 0.05, volume=7.0),
            bar("2024-09-04", 0.051)]
    out = splice(pred, succ, "2024-09-03", 24000.0)
    assert [b["date"][:10] for b in out] == [
        "2024-09-01", "2024-09-02", "2024-09-03", "2024-09-04"]
    assert out[2]["close"] == pytest.approx(0.05 * 24000.0)
    assert out[2]["volume"] == 7.0
    assert out[3]["open"] == pytest.approx(0.051 * 24000.0)
    assert out[1]["close"] == 1200.0
    bad = [bar("2024-09-03", 0.05 * 1.06)]
    with pytest.raises(SpliceBreak):
        splice(pred[:2], bad, "2024-09-03", 24000.0)


def test_hidden_splice_gap_and_jump():
    ok7 = [bar("2021-01-01"), bar("2021-01-08")]
    assert hidden_splices(ok7) == []
    gap8 = [bar("2021-01-01"), bar("2021-01-09")]
    out = hidden_splices(gap8)
    assert len(out) == 1 and "2021-01-09" in out[0]
    luna = [bar("2022-05-10", 100.0), bar("2022-05-11", 5.0)]
    out = hidden_splices(luna)
    assert len(out) == 1 and "2022-05-11" in out[0]
    up = [bar("2022-05-10", 1.0), bar("2022-05-11", 11.0)]
    assert len(hidden_splices(up)) == 1
    with pytest.raises(ValueError):
        hidden_splices([bar("2021-01-02"), bar("2021-01-01")])


def test_apply_cut():
    bars = [bar("2021-01-01"), bar("2021-01-02"), bar("2021-01-03")]
    assert [b["date"][:10] for b in apply_cut(bars, "2021-01-02")] == [
        "2021-01-02", "2021-01-03"]


def test_splice_empty_sides_raise():
    pred = [bar("2024-09-01", 1200.0)]
    succ = [bar("2024-09-03", 0.05)]
    with pytest.raises(SpliceBreak, match="predecessor"):
        splice(pred, succ, "2024-08-01", 24000.0)
    with pytest.raises(SpliceBreak, match="successor"):
        splice(pred, succ, "2024-10-01", 24000.0)


def test_splice_nonfinite_or_nonpositive_close_raises():
    pred = [bar("2024-09-01", 1200.0)]
    for c in (float("nan"), float("inf"), 0.0, -1.0):
        with pytest.raises(SpliceBreak):
            splice(pred, [bar("2024-09-03", c)], "2024-09-03", 24000.0)
    with pytest.raises(SpliceBreak):
        splice([bar("2024-09-01", float("nan"))], [bar("2024-09-03", 0.05)],
               "2024-09-03", 24000.0)


def test_hidden_splices_flags_invalid_close():
    out = hidden_splices([bar("2022-05-10", 100.0), bar("2022-05-11", 0.0)])
    assert any("2022-05-11" in s for s in out)
    out = hidden_splices([bar("2022-05-10", 100.0), bar("2022-05-11", float("nan"))])
    assert any("2022-05-11" in s for s in out)


def test_parity_no_shared_dates_fails():
    assert parity([bar("2021-01-01")], [bar("2021-02-01")])
    assert parity([], [bar("2021-02-01")])
    assert parity([], [])
