"""verify_registry.py invariant 8 under D9 -- re-trials are not corruption.

The chained pre-declaration is docs/notes/family-openness-v1.md, entry 16,071
on the live registry. Where this module and that note disagree, THE NOTE WINS.

The note ends chain-wide composition uniqueness: "a buried COMPOSITION ... may
be proposed again as a NEW strategy with a new id and a new number". The
verifier was written when that uniqueness held for every registration and was
never updated when SP5 Phase 2 shipped D9, so it declared the first two real
re-trials (chain lines 16183/16184) corruption and stopped the pipeline. What
it must check instead is the SAME rule the composer's re-trial oracle applies:

  * every EARLIER registration of the fingerprint is currently BURIED, and
  * the latest burying verdict's cutoff is >= RETRIAL_WINDOW_DAYS behind the
    OLDEST referenced cell's data end.

Two registrations of one composition in ONE run are never a re-trial, however
open the window: same data by construction.

Run: python -m pytest pipeline/test_verify_registry_d9.py -q
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

from . import composer
from .common import content_id
from .registry import Registry
from .test_pipeline import make_strategy, register_example_blocks

HERE = Path(__file__).resolve().parent
LAYER = HERE.parent

CUTOFF = "2023-12-31"
CELL = "MNQ_15m"                      # make_strategy's only cell


def run_verifier(log_path, *extra):
    return subprocess.run(
        [sys.executable, str(LAYER / "verify_registry.py"), str(log_path),
         *extra],
        capture_output=True, text=True)


def seeded(tmp_path):
    """A chain with one accepted card, the example grammar, and one strategy
    registered under run_id 'run-a'."""
    reg = Registry(tmp_path / "registry_log.jsonl")
    reg.register_card({"card_id": "card-1", "claim": "c", "quote": "q",
                       "topics": [], "tags": {"asset_classes": ["crypto"]},
                       "review": {"status": "pending", "reject_reason": None},
                       "source": {}, "links": [], "credibility_tier": "practitioner"})
    reg.review_card("card-1", "accepted", "coen")
    register_example_blocks(reg)
    spec = make_strategy(["card-1"])
    spec["generator"] = dict(spec["generator"], run_id="run-a")
    spec["strategy_id"] = None
    spec["strategy_id"] = content_id(spec, "strategy_id")
    reg.register_strategy(spec)
    return reg, spec


def twin_of(spec, run_id="run-b", created="2026-08-31T00:00:00Z"):
    """Same composition, new id, a later run -- what a re-trial IS."""
    out = json.loads(json.dumps(spec))
    out["created_utc"] = created
    out["generator"] = dict(out["generator"], run_id=run_id)
    out["strategy_id"] = None
    out["strategy_id"] = content_id(out, "strategy_id")
    assert out["strategy_id"] != spec["strategy_id"]
    assert (composer.composition_fingerprint(out)
            == composer.composition_fingerprint(spec))
    return out


def write_cutoff(tmp_path, sid, cutoff=CUTOFF, stage="gauntlet"):
    root = tmp_path / "artifacts" / sid
    target = root / stage if stage else root
    target.mkdir(parents=True, exist_ok=True)
    (target / "config.json").write_text(json.dumps({"cutoff": cutoff}),
                                        encoding="utf-8")


def write_bars(tmp_path, last_date, cell=CELL):
    d = tmp_path / "data"
    d.mkdir(parents=True, exist_ok=True)
    (d / (cell + ".csv")).write_text(
        "date,open,high,low,close,volume\n"
        "2019-01-02,1,1,1,1,1\n" + last_date + ",1,1,1,1,1\n",
        encoding="utf-8")


def bury(reg, sid, via_gauntlet=True):
    reg.record_state_change(sid, "screened")
    if via_gauntlet:
        reg.record_state_change(sid, "gauntlet")
    reg.record_state_change(sid, "graveyard", "edge_decay")


# ------------- the regression: a legitimate re-trial is not corruption -------

def test_a_legitimate_retrial_of_a_buried_composition_verifies(tmp_path):
    """Chain lines 16183/16184, in miniature. Original buried out of the
    gauntlet at cutoff 2023-12-31; the cell's bars now end well past
    RETRIAL_WINDOW_DAYS beyond it; the re-trial is a NEW numbered trial."""
    reg, spec = seeded(tmp_path)
    bury(reg, spec["strategy_id"])
    write_cutoff(tmp_path, spec["strategy_id"])
    write_bars(tmp_path, "2026-08-30")
    reg.register_strategy(twin_of(spec))

    out = run_verifier(reg.log_path)
    assert out.returncode == 0, out.stdout
    assert "duplicate composition" not in out.stdout


def test_a_retrial_that_itself_reaches_quarantine_still_verifies(tmp_path):
    """50f48ae9a07d01cc's shape: the re-trial passed the gauntlet and sits in
    quarantine. The ORIGINAL is what has to be buried, not the re-trial."""
    reg, spec = seeded(tmp_path)
    bury(reg, spec["strategy_id"])
    write_cutoff(tmp_path, spec["strategy_id"])
    write_bars(tmp_path, "2026-08-30")
    twin = twin_of(spec)
    reg.register_strategy(twin)
    reg.record_state_change(twin["strategy_id"], "screened")
    reg.record_state_change(twin["strategy_id"], "gauntlet")
    reg.record_state_change(twin["strategy_id"], "quarantine")

    out = run_verifier(reg.log_path)
    assert out.returncode == 0, out.stdout


# ------------- what must STILL fail -----------------------------------------

def test_a_duplicate_of_a_quarantined_registration_fails(tmp_path):
    """family-openness-v1 declares this case as a TIGHTENING: 'there is no
    burying verdict, no expiry ... a second copy of it is a duplicate, not a
    re-trial'."""
    reg, spec = seeded(tmp_path)
    sid = spec["strategy_id"]
    reg.record_state_change(sid, "screened")
    reg.record_state_change(sid, "gauntlet")
    reg.record_state_change(sid, "quarantine")
    write_cutoff(tmp_path, sid, "2019-01-01")
    write_bars(tmp_path, "2026-08-30")
    reg.register_strategy(twin_of(spec))

    out = run_verifier(reg.log_path)
    assert out.returncode == 1
    assert "duplicate composition" in out.stdout
    assert sid in out.stdout


