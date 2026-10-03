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
  window, CSCV_SPLITS, the group-id-seeded permutation null). SCOPE (step 7,
  gauntlet-protocol-v6.1-amendment-1): the null is built for EVERY family
  that has a v6.1 verdict needing stats, whenever the family is measurable
  (an observed PBO and at least PBO_MIN_DISTINCT distinct configurations),
  whether or not any of its verdicts passed. Until step 7 it was built only
  for a family with a passing verdict in the batch, so siblings statted on
  different nights could record different pbo_status. The fields depend only
  on the family's members and train series, so the rule is deterministic;
  each null is cached per (family, vintage, data_digest, inputs) in
  logs/gauntlet_stats_pbo_cache.json and computed once per vintage.
  RECORDED ONLY: no pass/fail/kill label is computed, and no
  pbo_family_kill key is written (verify_registry.py invariant 12 rejects
  one after the v6.1 note).
- plateau_ok: plateau.qualifies over the family, as the gauntlet recorded it.
- haircut: harvey_liu_haircut(train Sharpe, train years, trials_n), window
  "train", as _evaluate_candidate recorded it.
- effective trials are FLOORED at the highest k ever recorded on the chain
  (Ruling 20, Coen option 1): the silhouette argmax is unstable on a growing
  registry (302 -> 480 -> 44), and a lower N would flatter every later
  deflated Sharpe. Each entry records trials_n_raw (this run's argmax, method
  unchanged), trials_n_floor + trials_n_floor_entry_hash (the chain max BEFORE
  this run and the entry that supplied it, or null), and trials_n =
  max(raw, floor), the figure the deflated Sharpe AND the haircut use.
  trials_sr_var stays this run's. See _View.on_entry for what counts.

Resumable: the simulation is the expensive part and the simcache keeps every
series simulated before a deadline stop, keyed by the bars THROUGH the
vintage (not the whole file), so the next night's keys are the same. With
--chain, entries are chained per completed chunk, so a stop also keeps every
statistic already written. Chaining follows Ruling 10: one full chain read
outside chain.lock; under it, an O(tail) advance, a re-check that drops any
verdict already statted, and at most STATS_BATCH_MAX entries per hold.

chain.lock held at a flush (step 7, T7): the computed entries are KEPT in
memory and the flush is retried later in the same run, never waited for:
one non-blocking attempt after each further family while a full batch is
waiting, then a final drain of attempts DRAIN_INTERVAL_S apart for at most
DRAIN_RESERVE_S, never past the deadline. Once a flush has been held, the family loop holds DRAIN_RESERVE_S
back from the deadline for that drain. Entries still unwritten at the end
are dropped (exit_reason deferred_lock, exit 0) and the next run recomputes
them. chain.lock is never waited on, polled inside, or broken here.

Deadline (I5): clustering starts only when the time left covers its
estimate (the last measured duration, persisted in the status as cluster_s,
else CLUSTER_PRIOR_S); each PBO null likewise (pbo_null_mean_s, else
gauntlet.PBO_NULL_PRIOR_S). A pass that does not fit is a deadline stop.
One instance at a time (logs/gauntlet_stats.lock); a second one defers.

