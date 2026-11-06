"""Command-line tool for the off-chain crypto survivorship study
(docs/2026-11-06-crypto-survivorship-study-design.md). `python -m
tools.survivorship <cmd>` from research-layer/.

THE ORDER IS ENFORCED IN CODE (design s4). Step 1 (fetch-cmc, plan-fetch,
fetch, universe, preflight, freeze, draft-note) reads data or reproduces
numbers already on the chain: preflight's only engine calls are train runs of
registered specs on their OWN cells (P2). Step 3 (run, gauntlet, analyse) calls
`require_note` before any engine call or result read: no outcome number is
computed until a `survivorship-study-v1: ` note is on the chain COPY, after
the frozen registry prefix, carrying every manifest hash, and the manifest
still matches the inputs on disk.

Exit codes: 0 ok, 1 a check failed, 2 refused, 3 stopped at the run window
(run/gauntlet only; resume later). Every network call goes through an
injectable `fetch(url) -> bytes`. The tool never opens a live chain
(`--registry` passes refuse_live first) and never writes research-layer/data/.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

from pipeline import parallel
from pipeline.gauntlet_core import prior_s_for
from pipeline.screen import screen_prior_s_for
from tools import survivorship_archive as sa
from tools import survivorship_checks as sc
from tools import survivorship_harness as h
from tools import survivorship_stats as ss
from tools import survivorship_templates as st
from tools import survivorship_universe as su

LAYER = Path(__file__).resolve().parent.parent
DESIGN_REL = "docs/2026-11-06-crypto-survivorship-study-design.md"
NOTE_REL = "docs/notes/survivorship-study-v1.md"
RESULT_REL = "docs/notes/survivorship-study-v1-result.md"
NOTE_PREFIX = "survivorship-study-v1: "
RESULT_PREFIX = "survivorship-study-v1-result: "
CLARIFICATION = "Where G >= 0.10, L <= 0 and U < 0.10, the verdict is NOT MATERIAL."
CMC_FILE = "cmc_20231231.json"
CANON_RULE = 'json.dumps(obj, sort_keys=True, separators=(",", ":"))'
FINDINGS_CAP = 50

OK, FAIL, REFUSED, STOPPED = 0, 1, 2, 3


class Refused(Exception):
    """A precondition of the study's order is not met: exit 2, nothing run."""


class NoteMissing(Refused):
    """No chained pre-registration note matches the manifest."""


# ------------------------------------------------------------------ helpers

def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_canon(obj) -> str:
    return _sha(canon(obj).encode("utf-8"))


