"""Composer agent: compose candidate strategy specs from accepted research
cards and pre-register them into the chained registry, before any results.

Usage:
    python -m pipeline.composer --max-families 8 --sibling-cap 25 \
        [--dry-run] [--model claude-opus-5] [--registry registry_log.jsonl] \
        [--run-id 2026-08-06-manual]

Two-stage: one structured-output model call proposes idea families (blocks,
cited cards, sweep axes); deterministic code validates each family against
the block grammar, expands sweeps into sibling specs sharing a
sibling_group_id, and chains block_type_registered + strategy_registered
entries. Invalid families are dropped loudly, never silently.
"""
from __future__ import annotations

import re
import sys
import copy
import json
import hashlib
import argparse
import itertools
from pathlib import Path
from datetime import datetime, timezone

from .common import content_id
from .registry import Registry
from .blocks import BLOCK_TYPES, RETIRED_TYPES, validate_block, block_type_payload
from . import cells, loop_state

PIPELINE_VERSION = "g1.0.0"
DEFAULT_MODEL = "claude-opus-5"
SIBLING_CAP_DEFAULT = 60

# D9 (docs/notes/family-openness-v1.md, chained 2026-08-29 BEFORE this code):
# how far a cell's data must have moved past a burying verdict's cutoff before
# that buried COMPOSITION may be proposed again as a new strategy. Six months,
# measured in DATA and not on the wall clock.
#
# WHY THE CLOCK RUNS ON THE DATA. The gauntlet is deterministic: given the
# same spec over the same bars it returns the same verdict. A same-data
# re-test is therefore a KNOWN ANSWER bought at the price of a higher bar for
# every live survivor -- it spends the denominator (registered_n, the
# effective-trials clustering, the BH divisor over the quarantine cohort) and
# learns nothing. A re-test after the cell's data has moved on is a different
# object: different bars, a different out-of-sample window, and a genuinely
# different answer available. Per CELL, because a cell is the unit of
# survival here.
RETRIAL_WINDOW_DAYS = 183

# protocol-v4: a family may only sweep axes on a dense block type. Mixing a
# dense axis with a coarse one manufactures fake cliffs in plateau selection —
# channel_breakout lookback 55 -> 100 is a different strategy, not a
# perturbation. Coarse types stay usable at FIXED values.
SWEEPABLE_TYPES = {("entry", "channel_breakout_dense"),
                   ("entry", "ma_cross_dense"),
                   ("entry", "trend_scan_dense"),
                   ("entry", "zscore_reversion_dense"),
                   ("stop", "atr_stop_dense"),
                   ("target", "r_multiple_dense"),
                   ("filter", "vol_percentile_dense"),
                   ("regime", "regime_ma_short_dense"),
                   # D15 exit rules v7 (2026-09-03): dense by design (every grid
                   # >= 3 contiguous values), no coarse twin, so sweepable as is.
                   ("stop", "swing_stop"), ("stop", "ma_stop"),
                   ("stop", "channel_stop"), ("stop", "band_stop"),
                   ("exit", "ma_crossunder"), ("exit", "channel_exit"),
                   ("exit", "zscore_revert"), ("exit", "tstat_decay"),
                   ("exit", "regime_flip")}
ALLOWED_ASSETS = ("BTCUSD", "ETHUSD")
UNIVERSE_BASE = {"asset_class": "crypto", "timeframe": "1d", "session": "24x7"}
COST_MODEL = {"commission_per_side": 0.001, "slippage_ticks": 0.0005}
# ^ both are FRACTIONS of notional (10 bps + 5 bps); the field name
#   slippage_ticks is inherited from spec schema v1 — see design doc.

# spec s4 (D2): a declared table of which card asset_class TAGS are eligible
# input for a cell class's family proposals -- data, not inference. Crypto's
# entry is unused today because crypto stays unrestricted (every accepted
# card feeds it, exactly as before this task); it is declared here anyway so
# a future tightening of the crypto feed has one place to change. "fx" wired
# in Track 1; "equity_etf" (Track 2a, spec s4's routing table: `equities` +
# `cross` cards) wired here. The futures proxy lane (below) is additive to
# this table, not a member of it -- a futures card is never tagged
# `equities`, so it can only reach equity_etf via INDEX_FUTURES_PROXY_TOPICS.
#
# Track 2b addendum (docs/2026-08-27-sp4-track2b-addendum.md, "Routing"):
# bond_etf routes on `rates` + `cross` per the parent spec's table -- but the
# parent table calls rates->bond_etf a PROXY (rates cards are largely about
# rate FUTURES/derivatives, not the ETFs themselves), so every family citing
# a rates-tagged card records routed_via/proxy_card_ids exactly like the
# futures->equity_etf lane (see BOND_ETF_PROXY_TAGS below and run()'s
# bond_etf branch). metal_etf routes on `commodities` + `cross` NATIVELY --
# DEVIATION from the parent table (which omitted commodities entirely),
# declared here: commodities cards about gold/silver are the closest native
# population for a metals-only class. metal_etf's own proxy lane
# (METALS_PROXY_TOPICS, below) is additive on top of this native routing,
# the same shape as the futures->equity_etf lane.
ROUTING = {"crypto": ("crypto",), "fx": ("fx", "cross"),
          "equity_etf": ("equities", "cross"),
          "bond_etf": ("rates", "cross"), "metal_etf": ("commodities", "cross")}

# bond_etf's ROUTING tags are ALL proxy (unlike equity_etf, where "equities"
# is native and only the futures lane is proxy): the parent spec's table
# calls the whole rates->bond_etf lane a proxy, because rates cards are
# overwhelmingly about rate futures/derivatives, not the cash ETFs. "cross"
# cards are asset-class-agnostic by definition, so they are NOT proxy either
# -- only cards actually tagged "rates" are. Declared as a set rather than
# inlined in run() so expand_family_for_class's proxy_card_ids parameter
# (built for the futures->equity_etf lane) can be reused verbatim: a bond_etf
# run computes its proxy set as "every routed card tagged rates", exactly
# analogous to equity_etf computing its proxy set as "every routed futures
# card matching INDEX_FUTURES_PROXY_TOPICS".
BOND_ETF_PROXY_TAGS = frozenset({"rates"})

# spec s3/s10.7: fx cells carry single-fix daily bars (open=high=low=close),
# so any block whose semantics require a real intrabar range distinct from
# close would be silently fed degenerate inputs rather than erroring. These
# types are excluded from families proposed for a non-crypto, single-fix
# class instead. Kept as a standalone module constant (not renamed/removed --
# pinned by test_composer_fx.py) even though the authoritative source for
# exclusion is now cells.CLASSES[cls]["excluded_block_types"] (T4-rider-3,
# track 2a): the fx entry there carries these exact seven values, test-pinned
# equal.
#
# D15 exit rules v7 (2026-09-03): pct_stop -- which used to be "the remaining
# stop for fx families" -- is RETIRED for version-2 specs, and the three new
# types that read highs/lows (swing_stop, channel_stop, channel_exit) join
# this set. ma_stop and band_stop are close-only and stay ALLOWED, so they are
# what an fx family stops on now; the close-based signal exits (ma_crossunder,
# zscore_revert, tstat_decay, regime_flip) stay allowed too.
RANGE_REQUIRING = {"channel_breakout", "channel_breakout_dense",
                   "atr_stop", "atr_stop_dense",
                   "swing_stop", "channel_stop", "channel_exit"}

# D15 exit rules v7 (docs/2026-09-03-exit-rules-v7-design.md s2): the
# registration marker every spec the composer builds carries from 2026-09-03.
# version 2 = engine exit-rules-v7 (no implicit crossunder exit on ma_cross*,
# no retired block types); version 1 = the legacy engine path, frozen by
# golden. The SAME block list under a different version is a different trial
# (composition_fingerprint). validate_family validates at THIS version.
SPEC_VERSION = 2

# Track 2a addendum s"Routing": the futures->equity_etf PROXY lane (spec
# s10.8). A futures-tagged card routes to equity_etf ONLY when its topics
# intersect this declared set -- data, reviewed at build time, never
# inferred from the card text at runtime.
#
# MEASURED against the live registry (research-layer/registry_log.jsonl,
# read-only scratch scan, 2026-08-24, corrected 2026-08-25 -- see below): of
# 342 futures-tagged cards, 20 name a specific index-future instrument in
# their claim text (E-mini S&P 500 / ES / VIX futures). Their topic tags were
# compared against the full futures corpus's topic frequency to find topics
# that are genuinely DISCRIMINATING for index-related content rather than
# generic quant-research topics that happen to co-occur (e.g. "market
# microstructure" and "bar sampling" appear on the majority of ALL futures
# cards regardless of instrument, because most of this corpus is
# Lopez-de-Prado-style ML/microstructure research that uses E-mini S&P
# futures as its illustrative dataset without the CLAIM itself being about
# index-level behaviour). The six below are the topics that appear ONLY
# (ratio 1.0) or almost only on the index-named subset, with at least 2 real
# occurrences each, naming an actual index-futures product (S&P 500 / ES
# futures, or the VIX futures term structure and its TVIX/contango
# consequences):
#   S&P 500 (1/1), ES futures (1/1), VIX futures (2/2),
#   VIX futures term structure (2/2), TVIX (3/4), contango (2/3)
#
# CORRECTED 2026-08-25 (track 2a review): the first pass measured card review
# status by reading the embedded review.status on card_registered payloads,
# which is always "pending" at registration time -- it does not fold in the
# later card_reviewed entries the way Registry.cards(status=...) does, so it
# wrongly reported 342/342 pending. Re-measured THROUGH Registry.cards(),
# the same join the composer itself uses to build `accepted` in run(): of
# the 342 futures-tagged cards, 216 are accepted, 42 rejected, 84 still
# pending. Intersecting the 216 accepted futures cards' topics against the
# set below finds exactly ONE match today: f3c7efcd1bb41166 (topic
# "S&P 500"). The lane is therefore not empty -- a real --asset-class
# equity_etf composer run today would proxy-route that one card (the native
# `equities`/`cross` ROUTING entry above is unaffected and unrelated).
# test_composer_equity.py pins this real match directly rather than only
# exercising a fixture-injected case (build brief 2026-08-24 asked for a
# fixture-injected positive case only if measurement found zero accepted
# matches; it did not).
INDEX_FUTURES_PROXY_TOPICS = frozenset({
    "S&P 500", "ES futures", "VIX futures",
    "VIX futures term structure", "TVIX", "contango",
})

# Track 2b addendum ("Routing"): the futures->metal_etf PROXY lane, same
# shape as INDEX_FUTURES_PROXY_TOPICS above (a futures-tagged card routes to
# metal_etf only when its topics intersect this declared set). MEASURED
# against the live registry (research-layer/registry_log.jsonl, read-only
# scan, 2026-08-27) using the SAME method as the index-futures measurement:
# first find the futures-tagged cards whose claim/quote text actually NAMES
# a specific gold/silver/precious-metals futures instrument (COMEX gold,
# GC=F, XAU, XAG, silver futures, etc. -- not just the generic word "metal"
# or "gold" used idiomatically), then compare THEIR topic tags against the
# rest of the futures corpus to find topics that are genuinely discriminating
# for metals content.
#
# Of the full futures-tagged corpus -- 342 cards in ANY review state (216
# accepted, 42 rejected, 84 pending; same 342/216/42/84 split the index-
# futures measurement used) -- searching claim+quote text for
# gold|silver|precious metal|comex|xau|xag found exactly ONE hit, and it is
# NOT a real instrument reference: card 80def723ae88331b's quote is "The gold
# standard for weights is bootstrap/MC" -- an idiom about portfolio-weight
# estimation methods, not a metals claim; its topics (portfolio optimization,
# bootstrapping, monte carlo, computational speed) confirm this. A second
# card (e92073341e7fd1e2) mentions "metals" once, generically, as one line
# item inside a futures-PCA factor-loading claim ("long ... metals and other
# commodities") with topics (PCA, USD factor, commodities, trade-related
# risk) that are about the PCA factor structure, not about any metals
# instrument specifically. Neither card names gold, silver, or any specific
# metals product. Scanning every topic tag across all 342 cards for
# gold/silver/metal/precious/comex/xau/xag also found ZERO matching topics.
#
# Unlike the index-futures case (20 real index-named cards existed to derive
# discriminating topics FROM), there is no raw candidate set here at all: the
# corpus this pipeline has ingested so far simply contains no futures-tagged
# card that is actually about a metals futures product. METALS_PROXY_TOPICS
# is therefore declared EMPTY -- an honest record of what was measured, not
# an omission (see this project's "honest zero is the product claim"
# convention) -- so a real --asset-class metal_etf composer run today
# proxy-routes NOTHING via this lane; metal_etf's native `commodities`/
# `cross` ROUTING entry above is unaffected. test_composer_2b.py exercises
# the routing MECHANISM with a monkeypatched, non-empty topic set (fixture-
# injected, per the build brief's "fixture-injected routing tests either
# way"), because the live constant being empty means no real card can ever
# demonstrate a positive match today.
METALS_PROXY_TOPICS = frozenset()


def composition_fingerprint(spec: dict) -> str:
    """Identity of a STRATEGY, not of a registration: universe and blocks
    only. Excludes strategy_id, created_utc, run_id, name, family and
    provenance, so two registrations of the same strategy collide.

    Params are snapped to their grid values HERE rather than trusting the
    caller to have done it: 2 and 2.0 must never fingerprint differently.
    This function is the only thing standing between a buried strategy and
    a fresh id, so it does not rely on caller discipline."""
    blocks = []
    for b in spec["blocks"]:
        schema = BLOCK_TYPES.get((b["role"], b["type"]), {})
        params = {}
        for p, v in b["params"].items():
            grid = schema.get(p, {}).get("grid")
            params[p] = next((g for g in grid if g == v), v) if grid else v
        blocks.append({"role": b["role"], "type": b["type"],
                       "params": dict(sorted(params.items()))})
    blocks.sort(key=lambda b: (b["role"], b["type"],
                               json.dumps(b["params"], sort_keys=True)))
    u = spec["universe"]
    core = {"assets": sorted(u["assets"]),
            "timeframe": u["timeframe"],
            "asset_class": u.get("asset_class"),
            "session": u.get("session"),
            "blocks": blocks}
    # D15 exit rules v7: the same blocks under a different engine version are
    # a different trial, so the version is part of the identity -- but it
    # enters `core` ONLY when it is not 1, so every chained version-1
    # fingerprint (6,050 registrations on 2026-09-03) is byte-for-byte
    # unchanged and D9's buried-priors lookups keep resolving.
    if spec.get("version", 1) != 1:
        core["version"] = spec["version"]
    return hashlib.sha256(
        json.dumps(core, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False).encode("utf-8")).hexdigest()