Exit 0: done, nothing to do, stopped at the deadline, chain.lock held, or
another instance running (that one owns the status file; it is not touched).
Exit 1: the data or the chain refused the run (status exit_reason says why).
"""
from __future__ import annotations

import argparse
import hashlib
from array import array
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
                       PBO_KILL_PCTILE, PBO_NULL_DRAWS, PBO_NULL_PRIOR_S, _as_list)

LAYER = Path(__file__).resolve().parent.parent
CLUSTER_METHOD = "effective_trials/v3 correlation-distance"
# Specs per registry_series call; the deadline is checked between calls,
# so a cold night overruns it by at most one chunk's simulations.
SERIES_CHUNK = 10
# I5 (final review): a clustering pass is started only when the time left
# covers an estimate of it -- the last measured duration (status `cluster_s`,
# carried forward across runs), or this prior before one has been measured
# (about 1.5 h at 11.8k strategies, Ruling 13). Each PBO null likewise needs
# the measured mean (status `pbo_null_mean_s`) or gauntlet's PBO_NULL_PRIOR_S.
CLUSTER_PRIOR_S = 2 * 3600.0
# The job's own instance lock (M2). A run is bounded at 5.75 h inside PT6H,
# so a lock older than 7 h is stale; it is broken only when its holder is
# also dead, exactly as the worker's.
INSTANCE_LOCK = "gauntlet_stats.lock"
INSTANCE_STALE_AFTER_S = 7 * 3600
STATUS_NAME = "gauntlet_stats_status.json"
# Part C (step 7): one family's PBO result per (family, vintage, data_digest,
# inputs), so a family whose verdicts arrive over several nights of one
# vintage builds its permutation null once. Only the current vintage and
# digest are kept; an unreadable file is an empty cache, never an error.
PBO_CACHE_NAME = "gauntlet_stats_pbo_cache.json"
STATUS_FIELDS = ("ts_utc", "vintage", "verdicts_without_stats", "stats_written",
                 "retried_written", "deferred_lock",
                 "oldest_unstatted_verdict_age_hours", "trials_n_raw",
                 "trials_n_effective", "registered_n", "trials_common_days",
                 "stopped_at_deadline", "chained", "cluster_s", "pbo_null_mean_s",
                 "pbo_nulls_computed", "pbo_nulls_cached")
# T7 flush (step 7): computed entries a flush could not chain because
# chain.lock was held are kept and retried in the same run. Once that has
# happened the family loop starts nothing that would eat into this reserve,
# and the final drain makes one non-blocking attempt every DRAIN_INTERVAL_S
# inside it, never past the deadline. The worker's figures (2026-10-02): a
# reserve that outlasts a scanner card batch several times over; the loop's
# hours-long screen hold is outlasted by nothing affordable.
DRAIN_RESERVE_S = 75.0
DRAIN_INTERVAL_S = 5.0
# Injectable so tests drive the drain on a fake clock instead of sleeping.
_sleep = time.sleep


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


# Ruling 20: only these gauntlet protocols recorded a CLUSTER count in
# metrics.trials_n. No protocol / v2 hold a registration count (excluded), and
# v6.1 verdicts carry no trials_n.
FLOOR_PROTOCOLS = ("gauntlet-protocol-v3", "gauntlet-protocol-v4",
                   "gauntlet-protocol-v5", "gauntlet-protocol-v6")


class _View:
    """What the job needs from the chain, collected in the same single read
    that builds its ChainSnapshot."""

    def __init__(self) -> None:
        self.specs: list[dict] = []
        self.screen_tc_fail: set[str] = set()
        self.verdicts: list[dict] = []        # v6.1 gauntlet verdicts, chain order
        # Ruling 20: the highest k recorded ON THE CHAIN so far, and the hash
        # of the entry that supplied it (the first, on a tie).
        self.trials_floor: int | None = None
        self.trials_floor_hash: str | None = None

    def _floor_candidate(self, n, e: dict) -> None:
        # A cluster count is a positive int; anything else is no floor.
        if (isinstance(n, int) and not isinstance(n, bool) and n > 0
                and (self.trials_floor is None or n > self.trials_floor)):
            self.trials_floor, self.trials_floor_hash = n, entry_hash(e)

    def on_entry(self, e: dict) -> None:
        et, p = e["entry_type"], e["payload"]
        if et == "strategy_registered":
            self.specs.append(p)
        elif (et == "state_change" and p.get("to") == "graveyard"
              and p.get("reason") == "trade_count"):
            self.screen_tc_fail.add(p["strategy_id"])
        elif et == "gauntlet_stats":
            self._floor_candidate(p.get("trials_n_raw"), e)
        elif et == "verdict" and p.get("stage") == "gauntlet":
            m = p.get("metrics")
            if isinstance(m, dict) and m.get("protocol") in FLOOR_PROTOCOLS:
                self._floor_candidate(m.get("trials_n"), e)
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
              draws: int, null_fits=None, null_times: list | None = None
              ) -> dict | None:
    """One family's PBO with gauntlet.run's helpers and arguments, recorded
    values only. `pbo_status` says which of run()'s paths was taken without
    naming a gate outcome: not_measured_dead_group / underpowered / measured.

    I5: `null_fits()` is asked before the permutation null is built; False
    returns None (the caller stops at the deadline, nothing recorded for the
    family). Each null's measured duration is appended to `null_times`."""
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
        if null_fits is not None and not null_fits():
            return None
        t0 = time.time()
        null = permutation_null(
            series, s=CSCV_SPLITS, draws=draws,
            seed=int(hashlib.sha256(g.encode()).hexdigest()[:8], 16))
        if null_times is not None:
            null_times.append(time.time() - t0)
        out["pbo_null_draws"] = len(null)
        if not null:
            out["pbo_status"] = "underpowered"
        else:
            out["pbo_percentile"] = percentile_of(null, res["pbo"])
            out["pbo_null_p05"] = percentile(sorted(null), PBO_PASS_PCTILE)
            out["pbo_null_p95"] = percentile(sorted(null), PBO_KILL_PCTILE)
            out["pbo_status"] = "measured"
    return out


