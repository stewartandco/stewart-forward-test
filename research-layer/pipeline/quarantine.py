"""Quarantine forward test: the paper-trading phase, run daily.

Quarantine stops being a state label and becomes a real out-of-sample record.
For every strategy in `quarantine` state this appends one `quarantine_decision`
per asset per trading day, computed by the same pipeline/engine.py executors
the screen and the gauntlet use. Paper-trading forward on bars that did not
exist at selection time cannot be gamed by search, which is why the
multiple-testing correction moves here rather than gating entry to this stage.

Search-gaming is closed by construction, but SELECTIVE RECORDING is not: the
runner is deliberately idempotent and backfillable, so a missed day can be
filled in later. Nothing about that is hidden. `--review` reconstructs the days
a strategy OWED from the price files themselves and reports every bar date with
no decision, plus how long after its bar each row was actually chained
(`ts_utc` vs `payload.date`). A record kept faithfully every day and a record
backfilled to flatter a strategy do not look the same in that report.

Usage:
    python -m pipeline.quarantine --date 2026-08-17
        [--registry registry_log.jsonl] [--data-dir data]
    python -m pipeline.quarantine --review
        [--data-dir data] [--artifacts-dir artifacts]
    python -m pipeline.quarantine --catch-up
        records every OWED date through the --date path, oldest first, at
        most MAX_CATCHUP_DATES per run (a date where every owing strategy was
        deferred for a restated asset uses no slot). The daily ends with it (2026-09-28):
        a deferred class or a missed day is filled on the next run, for EVERY
        owed strategy alike, so backfill is a schedule, never a selection.

Conventions:
  * A decision for date D describes what the book DID on D's bar (entries fill
    at D's open on a signal from D-1's close) and its state at D's close. It is
    a record, not an instruction for D+1.
  * Bars up to and including D only. A decision is never invented for a bar
    that is not on disk. Per-spec since the 2026-08-27 per-class-calendars
    addendum (docs/2026-08-27-quarantine-per-class-calendars-addendum.md):
    a spec whose universe is missing D's bar is DEFERRED for the day, loudly,
    while specs whose bars are all present still record -- a class whose
    source publishes late (FRED-fed FX) must not refuse the classes that are
    on time. A missing price FILE, or a day where EVERY eligible spec
    defers, is still a hard refusal.
  * Dates at or before a strategy's quarantine-entry date are skipped: they
    precede its forward record.
  * `equity` is rebased to 1.0 at the last bar on or before the entry date.
  * Idempotent per (strategy_id, date, asset), so a missed day can be
    backfilled without duplicating.
  * One base `quarantine_data_snapshot` per date -- extended, when a deferred
    class is backfilled after the base was chained, by asset-disjoint
    `quarantine_data_snapshot_supplement` entries -- chained BEFORE the rows,
    recording two SHA-256s per asset: `data_sha256` of the whole price file
    (what screen.py and gauntlet.py already record) and `bars_sha256` of the
    bars up to and including that date. Because each day is recomputed from
    bar 0, the identity of those bars is load-bearing: without it a re-fetch
    would silently change what a reproduction yields for every historical
    day. Re-running a date whose `bars_sha256` no longer matches DEFERS the
    owing strategies that trade the asset (2026-10-08 addendum rule 2), never
    recomputes them -- while a refresh that merely appends later bars is
    correctly a non-event, which is what keeps backfill working.
  * A `--date` run whose write phase finds `logs/chain.lock` held by another
    writer DEFERS politely: exit 0, nothing recorded, no different from a day
    nobody got around to running. The day is owed and covered the same way as
    any other missed day -- an explicit `--date` backfill once the lock is
    free, and `--review` lists it among the dates never recorded.
  * A date is recorded all or nothing for the strategies that record
    (2026-10-16 fast path): every row is simulated and validated before
    chain.lock is taken, and the date's provenance plus rows go to the chain
    in ONE append. A failure before that append writes nothing and the run
    exits 1 -- except a ValueError in ONE strategy's simulation (observe_day's
    data-shaped raises, or a malformed row), which defers only that strategy
    (Coen 2026-10-16): a `simulation_failed` degraded item, WARN then FAIL
    after 3 days, and it stays owed. A strategy that left quarantine while the
    run simulated is skipped. Any other exception still fails the date.

Graduation is NOT automatic. `--review` reports and writes nothing.
"""
from __future__ import annotations

import sys
import json
import time
import hashlib
import argparse
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from . import bars as _bars
from . import cells
from .chainlock import ChainLock, ChainLockHeld
from .registry import (Registry, parse_iso_date, ChainMoved,
                       DuplicateQuarantineDecision,
                       validated_quarantine_decision,
                       validated_quarantine_snapshot)
from .quarantine_view import QuarantineView
from .engine import simulate_asset
from .qcells import (spec_cells, cell_path, split_cell_key, is_intraday_key,
                     complete_days)
from .screen import load_bars
from .series_cache import CACHE_DIRNAME, SeriesCache

MIN_TRADING_DAYS = 60
# a row chained more than this many days after its own bar is a backfill, not
# a forward observation, and is reported as such
BACKFILL_LAG_DAYS = 2
MAX_LISTED_GAPS = 12
# --catch-up re-runs at most this many owed dates per invocation, oldest
# first; the rest wait for the next run. Each date re-simulates every READY
# strategy (observe_day runs before the `seen` skip), but since the
# 2026-10-16 fast path it shares the run's one chain read and holds
# chain.lock only for two tail reads plus one append (well under a second per
# date). Wall time is dominated by simulation: minutes to tens of minutes per
# date (the 2026-10-16 replay measured one 490-row date at 2,308 s), so 10
# dates bound the chain.lock exposure of a catch-up, not its duration.
# Steady state owes one or two dates a day (equity lags a day, FRED fx
# arrives weekly). A date refused every run keeps its slot (oldest first) --
# that needs a human, and the run exits 1 each time.
MAX_CATCHUP_DATES = 10

# Engine perf (docs/2026-10-17-engine-perf-design.md s3.3): the recorder is a
# series-cache WRITER. One SeriesCache per registry directory per process, so
# catch-up's per-date run() calls and a plain --date run share one cache (one
# memory copy of each series, one disk directory). The cache only skips work:
# every series it serves is checked bar by bar against today's closes, and a
# recorded row is bit-identical with it, without it, cold or warm.
_SERIES_CACHES: dict[str, SeriesCache] = {}


def _series_cache(registry_path: Path) -> SeriesCache:
    """The process's cache at <registry dir>/seriescache (gitignored beside
    simcache/; never chained, safe to delete)."""
    directory = (Path(registry_path).parent / CACHE_DIRNAME).resolve()
    key = str(directory)
    cache = _SERIES_CACHES.get(key)
    if cache is None:
        cache = _SERIES_CACHES[key] = SeriesCache(directory)
    return cache


def _reset_series_caches() -> None:
    """Forget every process cache (tests: a fresh process's memory)."""
    _SERIES_CACHES.clear()


CONE_CAVEAT = (
    "  NOTE: that cone is a terminal-equity distribution over the strategy's\n"
    "  FULL trade count, so it is NOT directly comparable to a short forward\n"
    "  record. The graduation comparison belongs to the quarantine -> live\n"
    "  gate, which is pre-declared in its own chained note and cannot bind\n"
    "  before {n} trading days accrue.")


def quarantine_entry_dates(registry: Registry) -> dict[str, str]:
    """{strategy_id: YYYY-MM-DD} taken from the state_change that moved it
    into quarantine. The state machine allows that transition once."""
    out: dict[str, str] = {}
    for e in registry.entries():
        if (e["entry_type"] == "state_change"
                and e["payload"].get("to") == "quarantine"):
            out[e["payload"]["strategy_id"]] = e["ts_utc"][:10]
    return out


def decision_entries(registry: Registry) -> list[dict]:
    """Every chained decision as {strategy_id, date, asset, ts_utc}. `ts_utc`
    is the WRITE time and `date` the BAR date; the gap between them is what
    makes a late backfill visible."""
    return [{"strategy_id": e["payload"]["strategy_id"],
             "date": e["payload"]["date"],
             "asset": e["payload"]["asset"],
             "ts_utc": e["ts_utc"]}
            for e in registry.entries()
            if e["entry_type"] == "quarantine_decision"]