def test_a_duplicate_of_a_live_registration_fails(tmp_path):
    reg, spec = seeded(tmp_path)
    sid = spec["strategy_id"]
    for to in ("screened", "gauntlet", "quarantine", "live"):
        reg.record_state_change(sid, to)
    write_cutoff(tmp_path, sid, "2019-01-01")
    write_bars(tmp_path, "2026-08-30")
    reg.register_strategy(twin_of(spec))

    out = run_verifier(reg.log_path)
    assert out.returncode == 1
    assert "duplicate composition" in out.stdout


def test_a_duplicate_of_a_merely_proposed_registration_fails(tmp_path):
    """The pre-D9 case the invariant was written for, unchanged."""
    reg, spec = seeded(tmp_path)
    write_cutoff(tmp_path, spec["strategy_id"], "2019-01-01")
    write_bars(tmp_path, "2026-08-30")
    reg.register_strategy(twin_of(spec))
    out = run_verifier(reg.log_path)
    assert out.returncode == 1
    assert "duplicate composition" in out.stdout


def test_two_registrations_of_one_composition_in_the_same_run_fail(tmp_path):
    """The per-run invariant, keyed on generator.run_id. Same data by
    construction, so however open the window it is never a re-trial."""
    reg, spec = seeded(tmp_path)
    bury(reg, spec["strategy_id"])
    write_cutoff(tmp_path, spec["strategy_id"])
    write_bars(tmp_path, "2026-08-30")
    reg.register_strategy(twin_of(spec, run_id="run-a"))   # SAME run

    out = run_verifier(reg.log_path)
    assert out.returncode == 1
    assert "duplicate composition" in out.stdout
    assert "same run" in out.stdout


