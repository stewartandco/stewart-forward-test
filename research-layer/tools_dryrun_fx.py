"""tools_dryrun_fx.py -- Track-1 ship-bar step 3 harness (spec s8, step 3):
"Dry-run generation on the track's cells only: full compose->screen->gauntlet
pass, no API budget, nothing registered to the chain, results to a throwaway
dir; reviewed." Plan: docs/plans/2026-08-24-sp4-track1-fx.md Task 6.

This is a TOOL, not a pipeline module (like sweep_measure.py, verify_registry.py
at the layer root) -- runnable directly with `python tools_dryrun_fx.py
--workdir <dir>`, never imported by pipeline/ code.

What it does, in order:
  1. Refuses, LOUDLY, before touching anything, if the throwaway registry
     path resolves (realpath) to the live chain (research-layer/
     registry_log.jsonl). This check runs even when --registry was left at
     its default -- the default is always a fresh path under --workdir, so
     the only way to hit this refusal is an explicit, wrong --registry.
  2. Refuses if the real fx snapshot (Task 2's pinned adapter output,
     research-layer/data/<ID>_1d.csv + tradfi_snapshot_manifest.json) is not
     present, naming `python -m pipeline.tradfi_data snapshot --classes fx`
     as the fix. Nothing is created before this check either.
  3. Seeds a FRESH throwaway registry under --workdir with 3 fx-tagged
     accepted cards + 1 crypto-tagged accepted card (register_card +
     review_card, the same two-call pattern every seeded-registry test
     fixture in this package uses).
  4. Composes: composer.run(["--asset-class", "fx", ...], propose_fn=...)
     with a BUILT-IN fixture family (ma_cross_dense entry sweep, pct_stop,
     r_multiple target, fixed_fraction risk) citing the seeded fx cards --
     no API call, ever (propose_fn bypasses propose_families entirely, so
     no budget meter call happens). NOT --dry-run: the specs REGISTER to the
     throwaway chain, because screen and gauntlet need real chain state to
     walk, exactly as the plan specifies.
  5. Chains the screen and gauntlet protocol notes on the THROWAWAY registry
     only (screen.PROTOCOL / gauntlet.PROTOCOL), then runs screen.run() then
     gauntlet.run() for real (not --dry-run, same reason as step 4) against
     the throwaway registry and the REAL data/ directory -- fx snapshot CSVs
     and crypto CSVs alike, read-only.
  6. Prints a summary table and exits 0 only if every stage ran AND at least
     one spec reached a gauntlet verdict; otherwise exits nonzero with a
     named reason.

Nothing this script does ever writes to research-layer/registry_log.jsonl,
research-layer/artifacts/, or research-layer/logs/ -- every write lands under
--workdir. Nothing this script does spends Anthropic API budget.

Usage:
    python tools_dryrun_fx.py --workdir /path/to/scratch
    python tools_dryrun_fx.py --workdir /path/to/scratch --data-dir data --cutoff 2023-12-31
"""
from __future__ import annotations

import sys
import argparse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# Runnable from any cwd, exactly like verify_registry.py / sweep_measure.py.
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:                    # idempotent under re-import
    sys.path.insert(0, _HERE)

from pipeline import cells                                    # noqa: E402
from pipeline import composer                                  # noqa: E402
from pipeline import screen                                    # noqa: E402
from pipeline import gauntlet                                  # noqa: E402
from pipeline.reader import build_card                         # noqa: E402
from pipeline.registry import Registry                         # noqa: E402

LAYER_ROOT = Path(__file__).resolve().parent
LIVE_REGISTRY = LAYER_ROOT / "registry_log.jsonl"
DEFAULT_DATA_DIR = LAYER_ROOT / "data"
SNAPSHOT_COMMAND = "python -m pipeline.tradfi_data snapshot --classes fx"
RUN_ID = "dryrun-fx"

REFUSED_LIVE_CHAIN = 2
REFUSED_NO_SNAPSHOT = 3
FAILED_SHIP_BAR = 1


# ---------------------------------------------------------------- fixtures --

def _fixture_source_meta() -> dict:
    return {"type": "paper", "title": "Dry-run fixture source (tools_dryrun_fx)",
            "authors": ["harness"], "year": 2026,
            "url": "https://example.org/dryrun-fx", "doi": None, "isbn": None,
            "credibility_tier": "practitioner"}


def _fixture_card(claim: str, quote: str, asset_classes: list[str]) -> dict:
    raw = {"claim": claim, "quote": quote, "locator": "sec 1",
           "asset_classes": asset_classes, "topics": ["trend"],
           "horizon": "swing", "testability_score": 0.8,
           "data_required": ["daily OHLCV"], "notes": None}
    return build_card(raw, _fixture_source_meta(), "dryrun-fixture", RUN_ID)