def existing_decisions(registry: Registry) -> set[tuple[str, str, str]]:
    """Kept as the oracle for QuarantineView (test_quarantine_fastpath.py
    pins the view to it) and for hand tools. The recorder reads through
    QuarantineView, not this; its authority is the under-lock re-plan plus
    _append_at's refusal of any byte appended since the view."""
    return {(d["strategy_id"], d["date"], d["asset"])
            for d in decision_entries(registry)}


def data_snapshots(registry: Registry) -> dict[str, dict]:
    """{date: payload} for every snapshot on the chain. Kept as the oracle
    for QuarantineView and for hand tools; the recorder reads through
    QuarantineView, and its authority is the under-lock re-plan plus
    _append_at's refusal of any byte appended since the view.

    FIRST payload wins on a duplicated date, matching the writer (which
    refuses the second) and verify_registry.py (which reports it and keeps the
    first). A last-wins dict comprehension would have made this the one place
    in the system where a duplicate snapshot silently took effect."""
    out: dict[str, dict] = {}
    for e in registry.entries():
        if e["entry_type"] == "quarantine_data_snapshot":
            out.setdefault(e["payload"]["date"], e["payload"])
    return out


def snapshot_supplements(registry: Registry) -> dict[str, list[dict]]:
    """{date: [payloads in chain order]} for every
    quarantine_data_snapshot_supplement on the chain (2026-08-27 addendum).
    Kept as the oracle for QuarantineView and for hand tools, like
    data_snapshots; the recorder's authority is the under-lock re-plan plus
    _append_at's refusal of any byte appended since the view."""
    out: dict[str, list[dict]] = {}
    for e in registry.entries():
        if e["entry_type"] == "quarantine_data_snapshot_supplement":
            out.setdefault(e["payload"]["date"], []).append(e["payload"])
    return out


def _rows_of(source) -> list[dict]:
    """decision_entries from a QuarantineView (the recorder's one read), or
    from anything with entries() (a Registry; test stubs)."""
    if isinstance(source, QuarantineView):
        return source.decision_rows()
    return decision_entries(source)


def _keys_of(source) -> set[tuple[str, str, str]]:
    """existing_decisions, from a QuarantineView or anything with entries()."""
    if isinstance(source, QuarantineView):
        return source.decision_keys()
    return existing_decisions(source)


def merged_bars_coverage(base: dict, sups: list[dict]) -> dict[str, str]:
    """{asset: bars_sha256} covered for a date: the base snapshot first, then
    supplements in chain order, first writer wins on a (defective) overlap --
    the data_snapshots first-wins precedent, and the same merge the verifier
    performs for decision coverage."""
    merged: dict[str, str] = {}
    for p in [base] + sups:
        m = p.get("bars_sha256")
        if isinstance(m, dict):
            for a, h in m.items():
                merged.setdefault(a, h)
    return merged


def hash_price_files(data_dir: Path, cells: list[str]) -> dict[str, str]:
    """SHA-256 of each asset's price CSV, byte for byte, exactly as screen.py
    and gauntlet.py already record it in their artifact bundles. An honest
    record of what the file looked like when the rows were written, checkable
    with a plain `sha256sum`.

    NOT the re-run guard -- see hash_bars_through for why it cannot be.

    Keyed by cell key (2b-2): a bare asset names its `_1d.csv`, exactly as
    before.
    """
    return {c: hashlib.sha256(cell_path(data_dir, c).read_bytes()).hexdigest()
            for c in cells}


def hash_bars_through(data_dir: Path, key: str, date: str) -> str:
    r"""SHA-256 of the bars a date's decisions were actually computed from:
    the CSV header plus every data row dated <= `date`, in file order,
    LF-normalized, one '\n' per line including the last.

    The whole-file hash CANNOT serve as the re-run guard. load_bars truncates
    at the cutoff, so appending tomorrow's bar leaves an earlier date's bars
    byte-identical while changing sha256(file) -- which would refuse every
    backfill, and backfill is the runner's primary recovery path. This hash
    ignores bars after `date` and still catches a restatement of the bars that
    matter.

    Reproducible with shell tools, which is the point of recording it at all
    (substr because the tradfi CSVs stamp dates as 'YYYY-MM-DD 00:00:00';
    for bare-date files it is the whole field and changes nothing):

        { head -n 1 BTCUSD_1d.csv;
          awk -F, 'NR>1 && substr($1,1,10)<="2026-08-17"' BTCUSD_1d.csv; } \
            | tr -d '\r' | sha256sum

    LF normalization is load-bearing because this repo produces CRLF working
    copies; screen.py's bundle_hash sets the same precedent.

    Rows are hashed in FILE order, deliberately, even though load_bars sorts
    them: the shell recipe cannot sort, and a hash an auditor cannot reproduce
    is worth nothing. The cost is fail-closed -- a file whose rows get
    reordered without changing a single value hashes differently and the day
    is refused, which is the safe direction to be wrong in.
    """
    raw = cell_path(data_dir, key).read_bytes().replace(b"\r\n", b"\n")
    lines = raw.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()                       # trailing newline, not a row
    if not lines:
        # unreachable from run(), which refuses a date with no bar long
        # before this, but this is a public helper with a general contract
        raise ValueError(
            f"{key}: price file is empty, so there is nothing to hash as "
            f"the provenance of {date}")
    header, rows = lines[0], lines[1:]
    cutoff = date.encode()[:10]
    # dates are zero-padded ISO, so bytewise <= on the DATE PART is the same
    # ordering load_bars' fence applies. The [:10] matters for the tradfi
    # CSVs: b"2026-08-25 00:00:00" > b"2026-08-25" bytewise, so without it
    # the cutoff-day bar would be excluded from the very hash that is
    # supposed to prove what that day's decisions were computed from.
    kept = [r for r in rows if r.split(b",", 1)[0][:10] <= cutoff]
    body = b"".join(line + b"\n" for line in [header] + kept)
    return hashlib.sha256(body).hexdigest()


def hash_bars_used(data_dir: Path, cells: list[str],
                   date: str) -> dict[str, str]:
    return {c: hash_bars_through(data_dir, c, date) for c in cells}


def snapshot_conflicts(recorded: dict[str, str],
                       current: dict[str, str]) -> list[str]:
    """Why `current` cannot be reconciled with the coverage already chained
    for that date. Empty means the overlapping assets match. Both maps are
    bars_sha256, never data_sha256: a refresh that only appends future bars
    must be a non-event.

    Split out so the comparison is testable without running the whole command
    -- the check_aligned precedent. Coverage of MORE assets than this run
    needs is not a conflict: a strategy may have been buried since. Since the
    2026-08-27 per-class-calendars addendum, an asset in `current` that the
    chain does not cover YET is not a conflict either -- that is the
    supplement path, a class backfilled after the base snapshot was chained.
    Only 'covered but with a DIFFERENT hash' -- a restatement of bars that
    rows were computed from -- cannot be reconciled; since the 2026-10-08
    addendum (rule 2) the caller defers the owing strategies that trade the
    asset instead of refusing the day.
    """
    reasons = []
    for asset in sorted(current):
        if asset in recorded and recorded[asset] != current[asset]:
            reasons.append(
                f"{asset}: the bars up to this date have changed since it was "
                f"recorded (chained {recorded[asset]}, recomputed "
                f"{current[asset]})")
    return reasons


@dataclass
class _Provenance:
    """What the chain's provenance for one date lets a --date pass write
    (2026-10-16 fast path s3.5). Computed from a view, so the recorder asks
    it twice: before chain.lock, to simulate only what can record, and under
    it, to decide. The rules are the old write phase's, verbatim."""
    refusal: str | None = None
    write: tuple[str, dict] | None = None   # (entry_type, payload) or None
    ready: list[str] = field(default_factory=list)
    conflicted: list[str] = field(default_factory=list)
    reasons: dict[str, str] = field(default_factory=dict)
    deferred: list[tuple[str, str]] = field(default_factory=list)