def test_a_third_registration_in_the_retrials_own_run_fails(tmp_path):
    """The window is open for the original, so the FIRST re-trial is legal.
    A SECOND copy in that same re-trial run is not: it duplicates a
    registration made minutes earlier on identical bars."""
    reg, spec = seeded(tmp_path)
    bury(reg, spec["strategy_id"])
    write_cutoff(tmp_path, spec["strategy_id"])
    write_bars(tmp_path, "2026-08-30")
    reg.register_strategy(twin_of(spec, created="2026-08-31T00:00:00Z"))
    reg.register_strategy(twin_of(spec, created="2026-08-31T00:00:01Z"))

    out = run_verifier(reg.log_path)
    assert out.returncode == 1
    assert out.stdout.count("duplicate composition") == 1, out.stdout
    assert "same run" in out.stdout


# ------------- the 183-day boundary, both sides -----------------------------

def _boundary_chain(tmp_path, data_end):
    reg, spec = seeded(tmp_path)
    bury(reg, spec["strategy_id"])
    write_cutoff(tmp_path, spec["strategy_id"])
    write_bars(tmp_path, data_end)
    reg.register_strategy(twin_of(spec))
    return run_verifier(reg.log_path)


def test_the_window_opens_exactly_at_the_declared_boundary(tmp_path):
    cutoff = date(2023, 12, 31)
    exactly = (cutoff + timedelta(days=composer.RETRIAL_WINDOW_DAYS)).isoformat()
    out = _boundary_chain(tmp_path, exactly)
    assert out.returncode == 0, out.stdout


def test_one_day_short_of_the_window_is_still_a_duplicate(tmp_path):
    cutoff = date(2023, 12, 31)
    short = (cutoff + timedelta(days=composer.RETRIAL_WINDOW_DAYS - 1)).isoformat()
    out = _boundary_chain(tmp_path, short)
    assert out.returncode == 1, out.stdout
    assert "duplicate composition" in out.stdout
    assert "window" in out.stdout


def test_the_oldest_cell_governs_a_pooled_spec(tmp_path):
    """retrial_oracle takes min() over the cells: the freshest cell must not
    carry the stalest one into a re-test the stale one has not earned."""
    reg = Registry(tmp_path / "registry_log.jsonl")
    reg.register_card({"card_id": "card-1", "claim": "c", "quote": "q",
                       "topics": [], "tags": {"asset_classes": ["crypto"]},
                       "review": {"status": "pending", "reject_reason": None},
                       "source": {}, "links": [], "credibility_tier": "practitioner"})
    reg.review_card("card-1", "accepted", "coen")
    register_example_blocks(reg)
    spec = make_strategy(["card-1"])
    spec["generator"] = dict(spec["generator"], run_id="run-a")
    spec["universe"] = dict(spec["universe"], assets=["MNQ", "MES"])
    spec["strategy_id"] = None
    spec["strategy_id"] = content_id(spec, "strategy_id")
    reg.register_strategy(spec)

    bury(reg, spec["strategy_id"])
    write_cutoff(tmp_path, spec["strategy_id"])
    write_bars(tmp_path, "2026-08-30", cell="MNQ_15m")
    write_bars(tmp_path, "2024-01-05", cell="MES_15m")     # 5 days past cutoff
    reg.register_strategy(twin_of(spec))

    out = run_verifier(reg.log_path)
    assert out.returncode == 1
    assert "duplicate composition" in out.stdout


# ------------- what the verifier CANNOT see ---------------------------------

def test_an_unreadable_window_is_reported_not_failed(tmp_path):
    """The cutoff lives in artifacts/<sid>/gauntlet/config.json and the bars
    live in data/ -- neither is on the chain. The composer's failure direction
    on missing evidence is SHUT ('an expiry that cannot be established is not
    an expiry'), because it is deciding. The verifier's must be the opposite:
    it is CHECKING, with strictly less evidence, and a chain must not be
    declared corrupt because an artifact bundle was pruned."""
    reg, spec = seeded(tmp_path)
    bury(reg, spec["strategy_id"])
    reg.register_strategy(twin_of(spec))       # no artifacts/, no data/

    out = run_verifier(reg.log_path)
    assert out.returncode == 0, out.stdout
    assert "window not verifiable" in out.stdout


