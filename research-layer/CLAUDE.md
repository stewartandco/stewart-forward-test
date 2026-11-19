# research-layer -- pipeline notes

**This file is a MAP** (harness audit #11, Coen 2026-11-19). Every rule that used to be here lives
VERBATIM in `.claude/rules/<topic>.md`. Each rules file loads when Claude READS or EDITS a file its
`paths:` match. A Bash-only session (a hand run, a git command, a scheduled-task check) loads NONE of
them, so the "Always" block below is what every session must know. Before changing anything a topic
governs, Read its rules file in full. Sections refer to each other by title; the index maps titles to
files. Keep this map under 200 lines and 16 KB (`tools/test_claude_md_map.py`): new detail goes into
a rules file, never here.

## Always (no rules file loads for a Bash-only session)

- **Chain lock.** Before ANY batch of chain writes check `logs/chain.lock`: if present another writer is
  mid-window, so wait or defer. Hold it by hand ONLY around a raw writer that does not lock for itself:
  `python -c "from pipeline.chainlock import ChainLock; l=ChainLock('logs','session','manual work'); l.acquire(); input('release?'); l.release()"`.
  Tools that take it themselves (`tools.grid_switch chain-note`, `tools.reextract_shadow`, the stages)
  REFUSE while any chain.lock exists, so run them with none held. Never remove a chain.lock by hand: a
  stale one is broken by code; after a guard kill that `LEFT chain.lock in place`, run `verify_registry.py`
  first. (chain-lock, task-guard)
- **Live-tree git.** Git tracks `registry_log.d/`, NEVER `registry_log.jsonl`. NEVER checkout/switch/
  reset/rebase onto a commit from before the switch; never revert or reset the switch commit; never
  `git clean -x`/`-X`; never `git add -f registry_log.jsonl`; never rewrite history to drop the old chain
  blobs. Before ANY merge into live, this must print NOTHING:
  `git diff --name-only $(git merge-base HEAD <branch>) <branch> -- research-layer/registry_log.jsonl`.
  A hand chain commit = `python -m pipeline.chain_mirror sync`, then stage `research-layer/registry_log.d`;
  `git commit --pathspec-from-file=<an empty file>` commits the WHOLE index. A sync refusal: read
  chain-mirror.md (a prefix mismatch is Coen's call); never delete `registry_log.d` or hand-edit its
  MANIFEST to make a sync pass. (chain-mirror)
- **Worker git step.** NEVER `git merge`/`checkout`/`switch`/`reset`/`stash` in the LIVE tree while a
  worker run is in its git step. First ALL of: the last line of `logs/gauntlet-worker-run.log` is the
  current run's `==== ... exit N ====` (or `taskguard:`) line; no `.git/index.lock`,
  `logs/gauntlet_worker.lock` or `logs/chain.lock`; `git diff --cached` empty; the next :00/:30 fire
  more than a few minutes away. (gauntlet-worker)
- **Hand runs.** Never hand-run `python -m pipeline.gauntlet` without Coen's say-so. DO NOT run
  `pipeline.loop --seed-watermarks`. `pipeline.loop --dry-run` still runs stage 0, which rewrites `data/`
  and the tracked manifest; a hand `tradfi_data snapshot` is a REPAIR, never a routine. A real
  `python -m pipeline.composer` DRAINS the live sibling queue: pass `--loop-state` or `--dry-run`. Run a
  task wrapper as `cmd /c tasks\<wrapper>.bat` or from PowerShell, never typed into an open cmd console
  (its wall shrinks). The crypto-refresh wrapper run after 08:12 fetches nothing and marks every cell
  skipped. `tools.reextract_shadow` (spends money) and `tools.intraday_prefetch` are hand-run only, never
  scheduled: read their rules files first. (pipeline-loop, composer-hand-run-hazards, task-guard,
  crypto-refresh, reextract-shadow, intraday)
- **Chain copies.** Copy-based tools (replay, parity, rehearsals) run on a COPY of the chain taken between
  worker fires with no chain.lock held; check line count and size before and after. (gauntlet-stats)
- **Hand edits of state.** `logs/loop_state.json`: a TARGETED edit only, between fires (no loop.lock or
  chain.lock), backed up, CRLF kept; NEVER lower a class's headroom; never hand-type a card count into
  the loop; any out-of-cycle disposition of pending cards needs a re-bank. (pipeline-loop, triage-cost-controls)
- **Shared-tree files.** `data/tradfi_snapshot_manifest.json` and `data/crypto_snapshot_manifest.json`
  are tracked, rewritten by tasks and never committed: showing modified is expected; never
  `git checkout --` them. Never hand-write `logs/screen_commit_paths.txt` from PowerShell 5.1 (UTF-16).
- **Schedule (Perth).** Worker every :00/:30 (PT28M); stats 01:00 (PT6H); crypto refresh 08:02-08:15;
  quarantine daily 08:20; signal service 08:45; Reader + Sentinel 09:00-09:15; live gate Sun 09:10;
  loop 20:00 (PT11H, ends 07:00). Standing rule: no hand writes to the live tree 20:00-07:00.
- **Reverts and chained notes.** Never edit a chained note. A note is chained verbatim only after Coen
  approves the exact text (the two Switch 2 notes Coen chains himself). Arming text starts `<name>: `
  (colon AND space: `<name>:text` arms nothing) and must be LF bytes (`core.autocrlf` is on). Never revert
  Switch 2 once an intraday strategy is registered (pause = a forward commit setting crypto `ACTIVE_CELLS`
  timeframes back to `("1d",)`). Beats-hold: a rollback needs a superseding chained note FIRST, and
  after the first v6.2 verdict only a forward commit keeping `PROTOCOL_V62` in `V61_FAMILY`. A code
  rollback after amendment-2 also needs a superseding note; `CUT_RHO` changes only by a new note. A D-i
  revert must also delete `seriescache/`. (switch2, beats-hold, gauntlet-stats, series-cache)
- **Coen-only.** Repairing an intraday feed hole (deleting a `data/<ASSET>_<TF>.csv` IS that repair);
  editing `data/intraday_holes_ack.json`; `--chain` on task 28; moving a strategy out of the graveyard;
  retiring a cell (code + chained note together) or growing `EXCLUDED_CELLS`; answering a repeating
  `deferred_memory` (a capacity question, never a constant to raise; also the Switch 2 PAUSE trigger);
  registering a scheduled task (elevated, his step). Never touch trading-systems' `RouterConfig(mode="live")`.
- **Money and reporting.** `pipeline/budget.py` `PIPELINE_CAP_USD` (200) is THE cap; the binding
  lever is `TRIAGE_CEILING`; tests derive thresholds from the constants, never a dollar literal. More
  registrations or survivors at a fixed bar is arithmetic about the denominator, NEVER evidence of edge.
- **Wrappers, schedules, modules.** Task wrappers call `%PY%` by ABSOLUTE path, never bare `python`.
  Change a task's ExecutionTimeLimit and its wrapper's `--wall-s` together. Two-place settings:
  25_PipelineLoop's limit (its XML AND `apply_retry_settings.ps1`) and the live gate cadence
  (`run_livegate.bat` AND `morpheus-hub/tasks/xml/26_LiveGateWeekly.xml`). No `pipeline/*.py` may share
  a stdlib module name (`signal_service.py`, never `signal.py`).

## Rules files (`.claude/rules/`, each loads when Claude reads or edits a file its `paths:` match)

- `chain-lock.md`: Chain lock (logs/chain.lock): who holds it, retries, the manual hold, the unlocked-writer hazard
- `chain-mirror.md`: Chain mirror: registry_log.d/ segments, sync, the live-tree git prohibitions, recovery
- `intraday.md`: Intraday: per-bar mechanics, daily-mark statistics, prefetch/rehearsal tools and their windows
- `switch2.md`: Switch 2: crypto intraday live, the pause and revert rules, the two chained notes
- `pipeline-loop.md`: Pipeline loop (25_PipelineLoop): stages, trigger basis, watermarks, preflight, status files
- `task-guard.md`: Task guard: job objects, walls vs ExecutionTimeLimit, %PY%, wrapper exit codes, kill recovery
- `gauntlet-worker.md`: Gauntlet worker (27): protocol records, the worker, the git-step hazard, run order, locks
- `gauntlet-stats.md`: Statistics job (28): memory gate, effective trials, --chain, status files, exit codes, verifier, the five, parity
- `beats-hold.md`: Beats-hold (v6.2 / live gate v2): armed switches, rollback via superseding note, V61_FAMILY
- `sweep-rotation-retrials.md`: Sweep rotation, sibling queues, re-trials (D6/D10/D9): what charges N, known protocol gaps
- `composer-hand-run-hazards.md`: Composer hazards for hand runs in the live tree (the sibling queue)
- `cycle-deadline.md`: Cycle deadline: --deadline-utc, SAFETY_MARGIN_S, deferral, <stage>_result.json
- `gauntlet-worker-pool.md`: Gauntlet worker pool sizing (commit-bounded), simcache migrate
- `reextract-shadow.md`: Re-extract shadow run (tools/): dry-run first, holds chain.lock, spends money, run windows
- `triage-cost-controls.md`: Triage cost controls (stage 4a): ceilings, D44, the two ExecutionTimeLimit places, resident scanner
- `quarantine-catchup.md`: Quarantine daily catch-up (23, 08:20): owed dates, chain.lock per date, degraded ledger
- `series-cache.md`: Engine series cache: bit-identity rule, parity before any change, the D-i rollback rule
- `live-gate.md`: Quarantine -> live gate (26, Sun 09:10): verdicts, chain.lock, LIVE is a lifecycle state
- `signal-service.md`: Signal service (31, 08:45): read-only targets for the executor, its shape contract, exit codes
- `crypto-refresh.md`: Crypto grid refresh (33, 08:02-08:15): deadlines, bans, the tracked snapshot manifest
- `feed-health.md`: Feed health: holes, the Coen-only ack file, restated hazard, the digest as the alert
- `switch1-crypto-cells.md`: Switch 1: crypto per cell at 1d, retiring/excluding cells, merge-day gates
- `triage-escalations-removed.md`: Triage escalations: REMOVED by D44 (stub)
- `budget-lines.md`: Budget lines (D41): one cap constant, Reader 20, TRIAGE_CEILING as the lever
- `survivorship-study.md`: Survivorship study (tools/, closed): copy-only registry, run window, re-attempt = new study