# Three DISTINCT fx-tagged cards (distinct claim/quote -> distinct card_id)
# plus one crypto-tagged card, seeded so the fx routing table (composer.
# ROUTING["fx"] = ("fx", "cross")) has something real to exclude: the crypto
# card is accepted but never cited, proving routing rather than merely
# declaring it.
FX_CLAIMS = [
    ("Daily fx fixing series show short-horizon trend continuation after a "
     "moving-average crossover.",
     "moving average crossovers on daily fx fixing series show continuation "
     "over the following weeks"),
    ("FX pairs with a persistent fast/slow moving-average gap tend to keep "
     "that gap for several more fixes.",
     "the fast/slow moving average gap persists across subsequent daily fixes"),
    ("Percentage stops sized off the fixing series avoid the false intrabar "
     "stop-outs that a degenerate single-fix range would otherwise create.",
     "a percentage stop avoids spurious intrabar triggers on single-fix series"),
]
CRYPTO_CLAIM = ("Crypto trend strategies are the incumbent, unrestricted "
                "card feed and must stay excluded from the fx family here.",
                "the incumbent crypto card feed remains unrestricted")


def seed_cards(registry: Registry) -> tuple[list[str], str]:
    """Register + review 3 fx-tagged + 1 crypto-tagged card, all accepted.

    Mirrors the seeded_registry()/`_register_accepted()` pattern used by
    pipeline/test_composer.py and pipeline/test_composer_fx.py: register with
    review.status="pending" (build_card's default), then review_card(...,
    "accepted", ...). Returns (fx_card_ids, crypto_card_id).
    """
    fx_ids = []
    for claim, quote in FX_CLAIMS:
        card = _fixture_card(claim, quote, ["fx"])
        registry.register_card(card)
        registry.review_card(card["card_id"], "accepted", "dryrun-fx-harness")
        fx_ids.append(card["card_id"])
    crypto_card = _fixture_card(CRYPTO_CLAIM[0], CRYPTO_CLAIM[1], ["crypto"])
    registry.register_card(crypto_card)
    registry.review_card(crypto_card["card_id"], "accepted", "dryrun-fx-harness")
    return fx_ids, crypto_card["card_id"]


def fixture_family(fx_card_ids: list[str]) -> dict:
    """ma_cross_dense entry (3-point contiguous sweep on `fast`), pct_stop
    (the one stop RANGE_REQUIRING does not exclude on single-fix bars),
    r_multiple target, fixed_fraction risk -- the exact shape spec s10.7
    prescribes for fx families and the shape pipeline/test_composer_fx.py's
    fx_family() fixture proves valid against validate_family. Reimplemented
    here (not imported) so this operational tool has no dependency on any
    test module.

    "assets" is vestigial: validate_family's ALLOWED_ASSETS check is
    crypto-only (composer.py, unchanged scope); the real per-cell assets for
    an --asset-class fx run come from cells.class_cells("fx") inside
    expand_family_for_class.
    """
    return {
        "family": "dryrun_fx_trend",
        "rationale": "Dry-run fixture: MA-cross trend continuation on fx daily fixes.",
        "regime_hypothesis": ("Trending fx pairs continue after a fast/slow "
                               "crossover on their daily fix; pct_stop is used "
                               "in place of a range-based stop because single-"
                               "fix bars carry no real intrabar high/low."),
        "card_ids": list(fx_card_ids),
        "assets": ["BTCUSD"],
        "blocks": [
            {"role": "entry", "type": "ma_cross_dense",
             "params": {"fast": 13, "slow": 50, "direction": "long"}},
            {"role": "stop", "type": "pct_stop", "params": {"pct": 0.05}},
            {"role": "target", "type": "r_multiple", "params": {"r": 1.5}},
            {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}},
        ],
        # contiguous on ma_cross_dense.fast's grid [5, 8, 13, 20, 34]
        # (indices 1-3) so plateau bookkeeping has a neighbour either side.
        "sweep": [{"block": 0, "param": "fast", "values": [8, 13, 20]}],
    }


# ------------------------------------------------------------ guard rails --

def refuse_if_live_chain(registry_path: Path) -> str | None:
    """None if safe; otherwise the loud refusal message. Realpath compare so
    a symlink or a relative `../registry_log.jsonl` cannot sneak past a
    literal string comparison."""
    if registry_path.resolve() == LIVE_REGISTRY.resolve():
        return (f"REFUSED: --registry resolves to the LIVE chain "
                f"({LIVE_REGISTRY}). This harness never writes there -- "
                f"pass a --registry path under --workdir, or omit --registry "
                f"and let the default (a fresh path under --workdir) apply.")
    return None


