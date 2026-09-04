# Pipeline loop - design spec (2026-08-27)

> **AMENDED 2026-08-29 (Coen) - trigger basis.** Decision 2 and section 2
> below originally read "new **accepted**, class-routable cards". As built,
> that deadlocked the loop: a card only becomes accepted via the D31 triage
> panel, which runs *inside* a cycle, after the trigger decision - and
> nothing else in the system triages. The accepted count could not move
> between fires, so every fire honestly reported `no_trigger` and the pending
> backlog (539 cards) never drained. The trigger now counts **triggerable**
> cards: class-routable cards that are accepted **or pending**, never
> rejected - the cards a cycle could actually act on, matching this
> decision's own heading ("new-cards threshold"). The watermark is recorded
> on that same basis. Both paragraphs are corrected in place below; this
> banner is the record of the change.

Coen's stated goal (Morpheus 3.0, Track 1): the estate runs 24/7 -
constantly scanning for new resources, constantly converting them into
candidate edges, running every candidate through the gauntlet for each
active market, repeating.

This spec is the activation-and-orchestration successor to the ratified
autonomous-pipeline design (`stewartandco-agents/hubs/intelligence/
2026-08-16-autonomous-pipeline-design.md`, D29-D32). That spec defined the
five-agent chain and its two human gates; this one adds the single missing
piece - a resident cadence for the Composer -> screen -> gauntlet middle -
and the activation checklist that D29 gates on Ops Sentinel graduation.

## State this spec builds on (verified 2026-08-27)

- Reader scanner: LIVE 24/7 already. Resident loop, ~15 min cycles, daily
  09:00 keepalive (`21_ReaderScanner`), spend USD 26.78/35 MTD, D27 source
  probation active (30d: 5 admitted / 116 blocked).
- Triage: D31 panel BUILT, `--apply` deliberately unrun; 538 items pending
  Tier 3.
- Composer/screen/gauntlet: built for crypto + fx + equity_etf
  (`cells.LIVE_CLASSES`); every generation to date was session-launched.
- Perf: 2026-08-27 batch (P1-P5, B1, riders b0846f4) took a full gauntlet
  pass from 26 h to well under 1 h. 24/7 cycling is now computationally
  cheap; compute is local CPU and costs zero dollars.
- Quarantine: `23_QuarantineDaily` runs the forward test daily.
- Live router: exists, parked. Gate 2 (witnessed registration) is the only
  path to it and stays human (D32).

## Decisions made in session 2026-08-27 (Coen)

1. **Discovery unchanged.** The scanner keeps its curated watchlist and the
   D27 probation filter. "Constantly scanning" is already true; this spec
   does not widen intake.
2. **Trigger = new-cards threshold.** A generation cycle fires for a class
   only when enough genuinely new *triggerable*, class-routable cards have
   accumulated since that class's last generation - triggerable = accepted
   OR pending (never rejected), i.e. the cards a cycle could act on, since
   the cycle's own first stage is the triage panel that resolves the pending
   ones. Default threshold 25, tunable per class. No fixed-cadence churn: no
   new information, no new trials. (Amended 2026-08-29; see banner. Reading
   accepted-only here is the deadlock.)
3. **Budget: keep existing caps, park at limit.** Reader USD 35/mo and the
   pipeline cap stand. At 80% the loop stops starting new metered batches;
   at cap, metered stages park and self-resume on month rollover. Gauntlet
   and screen (pure local Python) are never budget-blocked.
