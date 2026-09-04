# Unifying the two trading-system pipelines — scoping set, 2026-08-17

Coen's goal, in his words: *"what I want at the end of the gauntlet/paper trading
process is successful edges that I can run, either standalone, or as part of an
optimized portfolio, or could potentially create TV indicators for and sell via
Whop. At this point, I just want the process to be uniform between what I've
already done, and what we're currently building."*

## The finding that shapes all four scopes

**The two pipelines differ only in where ideas come from. Everything downstream
should be one spine, and currently isn't.**

| | Idea source | Downstream |
|---|---|---|
| `trading-systems` | condition discovery (SOP Phase 0b scanner: FDR-controlled grid, era-stability split, recency+cost gate, regime tagging) | spec sheet → sweep → Phase 4 gates → designation → Pine → witnessed incubation |
| `stewart-forward-test` research layer | literature (Reader → quote-grounded cards → Composer) | screen → gauntlet → quarantine → live |

Two front ends, one spine. The front ends should stay separate — they are
genuinely different and both are valuable. The spine should be one thing.

## Why absorption was rejected

The obvious move — extend the research layer's grammar until it can express the
six registered systems, then run them through its gates — does not work. Three
reasons, in descending order of how fatal they are:

1. **The fence is already dead for them.** `screen-protocol-v1` trains to
   2023-12-31 and holds out 2024+. The six systems were developed against data
   through 2026 and registered in July–August 2026. Their parameters were chosen
   with the entire holdout visible. A verdict from that screen would look like
   out-of-sample evidence and would not be one — worse than no verdict, because
   the chain would then carry a claim implying a test that never happened.
2. **It creates a third implementation to keep in parity forever.** Two already
   exist (`strat/` in Python, 6 Pine scripts) and establishing they agree took
   **641/641 exact trade comparisons**, surfacing real divergences on the way
   (banker's vs half-away rounding on 74/106 ETH-S trades; `margin_long=100`
   silently trimming >1× entries). `trading-systems/CLAUDE.md` draws this line
   explicitly: copying data across trees is fine, importing code across trees is
   not.
3. **The multiple-testing denominator breaks.** The six came from a search whose
   honest selection burden was **N=1737**. Import them as ordinary research-layer
   specs and either those trials enter the DSR denominator and swamp everything,
   or they don't and the denominator lies.

Note what is *not* a blocker: adding block types is safe. Verified in gen-3 Task
5 — `composition_fingerprint` substitutes only grid values already equal under
`==`, and `preflight_block_types` refuses any change to a chained type's schema,
so new types cannot alter the 80 existing fingerprints. The grammar can grow
cheaply. Growth just doesn't solve the problem.

## The gap, measured

Gates the SOP has and the research layer lacks:

| SOP Phase 4/5 | Research layer |
|---|---|
| **PBO < 20% via CSCV** — the SOP calls this the *primary* tool and walk-forward "the WORST OOS scheme" | absent |
| Harvey-Liu haircut; backtested SR < 0.4 auto-reject | absent |
| Purged walk-forward as corroboration, majority pass + catastrophic veto | absent |
| Regime-conditional report (incl. short-side parabolic bucket) | absent |
| Pine-codeability gate at kickoff | absent |
| Pre-committed kill wire + success band per system | absent |
| **Selection by neighbourhood quality, never the point winner** | **violated** |
| `trials_log` — N read from a log of every config ever scored | partial (siblings are logged; the model's own search is not) |

Things the research layer has that the SOP does not: public hash-chained
provenance from literature to verdict, an outsider-runnable verifier,
composition fingerprinting with no resurrection, honest funnel counting, and
automated idea generation from a research corpus.

**The point-winner violation is live, not theoretical.** On 2026-08-17 the
gauntlet selected `ad654fd8097717bd` for quarantine because it held the highest
DSR in its sibling group. That is point-winner selection, which the SOP
explicitly forbids in favour of plateau quality — and it happens to be the
`ann_vol 0.4` arm carrying the widest tail of the twelve evaluated.

## The four sub-projects

Each gets its own spec → plan → build cycle. They are ordered by dependency.

| # | Scope | File | Depends on |
|---|---|---|---|
| 1 | **One gate standard** — reconcile both gate sets into a single pre-declared protocol | [1-gate-standard.md](1-gate-standard.md) | — |
| 2 | **One designation + registry** — where a designated system lives, whatever its origin | [2-designation-registry.md](2-designation-registry.md) | 1 |
| 3 | **One commercial phase** — Pine-codeability gate + parity harness for research-layer strategies | [3-commercial-phase.md](3-commercial-phase.md) | 2 |
| 4 | **One incubation loop + portfolio** — converge the two daily writers, measure the combined book | [4-incubation-portfolio.md](4-incubation-portfolio.md) | 2 |

**Recommended order: 1 → 2 → 3 and 4 in either order.** (1) is first because it
is the only one that changes what reaches quarantine, and the other three assume
its output. (3) and (4) are independent of each other.

## State at the time of scoping

- `stewart-forward-test` branch `claude/ai-agent-business-automation-0lzfd9`,
  chain **2307 entries VALID**, funnel **80 proposed → 43 screened-in → 3
  quarantined**, 371 offline tests green, all gen-3 work pushed through
  `7f48faf`.
- Quarantined: `9b6753a48c4d0ccd` (breakout, fixed_fraction),
  `ad654fd8097717bd` (breakout, vol_target 0.4), `ef7712f41e2188e2` (tstat).
  Entered 2026-08-17; **0 forward days**; first recordable day is 2026-08-18,
  whose bar exists after 00:00 UTC 2026-08-19.
- `trading-systems`: 6 registered systems (A1-PORTFOLIO, XRP-B1, R5/ETH-B1L,
  Q9/ETH-J1L, BUNDLE-EW-4, HOUSE-CORE), Pine parity 6/6 at 641/641 trades,
  `15_PaperBot` 08:10 and `16_TestnetBot` 08:12 daily. Suite ~907 tests, ~45 min.
- **A concurrent session shares this branch** and is mid-flight on its own
  autonomous-pipeline work (commit `ef305ea` unpushed, 20 untracked USDT CSVs
  from its `data_import.py`). Coordinate before assuming the tree is yours.
