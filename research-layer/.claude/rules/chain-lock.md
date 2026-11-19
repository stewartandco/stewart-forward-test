---
paths:
  - "pipeline/chainlock.py"
  - "pipeline/lock.py"
  - "pipeline/test_chainlock*.py"
  - "pipeline/screen.py"
  - "pipeline/test_screen_lock.py"
  - "logs/chain.lock*"
---
## Chain lock (logs/chain.lock) - manual sessions MUST honour it
Before any batch of chain writes (generation, backfill, hand-run gauntlet):
check logs/chain.lock. If present, another writer (scanner card batch,
25_PipelineLoop stage, quarantine daily, or another session) is mid-window --
wait or defer. To hold it for a manual working session:
python -c "from pipeline.chainlock import ChainLock; import time; l=ChainLock('logs','session','manual work'); l.acquire(); input('release?'); l.release()"
Never delete a fresh chain.lock; a stale one (>3h) with a LIVE (or
unreadable) holder pid is broken by the loop's two-strike rule, but a stale
lock whose holder pid is provably DEAD (a hard-killed loop, crash) is broken
on the first sighting -- same dead-pid fast path loop.lock uses.
- **Locks name their holder by (pid, start time) (2026-09-30).** Every lock file
  (chain.lock, loop.lock, gauntlet_worker.lock) records `pid_start_utc` beside
  the pid, and `holder_alive()` treats a LIVE pid whose start time differs as
  DEAD: Windows reuses pids, and on 2026-09-28 a reused pid made a dead
  holder look alive and wedged loop.lock and chain.lock. A lock written before
  this change has no start time and keeps the plain pid rule. A holder whose
  start time cannot be read counts as alive (conservative).
- **The gauntlet worker holds chain.lock only for a tail read plus ONE append
  (~16 ms), once per candidate, and never waits for it.** If another writer
  holds it the worker KEEPS that candidate's evaluated result in memory (no
  bundle written yet) and retries the write later in the SAME run: once after
  each chunk, before the next is dispatched, then in a final drain pass of
  non-blocking attempts every `DRAIN_INTERVAL_S` (5 s) inside a reserved
  `DRAIN_RESERVE_S` (75 s) that the chunk loop holds back, never past the
  deadline (2026-10-02; before that every such result was discarded). The
  measurement that motivated the change: overnight runs on 2026-10-01/02
  wrote 0 verdicts each while evaluating 48-120. **The retries rescue only
  SHORT holds** (scanner/inbox card batches and similar). Until 2026-10-08
  the loop's screen stage held chain.lock for hours overnight; a quarantine
  date holds it for TWO tail reads (the re-plan before the append and the
  read over its own entries after it) plus ONE append since 2026-10-16 (max
  0.33 s measured on the 2026-10-16 replay). Before that fast path, holds of
  minutes per date. Holds of minutes or hours (the pre-fast-path quarantine
  catch-up and the pre-2026-10-08 screen; neither is current behaviour) leave
  whole runs with `deferred_lock` equal to their evaluated count; the
  results are discarded at the end of the run and the next run re-evaluates
  those candidates. So the retries alone did NOT fix the overnight
  zero-write runs; screen's per-batch holds (next bullet) did. Each retry
  re-checks on the advanced snapshot exactly what the first write does; a
  candidate another writer moved or judged meanwhile is dropped unwritten
  (`dropped_stale`). Results still unwritten at the end stay queued
  (`deferred_lock`, exit 0) and are NEVER persisted: the next run re-evaluates
  them. It never sleep-polls inside, waits on, or breaks a lock.
  HAZARD: a hand-run writer that appends to registry_log.jsonl WITHOUT taking
  chain.lock inside that ~16 ms window makes the worker see a chain that moved
  under it. It then writes nothing for that candidate, leaves it queued and
  counts it as an ERROR, so the run exits 1 and the Ops Sentinel FAILs the
  digest. That is an alarm on purpose (an unlocked writer is the defect), not a
  flake. Take chain.lock for every hand-run chain write, as above.
- **Screen holds chain.lock only for each BATCH write, not for the stage
  (2026-10-08, Coen's decision; design `docs/2026-10-08-screen-lock-release-design.md`).**
  `pipeline.screen` evaluates every chunk with NO lock, then flushes the
  verdicts under ONE non-blocking acquire (holder `screen`) per batch of at
  most `SCREEN_BATCH_MAX` (200) specs per hold. A hold is normally one
  chunk (workers x 8 = 56 specs at 7 workers), but after a held lock the
  pending specs flush back to back in successive holds of up to 200 specs
  (about 8 s each), with no gap between them. Measured 2026-10-09 on a copy
  of the live 103,626-entry chain (200 specs, 78 pass / 122 fail): the write
  costs 0.04 s per spec, so a hold ran 1.1 to 2.5 s (median 2.3 s, max
  2.5 s); a full 200-spec batch would hold about 8 s. Under
  the lock it advances the snapshot, re-checks each spec is still `proposed`
  (one that is not is dropped unwritten and counted `dropped_stale`), writes
  the bundles, and appends each spec's three entries (screened, verdict,
  gauntlet/graveyard) in ONE write, so a crash cannot leave a `screened`
  orphan. Before this the stage held the lock for its whole write phase
  (5.3 s per spec measured 2026-10-08, 6.8 s on the 2026-10-09 copy, hours
  overnight) and the worker wrote nothing meanwhile. A batch that meets a held lock is KEPT and
  retried at the next chunk, then by a final drain of non-blocking attempts
  every `DRAIN_INTERVAL_S` (5 s) inside `DRAIN_RESERVE_S` (75 s), never past
  the deadline; the reserve is subtracted from the deadline budget only
  while a batch is pending. Specs still unwritten stay `proposed` (the next
  run re-screens them), the exit is 0, and `screen_result.json` counts them
  in `deferred` and `deferred_lock` (a deadline-stopped flush reports
  `stopped_at_deadline` instead, and counts in `deferred` only). The
  loop no longer wraps screen in chain.lock, and a held lock at screen no
  longer defers the cycle. Same hazard as the worker: an unlocked hand-run
  writer that moves the chain inside a batch's hold makes that batch write
  nothing (`ChainMoved`/`UnstableEntry`), exit 1; batches already written
  stay. That exit, like a `CellError` crash, writes NO `screen_result.json`
  (the loop unlinked the old one before the run, so none exists). A
  crashing spec (`CellError`) keeps the earlier batches; it and every later
  spec stay `proposed`.