def _plan_provenance(view, date: str, ready: list[str],
                     specs: dict[str, dict], bars_digests: dict[str, str],
                     data_digests: dict[str, str]) -> _Provenance:
    """`bars_digests` / `data_digests` cover every cell the `ready`
    strategies trade. No base and no supplement: write the base. A
    supplement with no base, or a base with no usable bars_sha256: refuse
    (a defective chain needs human eyes). Otherwise rule 2 (2026-10-08
    addendum): covered-but-different defers the strategies that trade the
    asset, and the cells the kept strategies need that nothing covers yet
    get a supplement. Coverage is the base then supplements in chain order,
    first writer wins (merged_bars_coverage). An empty `ready` plans nothing;
    "every owing strategy deferred" is `conflicted` non-empty with `ready`
    empty."""
    if not ready:
        return _Provenance()
    recorded = view.snapshots().get(date)
    sups = view.supplements().get(date, [])
    if recorded is None and not sups:
        return _Provenance(ready=list(ready), write=(
            "quarantine_data_snapshot", validated_quarantine_snapshot(
                {"date": date, "data_sha256": dict(data_digests),
                 "bars_sha256": dict(bars_digests)})))
    if recorded is None:
        return _Provenance(refusal=(
            f"REFUSED: {date} has supplement provenance but no base "
            f"quarantine_data_snapshot -- a defective chain needs human "
            f"eyes, not more rows."))
    if (not isinstance(recorded.get("bars_sha256"), dict)
            or not recorded["bars_sha256"]):
        # fails CLOSED, the `or {}` precedent: a base snapshot with no
        # usable bars_sha256 covers nothing, and papering over it with a
        # supplement for everything would legitimise a fabricated root
        return _Provenance(refusal=(
            f"REFUSED: the quarantine_data_snapshot chained for {date} "
            f"carries no usable bars_sha256 map, so it covers nothing -- "
            f"{', '.join(sorted(bars_digests))} included -- and cannot be "
            f"supplemented honestly."))
    covered = merged_bars_coverage(recorded, sups)
    conflicted = sorted(a for a in bars_digests
                        if a in covered and covered[a] != bars_digests[a])
    reasons = dict(zip(conflicted, snapshot_conflicts(
        covered, {a: bars_digests[a] for a in conflicted})))
    kept: list[str] = []
    deferred: list[tuple[str, str]] = []
    for sid in ready:
        hit = sorted(set(spec_cells(specs[sid])) & set(conflicted))
        if hit:
            deferred.append((sid, reasons[hit[0]]))
        else:
            kept.append(sid)
    keep_cells = {a for sid in kept for a in spec_cells(specs[sid])}
    new_cells = sorted(a for a in bars_digests
                       if a in keep_cells and a not in covered)
    write = None
    if kept and new_cells:
        # the backfill of a class deferred when the base was chained
        write = ("quarantine_data_snapshot_supplement",
                 validated_quarantine_snapshot({
                     "date": date,
                     "data_sha256": {a: data_digests[a] for a in new_cells},
                     "bars_sha256": {a: bars_digests[a] for a in new_cells}}))
    return _Provenance(write=write, ready=kept, conflicted=conflicted,
                       reasons=reasons, deferred=deferred)


def _rebase_index(bars: list[dict], entered: str, asset: str) -> int:
    """Index of the last bar on or before `entered` - the forward record's
    zero point."""
    idx = None
    for i, b in enumerate(bars):
        if b["date"] <= entered:
            idx = i
    if idx is None:
        raise ValueError(
            f"{asset}: no bar on or before the quarantine entry date "
            f"{entered}, so the forward record has no zero point (data starts "
            f"at {bars[0]['date']}). Rebasing on the first available bar would "
            f"silently fold pre-quarantine performance into the record.")
    return idx


def _rebase_index_day(bars: list[dict], entered: str, key: str) -> int:
    """Intraday zero point: the DAILY MARK of the entry date -- the last bar
    whose date part is on or before it (2b-2 design s3.4)."""
    idx = None
    for i, b in enumerate(bars):
        if b["date"][:10] <= entered[:10]:
            idx = i
    if idx is None:
        raise ValueError(
            f"{key}: no bar on or before the quarantine entry date "
            f"{entered}, so the forward record has no zero point (data starts "
            f"at {bars[0]['date']}). Rebasing on the first available bar would "
            f"silently fold pre-quarantine performance into the record.")
    return idx


def day_events(trades: list[dict], position: dict | None, bars: list[dict],
               date: str) -> tuple[int, int, str]:
    """(entries, exits, action) for one UTC day of an intraday book.

    entries = trades entered on the day, plus an open position entered on it;
    exits = trades closed on the day; action = the LATEST event of the day
    (the engine never enters on the bar it exits, so two events never share a
    bar), or "hold"."""
    events: list[tuple[str, str]] = []
    entries = exits = 0
    for t in trades:
        if str(t["entry_date"])[:10] == date:
            entries += 1
            events.append((str(t["entry_date"]),
                           "enter_long" if t["side"] == "long" else "enter_short"))
        if str(t["exit_date"])[:10] == date:
            exits += 1
            events.append((str(t["exit_date"]), "exit"))
    if position is not None:
        stamp = str(bars[position["entry_i"]]["date"])
        if stamp[:10] == date:
            entries += 1
            events.append((stamp, "enter_long" if position["side"] == 1
                           else "enter_short"))
    return entries, exits, (max(events)[1] if events else "hold")


def _observe_intraday(spec: dict, key: str, bars: list[dict], date: str,
                      entered: str, *, series_cache: SeriesCache | None = None
                      ) -> dict:
    universe = spec["universe"]
    session, tf = universe.get("session"), universe["timeframe"]
    if not bars or bars[-1]["date"][:10] != date:
        raise ValueError(f"no {key} bar for {date}")
    book = simulate_asset(spec["blocks"], bars, spec["cost_model"],
                          _bars.bars_per_year(session, tf),
                          version=spec.get("version", 1),
                          bars_per_day=_bars.bars_per_day(session, tf),
                          series_cache=series_cache, cell=key)
    pos = book["position"]
    entries, exits, action = day_events(book["trades"], pos, bars, date)
    base = book["equity"][_rebase_index_day(bars, entered, key)]
    if base <= 0:
        raise ValueError(
            f"{key}: equity baseline at {entered} is {base!r}, so the "
            f"rebased forward equity for {date} is undefined")
    return {"strategy_id": spec["strategy_id"], "date": date, "asset": key,
            "action": action, "price": bars[-1]["close"],
            "position_frac": pos["notional_frac"] if pos is not None else 0.0,
            "equity": book["equity"][-1] / base,
            "entries": entries, "exits": exits}


def observe_day(spec: dict, bars_by_cell: dict[str, list[dict]], date: str,
                entered: str, *, series_cache: SeriesCache | None = None
                ) -> list[dict]:
    """One decision row per CELL in the spec's universe: a RECORD of what the
    book did on `date`, not an instruction. `bars_by_cell` must already be
    truncated to bars <= date. A daily spec's cell key is its bare asset, so
    its rows are unchanged; an intraday cell records its daily mark (2b-2).
    `series_cache` (engine perf s3.3) keys each cell's indicator series by
    its cell key; None = the engine's private memory-only cache, as before."""
    rows = []
    intraday = spec["universe"].get("timeframe", "1d") != "1d"
    for asset in spec_cells(spec):
        bars = bars_by_cell[asset]
        if intraday:
            rows.append(_observe_intraday(spec, asset, bars, date, entered,
                                          series_cache=series_cache))
            continue
        if not bars or bars[-1]["date"] != date:
            raise ValueError(f"no {asset} bar for {date}")
        # Spec s10.11 (T3 review): mirror run_spec's periods_per_year
        # derivation here too. Without it, an fx spec reaching quarantine
        # would forward-run on /365 financing and sqrt(365) vol sizing --
        # silently diverging from the same spec's screen/gauntlet numbers at
        # the funnel's endpoint, the stage nothing downstream re-checks.
        periods_per_year = cells.SESSION_PERIODS.get(
            spec["universe"].get("session"), 365)
        # D15: the spec's registration version selects the engine path; a
        # legacy sid (no key) keeps the byte-identical version-1 path.
        book = simulate_asset(spec["blocks"], bars, spec["cost_model"],
                              periods_per_year, version=spec.get("version", 1),
                              series_cache=series_cache, cell=asset)
        pos = book["position"]
        exited = [t for t in book["trades"] if t["exit_date"] == date]
        if exited:
            # the engine never re-enters on the bar it exits (a signal seen
            # while in a position is ignored, not queued), so "exit" and an
            # entry can never both describe the same bar
            action = "exit"
        elif pos is not None and bars[pos["entry_i"]]["date"] == date:
            action = "enter_long" if pos["side"] == 1 else "enter_short"
        else:
            action = "hold"
        base = book["equity"][_rebase_index(bars, entered, asset)]
        if base <= 0:
            # reachable: a short gapped through its stop can drive
            # mark-to-market equity non-positive. The ratio is undefined, and
            # recording 0.0 would read as "the book was wiped out".
            raise ValueError(
                f"{asset}: equity baseline at {entered} is {base!r}, so the "
                f"rebased forward equity for {date} is undefined")
        rows.append({
            "strategy_id": spec["strategy_id"],
            "date": date,
            "asset": asset,
            "action": action,
            "price": bars[-1]["close"],
            "position_frac": pos["notional_frac"] if pos is not None else 0.0,
            "equity": book["equity"][-1] / base,
        })
    return rows