def test_explicit_dirs_override_the_defaults(tmp_path):
    """The loop invokes the verifier with the registry path alone, so the
    default must resolve beside the log; a chain copied elsewhere for
    inspection needs the dirs passed in."""
    reg, spec = seeded(tmp_path)
    bury(reg, spec["strategy_id"])
    write_cutoff(tmp_path, spec["strategy_id"])
    write_bars(tmp_path, "2026-08-30")
    reg.register_strategy(twin_of(spec))

    elsewhere = tmp_path / "copy"
    elsewhere.mkdir()
    copied = elsewhere / "registry_log.jsonl"
    copied.write_bytes(reg.log_path.read_bytes())

    bare = run_verifier(copied)
    assert bare.returncode == 0 and "window not verifiable" in bare.stdout
    full = run_verifier(copied,
                        "--artifacts-dir", str(tmp_path / "artifacts"),
                        "--data-dir", str(tmp_path / "data"))
    assert full.returncode == 0, full.stdout
    assert "window not verifiable" not in full.stdout


# ------------- one rule, one implementation ---------------------------------

def test_the_verifier_and_the_composer_share_one_predicate():
    """No second interpretation of D9 that can drift from the composer's."""
    import importlib.util
    loaded = importlib.util.spec_from_file_location(
        "_vr_shared", LAYER / "verify_registry.py")
    mod = importlib.util.module_from_spec(loaded)
    loaded.loader.exec_module(mod)
    assert mod.retrial_verdict is composer.retrial_verdict


def test_retrial_verdict_names_why_it_refused():
    """The predicate's vocabulary, which is what lets the verifier tell a real
    violation apart from missing evidence."""
    spec = {"universe": {"assets": ["MNQ"], "timeframe": "15m"},
            "generator": {"run_id": "run-b"}}
    no_cut = lambda sid: None                                  # noqa: E731
    end = lambda cell: "2026-08-30"                            # noqa: E731
    cut = lambda sid: "2023-12-31"                             # noqa: E731

    assert composer.retrial_verdict([], spec, cut, end) == composer.RETRIAL_OK
    assert composer.retrial_verdict(
        [("old", "graveyard", "run-b")], spec, cut, end) == composer.RETRIAL_SAME_RUN
    assert composer.retrial_verdict(
        [("old", "quarantine", "run-a")], spec, cut, end) == composer.RETRIAL_NOT_BURIED
    assert composer.retrial_verdict(
        [("old", "live", "run-a")], spec, cut, end) == composer.RETRIAL_NOT_BURIED
    assert composer.retrial_verdict(
        [("old", "graveyard", "run-a")], spec, no_cut, end) == composer.RETRIAL_WINDOW_UNKNOWN
    assert composer.retrial_verdict(
        [("old", "graveyard", "run-a")], spec, cut,
        lambda cell: "") == composer.RETRIAL_WINDOW_UNKNOWN
    assert composer.retrial_verdict(
        [("old", "graveyard", "run-a")], spec, cut,
        lambda cell: "2024-03-01") == composer.RETRIAL_WINDOW_SHUT
    assert composer.retrial_verdict(
        [("old", "graveyard", "run-a")], spec, cut, end) == composer.RETRIAL_OK


def test_retrial_verdict_takes_the_latest_burial_and_the_oldest_cell():
    """max() over the burials, min() over the cells -- the composer's rule,
    asserted on the shared predicate so the verifier inherits it."""
    spec = {"universe": {"assets": ["A", "B"], "timeframe": "1d"},
            "generator": {"run_id": "new"}}
    priors = [("old", "graveyard", "r1"), ("newer", "graveyard", "r2")]
    cutoffs = {"old": "2019-01-01", "newer": "2026-06-01"}
    assert composer.retrial_verdict(
        priors, spec, cutoffs.get,
        lambda cell: "2026-08-30") == composer.RETRIAL_WINDOW_SHUT
    # One fresh cell, one stale: the stale one governs and shuts the window
    # that the fresh one alone would have opened.
    ends = {("A", "1d"): "2026-08-30", ("B", "1d"): "2024-01-05"}
    assert composer.retrial_verdict(
        [("old", "graveyard", "r1")], spec, lambda sid: "2023-12-31",
        ends.get) == composer.RETRIAL_WINDOW_SHUT
    assert composer.retrial_verdict(
        [("old", "graveyard", "r1")], spec, lambda sid: "2023-12-31",
        lambda cell: "2026-08-30") == composer.RETRIAL_OK


