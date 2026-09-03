# Autonomous Pipeline Roadmap — discovery to paper, hands-off

**Status:** DRAFT for Coen. Nothing here is built. The phase order is the recommendation; each phase is a separate build with its own detailed plan.

**Coen's goal (2026-08-31):** Morpheus constantly scans the web for potential edges, constantly turns those into strategies, constantly gauntlets them, and gets the winners into **paper trading** — with no human action except setting *what to search for* and *the gauntlet parameters*. **Paper is automatic; only confirmed LIVE edges need a human.**

**Architecture decision this encodes:** Gate 2 (witnessed registration) moves from the `quarantine → paper` boundary to the `paper → live` boundary. Gate 2 exists to protect capital; paper and testnet risk none. Gate 1 (triage) is already automated via D31 auto-accept.

---

## What already exists (verified at source 2026-08-31 — do NOT rebuild)

| Stage | Status |
|---|---|
| Web scan | LIVE — `21_ReaderScanner` (daily 09:00 + logon), `22_SourceScout` (weekly Mon) |
| Cards → triage | LIVE — D31 auto-accept ON; loop runs `triage_batch --apply --limit TRIAGE_LIMIT` |
| Compose → screen → gauntlet → quarantine | LIVE — `25_PipelineLoop` 10:30/15:30/21:30, PT4H |
| Quarantine observation | LIVE — `23_QuarantineDaily`; first assessment due 2026-10-17 |
| **quarantine → paper** | **DOES NOT EXIST.** `loop.py` contains no reference to paper/promote/winner. This is the real gap. |
| Paper / testnet bots | LIVE but fed by hand — `15_PaperBot`, `16_TestnetBot`, in the separate `trading-systems/` repo |
| Coverage expansion | **BUILT, NOT ACTIVATED** — SP5 Phase 2 T4 (`bbd32da`, 17:12 on 08-31): sweep rotation D6, sibling queues D10, re-trial window D9. Waiting on Coen's Phase 3 activation commit; `ACTIVE_CELLS` crypto is EMPTY until then. |

**Consequence for planning:** two of the five things originally sketched are already done or already built. This roadmap reflects that, not the first sketch.

---

## Phase 1 — Fix the watermark stranding un-triaged cards

**Why first:** it throttles every phase after it, and no throughput setting compensates. Independent of every decision below.

**The defect.** `loop.py:886` banks the watermark at the full post-triage triggerable count:

```python
watermark_after_triage = _triggerable_counts(registry)[asset_class]
```

Its own comment says this number means *"cards seen and dispositioned as of this cycle"*. It is not that. `triggerable = accepted + pending`, and after triage the pending set still holds every card the cycle's `TRIAGE_LIMIT` never reached. Those are banked too, so they stop counting toward the next trigger. With 595 pending and a limit of 200, roughly 395 cards are banked unseen and only move as *new* cards arrive.

**Correct value:** `triggerable_after − pending_never_reviewed`.

**The subtlety that makes this a real build rather than a one-liner:** escalated cards are *deliberately* left un-dispositioned for Coen's T3 review, so they stay `pending` despite having been reviewed. They are tracked in `logs/triage_escalated.json` (the skip-set from `8cc36b8`). So "never reviewed" means pending AND not in the skip-set AND not reached this cycle. Wrong in either direction is bad: over-bank and cards strand (today's bug), under-bank and the same cards are re-triaged and re-paid forever (the bug `8cc36b8` fixed).

**Task 1 is investigation, not code.** Determine how the loop can learn what `triage_batch` actually dispositioned — does it report a count on stdout, write it to a file, or must the loop diff the registry before and after? That interface answer determines the implementation, and guessing it in a plan would be fiction.

**Definition of done:** a cycle whose triage limit is smaller than the backlog banks only what it reviewed; remaining pending cards still count toward the next trigger; escalated cards are not re-reviewed. A regression test proves all three.

---

## Phase 2 — Activate SP5 Phase 3 (coverage expansion)

**Why second:** it is already built. This is the largest available increase in genuine search space, and it costs a merge plus an activation commit rather than a build.

**What it turns on:** the 100-asset crypto grid, sweep rotation (`ROTATION_SIZE = 12`), sibling queues, and the re-trial window. Coverage today is **385 of 373,368 declared grid points — 0.1%**.

**Order, from the SP5 note and non-negotiable:** merge `feat/sp5-phase2`, then Coen's Phase 3 activation commit flips `ACTIVE_CELLS` for crypto. Phase 2 is behaviour-frozen by design; nothing sweeps differently until that commit.

**⛔ BLOCKER, VERIFIED 2026-08-31: the `family-openness-v1` note is NOT on the live chain.** Searched `registry_log.jsonl` (16,070 entries) for two distinct phrases from the note body — zero matches. The note is Lane A and MANDATORY: spec s7b (corrected in `e3aa045`) requires it chained BEFORE any re-trial code ships.

T4 (`bbd32da`) implements exactly that re-trial and queue behaviour and is already committed to `feat/sp5-phase2`. The ordering is still recoverable **only because phase2 is not merged**. The note's own text asserts it was pre-declared while "no re-trial code has shipped" — keep that true by chaining it BEFORE the merge, not after.