def _daily_bars(data_dir: Path, asset: str, cutoff: str) -> list[dict]:
    """load_bars with every date normalized to its DATE PART.

    The tradfi snapshot CSVs (FX/eq) stamp dates as 'YYYY-MM-DD 00:00:00'
    while the crypto files carry bare dates, and every comparison in this
    module -- readiness, the rebase zero point, owed dates, decision rows --
    is a plain-date comparison. Without this boundary an FX spec would defer
    FOREVER with its bars on disk, because 'D 00:00:00' never equals 'D'.
    The gauntlet hit the same class of bug on 2026-08-24 and normalizes with
    str()[:10]; this is the recorder's copy of that decision. Daily bars
    only: an intraday cell, where the time part is load-bearing, is loaded
    by `_load_cell_bars` and `_cell_days` with `load_bars(..., timeframe=tf)`
    and never passes through here."""
    bars = load_bars(data_dir, asset, cutoff)
    for b in bars:
        b["date"] = b["date"][:10]
    return bars


def _load_cell_bars(data_dir: Path, key: str, date: str) -> tuple[list[dict], bool]:
    """(bars <= date, is `date` complete for this cell). A daily cell is
    exactly today's path: its bars normalized to the date part, complete when
    the last bar IS the date. An intraday cell loads its whole file once (the
    completeness rule needs to know whether a later day exists, s3.3) and
    truncates in memory."""
    asset, tf = split_cell_key(key)
    if not is_intraday_key(key):
        bars = _daily_bars(data_dir, asset, date)
        return bars, bool(bars) and bars[-1]["date"] == date
    full = load_bars(data_dir, asset, "9999-12-31", timeframe=tf)
    upto = [b for b in full if b["date"][:10] <= date]
    return upto, date in complete_days(full, tf)


def _load_eligible_bars(data_dir: Path, cells: list[str], date: str
                        ) -> tuple[dict[str, list[dict]], dict[str, Path],
                                   dict[str, bool]]:
    """(bars <= date per cell, {cell: path} of MISSING price files,
    {cell: date complete}). A missing file defers the strategies that trade
    it (2026-10-08 addendum rule 3); the caller turns 'every owing strategy
    deferred for a missing bar or file' into the total-stall refusal."""
    bars_by_cell: dict[str, list[dict]] = {}
    missing: dict[str, Path] = {}
    complete: dict[str, bool] = {}
    for key in cells:
        path = cell_path(data_dir, key)
        if not path.exists():
            missing[key] = path
            continue
        bars_by_cell[key], complete[key] = _load_cell_bars(data_dir, key, date)
    return bars_by_cell, missing, complete


def _cell_days(data_dir: Path, key: str, through: str) -> list[str] | None:
    """The days a cell can be recorded on, up to `through`; None when its
    price file is missing. Daily: every bar date (today's rule). Intraday:
    every COMPLETE UTC day (s3.3)."""
    path = cell_path(data_dir, key)
    if not path.exists():
        return None
    asset, tf = split_cell_key(key)
    if not is_intraday_key(key):
        return [b["date"] for b in _daily_bars(data_dir, asset, through)]
    full = load_bars(data_dir, asset, "9999-12-31", timeframe=tf)
    return [d for d in complete_days(full, tf) if d <= through]


def _owed_by_date(data_dir: Path, spec: dict, after: str,
                  through: str) -> dict[str, set[str]] | None:
    """{day: cells that owed a decision on it} over (after, through].
    None if a price file is missing."""
    owed: dict[str, set[str]] = {}
    for key in spec_cells(spec):
        days = _cell_days(data_dir, key, through)
        if days is None:
            return None
        for d in days:
            if d > after:
                owed.setdefault(d, set()).add(key)
    return owed


def _last_bar_date(data_dir: Path, spec: dict) -> str | None:
    """The newest recordable day across the strategy's cells, or None if any
    price file is missing or empty. This is where the completeness window has
    to end: a record that stopped writing must not audit as complete."""
    latest = None
    for key in spec_cells(spec):
        days = _cell_days(data_dir, key, "9999-12-31")
        if not days:
            return None
        if latest is None or days[-1] > latest:
            latest = days[-1]
    return latest


def recordable_owed_dates(owed_by_sid: dict[str, dict[str, set[str]]],
                          universe_by_sid: dict[str, set[str]],
                          recorded: set[tuple[str, str, str]]) -> list[str]:
    """Sorted bar dates on which some quarantined strategy is owed a decision
    the per-date runner CAN record.

    A date counts for a strategy only when EVERY cell in its universe is
    recordable on it (a bar for a daily cell, a COMPLETE UTC day for an
    intraday cell): the per-date runner defers a strategy missing any cell's
    bar, so a calendar-gap date is not recordable, and counting it would
    retry it (and fail) on every run. A date with some cells recorded and
    others not is owed; the runner writes only the missing rows."""
    dates: set[str] = set()
    for sid, owed in owed_by_sid.items():
        universe = universe_by_sid[sid]
        for date, assets in owed.items():
            if assets != universe:
                continue
            if any((sid, date, a) not in recorded for a in universe):
                dates.add(date)
    return sorted(dates)


def catch_up(dates: list[str], run_date) -> tuple[int, bool]:
    """Record each owed date through `run_date(date)`, oldest first.
    `run_date` returns an exit code, or (exit code, deferred_only). At most
    MAX_CATCHUP_DATES dates that can record are attempted per run; a date on
    which every owing strategy was deferred for a restated asset uses no slot
    (2026-10-08 addendum rule 6), so blocked dates never starve newer ones. A
    failed date never strands the dates after it, but any failure makes the
    run exit 1. Returns (rc, reached_all): reached_all is False when the slot
    limit left owed dates unattempted, which is when the caller must not drop
    ledger items it could not re-check."""
    if not dates:
        print("catch-up: nothing owed")
        return 0, True
    print(f"catch-up: {len(dates)} owed date(s); recording up to "
          f"{MAX_CATCHUP_DATES} ({dates[0]} .. {dates[-1]})", flush=True)
    failed, used, attempted = [], 0, 0
    for date in dates:
        if used >= MAX_CATCHUP_DATES:
            break
        attempted += 1
        print(f"catch-up: --date {date}", flush=True)
        try:
            res = run_date(date)
        except Exception:
            # the --date path RAISES on some refusals (a non-identical
            # duplicate, a writer ValueError); one such date must not strand
            # every date after it
            traceback.print_exc(file=sys.stdout)
            res = 1
        rc, deferred_only = (res, False) if isinstance(res, int) else res
        if deferred_only:
            print(f"catch-up: {date} recorded nothing (every owing strategy "
                  f"deferred, restated); no slot used")
        else:
            used += 1
        if rc != 0:
            failed.append(date)
    left = len(dates) - attempted
    if left:
        print(f"catch-up: {left} owed date(s) left for the next run")
    if failed:
        print(f"catch-up: FAILED on {', '.join(failed)}")
        return 1, left == 0
    return 0, left == 0


