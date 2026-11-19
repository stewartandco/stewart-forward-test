---
paths:
  - "pipeline/taskguard.py"
  - "pipeline/test_taskguard.py"
  - "pipeline/test_chain_mirror_wrappers.py"
  - "tasks/*.bat"
  - "tools/taskguard_s4u_check.ps1"
---
## Task guard: a stopped task leaves nothing running (incident 2026-10-10/11)
- **Why:** at a task's ExecutionTimeLimit Task Scheduler terminates ONLY the
  process it launched, the wrapper's `cmd.exe` (TaskScheduler event 329,
  action result 0x8007042B). Windows never kills children with their parent.
  On 2026-10-10 the 16:00 worker's python outlived its PT28M wrapper by over
  an hour holding `gauntlet_worker.lock`; on 2026-10-11 the 01:00 stats run's
  python outlived PT6H by 45 min with 5.8 GB committed. Worse, cmd opens a
  `>>` target WITHOUT write sharing and the orphan kept the inherited
  handle, so the 16:30/17:00/17:30 fires could not open the run log: cmd
  skipped every redirected command, python never started, ERRORLEVEL stayed
  0, and each fire exited 0 having done nothing (reproduced in scratch).
- **Mechanism (`pipeline/taskguard.py`, stdlib + ctypes):**
  `run_gauntlet_worker.bat` and `run_gauntlet_stats.bat` re-run themselves
  under `"%PY%" -m pipeline.taskguard ... -- cmd /d /c "%~f0"`
  (`RL_TASKGUARD=1` marks the inner copy). The inner cmd and everything it
  starts sit in one job object with KILL_ON_JOB_CLOSE, and the guard's tree
  is that job PLUS every descendant of the inner cmd by parent pid. The
  guard ends the tree (a) at its wall, exit 124; (b) when the outer cmd Task
  Scheduler kills dies, exit 125; (c) if the guard itself is killed (the
  job's last handle closes). It also ends anything a NORMAL run leaves
  behind (log line `the run left N process(es) running`) and returns the
  inner wrapper's exit code unchanged. The wall counts from the outer
  cmd.exe's creation (the task's start), clamped to at most 10 min before
  the guard started, so a hand run from an old console is not killed at
  once. Walls: worker 1600 s + 45 s grace inside PT28M (a held append/index
  lock may stretch the kill to 150 s); stats 21300 s + 60 s inside PT6H (up
  to 240 s). The worker's 150 s can run PAST PT28M (1600 + 150 + up to 30 s
  for the tree to go = ~1780 s): Task Scheduler then stops the outer cmd and
  the task result is 267014, not 124, but the guard is not stopped with it
  and still ends the tree before the next :00/:30 fire. A hand run of a
  wrapper typed into an ALREADY-OPEN cmd console runs inside that console's
  cmd.exe, so its clock starts the 10-min clamp back (wall ~16.7 min, worker
  deadline ~15 min); run it as `cmd /c tasks\run_gauntlet_worker.bat` or
  from PowerShell for the full window. **Change a task's
  ExecutionTimeLimit and its wrapper's `--wall-s` together.**
- **⚠ `%PY%` is the interpreter by ABSOLUTE path, never bare `python`
  (cold review 2026-10-11, reproduced).** On this box bare `python`
  resolves to the WindowsApps PythonManager alias first (User PATH lists
  WindowsApps before Python\bin). It is a packaged app: it and the runtime it
  starts BREAK OUT of the job, and the guard's parent would be the alias, not
  the cmd. `Python\bin\python.exe` is the PyManager SHIM (not a link: its
  `python.exe.__target__` names `pythoncore-3.14-64\python.exe`), measured
  2026-10-11 as one process that stays in the job and passes the exit code
  through. A PyManager reinstall or default change rewrites `__target__` and
  so changes the interpreter silently; after one, re-run
  `test_taskguard.py` and `test_chain_mirror_wrappers.py`. The guard backs this up (it watches the nearest
  non-launcher ancestor and sweeps escaped descendants, logging `N had
  escaped the job`), but an escape line in the log means a wrapper is
  calling a launcher again: fix the wrapper. A new wrapper uses `%PY%` too.
