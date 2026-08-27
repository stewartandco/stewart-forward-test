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
    day. Re-running a date whose `bars_sha256` no longer matches is REFUSED,
    not recomputed -- while a refresh that merely appends later bars is
    correctly a non-event, which is what keeps backfill working.

Graduation is NOT automatic. `--review` reports and writes nothing.
"""
from __future__ import annotations

import sys
import json
import hashlib
import argparse
from pathlib import Path

from . import cells
from .chainlock import ChainLock, ChainLockHeld
from .registry import (Registry, parse_iso_date, DuplicateQuarantineDecision,
                       DuplicateQuarantineSnapshot,
                       OverlappingQuarantineSupplement)
from .engine import simulate_asset
from .screen import load_bars

MIN_TRADING_DAYS = 60
# a row chained more than this many days after its own bar is a backfill, not
# a forward observation, and is reported as such
BACKFILL_LAG_DAYS = 2
MAX_LISTED_GAPS = 12

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
    """Cheap in-process pre-filter only. The authoritative de-duplication is
    Registry.record_quarantine_decision, which re-checks under the lock."""
    return {(d["strategy_id"], d["date"], d["asset"])
            for d in decision_entries(registry)}


def data_snapshots(registry: Registry) -> dict[str, dict]:
    """{date: payload} for every snapshot on the chain. A cheap pre-read only;
    the authoritative uniqueness check is Registry.record_quarantine_snapshot,
    which re-checks under the lock.

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
    A cheap pre-read only, like data_snapshots; the authoritative disjointness
    check is Registry.record_quarantine_snapshot_supplement under the lock."""
    out: dict[str, list[dict]] = {}
    for e in registry.entries():
        if e["entry_type"] == "quarantine_data_snapshot_supplement":
            out.setdefault(e["payload"]["date"], []).append(e["payload"])
    return out


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


def hash_price_files(data_dir: Path, assets: list[str]) -> dict[str, str]:
    """SHA-256 of each asset's price CSV, byte for byte, exactly as screen.py
    and gauntlet.py already record it in their artifact bundles. An honest
    record of what the file looked like when the rows were written, checkable
    with a plain `sha256sum`.

    NOT the re-run guard -- see hash_bars_through for why it cannot be.
    """
    return {a: hashlib.sha256(
        (data_dir / f"{a}_1d.csv").read_bytes()).hexdigest() for a in assets}


def hash_bars_through(data_dir: Path, asset: str, date: str) -> str:
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
    raw = (data_dir / f"{asset}_1d.csv").read_bytes().replace(b"\r\n", b"\n")
    lines = raw.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()                       # trailing newline, not a row
    if not lines:
        # unreachable from run(), which refuses a date with no bar long
        # before this, but this is a public helper with a general contract
        raise ValueError(
            f"{asset}: price file is empty, so there is nothing to hash as "
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


def hash_bars_used(data_dir: Path, assets: list[str],
                   date: str) -> dict[str, str]:
    return {a: hash_bars_through(data_dir, a, date) for a in assets}


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
    rows were computed from -- is irreconcilable.
    """
    reasons = []
    for asset in sorted(current):
        if asset in recorded and recorded[asset] != current[asset]:
            reasons.append(
                f"{asset}: the bars up to this date have changed since it was "
                f"recorded (chained {recorded[asset]}, recomputed "
                f"{current[asset]})")
    return reasons


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


