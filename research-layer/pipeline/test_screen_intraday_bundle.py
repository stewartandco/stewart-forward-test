"""2b-3 D-i3: an intraday screen bundle commits a DAILY-marked equity.csv; the
per-bar curve goes to a gitignored equity_bars.csv pinned by sha in config.json.
Every 1d bundle stays byte-for-byte what it was (no new key, no new file).

Why: a 1h bundle's per-bar equity.csv measured 1.7-2.1 MB each, and bundles go
to the PUBLIC repo (research-layer/CLAUDE.md, "Intraday")."""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from . import screen
from .bars import daily_marks
from .test_screen import (cell_registry, chain_protocol_note, screen_run,
                          stamped_target_hit_bars, write_cell_data_dir)
from .test_screen_commit_list import NAMES, _listed

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "screen_bundle_1d"
# the 1d golden's three files were written by BASE a04e855c3's write_artifacts:
# regeneration command in fixtures/screen_bundle_1d/gen_golden.py


def _lf_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _hourly_equity(days=3):
    """72 hourly (stamp, value) rows over `days` UTC days; value = row index
    scaled, so each day's last bar is distinguishable."""
    rows = []
    for i in range(24 * days):
        d, h = divmod(i, 24)
        rows.append((f"2023-01-{d + 1:02d} {h:02d}:00:00", 1.0 + i / 1000))
    return rows


def _intraday_spec(tf="1h"):
    return {"strategy_id": "bbbbbbbbbbbbbbbb",
            "universe": {"assets": ["BTCUSDT"], "asset_class": "crypto",
                         "timeframe": tf, "session": "24x7"}}


def _read(path):
    return list(csv.reader(path.read_text(encoding="utf-8").splitlines()))


def test_a_1d_bundle_is_byte_identical(tmp_path):
    inp = json.loads((GOLDEN / "inputs.json").read_text(encoding="utf-8"))
    r = inp["result"]
    r["equity"] = [tuple(x) for x in r["equity"]]
    bundle = screen.write_artifacts(tmp_path, inp["spec"], r, inp["cutoff"],
                                    inp["data_hashes"], inp["data_end"])
    for name in ("trades.csv", "equity.csv", "config.json"):
        assert (bundle / name).read_bytes() == (GOLDEN / name).read_bytes(), name
    assert sorted(p.name for p in bundle.iterdir()) == [
        "config.json", "equity.csv", "trades.csv"]            # no equity_bars.csv


def test_a_spec_with_no_timeframe_is_a_1d_bundle(tmp_path):
    """An older spec has no universe.timeframe: a bare ["timeframe"] would raise."""
    spec = {"strategy_id": "abc123", "universe": {"assets": ["BTCUSD"]}}
    b = screen.write_artifacts(tmp_path, spec,
                               {"trades": [], "equity": [("2024-01-01", 1.0)]},
                               "2023-12-31", {"BTCUSD_1d": "d"}, {"BTCUSD_1d": "e"})
    assert not (b / "equity_bars.csv").exists()
    assert "equity_bars_sha256" not in json.loads((b / "config.json").read_text())


def test_an_intraday_bundle_carries_daily_marks(tmp_path):
    eq = _hourly_equity(3)
    b = screen.write_artifacts(tmp_path, _intraday_spec(),
                               {"trades": [], "equity": eq}, "2023-12-31",
                               {"BTCUSDT_1h": "d"}, {"BTCUSDT_1h": "e"})
    rows = _read(b / "equity.csv")
    assert rows[0] == ["date", "combined_equity"]
    assert len(rows) == 1 + 3
    # each row is the day's LAST bar, the stamp kept in full (daily_marks' format)
    assert [r[0] for r in rows[1:]] == ["2023-01-01 23:00:00", "2023-01-02 23:00:00",
                                        "2023-01-03 23:00:00"]
    assert [float(r[1]) for r in rows[1:]] == [eq[23][1], eq[47][1], eq[71][1]]
    assert [(r[0], float(r[1])) for r in rows[1:]] == daily_marks(eq)


def test_equity_bars_holds_todays_per_bar_rows_and_the_config_pins_it(tmp_path):
    eq = _hourly_equity(3)
    b = screen.write_artifacts(tmp_path, _intraday_spec(),
                               {"trades": [], "equity": eq}, "2023-12-31",
                               {"BTCUSDT_1h": "d"}, {"BTCUSDT_1h": "e"})
    raw = (b / "equity_bars.csv").read_bytes()
    assert b"\r" not in raw                                    # lineterminator "\n"
    rows = _read(b / "equity_bars.csv")
    assert rows[0] == ["date", "combined_equity"] and len(rows) == 1 + 72
    assert [(r[0], float(r[1])) for r in rows[1:]] == eq
    cfg = json.loads((b / "config.json").read_text(encoding="utf-8"))
    assert cfg["equity_bars_sha256"] == hashlib.sha256(raw).hexdigest()


