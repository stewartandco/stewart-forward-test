"""Report-only rehearsal of pipeline.gauntlet_stats on a chain COPY (Task 6,
effective trials per cell, docs/2026-11-04-effective-trials-per-cell-design.md
s7 last bullet).

Never scheduled. It runs the real `gauntlet_stats.run` WITHOUT --chain on a
registry COPY, reading the live data and sim cache, and measures the memory
and the time the per-cell clustering takes:

    python -m tools.stats_rehearsal --registry SCRATCH/registry_log.jsonl \\
        --data-dir LIVE/research-layer/data \\
        --simcache-dir LIVE/research-layer/simcache \\
        --scratch SCRATCH [--dump-aligned SCRATCH/aligned.npz]

The sim cache is READ, never written: `SimCache.put`, `_write`, `_poison` and
`_migrate_legacy` are replaced by functions that raise `SimCacheWriteRefused`
for the whole run, so a miss (which would simulate and `put`), a poisoned
entry (which would be unlinked) or a legacy entry (which would be migrated)
aborts the run (exit 2) and leaves the cache as it was. A data or sim-cache
directory fingerprint (file count, bytes, newest mtime) is taken before and
after and must match.

Refusals (exit 2, nothing run): any passthrough token (after --) other than
--cutoff, --deadline-hours and --pbo-null-draws with their values (an
ALLOW-list: gauntlet_stats' argparse takes option prefixes such as --chai
and lets a repeated option win, so --chain, --registry, --data-dir,
--simcache-dir, --logs-dir and --report, or any prefix of them, are refused
there), a --registry or any output path inside a directory that also holds
`registry_log.d` (the live layout), or a --registry that resolves to this
layer's own chain.

Writes ONLY under --scratch: logs/ (the job's status, PBO cache and instance
lock), report.md (the job's report), rehearsal.json (this tool's summary),
and --dump-aligned if given. rehearsal.json holds the job's status, wall
time, the memory readings, the per-partition table, every computed
gauntlet_stats payload, and per-strategy moments (Sharpe, length, skew,
kurtosis) of the aligned series the deflated Sharpe reads, so a DSR can be
recomputed under any SR*.

Memory: PROCESS_MEMORY_COUNTERS_EX via GetProcessMemoryInfo. PeakPagefileUsage
(the process's peak private commit) and PrivateUsage at the end of the run,
plus a sampler thread that polls PrivateUsage every --sample-ms while the
clustering runs and records, per phase, the private bytes at the phase start
and the maximum during it: `align` (cluster_registry_by_cell's aligned copies
of all n series) and each partition's `cut_partition`. The clustering's
increment (max during the call minus the private bytes at its start) is the
figure comparable to `gauntlet_stats.cluster_need_mb`, whose availability is
read after the series pass.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import threading
import time
from pathlib import Path

LAYER = Path(__file__).resolve().parent.parent
if str(LAYER) not in sys.path:
    sys.path.insert(0, str(LAYER))

from pipeline import cluster_cells, gauntlet, gauntlet_stats, simcache  # noqa: E402
from pipeline.stats import moments, sharpe                               # noqa: E402

MB = 2 ** 20


class SimCacheWriteRefused(RuntimeError):
    """A sim cache write path was reached during a read-only rehearsal."""


class _PMC(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_uint32), ("PageFaultCount", ctypes.c_uint32)] + [
        (n, ctypes.c_size_t) for n in (
            "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
            "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
            "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage",
            "PrivateUsage")]


def memory_counters() -> dict | None:
    """This process's PROCESS_MEMORY_COUNTERS_EX in bytes; None off Windows."""
    if os.name != "nt":
        return None
    k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PMC),
                                           ctypes.c_uint32]
    m = _PMC()
    m.cb = ctypes.sizeof(m)
    if not psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(m), m.cb):
        return None
    return {n: int(getattr(m, n)) for n, _ in _PMC._fields_[2:]}


def _private() -> int:
    c = memory_counters()
    return c["PrivateUsage"] if c else 0