def pbo_inputs_sha(g: str, fam: list[dict], train: dict, draws: int) -> str:
    """Everything group_pbo's result depends on: the family id (it seeds the
    null), the draw count, CSCV_SPLITS, and each member's train series, in
    sorted member order."""
    h = hashlib.sha256(json.dumps([g, draws, CSCV_SPLITS]).encode("utf-8"))
    for sid in sorted(s["sid"] for s in fam):
        r = array("d", train[sid])
        h.update(f"\n{sid}:{len(r)}:".encode("utf-8"))
        h.update(r.tobytes())
    return h.hexdigest()


class PboCache:
    """logs/gauntlet_stats_pbo_cache.json: {vintage, data_digest, families:
    {g: {inputs_sha, pbo}}}. A file for another vintage or digest, or one
    that cannot be read, starts empty. Written atomically after each null."""

    def __init__(self, logs_dir: Path, vintage: str, data_digest: str) -> None:
        self.path = logs_dir / PBO_CACHE_NAME
        self.vintage, self.data_digest = vintage, data_digest
        self.families: dict = {}
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            if (isinstance(d, dict) and d.get("vintage") == vintage
                    and d.get("data_digest") == data_digest
                    and isinstance(d.get("families"), dict)):
                self.families = d["families"]
        except (OSError, ValueError):
            pass

    def get(self, g: str, inputs_sha: str) -> dict | None:
        hit = self.families.get(g)
        if (isinstance(hit, dict) and hit.get("inputs_sha") == inputs_sha
                and isinstance(hit.get("pbo"), dict)):
            return dict(hit["pbo"])
        return None

    def put(self, g: str, inputs_sha: str, pbo: dict) -> None:
        self.families[g] = {"inputs_sha": inputs_sha, "pbo": dict(pbo)}
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"vintage": self.vintage,
                                   "data_digest": self.data_digest,
                                   "families": self.families}, sort_keys=True),
                       encoding="utf-8")
        tmp.replace(self.path)


def verdict_stats(sid: str, dsr_rets, train_rets: list[float],
                  train_sharpe: float | None, clustered: dict, pbo: dict,
                  plateau_ok: bool, vintage: str, data_digest: str,
                  data_end_by_cell: dict[str, str],
                  trials_floor: int | None = None,
                  trials_floor_hash: str | None = None) -> dict:
    """The gauntlet_stats payload (less strategy_id / verdict_entry_hash).
    `trials_floor` / `trials_floor_hash` are the chain max before this run
    and the entry that supplied it (Ruling 20), the same for every entry of
    a run."""
    trials_raw, trials_var = clustered["trials_n"], clustered["trials_var"]
    trials_n = max(trials_raw, trials_floor or 0)
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
        "trials_n_raw": trials_raw,
        "trials_n_floor": trials_floor,
        "trials_n_floor_entry_hash": trials_floor_hash,
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