def _owed_dates_for_catch_up(source: "QuarantineView | Registry",
                             quarantined: list[str],
                             entered: dict[str, str], specs: dict[str, dict],
                             data_dir: Path,
                             report: "DateReport | None" = None) -> list[str]:
    """The live inputs to recordable_owed_dates. A strategy with no spec, no
    entry date or a missing price file is skipped here, named on the console
    and (given `report`) reported to the degraded ledger; --review names it."""
    owed_by_sid: dict[str, dict[str, set[str]]] = {}
    universe_by_sid: dict[str, set[str]] = {}
    for sid in quarantined:
        spec, since = specs.get(sid), entered.get(sid)
        if spec is None or since is None:
            continue
        last_bar = _last_bar_date(data_dir, spec)
        owed = _owed_by_date(data_dir, spec, since, last_bar) if last_bar else None
        if owed is None:
            # since rule 3 (2026-10-08 addendum) --date DEFERS the strategy on
            # a missing file; say so here rather than let the gap look like
            # "nothing owed"
            if report is not None:
                for key in spec_cells(spec):
                    path = cell_path(data_dir, key)
                    if not path.exists():
                        report.degraded.append({"source": "price_file_missing", "key": key,
                                                "reason": f"no price file for {key} at {path}"})
                    elif not _cell_days(data_dir, key, "9999-12-31"):
                        report.degraded.append({"source": "price_file_missing", "key": key,
                                                "reason": f"price file for {key} has no bars"})
            print(f"catch-up: {sid} skipped (price file missing or empty); "
                  f"--review names it")
            continue
        owed_by_sid[sid] = owed
        universe_by_sid[sid] = set(spec_cells(spec))
    return recordable_owed_dates(owed_by_sid, universe_by_sid,
                                 _keys_of(source))


def _truncated(items: list[str]) -> str:
    """A bounded list that says what it dropped: a silently truncated list
    reads as 'that was all of them'."""
    shown = ", ".join(items[:MAX_LISTED_GAPS])
    if len(items) > MAX_LISTED_GAPS:
        shown += f", ... and {len(items) - MAX_LISTED_GAPS} more"
    return shown


def _report_completeness(registry_rows: list[dict], spec: dict | None,
                         entered: str | None, data_dir: Path) -> None:
    """The audit half of --review: what is missing, and how late what is there
    arrived. Both are computed from evidence already on the chain and on disk,
    not from anything the runner asserts about itself."""
    recorded_by_date: dict[str, set[str]] = {}
    for r in registry_rows:
        recorded_by_date.setdefault(r["date"], set()).add(r["asset"])

    if spec is None or entered is None:
        print("  completeness: cannot audit (no spec or entry date on chain)")
    else:
        # The window ends at the last bar that EXISTS, not at the last one
        # RECORDED. Ending it at the last recorded date would make a record
        # that has simply STOPPED audit as complete — a job dead for three
        # weeks would report zero unrecorded dates, and an ongoing outage is
        # exactly the failure most likely to persist unattended. This
        # over-reports by at most one day when --review runs before the day's
        # job; over-reporting by one beats under-reporting the last fifteen.
        last_bar = _last_bar_date(data_dir, spec)
        through = max([last_bar] + list(recorded_by_date)) if last_bar else None
        owed = (_owed_by_date(data_dir, spec, entered, through)
                if through else None)
        if owed is None:
            print("  completeness: cannot audit (price file missing for part "
                  "of the universe)")
        else:
            missing = sorted(d for d in owed if d not in recorded_by_date)
            print(f"  unrecorded bar dates in window: {len(missing)}"
                  + (f"  ({_truncated(missing)})" if missing else ""))
            # a date where some assets recorded and others did not is
            # incompleteness of the same kind, and would sit in the record
            # indefinitely if the re-run never happens
            partial = []
            for date in sorted(owed):
                gap = owed[date] - recorded_by_date.get(date, set())
                if gap and date in recorded_by_date:
                    partial.append(f"{date} missing {', '.join(sorted(gap))}")
            print(f"  partially recorded dates: {len(partial)}"
                  + (f"  ({_truncated(partial)})" if partial else ""))

    if not registry_rows:
        return
    lags = [(parse_iso_date(r["ts_utc"][:10]) - parse_iso_date(r["date"])).days
            for r in registry_rows]
    # A NEGATIVE lag means a row was chained before its own bar date existed —
    # the closest thing this system can produce to a fabricated forward
    # observation, since it means the price file carried a bar for a day that
    # had not happened. It must never fall into the reassuring branch below.
    early = [n for n in lags if n < 0]
    if early:
        print(f"  WARNING: {len(early)} row(s) chained BEFORE their own bar "
              f"date (min {min(lags)}d) — a decision was recorded against a "
              f"bar that did not exist at write time")
    late = [n for n in lags if n > BACKFILL_LAG_DAYS]
    if late:
        print(f"  backfill lag: max {max(lags)}d, {len(late)} row(s) chained "
              f"more than {BACKFILL_LAG_DAYS}d after their bar")
    elif not early:
        print(f"  backfill lag: max {max(lags)}d, every row chained within "
              f"{BACKFILL_LAG_DAYS}d of its bar")


def review(source: "QuarantineView | Registry", quarantined: list[str],
           entered: dict[str, str], specs: dict[str, dict],
           artifacts_dir: Path, data_dir: Path) -> int:
    """Report progress against the pre-declared minimum. WRITES NOTHING."""
    if not quarantined:
        print("No strategies in 'quarantine' state.")
        return 0

    rows_by_sid: dict[str, list[dict]] = {}
    for r in _rows_of(source):
        rows_by_sid.setdefault(r["strategy_id"], []).append(r)

    for sid in quarantined:
        rows = rows_by_sid.get(sid, [])
        n = len({r["date"] for r in rows})       # a date counts once, not once per asset
        verdict = ("ELIGIBLE FOR REVIEW" if n >= MIN_TRADING_DAYS
                   else "NOT YET ELIGIBLE")
        print(f"{sid}  entered {entered.get(sid, '?')}  "
              f"days {n}/{MIN_TRADING_DAYS}  {verdict}")
        _report_completeness(rows, specs.get(sid), entered.get(sid), data_dir)

        mc_path = artifacts_dir / sid / "gauntlet" / "mc_summary.json"
        if not mc_path.exists():
            print("  gauntlet MC projection: (no mc_summary.json found)")
            continue
        mc = json.loads(mc_path.read_text(encoding="utf-8"))
        cone = " ".join(f"{k}={mc[k]:.4f}" for k in ("p25", "p50", "p75")
                        if k in mc)
        if not cone:
            print("  gauntlet MC projection: (no cone stored)")
            continue
        print(f"  gauntlet MC projection: {cone}")
        print(CONE_CAVEAT.format(n=MIN_TRADING_DAYS))

    print("\nReview only — nothing written. Graduation is a separately "
          "human-gated decision.")
    return 0


class DateReport:
    """What one --date pass found, for the catch-up's slot rule and the
    degraded ledger (isolation design s6.1)."""

    def __init__(self) -> None:
        self.deferred_only = False      # owing strategies existed; every one was deferred (rule 2)
        self.degraded: list[dict] = []  # {"source", "key", "reason"}
        # the run returned before checking provenance (a deferred_lock skip,
        # or a raise), so its silence is not recovery: the catch-up must not
        # drop ledger items on the strength of it
        self.unchecked = False


