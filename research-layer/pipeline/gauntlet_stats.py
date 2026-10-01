"""Recorded statistics for v6.1 gauntlet verdicts (Build 2a).

Run nightly at 01:00 by \\Morpheus\\28_GauntletStats. On a WEEKLY data
vintage (bars truncated at the most recent Sunday) so simcache keys hold all
week and each night resumes where the last stopped. Computes effective
trials, the deflated Sharpe, PBO (recorded only), plateau_ok and the
Harvey-Liu haircut for every v6.1 verdict without a gauntlet_stats entry,
and with --chain appends one gauntlet_stats entry per verdict. Never changes
a strategy's state. Without --chain it only reports (the 480 -> 44
investigation must be accepted before --chain is scheduled).

Method, figure by figure (docs/2026-09-30-gauntlet-at-scale-design.md 4.2):
- the registry-wide series and the clustering are gauntlet.registry_series
  and gauntlet.cluster_registry, the functions gauntlet.run itself calls;
  the sibling families are gauntlet.sibling_families. Nothing is re-derived.
- deflated Sharpe: evaluate_spec's formula, psr(sharpe(r),
  expected_max_sharpe(trials_n, trials_var), len(r), skew, kurt), over the
  SAME series gauntlet.run passed evaluate_spec as daily_returns:
  cluster_registry's returns_by_id[sid] -- intersection-trimmed when classes
  are mixed or calendars ragged, native otherwise (Ruling 11; a different
  window would make v6 and v6.1 DSRs on one chain incomparable). The
  alignment used is recorded beside it (trials_alignment,
  trials_common_days), as v6 recorded it in metrics.
- PBO: the helpers and arguments of gauntlet.run's PBO section (train
  window, CSCV_SPLITS, the group-id-seeded permutation null, which -- as
  there -- is built only for a family with a passing verdict in this batch
  and at least PBO_MIN_DISTINCT distinct configurations). RECORDED ONLY:
  no pass/fail/kill label is computed, and no pbo_family_kill key is written
  (verify_registry.py invariant 12 rejects one after the v6.1 note).
- plateau_ok: plateau.qualifies over the family, as the gauntlet recorded it.
- haircut: harvey_liu_haircut(train Sharpe, train years, trials_n), window
  "train", as _evaluate_candidate recorded it.

Resumable: the simulation is the expensive part and the simcache keeps every
series simulated before a deadline stop, keyed by the bars THROUGH the
vintage (not the whole file), so the next night's keys are the same. With
--chain, entries are chained per completed chunk, so a stop also keeps every
statistic already written. Chaining follows Ruling 10: one full chain read
outside chain.lock; under it, an O(tail) advance, a re-check that drops any
verdict already statted, and at most STATS_BATCH_MAX entries per hold.

Exit 0: done, nothing to do, stopped at the deadline, or chain.lock held.
Exit 1: the data or the chain refused the run (status exit_reason says why).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import cells
from . import simcache
from .chainlock import ChainLock, ChainLockHeld
from .common import entry_hash
from .registry import (ChainMoved, Registry, UnstableEntry, STATS_BATCH_MAX)
from .screen import assert_cells_comparable, comparable_cells, load_cell_data
from .stats import (moments, sharpe, psr, expected_max_sharpe, percentile,
                    harvey_liu_haircut)
from .pbo import cscv_pbo, distinct_configs, permutation_null, percentile_of
from .plateau import qualifies
from .gauntlet_core import (PROTOCOL_V61, DEFAULT_CUTOFF, truncate_bars,
                            _annualized_sharpe_from_returns)
# Module-level names on purpose: the tests replace them to prove the job
# calls the gauntlet's own functions rather than a copy.
from .gauntlet import cluster_registry, registry_series, sibling_families
from .gauntlet import (CSCV_SPLITS, PBO_MIN_DISTINCT, PBO_PASS_PCTILE,
                       PBO_KILL_PCTILE, PBO_NULL_DRAWS, _as_list)

LAYER = Path(__file__).resolve().parent.parent
CLUSTER_METHOD = "effective_trials/v3 correlation-distance"
# Specs per registry_series call; the deadline is checked between calls,
# so a cold night overruns it by at most one chunk's simulations.
SERIES_CHUNK = 10
STATUS_NAME = "gauntlet_stats_status.json"
STATUS_FIELDS = ("ts_utc", "vintage", "verdicts_without_stats", "stats_written",
                 "oldest_unstatted_verdict_age_hours", "trials_n", "registered_n",
                 "trials_common_days", "stopped_at_deadline", "chained")


def vintage_date(today: date) -> str:
    """The most recent Sunday on or before `today`, ISO."""
    return (today - timedelta(days=(today.weekday() + 1) % 7)).isoformat()


def bars_through_sha256(path: Path, vintage: str) -> str:
    """sha256 of a cell's CSV header plus every row dated on or before
    `vintage` (date part only), LF-normalised, in file order --
    quarantine.hash_bars_through's rule, for any timeframe. The simcache key
    is built from this, not from the whole-file hash, so appending bars
    after the vintage leaves every key (and every cached series) valid."""
    raw = path.read_bytes().replace(b"\r\n", b"\n")
    lines = raw.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    if not lines:
        raise ValueError(f"{path.name}: price file is empty")
    header, rows = lines[0], lines[1:]
    cut = vintage.encode()[:10]
    kept = [r for r in rows if r.split(b",", 1)[0][:10] <= cut]
    return hashlib.sha256(b"".join(l + b"\n" for l in [header] + kept)).hexdigest()


def data_digest_of(hashes: dict[str, str]) -> str:
    """ONE digest of everything a run read: sha256 over the sorted
    `<cell_id>:<bars_through_sha256>` lines (each newline-terminated) of
    EVERY cell the run used -- the registry-wide input to clustering.

    Ruling 14: `data_vintage` names a Sunday but does not pin the bars. fx
    rows (FRED, about a week late) and equity_etf rows (a day late) dated on
    or before that Sunday keep arriving during the week, so two entries with
    one vintage can rest on different data; this digest tells them apart."""
    body = "".join(f"{cid}:{h}\n" for cid, h in sorted(hashes.items()))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class _View:
    """What the job needs from the chain, collected in the same single read
    that builds its ChainSnapshot."""

    def __init__(self) -> None:
        self.specs: list[dict] = []
        self.screen_tc_fail: set[str] = set()
        self.verdicts: list[dict] = []        # v6.1 gauntlet verdicts, chain order

    def on_entry(self, e: dict) -> None:
        et, p = e["entry_type"], e["payload"]
        if et == "strategy_registered":
            self.specs.append(p)
        elif (et == "state_change" and p.get("to") == "graveyard"
              and p.get("reason") == "trade_count"):
            self.screen_tc_fail.add(p["strategy_id"])
        elif et == "verdict" and p.get("stage") == "gauntlet":
            m = p.get("metrics")
            if isinstance(m, dict) and m.get("protocol") == PROTOCOL_V61:
                self.verdicts.append({"vh": entry_hash(e), "sid": p["strategy_id"],
                                      "verdict": p.get("verdict"),
                                      "ts_utc": e.get("ts_utc")})


def _age_hours(ts: str | None, now: datetime) -> float | None:
    if not ts:
        return None
    t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return (now - t).total_seconds() / 3600


def group_pbo(g: str, fam: list[dict], train: dict, live: bool,
              draws: int) -> dict:
    """One family's PBO with gauntlet.run's helpers and arguments, recorded
    values only. `pbo_status` says which of run()'s paths was taken without
    naming a gate outcome: not_measured_dead_group / underpowered / measured."""
    series = {s["sid"]: train[s["sid"]] for s in fam}
    res = cscv_pbo(series, s=CSCV_SPLITS)
    n_distinct = distinct_configs(series)
    out = {"pbo": res["pbo"], "pbo_n_distinct": n_distinct,
           "pbo_percentile": None, "pbo_null_p05": None, "pbo_null_p95": None,
           "pbo_null_draws": 0}
    if not live:
        out["pbo_status"] = "not_measured_dead_group"
    elif res["pbo"] is None or n_distinct < PBO_MIN_DISTINCT:
        out["pbo_status"] = "underpowered"
    else:
        null = permutation_null(
            series, s=CSCV_SPLITS, draws=draws,
            seed=int(hashlib.sha256(g.encode()).hexdigest()[:8], 16))
        out["pbo_null_draws"] = len(null)
        if not null:
            out["pbo_status"] = "underpowered"
        else:
            out["pbo_percentile"] = percentile_of(null, res["pbo"])
            out["pbo_null_p05"] = percentile(sorted(null), PBO_PASS_PCTILE)
            out["pbo_null_p95"] = percentile(sorted(null), PBO_KILL_PCTILE)
            out["pbo_status"] = "measured"
    return out


def verdict_stats(sid: str, dsr_rets, train_rets: list[float],
                  train_sharpe: float | None, clustered: dict, pbo: dict,
                  plateau_ok: bool, vintage: str, data_digest: str,
                  data_end_by_cell: dict[str, str]) -> dict:
    """The gauntlet_stats payload (less strategy_id / verdict_entry_hash)."""
    trials_n, trials_var = clustered["trials_n"], clustered["trials_var"]
    r = _as_list(dsr_rets)      # clustered["returns_by_id"][sid] (Ruling 11)
    sr_hat = sharpe(r)
    _, _, skew, kurt = moments(r)
    sr_star = expected_max_sharpe(trials_n, trials_var)
    # The gauntlet's t_years counted the train-window EQUITY points: the
    # first point plus one per train return (daily_returns_with_dates drops a
    # step only after equity <= 0). With no train return the haircut takes
    # its sr <= 0 branch, where t_years is never read.
    n_train_points = len(train_rets) + 1 if train_rets else 0
    return {
        "trials_n": trials_n,
        "registered_n": clustered["registered_n"],
        "trials_sr_var": trials_var,
        "expected_max_sharpe": sr_star,
        "trials_alignment": clustered["trials_alignment"],
        "trials_common_days": clustered["trials_common_days"],
        "deflated_sharpe": psr(sr_hat, sr_star, len(r), skew, kurt),
        **pbo,
        "plateau_ok": plateau_ok,
        "haircut": dict(harvey_liu_haircut(train_sharpe or 0.0,
                                           t_years=n_train_points / 365.0,
                                           n_trials=trials_n),
                        window="train"),
        "cluster_method": CLUSTER_METHOD,
        "data_vintage": vintage,
        "data_digest": data_digest,
        "data_end_by_cell": data_end_by_cell,
    }


def _write_status(logs_dir: Path, status: dict) -> None:
    p = logs_dir / STATUS_NAME
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(status, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(p)


def _fmt(v) -> str:
    return "-" if v is None else str(v)


def _write_report(path: Path, status: dict, extra: dict) -> None:
    lines = ["# gauntlet_stats report", "", "| key | value |", "|---|---|"]
    for k in STATUS_FIELDS:
        lines.append(f"| {k} | {_fmt(status.get(k))} |")
    for k, v in extra.items():
        lines.append(f"| {k} | {_fmt(v)} |")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--registry", type=Path, default=LAYER / "registry_log.jsonl")
    ap.add_argument("--data-dir", type=Path, default=LAYER / "data")
    ap.add_argument("--logs-dir", type=Path, default=LAYER / "logs")
    # Beside the registry, as gauntlet.run's default: every tmp-registry
    # test gets its own cache, and the live run shares research-layer/simcache.
    ap.add_argument("--simcache-dir", type=Path, default=None)
    ap.add_argument("--cutoff", default=DEFAULT_CUTOFF)
    ap.add_argument("--pbo-null-draws", type=int, default=PBO_NULL_DRAWS)
    ap.add_argument("--deadline-hours", type=float, default=5.75)
    ap.add_argument("--chain", action="store_true",
                    help="append gauntlet_stats entries (off = report only)")
    ap.add_argument("--report", type=Path, default=None)
    a = ap.parse_args(argv)
    t_end = time.time() + a.deadline_hours * 3600
    a.logs_dir.mkdir(parents=True, exist_ok=True)
    simcache_dir = a.simcache_dir or (a.registry.resolve().parent / "simcache")
    vintage = vintage_date(date.today())
    status = {"vintage": vintage, "verdicts_without_stats": 0, "stats_written": 0,
              "oldest_unstatted_verdict_age_hours": 0.0, "trials_n": None,
              "registered_n": None, "trials_common_days": None,
              "stopped_at_deadline": False, "chained": bool(a.chain),
              "exit_reason": None}
    extra: dict = {}
    done: set[str] = set()                    # verdict hashes chained this run
    pending: list[dict] = []

    def finish(rc: int, reason: str) -> int:
        left = [v for v in pending if v["vh"] not in done]
        now = datetime.now(timezone.utc)
        ages = [h for h in (_age_hours(v["ts_utc"], now) for v in left)
                if h is not None]
        status["verdicts_without_stats"] = len(left)
        status["oldest_unstatted_verdict_age_hours"] = (
            round(max(ages), 2) if ages else 0.0)
        status["exit_reason"] = reason
        status["ts_utc"] = now.isoformat()
        _write_status(a.logs_dir, status)
        if a.report is not None:
            _write_report(a.report, status, extra)
        print(f"gauntlet_stats: {reason} {json.dumps(status, sort_keys=True)}",
              flush=True)
        return rc

    try:
        registry = Registry(a.registry)
        view = _View()
        snap = registry.snapshot(on_entry=view.on_entry, track_stats=True)
        pending = [v for v in view.verdicts if v["vh"] not in snap.statted]
        all_specs = view.specs
        by_class: dict[str, int] = {}
        for s in all_specs:
            c = s["universe"].get("asset_class", "crypto")
            by_class[c] = by_class.get(c, 0) + 1
        extra.update({f"registered_{c}": n for c, n in sorted(by_class.items())})
        print(f"gauntlet_stats: {len(pending)} v6.1 verdict(s) without stats, "
              f"{len(all_specs)} registered, vintage {vintage}", flush=True)
        if not pending:
            return finish(0, "nothing_to_do")

        # Every registered spec's cells, cut at the vintage Sunday.
        cells_needed, class_of = comparable_cells(all_specs)
        bars_by_cell, _, _ = load_cell_data(a.data_dir, cells_needed, "9999-12-31")
        bars_by_cell = truncate_bars(
            bars_by_cell, {cells.cell_id(asset, tf): vintage for asset, tf in bars_by_cell})
        data_end = {cells.cell_id(asset, tf): (bars[-1]["date"] if bars else "")
                    for (asset, tf), bars in bars_by_cell.items()}
        try:
            assert_cells_comparable(data_end, class_of=class_of)
        except ValueError as exc:
            print(f"REFUSED: cells not comparable at vintage {vintage}: {exc}", flush=True)
            return finish(1, "cells_not_comparable")
        hashes = {cells.cell_id(asset, tf): bars_through_sha256(
                      a.data_dir / f"{asset}_{tf}.csv", vintage)
                  for asset, tf in bars_by_cell}
        data_digest = data_digest_of(hashes)

        cache = simcache.SimCache(simcache_dir)
        dated, eq_len = {}, {}
        hits = misses = 0
        for i in range(0, len(all_specs), SERIES_CHUNK):
            if time.time() >= t_end:
                status["stopped_at_deadline"] = True
                print(f"DEADLINE: {len(dated)}/{len(all_specs)} series ready "
                      f"({hits} cached, {misses} simulated and now cached); the "
                      f"next run resumes from the cache.", flush=True)
                return finish(0, "deadline")
            d, e, h, m = registry_series(all_specs[i:i + SERIES_CHUNK], bars_by_cell,
                                         hashes, cache, set())
            dated.update(d)
            eq_len.update(e)
            hits, misses = hits + h, misses + m
        bars_by_cell.clear()
        print(f"sim cache: {hits} hit(s), {misses} miss(es) over {len(all_specs)} "
              f"registered strategies", flush=True)

        # Ruling 14: an over-long last chunk must not start a clustering pass
        # the PT6H wall would kill with no status written.
        if time.time() >= t_end:
            status["stopped_at_deadline"] = True
            print(f"DEADLINE: all {len(dated)} series ready and cached, but no "
                  f"time left to cluster; the next run starts there.", flush=True)
            return finish(0, "deadline")
        try:
            clustered = cluster_registry(dated, eq_len, all_specs)
        except ValueError as exc:
            print(f"REFUSED: clustering: {exc}", flush=True)
            return finish(1, "cluster_refused")
        status.update({k: clustered[k] for k in
                       ("trials_n", "registered_n", "trials_common_days")})
        extra["trials_alignment"] = clustered["trials_alignment"]

        train = {s["strategy_id"]: dated[s["strategy_id"]].train(a.cutoff)
                 for s in all_specs}
        train_sharpe = {sid: _annualized_sharpe_from_returns(r)
                        for sid, r in train.items()}
        family_by_group, grids_by_group = sibling_families(
            all_specs, train_sharpe, view.screen_tc_fail)
        group_of = {s["strategy_id"]: s["provenance"]["sibling_group_id"]
                    for s in all_specs}
        spec_of = {s["strategy_id"]: s for s in all_specs}

        def own_ends(sid: str) -> dict[str, str]:
            """The strategy's OWN cells' truncated data ends (Ruling 14)."""
            u = spec_of[sid]["universe"]
            tf = u.get("timeframe", "1d")
            return {cid: data_end[cid] for cid in sorted(
                {cells.cell_id(asset, tf) for asset in u["assets"]})}
        pending_by_group: dict[str, list[dict]] = {}
        for v in pending:
            pending_by_group.setdefault(group_of[v["sid"]], []).append(v)

        items: list[tuple[str, str, dict]] = []

        def flush() -> str | None:
            """Chain what is computed, STATS_BATCH_MAX per chain.lock hold.
            Returns an exit reason when chaining must stop, else None."""
            nonlocal snap, items
            if not a.chain:
                items = []
                return None
            while items:
                chunk, rest = items[:STATS_BATCH_MAX], items[STATS_BATCH_MAX:]
                try:
                    with ChainLock(a.logs_dir, "gauntlet-stats",
                                   f"{len(chunk)} gauntlet_stats"):
                        snap = registry.advance(snap)
                        fresh = [vh for _, vh, _ in chunk if vh not in snap.statted]
                        snap = registry.record_gauntlet_stats_batch(snap, chunk)
                except ChainLockHeld:
                    print("chain.lock held: stats deferred to the next run", flush=True)
                    return "deferred_lock"
                except (ChainMoved, UnstableEntry) as exc:
                    print(f"REFUSED: nothing written for this chunk: {exc}", flush=True)
                    return "chain_refused"
                status["stats_written"] += len(fresh)
                done.update(vh for _, vh, _ in chunk)
                items = rest
            return None

        for g, fam in family_by_group.items():
            todo = pending_by_group.get(g)
            if not todo:
                continue
            if time.time() >= t_end:
                status["stopped_at_deadline"] = True
                break
            live = any(v["verdict"] == "pass" for v in todo)
            pbo = group_pbo(g, fam, train, live, a.pbo_null_draws)
            grids = grids_by_group.get(g, {})
            for v in todo:
                sid = v["sid"]
                sibling = next(x for x in fam if x["sid"] == sid)
                ok, _ = qualifies(sibling, fam, grids)
                items.append((sid, v["vh"], verdict_stats(
                    sid, clustered["returns_by_id"][sid], train[sid],
                    train_sharpe[sid], clustered,
                    pbo, ok, vintage, data_digest, own_ends(sid))))
            if len(items) >= STATS_BATCH_MAX:
                stop = flush()
                if stop is not None:
                    return finish(1 if stop == "chain_refused" else 0, stop)
        stop = flush()
        if stop is not None:
            return finish(1 if stop == "chain_refused" else 0, stop)
        if not a.chain:
            print(f"report only: {len(pending)} verdict(s) computed, nothing chained "
                  f"(--chain is off)", flush=True)
        return finish(0, "deadline" if status["stopped_at_deadline"] else "done")
    except Exception:
        # A chain, disk or data failure outside the expected refusals:
        # leave a status that says so (the Sentinel reads it), then raise.
        finish(1, "crashed")
        raise


if __name__ == "__main__":
    sys.exit(run())
