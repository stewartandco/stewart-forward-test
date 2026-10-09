# Screen releases chain.lock between batches: design

Date: 2026-10-08. Owner decisions: Coen, 2026-10-08 (screen writes as it goes; approach A).
Context: gauntlet-at-scale cutover (docs/2026-09-30-gauntlet-at-scale-design.md), item 5 of the post-cutover list.

## 1. Problem

The standalone gauntlet worker (`pipeline/gauntlet_worker.py`, task 27, every 30 min) needs `logs/chain.lock` for each verdict it writes. The pipeline loop (task 25, nightly 20:00) runs `pipeline.screen` inside `_lock_and_run` (loop.py ~1788), so `chain.lock` is held for the whole screen subprocess. On big nights that has been about 5 h. On 2026-10-01 the worker wrote 0 verdicts from 20:24 to 01:51 while evaluating 48-120 candidates a run. Keep-and-retry (2026-10-02) only rescues short holds.

Measured on a copy of the live chain (99,877 entries, 2026-10-08), most of the hold is screen's WRITE phase, not its evaluation:
- Screen evaluates every proposed spec, then writes each one with three calls: `record_state_change` to screened, `record_verdict`, and `record_state_change` to gauntlet/graveyard.
- Each call re-reads the whole chain: `strategy_states()` takes 1.56 s and `_head_hash()` 0.22 s. That is about 5.3 s per spec, or about 2.4 h for 1,600 specs, and it grows with the chain.
- A crash after evaluation loses the whole night's screen work. The "PARTIAL WRITE" path then leaves a mid-run state to repair by hand.

## 2. Goal and success criteria

- The worker keeps writing verdicts while the loop's screen stage runs. On a big screen night, worker runs during screen show verdicts written, not whole runs of `deferred_lock`.
- Screen's verdicts are identical:
  - same specs, same evaluation and engine, same gates (screen-protocol-v1);
  - same entry types and payloads per spec, in the same per-spec order;
  - same evidence bundles.
  Only timestamps and the interleaving with other writers' entries differ.
- `chain.lock` rules are unchanged: every writer takes it for its writes, never waits inside an acquire, and never breaks it.
- The append-only chain stays VALID under `verify_registry.py`. No verifier change is needed.
- The loop's night gets shorter: screen's write phase drops from about 5 s per spec to well under 0.1 s per spec.

## 3. Design

### 3.1 Who holds the lock (Coen: approach A)
- `pipeline/loop.py` runs the screen stage WITHOUT `_lock_and_run`.
  - Before starting screen it probes `chain.lock` once (`ChainLock.info()`); a holder is logged.
  - The probe no longer defers the cycle (`_defer_midcycle_lock` is not used for screen).
  - Triage (4a) and both composer calls keep today's `_lock_and_run` wrapper; they are minutes long.
  - The post-screen chain verify (4f) is unchanged.
- `pipeline/screen.py` takes `chain.lock` itself, only for each batch write. The holder is `screen`, and the purpose is `screen N verdict(s)`. (Amended 2026-10-09: screen is never told the run id, so the purpose does not name it.)

### 3.2 Screen's run (Coen: write as it goes)
1. Start: the existing orphan check (strategies resting in `screened`) and the screen-protocol note check, both unchanged. Then take one chain snapshot (`Registry.snapshot`: states, head hash, byte length) and select the `proposed` specs from it. Selection and order are unchanged.
2. For each evaluation chunk (today's chunking: workers x `SCREEN_CHUNK_PER_WORKER`; today's deadline budget asked before each chunk):
   - Evaluate with NO lock held, exactly as today.
   - Add the chunk's results to the pending batch, then try to flush it (3.3).
3. End: if a pending batch remains, run a final drain. This makes non-blocking flush attempts `DRAIN_INTERVAL_S` apart, inside `DRAIN_RESERVE_S`, and never past the deadline. Use the worker's constants or screen-local ones of the same values (5 s and 75 s).
4. `--dry-run`: unchanged. No lock, no writes.

### 3.3 Flushing a batch (one non-blocking attempt)
Under `chain.lock`, if acquired:
1. **Advance the snapshot** over only the bytes appended since (`Registry.advance`), verifying `prev_entry_hash` continuity of the tail.
2. **Re-check each spec** on the advanced snapshot: still `proposed`. A spec that fails is dropped unwritten and counted in `dropped_stale`. (Amended 2026-10-09: there is no separate "screened verdict already chained" check. The state check suffices: a chained `screened` verdict implies the spec has left `proposed`, and no transition returns to `proposed`.)
3. **Write the evidence bundles** for the specs still to be written: the same `write_artifacts` call, arguments and `bundle_hash`. A kept-but-unwritten spec never leaves a bundle on disk.
4. **Append, in ONE write,** all entries for every spec in the batch, using a new `Registry.record_screen_outcomes_batch(snap, items)` on the existing `_append_at` path (Rulings 8/9). For each spec, in order:
   - `state_change` proposed -> screened, with the same reason text as today;
   - the `screened` verdict, with metrics and `artifacts_hash`;
   - `state_change` screened -> gauntlet (pass) or graveyard (fail, the gate reason).
   The write rules:
   - Every transition is validated against the snapshot before anything is written.
   - Each prev hash comes from the parsed serialized line.
   - An entry that does not round-trip to identical bytes is refused before the write.
   - The size-check refuses if the file grew under the FileLock.
5. Release the lock.

If the lock is held, the batch stays pending and is retried at the next chunk boundary and in the final drain. A batch is capped at 200 specs per hold, the stats job's STATS_BATCH_MAX pattern; a larger pending set is flushed in successive holds.