def _record_date(args, registry: Registry, view: QuarantineView,
                 quarantined: list[str], entered: dict[str, str],
                 specs: dict[str, dict], report: DateReport) -> int:
    """The --date path: record one trading day for every eligible strategy."""
    if not quarantined:
        print("No strategies in 'quarantine' state.")
        return 0

    # Resolve who is actually owed a decision BEFORE touching the data, so a
    # gap in an asset nobody is trading today cannot refuse everyone's day.
    eligible = []
    for sid in quarantined:
        since = entered.get(sid)
        if sid not in specs:
            print(f"{sid}  skipped: no strategy_registered entry on the chain")
        elif since is None:
            print(f"{sid}  skipped: no quarantine entry on the chain")
        elif args.date <= since:
            print(f"{sid}  skipped: {args.date} is not after its "
                  f"quarantine entry {since}")
        else:
            eligible.append(sid)

    # Rule 1 (2026-10-08 addendum): only strategies that still OWE a row are
    # simulated and guarded. A fully recorded strategy's rows are already on
    # the chain; re-simulating it only fed its assets into the provenance
    # check, so one restated asset refused every OTHER strategy's day.
    seen_keys = view.decision_keys()
    owing: list[str] = []
    n_present = 0
    for sid in eligible:
        keys = [(sid, args.date, a) for a in spec_cells(specs[sid])]
        if all(k in seen_keys for k in keys):
            n_present += len(keys)
        else:
            owing.append(sid)
    eligible = owing

    # Per-class calendars (2026-08-27 addendum): a spec records only when
    # EVERY asset in its universe has a bar for the date. A missing bar with
    # the file present is publication lag (FRED-fed FX runs ~a week behind
    # the crypto calendar): that spec DEFERS, loudly, and an explicit --date
    # backfill records it once the bar publishes. A missing FILE stays a hard
    # deferral (rule 3), judged per strategy.
    bars_by_asset: dict[str, list[dict]] = {}
    ready: list[str] = []
    if eligible:
        bars_by_asset, missing_files, complete = _load_eligible_bars(
            args.data_dir,
            sorted({a for sid in eligible for a in spec_cells(specs[sid])}),
            args.date)
        for asset, path in sorted(missing_files.items()):
            report.degraded.append({"source": "price_file_missing", "key": asset,
                                    "reason": f"no price file for {asset} at {path}"})
        for sid in eligible:
            assets = spec_cells(specs[sid])
            gone = [a for a in assets if a in missing_files]
            if gone:
                print(f"{sid}  deferred: no price file for {gone[0]} at "
                      f"{missing_files[gone[0]]}")
                continue
            missing = [a for a in assets if not complete[a]]
            if missing:
                a = missing[0]
                ends = (f"data ends {bars_by_asset[a][-1]['date']}"
                        if bars_by_asset[a]
                        else "no bars on or before the date")
                print(f"{sid}  deferred: no {a} bar for {args.date} yet "
                      f"({ends})")
            else:
                ready.append(sid)
        if not ready and n_present == 0:
            # While a class that trades every calendar day is in the pool, a
            # day where NOTHING can record means the data pipeline is dead,
            # and going quiet would hide exactly the outage most likely to
            # persist unattended. Revisit if the pool ever goes tradfi-only.
            # It covers a missing bar OR a missing price file (2026-10-08
            # addendum rule 5).
            # A total stall also needs nothing already chained for the date
            # (n_present == 0): a date whose other strategies are already
            # chained is not a dead pipeline, only a lagging class (rule 1
            # follow-up); that case falls through to the normal summary, rc 0.
            print(f"REFUSED: nothing recorded for {args.date} -- every "
                  f"eligible strategy was deferred for a missing bar or "
                  f"price file. Either "
                  f"the data refresh is broken, or every class is late at "
                  f"once; backfill with an explicit --date once bars exist.",
                  file=sys.stderr)
            return 1

    # Fast path (2026-10-16 design s3.5): everything that does not need the
    # lock happens before it -- the date's digests, a provenance plan from
    # the run's one read (so a strategy deferred for a restated asset is
    # never simulated), and EVERY row's simulation and validation. A raise
    # here writes nothing and the date stays owed (s3.6); the one exception is
    # a ValueError in a single strategy's simulation, which defers THAT
    # strategy only (Coen 2026-10-16, below). The lock is then held for a tail
    # read plus ONE append.
    rows_by_sid: dict[str, list[dict]] = {}
    sim_failed: dict[str, str] = {}
    lock = None
    if ready:
        logs_dir = args.registry.parent / "logs"
        lock = ChainLock(logs_dir, holder="quarantine",
                         purpose=f"daily {args.date}")
        # Probe BEFORE simulating: a held lock no longer costs a whole date's
        # simulation, and the polite deferral keeps its old precedence over a
        # simulation raise. The acquire below stays as the backstop (the lock
        # can appear between this probe and it).
        if lock.info() is not None and lock.holder_alive():
            return _defer_for_lock(lock, args.date, report)
        assets = sorted({a for sid in ready for a in spec_cells(specs[sid])})
        bars_digests = hash_bars_used(args.data_dir, assets, args.date)
        data_digests = hash_price_files(args.data_dir, assets)
        pre = _plan_provenance(view, args.date, ready, specs, bars_digests,
                               data_digests)
        # engine perf s3.3: the run's one series cache (shared by catch-up's
        # dates); None only when a test disables it
        series_cache = _series_cache(args.registry)
        # a refusal simulates nothing; the plan under the lock refuses again
        # (ruling R7)
        for sid in ([] if pre.refusal else pre.ready):
            # Coen 2026-10-16: a data-shaped failure in ONE strategy's
            # simulation (observe_day's non-positive equity baseline, missing
            # bar or no zero point; validated_quarantine_decision's malformed
            # row -- all ValueError) must not hold back the others' rows, as
            # one strategy's missing bar or restated asset already does not
            # (isolation addendum rules 2-3). The strategy gets no rows, is
            # reported (stdout + a `simulation_failed` degraded item: WARN,
            # FAIL after 3 days) and stays owed, so catch-up retries it every
            # run. ValueError ONLY: any other exception is a code bug and
            # still fails the whole date, loudly, with nothing written.
            try:
                rows_by_sid[sid] = [
                    validated_quarantine_decision(row) for row in
                    observe_day(specs[sid], bars_by_asset, args.date,
                                entered[sid], series_cache=series_cache)]
            except ValueError as exc:
                sim_failed[sid] = str(exc)
                print(f"{sid}  deferred: simulation failed: {exc}")
                report.degraded.append({"source": "simulation_failed",
                                        "key": sid,
                                        "reason": f"{args.date}: {exc}"})
        if (sim_failed and n_present == 0
                and all(sid in sim_failed for sid in ready)):
            # The total-stall rule (above) for the simulation: it runs after
            # that check, so a day where NOTHING can record because every
            # ready strategy's simulation failed is refused here, before the
            # lock is taken (nothing to append). Same reading as the
            # missing-bar refusal: a date whose other strategies are already
            # chained (n_present > 0) is a lagging strategy, not a dead
            # pipeline, and falls through to the summary, exit 0.
            print(f"REFUSED: nothing recorded for {args.date} -- every "
                  f"ready strategy's simulation failed "
                  f"({'; '.join(f'{sid}: {sim_failed[sid]}' for sid in ready)})"
                  f". The data behind them is malformed; the strategies stay "
                  f"owed and the next run retries them.", file=sys.stderr)
            return 1

    if ready:
        try:
            lock.acquire()
        except ChainLockHeld:
            return _defer_for_lock(lock, args.date, report)

    try:
        written: list[dict] = []
        n_skipped = n_present
        if ready:
            pre_keys = seen_keys
            _under_lock(report, args.date, lambda: view.advance(registry))
            # The same decision against what is on the chain NOW: another
            # writer may have chained this date's provenance or rows while
            # we simulated.
            # The plan vouches for the strategies that will RECORD: a
            # strategy whose simulation failed queues no row, so its cells
            # stay out of the date's snapshot (else a later bar correction
            # would pin its retry as `restated` for ever; on retry its
            # cells go in as a supplement). The same holds for a strategy
            # that LEFT quarantine while the run simulated: its rows are
            # dropped, so the snapshot must not vouch for its cells either.
            # Coen 2026-10-16, fix rounds 1-2.
            states = view.states()
            left: set[str] = set()
            for sid in ready:
                if sid not in sim_failed and states.get(sid) != "quarantine":
                    left.add(sid)
                    print(f"{sid}  skipped: left quarantine "
                          f"({states.get(sid)}) while this run simulated")
            survivors = [sid for sid in ready
                         if sid not in sim_failed and sid not in left]
            cells = {a for sid in survivors for a in spec_cells(specs[sid])}
            plan = _plan_provenance(
                view, args.date, survivors, specs,
                {a: d for a, d in bars_digests.items() if a in cells},
                {a: d for a, d in data_digests.items() if a in cells})
            if plan.refusal:
                print(plan.refusal, file=sys.stderr)
                return 1
            if plan.conflicted:
                # Rule 2 (2026-10-08 addendum): covered-but-different defers
                # the owing strategies that trade the asset; the rest record.
                # No row is ever chained over a restated bar.
                for a in plan.conflicted:
                    report.degraded.append({"source": "restated", "key": a,
                                            "reason": plan.reasons[a]})
                print(f"RESTATED: the provenance chained for {args.date} "
                      f"does not match the bars this run would use for "
                      f"{', '.join(plan.conflicted)}; the strategies trading "
                      f"them are deferred, the rest record.", file=sys.stderr)
                for sid, reason in plan.deferred:
                    print(f"{sid}  deferred: {reason}")
                if not plan.ready:
                    report.deferred_only = True
                    print(f"\n0 decision(s) chained, {n_present} already "
                          f"present; every owing strategy deferred (restated).")
                    return 0
            # plan.ready is a subset of `survivors`, so a strategy whose
            # simulation failed (or that left quarantine) is never `late`
            late = [sid for sid in plan.ready if sid not in rows_by_sid]
            if late:
                report.unchecked = True
                print(f"REFUSED: the provenance for {args.date} changed under "
                      f"chain.lock so that {', '.join(late)} became recordable "
                      f"without being simulated; nothing written -- re-run "
                      f"the date.", file=sys.stderr)
                return 1
            chained = view.decision_keys()
            batch: set[tuple[str, str, str]] = set()
            items = [plan.write] if plan.write else []
            for sid in plan.ready:
                for row in rows_by_sid[sid]:
                    key = (row["strategy_id"], row["date"], row["asset"])
                    if key in chained:
                        # Ruling R3: a row on the chain at our one read is
                        # skipped as today's pre-filter skipped it. One that
                        # landed while we simulated is absorbed only when
                        # IDENTICAL -- same key with different numbers means
                        # the data or the spec moved underneath us.
                        landed = view.decision_payload(key)
                        if key not in pre_keys and landed != row:
                            dup = DuplicateQuarantineDecision(
                                "quarantine decision already chained for "
                                f"{key[0]} {key[1]} {key[2]}")
                            dup.chained = landed
                            raise dup
                        n_skipped += 1
                        continue
                    if key in batch:
                        # a universe that repeats an asset yields the same
                        # row twice in one date; the chain takes it once (the
                        # old per-row `seen` guard), or the verifier calls
                        # the duplicate decision INVALID
                        n_skipped += 1
                        continue
                    if states.get(sid) != "quarantine":
                        # Coen 2026-10-16: a live-gate move while the run
                        # simulated is routine, not a failure. Its rows are
                        # dropped (none queued, no degraded item) and the
                        # others record; before, the raise withheld the
                        # whole date.
                        if sid not in left:
                            left.add(sid)
                            print(f"{sid}  skipped: left quarantine "
                                  f"({states.get(sid)}) while this run "
                                  f"simulated")
                        continue
                    items.append(("quarantine_decision", row))
                    batch.add(key)
                    written.append(row)
            if not any(kind == "quarantine_decision" for kind, _ in items):
                # Provenance vouches for rows: with none queued (every owing
                # strategy left quarantine, failed simulation, or landed
                # while we simulated) not even the snapshot is appended
                # (Coen 2026-10-16, fix round 1).
                items = []
            if items:
                # provenance first (invariant 9 asks for an EARLIER
                # snapshot), then the rows in today's order. The view is not
                # moved onto _append_at's returned snapshot (that would skip
                # on_entry for our own rows); it is advanced over our own
                # tail instead.
                _under_lock(report, args.date,
                            lambda: registry._append_at(view.snap, items))
            for row in written:
                print(f"{row['strategy_id']}  {row['asset']}  "
                      f"{row['action']:<11} "
                      f"px={row['price']:.2f}  "
                      f"pos={row['position_frac']:.3f} "
                      f" eq={row['equity']:.4f}")

        print(f"\n{len(written)} decision(s) chained, {n_skipped} already "
              f"present.")
        if ready and items:
            # Move the view over what we just wrote (still under the lock,
            # O(own tail)), so the next catch-up date's advance starts from
            # our last line: a rewritten head between dates is then a
            # ChainMoved, not an unread tail (amends plan ruling R6). Not
            # through _under_lock: the date's entries ARE on the chain by now,
            # so "nothing written" would be false. The date still fails loudly
            # (the alarm); the next catch-up does not owe it, its rows exist.
            try:
                view.advance(registry)
            except ChainMoved as exc:
                report.unchecked = True
                landed = (f"the {len(written)} decision row(s) above"
                          + (" plus the date's provenance "
                             "snapshot/supplement" if plan.write else ""))
                print(f"CHAIN MOVED after the write for {args.date}: "
                      f"{landed} WERE chained; the chain then moved under "
                      f"chain.lock ({exc}). A writer appended without "
                      f"chain.lock, or the log was rewritten -- see "
                      f"research-layer/CLAUDE.md, Chain lock.",
                      file=sys.stderr)
                raise
        return 0
    finally:
        if lock is not None:
            lock.release()