def test_a_non_utf8_artifact_bundle_does_not_crash_the_gate(tmp_path):
    """MEDIUM defect, caught in review: UnicodeDecodeError subclasses
    ValueError, so it escaped burying_cutoff's (OSError, JSONDecodeError,
    AttributeError). One bad byte in one bundle would have been a traceback,
    exit 1, chain_invalid, and every loop cycle aborting -- the exact outage
    this invariant was rewritten to end, on a different trigger. The exposure
    is NEW: before this change the verifier never touched disk.
    """
    reg, spec = seeded(tmp_path)
    sid = spec["strategy_id"]
    bury(reg, sid)
    write_cutoff(tmp_path, sid)
    write_bars(tmp_path, "2026-08-30")
    reg.register_strategy(twin_of(spec))
    # a real invalid UTF-8 byte, mid-JSON, not a truncation
    bundle = tmp_path / "artifacts" / sid / "gauntlet" / "config.json"
    bundle.write_bytes(b'{"cutoff": "2023-12-\xff31"}')

    out = run_verifier(reg.log_path)
    assert out.returncode == 0, out.stdout
    assert "Traceback" not in out.stderr, out.stderr
    assert "window not verifiable" in out.stdout
    assert "REGISTRY VALID" in out.stdout


def test_a_non_utf8_bars_file_does_not_crash_the_gate(tmp_path):
    """The other evidence path, same subclassing trap: cell_data_end caught
    only OSError, and the decode error is raised by the READ, not the open."""
    reg, spec = seeded(tmp_path)
    bury(reg, spec["strategy_id"])
    write_cutoff(tmp_path, spec["strategy_id"])
    write_bars(tmp_path, "2026-08-30")
    reg.register_strategy(twin_of(spec))
    bars = tmp_path / "data" / (CELL + ".csv")
    bars.write_bytes(b"date,open,high,low,close,volume\n"
                     b"2019-01-02,1,1,1,1,1\n2026-08-\xff30,1,1,1,1,1\n")

    out = run_verifier(reg.log_path)
    assert out.returncode == 0, out.stdout
    assert "Traceback" not in out.stderr, out.stderr
    assert "window not verifiable" in out.stdout
    assert "REGISTRY VALID" in out.stdout


def test_every_refusal_constant_carries_a_reason():
    """RETRIAL_REASONS is read on the pre-spend path. A refusal constant with
    no entry would have been a KeyError there; the lookup now falls back to
    the constant's own name, and this keeps the table honest as well."""
    refusals = {composer.RETRIAL_SAME_RUN, composer.RETRIAL_NOT_BURIED,
                composer.RETRIAL_WINDOW_SHUT}
    assert refusals <= set(composer.RETRIAL_REASONS)
    # OK and UNKNOWN are handled before the lookup and must NOT read as
    # refusals if one ever reaches it
    assert composer.RETRIAL_OK not in composer.RETRIAL_REASONS
    assert composer.RETRIAL_WINDOW_UNKNOWN not in composer.RETRIAL_REASONS


# ------------- D15 (exit-rules-v7): version 2 after the chained note --------
#
# docs/2026-09-03-exit-rules-v7-design.md s2: after the chained exit-rules-v7
# note (identified by its first line), every strategy_registered MUST be
# version: 2 and carry no retired type (blocks.RETIRED_TYPES). Before the
# note, version: 1 entries stand -- the ~5,000 legacy registrations are
# history, not defects. The note is not on the live chain yet; these run on
# tmp chains only.

from .blocks import RETIRED_TYPES

V7_NOTE_TEXT = ("exit-rules-v7: test anchor\n\nAfter this entry every "
                "registration is version 2 with no retired type.")


def chain_v7_note(reg):
    reg.append("note", {"text": V7_NOTE_TEXT})


