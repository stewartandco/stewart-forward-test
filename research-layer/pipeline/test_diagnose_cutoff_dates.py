"""D-i3 readers audit: the layer-root diagnose_* scripts read artifacts/<sid>/
equity.csv and fence it by a STRING compare on the date column. An intraday
bundle's daily-mark stamps are 'YYYY-MM-DD 23:00:00', and
'2023-12-31 23:00:00' <= '2023-12-31' is False, which silently drops the cutoff
day. The compare is on the date part now; 1d rows (10 chars) are unchanged."""
from __future__ import annotations

import diagnose_live_gate
import diagnose_pbo_power
import diagnose_pbo_validity

CUTOFF = "2023-12-31"


def _bundle(tmp_path, rows):
    d = tmp_path / "abc"
    d.mkdir()
    (d / "equity.csv").write_text(
        "date,combined_equity\n" + "".join(f"{a},{b}\n" for a, b in rows),
        encoding="utf-8")
    return tmp_path


DAILY = [("2023-12-29", 1.0), ("2023-12-30", 1.1), ("2023-12-31", 1.2),
         ("2024-01-01", 1.3)]
STAMPED = [("2023-12-29 23:00:00", 1.0), ("2023-12-30 23:00:00", 1.1),
           ("2023-12-31 23:00:00", 1.2), ("2024-01-01 23:00:00", 1.3)]


def test_live_gate_1d_curve_is_unchanged(tmp_path):
    arts = _bundle(tmp_path, DAILY)
    assert diagnose_live_gate.train_curve(arts, "abc", CUTOFF) == [
        ("2023-12-29", 1.0), ("2023-12-30", 1.1), ("2023-12-31", 1.2)]


def test_live_gate_intraday_curve_keeps_the_cutoff_day(tmp_path):
    arts = _bundle(tmp_path, STAMPED)
    assert diagnose_live_gate.train_curve(arts, "abc", CUTOFF) == [
        ("2023-12-29 23:00:00", 1.0), ("2023-12-30 23:00:00", 1.1),
        ("2023-12-31 23:00:00", 1.2)]


def test_live_gate_accepts_a_full_stamp_cutoff_too(tmp_path):
    arts = _bundle(tmp_path, STAMPED)
    assert len(diagnose_live_gate.train_curve(
        arts, "abc", "2023-12-31 00:00:00")) == 3


def test_pbo_power_fence_keeps_the_cutoff_day(tmp_path):
    assert len(diagnose_pbo_power.train_curve(_bundle(tmp_path, DAILY), "abc", CUTOFF)) == 3
    sub = tmp_path / "s"
    sub.mkdir()
    assert len(diagnose_pbo_power.train_curve(_bundle(sub, STAMPED), "abc", CUTOFF)) == 3


def test_pbo_validity_fence_keeps_the_cutoff_day(tmp_path):
    a = diagnose_pbo_validity.train_returns(_bundle(tmp_path, DAILY), "abc", CUTOFF)
    sub = tmp_path / "s"
    sub.mkdir()
    b = diagnose_pbo_validity.train_returns(_bundle(sub, STAMPED), "abc", CUTOFF)
    assert len(a) == len(b) == 2 and a == b
