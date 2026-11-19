---
paths:
  - "pipeline/composer.py"
  - "pipeline/test_composer*.py"
  - "pipeline/loop.py"
  - "pipeline/loop_state.py"
  - "pipeline/test_phase2_freeze.py"
  - "pipeline/test_verify_registry_d9.py"
  - "verify_registry.py"
  - "docs/notes/family-openness-v1.md"
  - "docs/notes/correction-2026-10-02-retrial-path.md"
---
## Sweep rotation, sibling queues, re-trials (SP5 P2-T4: D6 / D10 / D9)
- **D6 rotation.** `loop.ROTATION_SIZE` = 12 (spec s5); `loop.ROTATION_CLASSES`
  = `("crypto",)`. A rotating class sweeps a window of 12 of its ACTIVE assets
  per generation, cursor `rotation_cursor` per class in loop_state.json,
  advanced ONLY on cycle_complete. **The small-set rule:** an active set of
  <= ROTATION_SIZE assets is returned WHOLE and the cursor never moves, so no
  `rotation_cursor` key is written for a class that does not rotate.
  ROTATION_CLASSES is a DECLARATION, never inferred from the asset count:
  equity_etf's active set is 16 assets (above the window), so an inferred
  "bigger than the window" rule would silently window it 12-of-16 and break
  the Phase 2 sweep freeze. Rotation is a SCHEDULE, never a selection --
  every active cell is swept with equal frequency and N accounting is
  untouched. The loop passes `--assets` only when the window differs from the
  full active list, so the four tradfi classes' composer argv is
  byte-identical to pre-D6.
- **Crypto rotates since Switch 1 (2026-10-17): 76 assets, 12 per window, the
  window WRAPS.** `loop_state.rotation_window` reads `items[(cursor + i) % n]`
  and `advance_rotation` sets `cursor = (cursor + 12) % 76`, so EVERY window is
  exactly 12 wide (never a short last one) and the cursor advances 12 per
  completed cycle. All 76 are reached in 7 windows: windows 1-6 are
  ADMITTED[0:12] .. ADMITTED[60:72]; after 6 advances the cursor is 72 and the
  7th window is ADMITTED[72:] + ADMITTED[:8] (the last 4 assets, then the first
  8 again). The next pass therefore starts 8 assets on (cursor 8), and the
  schedule closes after 19 windows (19 x 12 = 3 x 76): each asset is swept
  exactly 3 times per 19 completed cycles. (The Switch 1 plan's wording "7
  windows, the last 4 wide" was wrong; the code and `test_switch1.py` are the
  record.) A parked or failed cycle leaves the cursor where it was.
- **The D6 "second gate" is GONE (Switch 1).** It read the routing dispatch
  (`expander_for(cls) is expand_family`) in both `loop._sweep_window` and the
  composer's `--assets` refusal, because the legacy POOLED crypto expander had
  no active cells and could not accept a window. Switch 1 deleted that path
  (`composer.expand_family`, `ALLOWED_ASSETS`, `UNIVERSE_BASE`, `COST_MODEL`)
  and the refusal in the commit that activated crypto, so every class's
  composer accepts a window and rotation is gated ONLY by `ROTATION_CLASSES`.
  The advance in `_run_locked_cycle` is keyed on `rotates` (a window was
  emitted), not on class membership. Pinned by `test_switch1.py` and
  `test_phase2_freeze.py`.
- **Ruling 41 -- DONE at Switch 1: the admission line is 365 days (Coen,
  2026-10-17).** An active crypto cell needs at least 365 days of history
  before `screen.DEFAULT_CUTOFF` (2023-12-31), i.e. first bar on or before
  2022-12-31. 76 of the 100 declared assets pass; the 24 excluded stay
  declared but unswept: ENA ETHFI JUP PYTH RENDER STRK TAO WIF ZK ZRO (first
  bar 2024) and GAS GLM PROM ARB SUI FLOKI PEPE PENDLE WLD SEI TIA ORDI JTO
  BONK (first bar 2023-03-17..2023-12-15). (The older ">= 730 days" rule in
  cells.py admitted all of them and is superseded.) Why it matters: a spec on
  a late-starting cell is screened with zero train bars and buried, or, if left
  `proposed` (a screen deadline deferral does this), parks EVERY fire of every
  class at `stale_data` (a re-fetch cannot fix it). Enforced three ways: the
  `ACTIVE_CELLS["crypto"]` literal (import-time check: a subset of the
  declared assets in declaration order), `crypto_refresh.admission_problems`
  (item `crypto_admission` in the refresh ledger if a cell's first bar slips
  past the floor, or its file is missing/empty), and the crypto-grid-record-v1
  note.
