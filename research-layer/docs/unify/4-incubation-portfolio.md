# Scope 4 — One incubation loop and the portfolio layer

**Status:** scoped, not designed. Needs its own brainstorm → spec → plan.
**Depends on:** Scope 2 (you need one designation registry before you can blend
across origins).
**Independent of:** Scope 3.

## Problem

Two daily writers now run against two different append-only ledgers:

| | Writer | Ledger | Schedule |
|---|---|---|---|
| `trading-systems` | `python -m bot run` | `botstate/ledger.jsonl` (hash-chained, committed) | `15_PaperBot` 08:10 local |
| research layer | `python -m pipeline.quarantine --date D` | `research-layer/registry_log.jsonl` | not yet scheduled |

They record the same *kind* of thing — a paper-traded forward record for a
registered system — in two formats, on two cadences, with two idempotency
models. Nothing blends them, so there is no portfolio view across the nine
designated systems and no measurement of whether the two origins diversify each
other.

**The concurrency hazard is not hypothetical.** This repo has been bitten twice
by unsynchronised writers: the Reader v2 scanner swept another session's staged
files into its own commit, and the `16_TestnetBot` 08:12 run rewrote
`tally-latest.md` from scratch two minutes after `15_PaperBot`'s 08:10 chain
append, deleting the HOUSE-CORE and decay sections every day until it was caught.
Adding a third daily writer without deciding this deliberately would be the third
instance of the same mistake.

## Scope IN

- **Decide whether the two loops converge or stay formally independent**, and
  write the decision down either way. Both are defensible; drifting into "both,
  unsynchronised" is not.
- If they stay independent: an explicit non-interference contract — separate
  pathspecs, separate ledgers, non-overlapping schedules, and a documented reason
  why neither can clobber the other.
- **Schedule the research layer's daily runner.** It exists and is proven but is
  not yet a scheduled task. Its first recordable day is 2026-08-18.
- **A portfolio layer across both origins**: correlation and diversification
  measurement over the designated set, in the shape `strat/bundle.py` already
  proved — the BUNDLE certified at Sharpe 1.958 vs best single leg 1.495,
  **+31% from pure diversification**, participation ratio 3.02/4.
- A combined view surfaced where Coen already looks: the Morpheus `/pipeline`
  dashboard, which already renders the research-layer chain, and
  `botstate/status.json`, which already conforms to `AGENT_STATUS_CONVENTION`.

## Scope OUT

- **Live capital.** The router's activation runbook is at step 3 of 7 and step 4
  is a Coen decision. Untouched by this.
- Changing either system's kill wires, success bands or registration hashes.
- Merging the two hash chains into one. They are separate witnessed records with
  separate genesis; merging destroys both. Cross-referencing is the most that
  should happen.

## Decisions needed before this can be specced

1. **Converge or separate?** Convergence gives one loop, one ledger format, one
   audit. It also means rewriting a proven, running, hash-chained system that has
   a real track record accruing since July — high risk against a working thing.
   **My lean: stay separate, contract the boundary explicitly, and blend at the
   reporting layer only.** The bot's own design principle already supports this —
   `bot/status.py` composes from producers and re-derives nothing.
2. **The portfolio layer is premature for forward evidence and available for
   backtest evidence.** The three research-layer systems have **zero forward
   days**; the six have months. A forward-correlation measurement cannot run
   until roughly 2026-11 at the earliest. A backtest-correlation measurement can
   run today. Decide which is being built, and do not present the second as the
   first.
3. **Timeframe mismatch is real.** The research layer is daily-bar; the six
   trading systems are 1h/4h/12h. A correlation matrix across them needs an
   agreed resampling convention — almost certainly daily returns from each
   system's own equity curve, but it must be stated, not assumed.
4. **Whose weights?** `trading-systems` already has UPT-style blending machinery
   in its own tree, and the SOP's Phase 7 says each per-asset system is a return
   stream into it. The cleanest answer may be that the portfolio layer *is* that
   existing machinery, fed one extra return stream per research-layer system —
   no new blending code at all.

## Success criteria

- Exactly one documented answer to "which process writes what, when, and what
  stops them colliding."
- The research-layer daily runner is scheduled and its first forward days are
  accruing.
- A correlation/diversification number exists across the two origins, with its
  evidentiary basis (backtest vs forward) stated on its face.
- Neither existing ledger's integrity or replay is affected.

## Hazards

- **The 08:10 / 08:12 clobber is the precedent.** Any new schedule must be
  reasoned about against the existing two, not just given a free slot.
- The research-layer runner refuses a re-run whose `bars_sha256` no longer
  matches. A daily data refresh that only appends future bars is correctly a
  non-event, but a vendor restatement of historical bars will refuse the day —
  by design. Whoever operates the schedule needs to know that a refusal is a
  signal, not a fault.
- `trading-systems`' suite takes ~45 minutes. Budget for it; do not skip it
  before shipping anything that touches that tree.
