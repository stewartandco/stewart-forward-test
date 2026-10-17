"""Intraday rehearsal for Morpheus 2b-1: register 1d and 1h "twins" of real
crypto strategies on a COPY of the chain, run the real screen and gauntlet
worker on the copy, then read the outcomes back side by side.

It only writes to the paths it is given (the COPY registry, the twins file and
the report). `register` is the one command that appends to a chain, and it
REFUSES (exit 2, nothing written) a live chain: a --registry that resolves to
this layer's own `registry_log.jsonl`, or that sits in a directory which also
holds a `registry_log.d` (the live tree's segmented layout). Any other path is
trusted to be a scratch copy.

    python -m tools.intraday_rehearsal register --registry COPY \
        --assets BTCUSDT,ETHUSDT --originals 6 --seed 1 \
        [--timeframes 1d,1h] --out twins.json
    python -m tools.intraday_rehearsal report --registry COPY \
        --twins twins.json --artifacts-dir ART --out report.md
    python -m tools.intraday_rehearsal quarantine --registry COPY \
        --data-dir SCRATCH_DATA --twins twins.json --entered YYYY-MM-DD \
        [--n 2] --out report.md

`quarantine` (2b-2 design s7) is the recorder's end-to-end proof on the
COPY: it moves the first --n twins of the twins file's finest timeframe
(1h when it has any 1h twin, so a default 1d+1h file is unchanged; 1d for
a `--timeframes 1d` file, 2b-3 Switch 1) to `quarantine` BY HAND (screened,
gauntlet, a synthetic gauntlet `pass` with metrics {"rehearsal": true}, then
quarantine stamped <entered>T00:00:00Z) -- no twin passed the real gauntlet,
so the transition is made on the copy only and the report says so -- then
runs the real `pipeline.quarantine` --fetch-owed (a real Binance fetch into
--data-dir), --catch-up until "nothing owed" (at most 10 runs), --review and
`pipeline.livegate --dry-run`, with `verify_registry.py` before the appends
and after the whole run. It refuses the live chain exactly as `register`
does, and writes logs/ only beside the COPY.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import random
import re
import subprocess
import sys
import time
from pathlib import Path

LAYER = Path.cwd()
if str(LAYER) not in sys.path:
    sys.path.insert(0, str(LAYER))

from pipeline.common import content_id                      # noqa: E402
from pipeline.registry import Registry                      # noqa: E402

RUN_ID = "rehearsal-2b1"
LAYER_ROOT = Path(__file__).resolve().parent.parent


class LiveChainRefused(SystemExit):
    pass


def refuse_live_chain(registry: Path, what: str = "register twins") -> None:
    """Raise LiveChainRefused (exit code 2) for anything that looks live."""
    path = Path(registry).resolve()
    if path == (LAYER_ROOT / "registry_log.jsonl").resolve():
        why = f"it is this layer's own registry_log.jsonl ({LAYER_ROOT})"
    elif (path.parent / "registry_log.d").exists():
        why = f"{path.parent} also holds registry_log.d (the live layout)"
    else:
        return
    print(f"refusing to {what} on {path}: {why}. "
          f"Pass a scratch COPY of the chain.", file=sys.stderr)
    raise LiveChainRefused(2)


def refuse_live_data_dir(data_dir: Path, what: str) -> None:
    """Raise LiveChainRefused (exit code 2) for a data dir that looks live:
    this layer's own data/, or the data/ beside a directory holding
    registry_log.d. --fetch-owed writes *_1h.csv into the data dir, so a
    copied registry paired with the live data dir would still write into
    the live tree."""
    path = Path(data_dir).resolve()
    if path == (LAYER_ROOT / "data").resolve():
        why = f"it is this layer's own data/ ({LAYER_ROOT})"
    elif (path.parent / "registry_log.d").exists():
        why = f"{path.parent} also holds registry_log.d (the live layout)"
    else:
        return
    print(f"refusing to {what} with data dir {path}: {why}. "
          f"Pass a scratch data dir.", file=sys.stderr)
    raise LiveChainRefused(2)


TIMEFRAMES = ("1d", "1h")          # register's default (--timeframes 1d,1h)


def parse_timeframes(text: str) -> tuple[str, ...]:
    """`--timeframes` value: comma-separated, each a 24x7 timeframe
    `pipeline.bars` knows, no duplicates, at least one."""
    from pipeline.bars import BARS_PER_DAY_24X7
    tfs = tuple(x.strip() for x in text.split(",") if x.strip())
    bad = [t for t in tfs if t not in BARS_PER_DAY_24X7]
    if not tfs or bad or len(set(tfs)) != len(tfs):
        raise argparse.ArgumentTypeError(
            f"--timeframes {text!r}: want a comma list of distinct "
            f"{'/'.join(BARS_PER_DAY_24X7)}")
    return tfs


def _tf_order(tfs) -> list[str]:
    """Timeframes coarsest first (1d before 1h), unknown ones last."""
    from pipeline.bars import BARS_PER_DAY_24X7
    return sorted(set(tfs), key=lambda t: (BARS_PER_DAY_24X7.get(t, 10 ** 6), t))


def _kinds(spec: dict) -> set[str]:
    out = set()
    for b in spec.get("blocks", []):
        t = str(b.get("type", ""))
        if t == "vol_target":
            out.add("vol_target")
        elif t.startswith("vol_percentile"):
            out.add("vol_percentile")
    return out


def pick_originals(specs: dict[str, dict], n: int, seed: int) -> list[str]:
    """N crypto, version-2, 1d originals: one vol_target and one
    vol_percentile first (when the chain has them), then one per family in a
    seeded round-robin, then whatever is left."""
    rng = random.Random(seed)
    pool = sorted(s for s, p in specs.items()
                  if p.get("version") == 2
                  and p["universe"].get("asset_class", "crypto") == "crypto"
                  and p["universe"].get("timeframe", "1d") == "1d")
    chosen: list[str] = []
    for kind in ("vol_target", "vol_percentile"):
        hit = [s for s in pool if kind in _kinds(specs[s]) and s not in chosen]
        if hit and len(chosen) < n:
            chosen.append(rng.choice(hit))
    seen_fam = {specs[s].get("family") for s in chosen}
    rest = [s for s in pool if s not in chosen]
    rng.shuffle(rest)
    for s in list(rest):                       # one per new family first
        if len(chosen) >= n:
            break
        fam = specs[s].get("family")
        if fam not in seen_fam:
            chosen.append(s)
            seen_fam.add(fam)
    for s in rest:                             # then fill
        if len(chosen) >= n:
            break
        if s not in chosen:
            chosen.append(s)
    return chosen


def make_twin(orig: dict, asset: str, tf: str) -> dict:
    two = json.loads(json.dumps(orig))
    two["universe"] = dict(orig["universe"], assets=[asset], timeframe=tf,
                           session="24x7", asset_class="crypto")
    two["provenance"] = dict(
        orig["provenance"], run_id=RUN_ID,
        sibling_group_id=f"{RUN_ID}:{orig['strategy_id']}:{asset}_{tf}")
    two["strategy_id"] = None
    two["strategy_id"] = content_id(two, "strategy_id")
    return two


def register(registry: Path, assets: list[str], n: int, seed: int,
             out: Path, timeframes: tuple[str, ...] = TIMEFRAMES) -> dict:
    refuse_live_chain(registry)
    reg = Registry(registry)
    specs = {e["payload"]["strategy_id"]: e["payload"]
             for e in reg.entries() if e["entry_type"] == "strategy_registered"}
    originals = pick_originals(specs, n, seed)
    if len(originals) < n:
        raise SystemExit(f"only {len(originals)} eligible originals, wanted {n}")
    twins: dict[str, dict] = {}
    for sid in originals:
        for asset in assets:
            for tf in timeframes:
                two = make_twin(specs[sid], asset, tf)
                if two["strategy_id"] not in specs:
                    reg.register_strategy(two)
                twins[two["strategy_id"]] = {"original": sid, "asset": asset,
                                             "tf": tf}
    out.write_text(json.dumps(twins, indent=1, sort_keys=True), encoding="utf-8")
    return twins


def _num(v, nd=4):
    if v is None:
        return "-"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        return f"{v:.{nd}f}"
    return str(v)


def collect(registry: Path, artifacts_dir: Path, twins: dict) -> dict[str, dict]:
    """Per twin sid: screen outcome, gauntlet verdict + reason and the
    metrics, from the COPY's verdict entries and the artifact config.json."""
    reg = Registry(registry)
    state: dict[str, str] = {}
    reason: dict[str, str | None] = {}
    verdicts: dict[tuple[str, str], dict] = {}
    for e in reg.entries():
        p = e["payload"]
        sid = p.get("strategy_id")
        if sid not in twins:
            continue
        if e["entry_type"] == "strategy_registered":
            state[sid] = "proposed"
        elif e["entry_type"] == "state_change":
            state[sid] = p["to"]
            reason[sid] = p.get("reason")
        elif e["entry_type"] == "verdict":
            verdicts[(sid, p["stage"])] = p
    out = {}
    for sid in twins:
        scr = verdicts.get((sid, "screened"))
        gv = verdicts.get((sid, "gauntlet"))
        m = dict((gv or {}).get("metrics") or {})
        cfg = artifacts_dir / sid / "gauntlet" / "config.json"
        if cfg.exists():
            m = {**m, **(json.loads(cfg.read_text(encoding="utf-8"))
                         .get("metrics") or {})}
        out[sid] = {
            "state": state.get(sid, "unregistered"),
            "screen": scr["verdict"] if scr else "-",
            "screen_trades": ((scr or {}).get("metrics") or {}).get("trades"),
            "gauntlet": gv["verdict"] if gv else "-",
            "fail_reason": (m.get("fail_reason")
                            or (reason.get(sid) if gv and gv["verdict"] == "fail" else None)),
            "train_sharpe": m.get("train_sharpe"),
            "is_edge": m.get("is_edge_per_trade"),
            "oos_edge": m.get("oos_edge_per_trade"),
            "mc_p05": m.get("mc_p05_equity"),
            "p_ruin": m.get("p_ruin"),
        }
    return out