def _load_json(path: Path, hint: str):
    if not path.exists():
        raise Refused(f"{path} is missing ({hint})")
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(obj, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def _write_lf(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    if b"\r" in data:
        raise Refused(f"{path}: text holds a CR byte; notes are LF only")
    path.write_bytes(data)


def _cap(findings: list[str]) -> list[str]:
    if len(findings) <= FINDINGS_CAP:
        return findings
    return findings[:FINDINGS_CAP] + [f"... {len(findings) - FINDINGS_CAP} more"]


def _now(args) -> datetime:
    if getattr(args, "now", None):
        t = datetime.fromisoformat(args.now)
        if t.tzinfo is None:
            t = t.replace(tzinfo=h.CLOCK)
        return t.astimezone(h.CLOCK)
    return datetime.now(h.CLOCK)


def _require_window(args) -> None:
    """Downloads and engine runs START only inside [08:40, 20:00) Perth."""
    t = _now(args)
    if not (h.WINDOW_START <= t.time() < h.WINDOW_END):
        raise Refused(f"outside the run window [08:40, 20:00) Australia/Perth "
                      f"(now {t.isoformat()}); start inside it")


def _now_fn(args):
    if getattr(args, "now", None):
        fixed = _now(args)
        return lambda: fixed
    return h._now


def _study(args) -> Path:
    s = Path(args.study_dir).resolve()
    layers = {LAYER.resolve()}
    if getattr(args, "layer_dir", None):
        layers.add(Path(args.layer_dir).resolve())
    for layer in layers:
        if s == layer:
            raise Refused(f"--study-dir {s} is the research layer itself")
        for sub in ("data", "registry_log.d", "logs", "artifacts"):
            p = layer / sub
            if s == p or p in s.parents:
                raise Refused(f"--study-dir {s} is inside {p}")
    if (s / "registry_log.d").exists() or (s / "registry_log.jsonl").exists():
        raise Refused(f"--study-dir {s} holds a chain (registry_log.*): live layout")
    s.mkdir(parents=True, exist_ok=True)
    return s


def _refuse_live(args) -> Path:
    reg = Path(args.registry)
    st.refuse_live(reg)
    if not reg.exists():
        raise Refused(f"--registry {reg} does not exist")
    return reg


def _layer(args) -> Path:
    return Path(args.layer_dir).resolve() if getattr(args, "layer_dir", None) else LAYER


def _require_cmc(study: Path) -> Path:
    p = study / CMC_FILE
    if not p.exists():
        raise Refused(f"{p} is missing: run fetch-cmc first")
    return p


def _load_universe(study: Path) -> dict:
    return _load_json(study / "universe.json", "run universe first")


def _pairs(u: dict) -> list[str]:
    seen = set(u["A"])
    return list(u["A"]) + [p for p in u["B"] if p not in seen]


def _archive_bars(study: Path, sym: str, tf: str, months=None) -> list[dict]:
    """Every parsed bar of a pair's downloaded monthly zips, sorted by date.
    With `months`, each listed month's zip must be on disk (Refused if not)."""
    folder = study / "archive" / sym / tf
    if months is not None:
        paths = []
        for m in months:
            p = folder / f"{sym}-{tf}-{m}.zip"
            if not p.exists():
                raise Refused(f"{p} is missing: fetch is incomplete, run fetch")
            paths.append(p)
    else:
        paths = sorted(folder.glob(f"{sym}-{tf}-*.zip")) if folder.is_dir() else []
    bars: list[dict] = []
    for p in paths:
        bars.extend(sa.parse_zip(p))
    bars.sort(key=lambda b: b["date"])
    return bars


def _read_pipeline(path: Path):
    """Pipeline CSV bars (read-only), or None when the file does not exist."""
    if not path.exists():
        return None
    out = []
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            out.append({"date": row["date"], "open": float(row["open"]),
                        "high": float(row["high"]), "low": float(row["low"]),
                        "close": float(row["close"]), "volume": float(row["volume"])})
    return out


def _refuse_none_declared() -> None:
    bad = []
    for k, v in su.RENAMES.items():
        if any(x is None for x in v):
            bad.append(f"RENAMES[{k}] = {v!r}")
    bad += [f"DELISTED[{k}] = None" for k, v in su.DELISTED.items() if v is None]
    bad += [f"CUTS[{k}] = None" for k, v in su.CUTS.items() if v is None]
    if bad:
        raise Refused("declared facts not filled (confirm from Binance announcements "
                      "first, design P3/P4): " + "; ".join(bad))


def _declared() -> dict:
    return {"renames": {k: list(v) for k, v in su.RENAMES.items()},
            "delisted": dict(su.DELISTED), "cuts": dict(su.CUTS)}


def _template_list(copy) -> dict:
    tfs = sorted({s["universe"].get("timeframe", "1d") for s in copy.specs.values()})
    return {tf: st.templates(copy, tf) for tf in tfs}


def _input_hashes(study: Path, copy) -> dict:
    """The P5 hashes of the inputs on disk now (canonical JSON; CMC raw bytes)."""
    u = _load_universe(study)
    pf = _load_json(study / "preflight_1d.json", "run preflight --tf 1d first")
    d = _declared()
    return {
        "templates": _sha_canon(_template_list(copy)),
        "cells_A": _sha_canon(u["A"]),
        "cells_B": _sha_canon(u["B"]),
        "cmc": _sha(_require_cmc(study).read_bytes()),
        "renames": _sha_canon(d["renames"]),
        "delisted": _sha_canon(d["delisted"]),
        "cuts": _sha_canon(d["cuts"]),
        "first_bars": _sha_canon(pf["first_bars"]),
        "data_1d": _sha_canon(pf["data_sha256"]),
    }


def _verify_inputs(study: Path, copy, manifest: dict) -> None:
    now = _input_hashes(study, copy)
    want = manifest.get("hashes", {})
    diff = sorted(k for k in set(now) | set(want) if now.get(k) != want.get(k))
    if diff:
        raise Refused("the inputs on disk no longer match manifest.json (" +
                      ", ".join(diff) + "); a frozen input changed after freeze. "
                      "After the note, only a superseding chained note may change one")


def require_note(copy, manifest: dict) -> int:
    """The gate (design s4 step 3). Returns the entry index of the chained
    pre-registration note; raises NoteMissing unless a note in copy.notes
    starts with NOTE_PREFIX, holds every manifest hash, and sits after the
    frozen registry prefix. `upto` is the first EXCLUDED entry index (the
    templates are the registrations at entries 0..upto-1), so a note at
    index >= upto comes after every frozen registration."""
    want = list(manifest["hashes"].values())
    upto = int(manifest["upto"])
    why = []
    for idx, text in copy.notes:
        if not text.startswith(NOTE_PREFIX):
            continue
        missing = [x for x in want if x not in text]
        if missing:
            why.append(f"note at entry {idx} lacks {len(missing)} manifest hash(es)")
            continue
        if idx < upto:
            why.append(f"note at entry {idx} sits inside the frozen registry "
                       f"prefix (upto {upto})")
            continue
        return idx
    raise NoteMissing(
        f"no chained note starting {NOTE_PREFIX!r} matches manifest.json"
        + (": " + "; ".join(why) if why else "") +
        ". Chain the Coen-approved draft (design s4 step 2), re-copy the chain, "
        "then run again")


def _gate(args, study: Path, tf: str):
    """Everything run/gauntlet/analyse check before any engine call or result
    read. refuse_live has already run."""
    manifest = _load_json(study / "manifest.json", "run freeze first")
    copy = st.read_copy(Path(args.registry), int(manifest["upto"]))
    note_idx = require_note(copy, manifest)
    _verify_inputs(study, copy, manifest)
    pf = _load_json(study / f"preflight_{tf}.json", f"run preflight --tf {tf} first")
    if pf.get("passed") is not True:
        raise Refused(f"preflight_{tf}.json did not pass: a failed precondition "
                      f"stops the {tf} arm")
    if pf.get("upto") != manifest["upto"]:
        raise Refused(f"preflight_{tf}.json ran at upto {pf.get('upto')}, the "
                      f"manifest froze upto {manifest['upto']}")
    data_dir = study / f"data_{tf}"
    for pair, want in pf["data_sha256"].items():
        f = data_dir / f"{pair}_{tf}.csv"
        if not f.exists() or _sha(f.read_bytes()) != want:
            raise Refused(f"{f} differs from what preflight built and checked")
    return copy, manifest, note_idx, pf


def _rows(study: Path, copy, tf: str):
    u = _load_universe(study)
    pairs = _pairs(u)
    tmpl = st.templates(copy, tf)
    if not tmpl:
        raise Refused(f"no frozen templates at {tf}")
    data_dir = str(study / f"data_{tf}")
    rows = []
    for tkey in sorted(tmpl):
        for pair in pairs:
            spec = st.cell_spec(copy, tkey, tmpl[tkey], pair, tf)
            rows.append({"key": f"{tkey}|{pair}|{tf}", "stratum": u["strata"][pair],
                         "tkey": tkey, "pair": pair, "tf": tf,
                         "item": (spec, data_dir, tf)})
    return rows, tmpl, pairs, u


def _read_jsonl(path: Path):
    out, dups = {}, []
    if not path.exists():
        return out, dups
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
            k = r["key"]
        except (ValueError, KeyError, TypeError):
            continue                       # a torn tail; run_arm recomputes it
        if k in out:
            dups.append(k)
        out[k] = r
    return out, dups


def _arm_exit(summary: dict, n: int, label: str) -> int:
    s = dict(summary)
    s["rows"] = n
    s["n_errors"] = len(s["errors"])
    s["errors"] = s["errors"][:FINDINGS_CAP]
    print(f"{label}: " + json.dumps(s, sort_keys=True))
    if summary["errors"]:
        return FAIL
    if summary["stopped_at_window"]:
        print(f"{label}: stopped at the run window; resume inside 08:40-20:00 Perth")
        return STOPPED
    if summary["done"] + summary["skipped"] != n:
        return FAIL
    return OK


def _result_note_for(tf: str) -> bool:
    """Only the decision arm (1d, SV4) drafts the result note."""
    return tf == "1d"


# ---------------------------------------------------------------- commands

def cmd_fetch_cmc(args, fetch) -> int:
    study = _study(args)
    _require_window(args)
    out = study / CMC_FILE
    if out.exists():
        raise Refused(f"{out} exists: the CMC snapshot is a frozen input; "
                      f"delete it by hand to re-fetch")
    data = fetch(su.CMC_URL)
    tmp = out.with_name(out.name + ".part")
    tmp.write_bytes(data)
    try:
        rows = su.load_cmc(tmp)
    except Exception as exc:          # noqa: BLE001 - any parse failure
        tmp.unlink()
        print(f"fetch-cmc: FAIL, response is not a CMC listing: "
              f"{type(exc).__name__}: {exc}")
        return FAIL
    tmp.replace(out)
    print(f"fetch-cmc: wrote {out} ({len(data)} bytes, {len(rows)} rows, "
          f"sha256 {_sha(data)})")
    return OK


def _plan_symbols(study: Path, tf: str) -> list[str]:
    cmc_path = _require_cmc(study)
    if tf == "1d":
        syms = [su.pair_for(r["symbol"]) for r in su.load_cmc(cmc_path)]
        syms += [v[0] for v in su.RENAMES.values()]
        syms += list(su.declared_assets())
    else:
        pairs = _pairs(_load_universe(study))
        syms = pairs + [su.RENAMES[p][0] for p in pairs if p in su.RENAMES]
    return list(dict.fromkeys(syms))


def cmd_plan_fetch(args, fetch) -> int:
    study = _study(args)
    syms = _plan_symbols(study, args.tf)
    plan = sa.plan_downloads(syms, [args.tf], fetch)
    summary = sa.summarise(plan)
    with_files = {s for s, _, _ in plan}
    _write_json(study / f"plan_{args.tf}.json", {
        "tf": args.tf, "symbols": syms, "files": [list(t) for t in plan],
        "sizes": [list(t) + [plan.sizes[t]] for t in plan]})
    summary["symbols"] = len(syms)
    summary["symbols_without_files"] = len(syms) - len(with_files)
    print("plan-fetch: " + json.dumps(summary, sort_keys=True))
    print(f"plan-fetch: nothing downloaded; wrote {study / f'plan_{args.tf}.json'}")
    return OK


def _have(dest: Path, sym: str, tf: str, month: str) -> bool:
    z = dest / sym / tf / f"{sym}-{tf}-{month}.zip"
    c = z.with_name(z.name + ".CHECKSUM")
    if not (z.exists() and c.exists()):
        return False
    want = c.read_text(encoding="utf-8").split()
    return bool(want) and want[0].lower() == _sha(z.read_bytes())


def cmd_fetch(args, fetch) -> int:
    study = _study(args)
    _require_window(args)
    plan = _load_json(study / f"plan_{args.tf}.json", f"run plan-fetch --tf {args.tf} first")
    dest = study / "archive"
    new = have = 0
    errors = []
    for sym, tf, month in plan["files"]:
        if _have(dest, sym, tf, month):
            have += 1
            continue
        try:
            sa.download_month(sym, tf, month, dest, fetch)
            new += 1
        except sa.ChecksumMismatch as exc:
            errors.append(str(exc))
        except Exception as exc:      # noqa: BLE001 - network/disk: report, go on
            errors.append(f"{sym} {tf} {month}: {type(exc).__name__}: {exc}")
    print(f"fetch: {new} downloaded, {have} already present, {len(errors)} failed")
    for e in errors[:FINDINGS_CAP]:
        print(f"fetch: FAIL {e}")
    return FAIL if errors else OK


def cmd_universe(args, fetch) -> int:
    study = _study(args)
    cmc_path = _require_cmc(study)
    plan = _load_json(study / "plan_1d.json", "run plan-fetch --tf 1d first")
    months: dict[str, list[str]] = {}
    for sym, tf, month in plan["files"]:
        months.setdefault(sym, []).append(month)
    spans = {}
    for sym in plan["symbols"]:
        if not months.get(sym):
            continue                   # no Binance pair in the archive
        bars = _archive_bars(study, sym, "1d", months=months[sym])
        if bars:
            spans[sym] = (bars[0]["date"][:10], bars[-1]["date"][:10])
    cmc_bytes = cmc_path.read_bytes()
    cmc = su.load_cmc(cmc_path)
    try:
        B, excluded = su.pit_walk(cmc, spans, target=su.TARGET, cuts=su.CUTS)
    except RuntimeError as exc:
        print(f"universe: FAIL {exc}")
        return FAIL
    A = list(su.declared_assets())
    strata = su.strata(A, B)
    sizes = {s: sum(1 for v in strata.values() if v == s) for s in su.STRATA}
    _write_json(study / "universe.json", {
        "A": A, "B": B, "strata": strata, "excluded": excluded,
        "target": su.TARGET, "cmc_sha256": _sha(cmc_bytes),
        "spans": {k: list(v) for k, v in sorted(spans.items())},
        "stratum_sizes": sizes})
    print(f"universe: A {len(A)}, B {len(B)} (target {su.TARGET}), strata "
          + " ".join(f"{s} {sizes[s]}" for s in su.STRATA)
          + f", excluded {len(excluded)}")
    if len(B) != su.TARGET:
        print(f"universe: FAIL the walk admitted {len(B)}, not {su.TARGET}")
        return FAIL
    return OK


def _build_pair(study: Path, pipe_dir: Path, tf: str, pair: str, in_a: bool,
                checks: dict) -> dict | None:
    """Builds one cell's study CSV (cuts, splice, first-bar rule, END) and
    records its P1/P3/P4 findings. Data only: no engine call."""
    bars = _archive_bars(study, pair, tf)
    if not bars:
        checks["build"][pair] = ["no archive bars"]
        return None
    raw = bars
    if pair in su.CUTS:
        bars = sc.apply_cut(bars, su.CUTS[pair])
    if pair in su.RENAMES:
        succ, swap, ratio, _url = su.RENAMES[pair]
        try:
            bars = sc.splice(bars, _archive_bars(study, succ, tf), swap, ratio)
        except sc.SpliceBreak as exc:
            checks["P3"][pair] = [str(exc)]
            return None
    if in_a:
        pbars = _read_pipeline(pipe_dir / f"{pair}_{tf}.csv")
        if not pbars:
            checks["P1"][pair] = [f"pipeline file {pair}_{tf}.csv missing or empty"]
            return None
        found = sc.parity(raw, pbars)
        if found:
            checks["P1"][pair] = _cap(found)
        first = sc.first_bar(bars[0]["date"][:10], pbars[0]["date"][:10])
        bars = sc.apply_cut(bars, first)
    bars = [b for b in bars if b["date"][:10] <= su.END]
    if not bars:
        checks["build"][pair] = [f"no bars left after the first-bar rule and END {su.END}"]
        return None
    flags = []
    dates = [b["date"] for b in bars]
    if len(dates) != len(set(dates)):
        flags.append(f"{len(dates) - len(set(dates))} duplicate bar date(s)")
    flags += sc.hidden_splices(bars)
    if flags:
        checks["P4"][pair] = _cap(flags)
    sha = sa.write_pipeline_csv(bars, study / f"data_{tf}" / f"{pair}_{tf}.csv")
    return {"first": bars[0]["date"][:10], "last": bars[-1]["date"][:10],
            "n": len(bars), "sha256": sha}


def _p2(copy, tf: str, A: list[str], built: dict, data_dir: Path, workers: int) -> dict:
    """P2: the train run of every registered spec of the frozen templates on
    its OWN cell must reproduce its chained screen verdict exactly. Only train
    runs, only on A cells."""
    tmpl = st.templates(copy, tf)
    sids = sorted(s for v in tmpl.values() for s in v)
    a = set(A)
    misses, items, sel = [], [], []
    for sid in sids:
        spec = copy.specs[sid]
        pair = spec["universe"]["assets"][0]
        v = copy.screen.get(sid)
        if pair not in a:
            misses.append({"sid": sid, "pair": pair, "why": "own cell not in A; not run"})
        elif pair not in built:
            misses.append({"sid": sid, "pair": pair, "why": "own cell has no study data"})
        elif v is None:
            misses.append({"sid": sid, "pair": pair, "why": "no chained screen verdict"})
        else:
            items.append((spec, str(data_dir), tf))
            sel.append((sid, pair, v))
    results = parallel.run_all(h.train_job, items, workers) if items else []
    for (sid, pair, v), r in zip(sel, results):
        if isinstance(r, parallel.CellError):
            misses.append({"sid": sid, "pair": pair, "why": f"train run raised: {r}"})
            continue
        m = v.get("metrics", {})
        diff = {}
        if r["trades"] != m.get("trades"):
            diff["trades"] = [r["trades"], m.get("trades")]
        if r["net_pnl"] != m.get("net_pnl"):
            diff["net_pnl"] = [r["net_pnl"], m.get("net_pnl")]
        if r["screen_pass"] != (v.get("verdict") == "pass"):
            diff["pass"] = [r["screen_pass"], v.get("verdict")]
        if diff:
            misses.append({"sid": sid, "pair": pair, "why": "harness != chain "
                           "(harness, chain)", "diff": diff})
    if not sids:
        misses.append({"sid": None, "pair": None, "why": f"no registered templates at {tf}"})
    return {"checked": len(sids), "misses": misses}


def cmd_preflight(args, fetch) -> int:
    reg = _refuse_live(args)
    study = _study(args)
    _require_window(args)
    tf = args.tf
    pipe_dir = Path(args.pipeline_data_dir).resolve()
    data_dir = study / f"data_{tf}"
    if pipe_dir == data_dir.resolve() or study in pipe_dir.parents or pipe_dir == study:
        raise Refused(f"--pipeline-data-dir {pipe_dir} must be outside the study dir")
    u = _load_universe(study)
    upto = args.upto
    mpath = study / "manifest.json"
    if mpath.exists():
        m_upto = int(_load_json(mpath, "")["upto"])
        if upto is None:
            upto = m_upto
        elif upto != m_upto:
            raise Refused(f"--upto {upto} differs from the frozen manifest's {m_upto}")
    pairs = _pairs(u)
    for p in pairs:
        if p in su.RENAMES:
            _succ, swap, ratio, _url = su.RENAMES[p]
            if swap is None or ratio is None:
                raise Refused(f"RENAMES[{p}] has no swap date/ratio: confirm it from "
                              f"the Binance announcement first (P3)")
    copy = st.read_copy(reg, upto)
    if upto is None:
        upto = copy.n_entries
    checks = {"build": {}, "P1": {}, "P3": {}, "P4": {}}
    built = {}
    a = set(u["A"])
    for p in pairs:
        try:
            b = _build_pair(study, pipe_dir, tf, p, p in a, checks)
        except Refused:
            raise
        except Exception as exc:      # noqa: BLE001 - one cell must not hide the rest
            checks["build"][p] = [f"{type(exc).__name__}: {exc}"]
            b = None
        if b is not None:
            built[p] = b
    checks["P2"] = _p2(copy, tf, u["A"], built, data_dir, h.default_workers(args.workers))
    passed = (not any(checks[k] for k in ("build", "P1", "P3", "P4"))
              and not checks["P2"]["misses"] and len(built) == len(pairs))
    _write_json(study / f"preflight_{tf}.json", {
        "tf": tf, "upto": upto, "passed": passed, "pairs": pairs,
        "first_bars": {p: b["first"] for p, b in built.items()},
        "last_bars": {p: b["last"] for p, b in built.items()},
        "n_bars": {p: b["n"] for p, b in built.items()},
        "data_sha256": {p: b["sha256"] for p, b in built.items()},
        "checks": checks, "declared": _declared()})
    for k in ("build", "P1", "P3", "P4"):
        for p, found in checks[k].items():
            print(f"preflight {k} FAIL {p}: {found[0]}" +
                  (f" (+{len(found) - 1} more)" if len(found) > 1 else ""))
    for miss in checks["P2"]["misses"]:
        print(f"preflight P2 MISS {miss['sid']} ({miss['pair']}): {miss['why']}"
              + (f" {miss['diff']}" if "diff" in miss else ""))
    print(f"preflight {tf}: {'PASS' if passed else 'FAIL'}: {len(built)}/{len(pairs)} "
          f"cells built, P2 {checks['P2']['checked']} registered specs checked, "
          f"{len(checks['P2']['misses'])} miss(es); upto {upto}")
    return OK if passed else FAIL


def cmd_freeze(args, fetch) -> int:
    reg = _refuse_live(args)
    study = _study(args)
    _refuse_none_declared()
    u = _load_universe(study)
    if len(u["B"]) != u.get("target"):
        raise Refused(f"universe.json B has {len(u['B'])} pairs, target {u.get('target')}")
    pf = _load_json(study / "preflight_1d.json", "run preflight --tf 1d first")
    if pf.get("passed") is not True:
        raise Refused("preflight_1d.json did not pass: every check must pass before freeze")
    if pf.get("upto") != args.upto:
        raise Refused(f"preflight ran P2 at upto {pf.get('upto')}; re-run preflight "
                      f"with --upto {args.upto}")
    if pf.get("declared") != _declared():
        raise Refused("RENAMES/DELISTED/CUTS changed since preflight: re-run preflight")
    copy = st.read_copy(reg, args.upto)
    if not (0 < args.upto <= copy.n_entries):
        raise Refused(f"--upto {args.upto} outside 1..{copy.n_entries}")
    if not st.templates(copy, "1d"):
        raise Refused("no 1d templates in the frozen registry prefix")
    if _sha(_require_cmc(study).read_bytes()) != u["cmc_sha256"]:
        raise Refused("the CMC file differs from the one universe.json was built from")
    hashes = _input_hashes(study, copy)
    tl = _template_list(copy)
    manifest = {
        "upto": args.upto, "hashes": hashes, "canonical_json": CANON_RULE,
        "inputs": {"n_templates": {tf: len(v) for tf, v in tl.items()},
                   "A": u["A"], "B": u["B"],
                   "stratum_sizes": {s: sum(1 for v in u["strata"].values() if v == s)
                                     for s in su.STRATA},
                   **_declared(), "first_bars": pf["first_bars"]}}
    _write_json(study / "manifest.json", manifest)
    print(f"freeze: wrote {study / 'manifest.json'} (upto {args.upto})")
    for k in sorted(hashes):
        print(f"  {k}: {hashes[k]}")
    return OK


def _s5(layer: Path) -> str:
    text = (layer / DESIGN_REL).read_bytes().decode("utf-8").replace("\r\n", "\n")
    lines = text.split("\n")
    try:
        start = next(i for i, x in enumerate(lines) if x.startswith("## 5."))
        end = next(i for i, x in enumerate(lines) if i > start and x.startswith("## 6."))
    except StopIteration:
        raise Refused(f"{layer / DESIGN_REL}: no '## 5.' .. '## 6.' section") from None
    return "\n".join(lines[start:end]).strip("\n")


def cmd_draft_note(args, fetch) -> int:
    reg = _refuse_live(args)
    study = _study(args)
    layer = _layer(args)
    manifest = _load_json(study / "manifest.json", "run freeze first")
    copy = st.read_copy(reg, int(manifest["upto"]))
    _verify_inputs(study, copy, manifest)
    pf = _load_json(study / "preflight_1d.json", "run preflight --tf 1d first")
    inp = manifest["inputs"]
    out = [
        NOTE_PREFIX + "pre-registration of the off-chain crypto survivorship study, "
        f"design doc research-layer/{DESIGN_REL} (Coen approved 2026-11-06). It is "
        "chained before any study outcome number exists (design s4 step 2) and fixes "
        "the decision rule below (design s5, verbatim), the frozen inputs by sha256 "
        "(design P5), the pinned registry entry index for the templates, the splice "
        "and death list and the P4 cuts. It arms nothing in the pipeline: only the "
        "study tool (tools/survivorship.py) reads it, as the gate that refuses every "
        "outcome computation until this note is on the chain.",
        "",
        _s5(layer),
        "",
        CLARIFICATION,
        "",
        "## Frozen inputs (design P5)",
        "",
        f"Each hash is the sha256 of the UTF-8 canonical JSON {CANON_RULE}, except "
        "cmc, the sha256 of the raw CMC response bytes. templates = per timeframe, "
        "template key -> sorted registered strategy ids; cells_A / cells_B = the "
        "ordered pair lists; renames / delisted / cuts = the declared lists below; "
        "first_bars = pair -> first bar date of each 1d cell; data_1d = pair -> "
        "sha256 of each 1d study CSV.",
        "",
    ]
    out += [f"- {k}: {v}" for k, v in sorted(manifest["hashes"].items())]
    out += [
        "",
        f"- upto: {manifest['upto']} (0-based registry entry index; the templates are "
        f"the single-asset crypto strategies registered at entries 0..{manifest['upto'] - 1})",
        "- templates per timeframe: " + ", ".join(
            f"{tf} {n}" for tf, n in sorted(inp["n_templates"].items())),
        f"- cells: A {len(inp['A'])}, B {len(inp['B'])}; strata " + ", ".join(
            f"{s} {inp['stratum_sizes'].get(s, 0)}" for s in su.STRATA),
        "",
        "## Splices (renamed pairs, stratum N)",
        "",
    ]
    ren = inp["renames"]
    out += [f"- {p} -> {v[0]}: swap date {v[1]}, ratio {v[2]!r} successor units per "
            f"predecessor unit, {v[3]}" for p, v in sorted(ren.items())] or ["- none"]
    out += ["", "## Deaths (delisted pairs, stratum D)", ""]
    out += [f"- {p}: {url}" for p, url in sorted(inp["delisted"].items())] or ["- none"]
    out += ["", "## P4 cuts and exclusions", ""]
    out += [f"- {p}: cut to first usable date {d}"
            for p, d in sorted(inp["cuts"].items())] or ["- cuts: none"]
    p4 = pf.get("checks", {}).get("P4", {})
    out += [f"- P4 flag {p}: {'; '.join(v)}" for p, v in sorted(p4.items())] \
        or ["- exclusions: none"]
    text = "\n".join(out) + "\n"
    path = layer / NOTE_REL
    _write_lf(path, text)
    print(f"draft-note: wrote {path} ({len(text.encode('utf-8'))} bytes, LF). "
          f"DRAFT for Coen's approval; nothing chained.")
    return OK


def cmd_run(args, fetch) -> int:
    _refuse_live(args)
    study = _study(args)
    _require_window(args)
    copy, manifest, note_idx, _pf = _gate(args, study, args.tf)
    rows, _t, _p, _u = _rows(study, copy, args.tf)
    workers = h.default_workers(args.workers)
    s = h.run_arm(rows, h.full_job, study / f"results_{args.tf}.jsonl", workers,
                  now=_now_fn(args), est_s_per_item=screen_prior_s_for(args.tf),
                  chunk=workers * 8)
    return _arm_exit(s, len(rows), f"run {args.tf} (note at entry {note_idx})")


def cmd_gauntlet(args, fetch) -> int:
    _refuse_live(args)
    study = _study(args)
    _require_window(args)
    copy, manifest, note_idx, _pf = _gate(args, study, args.tf)
    rows, _t, _p, _u = _rows(study, copy, args.tf)
    workers = h.default_workers(args.workers)
    train_out = study / f"train_{args.tf}.jsonl"
    s1 = h.run_arm(rows, h.train_job, train_out, workers, now=_now_fn(args),
                   est_s_per_item=screen_prior_s_for(args.tf), chunk=workers * 8)
    rc = _arm_exit(s1, len(rows), f"gauntlet {args.tf} train")
    if rc != OK:
        return rc
    train, _d = _read_jsonl(train_out)
    grows = [r for r in rows if train.get(r["key"], {}).get("screen_pass") is True]
    s2 = h.run_arm(grows, h.gauntlet_job, study / f"gauntlet_{args.tf}.jsonl", workers,
                   now=_now_fn(args), est_s_per_item=prior_s_for(args.tf),
                   chunk=workers * 8)
    return _arm_exit(s2, len(grows), f"gauntlet {args.tf} v6.2 on screen passers")


def _verdict_text(v: str) -> str:
    if v == "REVERSE":
        return "NOT MATERIAL, flagged REVERSE (U < 0: our universe is harder than PIT)"
    return v


def _f(x) -> str:
    return "n/a" if x is None or x != x else f"{x:.4f}"


def cmd_analyse(args, fetch) -> int:
    _refuse_live(args)
    study = _study(args)
    layer = _layer(args)
    tf = args.tf
    copy, manifest, note_idx, pf = _gate(args, study, tf)
    rows, tmpl, pairs, u = _rows(study, copy, tf)
    keys = [r["key"] for r in rows]
    res, d1 = _read_jsonl(study / f"results_{tf}.jsonl")
    train, d2 = _read_jsonl(study / f"train_{tf}.jsonl")
    gaunt, d3 = _read_jsonl(study / f"gauntlet_{tf}.jsonl")
    if d1 or d2 or d3:
        raise Refused(f"duplicate result rows: {sorted(set(d1 + d2 + d3))[:5]}")
    miss = [k for k in keys if k not in res]
    if miss:
        raise Refused(f"results_{tf}.jsonl covers {len(keys) - len(miss)} of "
                      f"{len(keys)} template-cells: run `run --tf {tf}` to completion")
    miss = [k for k in keys if k not in train]
    if miss:
        raise Refused(f"train_{tf}.jsonl covers {len(keys) - len(miss)} of "
                      f"{len(keys)}: run `gauntlet --tf {tf}` to completion")
    passers = [k for k in keys if train[k].get("screen_pass") is True]
    miss = [k for k in passers if k not in gaunt]
    if miss:
        raise Refused(f"gauntlet_{tf}.jsonl covers {len(passers) - len(miss)} of "
                      f"{len(passers)} screen passers: run `gauntlet --tf {tf}`")
    tkeys = sorted(tmpl)
    m = ss.matrix([res[k] for k in keys], tkeys, pairs)
    strata = u["strata"]
    sidx = {s: np.array([j for j, p in enumerate(pairs) if strata[p] == s], dtype=int)
            for s in su.STRATA}
    prim = ss.primary(m, sidx)
    merged = {k: {**res[k], **train[k], **gaunt.get(k, {})} for k in keys}
    family = {t: copy.specs[tmpl[t][0]]["family"] for t in tkeys}
    sec = ss.secondaries(merged, strata, family)
    result = {**prim, "tf": tf, "stratum_sizes": {s: len(sidx[s]) for s in su.STRATA},
              "stratum_means": sec["stratum_mean_m"], "secondaries": sec,
              "n_templates": len(tkeys), "n_cells": len(pairs),
              "resamples": ss.RESAMPLES, "seed": ss.SEED, "note_index": note_idx,
              "upto": manifest["upto"], "manifest_hashes": manifest["hashes"],
              "preflight_passed": pf["passed"]}
    day = _now(args).date().isoformat()
    md = layer / "docs" / "runs" / f"{day}-survivorship-{tf}.md"
    _write_lf(md, ss.report(result))
    _write_json(md.with_suffix(".json"), result)
    print(f"analyse {tf}: {_verdict_text(prim['verdict'])}, G {_f(prim['G'])} "
          f"[{_f(prim['L'])}, {_f(prim['U'])}]; wrote {md}")
    if _result_note_for(tf):
        means = sec["stratum_mean_m"]
        lines = [
            RESULT_PREFIX + "result of the pre-registered crypto survivorship study "
            f"(note {NOTE_PREFIX.strip()} at registry entry {note_idx}); 1d arm, the "
            f"decision arm. Verdict: {_verdict_text(prim['verdict'])}. G = "
            f"{_f(prim['G'])}, 95% interval [L, U] = [{_f(prim['L'])}, {_f(prim['U'])}], "
            f"tau = {prim['tau']:.2f} ({len(tkeys)} templates x {len(pairs)} cells, "
            f"{ss.RESAMPLES} resamples, seed {ss.SEED}). Evidence only: it changes no "
            "protocol, gate, universe or quarantine record.",
            "",
            "Strata means of m (annualised OOS Sharpe, a None Sharpe scored 0.0):",
            "",
        ]
        lines += [f"- {s} ({len(sidx[s])} cells): {_f(means.get(s))}" for s in su.STRATA]
        lines += [
            "",
            f"Preconditions: preflight 1d PASSED (P1 parity, P2 the harness reproduced "
            f"{pf['checks']['P2']['checked']} registered screen verdicts exactly, P3 "
            f"splices, P4 no hidden splices); P5 inputs frozen at upto "
            f"{manifest['upto']} and every manifest hash matched at analysis.",
        ]
        path = layer / RESULT_REL
        _write_lf(path, "\n".join(lines) + "\n")
        print(f"analyse: wrote the result-note DRAFT {path}; nothing chained")
    return OK


# ------------------------------------------------------------------- parser

def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="tools.survivorship", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--study-dir", required=True)
    common.add_argument("--now", default=None,
                        help="ISO 8601 clock override (tests)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, func, *, tf=False, registry=False, workers=False, layer=False):
        p = sub.add_parser(name, parents=[common])
        if tf:
            p.add_argument("--tf", default="1d")
        if registry:
            p.add_argument("--registry", required=True, help="a COPY of the chain")
        if workers:
            p.add_argument("--workers", type=int, default=6)
        if layer:
            p.add_argument("--layer-dir", default=None,
                           help="research layer for docs/ (default this layer)")
        p.set_defaults(func=func)
        return p

    add("fetch-cmc", cmd_fetch_cmc)
    add("plan-fetch", cmd_plan_fetch, tf=True)
    add("fetch", cmd_fetch, tf=True)
    add("universe", cmd_universe, tf=True)
    p = add("preflight", cmd_preflight, tf=True, registry=True, workers=True)
    p.add_argument("--pipeline-data-dir", required=True,
                   help="the pipeline's data dir (read only)")
    p.add_argument("--upto", type=int, default=None)
    p = add("freeze", cmd_freeze, registry=True, layer=True)
    p.add_argument("--upto", type=int, required=True)
    add("draft-note", cmd_draft_note, registry=True, layer=True)
    add("run", cmd_run, tf=True, registry=True, workers=True, layer=True)
    add("gauntlet", cmd_gauntlet, tf=True, registry=True, workers=True, layer=True)
    add("analyse", cmd_analyse, tf=True, registry=True, layer=True)
    return ap


def main(argv: list[str] | None = None, *, fetch=None) -> int:
    args = _parser().parse_args(argv)
    try:
        return args.func(args, fetch or sa._http_get)
    except (Refused, st.LiveChainRefused) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return REFUSED


if __name__ == "__main__":
    sys.exit(main())