def _defer_for_lock(lock: ChainLock, date: str, report: DateReport) -> int:
    """chain.lock is held by another writer: defer the date politely (exit 0,
    nothing recorded). Shared by the pre-simulation probe and the acquire."""
    if lock.is_stale():
        print("WARN: chain.lock is stale (>3h); if no writer is "
              "alive, remove logs/chain.lock or let the loop's "
              "two-strike rule break it", file=sys.stderr)
    print(f"deferred_lock: chain.lock held, skipping {date}; "
          f"re-run with --date {date} to backfill")
    report.unchecked = True
    return 0


def _under_lock(report: DateReport, date: str, step):
    """Run one chain step of the write phase; a ChainMoved means the log is
    not where our read left it (rewritten, truncated, or appended WITHOUT
    chain.lock): nothing is written for the date, say so, and re-raise."""
    try:
        return step()
    except ChainMoved as exc:
        report.unchecked = True
        print(f"CHAIN MOVED: {exc}; nothing written for {date}. A writer "
              f"appended without chain.lock, or the log was rewritten -- "
              f"see research-layer/CLAUDE.md, Chain lock.", file=sys.stderr)
        raise


def run_one_date_for_catch_up(base: list[str], date: str, report: DateReport,
                              view: QuarantineView | None = None,
                              registry: Registry | None = None
                              ) -> tuple[int, bool]:
    one = DateReport()
    try:
        rc = run(base + ["--date", date], report=one, write_ledger=False,
                 view=view)
    except ChainMoved:
        # Spec s3.4: the date fails like any raise, and the shared view is
        # rebuilt with a fresh read so the dates after it are not stranded
        # on a snapshot the chain no longer matches.
        one.unchecked = True
        if view is not None:
            view.rebuild(registry)
        raise
    except BaseException:
        one.unchecked = True            # a raise: this date was not fully checked
        raise
    finally:
        # partial items survive a raise; the flag rides along either way
        report.degraded.extend(one.degraded)
        report.unchecked |= one.unchecked
    return rc, one.deferred_only


def _write_quarantine_ledger(registry_path: Path, report: DateReport, *,
                             remove_unseen: bool,
                             keep_sources: tuple[str, ...] = ()) -> None:
    from . import degraded
    degraded.record(Path(registry_path).parent / "logs" / degraded.QUARANTINE_LEDGER,
                    "quarantine", report.degraded, remove_unseen=remove_unseen,
                    keep_sources=keep_sources)


FETCH_SOURCE = "intraday_fetch_failed"
FETCH_PACING_S = 0.2                    # crypto_data.main's pause between cells


