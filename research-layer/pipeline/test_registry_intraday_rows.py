"""2b-2 design s3.2: a 9-field row only on a cell key, a 7-field row only on
a bare asset. The daily shape and its messages are unchanged."""
from __future__ import annotations

import pytest

from .registry import validated_quarantine_decision


def _daily(**kw):
    row = {"strategy_id": "a" * 16, "date": "2026-10-10", "asset": "BTCUSD",
           "action": "hold", "price": 100.0, "position_frac": 0.0,
           "equity": 1.0}
    row.update(kw)
    return row


def _hourly(**kw):
    row = _daily(asset="BTCUSDT@1h", entries=1, exits=2)
    row.update(kw)
    return row


def test_the_daily_shape_is_accepted_unchanged():
    assert validated_quarantine_decision(_daily()) == _daily()


def test_the_intraday_shape_is_accepted():
    assert validated_quarantine_decision(_hourly()) == _hourly()


def test_counts_on_a_bare_asset_are_refused():
    with pytest.raises(ValueError, match="unknown keys"):
        validated_quarantine_decision(_daily(entries=0, exits=0))


def test_a_cell_key_without_counts_is_refused():
    row = _hourly()
    del row["entries"], row["exits"]
    with pytest.raises(ValueError, match="missing"):
        validated_quarantine_decision(row)


@pytest.mark.parametrize("bad", [-1, 1.0, True, "1", None])
def test_a_count_must_be_a_non_negative_int(bad):
    with pytest.raises(ValueError, match="entries"):
        validated_quarantine_decision(_hourly(entries=bad))


def test_a_malformed_cell_key_is_refused():
    with pytest.raises(ValueError, match="cell key"):
        validated_quarantine_decision(_hourly(asset="BTCUSDT@1d"))