def observe_day(spec: dict, bars_by_asset: dict[str, list[dict]], date: str,
                entered: str) -> list[dict]:
    """One decision row per asset in the spec's universe: a RECORD of what the
    book did on `date`, not an instruction. `bars_by_asset` must already be
    truncated to bars <= date."""
    rows = []
    for asset in spec["universe"]["assets"]:
        bars = bars_by_asset[asset]
        if not bars or bars[-1]["date"] != date:
            raise ValueError(f"no {asset} bar for {date}")
        # Spec s10.11 (T3 review): mirror run_spec's periods_per_year
        # derivation here too. Without it, an fx spec reaching quarantine
        # would forward-run on /365 financing and sqrt(365) vol sizing --
        # silently diverging from the same spec's screen/gauntlet numbers at
        # the funnel's endpoint, the stage nothing downstream re-checks.
        periods_per_year = cells.SESSION_PERIODS.get(
            spec["universe"].get("session"), 365)
        book = simulate_asset(spec["blocks"], bars, spec["cost_model"],
                              periods_per_year)
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
    only: intraday timeframes, where the time part is load-bearing, never
    reach this module."""
    bars = load_bars(data_dir, asset, cutoff)
    for b in bars:
        b["date"] = b["date"][:10]
    return bars


def _load_eligible_bars_or_refuse(data_dir: Path, assets: list[str],
                                  date: str) -> dict[str, list[dict]] | None:
    """Bars <= date for every asset an eligible spec trades, or None after
    printing the refusal. A missing price FILE is a hard refusal -- a wrong
    path or a broken data dir must never read as publication lag -- while a
    missing BAR is judged per spec by the caller (2026-08-27 addendum)."""
    bars_by_asset: dict[str, list[dict]] = {}
    for asset in assets:
        path = data_dir / f"{asset}_1d.csv"
        if not path.exists():
            print(f"REFUSED: no price file for {asset} at {path}.",
                  file=sys.stderr)
            return None
        bars_by_asset[asset] = _daily_bars(data_dir, asset, date)
    return bars_by_asset


def _owed_by_date(data_dir: Path, spec: dict, after: str,
                  through: str) -> dict[str, set[str]] | None:
    """{bar date: assets that owed a decision on it} over (after, through].
    None if a price file is missing."""
    owed: dict[str, set[str]] = {}
    for asset in spec["universe"]["assets"]:
        if not (data_dir / f"{asset}_1d.csv").exists():
            return None
        for b in _daily_bars(data_dir, asset, through):
            if b["date"] > after:
                owed.setdefault(b["date"], set()).add(asset)
    return owed


def _last_bar_date(data_dir: Path, spec: dict) -> str | None:
    """The newest bar available across the strategy's universe, or None if any
    price file is missing or empty. This is where the completeness window has
    to end: a record that stopped writing must not audit as complete."""
    latest = None
    for asset in spec["universe"]["assets"]:
        if not (data_dir / f"{asset}_1d.csv").exists():
            return None
        bars = _daily_bars(data_dir, asset, "9999-12-31")
        if not bars:
            return None
        if latest is None or bars[-1]["date"] > latest:
            latest = bars[-1]["date"]
    return latest


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


def review(registry: Registry, quarantined: list[str],
           entered: dict[str, str], specs: dict[str, dict],
           artifacts_dir: Path, data_dir: Path) -> int:
    """Report progress against the pre-declared minimum. WRITES NOTHING."""
    if not quarantined:
        print("No strategies in 'quarantine' state.")
        return 0

    rows_by_sid: dict[str, list[dict]] = {}
    for r in decision_entries(registry):
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


def run(argv: list[str] | None = None) -> int:
    layer = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--registry", type=Path, default=layer / "registry_log.jsonl")
    ap.add_argument("--data-dir", type=Path, default=layer / "data")
    ap.add_argument("--artifacts-dir", type=Path, default=layer / "artifacts")
    ap.add_argument("--date", help="YYYY-MM-DD; the trading day to record")
    ap.add_argument("--review", action="store_true",
                    help="report progress against the minimum; writes nothing")
    args = ap.parse_args(argv)

    if args.review == bool(args.date):
        print("Give exactly one of --date YYYY-MM-DD or --review.",
              file=sys.stderr)
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
    states = registry.strategy_states()
    entered = quarantine_entry_dates(registry)
    quarantined = sorted(sid for sid, st in states.items()
                         if st == "quarantine")
    specs = {e["payload"]["strategy_id"]: e["payload"]
             for e in registry.entries()
             if e["entry_type"] == "strategy_registered"}

    if args.review:
        return review(registry, quarantined, entered, specs,
                      args.artifacts_dir, args.data_dir)

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

    # Per-class calendars (2026-08-27 addendum): a spec records only when
    # EVERY asset in its universe has a bar for the date. A missing bar with
    # the file present is publication lag (FRED-fed FX runs ~a week behind
    # the crypto calendar): that spec DEFERS, loudly, and an explicit --date
    # backfill records it once the bar publishes. A missing FILE stays a hard
    # refusal inside the loader.
    bars_by_asset: dict[str, list[dict]] = {}
    ready: list[str] = []
    if eligible:
        loaded = _load_eligible_bars_or_refuse(
            args.data_dir,
            sorted({a for sid in eligible
                    for a in specs[sid]["universe"]["assets"]}),
            args.date)
        if loaded is None:
            return 1
        bars_by_asset = loaded
        for sid in eligible:
            missing = [a for a in specs[sid]["universe"]["assets"]
                       if not bars_by_asset[a]
                       or bars_by_asset[a][-1]["date"] != args.date]
            if missing:
                a = missing[0]
                ends = (f"data ends {bars_by_asset[a][-1]['date']}"
                        if bars_by_asset[a]
                        else "no bars on or before the date")
                print(f"{sid}  deferred: no {a} bar for {args.date} yet "
                      f"({ends})")
            else:
                ready.append(sid)
        if not ready:
            # While a class that trades every calendar day is in the pool, a
            # day where NOTHING can record means the data pipeline is dead,
            # and going quiet would hide exactly the outage most likely to
            # persist unattended. Revisit if the pool ever goes tradfi-only.
            print(f"REFUSED: nothing recorded for {args.date} -- every "
                  f"eligible strategy was deferred for a missing bar. Either "
                  f"the data refresh is broken, or every class is late at "
                  f"once; backfill with an explicit --date once bars exist.",
                  file=sys.stderr)
            return 1

    # The write phase below (snapshot, then decision rows) is the only part
    # of a --date run that touches the chain. Eligibility resolution and bar
    # loading above are cheap local reads and must not hold the lock; the
    # lock is acquired as late as correctness allows, right before the first
    # possible chain write, and only when there is one to make (ready is
    # empty here only when nobody was eligible, in which case nothing below
    # writes anything and taking the lock would just waste other writers'
    # window time).
    lock = None
    if ready:
        logs_dir = args.registry.parent / "logs"
        lock = ChainLock(logs_dir, holder="quarantine",
                         purpose=f"daily {args.date}")
        try:
            lock.acquire()
        except ChainLockHeld:
            print(f"deferred_lock: chain.lock held, skipping {args.date}; "
                  f"re-run with --date {args.date} to backfill")
            return 0

    try:
        if ready:
            assets = sorted({a for sid in ready
                             for a in specs[sid]["universe"]["assets"]})
            # Provenance BEFORE any decision row, because invariant 9 asks for
            # an EARLIER snapshot: recording the bars afterwards would prove
            # nothing about what the rows were computed from.
            bars_digests = hash_bars_used(args.data_dir, assets, args.date)
            recorded = data_snapshots(registry).get(args.date)
            sups = snapshot_supplements(registry).get(args.date, [])
            if recorded is None and not sups:
                try:
                    registry.record_quarantine_snapshot(
                        {"date": args.date,
                         "data_sha256": hash_price_files(args.data_dir, assets),
                         "bars_sha256": bars_digests})
                except DuplicateQuarantineSnapshot as dup:
                    # another process chained this date's provenance between
                    # the read above and this write; reconcile against what
                    # landed
                    recorded = dup.chained
                    sups = snapshot_supplements(registry).get(args.date, [])
            if recorded is not None or sups:
                if recorded is None:
                    # The two chain reads above are separate un-locked walks,
                    # so a concurrent run can chain base+supplement BETWEEN
                    # them and leave this one seeing 'supplement but no base'
                    # on a healthy chain. Read the base again before crying
                    # defect: after the re-read, a supplement with genuinely
                    # no base can only be a hand-appended entry (the writer
                    # requires the base), which the verifier rejects --
                    # recording against it would compound the defect.
                    recorded = data_snapshots(registry).get(args.date)
                if recorded is None:
                    print(f"REFUSED: {args.date} has supplement provenance "
                          f"but no base quarantine_data_snapshot -- a "
                          f"defective chain needs human eyes, not more rows.",
                          file=sys.stderr)
                    return 1
                if (not isinstance(recorded.get("bars_sha256"), dict)
                        or not recorded["bars_sha256"]):
                    # fails CLOSED, the `or {}` precedent: a base snapshot
                    # with no usable bars_sha256 covers nothing, and papering
                    # over it with a supplement for everything would
                    # legitimise a fabricated provenance root
                    print(f"REFUSED: the quarantine_data_snapshot chained "
                          f"for {args.date} carries no usable bars_sha256 "
                          f"map, so it covers nothing -- "
                          f"{', '.join(sorted(bars_digests))} included -- "
                          f"and cannot be supplemented honestly.",
                          file=sys.stderr)
                    return 1
                covered = merged_bars_coverage(recorded, sups)
                conflicts = snapshot_conflicts(covered, bars_digests)
                if conflicts:
                    print(f"REFUSED: the provenance chained for {args.date} "
                          f"does not match the bars this run would use, so "
                          f"re-running the date would not reproduce the rows "
                          f"already on the chain:", file=sys.stderr)
                    for reason in conflicts:
                        print(f"  {reason}", file=sys.stderr)
                    return 1
                new_assets = sorted(set(bars_digests) - set(covered))
                if new_assets:
                    # the backfill of a class deferred when the base was
                    # chained
                    try:
                        registry.record_quarantine_snapshot_supplement(
                            {"date": args.date,
                             "data_sha256": hash_price_files(args.data_dir,
                                                             new_assets),
                             "bars_sha256": {a: bars_digests[a]
                                             for a in new_assets}})
                    except OverlappingQuarantineSupplement as clash:
                        # a concurrent writer covered (some of) these assets
                        # between the read above and this write; absorb only
                        # an IDENTICAL cover -- anything else is
                        # irreconcilable
                        landed = clash.chained.get("bars_sha256", {})
                        if any(landed.get(a) != bars_digests[a]
                               for a in new_assets):
                            print(f"REFUSED: a concurrent supplement for "
                                  f"{args.date} covers {new_assets} with "
                                  f"different bar hashes than this run "
                                  f"computed.", file=sys.stderr)
                            return 1

        seen = existing_decisions(registry)
        n_written = n_skipped = 0
        try:
            for sid in ready:
                for row in observe_day(specs[sid], bars_by_asset, args.date,
                                       entered[sid]):
                    key = (row["strategy_id"], row["date"], row["asset"])
                    if key in seen:
                        n_skipped += 1
                        continue
                    try:
                        registry.record_quarantine_decision(row)
                    except DuplicateQuarantineDecision as dup:
                        # Another process chained this row between our
                        # snapshot of `seen` and this write. The structural
                        # guarantee held -- no duplicate exists -- so a
                        # scheduler retry overlapping the daily job resolves
                        # quietly and truthfully instead of paging someone.
                        # Only this case is absorbed; every other ValueError
                        # from the writer means a malformed payload and stays
                        # fatal.
                        #
                        # But absorb ONLY when the chained row is identical
                        # to the one just computed. Same key with different
                        # numbers means the data or the spec moved underneath
                        # us, and silently discarding the losing row would
                        # hide exactly that.
                        if getattr(dup, "chained", None) != row:
                            raise
                        seen.add(key)
                        n_skipped += 1
                        continue
                    seen.add(key)
                    n_written += 1
                    print(f"{sid}  {row['asset']}  {row['action']:<11} "
                          f"px={row['price']:.2f}  "
                          f"pos={row['position_frac']:.3f} "
                          f" eq={row['equity']:.4f}")
        except BaseException:
            print(f"\nPARTIAL WRITE: {n_written} decision(s) chained before "
                  f"failure. Re-running this date is a no-op for what "
                  f"landed.", file=sys.stderr)
            raise

        print(f"\n{n_written} decision(s) chained, {n_skipped} already "
              f"present.")
        return 0
    finally:
        if lock is not None:
            lock.release()


if __name__ == "__main__":
    sys.exit(run())
