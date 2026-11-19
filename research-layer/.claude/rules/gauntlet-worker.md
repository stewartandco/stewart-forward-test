---
paths:
  - "pipeline/gauntlet_worker.py"
  - "pipeline/test_gauntlet_worker.py"
  - "pipeline/gauntlet_core.py"
  - "pipeline/test_gauntlet_core.py"
  - "pipeline/gauntlet.py"
  - "tasks/run_gauntlet_worker.bat"
  - "docs/notes/gauntlet-protocol-v6.1*.md"
---
## Gauntlet worker + recorded statistics (Build 2a, 2026-09-30)
Design: `docs/2026-09-30-gauntlet-at-scale-design.md`. Why: the gauntlet's first
phase re-simulated and clustered EVERY registered strategy before judging any
candidate, was not deadline-aware, and stopped completing as the registry grew.
Nothing the clustering produces is an input to any gate, so verdicts moved out
of it.
- **Protocol records.** `docs/notes/gauntlet-protocol-v6.1.md` (the addendum)
  and `docs/notes/correction-2026-09-30-pbo-family-kill.md` are chained
  VERBATIM, and only after Coen approves the exact text, BEFORE the first v6.1
  verdict. Once chained the chain is the record: never edit the files to
  "fix" a chained note. The addendum's first line starts
  `gauntlet-protocol-v6.1: `, and BOTH the worker's refusal check and
  verifier invariant 12 key on that prefix. With no such note the worker exits
  1 (`refused_no_protocol_note`) and writes nothing.
- **Worker: `python -m pipeline.gauntlet_worker --max-workers 6`**, wrapper
  `tasks/run_gauntlet_worker.bat`, task `\Morpheus\27_GauntletWorker` every 30
  minutes, 25-minute deadline per run. It judges queued candidates cheapest
  first with the six standalone gates in `pipeline/gauntlet_core.py`
  (sharpe_floor 0.4, oos_negative, edge_decay -25%, mc_p05 1.0, p_ruin 0.05,
  cost_stress; order and thresholds unchanged) and chains each verdict plus its
  state change as soon as it exists. Its import graph has no clustering or PBO
  module (a test pins it). Verdicts carry `metrics.protocol
  gauntlet-protocol-v6.1` and NO registry-wide statistics. The family kill is
  NOT applied. After each verdict write the worker appends the judged bundle's
  repo-relative path to `logs/gauntlet_worker_commit_paths.txt`; the wrapper
  commits `registry_log.jsonl` plus those bundles best-effort in ONE commit,
  scoped by `--pathspec-from-file` (never argv, never anything another session
  staged), and clears the list only after that commit succeeds. It skips the
  commit while chain.lock exists (the list is kept) and never pushes.
- **⚠ NEVER run `git merge`, `checkout`, `switch`, `reset` or `stash` in the
  LIVE tree while a worker run is in its git step (incident 2026-10-02 12:21).**
  `logs/gauntlet_worker.lock` is released when the python process exits, but
  `run_gauntlet_worker.bat` runs `git add` / `git commit` AFTER that. A merge
  started in that gap collided with the wrapper's commit ("unable to unlink old
  'research-layer/registry_log.jsonl': Invalid argument"), and the chain file
  was left at HEAD: the run's 48 uncommitted verdicts vanished from disk. The
  chain stayed VALID (a clean truncation) and the next run re-judged the same
  48 to identical verdicts on unchanged data, but on a day the data refreshed
  in between the re-judgement could differ. Before any such git command in the
  live tree, ALL of: the last line of `logs/gauntlet-worker-run.log` is the
  current run's `==== ... exit N ====` line (or its `taskguard:` kill
  line); no `.git/index.lock`,
  `logs/gauntlet_worker.lock` or `logs/chain.lock`; `git diff --cached` is
  empty; and the next :00/:30 fire is more than a few minutes away.
- **Run order and failure rules.** (1) Orphan repair: a v6.1 verdict with no
  following state change gets the change it implies (fail -> graveyard with the
  gate as the reason, pass -> quarantine); a verdict is NEVER re-evaluated.
  (2) The queue. A candidate that raises gets NO verdict, stays queued, and the
  run exits 1; nothing is skipped or buried because of a crash. A v6.1 orphan
  is caught ONLY by the worker's own repair at the start of each run: the
  loop's `gauntlet_orphan` pre-spend check (`loop._gauntlet_orphans`)
  deliberately EXCLUDES v6.1 verdicts, so the 20:00 loop never sees one, and
  an orphan is judged, so it is not counted in the status `queued` or
  `oldest_queued_age_hours`. If the repair itself raises (a chain or disk
  failure, or a chain that moved under chain.lock) the run exits 1 with
  `exit_reason` `crashed`, and the Sentinel FAILs both the task result and the
  `gauntlet_queue` check. If chain.lock is held the repair is deferred to the
  next run with only a log line (`orphan repair deferred` in
  `logs/gauntlet-worker-run.log`, `repaired` 0); nothing alarms on a deferral
  alone. (A hand run of `pipeline.gauntlet` refuses on any orphan, v6.1
  included, and exits 1; the loop no longer runs it, so no loop path creates a
  v6 orphan, and the `gauntlet_orphan` pre-spend check stays only as the alarm
  for one that already exists or that a hand run leaves.)
- **Locks.** The worker's own instance lock is `logs/gauntlet_worker.lock`
  (stale after one hour, broken only when stale AND its holder is dead). A
  second instance defers (exit 0) and NEVER writes the status file, so a wedged
  holder's status goes stale and the Sentinel catches it. chain.lock rules are
  in the Chain lock section: held for a tail read plus one append, never
  waited for, never broken; a write that finds it held is kept and retried
  within the same run (between chunks, then a <= 75 s final drain inside the
  deadline), never carried to the next run; an unlocked hand-run writer in
  that window turns into an exit-1 alarm.