- **Never mid-write.** Before a kill the guard suspends the whole tree and
  checks the locks it was given: `--lock` (logs\chain.lock,
  registry_log.d.lock) is waited for up to `--grace-s`; `--hard-lock`
  (registry_log.jsonl.lock, the chain's append FileLock) and an in-tree
  git.exe holding `.git\index.lock` up to `--hard-grace-s`. Between checks
  the tree is resumed so the holder can finish; past the grace it kills
  anyway and says `killed while busy`. A kill inside an append leaves a torn
  tail every later writer refuses (ChainMoved); a dead git leaves an
  index.lock that blocks all git. A lock file that has named nobody for
  over 5 s is not waited on. After a kill (and once the tree is gone, so
  nothing holds the log) it writes its log line, then removes a lock ONLY
  when it names a pid the guard just killed (provably dead, the loop's
  dead-pid fast path): the `--release-lock` instance lock
  (gauntlet_worker.lock / gauntlet_stats.lock) always; a `--lock`
  (chain.lock, the mirror lock) only when no killed pid also held a
  `--hard-lock`. Why (round-2 review): only the 20:00 loop breaks a dead
  holder's chain.lock, so one left by a kill would stall every chain writer
  (worker `deferred_lock` and no commit, stats, quarantine) for up to a day,
  mostly silently. If the killed tree DID hold the append lock the log says
  `LEFT chain.lock in place ... check the chain`: run `verify_registry.py`
  before removing it by hand. A `--hard-lock` is never removed.
- **Known gap:** a process that escaped the job (only a packaged launcher
  does) AND whose parent then died is not reachable by the parent-pid sweep.
  Nothing in the pipeline does that today; `%PY%` keeps everything in the
  job.
- **What a guard kill looks like:** the run log's last line is
  `taskguard: wall Ns reached; ended the task's process tree (exit 124)` (or
  `parent process exited ...`), with NO `==== ... exit N ====` line for that
  run, and the task result is 124. Treat a `taskguard:` line as that run's
  terminator in the pre-merge checklist below. A 124 is a defect to read (the
  job's own deadline should have ended it first), never weather.
- **Wrapper exit 4 = the run log is locked** by another process: nothing
  ran, a line went to `logs\gauntlet-worker-run.locked.log` (or
  `gauntlet-stats-run.locked.log`). With the guard this should never happen;
  if it does, find the holder (scoped, never by image name) before anything.
- **The worker's git block uses `|| goto :keep`**, never `if errorlevel 1`,
  after a redirected git: a failed `>>` leaves ERRORLEVEL unchanged, so the
  old test fell through and deleted the bundle list with nothing committed.
- **The task clock.** The guard always sets (overwrites)
  `RL_TASK_STARTED_UTC` to the task's start as above; the worker's 25-minute
  deadline and the stats job's `--deadline-hours` count from it
  (`taskguard.task_started`), so python's startup counts (3 min 19 s on
  2026-10-10 under memory pressure). A missing, future or over-a-day-old
  stamp means "now" (hand runs and tests are unchanged).
- **Fail open:** if the job object cannot be made the command still runs
  under the wall, the parent watch and the descendant sweep, with a
  `taskguard: WARNING no job object` line.
- **Proof on the real S4U path** (Interactive tasks do NOT show the bug:
  stopping one sends Ctrl+C to its console): `tools/taskguard_s4u_check.ps1`,
  run by hand ELEVATED. It registers two throwaway S4U tasks with a PT1M
  limit (the old wrapper shape as a control, the guarded shape), reads
  heartbeats, prints PASS/FAIL and unregisters them.
- Tests: `test_taskguard.py` (real process trees: wall, parent death, a
  killed guard, the lock waits, stragglers, a python escaping the job via the
  WindowsApps alias, and a CONTROL proving the orphan survives without the
  guard) and the guard tests at the end of
  `test_chain_mirror_wrappers.py` (the real .bat: kill the outer cmd, wall
  then a clean next fire, locked log -> 4).