def v7_compliant_as_is(spec: dict) -> bool:
    """D15(b): a legacy (version-1) composition whose engine behaviour is
    UNCHANGED under exit-rules-v7 -- no retired block type and an entry that
    never had the implicit crossunder exit (ma_cross*) -- is compliant as
    registered and must NOT be re-registered as a version-2 trial (it would
    be a duplicate: same blocks, same behaviour, a second N). Read by the
    re-trial classification tool (plan Task 5); it decides nothing here."""
    if any((b["role"], b["type"]) in RETIRED_TYPES for b in spec["blocks"]):
        return False
    entry = next(b for b in spec["blocks"] if b["role"] == "entry")
    return not entry["type"].startswith("ma_cross")


def registered_fingerprints(registry: Registry) -> dict[str, str]:
    """{composition_fingerprint: strategy_id} over every registered strategy,
    in ANY lifecycle state — a graveyarded composition must never return."""
    out = {}
    for e in registry.entries():
        if e["entry_type"] == "strategy_registered":
            out.setdefault(composition_fingerprint(e["payload"]),
                           e["payload"]["strategy_id"])
    return out


def screen_siblings(specs: list[dict], known_fps: dict[str, str],
                    run_fps: dict[str, str],
                    retrial_ok=None) -> tuple[list[dict], list[str], bool]:
    """Rule 7 at SIBLING level -> (kept_specs, drop_notes, malformed).

    A composition already registered in ANY lifecycle state, graveyard
    included, is not re-registered — but its siblings can be. A real idea
    comes back at neighbouring parameters; one that worked only at the exact
    buried point was overfit and deserved burying. So a collision drops that
    sibling alone and the family survives on the rest.

    Intra-family duplicates are different in kind: two siblings that are the
    same composition mean duplicate blocks or mirrored sweep axes, which is a
    malformed proposal rather than a collision. malformed=True means the
    caller drops the whole family and ignores kept_specs.

    retrial_ok (D9, docs/notes/family-openness-v1.md) turns the chain
    collision from a PERMANENT exclusion into an expiry. It is a callable
    (matched_strategy_id, spec) -> bool: True means that registration's
    burying verdict is far enough behind the target cell's current data end
    (RETRIAL_WINDOW_DAYS) that the composition may be proposed again as a NEW
    strategy with a new id and its own number, entering N honestly.
    retrial_oracle() builds the real one.

    DEFAULT None = today's behaviour, byte-identical: every collision is a
    permanent drop. Every caller that predates D9 — and every test that
    exercises the resurrection guard directly — is unchanged by passing
    nothing, and a caller with no artifacts to read cannot accidentally open
    the window.

    IN-RUN AND IN-CYCLE DUPLICATES ARE NEVER RE-TRIALS and the oracle is not
    consulted for them: two siblings of one family that are the same
    composition still kill the family as malformed, and a composition
    duplicating one registered earlier in THIS run is still dropped. Those are
    same-data by construction, which is exactly what the window excludes.

    The invariant the public chain rests on: no returned spec's fingerprint is
    in run_fps, nor in known_fps unless retrial_ok admitted it explicitly."""
    fam_fps: dict[str, str] = {}
    kept_specs: list[dict] = []
    drop_notes: list[str] = []
    for spec in specs:
        fp = composition_fingerprint(spec)
        if fp in fam_fps:
            # Return on the FIRST duplicate rather than listing every one:
            # the family is already dead, and one named pair diagnoses the
            # mirrored axis better than its downstream consequences do.
            drop_notes.append(
                f"siblings {fam_fps[fp]} and {spec['strategy_id']} are the "
                f"same composition — duplicate blocks or mirrored sweep axes")
            return kept_specs, drop_notes, True
        fam_fps[fp] = spec["strategy_id"]
        # `fp not in run_fps` is LOAD-BEARING, not defensive. Without it a
        # composition admitted as a re-trial by family A is admitted AGAIN by
        # family B in the same run: B reaches this branch first and never
        # falls through to the run_fps drop below, so ONE composition chains
        # twice on identical data -- exactly the same-data re-test the whole
        # window exists to forbid. family-openness-v1: "a composition
        # duplicating one already registered earlier in the same run is still
        # dropped ... Those ARE same-data by construction."
        #
        # Ordered known_fps-first on purpose (message-preserving): a spec in
        # BOTH sets falls through to the known_fps drop below and reports the
        # pre-D9 wording, so the no-oracle path stays byte-identical.
        if (fp in known_fps and fp not in run_fps
                and retrial_ok is not None and retrial_ok(known_fps[fp], spec)):
            drop_notes.append(
                f"sibling {spec['strategy_id']} admitted as a RE-TRIAL of "
                f"buried {known_fps[fp]}: its cell's data has moved "
                f">= {RETRIAL_WINDOW_DAYS} days past that burial's cutoff "
                f"(D9) — a new strategy, a new number, N charged in full")
            kept_specs.append(spec)
        elif fp in known_fps:
            drop_notes.append(f"sibling {spec['strategy_id']} dropped: "
                              f"composition already registered as {known_fps[fp]}")
        elif fp in run_fps:
            drop_notes.append(f"sibling {spec['strategy_id']} dropped: "
                              f"composition duplicates family {run_fps[fp]} "
                              f"in this run")
        else:
            kept_specs.append(spec)
    return kept_specs, drop_notes, False


# ---------------- D9: the re-trial window ----------------

def _date10(value) -> str:
    """The DATE part of a chained/on-disk date string, or "".

    data_end can carry a time component ("2026-08-27 00:00:00") while a
    verdict cutoff is a bare date. screen.load_bars' own fence compares
    `row["date"][:10] > cutoff[:10]` for exactly this reason; the re-trial
    window uses the same rule rather than inventing a second one.
    """
    if not isinstance(value, str):
        return ""
    return value[:10]


def retrial_window_open(verdict_cutoff, cell_data_end,
                        window_days: int = RETRIAL_WINDOW_DAYS) -> bool:
    """PURE. True when this cell's CURRENT data end is at least `window_days`
    past the burying verdict's cutoff (D9).

    Boundary, pinned on both sides by test: exactly `window_days` past the
    cutoff is OPEN; one day short is SHUT.

    Anything it cannot read — a missing cutoff, an empty data end, a string
    that is not a date — is SHUT. An expiry that cannot be established is not
    an expiry, and this function's failure direction must be the conservative
    one: a composition stays buried.
    """
    a, b = _date10(verdict_cutoff), _date10(cell_data_end)
    if not a or not b:
        return False
    try:
        cutoff = datetime.strptime(a, "%Y-%m-%d").date()
        end = datetime.strptime(b, "%Y-%m-%d").date()
    except ValueError:
        return False
    return (end - cutoff).days >= window_days


# ---- the D9 admission rule itself, in ONE place -------------------------
#
# Two callers, and they are not symmetric. The composer is DECIDING, with the
# artifact bundles and the bars in front of it; verify_registry.py is CHECKING
# a chain, and the cutoff (artifacts/<sid>/gauntlet/config.json) and the data
# end (data/<cell>.csv) are not ON the chain, so it can be handed a log with
# neither. Their correct failure directions on missing evidence are therefore
# OPPOSITE: the composer must refuse ("an expiry that cannot be established is
# not an expiry"), the verifier must not condemn a chain because a bundle was
# pruned. A bool cannot carry that distinction, so the rule returns a NAMED
# verdict and each caller reads it its own way. What neither caller gets to do
# is hold its own copy of the rule.
RETRIAL_OK = "ok"
RETRIAL_SAME_RUN = "same_run"
RETRIAL_NOT_BURIED = "not_buried"
RETRIAL_WINDOW_SHUT = "window_shut"
RETRIAL_WINDOW_UNKNOWN = "window_unknown"

# Why each refusal refused, in the words of family-openness-v1, for whoever
# reads the failure rather than the code.
RETRIAL_REASONS = {
    RETRIAL_SAME_RUN: "registered twice in the same run — same data by "
                      "construction, never a re-trial",
    RETRIAL_NOT_BURIED: "the earlier registration is not buried — no burying "
                        "verdict, no expiry, permanently excluded",
    RETRIAL_WINDOW_SHUT: "the re-trial window is shut — the cell's data has "
                         f"not moved {RETRIAL_WINDOW_DAYS} days past that "
                         "burial's cutoff",
}


def retrial_verdict(priors, spec, cutoff_of, data_end_of_cell,
                    window_days: int = RETRIAL_WINDOW_DAYS) -> str:
    """PURE given its two lookups. The D9 rule (docs/notes/family-openness-v1)
    applied to ONE candidate registration of an already-registered
    composition.

    priors  -- (strategy_id, lifecycle_state, run_id) for EVERY registration
               of this composition that already exists, in chain order. Empty
               means the composition is new and there is nothing to decide.
    cutoff_of        -- strategy_id -> burying verdict cutoff, or None.
    data_end_of_cell -- (asset, timeframe) -> last bar date, or "".

    Returns one of the RETRIAL_* constants:

      SAME_RUN   two registrations of one composition inside one run. Checked
                 FIRST and unconditionally: "IN-RUN AND IN-CYCLE DUPLICATES
                 REMAIN MALFORMED AND ARE NOT RE-TRIALS ... those ARE
                 same-data by construction".
      NOT_BURIED some prior registration is in quarantine or live (or has not
                 been judged at all). EVERY prior, not the first one
                 known_fps happens to name: once a composition can be
                 registered twice, the oldest copy can be buried while a
                 LATER one sits under test, and "a second copy of it is a
                 duplicate, not a re-trial".
      WINDOW_*   the LATEST burial governs (max over the cutoffs) and the
                 OLDEST cell governs (min over the data ends): a pooled spec
                 is one trial over all its cells, so the freshest cell must
                 not carry the stalest into a re-test it has not earned.
                 UNKNOWN means a cutoff or a data end could not be read at
                 all, which is not the same claim as SHUT.
      OK         admissible as a NEW numbered trial.
    """
    if not priors:
        return RETRIAL_OK
    run_id = (spec.get("generator") or {}).get("run_id")
    if run_id and any(prior_run == run_id for _, _, prior_run in priors):
        return RETRIAL_SAME_RUN
    if any(st != "graveyard" for _, st, _ in priors):
        return RETRIAL_NOT_BURIED
    cutoffs = [cutoff_of(sid) for sid, _, _ in priors]
    if not all(cutoffs):
        return RETRIAL_WINDOW_UNKNOWN
    uni = spec.get("universe") or {}
    tf = uni.get("timeframe")
    ends = [_date10(data_end_of_cell((a, tf))) for a in uni.get("assets") or []]
    if not ends or not all(ends):
        return RETRIAL_WINDOW_UNKNOWN
    return (RETRIAL_OK if retrial_window_open(max(cutoffs), min(ends),
                                              window_days)
            else RETRIAL_WINDOW_SHUT)


def cell_data_end(data_dir, asset: str | tuple, timeframe: str | None = None) -> str:
    """The last bar date on disk for one cell, or "" when the cell has no
    cached data. Accepts either (asset, timeframe) or a single (a, tf) tuple.

    Deliberately reads the CSV rather than any snapshot manifest: the manifest
    records what a fetch claimed, and the re-trial window has to run on the
    bars a gauntlet would actually be given. "" (unreadable/absent) closes the
    window, via retrial_window_open.
    """
    if timeframe is None:
        asset, timeframe = asset
    path = Path(data_dir) / f"{asset}_{timeframe}.csv"
    last = ""
    try:
        with path.open("r", encoding="utf-8") as f:
            header = f.readline()
            if not header:
                return ""
            for line in f:
                if line.strip():
                    last = line.split(",", 1)[0]
    # UnicodeDecodeError is a ValueError, NOT an OSError, so one non-UTF-8
    # byte anywhere in the file used to escape this handler. It reaches the
    # read calls above, not just open(), and it must be caught: this function
    # is on verify_registry.py's pre-spend path, where an uncaught exception
    # is a traceback, exit 1, chain_invalid and a stopped pipeline -- over a
    # corrupt CSV rather than anything on the chain.
    except (OSError, UnicodeDecodeError):
        return ""
    return _date10(last)


def buried_from(registry: Registry) -> dict[str, str]:
    """{strategy_id: the lifecycle state it was buried FROM} for every
    strategy CURRENTLY in the graveyard.

    Two facts, deliberately taken from two places. Membership comes from
    strategy_states() (the state machine's own answer to "is this buried
    now"), while the FROM state comes from the burying state_change entry's
    `buried_at` field, because that is what says which stage's verdict — and
    therefore which artifact bundle's cutoff — did the burying.
    """
    states = registry.strategy_states()
    out: dict[str, str] = {}
    for e in registry.entries():
        if e["entry_type"] != "state_change":
            continue
        p = e["payload"]
        if p.get("to") == "graveyard" and states.get(p["strategy_id"]) == "graveyard":
            out[p["strategy_id"]] = p.get("buried_at") or p.get("from") or ""
    return out


def burying_cutoff(artifacts_dir, strategy_id: str, buried_at: str) -> str | None:
    """The cutoff of the verdict that buried this strategy, or None.

    The cutoff is not on the chain — record_verdict's payload carries
    strategy_id/stage/verdict/metrics/artifacts_hash and no window — so it is
    read from the artifact bundle the verdict hashes: `gauntlet/config.json`
    for a strategy buried out of the gauntlet, the screen bundle's
    `config.json` otherwise. Both writers put "cutoff" at the top level.

    None on anything unreadable (no bundle, corrupt JSON, no cutoff key),
    which closes the window. That is also why no tmp-registry test in the
    suite is disturbed by D9: those registries have no artifact bundles, so
    no window ever opens for them.
    """
    root = Path(artifacts_dir) / strategy_id
    candidates = ([root / "gauntlet" / "config.json", root / "config.json"]
                  if buried_at == "gauntlet"
                  else [root / "config.json", root / "gauntlet" / "config.json"])
    for path in candidates:
        try:
            cutoff = json.loads(path.read_text(encoding="utf-8")).get("cutoff")
        # UnicodeDecodeError subclasses ValueError, not OSError and not
        # JSONDecodeError, so a single non-UTF-8 byte in a bundle used to
        # escape all three and propagate. Same reason as cell_data_end: this
        # runs inside the loop's pre-spend chain gate.
        except (OSError, UnicodeDecodeError, json.JSONDecodeError,
                AttributeError):
            continue
        if isinstance(cutoff, str) and cutoff:
            return cutoff
    return None