**Therefore Phase 2 starts with a chain write, not a merge:**
1. Chain `docs/notes/family-openness-v1.md` — payload is the file verbatim, TEXT mode, `registry.append("note", {"text": text})`, under chain lock.
2. Merge `feat/sp5-phase2`.
3. Coen's Phase 3 activation commit flips `ACTIVE_CELLS` for crypto.

**This is the honest answer to "no limits".** More coverage adds information. More variations on already-covered cells adds multiple-comparisons risk, which is what the gauntlet exists to absorb. Phase 2 buys the first without buying the second.

---

## Phase 3 — Replace card-count limits with a spend rate

**Status 2026-09-03: the TIME half is built and merged (`34c06b9`) — see `2026-09-03-phase3-steps1-3-deadline.md`. Screen and gauntlet take `--deadline-utc`, stop before starting what cannot finish, and report `stopped_at_deadline` as an OK outcome; the loop derives the deadline from the live task window. The SPEND half (steps 4–5 of `2026-09-03-phase3-spend-and-time-throttle.md`) waits on Coen's monthly cap.**

**Why third:** once Phases 1–2 land, `TRIAGE_LIMIT` is the wrong knob. It is a proxy for cost and wall-clock expressed in the wrong unit, and it needs re-deriving every time the window or panel speed changes. It has already rotted twice: the PT2H-era `<= 40` ceiling, and a hardcoded `"120 min"` in a test.

**Change:** the loop triages until it hits a per-cycle spend allowance or the window guard, whichever comes first. `TRIAGE_LIMIT` becomes a derived safety ceiling rather than the control.

**Coen's decision required — the monthly rate.** The pipeline agent has **USD 20/month** today (D33, part of the D28 Intelligence band). Continuous operation is unbounded spend by definition, so this number *is* the throttle. "No limits" resolves to "a number you choose here", and everything downstream scales off it.

---

## Phase 4 — Continuous operation

**Why fourth:** pointless before Phase 1 (the backlog strands anyway) and before Phase 3 (no rate to run against).

**The decision to revisit:** on 2026-08-27 the architecture was set as *"scheduled trigger-check task ~3x daily, NOT resident."* Continuous means changing that. The chain lock protocol already makes concurrent writers safe, so this is tractable rather than a rewrite.

**Two viable shapes:**

- **More frequent scheduled task** — hourly instead of 3×/day. Cheap, low-risk, keeps every existing guard including the PT4H window and the Sentinel's exit-code contract.
- **Resident service** — a real loop. Removes the window-fitting problem entirely (no `ExecutionTimeLimit`, so the whole mid-flight-kill bug class disappears), but needs its own supervision, and the Sentinel currently reads task exit codes rather than service health. `90_SdcaWebui` is the cautionary precedent: a persistent task needs a watchdog, and the watchdog must not be able to kill what it guards.

**Recommendation:** hourly scheduled first. It captures most of the throughput gain for a fraction of the risk, and defers the supervision problem until there is evidence that frequency is the binding constraint.

---

## Phase 5 — The quarantine → paper bridge

**Why last:** it is the only genuinely new surface, it crosses two repos, and it is worth building against a pipeline that is actually producing candidates.

**What it is:** a promotion path from a quarantine survivor in `stewart-forward-test/research-layer` to a live paper sleeve in `trading-systems/` (`15_PaperBot`, `16_TestnetBot`), with no human step. Today this is done by hand.

**Coen's decisions required before this can be specified:**

1. **What promotes.** Gauntlet pass alone, or gauntlet pass plus N days of clean quarantine observation? `quarantine-live-protocol-v1` has its first assessment due 2026-10-17 — does paper promotion wait for that protocol or run ahead of it?
2. **What "confirmed live edge" means.** This is the new Gate 2 and the only remaining human gate. Paper performance over what window, against what bar?
3. **Capital isolation.** Confirm paper and testnet cannot reach real capital under any path — this is precisely the assumption that justifies moving the gate, so it gets verified at source, not assumed.
4. **De-promotion.** What removes a failing strategy from paper? Without this, paper accumulates forever and the signal degrades.

**Invariant this must not break** (from `project_signal_surface_integrity`): never publish one system's regime from another system's rule, and any publishing task must be in `DAILY_TASKS`.

---

## Cross-cutting: what stays human

After all five phases, exactly one gate remains: **paper → live**. Everything upstream runs unattended. Coen's inputs reduce to the two he named — what to search for, and the gauntlet parameters — plus the monthly spend rate from Phase 3 and the promotion/de-promotion rules from Phase 5.

## Risks not to paper over

- **"Honest zero is the product claim."** Results so far: 0/303 bond+metal, 0/96 eq-gen1, 2/20 vs BTC-hold. Running faster mostly produces zeros faster. That is a fine outcome and cheap at these rates — but the goal statement says "get the winners into paper trading", and the system should not be tuned on the assumption that winners are currently being suppressed by throughput.
- **Removing Gate 2 from `quarantine → paper` is only safe while paper is genuinely capital-free.** Phase 5 decision 3 is load-bearing, and worth re-verifying whenever the paper bots change.
- **Automation hides failure.** The 08-31 watchdog defect ran for two days under a task that read `Running` throughout, and `90_SdcaWebui` sat dead for a week behind an exit code nobody read. Every phase here needs a health signal showing that *work happened*, not that a process returned 0.