4. **Architecture: scheduled trigger-check task**, not a resident daemon
   and not a bolt-on to the scanner. `pipeline/loop.py --once` fired ~3x
   daily by a `\StewartCo\` task. With triggers moving on a timescale of
   days and a cycle under an hour, this is functionally 24/7 with none of
   the resident-process ops surface.

## Step 0 - activation checklist (governance, before anything unattended)

Per D21/D29, in order, each item evidence-verified in session with Coen:

1. **Verify Ops Sentinel graduation.** Week 1 signed off 2026-08-18 (zero
   missed anomalies, zero false alarms). Week 2 evidence: Sentinel action
   log + Coen confirmation; the 2026-08-23 GitHub-504 false alarm sits
   inside the <1/week allowance but the call is Coen's. No graduation, no
   activation - the rest of this checklist waits.
2. **D31 triage activation**: run the panel with `--apply` on the pending
   Tier 3 backlog (538 items at time of writing). Unanimous three-reviewer
   accepts auto-apply with provenance `auto-d31`; any dissent stays in
   Coen's queue; duplicates fingerprint-reject. Hand-verify a 20-card
   sample per D34's precision stage before the first unattended run.
3. **Historical note**: D21's pre-approved Composer campaign (125 cards ->
   specs, $15 cap, written 2026-08-13) is treated as historical - five
   generations have since run under D29's building allowance. The loop's
   first triggered cycle is the real post-graduation act.
4. **Register the scheduled task + pins** (see Ops wiring) only after one
   supervised `--once` cycle has run clean end to end in session.

## Components

### 1. Chain lock (new, load-bearing)

`registry_log.jsonl` currently has no locking; the 2026-08-14 incident
(scanner writing 372+ cards during gauntlet work) was survived by
coordination and luck. An unattended writer makes that a defect. Ship a
lockfile protocol (`logs/chain.lock`, atomic create, holder + ts_utc +
purpose inside, stale after a declared TTL) adopted by ALL writers: the
scanner's registration path, the loop, `23_QuarantineDaily`'s recorder,
and manual sessions (documented in the repo CLAUDE.md). Rules:

- Writers take the lock for append windows, not whole runs; the scanner's
  15-min cycle must never block on a gauntlet.
- The loop DEFERS (exit 0, `deferred_lock` in status) if the lock is held;
  it never breaks a fresh lock. A stale lock is surfaced as a WARN in
  status.json and the digest, never silently stolen on the first offense.
- Read paths stay lock-free (the tail parser already tolerates partial
  lines).

### 2. Watermark state (the trigger)

`logs/loop_state.json`: per class, the chain position (entry index + card
count) at that class's last completed generation, plus the count of new
triggerable, class-routable cards since. Triggerable = accepted OR pending;
rejected cards are settled and never count. Class-routable follows the SP4
routing rules already shipped (crypto unrestricted; non-crypto
class-matched + cross_asset; recorded proxy lanes). A card can trigger
more than one class; each class's watermark is independent. The threshold
(default 25) lives in this file, per class, Coen-editable.

**The watermark is recorded on the same basis it is compared against** -
the post-triage triggerable count, not the accepted count. A watermark
measured on a different basis than the trigger reads either never fires
(the 2026-08-29 deadlock) or fires forever. `--seed-watermarks` seeds on
that same basis for the same reason. Note the trade this implies: pending
cards a cycle's `--limit 200` did not reach are still banked in the
watermark, so a backlog above 200 drains across several cycles as new cards
arrive, rather than in one sweep.

`logs/pipeline_status.json` carries BOTH counts per class -
`routable_<cls>` (accepted-only: what a composer could consume right now)
and `triggerable_<cls>` (accepted+pending: what the trigger compares). The
digest reports both so an undrained pending backlog is visible rather than
inferred.

### 3. Cycle body (existing code paths only)

For the triggered class, in order: D31 triage panel over pending cards ->
Composer generation (class-aware brief, per-class calendars, budget
metered through `record_call`) -> screen -> gauntlet (sim cache, parallel
evaluation, PBO policy as shipped 08-26/27) -> quarantine registration for
passes -> chain commit (scoped git add of the chain + artifacts) ->
watermark advance -> status/digest/exit code. One class per fire; if two
classes are over threshold the loop takes the one whose watermark is
oldest and leaves the other for the next fire.

The loop invents no policy: gates, thresholds, protocol-v6 denominator
rules, benchmark-relative recording, and per-class calendars all bind
exactly as they do for a session-launched generation. New cells still
enter the denominator at ACTIVATION only; the loop can never activate a
class (that is Coen's, per SP4).

### 4. Budget behaviour

Before every metered stage: check the ledger. Over 80% of the month cap:
finish the current batch, start no new one, WARN in status. At cap: park
the cycle (`deferred_budget`), exit 0, self-resume on rollover - the
proven scanner pattern. A parked cycle keeps its trigger; nothing is lost.
The hard stop is checked BEFORE spending (the 2026-08-16 lesson: a cap
that notices after the money is gone is a report, not a cap).

### 5. Ops wiring

- Task `\StewartCo\25_PipelineLoop`, ~3 fires daily (10:30 / 15:30 / 21:30
  local - clear of the 08:0x signal herd, the 08:50 tradfi lane, the 09:00
  scanner keepalive and Norgate trigger), exit-code-propagating bat.
- Registered in `setup_scheduler.bat` in the same pass as the live
  trigger; added to `$RETRY_TASKS` + `apply_retry_settings.ps1 -Task` from
  an elevated shell (standing scheduler rules).
- **Pinned in `sc-ops-sentinel/manifest.json` and Morpheus fleet
  `tasks.py` TOGETHER** (standing rule), deliberately, after its exit code
  is proven trustworthy - target: 3 clean scheduled days.
- `logs/status.json` per AGENT_STATUS_CONVENTION (per-class watermarks,
  last cycle verdicts, budget, lock state), daily digest lines, and the
  Ops Sentinel FAIL-on-nonzero contract. Threepio's Research/funnel and
  fleet panels then render the loop with zero Morpheus changes.

### 6. Exit codes and honesty

Exit 0: cycle ran clean, OR no trigger, OR deferred (lock/budget) - each
distinguished in status.json. Exit nonzero: a real defect (crash, chain
verify failure, gate error). A stale, broken, or over-budget loop must be
visible as such in the next morning digest, never as healthy.

## What stays human, permanently or until separately decided

- Gate 2: witnessed registration to live capital (D32). The loop ends at
  quarantine entry.
- D27 source promotions and revocations.
- Class activations: bond/metal (2b) and futures (Norgate, readout
  ~2026-08-30) join the loop only via `LIVE_CLASSES`, Coen's call.
- Budget raises (a new D-entry amending D28).
- Quarantine graduation calibration (first assessment 2026-10-17) and any
  kill/graduate decision.
- Triage dissent cards (Tier 3 queue).

## Honest framing

The chain has produced zero live-grade survivors, and the two batches that
looked like breakthroughs (gen-5's 17, eq-gen1's 96) were gate-loosening
and beta respectively. This loop makes the pipeline produce its honest
result faster and without Coen's hands - throughput, coverage, and a
public record. Every triggered cycle raises the BH bar for the existing
quarantine pool by design; the new-cards threshold exists precisely so
that cost is only paid for genuinely new information. The count of specs
a cycle registers is a PRODUCTIVITY number, never an edge claim.

## Out of scope

- Widening discovery beyond the watchlist + D27 (decided against,
  2026-08-27).
- Any live-capital transition; auto-signing registrations.
- Vectorising or otherwise changing the engine, gates, or protocol.
- Lowering any threshold to make the loop "find more".
- Local-LLM substitution for any metered stage.

## Success criteria

1. A new accepted card can reach a gauntlet verdict and (if it passes)
   quarantine registration with Coen touching nothing but his Tier 3
   queue.
2. Every loop-registered trial appears in the declared denominator; chain
   verify stays VALID across loop, scanner, and quarantine writers under
   the lock protocol.
3. No metered call ever exceeds the month cap; a capped month parks and
   self-resumes.
4. The loop's health is readable from the morning digest and the Threepio
   dashboard without opening a terminal.
5. Nothing reaches live capital without a signed witnessed registration.
6. If the corpus goes quiet, the loop does nothing, spends nothing, and
   says so.

## Constraints inherited

- D8: never write to stewart-forward-test main, root
  `forward_test_log.jsonl`, or root `verify.py`. Loop work stays on the
  research-layer branch.
- Concurrent sessions are normal: scoped `git add` with explicit paths,
  never `-A`, never `reset --hard`.
- NEVER `taskkill /F /IM python.exe`; scoped kills only.
- Scheduler changes go through `setup_scheduler.bat` in the same pass;
  never enumerate triggers by index.
- Tiingo lane: never fired twice in an hour (the loop never calls it).