def retrial_oracle(registry: Registry, artifacts_dir, data_end_of_cell=None,
                   data_dir=None):
    """Build screen_siblings' `retrial_ok` callable for one run.

    (matched_strategy_id, candidate_spec) -> bool. True only when ALL of:
      * the matched registration is CURRENTLY BURIED. A registration in
        quarantine or live has NO burying verdict, therefore no expiry, and
        stays permanently excluded — family-openness-v1 declares that case
        explicitly, and declares it as a TIGHTENING: the strategy is under
        test and a second copy of it is a duplicate, not a re-trial;
      * that burial's verdict cutoff is readable;
      * every cell the candidate spec names has data ending at least
        RETRIAL_WINDOW_DAYS past that cutoff. EVERY cell, not any: a pooled
        multi-asset spec is one trial over all its cells, so the newest cell
        must not carry the oldest one into a re-test the oldest has not
        earned. min() over the cells' data ends is that rule.

    `data_end_of_cell` is a ((asset, timeframe) -> date string) callable,
    injected so the window can be tested without a data directory; it defaults
    to reading `data_dir` off disk.
    """
    if data_end_of_cell is None:
        if data_dir is None:
            raise ValueError("retrial_oracle needs data_end_of_cell or data_dir")
        data_end_of_cell = lambda cell: cell_data_end(data_dir, cell)   # noqa: E731
    buried = buried_from(registry)
    # EVERY registration of a composition, not just the one known_fps happens
    # to name. registered_fingerprints() collapses a fingerprint to its FIRST
    # registering id, which was lossless while a fingerprint could only ever
    # be registered once -- D9 is precisely what ends that. Once a re-trial
    # exists, the oldest registration of a composition can be buried while a
    # LATER copy of it sits in quarantine, and opening the window off the old
    # burial alone would readmit a composition that is currently under test.
    # family-openness-v1 forbids exactly that: "a second copy of it is a
    # duplicate, not a re-trial".
    fingerprint_ids: dict[str, list[str]] = {}
    run_of: dict[str, str] = {}
    for e in registry.entries():
        if e["entry_type"] == "strategy_registered":
            p = e["payload"]
            fingerprint_ids.setdefault(
                composition_fingerprint(p), []).append(p["strategy_id"])
            run_of[p["strategy_id"]] = (p.get("generator") or {}).get("run_id") or ""
    states = registry.strategy_states()
    cutoffs: dict[str, str | None] = {}

    def _cutoff(sid: str) -> str | None:
        if sid not in cutoffs:
            # buried_from() is the membership answer; a sid retrial_verdict
            # asks about is buried by then, but .get keeps a caller that
            # reaches here another way from raising.
            cutoffs[sid] = burying_cutoff(artifacts_dir, sid, buried.get(sid, ""))
        return cutoffs[sid]

    def _ok(strategy_id: str, spec: dict) -> bool:
        sids = fingerprint_ids.get(composition_fingerprint(spec)) or [strategy_id]
        priors = [(s, states.get(s, "proposed"), run_of.get(s, "")) for s in sids]
        # ONE reading of the rule, shared with verify_registry.py. The oracle
        # is DECIDING, so anything short of OK — including a window it merely
        # cannot establish — is a refusal.
        return retrial_verdict(priors, spec, _cutoff, data_end_of_cell) == RETRIAL_OK

    return _ok


# ---------------- D10: caps become queues ----------------

def split_for_cycle(specs: list[dict], cap: int | None) -> tuple[list[dict], list[dict]]:
    """(this_cycle, queued) — split-and-carry, replacing a refusal.

    family-openness-v1: a family whose sweep exceeds the per-cycle sibling
    bound registers the first window NOW and QUEUES the remainder, draining
    across subsequent cycles until every proposed variation has been tested.
    The invariant, checkable rather than aspirational: `this_cycle + queued`
    is `specs`, in order, always — no proposed variation is ever dropped
    without either a gauntlet verdict or a queue entry.

    cap None or <= 0 means NO per-cycle bound, and returns everything. That is
    the manual-run case: a hand invocation with nowhere to persist a queue
    must register the whole family rather than discard its tail, because a
    dropped tail is exactly the outcome D10 exists to remove.

    ================= KNOWN HARM, DECLARED, NOT FIXED =================
    A SPLIT SWEEP CAN MANUFACTURE `edge_of_grid` PLATEAU FAILURES AT THE CUT
    (P2-T4 review F5). plateau.qualifies fails a sibling when a one-grid-step
    neighbour is not registered, and it reads what is ON THE CHAIN when the
    gauntlet runs. In the cycle that splits a family, the queued combos are
    not on the chain yet, so the siblings adjacent to the cut can be failed
    for a capacity reason wearing a statistical costume -- which is precisely
    what family-openness-v1 condemns, one layer further down than the refusal
    this function replaced. The queued half draining later does NOT repair it:
    those verdicts are already written, and verdicts are never re-judged.

    WHY IT IS NOT FIXED HERE. No PARTITION of a sweep avoids it: a sweep is a
    cartesian product over one or more axes, and no partition leaves every
    axis's adjacency intact -- with a single axis the product IS the axis, so
    any cut severs it. Cutting on the outermost axis's boundary (the obvious
    "clean" fix) preserves the inner axes' neighbourhoods but still severs the
    outer one, AND makes the window size vary with the family's shape -- a
    60-cap silently becoming 45 or 75 -- which trades a visible harm for a
    hidden one.

    THAT SEARCH WAS EXHAUSTIVE ONLY OVER PARTITIONS, and the reader should not
    take it for more (P2-T4 re-review). Every option above is a way to CUT the
    sweep; the interesting alternative changes WHEN THE GROUP IS JUDGED
    instead. Three live options, all Coen's call, none of them Phase 2 work:

      1. Accept it, as shipped: the split registers now and the cut siblings
         may draw an edge_of_grid verdict they did not earn.
      2. Queue at FAMILY granularity rather than sibling granularity, so no
         sweep is ever cut. The chained note's own wording forecloses this:
         "a family whose sweep exceeds the per-cycle sibling bound registers
         the first window now and QUEUES the remainder".
      3. HOLD THE SIBLING GROUP OUT OF THE GAUNTLET until its queue drains, so
         no verdict is ever written against a partial neighbourhood. This
         costs LATENCY rather than correctness, and -- unlike (2) -- it does
         not touch the note at all: the note governs the COMPOSER's admission
         rules, not the gauntlet's batching. It is the only option that keeps
         both the note's wording and the plateau neighbourhood intact.

    Nothing subtle was invented here; (3) is named so the next reader knows
    the question is open rather than closed.

    SCOPE, measured: this can only bite a family whose sweep exceeds the cap,
    i.e. one that TODAY is refused outright and registers nothing at all. No
    currently-reachable behaviour regresses. It is on the list for Coen.
    """
    if cap is None or cap <= 0 or len(specs) <= cap:
        return list(specs), []
    return list(specs[:cap]), list(specs[cap:])


def validate_family(fam: dict, accepted_ids: set[str], sibling_cap: int,
                    excluded_types: frozenset[str] = frozenset(),
                    asset_class: str = "crypto") -> list[str]:
    """Return error strings; empty = family is expandable.

    excluded_types (spec s10.7): block types banned for the family's target
    class -- empty by default so every existing crypto caller is unchanged.
    fx families are validated with excluded_types=RANGE_REQUIRING.

    D15 exit rules v7: every block is validated at SPEC_VERSION, so a retired
    type (blocks.RETIRED_TYPES: exit/time_stop, stop/pct_stop) is an error
    here -- one error per retired block, from validate_block, which is the
    single place the retirement rule lives. An `exit` block may appear zero
    or many times (signal exits are optional and additive); the role checks
    below are unchanged.

    asset_class (real-fx-generation finding, task 6b follow-up): which
    class's declared assets fam["assets"] is checked against. Defaults to
    "crypto" so every existing caller -- none of which passes this
    argument -- keeps checking against ALLOWED_ASSETS exactly as before
    (crypto specs use the legacy BTCUSD/ETHUSD names, never the cells.py
    …USDT grid, so "crypto" is never resolved through cells.CLASSES here).
    A non-crypto class checks against cells.CLASSES[asset_class]["assets"]
    instead and names the class in the error, because the first real fx
    generation dropped all 5 proposed families on this exact check still
    enforcing BTCUSD/ETHUSD against a model that had correctly been told
    (via proposal_schema_for) to propose fx tickers.
    """
    errors = []
    if not re.fullmatch(r"[a-z0-9_]+", fam.get("family", "")):
        errors.append(f"family name {fam.get('family')!r} must match [a-z0-9_]+")
    if not fam.get("card_ids"):
        errors.append("no cards cited")
    for cid in fam.get("card_ids", []):
        if cid not in accepted_ids:
            errors.append(f"card {cid} not accepted (or unknown)")
    allowed = ALLOWED_ASSETS if asset_class == "crypto" else cells.CLASSES[asset_class]["assets"]
    if not fam.get("assets") or not set(fam["assets"]) <= set(allowed):
        if asset_class == "crypto":
            errors.append(f"assets {fam.get('assets')} must be a non-empty subset of {list(allowed)}")
        else:
            errors.append(f"assets {fam.get('assets')} must be a non-empty subset of "
                          f"{asset_class} assets {list(allowed)}")

    blocks = fam.get("blocks", [])
    roles = [b.get("role") for b in blocks]
    if roles.count("entry") != 1:
        errors.append("exactly one entry block required")
    if "stop" not in roles:
        errors.append("at least one stop block required")
    if "risk" not in roles:
        errors.append("at least one risk block required")
    types = {b.get("type") for b in blocks}
    if {"regime_ma", "regime_ma_short"} <= types:
        errors.append("cannot combine regime_ma and regime_ma_short — the "
                      "gate AND is empty, so the spec would never trade")
    for b in blocks:
        errors.extend(validate_block(b.get("role"), b.get("type"), b.get("params", {}),
                                     version=SPEC_VERSION))
        if b.get("type") in excluded_types:
            # Named the CLASS, not a hardcoded bar_kind (track 2a review
            # nit): fx is the only class with a non-empty excluded_types
            # today, but a future class's own reason for excluding a block
            # type need not be single_fix bars, so the class's own declared
            # bar_kind is read back rather than assumed.
            bar_kind = cells.CLASSES.get(asset_class, {}).get("bar_kind", "unknown")
            errors.append(
                f"block type {b.get('type')!r} requires a real high/low "
                f"distinct from close and is excluded for class {asset_class!r} "
                f"(bar_kind {bar_kind!r})")

    seen_axes = set()
    for ax in fam.get("sweep", []):
        i = ax.get("block")
        if not isinstance(i, int) or not 0 <= i < len(blocks):
            errors.append(f"sweep axis references bad block index {i!r}")
            continue
        key = (blocks[i].get("role"), blocks[i].get("type"))
        schema = BLOCK_TYPES.get(key, {})
        p = ax.get("param")
        if p not in schema:
            errors.append(f"sweep axis param {p!r} not in {key[0]}/{key[1]}")
            continue
        if key not in SWEEPABLE_TYPES:
            errors.append(
                f"{key[0]}/{key[1]} is not sweepable — protocol-v4 allows "
                f"sweep axes only on dense block types {sorted(SWEEPABLE_TYPES)}")
        if (i, p) in seen_axes:
            errors.append(f"duplicate sweep axis {p!r} on block {i}")
        seen_axes.add((i, p))
        values = ax.get("values", [])
        if not values or not set(values) <= set(schema[p]["grid"]):
            errors.append(f"sweep values for {p!r} not a subset of grid {schema[p]['grid']}")
        if len(set(values)) != len(values):
            errors.append(f"duplicate values in sweep axis {p!r}")
        # protocol-v4: plateau selection (pipeline/plateau.py) requires a
        # registered sibling on BOTH sides of every swept axis, so a two-value
        # axis can never produce a survivor -- both of its points are grid
        # edges. Reject it here rather than let a family burn a whole
        # generation on a sweep that is structurally unpromotable.
        if len(values) < 3:
            errors.append(
                f"sweep axis {p!r} declares {len(values)} value(s) — needs at "
                f"least 3: with two, both points are grid edges and plateau "
                f"selection can never produce a survivor")
        # protocol-v4: gauntlet.py reads a swept axis's neighbours off the
        # PARAMETER'S DECLARED GRID (BLOCK_TYPES[key][p]["grid"]), never off
        # the family's own swept subset. A family that sweeps {20, 55, 100}
        # on channel_breakout_dense.lookback (full grid [20, 35, 55, 75, 100])
        # would pass the >=3 check above and then silently produce ZERO
        # survivors: 55's true grid neighbours are 35 and 75, and neither was
        # ever registered, so every point reads as edge_of_grid. Defining
        # neighbours relative to the family's own values instead is not an
        # option here — that would make the 20->55 jump count as a one-step
        # perturbation, which is exactly the coarse-grid problem the dense
        # block types exist to eliminate. "One step" means one declared-grid
        # step, so the swept values must be contiguous on that grid.
        grid = schema.get(p, {}).get("grid")
        if grid and values and set(values) <= set(grid):
            idxs = sorted(grid.index(v) for v in set(values))
            gap_vals = [grid[j] for j in range(idxs[0], idxs[-1] + 1)
                       if j not in idxs]
            if gap_vals:
                errors.append(
                    f"sweep axis {p!r} values {sorted(set(values))} are not "
                    f"contiguous on grid {grid} — skips {gap_vals}, so a "
                    f"one-grid-step neighbour is never registered and the "
                    f"axis cannot form a neighbourhood")

    # D10 (docs/notes/family-openness-v1.md, chained 2026-08-29): the
    # "{n} siblings exceeds cap {sibling_cap} — rejected, not clipped" refusal
    # that stood here is GONE. It discarded proposed work without a verdict,
    # which is a capacity limit presented as a judgment — the same mistake
    # protocol-v6 retired when it dropped one-winner-per-sibling-group. The
    # cap is now a per-cycle SCHEDULE applied after expansion by
    # split_for_cycle(): the first window registers now, the remainder queues
    # in loop_state.json and drains on later cycles. `sibling_cap` stays in
    # this signature (every caller and test passes it positionally) but no
    # longer decides anything here.
    return errors