def register_time_stop_type(reg):
    """exit/time_stop is a retired type; it must still be a REGISTERED type
    on the tmp chain, or invariant 6 (unregistered block type) would fire and
    mask the rule under test."""
    reg.register_block_type({"role": "exit", "type": "time_stop",
                             "params_schema": {"max_bars": {"type": "int",
                                                            "grid": [5]}}})


def spec_variant(spec, *, version, extra_blocks=(), window_min=30):
    """A NEW composition (window_min differs from make_strategy's 15, so
    invariant 8 sees no prior) at the requested version."""
    out = json.loads(json.dumps(spec))
    out["version"] = version
    out["blocks"][0]["params"]["window_min"] = window_min
    out["blocks"].extend(list(extra_blocks))
    out["generator"] = dict(out["generator"], run_id=f"run-v{version}")
    out["strategy_id"] = None
    out["strategy_id"] = content_id(out, "strategy_id")
    return out


TIME_STOP = {"role": "exit", "type": "time_stop", "params": {"max_bars": 5}}


def test_retired_types_are_the_two_the_design_names():
    assert set(RETIRED_TYPES) == {("exit", "time_stop"), ("stop", "pct_stop")}


def test_version_1_history_before_the_note_stands(tmp_path):
    """seeded()'s version-1 registration AND a version-1 registration
    carrying a retired type, both BEFORE the note: unchanged history, VALID.
    The verifier must never call the chain corrupt over what it already
    said was fine."""
    reg, spec = seeded(tmp_path)
    register_time_stop_type(reg)
    reg.register_strategy(spec_variant(spec, version=1,
                                       extra_blocks=[TIME_STOP]))
    chain_v7_note(reg)

    out = run_verifier(reg.log_path)
    assert out.returncode == 0, out.stdout
    assert "exit-rules-v7" not in "".join(
        ln for ln in out.stdout.splitlines() if ln.startswith("  line"))


def test_a_version_1_registration_after_the_note_fails(tmp_path):
    reg, spec = seeded(tmp_path)
    chain_v7_note(reg)
    late = spec_variant(spec, version=1)
    reg.register_strategy(late)

    out = run_verifier(reg.log_path)
    assert out.returncode == 1, out.stdout
    assert "version 1 after exit-rules-v7" in out.stdout
    assert late["strategy_id"] in out.stdout


def test_a_registration_with_no_version_after_the_note_fails(tmp_path):
    reg, spec = seeded(tmp_path)
    chain_v7_note(reg)
    late = spec_variant(spec, version=1)
    del late["version"]
    reg.register_strategy(late)

    out = run_verifier(reg.log_path)
    assert out.returncode == 1, out.stdout
    assert "version None after exit-rules-v7" in out.stdout


def test_a_version_2_registration_with_a_retired_type_after_the_note_fails(tmp_path):
    reg, spec = seeded(tmp_path)
    register_time_stop_type(reg)
    chain_v7_note(reg)
    late = spec_variant(spec, version=2, extra_blocks=[TIME_STOP])
    reg.register_strategy(late)

    out = run_verifier(reg.log_path)
    assert out.returncode == 1, out.stdout
    assert "retired block type exit/time_stop" in out.stdout
    assert "version" not in [ln for ln in out.stdout.splitlines()
                             if "retired block type" in ln][0].split("after")[0]


def test_a_clean_version_2_registration_after_the_note_verifies(tmp_path):
    reg, spec = seeded(tmp_path)
    chain_v7_note(reg)
    reg.register_strategy(spec_variant(spec, version=2))

    out = run_verifier(reg.log_path)
    assert out.returncode == 0, out.stdout
    assert "REGISTRY VALID" in out.stdout


def test_an_unrelated_note_does_not_arm_the_rule(tmp_path):
    """Only a note whose text STARTS with 'exit-rules-v7:' is the marker.
    A note that merely mentions it in passing (an incident write-up, say)
    must not retroactively demand version 2."""
    reg, spec = seeded(tmp_path)
    reg.append("note", {"text": "incident: see exit-rules-v7: for context"})
    reg.append("note", {"text": 42})           # malformed text: not the marker
    reg.register_strategy(spec_variant(spec, version=1))

    out = run_verifier(reg.log_path)
    assert out.returncode == 0, out.stdout