def fetch_owed(quarantined: list[str], specs: dict[str, dict], data_dir: Path,
               fetch=None) -> list[dict]:
    """Fetch every intraday cell a quarantined strategy trades, once each
    (2b-2 design s3.5): incremental, closed bars only, atomic write, into
    `data_dir`. Nothing is owed while no intraday strategy is quarantined.
    A cell whose fetch raises is a degraded item; the step never fails for
    it -- its days stay owed and catch-up records them after a later
    successful fetch.

    Binance safety follows crypto_data.main: cells are paced FETCH_PACING_S
    apart, and a ban (418) or a rate limit that reaches this loop (retries
    exhausted, or a Retry-After beyond the cap) stops the fetch at once and
    owes every cell left. No long back-off sleep inside the 08:20 task: the
    next day's run retries."""
    cells_owed = sorted({k for sid in quarantined if sid in specs
                         for k in spec_cells(specs[sid]) if is_intraday_key(k)})
    if not cells_owed:
        print("fetch-owed: no intraday cell owed")
        return []
    # Imported only once a cell is owed: a run with nothing to fetch must not
    # depend on crypto_data importing.
    from .crypto_data import BinanceBanned, RateLimited
    if fetch is None:
        from .crypto_data import fetch_symbol as fetch
    items = []
    for n, key in enumerate(cells_owed):
        if n:
            time.sleep(FETCH_PACING_S)    # the IP budget is shared with every
                                          # Binance consumer on this box
        asset, tf = split_cell_key(key)
        try:
            fetch(asset, tf, Path(data_dir))
            print(f"fetch-owed: {key} fetched")
        except BinanceBanned as exc:
            # An IP-wide ban shared by every Binance consumer on this box:
            # stop at once (more calls can extend it) and owe every cell left.
            print(f"fetch-owed: {key} FAILED: {exc!r}; Binance IP ban, "
                  f"stopping", file=sys.stderr)
            items.append({"source": FETCH_SOURCE, "key": key,
                          "reason": f"fetch of {asset} {tf} failed: {exc!r}"})
            for rest in cells_owed[n + 1:]:
                items.append({"source": FETCH_SOURCE, "key": rest,
                              "reason": "skipped: Binance IP ban (418) on "
                                        f"{key}; not fetched this run"})
            break
        except RateLimited as exc:
            # crypto_data.main aborts its whole run here: the next call would
            # keep hammering the shared IP and invite a 418. Record and stop;
            # no back-off sleep in the 08:20 task (the next run retries).
            print(f"fetch-owed: {key} FAILED: {exc!r}; Binance rate limit "
                  f"(retry_after {exc.retry_after}s), stopping", file=sys.stderr)
            items.append({"source": FETCH_SOURCE, "key": key,
                          "reason": f"fetch of {asset} {tf} failed: Binance "
                                    f"rate limit: {exc!r}"})
            for rest in cells_owed[n + 1:]:
                items.append({"source": FETCH_SOURCE, "key": rest,
                              "reason": "skipped: Binance rate limit (429) on "
                                        f"{key}; not fetched this run"})
            break
        except Exception as exc:          # one dead cell must not stop the rest
            print(f"fetch-owed: {key} FAILED: {exc!r}")
            items.append({"source": FETCH_SOURCE, "key": key,
                          "reason": f"fetch of {asset} {tf} failed: {exc!r}"})
    return items


def _write_fetch_ledger(registry_path: Path, items: list[dict]) -> None:
    """The fetch step is the only authority on FETCH_SOURCE items: it drops
    a cell's item once that cell fetches, and keeps every other source."""
    from . import degraded
    path = Path(registry_path).parent / "logs" / degraded.QUARANTINE_LEDGER
    previous, _ = degraded.read_items(path)
    keep = tuple(sorted({i["source"] for i in previous} - {FETCH_SOURCE}))
    degraded.record(path, "quarantine", items, remove_unseen=True,
                    keep_sources=keep)


def _split_unsupported(quarantined: list[str], specs: dict
                       ) -> tuple[list[str], list[dict]]:
    """2b-2 (design s3.6): every 24x7 timeframe in bars.BARS_PER_DAY_24X7 is
    recorded. An intraday spec the engine cannot simulate (a non-24x7
    session, an unknown timeframe) is skipped and reported as degraded
    (`intraday_unsupported`) rather than crash the date; registration-time
    refusal of such a spec belongs to 2b-3."""
    keep, items = [], []
    for sid in quarantined:
        universe = specs.get(sid, {}).get("universe", {})
        tf = universe.get("timeframe", "1d")
        session = universe.get("session")
        if tf != "1d":
            try:
                _bars.bars_per_day(session, tf)
            except _bars.IntradayUnsupported as exc:
                print(f"{sid}  skipped: intraday timeframe {tf!r} in session "
                      f"{session!r} is not supported ({exc})")
                items.append({"source": "intraday_unsupported", "key": sid,
                              "reason": f"{tf} strategy in session {session!r}: {exc}"})
                continue
        keep.append(sid)
    return keep, items


def run(argv: list[str] | None = None, *, report: "DateReport | None" = None,
        write_ledger: bool = True,
        view: QuarantineView | None = None) -> int:
    layer = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--registry", type=Path, default=layer / "registry_log.jsonl")
    ap.add_argument("--data-dir", type=Path, default=layer / "data")
    ap.add_argument("--artifacts-dir", type=Path, default=layer / "artifacts")
    ap.add_argument("--date", help="YYYY-MM-DD; the trading day to record")
    ap.add_argument("--review", action="store_true",
                    help="report progress against the minimum; writes nothing")
    ap.add_argument("--catch-up", action="store_true",
                    help="record every owed date (deferred or missed) through "
                         "the --date path, oldest first")
    ap.add_argument("--fetch-owed", action="store_true",
                    help="fetch the intraday bars quarantined strategies owe (2b-2)")
    args = ap.parse_args(argv)

    if sum([args.review, bool(args.date), args.catch_up, args.fetch_owed]) != 1:
        print("Give exactly one of --date YYYY-MM-DD, --review, --catch-up or "
              "--fetch-owed.", file=sys.stderr)
        return 1
    if args.date is not None:
        try:
            parse_iso_date(args.date, "--date")
        except ValueError as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 1

    # A missing path must never read as "nothing to do": Registry.entries()
    # returns silently on a missing file, so a wrong path, a moved cwd or a
    # scheduler quirk would otherwise report success and leave an invisible
    # hole in the forward record.
    if not args.registry.exists():
        print(f"REFUSED: no registry at {args.registry}. The forward record "
              f"cannot be appended to a chain that is not there.",
              file=sys.stderr)
        return 1
    if not args.data_dir.is_dir():
        print(f"REFUSED: no data directory at {args.data_dir}. Decisions are "
              f"computed from bars, and their completeness is audited against "
              f"the price files.", file=sys.stderr)
        return 1

    registry = Registry(args.registry)
    # ONE chain read per run (2026-10-16 fast path s3.1). Catch-up hands the
    # same view to every date's pass, which moves it over the tail only; a
    # ChainMoved here fails that date (run_one_date_for_catch_up rebuilds).
    if view is None:
        view = QuarantineView.build(registry)
    else:
        view.advance(registry)
    states = view.states()
    entered = view.entry_dates()
    quarantined = sorted(sid for sid, st in states.items()
                         if st == "quarantine")
    specs = view.specs()

    quarantined, intraday_items = _split_unsupported(quarantined, specs)

    if args.fetch_owed:
        items = fetch_owed(quarantined, specs, args.data_dir)
        try:
            _write_fetch_ledger(args.registry, items)
        except OSError as exc:
            print(f"degraded_ledger_error: {exc}", file=sys.stderr)
        return 0

    if args.review:
        return review(view, quarantined, entered, specs,
                      args.artifacts_dir, args.data_dir)

    if args.catch_up:
        report = DateReport()
        report.degraded.extend(intraday_items)
        dates = _owed_dates_for_catch_up(view, quarantined, entered,
                                         specs, args.data_dir, report=report)
        base = ["--registry", str(args.registry), "--data-dir",
                str(args.data_dir), "--artifacts-dir", str(args.artifacts_dir)]
        rc, reached_all = catch_up(
            dates, lambda d: run_one_date_for_catch_up(
                base, d, report, view=view, registry=registry))
        if write_ledger:
            # The authoritative pass: it removes recovered items, but only
            # when it reached every owed date (design s3.1, Review Focus 1)
            # AND no attempted date failed or was skipped on chain.lock: such
            # a date may not have reached its provenance check, so an item it
            # did not report is unseen, not recovered.
            # Same guard as --date: the ledger is observability, a failed
            # write is reported and never the run's exit code.
            recovered_is_provable = (reached_all and rc == 0
                                     and not report.unchecked)
            try:
                _write_quarantine_ledger(args.registry, report,
                                         remove_unseen=recovered_is_provable,
                                         keep_sources=(FETCH_SOURCE,))
            except OSError as exc:
                print(f"degraded_ledger_error: {exc}", file=sys.stderr)
        return rc

    report = report if report is not None else DateReport()
    report.degraded.extend(intraday_items)
    try:
        return _record_date(args, registry, view, quarantined, entered, specs,
                            report)
    finally:
        if write_ledger:
            # --date only ADDS or refreshes: it sees one date, so an item it
            # does not see may still be live on another (design s3.1). The
            # ledger is observability: a failed write is reported, never the
            # run's exit code (a non-zero exit makes the loop retry a metered
            # cycle).
            try:
                _write_quarantine_ledger(args.registry, report,
                                         remove_unseen=False)
            except OSError as exc:
                print(f"degraded_ledger_error: {exc}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(run())