def refuse_if_no_snapshot(data_dir: Path) -> str | None:
    """None if the Task-2 fx snapshot looks present and complete; otherwise
    the loud refusal message naming the exact command to fix it."""
    manifest = data_dir / "tradfi_snapshot_manifest.json"
    eur_csv = data_dir / "EUR_1d.csv"
    missing_assets = [a for a in cells.FX_ASSETS
                      if not (data_dir / f"{a}_1d.csv").exists()]
    if not eur_csv.exists() or not manifest.exists() or missing_assets:
        detail = f"; also missing: {', '.join(missing_assets)}" if (
            missing_assets and eur_csv.exists()) else ""
        return (f"REFUSED: no fx snapshot at {data_dir} "
                f"(EUR_1d.csv exists={eur_csv.exists()}, "
                f"tradfi_snapshot_manifest.json exists={manifest.exists()}"
                f"{detail}). Run `{SNAPSHOT_COMMAND}` first (Task 2's pinned "
                f"adapter), then re-run this harness.")
    return None


# --------------------------------------------------------------- summary --

def _new_entries(registry_path: Path, since: int) -> tuple[list[dict], int]:
    """Entries appended since the `since`-th entry (0-indexed count), and the
    new total count -- used to attribute entries to the stage that wrote them
    without needing entry_type to carry a stage tag of its own."""
    all_entries = list(Registry(registry_path).entries())
    return all_entries[since:], len(all_entries)


def _graveyard_reasons(entries: list[dict]) -> Counter:
    return Counter(e["payload"]["reason"] for e in entries
                   if e["entry_type"] == "state_change"
                   and e["payload"]["to"] == "graveyard")


def print_summary(compose_n: int, screen_entries: list[dict],
                  gauntlet_entries: list[dict]) -> dict:
    screen_verdicts = [e for e in screen_entries
                       if e["entry_type"] == "verdict"
                       and e["payload"]["stage"] == "screened"]
    screen_pass = sum(1 for e in screen_verdicts if e["payload"]["verdict"] == "pass")
    screen_buried = _graveyard_reasons(screen_entries)

    gauntlet_verdicts = [e for e in gauntlet_entries
                         if e["entry_type"] == "verdict"
                         and e["payload"]["stage"] == "gauntlet"]
    gauntlet_pass = sum(1 for e in gauntlet_verdicts if e["payload"]["verdict"] == "pass")
    gauntlet_buried = _graveyard_reasons(gauntlet_entries)
    quarantine_n = sum(1 for e in gauntlet_entries
                       if e["entry_type"] == "state_change"
                       and e["payload"]["to"] == "quarantine")

    era_present = sum(1 for e in gauntlet_verdicts
                      if "era_summary" in e["payload"]["metrics"])
    alignments = sorted({e["payload"]["metrics"].get("trials_alignment")
                         for e in gauntlet_verdicts})

    print("\n=== FX dry-run summary (run-id " + RUN_ID + ") ===")
    print(f"specs composed:      {compose_n}")
    print(f"screened:             {len(screen_verdicts)}  "
          f"(pass={screen_pass}, fail={len(screen_verdicts) - screen_pass})")
    print(f"  buried by reason:   {dict(screen_buried) or '{}'}")
    print(f"gauntlet verdicts:    {len(gauntlet_verdicts)}  "
          f"(pass={gauntlet_pass}, fail={len(gauntlet_verdicts) - gauntlet_pass})")
    print(f"  buried by gate:     {dict(gauntlet_buried) or '{}'}")
    print(f"  -> quarantine:      {quarantine_n}")
    print(f"era_summary present: {era_present} / {len(gauntlet_verdicts)} "
          f"gauntlet verdict(s)")
    print(f"trials_alignment:     {alignments}")

    return {"n_composed": compose_n, "n_screened": len(screen_verdicts),
            "n_screen_pass": screen_pass, "n_gauntlet_verdicts": len(gauntlet_verdicts),
            "n_gauntlet_pass": gauntlet_pass, "n_quarantine": quarantine_n,
            "era_summary_present": era_present, "trials_alignment": alignments}


# --------------------------------------------------------------------- run --