class Sampler:
    """Polls PrivateUsage on a thread; `phase(name)` starts a new phase and
    records the private bytes at its start; the max per phase is kept."""

    def __init__(self, interval_s: float):
        self.interval_s = interval_s
        self.phases: list[dict] = []
        self._cur: dict | None = None
        self._stop = threading.Event()
        self._t: threading.Thread | None = None

    def phase(self, name: str, **info) -> None:
        p = _private()
        self._cur = {"phase": name, "start": p, "max": p, **info}
        self.phases.append(self._cur)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            cur = self._cur
            if cur is not None:
                p = _private()
                if p > cur["max"]:
                    cur["max"] = p

    def start(self) -> None:
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def stop(self) -> None:
        cur = self._cur
        if cur is not None:
            cur["max"] = max(cur["max"], _private())
        self._stop.set()
        if self._t is not None:
            self._t.join()


def _refuse(name: str):
    def f(self, *a, **k):
        raise SimCacheWriteRefused(f"read-only rehearsal: SimCache.{name} refused "
                                   f"(args {a!r:.200})")
    return f


WRITE_PATHS = ("put", "_write", "_poison", "_migrate_legacy")


def fingerprint(d: Path) -> dict:
    """(files, bytes, newest mtime_ns) of a directory, recursively."""
    n = size = newest = 0
    stack = [d]
    while stack:
        with os.scandir(stack.pop()) as it:
            for e in it:
                if e.is_dir(follow_symlinks=False):
                    stack.append(Path(e.path))
                else:
                    st = e.stat(follow_symlinks=False)
                    n, size = n + 1, size + st.st_size
                    newest = max(newest, st.st_mtime_ns)
    return {"files": n, "bytes": size, "newest_mtime_ns": newest}


def _live_layout(p: Path) -> bool:
    """True when p, or any parent of it, is a directory that holds a
    registry_log.d (a research-layer tree)."""
    p = p.resolve()
    for d in (p, *p.parents):
        if (d / "registry_log.d").is_dir():
            return True
    return False


# The ONLY gauntlet_stats options a rehearsal passes through. gauntlet_stats'
# argparse accepts any unambiguous prefix of an option (`--chai` is --chain)
# and the last of a repeated option wins (`--registry LIVE` after the
# wrapper's own), so a deny-list cannot hold: every other token is refused.
PASSTHROUGH_ALLOWED = ("--cutoff", "--deadline-hours", "--pbo-null-draws")


def _is_number(s: str) -> bool:
    try:
        float(s)
    except ValueError:
        return False
    return True


