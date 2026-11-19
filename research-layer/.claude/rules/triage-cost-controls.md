---
paths:
  - "pipeline/allowance.py"
  - "pipeline/test_allowance.py"
  - "pipeline/triage.py"
  - "pipeline/triage_batch.py"
  - "pipeline/test_triage_batch.py"
  - "pipeline/test_triage_eval_gate.py"
  - "pipeline/relevance.py"
  - "pipeline/scanner.py"
  - "pipeline/test_scanner.py"
  - "pipeline/loop.py"
  - "logs/loop_state.json"
---
## Triage cost controls (loop stage 4a)
- **`--limit` is DERIVED each cycle from the spend allowance (Phase 3 step 5,
  2026-09-03), clamped to `loop.TRIAGE_CEILING` = 200 (`TRIAGE_LIMIT` is its
  alias; the Gate-2 window test governs the ceiling).** `pipeline/allowance.py`:
  `expected_cycles = max(10, cycles completed in the trailing 30 days)`
  (state["cycles"]); `allowance = PIPELINE_CAP_USD x (1 - 0.15) / expected`;
  `triage_count = clamp(floor((allowance - composer_pair_usd) / usd_per_card), 1, ceiling)`.
  The two unit costs are the loop's OWN measured spend deltas (trailing means
  in state["calibration"]; priors 0.018/card and 0.64/pair from 2026-09-01/02).
  At USD 40 and 20 cycles/month that was ~USD 1.70 a cycle, ~58 cards -- the
  intended effect of the cap, stated in the plan. **Since D41 raised the cap to
  200 the derivation saturates: the allowance buys more than `TRIAGE_CEILING`
  at every cycle count, so the limit passed is ALWAYS 200 and the cap no longer
  shapes cycle size.** Never hand-type a card count into the loop; change
  `TRIAGE_CEILING` (now the binding constraint), the cap, or the reserve.
- **Two parks after triage, both BANK the reviewed cards.** `deferred_budget`
  = the MONTHLY hard-cap line (WARN, budget_cap semantics; since D41 there is
  no earlier batch-stop line),
  checked first; `deferred_cycle_budget` = this cycle's own allowance would be
  exceeded by the composer pair (overall OK -- Coen: a park counts as a clean
  day). Before 2026-09-03 the monthly park recorded a park and never banked,
  so the cards triage had just paid for were re-paid on the next fire.
- Status items on every path that reaches triage: `cycle_usd_allowance`,
  `expected_cycles`, `triage_limit_used`, `usd_per_card`, `composer_pair_usd`,
  `cycle_spent`. The digest can say WHY a cycle was the size it was.
- **ExecutionTimeLimit lives in TWO places and the second one wins.**
  `morpheus-hub/tasks/xml/25_PipelineLoop.xml` declares it, but
  `morpheus-hub/tasks/apply_retry_settings.ps1` re-stamps every `$RETRY_TASKS`
  entry from its per-task limit table, and 25_PipelineLoop is on that list.
  Change it in BOTH places or the override wins (that mismatch is how the
  live task once sat at PT1H while its XML said PT2H). Current values: XML
  PT11H + override PT11H.
- The loop WARNs at startup (never refuses -- a nonzero exit would FAIL the
  Sentinel digest) when the LIVE task's window is under PT2H, naming the
  elevated fix command. Reading the task setting is fully defensive: not
  registered, no schtasks, odd duration -> silent no-op, so manual runs and
  tests behave identically.
- **D44 (Coen, 2026-09-26): triage decides EVERY card; nothing waits for Coen.**
  A full panel of three unanimous accepts -> accepted. ANY dissent, or a panel
  still short after bounded in-call re-asks (`MAX_VOTE_ATTEMPTS`), -> rejected
  `claim_not_supported`, provenance `auto-d44`. A majority rule was offered and
  rejected (D31's reason: one reviewer spotting overreach must never be
  outvoted). The escalation skip-set (`logs/triage_escalated.json`), its
  `--escalated-state` / `--no-skip-escalated` / `--queue` flags and Coen's T3
  card queue are GONE; the 261 cards it held were resolved under the new rule
  on 2026-09-26. Each rejection's dissent reasons are appended to
  `logs/triage_rejections.jsonl` (the chain only records the reason code), so
  a rejected card can still be audited and revoked. `triage_result.json`
  still carries `skipped_escalated` (always 0) because loop.py reads it.
- **Resolving cards OUTSIDE a cycle moves the trigger basis -- re-bank.** The
  retired skip-set's cards were PENDING (so triggerable) and already banked in
  every class watermark. Rejecting them out of cycle (the D44 backfill) dropped
  every class BELOW its watermark (crypto -95 .. equity_etf -142) and would have
  stalled the loop. Fixed the same day under loop.lock: each watermark lowered
  by the retired cards routable to that class (crypto 261, fx 67, equity_etf
  205, bond_etf 78, metal_etf 80), restoring the pre-backfill gaps exactly;
  backup `logs/loop_state.json.bak-2026-09-26-pre-d44-rebank`. Any future
  out-of-cycle disposition of pending cards needs the same re-bank.
- **D44 sources: accepted by default.** D27 case 3's AI source screen
  (`relevance.screen_source`, one Sonnet call per discovered source that could
  BLOCK it on content) is REMOVED, not unwired -- `process_admissions` has no
  `screen`/`can_spend` parameter (test-guarded). A discovered source that passes
  the mechanical prefilter (reachable, has articles, not a store/login
  subdomain) goes straight onto probation and its YIELD decides. The per-item
  relevance screen still filters its articles. The 97 domains the old screen
  had blocked were reopened to `proposed` on 2026-09-26 so they re-enter
  through the prefilter. Code changes reach the RESIDENT scanner only after it
  is restarted (`run_scanner.ps1`).
- `no_new_accepted_cards` outcome: triage ran but accepted nothing new for a
  class that fired on pending cards, so the composer would see an unchanged
  corpus. Exits 0 before any metered composer call and still advances the
  watermark (those cards were seen). Spec Decision 2, "no new information, no
  new trials".
- `gauntlet_orphan` outcome (exit 1, FAIL): a strategy sits in state
  'gauntlet' with a non-v6.1 gauntlet verdict already chained -- a hand run of
  gauntlet.py refuses on this unconditionally. Since step 8 (2026-10-07) the
  loop no longer runs gauntlet.py, so this is an alarm for an orphan that
  already exists or that a hand run left, detected next to the pre-spend chain
  verify. Repair the chain manually.
- Fires ONCE at 20:00 local with a PT11H window, ending 07:00 (Coen 2026-09-28:
  PT4H killed every cycle 09-25..27 once the registry passed ~10k strategies --
  the gauntlet's registry-wide clustering is not deadline-aware -- and the 02:30
  fire never ran behind the killed 22:30 instance). Was 22:30/02:30 PT4H from
  2026-09-04 (night: a cycle needs ~9 GB of commit the desktop does not leave
  free by day -- WATCH the 20:00 start for that; the gauntlet's commit-bounded
  worker count is the defence). No window may reach 08:20 QuarantineDaily.
  morpheus-hub/tasks/xml/25_PipelineLoop.xml + apply_retry_settings.ps1's
  $TIME_LIMIT_OVERRIDES carry it -- change BOTH once \Morpheus\25_PipelineLoop (moved from \StewartCo\ on 2026-09-13; the loop's TASK_NAME follows it) is
  registered (activation Coen-gated per D29); exit 0 covers no_trigger and
  polite deferrals (distinguished in status items.outcome); nonzero = real
  defect (Sentinel FAILs the digest).

