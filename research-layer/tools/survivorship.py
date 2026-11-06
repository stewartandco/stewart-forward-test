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
(fetch, preflight's P2, run, gauntlet; resume later). Every network call goes
through an injectable `fetch(url) -> bytes`. `--now` is a test clock: it is
refused unless the environment sets SURVIVORSHIP_TEST_CLOCK=1. The tool never opens a live chain
(`--registry` passes refuse_live first) and never writes research-layer/data/.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import http.client
import json
import os
import subprocess
import sys
import time
import urllib.error
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
CODE_DIR = Path(__file__).resolve().parent          # tools/survivorship*.py live here
# The design's counts (s1.2, s3). freeze refuses others unless --accept-counts.
DESIGN_COUNTS = {"templates_1d": 80, "sids_1d": 960,
                 "strata": {"S": 66, "R": 10, "F": 22, "N": 7, "D": 5}}

OK, FAIL, REFUSED, STOPPED = 0, 1, 2, 3

FETCH_EST_S = 2.0          # per archive file: zip + .CHECKSUM, each paced 0.2 s
FETCH_BATCH = 25           # files per fetch progress line
BAN_CODES = (403, 418, 429)  # the archive refusing or rate-limiting this client
P2_CHUNK_PER_WORKER = 8
TEST_CLOCK_ENV = "SURVIVORSHIP_TEST_CLOCK"


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


def _test_clock(args) -> bool:
    """--now is for tests only (final review M-b): an operator who passes it
    without SURVIVORSHIP_TEST_CLOCK=1 is refused, so the 08:40-20:00 window
    cannot be moved from the command line."""
    if not getattr(args, "now", None):
        return False
    if os.environ.get(TEST_CLOCK_ENV) != "1":
        raise Refused(f"--now is a test clock; it is refused unless "
                      f"{TEST_CLOCK_ENV}=1 is set")
    return True


def _now(args) -> datetime:
    if _test_clock(args):
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


def _real_now() -> datetime:
    return datetime.now(h.CLOCK)


def _now_fn(args):
    """The run clock for run_arm. With --now it is an OFFSET clock: it starts
    at --now and advances with real time, so a long run started with --now
    still stops at 20:00 by its own reckoning."""
    if _test_clock(args):
        fixed, t0 = _now(args), _real_now()
        return lambda: fixed + (_real_now() - t0)
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


def _cmc_rows(path: Path) -> list[dict]:
    """su.load_cmc, a refusal (not a traceback) when the file is unusable."""
    try:
        return su.load_cmc(path)
    except (ValueError, KeyError, TypeError) as exc:
        raise Refused(f"{path} is not a usable CMC listing: {exc}; delete it and "
                      f"re-run fetch-cmc") from None


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


def _design_bytes() -> bytes:
    """The design doc of the tool's OWN layer, LF-normalised (the git blob's
    bytes under core.autocrlf). Never read from --layer-dir."""
    return (LAYER / DESIGN_REL).read_bytes().replace(b"\r\n", b"\n")


def _study_code_files() -> dict:
    """name -> sha256 of every tools/survivorship*.py (LF-normalised bytes,
    sorted by name): the study code and its decision constants (TAU, SEED,
    END, the strata rule ...). Tests are named test_*, so never included."""
    return {f.name: _sha(f.read_bytes().replace(b"\r\n", b"\n"))
            for f in sorted(Path(CODE_DIR).glob("survivorship*.py"))}


def _git_head() -> str | None:
    """`git rev-parse HEAD` of the tool's layer: recorded, informational only."""
    try:
        r = subprocess.run(["git", "-C", str(LAYER), "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return (r.stdout.strip() or None) if r.returncode == 0 else None


def _check_strata(u: dict) -> None:
    """universe.json's strata must be the pure function of A, B and the hashed
    RENAMES/DELISTED (I1): a hand-edited stratum is refused."""
    want = su.strata(u["A"], u["B"])
    have = u.get("strata") or {}
    if have != want:
        bad = sorted(p for p in set(want) | set(have) if have.get(p) != want.get(p))
        raise Refused(f"universe.json strata differ from strata(A, B) under the "
                      f"declared RENAMES/DELISTED for {len(bad)} pair(s) "
                      f"({', '.join(bad[:5])}); re-run universe")


def _manifest_digest(manifest: dict) -> str:
    """sha256 of the canonical JSON of the manifest's hashes: every result row
    carries it, so a row is tied to the frozen inputs it was computed under."""
    return _sha_canon(manifest["hashes"])


def _counts(copy, u: dict) -> dict:
    tmpl = st.templates(copy, "1d")
    return {"templates_1d": len(tmpl), "sids_1d": sum(len(v) for v in tmpl.values()),
            "strata": {s: sum(1 for v in u["strata"].values() if v == s)
                       for s in su.STRATA}}


def _sid_counts(tl: dict) -> dict:
    return {tf: sum(len(v) for v in t.values()) for tf, t in tl.items()}


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
        "design_doc_sha256": _sha(_design_bytes()),
        "study_code": _sha_canon(_study_code_files()),
    }


def _verify_inputs(study: Path, copy, manifest: dict) -> None:
    now = _input_hashes(study, copy)
    want = manifest.get("hashes", {})
    diff = sorted(k for k in set(now) | set(want) if now.get(k) != want.get(k))
    if diff:
        raise Refused("the inputs on disk no longer match manifest.json (" +
                      ", ".join(diff) + "); a frozen input changed after freeze. "
                      "After the note, only a superseding chained note may change one")


def _line_sha(registry: Path, idx: int, text: str) -> str:
    """sha256 of entry `idx`'s exact JSONL line, without its line terminator
    (CRLF on the live file, LF in registry_log.d: the same hash either way)."""
    with open(registry, "rb") as f:
        for i, line in enumerate(f):
            if i == idx:
                line = line.rstrip(b"\n").rstrip(b"\r")
                e = json.loads(line)
                if e.get("entry_type") != "note" or e.get("payload", {}).get("text") != text:
                    raise NoteMissing(f"entry {idx} of {registry} is not the matched note")
                return _sha(line)
    raise NoteMissing(f"{registry} has no entry {idx}")


def require_note(copy, manifest: dict, registry: Path | None = None) -> tuple:
    """The gate (design s4 step 3). Returns (entry index, sha256 of that
    entry's exact JSONL line; None without `registry`) of the chained
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
        return idx, (None if registry is None else _line_sha(Path(registry), idx, text))
    raise NoteMissing(
        f"no chained note starting {NOTE_PREFIX!r} matches manifest.json"
        + (": " + "; ".join(why) if why else "") +
        ". Chain the Coen-approved draft (design s4 step 2), re-copy the chain, "
        "then run again")


def _check_pf_pairs(pf: dict, u: dict, tf: str) -> None:
    want = _pairs(u)
    if pf.get("pairs") != want:
        raise Refused(f"preflight_{tf}.json checked pairs {len(pf.get('pairs') or [])} "
                      f"that are not universe.json's A + B-only list ({len(want)}): "
                      f"re-run preflight --tf {tf}")
    if set(pf.get("data_sha256", {})) != set(want):
        raise Refused(f"preflight_{tf}.json built data for a different pair set than "
                      f"universe.json: re-run preflight --tf {tf}")


def _gate(args, study: Path, tf: str):
    """Everything run/gauntlet/analyse check before any engine call or result
    read. refuse_live has already run."""
    manifest = _load_json(study / "manifest.json", "run freeze first")
    copy = st.read_copy(Path(args.registry), int(manifest["upto"]))
    note_idx, note_sha = require_note(copy, manifest, Path(args.registry))
    _verify_inputs(study, copy, manifest)
    u = _load_universe(study)
    _check_strata(u)
    pf = _load_json(study / f"preflight_{tf}.json", f"run preflight --tf {tf} first")
    _check_pf_pairs(pf, u, tf)
    if tf != "1d" and pf.get("declared") != _declared():
        raise Refused(f"preflight_{tf}.json ran under other RENAMES/DELISTED/CUTS: "
                      f"re-run preflight --tf {tf}")
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
    return copy, manifest, note_idx, note_sha, pf


def _rows(study: Path, copy, tf: str, manifest: dict, pf: dict):
    """One row per template-cell. Each carries a `stamp` (I2): the cell spec's
    strategy id, the cell's study-CSV sha256 from preflight_<tf>.json and the
    manifest digest. run_arm writes it into every result line and recomputes
    a line whose stamp differs; analyse refuses one."""
    u = _load_universe(study)
    pairs = _pairs(u)
    tmpl = st.templates(copy, tf)
    if not tmpl:
        raise Refused(f"no frozen templates at {tf}")
    data_dir = str(study / f"data_{tf}")
    digest = _manifest_digest(manifest)
    rows = []
    for tkey in sorted(tmpl):
        for pair in pairs:
            spec = st.cell_spec(copy, tkey, tmpl[tkey], pair, tf)
            rows.append({"key": f"{tkey}|{pair}|{tf}", "stratum": u["strata"][pair],
                         "tkey": tkey, "pair": pair, "tf": tf,
                         "stamp": {"strategy_id": spec["strategy_id"],
                                   "data_sha256": pf["data_sha256"][pair],
                                   "manifest_digest": digest},
                         "item": (spec, data_dir, tf)})
    return rows, tmpl, pairs, u


def _check_stamps(name: str, found: dict, rows: list[dict]) -> None:
    """analyse refuses any result line not of the current frozen grid or whose
    stamp differs from the current one (I2)."""
    want = {r["key"]: r["stamp"] for r in rows}
    for k, rec in found.items():
        if k not in want:
            raise Refused(f"{name}: a row for {k!r}, not a template-cell of the "
                          f"frozen grid")
        diff = sorted(f for f, v in want[k].items() if rec.get(f) != v)
        if diff:
            raise Refused(f"{name}: the row for {k!r} was computed under other "
                          f"inputs ({', '.join(diff)} differ); re-run the arm")


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
    try:
        data = fetch(su.CMC_URL)
    except (OSError, http.client.HTTPException) as exc:   # URLError/HTTPError are OSError
        print(f"fetch-cmc: FAIL network error fetching the CMC snapshot: "
              f"{type(exc).__name__}: {exc}; nothing written")
        return FAIL
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
        syms = [su.pair_for(r["symbol"]) for r in _cmc_rows(cmc_path)]
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
    """Downloads every planned file not already on disk (checksum-verified).
    Before EACH download it checks the run window (h._chunk_ok, one file at
    FETCH_EST_S) and stops with exit 3 when the next file would not finish
    before 20:00; a re-run resumes (`_have`). An HTTP 403/418/429 stops it at
    once, exit 1: the archive is refusing this client and more requests make
    it worse. Any other per-file failure is reported and fetch goes on."""
    study = _study(args)
    _require_window(args)
    plan = _load_json(study / f"plan_{args.tf}.json", f"run plan-fetch --tf {args.tf} first")
    now = _now_fn(args)
    dest = study / "archive"
    files = plan["files"]
    new = have = 0
    errors = []
    stopped = banned = None
    t0 = time.monotonic()
    for i, (sym, tf, month) in enumerate(files, 1):
        if _have(dest, sym, tf, month):
            have += 1
        else:
            if not h._chunk_ok(now, 1, FETCH_EST_S):
                stopped = i - 1
                break
            try:
                sa.download_month(sym, tf, month, dest, fetch)
                new += 1
            except sa.ChecksumMismatch as exc:
                errors.append(str(exc))
            except urllib.error.HTTPError as exc:
                if exc.code in BAN_CODES:
                    banned = (sym, tf, month, exc.code)
                    break
                errors.append(f"{sym} {tf} {month}: HTTPError {exc.code}: {exc}")
            except Exception as exc:      # noqa: BLE001 - network/disk: report, go on
                errors.append(f"{sym} {tf} {month}: {type(exc).__name__}: {exc}")
        if i % FETCH_BATCH == 0 or i == len(files):
            print(h.progress("fetch", i, len(files), i - have, t0, unit="files",
                             extra=f"; {new} downloaded, {have} present, "
                                   f"{len(errors)} failed"), flush=True)
    print(f"fetch: {new} downloaded, {have} already present, {len(errors)} failed")
    for e in errors[:FINDINGS_CAP]:
        print(f"fetch: FAIL {e}")
    if banned:
        sym, tf, month, code = banned
        print(f"fetch: FAIL HTTP {code} on {sym} {tf} {month}: the archive is refusing "
              f"or rate-limiting this client; stopped at once, nothing more requested. "
              f"Wait before re-running; fetch resumes from what is on disk")
        return FAIL
    if errors:
        return FAIL
    if stopped is not None:
        print(f"fetch: stopped at the run window after {stopped}/{len(files)} files; "
              f"resume inside 08:40-20:00 Perth (files on disk are kept)")
        return STOPPED
    return OK


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
    cmc = _cmc_rows(cmc_path)
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


def _plan_months(study: Path, tf: str) -> dict[str, list[str]]:
    """symbol -> the months plan-fetch planned for it at `tf`."""
    plan = _load_json(study / f"plan_{tf}.json", f"run plan-fetch --tf {tf} first")
    months: dict[str, list[str]] = {}
    for sym, ftf, month in plan["files"]:
        if ftf == tf:
            months.setdefault(sym, []).append(month)
    return months


def _missing_months(study: Path, sym: str, tf: str, months: list[str] | None) -> list[str]:
    if not months:
        return [f"no planned months for {sym} {tf} in plan_{tf}.json"]
    folder = study / "archive" / sym / tf
    return [f"{sym}-{tf}-{m}.zip is planned but not on disk (fetch is incomplete)"
            for m in sorted(months) if not (folder / f"{sym}-{tf}-{m}.zip").exists()]


def _build_pair(study: Path, pipe_dir: Path, tf: str, pair: str, in_a: bool,
                checks: dict, months: dict, stratum: str) -> dict | None:
    """Builds one cell's study CSV (cuts, splice, first-bar rule, END) and
    records its P1/P3/P4 findings. Data only: no engine call. Every month
    plan_<tf>.json lists for the pair (and its rename successor) must be on
    disk (I5); the last bar must be END for S/R/F/N cells and before END for
    D cells, or it is a stratum classification error."""
    succ = su.RENAMES[pair][0] if pair in su.RENAMES else None
    missing = _missing_months(study, pair, tf, months.get(pair))
    if succ:
        missing += _missing_months(study, succ, tf, months.get(succ))
    if missing:
        checks["build"][pair] = _cap(missing)
        return None
    bars = _archive_bars(study, pair, tf, months=months[pair])
    if not bars:
        checks["build"][pair] = ["no archive bars"]
        return None
    raw = bars
    if pair in su.CUTS:
        bars = sc.apply_cut(bars, su.CUTS[pair])
    if pair in su.RENAMES:
        succ, swap, ratio, _url = su.RENAMES[pair]
        try:
            bars = sc.splice(bars, _archive_bars(study, succ, tf, months=months[succ]),
                             swap, ratio)
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
    last = bars[-1]["date"][:10]
    if (last >= su.END) if stratum == "D" else (last != su.END):
        want = f"before END {su.END}" if stratum == "D" else f"on END {su.END}"
        checks["P3"].setdefault(pair, []).append(
            f"stratum classification error: {pair} is stratum {stratum} but its last "
            f"bar is {last} (want {want}); re-check DELISTED/RENAMES (design P3)")
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


def _p2(copy, tf: str, A: list[str], built: dict, data_dir: Path, workers: int,
        now=h._now) -> dict:
    """P2: the train run of every registered spec of the frozen templates on
    its OWN cell must reproduce its chained screen verdict exactly. Only train
    runs, only on A cells. Runs in chunks of workers x P2_CHUNK_PER_WORKER,
    each inside the worker env and only when it fits the run window (the
    screen's per-spec prior); a chunk that does not fit stops P2
    (`stopped_at_window`), and preflight exits 3."""
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
    results: list = []
    stopped = False
    chunk = max(1, workers * P2_CHUNK_PER_WORKER)
    est = screen_prior_s_for(tf)
    t0 = time.monotonic()
    for i in range(0, len(items), chunk):
        batch = items[i:i + chunk]
        if not h._chunk_ok(now, len(batch), est):
            stopped = True
            break
        results += h.run_pool(h.train_job, batch, workers)
        print(h.progress("preflight P2", len(results), len(items), len(results), t0,
                         unit="train runs"), flush=True)
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
    return {"checked": len(sids), "run": len(results), "stopped_at_window": stopped,
            "misses": misses}


def cmd_preflight(args, fetch) -> int:
    reg = _refuse_live(args)
    s_res = Path(args.study_dir).resolve()
    pipe_dir = Path(args.pipeline_data_dir).resolve()
    if pipe_dir == s_res or s_res in pipe_dir.parents or pipe_dir in s_res.parents:
        raise Refused(f"--pipeline-data-dir {pipe_dir} and --study-dir {s_res} must "
                      f"not contain one another (the pipeline data is read only)")
    study = _study(args)
    _require_window(args)
    tf = args.tf
    data_dir = study / f"data_{tf}"
    if data_dir.resolve().parent != study:
        raise Refused(f"--tf {tf!r} does not name a data dir inside the study")
    u = _load_universe(study)
    _check_strata(u)
    months = _plan_months(study, tf)
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
    if data_dir.is_dir():
        for f in data_dir.iterdir():
            if f.is_file():
                f.unlink()            # a stale CSV from an earlier universe never survives
    checks = {"build": {}, "P1": {}, "P3": {}, "P4": {}}
    built = {}
    a = set(u["A"])
    for p in pairs:
        try:
            b = _build_pair(study, pipe_dir, tf, p, p in a, checks, months,
                            u["strata"][p])
        except Refused:
            raise
        except Exception as exc:      # noqa: BLE001 - one cell must not hide the rest
            checks["build"][p] = [f"{type(exc).__name__}: {exc}"]
            b = None
        if b is not None:
            built[p] = b
    checks["P2"] = _p2(copy, tf, u["A"], built, data_dir, h.default_workers(args.workers),
                       now=_now_fn(args))
    stopped = checks["P2"]["stopped_at_window"]
    passed = (not any(checks[k] for k in ("build", "P1", "P3", "P4"))
              and not checks["P2"]["misses"] and len(built) == len(pairs)
              and not stopped)
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
    if stopped:
        print(f"preflight {tf}: STOPPED at the run window after "
              f"{checks['P2']['run']}/{checks['P2']['checked']} P2 train runs; "
              f"preflight_{tf}.json says not passed. Re-run preflight inside "
              f"08:40-20:00 Perth (it starts over)")
        return STOPPED
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
    _check_strata(u)
    pf = _load_json(study / "preflight_1d.json", "run preflight --tf 1d first")
    if pf.get("passed") is not True:
        raise Refused("preflight_1d.json did not pass: every check must pass before freeze")
    if pf.get("upto") != args.upto:
        raise Refused(f"preflight ran P2 at upto {pf.get('upto')}; re-run preflight "
                      f"with --upto {args.upto}")
    if pf.get("declared") != _declared():
        raise Refused("RENAMES/DELISTED/CUTS changed since preflight: re-run preflight")
    _check_pf_pairs(pf, u, "1d")
    copy = st.read_copy(reg, args.upto)
    if not (0 < args.upto <= copy.n_entries):
        raise Refused(f"--upto {args.upto} outside 1..{copy.n_entries}")
    if not st.templates(copy, "1d"):
        raise Refused("no 1d templates in the frozen registry prefix")
    if _sha(_require_cmc(study).read_bytes()) != u["cmc_sha256"]:
        raise Refused("the CMC file differs from the one universe.json was built from")
    counts = _counts(copy, u)
    off = sorted(k for k in DESIGN_COUNTS if counts[k] != DESIGN_COUNTS[k])
    if off and not args.accept_counts:
        raise Refused("the frozen counts differ from the design's (" + "; ".join(
            f"{k} {counts[k]}, design {DESIGN_COUNTS[k]}" for k in off) +
            "); re-check the registry prefix and the universe, or pass "
            "--accept-counts (the note then states the difference)")
    hashes = _input_hashes(study, copy)
    tl = _template_list(copy)
    manifest = {
        "upto": args.upto, "hashes": hashes, "canonical_json": CANON_RULE,
        "accepted_counts": bool(off),
        "design_counts": DESIGN_COUNTS,
        "git_head": _git_head(),
        "inputs": {"n_templates": {tf: len(v) for tf, v in tl.items()},
                   "n_sids": _sid_counts(tl),
                   "A": u["A"], "B": u["B"],
                   "stratum_sizes": counts["strata"],
                   **_declared(), "first_bars": pf["first_bars"],
                   "study_code_files": _study_code_files()}}
    _write_json(study / "manifest.json", manifest)
    print(f"freeze: wrote {study / 'manifest.json'} (upto {args.upto})")
    if off:
        print("freeze: ACCEPTED counts that differ from the design: " + "; ".join(
            f"{k} {counts[k]} (design {DESIGN_COUNTS[k]})" for k in off))
    for tf, n in sorted(manifest["inputs"]["n_templates"].items()):
        print(f"  templates {tf}: {n} templates, "
              f"{manifest['inputs']['n_sids'][tf]} strategy ids")
    for k in sorted(hashes):
        print(f"  {k}: {hashes[k]}")
    return OK


def _s5() -> str:
    lines = _design_bytes().decode("utf-8").split("\n")
    try:
        start = next(i for i, x in enumerate(lines) if x.startswith("## 5."))
        end = next(i for i, x in enumerate(lines) if i > start and x.startswith("## 6."))
    except StopIteration:
        raise Refused(f"{LAYER / DESIGN_REL}: no '## 5.' .. '## 6.' section") from None
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
    accepted = manifest.get("accepted_counts") is True
    dc = manifest.get("design_counts") or DESIGN_COUNTS
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
        _s5(),
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
        "sha256 of each 1d study CSV; design_doc_sha256 = the sha256 of the design "
        "doc's LF-normalised bytes (the file s5 above is quoted from); study_code = "
        "file name -> sha256 of the LF-normalised bytes of every "
        "tools/survivorship*.py (the study code and its decision constants). Every "
        "result row carries its cell spec's strategy id, its cell's study-CSV sha256 "
        "and the sha256 of the canonical JSON of these hashes; analysis refuses a row "
        "whose stamp differs.",
        "",
    ]
    out += [f"- {k}: {v}" for k, v in sorted(manifest["hashes"].items())]
    out += [
        "",
        f"- upto: {manifest['upto']} (0-based registry entry index; the templates are "
        f"the single-asset crypto strategies registered at entries 0..{manifest['upto'] - 1})",
        "- templates per timeframe: " + ", ".join(
            f"{tf} {n} templates, {inp['n_sids'][tf]} strategy ids"
            + (f" (design: {dc['templates_1d']} templates, {dc['sids_1d']} strategy ids)"
               if tf == "1d" and accepted else "")
            for tf, n in sorted(inp["n_templates"].items())),
        f"- cells: A {len(inp['A'])}, B {len(inp['B'])}; strata " + ", ".join(
            f"{s} {inp['stratum_sizes'].get(s, 0)}" for s in su.STRATA)
        + (" (design: " + ", ".join(f"{s} {dc['strata'][s]}" for s in su.STRATA) + ")"
           if accepted else ""),
    ]
    if accepted:
        out.append("- accepted counts: the frozen counts above differ from the design's "
                   "(design s1.2 and s3); freeze ran with --accept-counts, and this note "
                   "pre-registers the counts as frozen, not the design's.")
    out += [
        f"- study code: {len(inp['study_code_files'])} files tools/survivorship*.py, "
        "sha256 of each file's LF-normalised bytes (study_code above hashes this map): "
        + ", ".join(f"{n} {d}" for n, d in sorted(inp["study_code_files"].items())),
        f"- git HEAD at freeze (informational, not checked): {manifest.get('git_head')}",
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
    copy, manifest, note_idx, _ns, pf = _gate(args, study, args.tf)
    rows, _t, _p, _u = _rows(study, copy, args.tf, manifest, pf)
    workers = h.default_workers(args.workers)
    s = h.run_arm(rows, h.full_job, study / f"results_{args.tf}.jsonl", workers,
                  now=_now_fn(args), est_s_per_item=screen_prior_s_for(args.tf),
                  chunk=workers * 8, label=f"run {args.tf}")
    return _arm_exit(s, len(rows), f"run {args.tf} (note at entry {note_idx})")


def cmd_gauntlet(args, fetch) -> int:
    _refuse_live(args)
    study = _study(args)
    _require_window(args)
    copy, manifest, note_idx, _ns, pf = _gate(args, study, args.tf)
    rows, _t, _p, _u = _rows(study, copy, args.tf, manifest, pf)
    workers = h.default_workers(args.workers)
    train_out = study / f"train_{args.tf}.jsonl"
    s1 = h.run_arm(rows, h.train_job, train_out, workers, now=_now_fn(args),
                   est_s_per_item=screen_prior_s_for(args.tf), chunk=workers * 8,
                   label=f"gauntlet {args.tf} train")
    rc = _arm_exit(s1, len(rows), f"gauntlet {args.tf} train")
    if rc != OK:
        return rc
    train, _d = _read_jsonl(train_out)
    grows = [r for r in rows if train.get(r["key"], {}).get("screen_pass") is True]
    s2 = h.run_arm(grows, h.gauntlet_job, study / f"gauntlet_{args.tf}.jsonl", workers,
                   now=_now_fn(args), est_s_per_item=prior_s_for(args.tf),
                   chunk=workers * 8, label=f"gauntlet {args.tf} v6.2")
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
    copy, manifest, note_idx, note_sha, pf = _gate(args, study, tf)
    rows, tmpl, pairs, u = _rows(study, copy, tf, manifest, pf)
    keys = [r["key"] for r in rows]
    res, d1 = _read_jsonl(study / f"results_{tf}.jsonl")
    train, d2 = _read_jsonl(study / f"train_{tf}.jsonl")
    gaunt, d3 = _read_jsonl(study / f"gauntlet_{tf}.jsonl")
    if d1 or d2 or d3:
        raise Refused(f"duplicate result rows: {sorted(set(d1 + d2 + d3))[:5]}")
    for name, found in ((f"results_{tf}.jsonl", res), (f"train_{tf}.jsonl", train),
                        (f"gauntlet_{tf}.jsonl", gaunt)):
        _check_stamps(name, found, rows)
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
              "note_line_sha256": note_sha,
              "upto": manifest["upto"], "manifest_hashes": manifest["hashes"],
              "preflight_passed": pf["passed"]}
    day = _now(args).date().isoformat()
    md = layer / "docs" / "runs" / f"{day}-survivorship-{tf}.md"
    note_line = f"pre-registration note: entry {note_idx}, line sha256 {note_sha}"
    _write_lf(md, ss.report(result) + "\n## Pre-registration\n\n" + note_line + "\n")
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
            "",
            note_line,
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
            p.add_argument("--workers", type=int, default=h.default_worker_request(),
                           help="pool size (default (cores - 2) // 2, at most "
                                f"{h.MAX_WORKERS}; also bounded by free commit)")
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
    p.add_argument("--accept-counts", action="store_true",
                   help="freeze although the 1d template/sid counts or the stratum "
                        "sizes differ from the design's (recorded; the note says so)")
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
