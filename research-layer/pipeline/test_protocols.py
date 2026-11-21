"""Task 1 (beats-hold): protocol names live in pipeline/protocols.py and every
site that exact-matched gauntlet-protocol-v6.1 also accepts v6.2. Note-prefix
checks (which arm v6.1 rules) stay keyed on the v6.1 note only."""
from pathlib import Path

from . import gauntlet_stats as gs
from . import gauntlet_worker as gw
from . import loop
from .common import entry_hash
from .protocols import (LIVE_V1, LIVE_V2, NOTE_LIVE_V2, NOTE_V62, PROTOCOL_V61,
                        PROTOCOL_V62, V61_FAMILY)
from .test_gauntlet import gauntlet_registry, run_verifier

LAYER = Path(__file__).resolve().parent.parent


def _v62_chain(tmp_path):
    reg, spec = gauntlet_registry(tmp_path)
    v = reg.record_verdict(spec["strategy_id"], "gauntlet", "pass",
                           {"protocol": PROTOCOL_V62}, "0" * 64)
    return reg, spec, v


def test_protocol_constants():
    assert PROTOCOL_V61 == "gauntlet-protocol-v6.1"
    assert PROTOCOL_V62 == "gauntlet-protocol-v6.2"
    assert V61_FAMILY == (PROTOCOL_V61, PROTOCOL_V62)
    assert NOTE_V62 == "gauntlet-protocol-v6.2: "
    assert LIVE_V1 == "quarantine-live-protocol-v1"
    assert LIVE_V2 == "quarantine-live-protocol-v2"
    assert NOTE_LIVE_V2 == "quarantine-live-protocol-v2: "


def test_v62_verdict_counts_as_unstatted_for_stats(tmp_path):
    reg, spec, v = _v62_chain(tmp_path)
    view = gs._View()
    reg.snapshot(on_entry=view.on_entry)
    assert [x["vh"] for x in view.verdicts] == [entry_hash(v)]


def test_registry_snapshot_tracks_v62_verdict(tmp_path):
    reg, spec, v = _v62_chain(tmp_path)
    snap = reg.snapshot(track_stats=True)
    assert dict(snap.v61_verdicts) == {entry_hash(v): spec["strategy_id"]}


def test_worker_repairs_v62_orphan(tmp_path):
    reg, spec, v = _v62_chain(tmp_path)
    view = gw._View()
    snap = reg.snapshot(on_entry=view.on_entry)
    assert [sid for sid, _ in view.orphans(snap)] == [spec["strategy_id"]]


def test_loop_ignores_v62_orphan(tmp_path):
    reg, spec, v = _v62_chain(tmp_path)
    assert loop._gauntlet_orphans(reg) == []


def test_verifier_accepts_stats_on_v62_verdict(tmp_path):
    reg, spec, v = _v62_chain(tmp_path)
    reg.record_state_change(spec["strategy_id"], "quarantine", "gauntlet pass")
    reg.record_gauntlet_stats(spec["strategy_id"], entry_hash(v),
                              {"trials_n": 3, "deflated_sharpe": 0.4})
    r = run_verifier(reg.log_path)
    assert r.returncode == 0, r.stdout


def test_no_bare_v61_literal_left():
    files = [p for p in (LAYER / "pipeline").glob("*.py")
             if not p.name.startswith("test_")] + [LAYER / "verify_registry.py"]
    bad = []
    for p in files:
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for op in ("==", "!="):
                if (f'{op} "gauntlet-protocol-v6.1"' in line
                        or f"{op} PROTOCOL_V61" in line):
                    bad.append(f"{p.name}:{n}: {line.strip()}")
    assert not bad, bad
