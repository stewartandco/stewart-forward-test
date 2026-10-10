"""Intraday rehearsal for Morpheus 2b-1: register 1d and 1h "twins" of real
crypto strategies on a COPY of the chain, run the real screen and gauntlet
worker on the copy, then read the outcomes back side by side.

It only writes to the paths it is given (the COPY registry, the twins file and
the report); it never touches the live tree. `register` is the one command
that appends to a chain, so it refuses to run on anything but the path the
caller names with --registry.

    python -m tools.intraday_rehearsal register --registry COPY \
        --assets BTCUSDT,ETHUSDT --originals 6 --seed 1 --out twins.json
    python -m tools.intraday_rehearsal report --registry COPY \
        --twins twins.json --artifacts-dir ART --out report.md
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

LAYER = Path.cwd()
if str(LAYER) not in sys.path:
    sys.path.insert(0, str(LAYER))

from pipeline.common import content_id                      # noqa: E402
from pipeline.registry import Registry                      # noqa: E402

RUN_ID = "rehearsal-2b1"
TIMEFRAMES = ("1d", "1h")


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
             out: Path) -> dict:
    reg = Registry(registry)
    specs = {e["payload"]["strategy_id"]: e["payload"]
             for e in reg.entries() if e["entry_type"] == "strategy_registered"}
    originals = pick_originals(specs, n, seed)
    if len(originals) < n:
        raise SystemExit(f"only {len(originals)} eligible originals, wanted {n}")
    twins: dict[str, dict] = {}
    for sid in originals:
        for asset in assets:
            for tf in TIMEFRAMES:
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
        for tf in TIMEFRAMES:
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.intraday_rehearsal")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("register")
    r.add_argument("--registry", type=Path, required=True)
    r.add_argument("--assets", required=True)
    r.add_argument("--originals", type=int, default=6)
    r.add_argument("--seed", type=int, default=20261010)
    r.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("report")
    p.add_argument("--registry", type=Path, required=True)
    p.add_argument("--twins", type=Path, required=True)
    p.add_argument("--artifacts-dir", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    if a.cmd == "register":
        twins = register(a.registry, [x for x in a.assets.split(",") if x],
                         a.originals, a.seed, a.out)
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