- **`docs/2026-08-28-market-data-universe-design.md` s5 is STALE in this
  worktree** on the rotation rule (as is s4 on crypto's benchmark and s7b on
  the resurrection chaining). Trust the code, `docs/notes/family-openness-v1.md`
  and this file.
- **D10 sibling queues.** `validate_family`'s "exceeds cap, rejected, not
  clipped" refusal is GONE (chained pre-declaration:
  `docs/notes/family-openness-v1.md`). Overflow now splits via
  `composer.split_for_cycle` and queues in loop_state.json as
  `sibling_queue` per class; depth is reported as `queue_<cls>` in
  pipeline_status.json. **The composer writes that queue as a SUBPROCESS**, so
  `loop_state.refresh_queues(state, path)` runs right after the composer stage
  or the loop's own save clobbers it. A cycle whose class has a non-empty
  queue DRAINS instead of proposing -- that STAGE makes no metered model call
  (the cycle's triage panel still runs and still costs). Invariant,
  test-pinned: no proposed variation is ever dropped without either a gauntlet
  verdict or a queue entry.
- **A queue is its own trigger, at BOTH gates.** `pick_class` treats
  `queue_depth > 0` as over-threshold, and the `no_new_accepted_cards` stop
  exempts a queued class. Queued work is already proposed and already counted;
  it needs capacity, not new cards. With only one of the two, a class whose
  card flow goes quiet parks its queue forever -- the silent drop D10 removes,
  moved one gate earlier. The trigger BASIS itself is untouched.
- **The drain runs BEFORE the "no accepted cards" refusal**, which is a
  proposal precondition, not a drain one. Revoke the last accepted card with
  that check first and every cycle exits 1 without ever looking at the queue.
- **`sibling_queue_dead`** holds queued specs the registry refuses outright --
  a queued spec can outlive its cited card, because `review_card` may revoke
  an acceptance at any time and `register_strategy` then refuses forever. The
  drain catches `ValueError` PER SPEC (never a bare except -- chain IO errors
  must still abort), parks the offender with the registry's own reason, and
  keeps going. Depth shows as `queue_dead_<cls>` in pipeline_status.json,
  emitted only when non-zero, and it is **a human action item, not a level to
  watch drain** -- it never re-triggers its class.
- **⚠ KNOWN HARM (F5), declared not fixed: a split sweep can manufacture
  `edge_of_grid` plateau failures at the cut.** `plateau.qualifies` reads what
  is on the chain when the gauntlet runs, and the queued combos are not there
  yet, so siblings adjacent to the cut can be failed for a capacity reason
  wearing a statistical costume -- the very thing family-openness-v1 condemns,
  one layer down. Draining later does NOT repair it: those verdicts are
  already written. No PARTITION avoids it (a cartesian product cannot be split
  without severing an axis; cutting on the outer axis makes the window size
  vary with family shape, trading a visible harm for a hidden one) -- but that
  is exhaustive only over ways to CUT the sweep. Three open options, Coen's
  call: (1) accept it as shipped; (2) queue at FAMILY granularity so no sweep
  is cut, which the chained note's wording forecloses; (3) HOLD a split
  sibling group out of the gauntlet until its queue drains, which costs
  latency rather than correctness and does not touch the note at all (the note
  governs the composer's admission, not the gauntlet's batching). Only
  reachable for families that TODAY are refused outright, so nothing
  regresses.
- **D9 re-trials.** `composer.RETRIAL_WINDOW_DAYS` = 183. A composition whose
  fingerprint matches a registered strategy is still dropped UNLESS that
  registration is currently BURIED and its burying verdict's cutoff is >= 183
  days behind the target cell's CURRENT data end. **A composition with no
  burying verdict (quarantine or live) has no expiry and stays permanently
  excluded** -- that half is a tightening. The cutoff is not on the chain; it
  is read from `artifacts/<sid>/gauntlet/config.json` (or the screen bundle's
  `config.json`), so anything unreadable closes the window -- which is also
  why no tmp-registry test opens it. In-run/in-cycle duplicates are still
  malformed/dropped: those ARE same-data. Every re-trial is a NEW strategy id
  entering N honestly. **More registrations and more survivors at a fixed bar
  is arithmetic about the denominator, NEVER evidence of edge** -- see the
  chained note's own wording before reporting any of it.
- **D9 ends chain-wide fingerprint uniqueness on purpose.** All 2,775 pre-D9
  registrations carried distinct composition fingerprints; a re-trial is by
  definition a second registration of one. The surviving invariant is PER RUN:
  no single run may register a composition twice. That is why
  `screen_siblings` checks `fp not in run_fps` before admitting a re-trial --
  without it, family A's re-trial and family B's copy of it both chain, same
  run, same data.
- **`verify_registry.py` invariant 8 enforces that SAME rule, through the same
  function.** `composer.retrial_verdict` is the one implementation; the
  verifier and `retrial_oracle` are both thin readers of it, and a second copy
  of the rule anywhere is a defect. They differ ONLY in how they read a window
  they cannot establish: the composer is deciding, so unreadable = refuse; the
  verifier is checking with strictly less evidence, so unreadable = report
  `window not verifiable` and PASS. A verifier that failed there would call
  the chain corrupt every time an artifact bundle was pruned.
- **`Registry.register_strategy` enforces invariant 10 at WRITE time
  (2026-10-02).** Once the chain holds the exit-rules-v7 note (detected by
  `blocks.is_exit_rules_v7_note`, the verifier's own rule) it raises
  `ValueError` before any write for a spec that is not version 2 or carries a
  `blocks.RETIRED_TYPES` block. Why: the verifier only reports a violation
  after it is chained, so a hand-run writer (e.g. re-registering one of the
  five's version-1 `exit/time_stop` compositions) would leave the append-only
  chain INVALID for good. Verifier tests that need such an entry write it with
  a raw `reg.append`.
- **The window leg is not a chain fact.** The verifier reads the cutoff from
  `artifacts/` and the data end from `data/`, defaulting to beside the log and
  overridable with `--artifacts-dir`/`--data-dir` (the loop passes both). Run
  it against a COPY of the chain without those dirs and it still says VALID,
  but it has only checked the buried-priors and same-run legs -- read the
  NOTE line before concluding a re-trial was verified.
- **⚠ THE 183-DAY WINDOW DOES NOT CURRENTLY BITE. Known protocol gap,
  measured 2026-09-01, behaviour deliberately UNCHANGED -- Coen's call.** The
  cutoff `burying_cutoff` reads is the fixed train/OOS split constant, not a
  per-verdict date: **all 4,065 `config.json` bundles carry
  `cutoff = 2023-12-31`, zero exceptions, zero missing.** So the window is
  OPEN for all 2,702 burials and SHUT for none; the narrowest margin is 964
  days against a 183-day requirement. The first two live re-trials
  (`50f48ae9a07d01cc`, `4f8d2fc81c27f76e`, chain lines 16183/16184) are
  therefore **9-day re-tests** -- buried 2026-08-22, re-registered 2026-08-31,
  gauntlet `data_end` 2026-08-21 against bars ending 2026-08-30 -- on a chain
  that is 26 days old. As shipped, D9 reads as "any buried composition is
  re-triable", and every re-trial charges N in full. **Do not report re-trial
  survivor counts as edge** (the chained note says so itself).
- **If that clock is ever fixed, the fix is NOT uniform across burial
  stages.** The gauntlet bundle's per-cell `data_end` IS an honest clock
  (1175/1218 carry it, all > cutoff) and would have shut the window on both
  live re-trials. But **screen** bundles' `data_end` is train-truncated
  (2767/2767 have `data_end <= cutoff`), so it carries no information -- and
  **1,629 of the 2,702 burials came from `screened`, not `gauntlet`**. A
  re-screen runs a fixed train window and is deterministic, so it returns the
  identical verdict however much new data arrives: whether a screen-buried
  composition should be re-triable at all before the CUTOFF itself moves is an
  open protocol question.
  `test_a_buried_composition_uses_the_screen_cutoff_when_it_never_reached_gauntlet`
  currently asserts that it should. Leave that test alone until the question
  is decided on the chain.

