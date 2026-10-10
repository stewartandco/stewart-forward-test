from __future__ import annotations

import json

from tools import daily_parity as dp


def test_compare_identical_and_different(tmp_path):
    a = {"sample": ["s1"], "results": {"s1": {"run_full": "x"}}, "errors": {}}
    b = json.loads(json.dumps(a))
    (tmp_path / "a.json").write_text(json.dumps(a))
    (tmp_path / "b.json").write_text(json.dumps(b))
    assert dp.main(["compare", str(tmp_path / "a.json"), str(tmp_path / "b.json")]) == 0
    b["results"]["s1"]["run_full"] = "y"
    (tmp_path / "b.json").write_text(json.dumps(b))
    assert dp.main(["compare", str(tmp_path / "a.json"), str(tmp_path / "b.json")]) == 1


def test_select_includes_special_specs_and_stratifies():
    def spec(sid, cls, special=False):
        blocks = [{"type": "vol_target" if special else "fixed_fraction"}]
        return {"strategy_id": sid, "blocks": blocks,
                "universe": {"asset_class": cls, "timeframe": "1d"}}
    specs = {f"v{i}": spec(f"v{i}", "crypto", True) for i in range(3)}
    specs.update({f"f{i}": spec(f"f{i}", "fx") for i in range(5)})
    specs.update({f"e{i}": spec(f"e{i}", "equity_etf") for i in range(5)})
    specs["h1"] = dict(spec("h1", "crypto"), universe={"asset_class": "crypto", "timeframe": "1h"})
    got = dp.select(specs, 7, 1)
    assert {"v0", "v1", "v2"} <= set(got) and "h1" not in got and len(got) == 7
    assert any(s.startswith("f") for s in got) and any(s.startswith("e") for s in got)