def _prior_timings(logs_dir: Path) -> tuple[float | None, float | None]:
    """(cluster_s, pbo_null_mean_s) persisted by the last run that wrote a
    status; None for a missing, unreadable or non-positive value."""
    try:
        st = json.loads((logs_dir / STATUS_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    if not isinstance(st, dict):
        return None, None

    def pos(v):
        return (float(v) if isinstance(v, (int, float))
                and not isinstance(v, bool) and v > 0 else None)
    return pos(st.get("cluster_s")), pos(st.get("pbo_null_mean_s"))


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
    prior_cluster_s, prior_null_s = _prior_timings(a.logs_dir)
    status = {"vintage": vintage, "verdicts_without_stats": 0, "stats_written": 0,
              # T7 flush: written on a retry after a held flush / still
              # unwritten (dropped) at the end of the run
              "retried_written": 0, "deferred_lock": 0,
              # part C: PBO nulls built this run / served from the cache
              "pbo_nulls_computed": 0, "pbo_nulls_cached": 0,
              "oldest_unstatted_verdict_age_hours": 0.0, "trials_n_raw": None,
              "trials_n_effective": None,
              "registered_n": None, "trials_common_days": None,
              "stopped_at_deadline": False, "chained": bool(a.chain),
              # carried forward until this run measures its own (I5)
              "cluster_s": prior_cluster_s, "pbo_null_mean_s": prior_null_s,
              "exit_reason": None}
    extra: dict = {}
    done: set[str] = set()                    # verdict hashes chained this run
    pending: list[dict] = []

    def finish(rc: int, reason: str, write: bool = True) -> int:
        left = [v for v in pending if v["vh"] not in done]
        now = datetime.now(timezone.utc)
        ages = [h for h in (_age_hours(v["ts_utc"], now) for v in left)
                if h is not None]
        status["verdicts_without_stats"] = len(left)
        status["oldest_unstatted_verdict_age_hours"] = (
            round(max(ages), 2) if ages else 0.0)
        status["exit_reason"] = reason
        status["ts_utc"] = now.isoformat()
        if write:
            _write_status(a.logs_dir, status)
            if a.report is not None:
                _write_report(a.report, status, extra)
        print(f"gauntlet_stats: {reason} {json.dumps(status, sort_keys=True)}",
              flush=True)
        return rc

    # M2: one stats job at a time. A second instance (a hand --chain run
    # beside the 01:00 task) defers, exits 0 and never touches the status or
    # the report: the live instance owns them. Without this, run B could read
    # its trials floor before run A's entries are chained and record a lower
    # N after A's, breaking the addendum's "trials_n never falls".
    inst = ChainLock(a.logs_dir, "gauntlet-stats", "stats run",
                     stale_after_s=INSTANCE_STALE_AFTER_S, name=INSTANCE_LOCK)
    try:
        inst.acquire()
    except ChainLockHeld:
        if not (inst.is_stale() and not inst.holder_alive()):
            return finish(0, "deferred_instance", write=False)
        try:
            inst.break_stale()
            inst.acquire()
        except ChainLockHeld:
            return finish(0, "deferred_instance", write=False)
    try:
        return _run_locked(a, t_end, vintage, simcache_dir, status, extra, done,
                           pending, finish, prior_cluster_s, prior_null_s)
    finally:
        inst.release()


def _run_locked(a, t_end: float, vintage: str, simcache_dir: Path, status: dict,
                extra: dict, done: set, pending: list, finish,
                prior_cluster_s: float | None,
                prior_null_s: float | None) -> int:
    """run()'s body, under the instance lock. `pending` is filled IN PLACE so
    run()'s finish() sees it."""
    try:
        registry = Registry(a.registry)
        view = _View()
        snap = registry.snapshot(on_entry=view.on_entry, track_stats=True)
        pending[:] = [v for v in view.verdicts if v["vh"] not in snap.statted]
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

        # Ruling 14 + I5: never start a clustering pass the PT6H wall would
        # kill with no status written. The time left must cover the estimate
        # (last measured duration, else CLUSTER_PRIOR_S), not merely be > 0.
        cluster_est = prior_cluster_s or CLUSTER_PRIOR_S
        left_s = t_end - time.time()
        if left_s < cluster_est:
            status["stopped_at_deadline"] = True
            print(f"DEADLINE: all {len(dated)} series ready and cached, but "
                  f"{max(left_s, 0.0):.0f}s left is under the clustering estimate "
                  f"({cluster_est:.0f}s); the next run starts there.", flush=True)
            return finish(0, "deadline")
        t_cluster0 = time.time()
        try:
            clustered = cluster_registry(dated, eq_len, all_specs)
        except ValueError as exc:
            print(f"REFUSED: clustering: {exc}", flush=True)
            return finish(1, "cluster_refused")
        status["cluster_s"] = round(time.time() - t_cluster0, 1)
        # Ruling 20: ONE floor per run, from the read above, before chaining.
        floor, floor_hash = view.trials_floor, view.trials_floor_hash
        status.update({"trials_n_raw": clustered["trials_n"],
                       "trials_n_effective": max(clustered["trials_n"], floor or 0),
                       "registered_n": clustered["registered_n"],
                       "trials_common_days": clustered["trials_common_days"]})
        extra["trials_alignment"] = clustered["trials_alignment"]
        extra["trials_n_floor"] = floor

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
        # T7 flush: verdict hashes whose computed entry was kept because a
        # flush found chain.lock held. Non-empty = the run holds the reserve.
        held_vh: set[str] = set()

        def flush() -> str | None:
            """Chain what is computed, STATS_BATCH_MAX per chain.lock hold,
            each hold one non-blocking attempt. Returns "held" when chain.lock
            is held (everything not yet chained stays in `items`, kept for a
            retry), "chain_refused" when chaining must stop, else None."""
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
                    if not held_vh:
                        print(f"chain.lock held: {len(items)} computed stats entr"
                              f"{'y' if len(items) == 1 else 'ies'} kept for a "
                              f"retry this run", flush=True)
                    held_vh.update(vh for _, vh, _ in items)
                    return "held"
                except (ChainMoved, UnstableEntry) as exc:
                    print(f"REFUSED: nothing written for this chunk: {exc}", flush=True)
                    return "chain_refused"
                status["stats_written"] += len(fresh)
                status["retried_written"] += sum(1 for vh in fresh if vh in held_vh)
                done.update(vh for _, vh, _ in chunk)
                items = rest
            return None

        def reserve_s() -> float:
            """Time the family loop leaves for the final drain: the reserve
            once a flush has been held, else nothing."""
            return DRAIN_RESERVE_S if held_vh and items else 0.0

        def final_drain() -> str | None:
            """Non-blocking flush attempts DRAIN_INTERVAL_S apart, for at most
            DRAIN_RESERVE_S and never past the deadline. Never waits on,
            polls inside, or breaks chain.lock: each attempt is one try.
            Returns the last flush's outcome."""
            stop_at = min(time.time() + DRAIN_RESERVE_S, t_end)
            n0, attempts, out = len(items), 0, None
            while items:
                out = flush()
                attempts += 1
                if out != "held":
                    break
                if stop_at - time.time() <= DRAIN_INTERVAL_S:
                    break
                _sleep(DRAIN_INTERVAL_S)
            print(f"gauntlet_stats: final drain, {attempts} attempt(s), "
                  f"{n0 - len(items)} of {n0} kept entr"
                  f"{'y' if n0 == 1 else 'ies'} chained", flush=True)
            return out

        # I5: each family's null is started only when the time left covers
        # the measured mean null (this run's, else the last run's, else the
        # gauntlet's prior). A family that does not fit is a deadline stop.
        null_times: list[float] = []

        def null_rate() -> float:
            return (sum(null_times) / len(null_times) if null_times
                    else prior_null_s or PBO_NULL_PRIOR_S)

        def null_fits() -> bool:
            return t_end - time.time() >= null_rate() + reserve_s()

        pbo_cache = PboCache(a.logs_dir, vintage, data_digest)
        for g, fam in family_by_group.items():
            todo = pending_by_group.get(g)
            if not todo:
                continue
            if time.time() >= t_end - reserve_s():
                status["stopped_at_deadline"] = True
                break
            # Part C (amendment-1): every family with a verdict needing stats
            # gets its null when measurable, passing verdict or not; the
            # result depends on the family alone, so it is cached.
            inputs_sha = pbo_inputs_sha(g, fam, train, a.pbo_null_draws)
            pbo = pbo_cache.get(g, inputs_sha)
            if pbo is not None:
                status["pbo_nulls_cached"] += pbo["pbo_null_draws"] > 0
            else:
                n_nulls = len(null_times)
                pbo = group_pbo(g, fam, train, True, a.pbo_null_draws,
                                null_fits=null_fits, null_times=null_times)
                if pbo is not None:
                    status["pbo_nulls_computed"] += len(null_times) - n_nulls
                    pbo_cache.put(g, inputs_sha, pbo)
            if null_times:
                status["pbo_null_mean_s"] = round(null_rate(), 3)
            if pbo is None:
                status["stopped_at_deadline"] = True
                print(f"DEADLINE: family {g}'s PBO null (~{null_rate():.0f}s) does "
                      f"not fit the time left; it and later families are statted "
                      f"by the next run.", flush=True)
                break
            grids = grids_by_group.get(g, {})
            for v in todo:
                sid = v["sid"]
                sibling = next(x for x in fam if x["sid"] == sid)
                ok, _ = qualifies(sibling, fam, grids)
                items.append((sid, v["vh"], verdict_stats(
                    sid, clustered["returns_by_id"][sid], train[sid],
                    train_sharpe[sid], clustered,
                    pbo, ok, vintage, data_digest, own_ends(sid),
                    floor, floor_hash)))
            # a full batch (kept entries from a held flush included): one
            # non-blocking attempt now, before the next family starts
            if len(items) >= STATS_BATCH_MAX:
                if flush() == "chain_refused":
                    return finish(1, "chain_refused")
        out = flush()
        if out == "chain_refused":
            return finish(1, "chain_refused")
        if out == "held" and final_drain() == "chain_refused":
            return finish(1, "chain_refused")
        if items:
            status["deferred_lock"] = len(items)
            print(f"chain.lock held through the run: {len(items)} computed stats "
                  f"entr{'y' if len(items) == 1 else 'ies'} not written; the next "
                  f"run recomputes them", flush=True)
            return finish(0, "deferred_lock")
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
