---
paths:
  - "pipeline/beats_hold.py"
  - "pipeline/protocols.py"
  - "pipeline/livegate.py"
  - "pipeline/gauntlet_core.py"
  - "pipeline/test_beats_hold*.py"
  - "pipeline/test_protocols.py"
  - "docs/notes/gauntlet-protocol-v6.2.md"
  - "docs/notes/quarantine-live-protocol-v2.md"
---
## Beats-hold (gauntlet v6.2 / live gate v2, 2026-11-05)
Design: `docs/2026-11-05-beats-hold-design.md` (BH1-BH5, Coen's calls). A
strategy must beat holding its own asset, risk-adjusted (Sharpe vs Sharpe),
at BOTH stages. Rehearsal readout: `docs/runs/2026-11-05-beats-hold-rehearsal.md`
(docs/runs/ is gitignored, so it exists only in the tree that ran it).
- **Two dormant switches, each armed only by its own chained note (BH5).**
  The code merges first and keeps running v6.1 / v1, byte for byte, until
  the note is on the chain. Then the next run switches by itself. Nothing
  refuses in between. Arming is `text.startswith(prefix)`, and the prefix
  includes its colon and space: `gauntlet-protocol-v6.2: ` (gauntlet
  worker) and `quarantine-live-protocol-v2: ` (live gate). A note that
  only mentions the name, or a second copy of the note, changes nothing
  (tests pin both). The two notes are independent: either can go first.
  Texts: `docs/notes/gauntlet-protocol-v6.2.md` and
  `docs/notes/quarantine-live-protocol-v2.md`, both CHAINED 2026-11-06
  (entries 191,597 and 191,598), so both switches are armed. A superseding
  note is chained the same way, verbatim after Coen approves its text, with
  `python -m tools.grid_switch chain-note --prefix <name>:`.
  `--prefix` takes `<name>:` (the tool
  refuses one not ending in ":"), but the note's text must start
  `<name>: ` (colon AND space) to arm, because the arming check includes
  the space: a note chained as `<name>:text` passes the tool and arms
  nothing. Never edit a note after it is chained.
- **Gauntlet (v6.2): an OPT-IN battery, never an edit of `FAIL_ORDER`.**
  `gauntlet_core.evaluate_standalone(..., battery="v6.1")` is the default.
  It is the six gates, with a result byte-identical to before (the parity
  tools and the legacy `pipeline/gauntlet.py` use it). `battery="v6.2"`
  runs the same six gates, then `beats_hold` LAST (`FAIL_ORDER_V62`):
  strict `Sharpe(strategy OOS) > Sharpe(hold OOS)` on the date
  intersection of daily marks (`bars.daily_bars`, so an intraday cell is
  not mis-annualised). A None Sharpe (fewer than 30 returns, or a flat
  book) FAILS. The hold is bought at the first OOS bar's open, and one
  round trip of the class cost model is charged. A multi-asset spec uses
  the mean of the per-asset levels. `metrics.beats_hold` is recorded
  (strategy_sharpe, hold_sharpe, n_days, window, basis) for every class
  with `benchmark: "self"`; the gate does not apply to any other class.
  `metrics.protocol` is `gauntlet-protocol-v6.2`. Only the worker passes
  "v6.2", and only once the note is chained. It still REFUSES without the
  v6.1 note, and the battery is fixed per run (a note chained mid-run
  takes effect at the next run). Code: `pipeline/beats_hold.py`
  (`hold_oos_returns`, `gauntlet_beats_hold`).
- **Live gate (v2): graduation must beat zero AND the hold (BH4).**
  `livegate.assess(..., hold_arm=True)`. `run` sets it when the v2 note is
  chained. The kill arm is v1's, unchanged. For each eligible survivor
  of the kill pass, `p = max(p_zero, p_hold)`, then Benjamini-Hochberg as
  before:
  - `p_zero = 1 - PSR vs 0` (v1's test);
  - `p_hold` = a one-sided studentized circular block bootstrap of the
    paired Sharpe difference against `forward_hold_series(rows)`: the
    quarantine rows' prices, rebased at the first forward row, with no
    cost charged.
  Seed `int(sid,16) % 2**31` (0 for a non-hex id). The report gains
  p_hold, paths, p, SR and hold-SR columns; the console line gains
  `p_hold`, `p_hold_paths` and `p`; a v2 verdict's metrics gain `p_zero`,
  `p_hold`, `p` and `p_hold_paths` (all null on a burial). A record that
  cannot be paired (its equity touched 0) gets p_hold = 1.0 and never
  graduates; so does one where any asset's FIRST row price is 0 or below
  (the registry requires a finite price only; `forward_hold_series`
  returns `[]`, final review M1). Neither raises.
  - **The floor refinement (final review I1, `beats_hold.hold_pvalue`).**
    2000 paths floor p_hold at 1/2001, and BH needs p <= q/m for a lone
    rejection, so above about 100 survivors no single strategy could
    graduate (the cohort is heading toward ~900). Two stages: every
    survivor is tested at `HOLD_TEST_PATHS`; one whose p_hold is EXACTLY
    1/2001 is redrawn with the same seed and `refined_paths(m, q)` =
    ceil(2 m / q) paths (m = survivors hold-tested this assessment, q the
    `--q` passed to `assess`), only when that exceeds 2000. Any other p is
    kept. `p_hold_paths` records which. Under v1 nothing changes.
- **Constants (the plan's calls, recorded in the v2 note; changing one is
  a protocol change):** `beats_hold.HOLD_BLOCK_LEN = 5` (days),
  `HOLD_TEST_PATHS = 2000`, numpy `default_rng(seed)`, `SR_DIFF_TOL =
  1e-9`. When |per-period Sharpe difference| < SR_DIFF_TOL, p_hold = 1.0.
  Without that floor a pure-beta or scaled-copy book (difference 0 up to
  rounding) would graduate about 5% of the time by noise. A constant
  series, or one with zero variance, also gets 1.0. One test call at T=500
  takes about 0.05 s. `HOLD_CHUNK_PATHS = 2000` (also in the note) bounds
  memory: every block start is drawn first in ONE `rng.integers(0, T,
  size=(paths, ceil(T/5)))` call, then the rows are evaluated 2000 at a
  time, so the chunk size never changes a p-value and a call at <= 2000
  paths is bit-identical to the unchunked code (pinned by recorded values
  in `test_beats_hold.py`). One refined call at T=500, m=900 (36,000
  paths) takes about 0.95 s with a traced peak of about 107 MB; unchunked
  its (paths, T, 4) array alone would be about 576 MB.
- **`pipeline/protocols.py` holds the names.** `V61_FAMILY =
  (gauntlet-protocol-v6.1, gauntlet-protocol-v6.2)` is what every
  exact-match on a verdict's `metrics.protocol` accepts:
  - the worker's orphan repair;
  - `gauntlet_stats` (stats for v6.2 verdicts);
  - the registry stats snapshot;
  - the loop's `gauntlet_orphan` exclusion;
  - verifier invariant 11.

  **A new protocol string MUST join `V61_FAMILY`.** Otherwise its verdicts
  silently drop out of stats, orphan repair and the verifier, and the 20:00
  loop FAILs `gauntlet_orphan` (`test_protocols.py` greps for stray
  literals). The note-prefix checks that arm v6.1 rules stay keyed on
  `PROTOCOL_V61` alone.
- **Rollback, in three stages (each switch on its own note).**
  1. **Before a note is chained:** a revert of the MERGE is safe.
     Behaviour is unchanged (parity-proven) and nothing on the chain names
     v6.2 or live v2.
  2. **After a note is chained:** ANY rollback, revert, or forward disarm
     of that switch (for example code that ignores the note) needs a
     SUPERSEDING chained note FIRST, approved by Coen. The chained text
     says the next run judges under it, so code that stops doing so would
     judge under a chained protocol that says otherwise; same as the
     amendment-2 rule ("Effective trials per cell"). Gauntlet: after the
     v6.2 note, the worker must not label or judge v6.1 again until a note
     says so. **Live v2 equivalent:** after the
     `quarantine-live-protocol-v2: ` note, the live gate must not assess
     under v1 again (graduation by PSR alone, or without the floor
     refinement) until a superseding note is chained. v2 TIGHTENS v1, so
     going back is a LOOSENING, and v1's standing rule applies: the
     evidence and the argument go on the chain before the results they
     would affect exist. This holds even if no live_gate verdict has been
     chained under v2 yet.
  3. **After the FIRST v6.2 verdict is chained:** additionally, a forward
     commit ONLY, never a revert, keeping `PROTOCOL_V62` in `V61_FAMILY`.
     Otherwise verifier invariant 11 rejects those verdicts'
     `gauntlet_stats` entries (chain INVALID), and the loop's
     `gauntlet_orphan` check counts any that is still in `gauntlet` state.
     Nothing matches `live_gate` verdicts by protocol string, so live v2
     has no family tuple and no stage 3: stage 2 is its whole rule.