(Amended 2026-10-09: measured hold, on a copy of the live chain with 200 specs at the default 7 workers. Per-hold lock duration, acquire to release: median 2.27 s, max 2.48 s at 56-spec chunks (4 holds: 56, 56, 56 and 32 specs = 2.48, 2.38, 2.16, 1.11 s). A full 200-spec batch is about 8 s. Write phase 0.041 s per spec, against 6.84 s per spec before. `verify_registry` VALID. A hold is a few seconds, not well under a second. After a held lock, pending specs flush back to back in successive holds of up to 200 specs, about 8 s each.)

### 3.4 Failure handling
- **Lock held through the end:** the unwritten specs stay `proposed` and the next run screens them. The exit is 0, with counters `deferred_lock` (specs unwritten) and `retried_written` (specs written on a retry).
- **Deadline:**
  - Unstarted specs stay `proposed`, as today.
  - The reserve is subtracted from the budget only while a batch is pending.
  - No flush starts after the deadline.
- **`CellError` in evaluation:**
  - Today's rule stands: the run raises and exits nonzero, and the loop records `stage_failed`.
  - Batches already written stay chained.
  - The crashed spec and every spec after it stay `proposed`. Nothing is buried because of a crash.
  - The PARTIAL WRITE message is removed, because that state can no longer arise.
- **Chain moved / UnstableEntry under the lock** (an unlocked writer):
  - Nothing is written for that batch, and the run exits 1: an alarm, the worker's rule.
  - Earlier batches stay chained.
  - The loop records `stage_failed`.
- **Orphans:** a spec's three entries land in one write, so a crash cannot leave a spec in `screened` without its verdict. The start-of-run orphan check stays as a backstop.

### 3.5 Result contract with the loop
- `deadline.write_result(registry, "screen", ...)` keeps `evaluated`, `deferred`, `deadline_utc` and `stopped_at_deadline`, with these meanings:
  - `evaluated` = specs CHAINED this run;
  - `deferred` = specs not started plus specs evaluated but left unwritten.
- New optional counters are added to the same file: `deferred_lock`, `retried_written` and `dropped_stale`. `write_result` gains keyword-only extras and stays backward compatible.
- No existing key is renamed. The builder must grep `pipeline/`, `sc-ops-sentinel` and `morpheus-hub` for readers of `screen_result.json` and `deferred_screen` before changing anything. The reader-agent `deferred_screen` in relevance.py, scanner.py, report.py and seen.py is a different concept, and is untouched.
- Loop watermark banking and cycle_complete semantics are unchanged.

## 4. Why this is safe to run with evaluation unlocked
- Only the screen stage moves a `proposed` spec.
- The composer registers new specs only between the loop's own stages, and runs under the loop lock.
- The worker touches only `gauntlet`-state strategies; the stats job writes `gauntlet_stats`; the scanner writes cards; quarantine writes quarantine entries.
- A hand-run writer is caught by the per-spec re-check (3.3 step 2) or by the size-check (exit 1).

## 5. Testing (tmp registries; never the live tree)
- **Identity:** on a fixture with passes and fails, the old screen (loaded via `git show <base>:research-layer/pipeline/screen.py`) and the new screen produce the same entries in per-spec order (types, payloads, bundles); only `ts_utc` differs. `verify_registry` is VALID for both. (Amended 2026-10-09: the unit identity fixture produces fails only (`trade_count`). Pass-path identity rests on the measured proof, 78 passes and 122 fails on a copy of the live chain with entries identical apart from `ts_utc` and the prev hash derived from it and all 200 bundles byte-equal, and on the payload helpers both screens share.)
- **Lock behaviour:**
  - Lock held at the first attempt, then released before the next chunk: written in the same run, `retried_written` counted, each bundle written exactly once.
  - Lock held for the whole run: nothing written, no bundles, all specs `proposed`, `deferred_lock` = N, exit 0.
- **Stale spec:** a spec moved out of `proposed` between evaluation and flush is dropped (`dropped_stale`), and the rest of the batch is written.
- **Chain moved:** a foreign unlocked append before the flush writes nothing for that batch, exits 1, and earlier batches remain.
- **CellError mid-run:** earlier batches remain, the crashed spec and later ones stay `proposed`, nonzero exit.
- **Deadline** (fake clock): no flush starts past the deadline, and the reserve is held only while a batch is pending.
- **Concurrency:** a worker-style writer appends between chunks, screen still writes, and the chain is VALID.
- **Loop:** the screen stage runs without `_lock_and_run`, a lock held at the probe does not defer the cycle, and triage/composer keep their wrapper.
- **Mutations** (Python string-replace plus `ast.parse`; never sed). Each must fail a test:
  - drop the re-check;
  - bundles before the lock;
  - wrap screen in the loop lock again;
  - per-entry appends instead of one write.
- **Measured proof before merge:** on a copy of the live chain, run old and new screen over a real sample of proposed-shaped specs. Show identical entries apart from timestamps, and time the write phase per spec (target well under 0.1 s, against about 5.3 s today).

## 6. Rollout
- Builder plus cold review, then a guarded merge in the live tree by day (research-layer CLAUDE.md git rule).
- First night's evidence: worker runs during screen show verdicts written, the loop's screen finishes sooner, and `verify_registry` is VALID next morning.
- Rollback: a code revert (no flag, as in step 8).
- Docs: research-layer/CLAUDE.md (the chain-lock section and the loop's screen description) and an amendment line in docs/2026-09-30-gauntlet-at-scale-design.md.

## 7. Out of scope
- Triage's and the composer's own chain writes (minutes; they stay under the loop lock).
- The composer registering specs on cells with no usable history (see the SP5 Phase 3 prerequisite, Ruling 41).