def test_bundle_hash_ignores_equity_bars_and_config_pins_it(tmp_path):
    b = screen.write_artifacts(tmp_path, _intraday_spec(),
                               {"trades": [], "equity": _hourly_equity(3)},
                               "2023-12-31", {"BTCUSDT_1h": "d"}, {"BTCUSDT_1h": "e"})
    before = screen.bundle_hash(b)
    cfg = json.loads((b / "config.json").read_text(encoding="utf-8"))
    assert cfg["equity_bars_sha256"] == _lf_sha(b / "equity_bars.csv")
    (b / "equity_bars.csv").unlink()
    assert screen.bundle_hash(b) == before                     # three names only
    # ...but the pin is inside config.json, which bundle_hash DOES cover, so
    # the per-bar curve is still bound to the chained artifacts_hash
    assert b"equity_bars_sha256" in (b / "config.json").read_bytes()


def test_the_pin_is_the_sha_over_lf_normalised_bytes(tmp_path):
    """bundle_hash's convention: CRLF -> LF before hashing. A checkout that
    turned the file CRLF must still match the pin."""
    b = screen.write_artifacts(tmp_path, _intraday_spec(),
                               {"trades": [], "equity": _hourly_equity(2)},
                               "2023-12-31", {"BTCUSDT_1h": "d"}, {"BTCUSDT_1h": "e"})
    p = b / "equity_bars.csv"
    pin = json.loads((b / "config.json").read_text())["equity_bars_sha256"]
    p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))
    assert screen._lf_sha256(p) == pin       # the pin's own function, on CRLF bytes
    assert hashlib.sha256(p.read_bytes()).hexdigest() != pin   # the raw sha would not


def test_an_intraday_screen_run_writes_daily_marks_and_per_bar_files(tmp_path):
    """End to end through screen.run: the equity.csv no longer carries the
    per-bar rows; equity_bars.csv does, and it is what the engine simulated."""
    reg, (spec,) = cell_registry(tmp_path, [(["ETHUSDT"], "1h")])
    bars = stamped_target_hit_bars(1)
    data = write_cell_data_dir(tmp_path, {("ETHUSDT", "1h"): bars})
    chain_protocol_note(reg)
    art = tmp_path / "art"
    assert screen_run(["--registry", str(reg.log_path), "--data-dir", str(data),
                       "--artifacts-dir", str(art), "--workers", "1"]) == 0
    b = art / spec["strategy_id"]
    per_bar = _read(b / "equity_bars.csv")[1:]
    daily = _read(b / "equity.csv")[1:]
    assert len(per_bar) > len(daily) >= 1
    assert [(d, float(v)) for d, v in daily] == daily_marks(
        [(d, float(v)) for d, v in per_bar])
    cfg = json.loads((b / "config.json").read_text(encoding="utf-8"))
    assert cfg["equity_bars_sha256"] == _lf_sha(b / "equity_bars.csv")


def test_equity_bars_is_never_listed_for_commit(tmp_path):
    reg, (spec,) = cell_registry(tmp_path, [(["ETHUSDT"], "1h")])
    data = write_cell_data_dir(tmp_path, {("ETHUSDT", "1h"): stamped_target_hit_bars(1)})
    chain_protocol_note(reg)
    rc = screen.run(["--registry", str(reg.log_path), "--data-dir", str(data),
                     "--artifacts-dir", str(reg.log_path.parent / "artifacts"),
                     "--logs-dir", str(tmp_path / "logs"), "--workers", "1"])
    assert rc == 0
    sid = spec["strategy_id"]
    on_disk = {p.name for p in (tmp_path / "artifacts" / sid).iterdir()}
    assert "equity_bars.csv" in on_disk                        # it WAS written...
    assert sorted(_listed(tmp_path)) == sorted(                # ...and never listed
        f"research-layer/artifacts/{sid}/{n}" for n in NAMES)


def test_equity_bars_is_gitignored():
    here = Path(__file__).resolve().parent
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=here,
                             capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")

    def ignored(rel):
        return subprocess.run(["git", "check-ignore", "-q", rel],
                              cwd=top).returncode == 0
    assert ignored("research-layer/artifacts/0123456789abcdef/equity_bars.csv")
    # the three committed names stay committable
    for n in NAMES:
        assert not ignored(f"research-layer/artifacts/0123456789abcdef/{n}")