def render(twins: dict, rows: dict[str, dict]) -> str:
    groups: dict[tuple[str, str], dict[str, str]] = {}
    for sid, t in twins.items():
        groups.setdefault((t["original"], t["asset"]), {})[t["tf"]] = sid
    lines = ["# Intraday rehearsal report (2b-1)", "",
             f"{len(twins)} twins, {len(groups)} (original, asset) pairs.", "",
             "| original | asset | tf | state | screen (trades) | gauntlet | fail reason "
             "| train_sharpe | is edge/trade | oos edge/trade | mc_p05 | p_ruin | twin id |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for (orig, asset) in sorted(groups):
        for tf in _tf_order(t["tf"] for t in twins.values()):
            sid = groups[(orig, asset)].get(tf)
            if sid is None:
                continue
            r = rows[sid]
            lines.append(
                f"| {orig} | {asset} | {tf} | {r['state']} | "
                f"{r['screen']} ({_num(r['screen_trades'], 0)}) | {r['gauntlet']} | "
                f"{r['fail_reason'] or '-'} | {_num(r['train_sharpe'], 3)} | "
                f"{_num(r['is_edge'], 5)} | {_num(r['oos_edge'], 5)} | "
                f"{_num(r['mc_p05'], 3)} | {_num(r['p_ruin'], 4)} | {sid} |")
    return "\n".join(lines) + "\n"


# ---- quarantine: the 2b-2 end-to-end proof on a chain copy ----

MAX_CATCHUP_RUNS = 10
HAND_REASON = "rehearsal-2b2: hand transition on a chain COPY (design s7)"
SYNTHETIC_METRICS = {"rehearsal": True}
_FAIL_LINE = re.compile(r"^  line (\d+): ")
_GATE_LINE = re.compile(r"^(\S+)\s+days=\s*(\d+)\s+trades=\s*(\d+)\s")


class _Tee:
    """Echo to the real stream and keep a copy for the report. Deliberately
    not an io.TextIOBase: its finalizer would flush a stream already closed."""

    def __init__(self, echo):
        self.echo, self.buf = echo, io.StringIO()
        self.marks: list[tuple[float, str]] = []    # (clock, date) per catch-up date

    def write(self, s):
        if s.startswith("catch-up: --date "):
            self.marks.append((time.perf_counter(), s.split()[-1]))
        self.echo.write(s)
        self.buf.write(s)
        return len(s)

    def flush(self):
        self.echo.flush()


def _captured(fn, argv, date_secs: dict | None = None) -> tuple[int, str, float]:
    """(exit code, stdout then stderr text, seconds) of an in-process CLI.
    Given `date_secs`, the seconds each `catch-up: --date D` pass took (to
    the next date's line, or the end of the run) are added under D."""
    out, err = _Tee(sys.stdout), _Tee(sys.stderr)
    t0 = time.perf_counter()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = fn(argv)
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 1
    t1 = time.perf_counter()
    if date_secs is not None:
        ends = [t for t, _ in out.marks[1:]] + [t1]
        for (t, d), end in zip(out.marks, ends):
            date_secs[d] = date_secs.get(d, 0.0) + end - t
    return rc, out.buf.getvalue() + err.buf.getvalue(), t1 - t0


def verify_failures(registry: Path, data_dir: Path) -> tuple[int, list[str], str]:
    """(exit code, the `  line N: ...` failure lines, verdict line) of
    verify_registry.py on the copy, with the copy's own data dir."""
    r = subprocess.run([sys.executable, str(LAYER_ROOT / "verify_registry.py"),
                        str(registry), "--data-dir", str(data_dir)],
                       capture_output=True, text=True, cwd=str(LAYER_ROOT),
                       encoding="utf-8", errors="replace")
    lines = (r.stdout + r.stderr).splitlines()
    fails = [ln for ln in lines if _FAIL_LINE.match(ln)]
    verdict = next((ln.strip() for ln in lines if "REGISTRY " in ln),
                   "(no verdict line)")
    return r.returncode, fails, verdict


def _n_lines(path: Path) -> int:
    with Path(path).open("rb") as f:
        return sum(1 for _ in f)


def hand_quarantine(registry: Path, sids: list[str], entered: str) -> list[int]:
    """The hand transition, on the COPY: screened -> gauntlet -> synthetic
    pass -> quarantine at <entered>T00:00:00Z. Returns the 1-based chain line
    of each synthetic verdict (the only lines a protocol invariant may flag
    as the known artefact of this step)."""
    reg = Registry(registry)
    synthetic = []
    for sid in sids:
        reg.record_state_change(sid, "screened", HAND_REASON)
        reg.record_state_change(sid, "gauntlet", HAND_REASON)
        synthetic.append(_n_lines(registry) + 1)
        reg.record_verdict(sid, "gauntlet", "pass", dict(SYNTHETIC_METRICS),
                           "0" * 64)
        reg.record_state_change(sid, "quarantine", HAND_REASON,
                                ts_utc=f"{entered}T00:00:00Z")
    return synthetic


def _sid_blocks(text: str, sids: list[str]) -> list[str]:
    """The lines of a --review / livegate printout that belong to `sids`: a
    line starting with one of them, plus the indented lines under it."""
    out, keep = [], False
    for line in text.splitlines():
        if any(line.startswith(s) for s in sids):
            keep = True
            out.append(line)
        elif keep and line.startswith("  "):
            out.append(line)
        else:
            keep = False
    return out


def _record_rows(registry: Path, sids: list[str]) -> dict[str, list[dict]]:
    rows: dict[str, list[dict]] = {s: [] for s in sids}
    for e in Registry(registry).entries():
        if (e["entry_type"] == "quarantine_decision"
                and e["payload"]["strategy_id"] in rows):
            rows[e["payload"]["strategy_id"]].append(e["payload"])
    return rows


def _n_entries(rows: list[dict]) -> int:
    """Entries across rows: an intraday row carries `entries`; a daily row
    records at most one event, so action 'enter' counts one."""
    return sum(r["entries"] if "entries" in r
               else (1 if r.get("action") == "enter" else 0) for r in rows)


def _decisions(registry: Path) -> list[dict]:
    return [e["payload"] for e in Registry(registry).entries()
            if e["entry_type"] == "quarantine_decision"]


def _n_decisions(registry: Path) -> int:
    return len(_decisions(registry))


def quarantine(registry: Path, data_dir: Path, twins_path: Path, entered: str,
               n: int, out: Path) -> int:
    """The s7 proof on a COPY. Exit 0 = every proof criterion held, 1 = one
    failed (the report says which), 3 = BLOCKED (a fetch failed: no catch-up
    is attempted, the report names the failed cells)."""
    refuse_live_chain(registry, "run the quarantine rehearsal")
    refuse_live_data_dir(data_dir, "run the quarantine rehearsal")
    from pipeline import quarantine as q
    from pipeline import livegate, degraded
    from pipeline.registry import parse_iso_date
    parse_iso_date(entered, "--entered")
    registry, data_dir = Path(registry), Path(data_dir)
    if not data_dir.is_dir():
        raise SystemExit(f"no scratch data dir at {data_dir}")
    twins = json.loads(Path(twins_path).read_text(encoding="utf-8"))
    tfs = _tf_order(t["tf"] for t in twins.values())
    tf = "1h" if "1h" in tfs else (tfs[-1] if tfs else "1h")
    picked = [s for s, t in twins.items() if t["tf"] == tf][:n]
    if len(picked) < n:
        raise SystemExit(f"only {len(picked)} {tf} twins in {twins_path}, wanted {n}")
    states = Registry(registry).strategy_states()
    wrong = {s: states.get(s, "unregistered") for s in picked
             if states.get(s) != "proposed"}
    if wrong:
        raise SystemExit(f"every picked twin must be 'proposed' on the copy "
                         f"(a fresh copy + register); found {wrong}")
    specs = {e["payload"]["strategy_id"]: e["payload"]
             for e in Registry(registry).entries()
             if e["entry_type"] == "strategy_registered"}

    base = ["--registry", str(registry), "--data-dir", str(data_dir),
            "--artifacts-dir", str(registry.parent / "artifacts")]
    log: list[str] = []
    catchup_rcs: list[int] = []
    nothing_owed = False

    print("verify BEFORE the hand transition ...", flush=True)
    v_before = verify_failures(registry, data_dir)
    synthetic = hand_quarantine(registry, picked, entered)
    print(f"hand transition: {len(picked)} twin(s) -> quarantine at {entered}",
          flush=True)

    rc, text, secs = _captured(q.run, base + ["--fetch-owed"])
    log.append(f"$ quarantine --fetch-owed  (rc {rc}, {secs:.1f}s)\n{text}")
    ledger, _ = degraded.read_items(registry.parent / "logs"
                                    / degraded.QUARANTINE_LEDGER)
    blocked = [i for i in ledger if i.get("source") == q.FETCH_SOURCE]

    decisions_before = _n_decisions(registry)
    catchup_secs = 0.0
    date_secs: dict[str, float] = {}
    if not blocked:
        for k in range(MAX_CATCHUP_RUNS):
            rc, text, secs = _captured(q.run, base + ["--catch-up"], date_secs)
            catchup_secs += secs
            catchup_rcs.append(rc)
            log.append(f"$ quarantine --catch-up #{k + 1}  (rc {rc}, "
                       f"{secs:.1f}s)\n{text}")
            if "catch-up: nothing owed" in text:
                nothing_owed = True
                break
            if rc != 0:
                break
    new_rows = _decisions(registry)[decisions_before:]
    strategy_days = len(new_rows)
    picked_set = set(picked)
    twin_by_date: dict[str, int] = {}
    other_by_date: dict[str, int] = {}
    for r in new_rows:
        book = twin_by_date if r["strategy_id"] in picked_set else other_by_date
        book[r["date"]] = book.get(r["date"], 0) + 1
    only_twins = sorted(d for d in twin_by_date if d not in other_by_date)
    only_secs = sum(date_secs.get(d, 0.0) for d in only_twins)
    only_rows = sum(twin_by_date[d] for d in only_twins)

    rc_rev, review_text, _ = _captured(q.run, base + ["--review"])
    rc_gate, gate_text, _ = _captured(livegate.run, [
        "--registry", str(registry), "--data-dir", str(data_dir), "--dry-run"])
    print("verify AFTER the rehearsal ...", flush=True)
    v_after = verify_failures(registry, data_dir)

    # what each picked twin owed: every complete day in (entered, data end]
    rows = _record_rows(registry, picked)
    owed: dict[str, list[str]] = {}
    for sid in picked:
        last = q._last_bar_date(data_dir, specs[sid])
        by_date = (q._owed_by_date(data_dir, specs[sid], entered, last)
                   if last else None)
        owed[sid] = sorted(by_date or {})
    missing = {s: sorted(set(owed[s]) - {r["date"] for r in rows[s]})
               for s in picked}

    before_set = set(v_before[1])
    new_fails = [ln for ln in v_after[1] if ln not in before_set]
    synth_new = [ln for ln in new_fails
                 if int(_FAIL_LINE.match(ln).group(1)) in synthetic]
    other_new = [ln for ln in new_fails if ln not in synth_new]
    per_line: dict[str, list[str]] = {}
    for ln in synth_new:
        per_line.setdefault(_FAIL_LINE.match(ln).group(1), []).append(ln)

    review_blocks = {s: "\n".join(_sid_blocks(review_text, [s])) for s in picked}
    review_ok = rc_rev == 0 and all(
        "unrecorded bar dates in window: 0" in review_blocks[s]
        and "partially recorded dates: 0" in review_blocks[s] for s in picked)
    gate_lines = _sid_blocks(gate_text, picked)
    # the gate must READ the record: its days and trades per twin equal the
    # chained dates and its closed trades (summed exits on intraday rows,
    # action 'exit' on daily rows: livegate.closed_forward_trades), not
    # merely print a line
    gate_expect = {s: (len({r["date"] for r in rows[s]}),
                       livegate.closed_forward_trades(rows[s])) for s in picked}
    gate_seen: dict[str, tuple[int, int]] = {}
    for ln in gate_lines:
        m = _GATE_LINE.match(ln)
        if m and m.group(1) in gate_expect:
            gate_seen[m.group(1)] = (int(m.group(2)), int(m.group(3)))
    gate_ok = rc_gate == 0 and all(gate_seen.get(s) == gate_expect[s]
                                   for s in picked)
    # a verify that crashed, printed nothing or said anything but VALID is
    # not evidence: both runs must exit 0 with the VALID verdict
    verify_ok = all(v[0] == 0 and "REGISTRY VALID" in v[2]
                    for v in (v_before, v_after))
    ok = (not blocked and nothing_owed and all(owed.values())
          and not any(missing.values()) and review_ok and gate_ok
          and verify_ok
          and not other_new and all(len(v) <= 1 for v in per_line.values()))
    status = "BLOCKED" if blocked else ("PASS" if ok else "FAIL")

    L = ["# Intraday paper-stage rehearsal (2b-2, design s7)", "",
         f"Status: **{status}**", "",
         f"Chain COPY `{registry}`; scratch data `{data_dir}`; logs beside the copy.", "",
         f"{len(picked)} {tf} twin(s) from `{twins_path}` moved to `quarantine` BY "
         f"HAND on the copy (screened, gauntlet, a synthetic gauntlet `pass` with "
         f"metrics `{json.dumps(SYNTHETIC_METRICS)}` and artifacts hash 64 zeros, "
         f"then quarantine at `{entered}T00:00:00Z`): no twin passed the real "
         f"gauntlet, so this transition is the rehearsal's own, never a recorder "
         f"output.", ""]
    L += ["## Checks", "",
          f"- fetch succeeded: {not blocked}",
          f"- catch-up reached nothing owed: {nothing_owed}",
          f"- every owed day recorded: {all(owed.values()) and not any(missing.values())}",
          f"- --review shows no gap: {review_ok}",
          f"- live gate read the record (days, trades) expected "
          f"{gate_expect} seen {gate_seen}: {gate_ok}",
          f"- verify BEFORE and AFTER exit 0 and REGISTRY VALID: {verify_ok}",
          f"- no unexpected new verify failure: {not other_new}", ""]
    if blocked:
        L += ["## BLOCKED: the fetch failed", ""]
        L += [f"- `{i['key']}`: {i['reason']}" for i in blocked] + [""]
    L += ["## Rows per strategy", "",
          "| strategy | original | cell | owed days | days | entries | exits "
          "| first | last | missing |",
          "|---|---|---|---:|---:|---:|---:|---|---|---|"]
    for sid in picked:
        r = rows[sid]
        dates = sorted({x["date"] for x in r})
        L.append(f"| `{sid}` | `{twins[sid]['original']}` | "
                 f"{', '.join(sorted({x['asset'] for x in r})) or '-'} | "
                 f"{len(owed[sid])} | {len(dates)} | "
                 f"{_n_entries(r)} | "
                 f"{livegate.closed_forward_trades(r)} | "
                 f"{dates[0] if dates else '-'} | {dates[-1] if dates else '-'} | "
                 f"{', '.join(missing[sid]) or 'none'} |")
    L += ["", "Recorded dates per strategy:", ""]
    for sid in picked:
        L.append(f"- `{sid}`: "
                 + (", ".join(sorted({x['date'] for x in rows[sid]})) or "none"))
    per_day = (f"{catchup_secs / strategy_days:.1f}" if strategy_days else "n/a")
    L += ["", "## Timing", "",
          f"- catch-up runs: {len(catchup_rcs)} (exit codes {catchup_rcs}); "
          f"reached `catch-up: nothing owed`: {nothing_owed}",
          f"- catch-up wall time {catchup_secs:.1f}s for {strategy_days} "
          f"strategy-day(s) chained (every quarantined strategy on the copy)",
          f"- seconds per strategy-day: {per_day} (wall clock including chain "
          f"reads and snapshots, so an upper bound)",
          f"- {tf} only: {len(only_twins)} date(s) on which ONLY the picked {tf} "
          f"twins recorded took {only_secs:.1f}s for {only_rows} {tf} "
          f"strategy-day(s) = "
          + (f"{only_secs / only_rows:.1f}" if only_rows else "n/a")
          + f" seconds per {tf} strategy-day (per-date pass time, chain reads "
          "included)",
          f"- rows chained by OTHER quarantined strategies on the copy: "
          f"{sum(other_by_date.values())} over {len(other_by_date)} date(s) "
          f"({', '.join(f'{d}: {n}' for d, n in sorted(other_by_date.items())) or 'none'})",
          "", "Per catch-up date (seconds, picked-twin rows, other rows):", "",
          *[f"- {d}: {date_secs[d]:.1f}s, {twin_by_date.get(d, 0)}, "
            f"{other_by_date.get(d, 0)}" for d in sorted(date_secs)], "",
          f"## --review (the picked twins, exit {rc_rev})", "", "```",
          *_sid_blocks(review_text, picked), "```", "",
          f"## Live gate (`--dry-run`, exit {rc_gate})", "", "```",
          *(gate_lines or [gate_text.strip()[-2000:]]), "```", "",
          "## verify_registry.py", "",
          f"verify BEFORE (exit {v_before[0]}, {v_before[2]}): "
          f"{len(v_before[1])} failure line(s)", "",
          "```", *(v_before[1] or ["(none)"]), "```", "",
          f"verify AFTER (exit {v_after[0]}, {v_after[2]}): "
          f"{len(v_after[1])} failure line(s)", "",
          "```", *(v_after[1] or ["(none)"]), "```", ""]
    if not new_fails:
        L.append("The rehearsal added no new failure line.")
    else:
        if synth_new:
            L.append("Known artefact of the hand transition (the synthetic "
                     f"verdict, lines {sorted(int(k) for k in per_line)}), not a "
                     "recorder defect:")
            L += [f"- `{ln.strip()}`" for ln in synth_new]
        if other_new:
            L.append("UNEXPECTED new failure lines:")
            L += [f"- `{ln.strip()}`" for ln in other_new]
        else:
            L.append("Apart from that artefact the rehearsal added no new "
                     "failure line.")
    L += ["", "## Console log", "", "```", *[x.rstrip() for x in log], "```", ""]
    Path(out).write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {out}: {status}")
    if blocked:
        return 3
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.intraday_rehearsal")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("register")
    r.add_argument("--registry", type=Path, required=True)
    r.add_argument("--assets", required=True)
    r.add_argument("--originals", type=int, default=6)
    r.add_argument("--seed", type=int, default=20261010)
    r.add_argument("--timeframes", type=parse_timeframes, default=TIMEFRAMES,
                   help="comma list of twin timeframes (default 1d,1h)")
    r.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("report")
    p.add_argument("--registry", type=Path, required=True)
    p.add_argument("--twins", type=Path, required=True)
    p.add_argument("--artifacts-dir", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    qp = sub.add_parser("quarantine")
    qp.add_argument("--registry", type=Path, required=True)
    qp.add_argument("--data-dir", type=Path, required=True)
    qp.add_argument("--twins", type=Path, required=True)
    qp.add_argument("--entered", required=True)
    qp.add_argument("--n", type=int, default=2)
    qp.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    if a.cmd == "quarantine":
        return quarantine(a.registry, a.data_dir, a.twins, a.entered, a.n, a.out)
    if a.cmd == "register":
        twins = register(a.registry, [x for x in a.assets.split(",") if x],
                         a.originals, a.seed, a.out, a.timeframes)
        print(f"registered {len(twins)} twins of "
              f"{len({t['original'] for t in twins.values()})} originals -> {a.out}")
        return 0
    twins = json.loads(a.twins.read_text(encoding="utf-8"))
    rows = collect(a.registry, a.artifacts_dir, twins)
    a.out.write_text(render(twins, rows), encoding="utf-8")
    print(f"wrote {a.out}: {len(twins)} twins")
    return 0


if __name__ == "__main__":
    sys.exit(main())