def check_passthrough(tokens: list[str]) -> tuple[list[str] | None, str | None]:
    """(normalised tokens, None) when every token is an allowed option with
    its value (`--opt value` or `--opt=value`), else (None, the reason). The
    normalised form is always `--opt=value`, so gauntlet_stats can never read
    a value as an option."""
    out: list[str] = []
    i = 1 if tokens[:1] == ["--"] else 0     # an argparse that keeps the --
    while i < len(tokens):
        tok = tokens[i]
        name, eq, value = tok.partition("=")
        if name not in PASSTHROUGH_ALLOWED:
            what = "option" if tok.startswith("-") else "argument"
            return None, (f"passthrough {what} {tok!r} is not allowed (only "
                          f"{', '.join(PASSTHROUGH_ALLOWED)}, each with its value)")
        if not eq:
            if i + 1 >= len(tokens):
                return None, f"passthrough option {tok!r} has no value"
            value = tokens[i + 1]
            if value.startswith("-") and not _is_number(value):
                return None, (f"passthrough option {tok!r} takes a value, "
                              f"not {value!r}")
            i += 1
        if value == "":
            return None, f"passthrough option {name!r} has an empty value"
        out.append(f"{name}={value}")
        i += 1
    return out, None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--registry", type=Path, required=True)
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--simcache-dir", type=Path, required=True)
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--dump-aligned", type=Path, default=None)
    ap.add_argument("--sample-ms", type=float, default=10.0)
    ap.add_argument("--no-fingerprint", action="store_true",
                    help="skip the before/after directory fingerprints (tests)")
    ap.add_argument("passthrough", nargs="*",
                    help="extra gauntlet_stats arguments after --")
    a = ap.parse_args(argv)
    passthrough, why = check_passthrough(a.passthrough)
    if passthrough is None:
        print(f"REFUSED: {why}; a rehearsal never chains and its registry, "
              f"data, sim cache, logs and report paths are its own", flush=True)
        return 2
    if a.registry.resolve() == (LAYER / "registry_log.jsonl").resolve():
        print(f"REFUSED: {a.registry} is this layer's own chain", flush=True)
        return 2
    outs = [a.registry, a.scratch] + ([a.dump_aligned] if a.dump_aligned else [])
    for p in outs:
        if _live_layout(p):
            print(f"REFUSED: {p} is inside a research-layer tree "
                  f"(a registry_log.d beside it): use a scratch directory",
                  flush=True)
            return 2
    logs_dir, report = a.scratch / "logs", a.scratch / "report.md"
    logs_dir.mkdir(parents=True, exist_ok=True)

    fp_before = None if a.no_fingerprint else {
        "data": fingerprint(a.data_dir), "simcache": fingerprint(a.simcache_dir)}
    sampler = Sampler(a.sample_ms / 1000.0)
    captured: dict = {}
    payloads: list[dict] = []
    writes: list[str] = []

    real = {n: getattr(simcache.SimCache, n) for n in WRITE_PATHS}
    real_crbc = gauntlet_stats.cluster_registry_by_cell
    real_vs = gauntlet_stats.verdict_stats
    real_align = gauntlet._align_for_trials
    real_cut = cluster_cells.cut_partition

    def refusing(name):
        f = _refuse(name)

        def g(self, *x, **k):
            writes.append(name)
            return f(self, *x, **k)
        return g

    def align(*x, **k):
        out = real_align(*x, **k)
        sampler.phase("after_align", n=len(out[0]), L=out[2])
        return out

    def cut(returns_by_id):
        sampler.phase("cut_partition", n=len(returns_by_id))
        return real_cut(returns_by_id)

    def crbc(dated, eq_len, specs):
        captured["private_before_cluster"] = _private()
        captured["gate_days_bound"] = gauntlet_stats.cluster_days_bound(dated)
        sampler.phase("cluster_start")
        sampler.start()
        t0 = time.perf_counter()
        try:
            res = real_crbc(dated, eq_len, specs)
        finally:
            sampler.stop()
        captured["cluster_wall_s"] = time.perf_counter() - t0
        captured["clustered"] = res
        captured["key_of"] = {s["strategy_id"]: cluster_cells.partition_key(s)
                              for s in specs if s["strategy_id"] in res["returns_by_id"]}
        return res

    def vs(sid, dsr_rets, *x, **k):
        out = real_vs(sid, dsr_rets, *x, **k)
        payloads.append({"strategy_id": sid, **out})
        return out

    for n in WRITE_PATHS:
        setattr(simcache.SimCache, n, refusing(n))
    gauntlet_stats.cluster_registry_by_cell = crbc
    gauntlet_stats.verdict_stats = vs
    gauntlet._align_for_trials = align
    cluster_cells.cut_partition = cut
    argv_run = ["--registry", str(a.registry), "--data-dir", str(a.data_dir),
                "--simcache-dir", str(a.simcache_dir), "--logs-dir", str(logs_dir),
                "--report", str(report), *passthrough]
    t0 = time.perf_counter()
    rc, refused = None, None
    try:
        rc = gauntlet_stats.run(argv_run)
    except SimCacheWriteRefused as exc:
        refused = str(exc)
    finally:
        wall_s = time.perf_counter() - t0
        mem_end = memory_counters()
        for n, f in real.items():
            setattr(simcache.SimCache, n, f)
        gauntlet_stats.cluster_registry_by_cell = real_crbc
        gauntlet_stats.verdict_stats = real_vs
        gauntlet._align_for_trials = real_align
        cluster_cells.cut_partition = real_cut

    summary: dict = {"argv": argv_run, "rc": rc, "wall_s": round(wall_s, 1),
                     "simcache_write_attempts": writes, "simcache_refused": refused,
                     "pipeline_file": gauntlet_stats.__file__,
                     "memory_end": mem_end}
    try:
        summary["status"] = json.loads(
            (logs_dir / gauntlet_stats.STATUS_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        summary["status"] = None
    res = captured.get("clustered")
    if res is not None:
        before = captured["private_before_cluster"]
        n, L = len(res["returns_by_id"]), res["trials_common_days"]
        n_max = res["largest_partition_n"]
        peak = max(p["max"] for p in sampler.phases)
        parts = [p for p in sampler.phases if p["phase"] == "cut_partition"]
        biggest = max(parts, key=lambda p: (p["n"], p["max"] - p["start"]),
                      default=None)
        after_align = next((p for p in sampler.phases
                            if p["phase"] == "after_align"), None)
        summary["cluster"] = {
            "n": n, "L": L, "n_max": n_max,
            "n_max_partition": max(res["by_partition"].items(),
                                   key=lambda kv: (kv[1]["n"], kv[0]))[0],
            "partitions": res["partitions"], "trials_n_raw": res["trials_n"],
            "trials_sr_var": res["trials_var"], "late_merges": res["late_merges"],
            "sum_n2": res["sum_n2"], "trials_alignment": res["trials_alignment"],
            "never_traded": sum(b["never_traded"] for b in res["by_partition"].values()),
            "wall_s": round(captured["cluster_wall_s"], 2),
            "gate_days_bound": captured["gate_days_bound"],
            "private_before_mb": round(before / MB, 1),
            "private_max_mb": round(peak / MB, 1),
            "increment_mb": round((peak - before) / MB, 1),
            "align_increment_mb": (round((after_align["start"] - before) / MB, 1)
                                   if after_align else None),
            "align_bytes_per_nl": (round((after_align["start"] - before) / (n * L), 3)
                                   if after_align and n and L else None),
            "largest_cut": (None if biggest is None else {
                "n": biggest["n"],
                "increment_mb": round((biggest["max"] - biggest["start"]) / MB, 1)}),
            "max_cut_increment_mb": round(max(
                (p["max"] - p["start"] for p in parts), default=0) / MB, 1),
            "need_mb_actual_L": gauntlet_stats.cluster_need_mb(n, n_max, L or 0),
            "need_mb_gate_bound": gauntlet_stats.cluster_need_mb(
                n, n_max, captured["gate_days_bound"]),
            "by_partition": res["by_partition"],
        }
        mom = {}
        for sid, r in res["returns_by_id"].items():
            rl = gauntlet._as_list(r)       # what verdict_stats reads
            _, _, sk, ku = moments(rl)
            mom[sid] = [sharpe(rl), len(rl), sk, ku]
        summary["moments"] = mom
        if a.dump_aligned is not None:
            import numpy as np
            ids = sorted(res["returns_by_id"])
            np.savez(a.dump_aligned, ids=np.array(ids),
                     keys=np.array([captured["key_of"][i] for i in ids]),
                     X=np.asarray([res["returns_by_id"][i] for i in ids],
                                  dtype=np.float64))
    summary["payloads"] = payloads
    if fp_before is not None:
        fp_after = {"data": fingerprint(a.data_dir),
                    "simcache": fingerprint(a.simcache_dir)}
        summary["fingerprint_before"], summary["fingerprint_after"] = fp_before, fp_after
        summary["fingerprint_match"] = fp_before == fp_after
    (a.scratch / "rehearsal.json").write_text(json.dumps(summary, indent=1),
                                              encoding="utf-8")
    print(f"stats_rehearsal: rc {rc}, wall {wall_s:.1f}s, peak private "
          f"{(mem_end or {}).get('PeakPagefileUsage', 0) / MB:.0f} MB, simcache "
          f"writes attempted {len(writes)}"
          + ("" if fp_before is None else
             f", fingerprints {'MATCH' if summary['fingerprint_match'] else 'DIFFER'}"),
          flush=True)
    if refused is not None or writes:
        print(f"ABORTED: a sim cache write was attempted ({refused or writes}); "
              f"nothing was written to it. Stop and report.", flush=True)
        return 2
    if fp_before is not None and not summary["fingerprint_match"]:
        print("WARNING: a data or sim cache fingerprint changed during the run "
              "(another process may have written it); read rehearsal.json",
              flush=True)
        return 3
    return rc


if __name__ == "__main__":
    sys.exit(main())