def _build_name(assets: list[str], family: str, blocks: list[dict],
                timeframe: str = UNIVERSE_BASE["timeframe"]) -> str:
    # timeframe defaults to the crypto grid's "1d" so the crypto call site
    # (expand_family) is untouched; the non-crypto path (expand_family_for_
    # class) passes its own class timeframe explicitly (spec s4).
    bits = ["+".join(a.replace("USD", "") for a in assets),
            timeframe, family]
    for b in blocks:
        for p, v in sorted(b["params"].items()):
            short = "".join(w[0] for w in p.split("_"))
            bits.append(f"{short}{v}")
    full = " ".join(bits)
    if len(full) <= 120:
        return full
    # keep names unique after truncation: suffix = hash of the full name
    digest = hashlib.sha256(full.encode("utf-8")).hexdigest()[:8]
    return full[:111] + "~" + digest


def _snap_to_grid(blocks: list[dict]) -> None:
    """Replace each param value with the equal grid element (in place), so
    2 and 2.0 hash identically. Membership was already validated."""
    for b in blocks:
        schema = BLOCK_TYPES.get((b["role"], b["type"]), {})
        for p, v in b["params"].items():
            grid = schema.get(p, {}).get("grid")
            if grid:
                b["params"][p] = next(g for g in grid if g == v)


def expand_family(fam: dict, run_id: str, model: str, created_utc: str) -> list[dict]:
    """Cartesian expansion of sweep axes, in declaration order. Deterministic:
    same family + run_id + timestamp -> same strategy_ids."""
    assets = sorted(fam["assets"])
    axes = fam.get("sweep", [])
    combos = itertools.product(*[ax["values"] for ax in axes]) if axes else [()]
    specs = []
    for combo in combos:
        blocks = copy.deepcopy(fam["blocks"])
        for ax, val in zip(axes, combo):
            blocks[ax["block"]]["params"][ax["param"]] = val
        _snap_to_grid(blocks)
        spec = {
            "strategy_id": None,
            "version": SPEC_VERSION,
            "created_utc": created_utc,
            "name": _build_name(assets, fam["family"], blocks),
            "family": fam["family"],
            "universe": {"assets": assets, **UNIVERSE_BASE},
            "blocks": blocks,
            "provenance": {
                "card_ids": sorted(fam["card_ids"]),
                "parent_strategy_id": None,
                "sibling_group_id": f"{fam['family']}-{run_id}",
                "generation": 0,
            },
            "generator": {
                "agent": "composer",
                "model": model,
                "pipeline_version": PIPELINE_VERSION,
                "run_id": run_id,
            },
            "cost_model": dict(COST_MODEL),
        }
        spec["strategy_id"] = content_id(spec, "strategy_id")
        specs.append(spec)
    return specs


def sweep_cells(asset_class: str, assets=None) -> list[tuple[str, str]]:
    """The cells THIS invocation may sweep: active_cells(), optionally
    narrowed to an explicit asset subset (D6's rotation window, or a manual
    `--assets` override for a hand run).

    Two gates, in order, and neither substitutes for the other:
    ACTIVE_CELLS decides what may EVER be swept (a denominator decision, and
    Coen's own commit); this subset decides what is swept THIS GENERATION (a
    schedule). A subset naming an inactive asset is refused loudly rather than
    silently ignored — the whole point of the rotation window is that it is a
    view onto the active set, never a way past it.
    """
    active = cells.active_cells(asset_class)
    if assets is None:
        return active
    wanted = list(dict.fromkeys(assets))
    unknown = [a for a in wanted if a not in {c[0] for c in active}]
    if unknown:
        raise ValueError(
            f"--assets names {unknown} which are not ACTIVE cells of class "
            f"{asset_class!r}; the sweep subset is a view onto the active set "
            f"(cells.ACTIVE_CELLS), never a way past it")
    keep = set(wanted)
    return [c for c in active if c[0] in keep]


def expand_family_for_class(fam: dict, run_id: str, model: str, created_utc: str,
                            asset_class: str,
                            proxy_card_ids: frozenset[str] = frozenset(),
                            assets=None) -> list[dict]:
    """Non-crypto counterpart of expand_family (spec s4/s10.7-8).

    The family's blocks and sweep axes expand exactly like the crypto path
    (same combo loop, same _snap_to_grid), but the universe/cost_model come
    from cells.CLASSES[asset_class] rather than UNIVERSE_BASE/COST_MODEL, and
    every combo explodes further into one spec PER CELL of that class via
    the existing expand_universe: a cell is one asset at the class's declared
    timeframe, the same unit-of-survival contract cells.py declares for
    crypto (a strategy that only works on one fx pair must not be excluded).
    expand_universe already carries the base spec's asset_class/session onto
    every cell spec untouched, so the base universe built here just needs to
    be correct once. Deterministic: same family + run_id + timestamp -> same
    strategy_ids, exactly like expand_family.

    fam["assets"] is NOT used for cell selection here (that comes from
    cells.active_cells(asset_class)): validate_family now checks it against
    THIS class's own declared assets (real-fx-generation finding, task 6b
    follow-up), rather than the crypto-only ALLOWED_ASSETS list it used to
    be pinned against regardless of asset_class -- so the field is a real,
    class-checked value on this path. It is still not a cell-selecting one:
    per-cell expansion below is exhaustive over cells.active_cells(asset_class)
    regardless of which of the class's assets the family named, so the
    model's chosen subset is validated and then discarded here. DECLARED vs
    ACTIVE (SP5 D4): cells.class_cells is the class's whole declared grid,
    which admits data/import work; cells.active_cells is the subset the
    ACTIVE_CELLS gate permits a generation to SWEEP, because sweeping a cell
    moves the trial denominator. The four tradfi classes gate "all", so
    active == declared for them today; crypto's gate is empty until Coen's
    Phase 3 activation commit, and expansion here follows the gate, never
    the declaration.

    proxy_card_ids (Coen, 2026-08-25: proxy routing is recorded on the
    REGISTRATION, not only on the run-level drift record): the full set of
    card_ids that reached this run's proposer via INDEX_FUTURES_PROXY_TOPICS
    rather than a native ROUTING tag (run()'s proxy_routed_card_ids, as a
    frozenset). A family whose OWN card_ids intersect this set gets that
    intersection stamped on every spec it expands to:
    provenance["routed_via"] = "proxy" and
    provenance["proxy_card_ids"] = the sorted intersection (that family's
    proxy subset, not the whole run's). A family that cites no proxy card
    gets NEITHER key -- their absence IS "natively routed", so no null or
    empty-list placeholder is ever written. This makes the Norgate re-test
    set (spec D2/D4) chain-queryable per strategy, independent of whatever a
    later run's drift record says. Default frozenset() so every existing
    caller (fx, and any equity_etf caller that predates this task) keeps
    emitting neither key, byte-identical to before this change.
    composition_fingerprint reads only universe+blocks, so this NEVER
    affects a spec's identity, its N accounting, or resurrection-guard
    de-duplication (test-pinned).
    """
    cls_spec = cells.CLASSES[asset_class]
    timeframe = cls_spec["timeframes"][0]
    axes = fam.get("sweep", [])
    combos = itertools.product(*[ax["values"] for ax in axes]) if axes else [()]
    fam_proxy_card_ids = sorted(set(fam["card_ids"]) & proxy_card_ids)
    specs = []
    for combo in combos:
        blocks = copy.deepcopy(fam["blocks"])
        for ax, val in zip(axes, combo):
            blocks[ax["block"]]["params"][ax["param"]] = val
        _snap_to_grid(blocks)
        provenance = {
            "card_ids": sorted(fam["card_ids"]),
            "parent_strategy_id": None,
            "sibling_group_id": f"{fam['family']}-{run_id}",
            "generation": 0,
        }
        if fam_proxy_card_ids:
            provenance["routed_via"] = "proxy"
            provenance["proxy_card_ids"] = fam_proxy_card_ids
        base = {
            "strategy_id": None,
            "version": SPEC_VERSION,
            "created_utc": created_utc,
            "name": "",   # recomputed per cell below, once its asset is known
            "family": fam["family"],
            "universe": {"assets": [], "asset_class": asset_class,
                         "timeframe": timeframe, "session": cls_spec["session"]},
            "blocks": blocks,
            "provenance": provenance,
            "generator": {
                "agent": "composer",
                "model": model,
                "pipeline_version": PIPELINE_VERSION,
                "run_id": run_id,
            },
            "cost_model": dict(cls_spec["cost_model"]),
        }
        # the declared grid (class_cells) admits data/import work; the ACTIVE
        # set admits sweeping (SP5 s3, P2-T1's gate); `assets` narrows THIS
        # generation's slice of the active set (D6's rotation window, spec s5)
        # without changing what is active -- see sweep_cells.
        for cell_spec in expand_universe(base, sweep_cells(asset_class, assets)):
            cell_spec["name"] = _build_name(
                cell_spec["universe"]["assets"], fam["family"],
                cell_spec["blocks"], cell_spec["universe"]["timeframe"])
            cell_spec["strategy_id"] = content_id(cell_spec, "strategy_id")
            specs.append(cell_spec)
    return specs


def expander_for(asset_class: str):
    """The family-expansion callable for one class - the ROUTING DECISION,
    lifted out of run() so it can be observed rather than only executed.

    Pure refactor (P2-T3 rider): run() used to inline
    `if args.asset_class == "crypto": expand_family(...) else
    expand_family_for_class(...)`. Behaviour is byte-identical; the branch
    simply has a name now.

    crypto is the special case: it still takes the LEGACY POOLED path
    (expand_family + UNIVERSE_BASE + ALLOWED_ASSETS), which builds one
    multi-asset BTCUSD/ETHUSD spec per sweep combo, while every other class
    takes the per-cell path (expand_family_for_class, one single-asset spec
    per ACTIVE cell). SP5 Phase 3's activation commit deletes the crypto
    branch here and flips CLASSES["crypto"]["benchmark"] to "self" in the
    SAME commit - a benchmark:"self" class needs exactly one asset per cell,
    which only the per-cell path provides. That coupling is what
    test_gauntlet_classes.py's
    test_crypto_benchmark_and_the_legacy_pooled_path_flip_together reads this
    function for; making the dispatch observable is the whole point of it
    being a function.
    """
    return expand_family if asset_class == "crypto" else expand_family_for_class


PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "families": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "family": {"type": "string"},
                    "rationale": {"type": "string"},
                    "regime_hypothesis": {"type": "string"},
                    "card_ids": {"type": "array", "items": {"type": "string"}},
                    "assets": {"type": "array",
                               "items": {"enum": list(ALLOWED_ASSETS)}},
                    "blocks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "role": {"enum": ["entry", "filter", "exit",
                                                   "stop", "target", "risk", "regime"]},
                                "type": {"type": "string"},
                                # structured outputs cannot express free-form
                                # maps (every object needs additionalProperties
                                # false), so params travel as name/value pairs
                                # and normalize_proposal() converts to dicts
                                "params": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"},
                                            "value": {"type": ["number", "string"]},
                                        },
                                        "required": ["name", "value"],
                                        "additionalProperties": False,
                                    },
                                },
                            },
                            "required": ["role", "type", "params"],
                            "additionalProperties": False,
                        },
                    },
                    "sweep": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "block": {"type": "integer"},
                                "param": {"type": "string"},
                                "values": {"type": "array",
                                           "items": {"type": ["number", "string"]}},
                            },
                            "required": ["block", "param", "values"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["family", "rationale", "regime_hypothesis",
                              "card_ids", "assets", "blocks", "sweep"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["families"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """\
You are the Composer agent in Stewart & Co.'s research pipeline. You design
candidate trading strategies for crypto daily bars (BTCUSD, ETHUSD) as
compositions of typed blocks, grounded in accepted research cards.

What happened so far, as measured, not as opinion:

Generation 1 registered three families, all long-biased. 13 of its 22 specs
passed the screen; all 13 then failed out of sample, because their per-trade
edge scaled with a price drift and a volatility level that did not persist.
Passive buy-and-hold decayed just as hard over the same window.

Generation 2 corrected the long-only bias: all four families were
short-capable or symmetric. It still passed nothing. 18 of its 34 specs
cleared the screen and all 18 then failed the gauntlet. The detail:
- Symmetric z-score reversion lost 46% to 66% over the training window and
  failed the screen outright — the same way generation 1's reversion family
  died, on net_negative.
- A short-only trend family produced only 10-19 trades in six and a half years
  and failed the screen's trade-count floor. Four of its eight specs were also
  unprofitable, so the trade count is what it failed on, not the only thing
  wrong with it.
- Of the 18 that reached the gauntlet, 12 showed POSITIVE
  volatility-normalized edge decay out of sample, from +1.8% to +54.3%: they
  held or improved their edge per unit of available opportunity. The other 6
  ran down to -69.8%, and both families that reached the gauntlet appear in
  both groups.
- All 18 used vol_target sizing, so nothing here compares sizing rules. Within
  that single arm the four worst ruin and the four worst Monte Carlo outcomes
  were vol_target by construction. The grammar's other sizing rule,
  fixed_fraction, has been registered on 4 specs and has never reached the
  gauntlet.

Draw your own conclusions from those facts.

Rules:
- Use ONLY the block types and parameter grid values given in the grammar.
- Every family must cite the card_ids that motivate it. Cite only cards that
  genuinely inform the composition; do not decorate with irrelevant citations.
- Exactly one entry block; at least one stop and one risk block per family.
- EXITS (D15, 2026-09-03): a position closes ONLY through (a) a stop-loss whose
  LEVEL is indicator-placed (atr_stop*, swing_stop, ma_stop, channel_stop,
  band_stop), (b) an R-multiple target (optional), (c) declared indicator-EVENT
  exit blocks (optional, zero or more: ma_crossunder, channel_exit,
  zscore_revert, tstat_decay, regime_flip). NEVER a time stop of any kind --
  exiting on the calendar is forbidden. An exit block MAY reuse the entry's
  indicator (crossover in, crossunder out is the canonical example).
- Every family must state a regime_hypothesis: which market conditions it
  expects to work in, and why it is not merely levered exposure to an upward
  drift. A family whose edge disappears when drift and volatility fall should
  say so plainly.
- Short-capable types exist: trend_scan_ds and ma_cross_ds take a direction
  parameter (long, short, both), and regime_ma_short permits entries below a
  moving average. channel_breakout and zscore_reversion already accept
  direction: both.
- regime_ma and regime_ma_short cannot appear in the same family — their
  filters are mutually exclusive and the spec would never trade. Express
  "long in one regime, short in the other" as two separate families.
- Choose sweep axes ONLY where the cited research motivates exploring the
  parameter, and sweep them on a DENSE block type (the *_dense variants).
  Coarse types may be USED at fixed values but may NOT be swept.
- Every swept axis must declare at least THREE values that are CONTIGUOUS on
  that parameter's declared grid — e.g. [35, 55, 75], never [20, 55, 100].
  Selection requires a registered sibling one step BELOW and one step ABOVE a
  candidate on every swept axis, so a two-value sweep and a gapped sweep can
  never produce a survivor and will be rejected.
- Prefer ONE or TWO well-motivated axes at FIVE contiguous values over several
  axes at three: five values leave three eligible candidates, three leave one.
- Strategies must be implementable from daily OHLCV alone.
- Propose fewer, better-grounded families over many weak ones. If the cards
  support only two good families, propose two."""


def system_prompt_for(asset_class: str) -> str:
    """The Composer's mission-statement + history brief for one class.

    "crypto" returns SYSTEM_PROMPT verbatim (identity, not reconstruction),
    so the crypto call site's exact bytes are never touched by this function
    existing.

    T4's review (task 6b, spec s10.7 pre-activation rider) found the live
    model still reasoned against "crypto daily bars (BTCUSD, ETHUSD)" and the
    crypto generation-1/2 history on --asset-class fx runs. The fx branch
    below states the REAL fx universe, pulling the asset list from
    cells.CLASSES["fx"]["assets"] rather than a second hardcoded copy, and
    omits the gen-1/2 paragraphs entirely: those report concrete crypto
    trial counts and screen/gauntlet outcomes, not a lesson that
    generalizes, so repeating them to the fx proposer would misstate a
    chain history it was never part of.

    "equity_etf" (track 2a) follows the same pattern with its own honesty
    limits in place of fx's single-fix-bar limits: split-adjusted PRICE
    returns with dividends excluded, and a survivorship-alive-in-2026
    universe (spec "Honesty limits" #1-2) -- both named explicitly so a
    family never claims an edge this data cannot see.

    "bond_etf" and "metal_etf" (track 2b) follow the same pattern again, each
    with its OWN honest wording (addendum build delta #2): bond's brief
    states plainly that price returns exclude dividends AND coupon
    distributions -- coupon is most of a bond ETF's total return, so a long
    bond_etf edge is understated far more severely than equity_etf's dividend
    gap. metal's brief states that GLD/SLV track spot gold/silver via a
    physically-backed TRUST structure, not a futures roll, so there is no
    roll-yield honesty limit to name (unlike a futures-based metals product).
    """
    if asset_class == "crypto":
        return SYSTEM_PROMPT
    if asset_class == "equity_etf":
        return _equity_etf_system_prompt()
    if asset_class == "bond_etf":
        return _bond_etf_system_prompt()
    if asset_class == "metal_etf":
        return _metal_etf_system_prompt()
    if asset_class != "fx":
        raise ValueError(f"no proposer brief declared for asset_class {asset_class!r}")
    cls_spec = cells.CLASSES["fx"]
    assets = ", ".join(cls_spec["assets"])
    cost = cls_spec["cost_model"]
    commission = f"{cost['commission_per_side']:.5f}"
    slippage = f"{cost['slippage_ticks']:.5f}"
    financing = f"{cost['short_financing_per_year']:.1%}"
    return f"""\
You are the Composer agent in Stewart & Co.'s research pipeline. You design
candidate trading strategies for the fx universe (12 USD-per-foreign FRED
daily spot fixes: {assets}) as compositions of typed blocks, grounded
in accepted research cards.

The fx universe, stated plainly because it differs from crypto in ways that
change what a sound family looks like:
- Each bar is one daily spot fix, not a real OHLC bar: open, high, low and
  close are all the same value and volume is 0. True range degenerates to
  |close minus previous close|, so any block whose semantics need a real
  high/low distinct from close is excluded outright rather than silently fed
  a degenerate input.
- The calendar is a weekday calendar (the series' own published fix days,
  five per week), not crypto's 24x7 grid: there is no Saturday or Sunday bar
  and no synthetic filling of holiday holes.
- The declared cost model charges {commission} commission per side plus
  {slippage} slippage per side, and accrues a short financing cost of
  {financing} per year on every bar a position is held short. Financing is
  a COST, not a return input: the interest-rate differential between the
  two currencies (carry) is excluded from returns entirely, so a family
  whose thesis depends on collecting carry will not show it in this cost
  model.
- Strategies must be implementable from the daily single-fix bar alone:
  there is no intrabar information to draw on.

This is the first generation proposed for this class: there is no fx
generation history yet to report.

Rules:
- Use ONLY the block types and parameter grid values given in the grammar.
- Every family must cite the card_ids that motivate it. Cite only cards that
  genuinely inform the composition; do not decorate with irrelevant citations.
- Exactly one entry block; at least one stop and one risk block per family.
- EXITS (D15, 2026-09-03): a position closes ONLY through (a) a stop-loss whose
  LEVEL is indicator-placed -- for this single-fix class that means ma_stop or
  band_stop (atr_stop*, swing_stop and channel_stop read a real high/low and
  are EXCLUDED here), (b) an R-multiple target (optional), (c) declared
  indicator-EVENT exit blocks (optional, zero or more: ma_crossunder,
  zscore_revert, tstat_decay, regime_flip; channel_exit reads a high/low and
  is EXCLUDED here). NEVER a time stop of any kind -- exiting on the calendar
  is forbidden. An exit block MAY reuse the entry's indicator (crossover in,
  crossunder out is the canonical example).
- Every family must state a regime_hypothesis: which market conditions it
  expects to work in, and why it is not merely levered exposure to an upward
  drift. A family whose edge disappears when drift and volatility fall should
  say so plainly.
- Short-capable types exist: trend_scan_ds and ma_cross_ds take a direction
  parameter (long, short, both), and regime_ma_short permits entries below a
  moving average. channel_breakout and zscore_reversion already accept
  direction: both.
- regime_ma and regime_ma_short cannot appear in the same family: their
  filters are mutually exclusive and the spec would never trade. Express
  "long in one regime, short in the other" as two separate families.
- Choose sweep axes ONLY where the cited research motivates exploring the
  parameter, and sweep them on a DENSE block type (the *_dense variants).
  Coarse types may be USED at fixed values but may NOT be swept.
- Every swept axis must declare at least THREE values that are CONTIGUOUS on
  that parameter's declared grid, e.g. [35, 55, 75], never [20, 55, 100].
  Selection requires a registered sibling one step BELOW and one step ABOVE a
  candidate on every swept axis, so a two-value sweep and a gapped sweep can
  never produce a survivor and will be rejected.
- Prefer ONE or TWO well-motivated axes at FIVE contiguous values over several
  axes at three: five values leave three eligible candidates, three leave one.
- Propose fewer, better-grounded families over many weak ones. If the cards
  support only two good families, propose two."""


def _equity_etf_system_prompt() -> str:
    """Track 2a's mission-statement + honesty-limits brief, split out of
    system_prompt_for like the fx branch (kept separate rather than inlined
    so the dispatcher above stays a short, readable table of branches).
    """
    cls_spec = cells.CLASSES["equity_etf"]
    assets = ", ".join(cls_spec["assets"])
    n_assets = len(cls_spec["assets"])
    cost = cls_spec["cost_model"]
    commission = f"{cost['commission_per_side']:.5f}"
    slippage = f"{cost['slippage_ticks']:.5f}"
    financing = f"{cost['short_financing_per_year']:.1%}"
    return f"""\
You are the Composer agent in Stewart & Co.'s research pipeline. You design
candidate trading strategies for the equity-index ETF universe ({n_assets}
daily OHLCV series tracking major equity indices: {assets}) as compositions
of typed blocks, grounded in accepted research cards.

The equity_etf universe, stated plainly because two honesty limits shape what
a sound family can honestly claim:
- Returns are split-adjusted PRICE returns; dividends are excluded from every
  series. A long-only strategy's edge is therefore systematically
  understated relative to a total-return investor -- worst for the
  higher-yield markets in this universe -- so a family must not claim an
  edge that depends on dividend income this data cannot see.
- The universe is survivorship-alive-in-2026: every fund in it exists today,
  so a fund that would have been delisted somewhere in this history is not
  represented, and a family reasoning about historical fund mortality is
  reasoning about data this universe does not carry.
- Each bar is a REAL daily OHLC bar (Tiingo daily bars), unlike fx's
  single-fix bars: range-based block types are eligible here, and none are
  excluded for this class.
- The calendar is a weekday calendar (each fund's own trading days), not
  crypto's 24x7 grid: there is no Saturday or Sunday bar and no synthetic
  filling of holiday holes.
- The declared cost model charges {commission} commission per side plus
  {slippage} slippage per side, and accrues a short financing cost of
  {financing} per year on every bar a position is held short.
- Strategies must be implementable from daily OHLCV alone.

This is the first generation proposed for this class: there is no equity_etf
generation history yet to report.

Rules:
- Use ONLY the block types and parameter grid values given in the grammar.
- Every family must cite the card_ids that motivate it. Cite only cards that
  genuinely inform the composition; do not decorate with irrelevant citations.
- Exactly one entry block; at least one stop and one risk block per family.
- EXITS (D15, 2026-09-03): a position closes ONLY through (a) a stop-loss whose
  LEVEL is indicator-placed (atr_stop*, swing_stop, ma_stop, channel_stop,
  band_stop), (b) an R-multiple target (optional), (c) declared indicator-EVENT
  exit blocks (optional, zero or more: ma_crossunder, channel_exit,
  zscore_revert, tstat_decay, regime_flip). NEVER a time stop of any kind --
  exiting on the calendar is forbidden. An exit block MAY reuse the entry's
  indicator (crossover in, crossunder out is the canonical example).
- Every family must state a regime_hypothesis: which market conditions it
  expects to work in, and why it is not merely levered exposure to an upward
  drift. A family whose edge disappears when drift and volatility fall should
  say so plainly.
- Short-capable types exist: trend_scan_ds and ma_cross_ds take a direction
  parameter (long, short, both), and regime_ma_short permits entries below a
  moving average. channel_breakout and zscore_reversion already accept
  direction: both.
- regime_ma and regime_ma_short cannot appear in the same family: their
  filters are mutually exclusive and the spec would never trade. Express
  "long in one regime, short in the other" as two separate families.
- Choose sweep axes ONLY where the cited research motivates exploring the
  parameter, and sweep them on a DENSE block type (the *_dense variants).
  Coarse types may be USED at fixed values but may NOT be swept.
- Every swept axis must declare at least THREE values that are CONTIGUOUS on
  that parameter's declared grid, e.g. [35, 55, 75], never [20, 55, 100].
  Selection requires a registered sibling one step BELOW and one step ABOVE a
  candidate on every swept axis, so a two-value sweep and a gapped sweep can
  never produce a survivor and will be rejected.
- Prefer ONE or TWO well-motivated axes at FIVE contiguous values over several
  axes at three: five values leave three eligible candidates, three leave one.
- Propose fewer, better-grounded families over many weak ones. If the cards
  support only two good families, propose two."""


def _bond_etf_system_prompt() -> str:
    """Track 2b's mission-statement + honesty-limits brief, split out like
    the equity_etf branch. bond's honesty limit is stronger than equity_etf's:
    coupon distributions are most of a bond ETF's total return (unlike a
    dividend, which is a minority of most equity total returns), so the
    understatement here is named plainly rather than left implicit.
    """
    cls_spec = cells.CLASSES["bond_etf"]
    assets = ", ".join(cls_spec["assets"])
    n_assets = len(cls_spec["assets"])
    cost = cls_spec["cost_model"]
    commission = f"{cost['commission_per_side']:.5f}"
    slippage = f"{cost['slippage_ticks']:.5f}"
    financing = f"{cost['short_financing_per_year']:.1%}"
    return f"""\
You are the Composer agent in Stewart & Co.'s research pipeline. You design
candidate trading strategies for the bond ETF universe ({n_assets} daily
OHLCV series tracking US Treasury, TIPS, investment-grade, high-yield and
emerging-market debt: {assets}) as compositions of typed blocks, grounded in
accepted research cards.

The bond_etf universe, stated plainly because one honesty limit shapes what a
sound family can honestly claim, more severely than for equities:
- Returns are split-adjusted PRICE returns; dividends AND coupon
  distributions are excluded from every series. Coupon income is most of a
  bond ETF's total return -- far more than a dividend is of most equity
  total returns -- so a long-only bond_etf strategy's edge here is
  MATERIALLY UNDERSTATED relative to a total-return holder, worse than the
  equivalent gap for equity_etf. A family must not claim an edge that
  depends on coupon income this data cannot see.
- Each bar is a REAL daily OHLC bar (Tiingo daily bars), like equity_etf and
  unlike fx's single-fix bars: range-based block types are eligible here,
  and none are excluded for this class.
- The calendar is a weekday calendar (each fund's own trading days), not
  crypto's 24x7 grid: there is no Saturday or Sunday bar and no synthetic
  filling of holiday holes.
- The declared cost model charges {commission} commission per side plus
  {slippage} slippage per side, and accrues a short financing cost of
  {financing} per year on every bar a position is held short.
- Strategies must be implementable from daily OHLCV alone.

This is the first generation proposed for this class: there is no bond_etf
generation history yet to report.

Rules:
- Use ONLY the block types and parameter grid values given in the grammar.
- Every family must cite the card_ids that motivate it. Cite only cards that
  genuinely inform the composition; do not decorate with irrelevant citations.
- Exactly one entry block; at least one stop and one risk block per family.
- EXITS (D15, 2026-09-03): a position closes ONLY through (a) a stop-loss whose
  LEVEL is indicator-placed (atr_stop*, swing_stop, ma_stop, channel_stop,
  band_stop), (b) an R-multiple target (optional), (c) declared indicator-EVENT
  exit blocks (optional, zero or more: ma_crossunder, channel_exit,
  zscore_revert, tstat_decay, regime_flip). NEVER a time stop of any kind --
  exiting on the calendar is forbidden. An exit block MAY reuse the entry's
  indicator (crossover in, crossunder out is the canonical example).
- Every family must state a regime_hypothesis: which market conditions it
  expects to work in, and why it is not merely levered exposure to an upward
  drift. A family whose edge disappears when drift and volatility fall should
  say so plainly.
- Short-capable types exist: trend_scan_ds and ma_cross_ds take a direction
  parameter (long, short, both), and regime_ma_short permits entries below a
  moving average. channel_breakout and zscore_reversion already accept
  direction: both.
- regime_ma and regime_ma_short cannot appear in the same family: their
  filters are mutually exclusive and the spec would never trade. Express
  "long in one regime, short in the other" as two separate families.
- Choose sweep axes ONLY where the cited research motivates exploring the
  parameter, and sweep them on a DENSE block type (the *_dense variants).
  Coarse types may be USED at fixed values but may NOT be swept.
- Every swept axis must declare at least THREE values that are CONTIGUOUS on
  that parameter's declared grid, e.g. [35, 55, 75], never [20, 55, 100].
  Selection requires a registered sibling one step BELOW and one step ABOVE a
  candidate on every swept axis, so a two-value sweep and a gapped sweep can
  never produce a survivor and will be rejected.
- Prefer ONE or TWO well-motivated axes at FIVE contiguous values over several
  axes at three: five values leave three eligible candidates, three leave one.
- Propose fewer, better-grounded families over many weak ones. If the cards
  support only two good families, propose two."""


def _metal_etf_system_prompt() -> str:
    """Track 2b's mission-statement + honesty-limits brief for metal_etf.
    Unlike bond/equity, there is no dividend/coupon gap to name: GLD/SLV are
    physically-backed trusts, and this data is price-only for every class in
    this table anyway. What DOES need naming, honestly, is that GLD/SLV do
    not equal owning the metal directly -- they track spot via a trust
    structure, so a family reasoning about physical delivery, storage cost,
    or futures roll yield is reasoning about a product this universe is not.
    """
    cls_spec = cells.CLASSES["metal_etf"]
    assets = ", ".join(cls_spec["assets"])
    n_assets = len(cls_spec["assets"])
    cost = cls_spec["cost_model"]
    commission = f"{cost['commission_per_side']:.5f}"
    slippage = f"{cost['slippage_ticks']:.5f}"
    financing = f"{cost['short_financing_per_year']:.1%}"
    return f"""\
You are the Composer agent in Stewart & Co.'s research pipeline. You design
candidate trading strategies for the metal ETF universe ({n_assets} daily
OHLCV series tracking gold and silver: {assets}) as compositions of typed
blocks, grounded in accepted research cards.

The metal_etf universe, stated plainly because one honesty limit shapes what
a sound family can honestly claim:
- Returns are split-adjusted PRICE returns. GLD/SLV track spot gold/silver
  via a PHYSICALLY-BACKED TRUST structure, not a futures roll and not direct
  physical ownership: there is no roll yield to collect or lose, and no
  storage or insurance cost borne by the strategy, but also no delivery
  option. A family reasoning about futures roll yield, contango/backwardation
  carry, or physical delivery is reasoning about a product this universe
  does not represent.
- Each bar is a REAL daily OHLC bar (Tiingo daily bars), like equity_etf and
  bond_etf and unlike fx's single-fix bars: range-based block types are
  eligible here, and none are excluded for this class.
- The calendar is a weekday calendar (each fund's own trading days), not
  crypto's 24x7 grid: there is no Saturday or Sunday bar and no synthetic
  filling of holiday holes.
- The declared cost model charges {commission} commission per side plus
  {slippage} slippage per side, and accrues a short financing cost of
  {financing} per year on every bar a position is held short -- higher than
  bond_etf's, reflecting metals' higher borrow cost (Phase C table).
- Strategies must be implementable from daily OHLCV alone.
- Only 2 assets are declared for this class (GLD, SLV): a family's cited
  research should motivate gold and/or silver specifically, not a generic
  "commodities" thesis borrowed from a different instrument.

This is the first generation proposed for this class: there is no metal_etf
generation history yet to report.

Rules:
- Use ONLY the block types and parameter grid values given in the grammar.
- Every family must cite the card_ids that motivate it. Cite only cards that
  genuinely inform the composition; do not decorate with irrelevant citations.
- Exactly one entry block; at least one stop and one risk block per family.
- EXITS (D15, 2026-09-03): a position closes ONLY through (a) a stop-loss whose
  LEVEL is indicator-placed (atr_stop*, swing_stop, ma_stop, channel_stop,
  band_stop), (b) an R-multiple target (optional), (c) declared indicator-EVENT
  exit blocks (optional, zero or more: ma_crossunder, channel_exit,
  zscore_revert, tstat_decay, regime_flip). NEVER a time stop of any kind --
  exiting on the calendar is forbidden. An exit block MAY reuse the entry's
  indicator (crossover in, crossunder out is the canonical example).
- Every family must state a regime_hypothesis: which market conditions it
  expects to work in, and why it is not merely levered exposure to an upward
  drift. A family whose edge disappears when drift and volatility fall should
  say so plainly.
- Short-capable types exist: trend_scan_ds and ma_cross_ds take a direction
  parameter (long, short, both), and regime_ma_short permits entries below a
  moving average. channel_breakout and zscore_reversion already accept
  direction: both.
- regime_ma and regime_ma_short cannot appear in the same family: their
  filters are mutually exclusive and the spec would never trade. Express
  "long in one regime, short in the other" as two separate families.
- Choose sweep axes ONLY where the cited research motivates exploring the
  parameter, and sweep them on a DENSE block type (the *_dense variants).
  Coarse types may be USED at fixed values but may NOT be swept.
- Every swept axis must declare at least THREE values that are CONTIGUOUS on
  that parameter's declared grid, e.g. [35, 55, 75], never [20, 55, 100].
  Selection requires a registered sibling one step BELOW and one step ABOVE a
  candidate on every swept axis, so a two-value sweep and a gapped sweep can
  never produce a survivor and will be rejected.
- Prefer ONE or TWO well-motivated axes at FIVE contiguous values over several
  axes at three: five values leave three eligible candidates, three leave one.
- Propose fewer, better-grounded families over many weak ones. If the cards
  support only two good families, propose two."""


def proposal_schema_for(asset_class: str) -> dict:
    """The structured-output schema for one class's proposer call.

    "crypto" returns PROPOSAL_SCHEMA verbatim (identity, not reconstruction,
    task 6b), so the crypto call site's exact schema object is never touched
    by this function existing.

    A non-crypto class only swaps the "assets" enum, deep-copied off
    PROPOSAL_SCHEMA rather than hand-duplicating the families/blocks/sweep
    structure, and pulls the enum from cells.CLASSES[asset_class]["assets"]
    (never a second hardcoded copy of the asset list).

    The enum still is not cell-selecting, consistent with T4's documented
    friction (spec s10.7): validate_family now checks fam["assets"] against
    this same per-class list (real-fx-generation finding, task 6b follow-up)
    so the model's proposal is genuinely validated, but expand_family_for_
    class still ignores fam["assets"] entirely when building specs, sourcing
    the real per-cell assets from cells.active_cells(asset_class) instead.
    The model may propose any subset of the class's declared assets here;
    per-cell expansion overrides it regardless of what was proposed. Note
    those are two different lists (SP5 D4): this enum is the DECLARED asset
    list (CLASSES), while expansion sweeps only the cells the ACTIVE_CELLS
    gate activates -- so a declared-but-not-yet-active asset can legally be
    proposed here and still contribute no cell.
    """
    if asset_class == "crypto":
        return PROPOSAL_SCHEMA
    schema = copy.deepcopy(PROPOSAL_SCHEMA)
    assets = list(cells.CLASSES[asset_class]["assets"])
    schema["properties"]["families"]["items"]["properties"]["assets"]["items"]["enum"] = assets
    return schema


def expand_universe(spec: dict, cells: list[tuple[str, str]]) -> list[dict]:
    """One spec per declared cell.

    A CELL is one asset at one timeframe, and it is the unit of survival:
    excluding a strategy that only works on 15m ETH is exactly what this
    exists to prevent. Each (blocks, cell) pair is a separate registered
    strategy and therefore a separate TRIAL - composition_fingerprint already
    hashes assets and timeframe, so they register side by side without
    tripping the resurrection guard.

    The 2-asset mean-combine path in engine.run_spec is untouched and still
    serves the legacy BTC+ETH 1d specs.
    """
    from .cells import cell_id, validate_cell

    out = []
    for asset, timeframe in cells:
        validate_cell(asset, timeframe)
        s = json.loads(json.dumps(spec))          # deep copy, no shared state
        s["universe"] = dict(s.get("universe", {}),
                             assets=[asset], timeframe=timeframe)
        # ONE SIBLING GROUP PER CELL (Coen, 2026-08-18). The deep copy carried
        # provenance verbatim, so one parameter set across 30 cells landed in
        # one group -- and selection, PBO and the plateau gate are all
        # per-group, with select_survivors keeping exactly ONE winner. That
        # would have discarded 29 of 30 cells, making a cell the unit of
        # COMPETITION when this function exists to make it the unit of
        # SURVIVAL. Scoping the id to the cell keeps the family and run
        # readable in the id, so lineage survives on the chain.
        prov = s.get("provenance") or {}
        if prov.get("sibling_group_id"):
            prov = dict(prov, sibling_group_id=(
                f"{prov['sibling_group_id']}:{cell_id(asset, timeframe)}"))
            s["provenance"] = prov
        out.append(s)
    return out


def grammar_summary() -> str:
    """The grammar as the model sees it: live types only. Retired types
    (D15 exit rules v7) stay in BLOCK_TYPES because their chained schemas are
    immutable, but they are omitted from the listing and named on one closing
    line so the model is told, not left to guess, why they are missing."""
    lines = []
    for (role, btype), schema in BLOCK_TYPES.items():
        if (role, btype) in RETIRED_TYPES:
            continue
        params = ", ".join(f"{p} in {s['grid']}" for p, s in schema.items())
        lines.append(f"- {role}/{btype}: {params or '(no params)'}")
    lines.append("- retired (never use): " + ", ".join(
        f"{r}/{ty} ({why})" for (r, ty), why in RETIRED_TYPES.items()))
    return "\n".join(lines)


def cards_summary(accepted: dict[str, dict]) -> str:
    lines = []
    for cid, c in accepted.items():
        lines.append(f"- {cid} [{c['testability']['score']:.2f}] {c['claim']}")
    return "\n".join(lines)


def preflight_block_types(registry: Registry) -> list[str]:
    """Conflicts between the in-code grammar and already-chained block types.
    Non-empty means blocks.py mutated a chained schema — abort before any write."""
    chained = {}
    for e in registry.entries():
        if e["entry_type"] == "block_type_registered":
            chained[(e["payload"]["role"], e["payload"]["type"])] = e["payload"]["params_schema"]
    return [
        f"{role}/{btype} already chained with a different params_schema"
        for (role, btype), schema in BLOCK_TYPES.items()
        if (role, btype) in chained and chained[(role, btype)] != schema
    ]


def _client_and_meter():
    """Real client and budget meter. Split out so tests can stub it, and so
    both the dry run and the real run go through the SAME metered path.

    The sc-reader key lives in the reader's .env, not in the ambient
    environment, so load it the way every other entry point does. Loading it
    here is what makes an UNATTENDED run work: every composer call before
    2026-08-29 was session-launched, where the key happened to be in the
    session env, so the first scheduled cycle was the first to die on
    "Could not resolve authentication method" -- thirty frames deep in the
    anthropic SDK, saying nothing about where the key actually belongs, after
    the triage stage had already paid full freight for the cycle.
    """
    import anthropic
    from pathlib import Path as _Path
    from .budget import BudgetMeter, PIPELINE_CAP_USD
    from .scanner import DEFAULT_READER_ENV, _load_api_key

    _load_api_key(DEFAULT_READER_ENV)      # raises SystemExit with the path
    logs = _Path(__file__).resolve().parent.parent / "logs"
    return anthropic.Anthropic(), BudgetMeter(
        logs / "budget_ledger.jsonl", monthly_cap_usd=PIPELINE_CAP_USD,
        agent="pipeline")


def propose_families(model: str, accepted: dict[str, dict],
                     max_families: int, client=None, meter=None,
                     asset_class: str = "crypto") -> list[dict]:
    """Propose idea families, recording the spend against the pipeline's cap.

    Until 2026-08-22 this called anthropic directly with no record_call, so the
    Composer was the one metered agent's unmetered mouth: D33's USD 20 cap
    could not see it and could not bind it, and neither of generation 4's two
    live calls left a ledger row. The cap is checked BEFORE the call, because a
    cap that only notices after the money is gone is a report, not a cap.

    asset_class defaults to "crypto" (today's unrestricted prompt, unchanged).
    A non-crypto class gets its own mission-statement + schema from
    system_prompt_for/proposal_schema_for (task 6b, spec s10.7 pre-activation
    rider): the crypto mission statement and BTCUSD/ETHUSD-only schema must
    never reach a non-crypto run. It also appends one line naming its
    excluded block types so the model does not spend a family on a block
    that validate_family will reject anyway.
    """
    if client is None or meter is None:
        client, meter = _client_and_meter()
    if not meter.can_spend():
        raise SystemExit(
            f"REFUSED: pipeline budget at cap "
            f"({meter.month_spend():.2f} of {meter.monthly_cap_usd:.2f} USD "
            f"this month). The Composer will not spend past D33's limit.")
    # T4-rider-3 (track 2a): read the excluded set from the class's own
    # declaration (cells.CLASSES[cls]["excluded_block_types"]) rather than
    # always naming RANGE_REQUIRING -- equity_etf declares an EMPTY set (real
    # OHLC bars, spec s"Class declaration"), so this note is skipped entirely
    # for it, exactly like crypto. Only fx (today) has anything to name.
    excluded_types = (cells.CLASSES[asset_class]["excluded_block_types"]
                      if asset_class != "crypto" else frozenset())
    exclusion_note = ""
    if excluded_types:
        bar_kind = cells.CLASSES[asset_class]["bar_kind"]
        reason = ("single-fix daily bars (no real intrabar high/low distinct "
                  "from close)" if bar_kind == "single_fix" else f"bar_kind {bar_kind!r}")
        exclusion_note = (
            f"\n\nThis run targets asset_class={asset_class!r} cells with "
            f"{reason}: do NOT propose these excluded block types: "
            f"{sorted(excluded_types)}.")
    with client.messages.stream(
        model=model,
        max_tokens=32_000,
        system=system_prompt_for(asset_class),
        output_config={"format": {"type": "json_schema",
                                  "schema": proposal_schema_for(asset_class)}},
        messages=[{
            "role": "user",
            "content": (
                f"Block grammar:\n{grammar_summary()}\n\n"
                f"Accepted research cards:\n{cards_summary(accepted)}\n\n"
                f"Propose up to {max_families} strategy families per your rules."
                f"{exclusion_note}"
            ),
        }],
    ) as stream:
        message = stream.get_final_message()
    # Recorded whatever the outcome: a refusal still consumed tokens, and a
    # row missing because the answer was unusable is exactly how the meter
    # drifts from the bill.
    meter.record_call(model, message.usage, "composer", agent="pipeline")
    if message.stop_reason == "refusal":
        print("  model refusal", file=sys.stderr)
        return []
    text = next(b.text for b in message.content if b.type == "text")
    return normalize_proposal(json.loads(text)["families"])


def normalize_proposal(families: list[dict]) -> list[dict]:
    """Convert model-emitted [{name, value}] param lists back into the
    {name: value} dicts the rest of the pipeline uses (see PROPOSAL_SCHEMA)."""
    for fam in families:
        for b in fam.get("blocks", []):
            if isinstance(b.get("params"), list):
                b["params"] = {p["name"]: p["value"] for p in b["params"]}
    return families


def drift_record(run_id: str, dry_run: bool, specs: list[dict],
                 routing: dict | None = None,
                 routed_card_ids: list[str] | None = None,
                 proxy_routed_card_ids: list[str] | None = None) -> dict:
    """What one Composer run emitted. Written for both the dry run and the real
    run so the gap between the batch Coen approved and the batch that got
    chained stops being invisible.

    routing/routed_card_ids stay None for crypto runs (the default), so the
    crypto shape of this dict is byte-identical to before this task
    (regression-pinned in test_composer_fx.py). A non-crypto run adds both
    keys: routing names the asset_class + eligible card tags (spec s4),
    routed_card_ids is the accepted-card feed actually shown to the
    proposer, so a family's absence is auditable back to routing, not a
    silent drop.

    proxy_routed_card_ids (track 2a, spec s10.8/"Routing"): the futures-
    tagged card_ids that reached the proposer via INDEX_FUTURES_PROXY_TOPICS
    rather than a native ROUTING tag -- recorded here, on the DRIFT RECORD,
    not on the strategy registration (T4 built routing/routed_card_ids on
    the drift record for fx, never on the spec payload itself; this stays
    consistent with that, rather than the parent spec's "recorded on the
    registration" prose). Stays None for crypto and fx runs, where the
    concept does not apply; an equity_etf run always sets it (even to []
    when the proxy lane finds nothing), so an empty list is visibly "the
    lane ran and found none" rather than "this run predates the lane"."""
    record = {"run_id": run_id,
             "mode": "dry" if dry_run else "real",
             "n_specs": len(specs),
             "strategy_ids": [s["strategy_id"] for s in specs],
             "families": sorted({s["family"] for s in specs})}
    if routing is not None:
        record["routing"] = routing
        record["routed_card_ids"] = routed_card_ids or []
    if proxy_routed_card_ids is not None:
        record["proxy_routed_card_ids"] = proxy_routed_card_ids
    return record


def drift_between(dry: dict, real: dict) -> dict:
    """The batch-gate drift. NOT a multiple-testing trial count: nothing in a
    dry run was ever backtested, so none of it inflated any maximum Sharpe. It
    is recorded because it is a real, auditable record of search that the chain
    would otherwise lose."""
    d, r = set(dry["strategy_ids"]), set(real["strategy_ids"])
    return {"run_id": real["run_id"],
            "n_dry": dry["n_specs"], "n_real": real["n_specs"],
            "dropped": sorted(d - r), "added": sorted(r - d),
            "dropped_families": sorted(set(dry["families"]) - set(real["families"])),
            "added_families": sorted(set(real["families"]) - set(dry["families"])),
            "note": "batch-gate drift; not a trial count — dry-run specs were "
                    "never scored, so they inflated no maximum"}


def _persist_drift_record(record: dict, registry_path: Path) -> None:
    """Append one JSON line to logs/batch_drift.jsonl, next to wherever the
    registry lives (repo root in production, tmp_path in tests -- so tests
    never spam the live logs/ directory). A file write, NOT a chain write:
    this never touches registry_log.jsonl."""
    log_path = registry_path.resolve().parent / "logs" / "batch_drift.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")


def routable_cards(accepted: dict[str, dict], asset_class: str) -> tuple[dict[str, dict], dict]:
    """Pure selection of accepted cards routable to asset_class.

    Returns (cards, meta) where meta has keys "routing", "routed_card_ids",
    "proxy_routed_card_ids", carrying exactly what run() previously computed
    for the drift record:
      - crypto: all three are None -- no routing applied at all.
      - every non-crypto class: "routing" and "routed_card_ids" are
        populated (a dict and a sorted list respectively).
      - fx: "proxy_routed_card_ids" stays None -- fx has no proxy lane, so
        None here means "the lane never ran", never "ran and found none".
      - equity_etf / metal_etf / bond_etf: "proxy_routed_card_ids" is
        always a list (possibly empty) -- these classes' proxy lanes always
        run, so an empty list means "ran and found none".
    The returned cards mapping may alias `accepted` itself (the crypto path
    returns it directly, unfiltered) -- callers must not mutate it.

    Moved out of run() so pipeline/loop.py watermarks count the SAME set the
    composer would consume. Chain untouched; no side effects.
    """
    # Card routing (spec s4/D2): crypto stays unrestricted (every accepted
    # card feeds it, unchanged). A non-crypto class only shows the proposer
    # cards tagged for its ROUTING entry; reader.py:162 defaults an untagged
    # card's asset_classes to ["cross"], so a card with no tags at all is
    # defensively treated as ["cross"] here too rather than dropped.
    routing_info = None
    routed_card_ids = None
    proxy_routed_card_ids = None
    if asset_class == "crypto":
        propose_input = accepted
    else:
        eligible_tags = set(ROUTING[asset_class])
        propose_input = {
            cid: c for cid, c in accepted.items()
            if set((c.get("tags") or {}).get("asset_classes") or ["cross"]) & eligible_tags
        }
        # Track 2a / spec s10.8: the futures->equity_etf PROXY lane. A
        # futures-tagged card is not eligible via ROUTING above (futures
        # cards never carry an "equities" or "cross" tag by definition of
        # this check), so it can only reach the proposer here, and only when
        # its topics intersect the declared INDEX_FUTURES_PROXY_TOPICS set.
        # Additive to propose_input (a dict keyed by card_id, so a card that
        # somehow matched both paths is never double-counted or duplicated).
        if asset_class == "equity_etf":
            proxy_cards = {
                cid: c for cid, c in accepted.items()
                if "futures" in ((c.get("tags") or {}).get("asset_classes") or [])
                and set((c.get("tags") or {}).get("topics") or []) & INDEX_FUTURES_PROXY_TOPICS
            }
            proxy_routed_card_ids = sorted(proxy_cards)
            propose_input = {**propose_input, **proxy_cards}
        # Track 2b / addendum "Routing": the futures->metal_etf PROXY lane,
        # same shape as the futures->equity_etf lane above (a futures-tagged
        # card is invisible to the native `commodities`/`cross` ROUTING
        # entry, so it can only reach the proposer here, and only when its
        # topics intersect METALS_PROXY_TOPICS). METALS_PROXY_TOPICS is
        # measured EMPTY today (composer.py comment above it), so
        # proxy_cards is always {} on a real run right now -- the branch
        # still runs (and still sets proxy_routed_card_ids to [] rather than
        # leaving it None) so an equity_etf-style "the lane ran and found
        # none" record is written, not "this run predates the lane".
        elif asset_class == "metal_etf":
            proxy_cards = {
                cid: c for cid, c in accepted.items()
                if "futures" in ((c.get("tags") or {}).get("asset_classes") or [])
                and set((c.get("tags") or {}).get("topics") or []) & METALS_PROXY_TOPICS
            }
            proxy_routed_card_ids = sorted(proxy_cards)
            propose_input = {**propose_input, **proxy_cards}
        # Track 2b / addendum "Routing": bond_etf's entire rates->bond_etf
        # lane is declared a PROXY (unlike equity_etf/metal_etf, where only a
        # topic-matched futures subset is proxy and the rest of ROUTING is
        # native) -- the parent spec's table calls it that because rates
        # cards are largely about rate FUTURES/derivatives, not the cash
        # ETFs themselves. So every card that reached propose_input via the
        # "rates" tag (BOND_ETF_PROXY_TAGS) is proxy; a card that reached it
        # only via "cross" (asset-class-agnostic by definition) is native.
        # No separate topic-matching step is needed here -- unlike the
        # futures->equity_etf/metal_etf lanes, this is a whole-TAG proxy, not
        # a whole-CLASS-minus-topic-filter proxy.
        elif asset_class == "bond_etf":
            proxy_routed_card_ids = sorted(
                cid for cid, c in propose_input.items()
                if set((c.get("tags") or {}).get("asset_classes") or ["cross"]) & BOND_ETF_PROXY_TAGS
            )
        routed_card_ids = sorted(propose_input)
        routing_info = {"asset_class": asset_class,
                        "eligible_tags": sorted(eligible_tags)}

    meta = {"routing": routing_info, "routed_card_ids": routed_card_ids,
            "proxy_routed_card_ids": proxy_routed_card_ids}
    return propose_input, meta


def _drain_sibling_queue(args, registry: Registry, queue_state: dict,
                         queue_path: Path, known_fps: dict[str, str],
                         per_cycle_specs: int | None) -> int:
    """D10: register one window off this class's sibling queue, propose nothing.

    A drain replaces THIS STAGE's proposal, not the whole cycle: the composer
    makes no metered model call, and registers at most the same per-cycle
    window a proposal would. The loop's other metered stage (the triage panel)
    still runs as usual -- the saving is composer-level, not cycle-level.

    Every drained spec is still screened against the chain. A queued spec was
    never registered, so a collision here means some OTHER run registered an
    equivalent composition while this one sat in the queue -- that is the
    de-duplication rule doing its job, not the cap discarding work, and the
    composition is under test either way.

    The queue is only MUTATED on a real run. The loop invokes the composer
    twice per cycle (a --dry-run preflight, then the real run); a dry run that
    consumed the queue would leave the real run with nothing to register.
    """
    drained = loop_state.dequeue_specs(queue_state, args.asset_class,
                                       per_cycle_specs or 10 ** 9)
    remaining = loop_state.queue_depth(queue_state, args.asset_class)
    fresh, collided = [], []
    for spec in drained:
        (collided if composition_fingerprint(spec) in known_fps else fresh).append(spec)

    print(f"QUEUE DRAIN ({args.asset_class}): {len(drained)} queued sibling(s) "
          f"taken, {len(fresh)} to register, {len(collided)} now collide with "
          f"the chain, {remaining} still queued. No families proposed (D10: a "
          f"drain replaces this stage's proposal, it does not add to it; the "
          f"cycle's triage panel still runs and still costs).")
    for spec in collided:
        print(f"    - queued {spec['strategy_id']} dropped: composition "
              f"already registered as {known_fps[composition_fingerprint(spec)]}")

    _persist_drift_record(
        drift_record(args.run_id, args.dry_run, fresh), args.registry)

    if args.dry_run:
        print(f"\nDRY RUN — queue drain only; {len(fresh)} spec(s) would be "
              f"registered, nothing written and the queue is untouched.")
        return 0

    # loop_state.json is Coen-editable, so a queued spec is re-validated
    # against the schema before it can reach the chain -- the proposal path
    # validates every spec it registers and the drain path must not be the
    # softer door into the same registry.
    import jsonschema
    validator = jsonschema.Draft202012Validator(json.loads(
        (Path(__file__).resolve().parent.parent / "schemas"
         / "strategy_spec.schema.json").read_text(encoding="utf-8")))
    for spec in fresh:
        validator.validate(spec)

    existing = registry.block_types()
    n_blocks = 0
    for key in BLOCK_TYPES:
        if key not in existing:
            registry.register_block_type(block_type_payload(*key))
            n_blocks += 1
    n_written = 0
    dead: list[dict] = []
    try:
        for spec in fresh:
            # ValueError ONLY, and per spec (P2-T4 review F3). A queued spec
            # can outlive the card that justified it -- review_card may revoke
            # an acceptance at any time, and register_strategy then refuses it
            # forever. Raising here would wedge the whole class: every
            # subsequent drain re-hits the same spec, nothing after it ever
            # registers, and the queue behind it is stranded with neither a
            # verdict nor any prospect of one. That inverts the invariant D10
            # exists to serve, so the offender is parked (visibly, with the
            # registry's own reason) and the drain continues.
            #
            # Deliberately NOT a bare except: an OSError on the chain file, a
            # KeyboardInterrupt or a hash failure is an infrastructure problem
            # that must still abort loudly through the handler below. Only the
            # registry's own domain refusal is survivable.
            try:
                registry.register_strategy(spec)
            except ValueError as exc:
                dead.append({"spec": spec, "reason": str(exc)[:300]})
                print(f"  DEAD queued {spec['strategy_id']}: {exc}")
                continue
            n_written += 1
            print(f"  registered {spec['strategy_id']}  {spec['name']}")
    except BaseException:
        # The queue has NOT been saved yet at this point, so the unwritten
        # remainder is still on disk and the next cycle re-drains it. Only the
        # specs already chained above are duplicated on that re-drain, and the
        # screen at the top of this function catches them.
        print(f"\nPARTIAL WRITE: {n_written}/{len(fresh)} queued spec(s) chained "
              f"before failure; the queue on disk is unchanged, so the next "
              f"cycle re-drains this window.", file=sys.stderr)
        raise
    loop_state.record_dead_specs(queue_state, args.asset_class, dead)
    loop_state.save(queue_path, queue_state)

    dead_txt = (f", {len(dead)} moved to sibling_queue_dead (the registry "
                f"refuses them; they need a human)" if dead else "")
    print(f"\nqueue drain complete: {n_written} spec(s) registered, "
          f"{remaining} still queued for {args.asset_class}{dead_txt}, "
          f"{n_blocks} block type(s) newly registered.")
    return 0


def run(argv: list[str] | None = None, propose_fn=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--max-families", type=int, default=8)
    ap.add_argument("--sibling-cap", type=int, default=SIBLING_CAP_DEFAULT)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--registry", type=Path,
                    default=Path(__file__).resolve().parent.parent / "registry_log.jsonl")
    ap.add_argument("--run-id",
                    default=datetime.now(timezone.utc).strftime("%Y-%m-%d") + "-manual")
    ap.add_argument("--dry-run", action="store_true",
                    help="print families and specs, do not write to the registry")
    ap.add_argument("--asset-class", choices=sorted(cells.CLASSES), default="crypto",
                    help="cell class to compose for (spec s4); crypto is "
                         "today's unrestricted default and every new branch "
                         "below is guarded on asset_class != 'crypto'")
    ap.add_argument("--assets", default=None,
                    help="comma-separated subset of this class's ACTIVE assets "
                         "to sweep THIS generation (spec s5/D6: the loop's "
                         "rotation window, or a manual override for a hand "
                         "run). A schedule, never a filter -- omitted means "
                         "the whole active set, exactly as before D6")
    ap.add_argument("--data-dir", type=Path, default=None,
                    help="bar cache, read ONLY to date each cell's data for the "
                         "D9 re-trial window; defaults next to --registry so a "
                         "tmp-registry test never reads the live cache")
    ap.add_argument("--loop-state", type=Path, default=None,
                    help="where the D10 sibling queue is persisted; defaults to "
                         "logs/loop_state.json next to --registry, for the same "
                         "test-isolation reason as --data-dir")
    args = ap.parse_args(argv)

    # --assets is a view onto the ACTIVE CELL set, and the legacy POOLED path
    # has none: its universe comes from ALLOWED_ASSETS + UNIVERSE_BASE, not
    # from cells.active_cells. Refuse rather than accept an argument that
    # would be silently ignored.
    #
    # Keyed on the REAL ROUTING DISPATCH, not on the literal string "crypto"
    # (P2-T4 review F2). The two are the same fact today, but only the
    # dispatch stays correct through Phase 3: that commit switches
    # expander_for("crypto") to the per-cell path, and on the same commit this
    # refusal must stop firing -- because pipeline/loop.py starts emitting a
    # rotation window for crypto at exactly that moment. Written against the
    # class name instead, Phase 3 would have produced a guaranteed nonzero
    # composer exit on every crypto fire (stage_failed, Sentinel FAIL, three
    # times a day) until someone noticed. Same convention the P2-T3 rider
    # established for the benchmark pin: read the dispatch, never a proxy
    # for it.
    if args.assets and expander_for(args.asset_class) is expand_family:
        print(f"--assets has no meaning on the legacy pooled path that class "
              f"{args.asset_class!r} still routes through "
              f"(composer.expander_for({args.asset_class!r}) is expand_family, "
              f"whose universe is ALLOWED_ASSETS/UNIVERSE_BASE, not active "
              f"cells). It becomes meaningful when that class moves to "
              f"expand_family_for_class -- for crypto, SP5 Phase 3's "
              f"activation commit.")
        return 1
    sweep_assets = ([a.strip() for a in args.assets.split(",") if a.strip()]
                    if args.assets else None)

    registry = Registry(args.registry)
    accepted = registry.cards(status="accepted")

    conflicts = preflight_block_types(registry)
    if conflicts:
        for c in conflicts:
            print(f"  GRAMMAR CONFLICT: {c}")
        print("Aborting — grammar changes must be additive (new types); "
              "never mutate a chained params_schema.")
        return 1

    # D6/D10 per-cycle bound, expressed in the unit run() actually splits:
    # EXPANDED SPECS. The cap has always been a bound on the family's SWEEP
    # (its combo count), and the per-cell expander multiplies every combo by
    # the cells it sweeps -- so the equivalent post-expansion bound is
    # sibling_cap x specs-per-combo. Computing it this way is what keeps D10
    # byte-identical for every family that fits: a family of n <= cap combos
    # expands to at most cap x per_combo specs and is therefore never split.
    # Only families that TODAY are refused outright are affected.
    try:
        specs_per_combo = (1 if args.asset_class == "crypto"
                           else max(1, len(sweep_cells(args.asset_class, sweep_assets))))
    except ValueError as exc:
        print(f"  {exc}")
        return 1
    per_cycle_specs = (args.sibling_cap * specs_per_combo
                       if args.sibling_cap and args.sibling_cap > 0 else None)
    queue_path = (args.loop_state if args.loop_state is not None
                  else args.registry.resolve().parent / "logs" / "loop_state.json")
    data_dir = (args.data_dir if args.data_dir is not None
                else args.registry.resolve().parent / "data")

    known_fps = registered_fingerprints(registry)

    # -- D10 drain, BEFORE any proposal -------------------------------------
    # family-openness-v1: the queued remainder of an over-cap family is fed
    # into the NEXT cycle for that class BEFORE new families are proposed,
    # draining until empty. A draining invocation does NOT propose, so THIS
    # STAGE makes no metered model call (a real saving, not just an ordering);
    # the cycle's triage panel is a separate stage and still runs. It keeps
    # the per-cycle bound meaning what it says -- one window of siblings per
    # cycle, whether they are new or carried.
    #
    # ORDERED BEFORE THE "no accepted cards" REFUSAL ON PURPOSE (P2-T4 review
    # F3, second half). That refusal is a PROPOSAL precondition -- you cannot
    # invent families without cards -- and it is not a drain precondition: a
    # queued spec was already proposed and already cites its cards. Checked
    # first, it wedges the queue exactly as the poison pill did, one gate
    # earlier: revoke the last accepted card and every subsequent cycle exits
    # 1 without ever looking at the queue, stranding it forever.
    queue_state = loop_state.load(queue_path)
    if loop_state.queue_depth(queue_state, args.asset_class):
        return _drain_sibling_queue(args, registry, queue_state, queue_path,
                                    known_fps, per_cycle_specs)

    if not accepted:
        print("No accepted cards in the registry — run the Reader and triage first.")
        return 1

    # Card routing (spec s4/D2): see routable_cards() for the full lane
    # breakdown (crypto unrestricted; fx/equity_etf/bond_etf/metal_etf
    # filtered + proxy lanes). Extracted so pipeline/loop.py's watermark can
    # compute the same routable set without duplicating this logic.
    propose_input, routing_meta = routable_cards(accepted, args.asset_class)
    routing_info = routing_meta["routing"]
    routed_card_ids = routing_meta["routed_card_ids"]
    proxy_routed_card_ids = routing_meta["proxy_routed_card_ids"]

    if propose_fn is None:
        proposals = propose_families(args.model, propose_input, args.max_families,
                                     asset_class=args.asset_class)
        # NB the dry run reaches here too: it makes the same call and costs the
        # same, so it is metered the same.
    else:
        proposals = propose_fn(propose_input)
    if len(proposals) > args.max_families:
        print(f"  NOTE: {len(proposals) - args.max_families} families beyond "
              f"--max-families {args.max_families} discarded unvalidated.")
        proposals = proposals[:args.max_families]

    import jsonschema
    schema = json.loads(
        (Path(__file__).resolve().parent.parent / "schemas"
         / "strategy_spec.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)

    created_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    accepted_ids = set(accepted)
    run_fps: dict[str, str] = {}
    # D9: burial with EXPIRY, not permanent exclusion. The oracle opens only
    # for a matched registration that is currently BURIED, whose burying
    # verdict's cutoff is readable, and whose target cell's data on disk has
    # moved >= RETRIAL_WINDOW_DAYS past it. Quarantine and live registrations
    # have no burying verdict, so they never expire.
    retrial_ok = retrial_oracle(registry, args.registry.resolve().parent / "artifacts",
                                data_dir=data_dir)
    # T4-rider-3 (spec s10.7 addendum, track 2a): the exclusion set is now a
    # per-class DECLARATION (cells.CLASSES[cls]["excluded_block_types"])
    # instead of an inferred "any non-crypto class" rule -- equity_etf has
    # real OHLC bars and excludes nothing, unlike fx's single-fix bars.
    # crypto's declared set is frozenset() too, so this is byte-identical to
    # the old `if args.asset_class != "crypto" else frozenset()` branch for
    # every class that existed before this change.
    excluded_types = cells.CLASSES[args.asset_class]["excluded_block_types"]
    kept, dropped, seen_names = [], 0, set()
    carried: list[dict] = []            # D10 overflow, queued at the end of run()
    for fam in proposals:
        name = fam.get("family", "?")
        errors = validate_family(fam, accepted_ids, args.sibling_cap,
                                 excluded_types=excluded_types,
                                 asset_class=args.asset_class)
        if name in seen_names:
            errors.append("duplicate family name in this run")
        if errors:
            dropped += 1
            print(f"  DROPPED family {name}:")
            for e in errors:
                print(f"    - {e}")
            continue
        seen_names.add(name)
        # The routing decision itself lives in expander_for() so it can be
        # observed by a test, not just executed here (P2-T3 rider); the two
        # expanders take different arguments, so the call still forks.
        expander = expander_for(args.asset_class)
        if expander is expand_family:
            specs = expander(fam, args.run_id, args.model, created_utc)
        else:
            specs = expander(
                fam, args.run_id, args.model, created_utc, args.asset_class,
                proxy_card_ids=frozenset(proxy_routed_card_ids or ()),
                assets=sweep_assets)
        kept_specs, drop_notes, malformed = screen_siblings(
            specs, known_fps, run_fps, retrial_ok=retrial_ok)
        if malformed:
            dropped += 1
            print(f"  DROPPED family {name}:")
            for note in drop_notes:
                print(f"    - {note}")
            continue

        # Split the three outcomes: a chain collision is expected saturation
        # as the grammar space fills, a family duplicated under two names in
        # one run is a proposal-quality defect, and a D9 RE-TRIAL is a buried
        # composition readmitted on new data. Reporting them together would
        # send the operator after the wrong thing.
        kept_ids = {s["strategy_id"] for s in kept_specs}
        n_retrial = sum(1 for s in specs
                        if s["strategy_id"] in kept_ids
                        and composition_fingerprint(s) in known_fps)
        n_buried = sum(1 for s in specs
                       if composition_fingerprint(s) in known_fps) - n_retrial
        n_dupe = len(specs) - len(kept_specs) - n_buried
        dupe_txt = f", {n_dupe} duplicated in this run" if n_dupe else ""
        retrial_txt = f", {n_retrial} re-trial(s) (D9)" if n_retrial else ""
        print(f"  family {name}: {len(specs)} expanded, {n_buried} already "
              f"registered{dupe_txt}{retrial_txt}, {len(kept_specs)} new")
        for note in drop_notes:
            print(f"    - {note}")
        if not kept_specs:
            # expand_family always yields at least one combo, so an empty
            # kept_specs here is always a genuine all-collide, never a
            # vacuous family reported as one.
            dropped += 1
            print(f"  DROPPED family {name}: every sibling already registered"
                  + (" or duplicated in this run" if n_dupe else ""))
            continue
        for spec in kept_specs:
            validator.validate(spec)   # composer bug if this raises: abort pre-write
            run_fps[composition_fingerprint(spec)] = name
        # D10 split-and-carry, per FAMILY (exactly where the cap used to
        # refuse, so a family that fits is untouched): the first window
        # registers now, the remainder queues and drains on later cycles.
        # Nothing is dropped -- the two halves are `kept_specs`, in order.
        this_cycle, overflow = split_for_cycle(kept_specs, per_cycle_specs)
        if overflow:
            print(f"  QUEUED family {name}: {len(this_cycle)} sibling(s) this "
                  f"cycle, {len(overflow)} carried to the next {args.asset_class} "
                  f"cycle (D10 -- a cap is a schedule, not a verdict)")
            carried.extend(overflow)
        kept.append((fam, this_cycle))

    total = sum(len(s) for _, s in kept)

    # The batch-gate drift: what got approved at THIS invocation (dry or
    # real) vs what a later real run actually chains. Written for both modes
    # so the gap stops being invisible -- gen 3's approved dry run was 20
    # specs, the real run chained 24 with a directional mirror dropped.
    all_specs = [s for _, specs in kept for s in specs]
    _persist_drift_record(
        drift_record(args.run_id, args.dry_run, all_specs,
                    routing=routing_info, routed_card_ids=routed_card_ids,
                    proxy_routed_card_ids=proxy_routed_card_ids),
        args.registry)

    for fam, specs in kept:
        print(f"family {fam['family']}: {len(specs)} sibling(s), "
              f"cites {len(fam['card_ids'])} card(s) — {fam['rationale']}")
        print(f"  regime hypothesis: {fam.get('regime_hypothesis', '(none)')}")
        if args.dry_run:
            for spec in specs:
                print(json.dumps(spec, indent=2, ensure_ascii=False))

    if args.dry_run:
        print(f"\nDRY RUN — {len(kept)} families kept, {dropped} dropped, "
              f"{total} sibling spec(s), {len(carried)} would queue (D10); "
              f"nothing written.")
        return 0

    existing = registry.block_types()
    n_blocks = 0
    for key in BLOCK_TYPES:
        if key not in existing:
            registry.register_block_type(block_type_payload(*key))
            n_blocks += 1
    n_written = 0
    try:
        for fam, specs in kept:
            for spec in specs:
                registry.register_strategy(spec)
                n_written += 1
                print(f"  registered {spec['strategy_id']}  {spec['name']}")
    except BaseException:
        print(f"\nPARTIAL WRITE: {n_written}/{total} spec(s) chained before failure — "
              f"the last sibling group is incomplete; review the registry tail "
              f"before re-running.", file=sys.stderr)
        raise

    # D10: the carried remainder is persisted AFTER the chain writes above,
    # never before. A queue entry written for work that then failed to chain
    # would double-register it on the next drain; written last, a crash leaves
    # the overflow unqueued, which the very next generation re-proposes -- the
    # honest failure direction of the two.
    if carried:
        queue_state = loop_state.load(queue_path)
        loop_state.enqueue_specs(queue_state, args.asset_class, carried)
        loop_state.save(queue_path, queue_state)
        print(f"  QUEUE: {len(carried)} sibling(s) carried to the next "
              f"{args.asset_class} cycle (depth now "
              f"{loop_state.queue_depth(queue_state, args.asset_class)}) "
              f"-> {queue_path}")

    print(f"\n{len(kept)} families kept, {dropped} dropped, {total} spec(s) "
          f"registered in {len(kept)} sibling group(s), "
          f"{n_blocks} block type(s) newly registered.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