def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="tools_dryrun_fx.py",
        description=(
            "Track-1 ship-bar step 3 harness (spec s8, step 3): a fixture-"
            "driven fx dry-run generation -- compose -> screen -> gauntlet -- "
            "on a THROWAWAY registry under --workdir. It never touches the "
            "live chain (research-layer/registry_log.jsonl), never spends "
            "Anthropic API budget (propose_fn is injected), and never writes "
            "to research-layer/artifacts or research-layer/logs. It DOES "
            "read the real research-layer/data snapshot written by "
            f"`{SNAPSHOT_COMMAND}` (refused loudly if absent)."))
    ap.add_argument("--workdir", required=True, type=Path,
                    help="scratch directory; a fresh timestamped subfolder "
                         "is created under it for this run's throwaway "
                         "registry, artifacts and logs")
    ap.add_argument("--registry", type=Path, default=None,
                    help="override the throwaway registry path (default: a "
                         "fresh path under --workdir). Refused if it "
                         "resolves to the live chain.")
    ap.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR,
                    help="real research-layer data/ directory to READ (never "
                         "written); default: the layer's own data/")
    ap.add_argument("--cutoff", default=None,
                    help="train/OOS cutoff passed to screen and gauntlet "
                         "(default: each stage's own DEFAULT_CUTOFF)")
    args = ap.parse_args(argv)

    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = args.workdir / f"dryrun-fx-{run_stamp}"
    registry_path = args.registry if args.registry is not None else (
        run_dir / "registry_log.jsonl")

    # Guard 1 (FIRST act): live-chain collision, before any filesystem write.
    refusal = refuse_if_live_chain(registry_path)
    if refusal:
        print(refusal, file=sys.stderr)
        return REFUSED_LIVE_CHAIN

    # Guard 2: the fx snapshot must already exist (Task 2's job, not this
    # harness's). Still before any write.
    refusal = refuse_if_no_snapshot(args.data_dir)
    if refusal:
        print(refusal, file=sys.stderr)
        return REFUSED_NO_SNAPSHOT

    run_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir = run_dir / "artifacts"
    print(f"throwaway registry: {registry_path}")
    print(f"throwaway artifacts: {artifacts_dir}")
    print(f"reading data from:   {args.data_dir}  (read-only)")

    registry = Registry(registry_path)
    fx_card_ids, crypto_card_id = seed_cards(registry)
    print(f"seeded {len(fx_card_ids)} fx-tagged card(s) {fx_card_ids} + "
          f"1 crypto-tagged card {crypto_card_id!r} (accepted)")

    n0 = 0

    def propose_fn(cards):
        # cards is the routed input (fx + cross only, per composer.ROUTING);
        # the fixture ignores it and always cites the seeded fx cards, so a
        # routing bug (the crypto card leaking in) would show up as an extra
        # entry in `cards`, not as a change to what gets proposed here.
        return [fixture_family(fx_card_ids)]

    print("\n--- compose (--asset-class fx, propose_fn injected, no API call) ---")
    compose_rc = composer.run(
        ["--asset-class", "fx", "--registry", str(registry_path),
         "--run-id", RUN_ID],
        propose_fn=propose_fn)
    compose_entries, n1 = _new_entries(registry_path, n0)
    n_composed = sum(1 for e in compose_entries if e["entry_type"] == "strategy_registered")
    if compose_rc != 0 or n_composed < 1:
        print(f"\nSHIP BAR NOT MET: compose exited {compose_rc} with "
              f"{n_composed} spec(s) registered.", file=sys.stderr)
        return FAILED_SHIP_BAR

    # Protocol anchors, chained to the THROWAWAY chain only -- screen.run()
    # and gauntlet.run() both hard-refuse a real (non-dry) run without one.
    registry.append("note", {"text": f"{screen.PROTOCOL}: fx dry-run harness anchor"})
    registry.append("note", {"text": f"{gauntlet.PROTOCOL}: fx dry-run harness anchor"})
    n1 += 2

    cutoff_args = ["--cutoff", args.cutoff] if args.cutoff else []

    print("\n--- screen (real run, throwaway chain + real data/) ---")
    screen_rc = screen.run(
        ["--registry", str(registry_path), "--data-dir", str(args.data_dir),
         "--artifacts-dir", str(artifacts_dir / "screen")] + cutoff_args)
    screen_entries, n2 = _new_entries(registry_path, n1)
    if screen_rc != 0:
        print(f"\nSHIP BAR NOT MET: screen exited {screen_rc}.", file=sys.stderr)
        return FAILED_SHIP_BAR

    print("\n--- gauntlet (real run, throwaway chain + real data/) ---")
    gauntlet_rc = gauntlet.run(
        ["--registry", str(registry_path), "--data-dir", str(args.data_dir),
         "--artifacts-dir", str(artifacts_dir / "gauntlet")] + cutoff_args)
    gauntlet_entries, n3 = _new_entries(registry_path, n2)
    if gauntlet_rc != 0:
        print(f"\nSHIP BAR NOT MET: gauntlet exited {gauntlet_rc}.", file=sys.stderr)
        return FAILED_SHIP_BAR

    summary = print_summary(n_composed, screen_entries, gauntlet_entries)

    if summary["n_gauntlet_verdicts"] < 1:
        print("\nSHIP BAR NOT MET: no spec reached a gauntlet verdict "
              "(every composed spec was buried at the screen).",
              file=sys.stderr)
        return FAILED_SHIP_BAR

    print(f"\nSHIP BAR MET: compose -> screen -> gauntlet all ran, "
          f"{summary['n_gauntlet_verdicts']} spec(s) reached a gauntlet "
          f"verdict. Results left under {run_dir} for review; the live "
          f"chain was never touched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
