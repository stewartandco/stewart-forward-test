---
paths:
  - "pipeline/signal_service.py"
  - "pipeline/test_signal_service.py"
  - "tasks/run_signal_service.bat"
---
## Signal service (build 4a, 31_SignalService)
- `python -m pipeline.signal_service` (wrapper `tasks/run_signal_service.bat`, task
  `\Morpheus\31_SignalService`, daily 08:45, after the 08:20 quarantine
  daily) writes `logs/signals/targets-<bar_end>.json` for every crypto 1d
  strategy in state `quarantine` or `live`: `{bar_end, engine_rev,
  data_digest {asset: sha256 of data/<asset>_1d.csv, for the assets the
  published strategies trade}, written_utc, strategies {sid: {spec_sha, max_dd
  (the screened verdict's, or null), assets {asset: {side long|short|flat,
  frac}}}}, deferred {sid: reason}}` (`deferred` is always present, `{}` when
  nothing is deferred; the executor ignores it). The trading-systems executor
  reads exactly this shape; change it only together with the executor.
- **READ-ONLY.** One `Registry.snapshot()` read (complete lines only), never
  an append, never `logs/chain.lock`, never data/, never git. The file is
  published by `os.replace` from a `.json.tmp`, so the executor never reads a
  torn file. logs/ is gitignored: a targets file is never committed.
- **P1, the synthetic next bar:** entries (and time / declared exits) fill at
  the NEXT open from the last close's decision, so a plain run cannot see a
  decision taken on the last close. `next_bar_position` appends one flat bar
  (open = high = low = close = the last close) and reads the engine's own
  position after it, with periods per year and version derived exactly as
  `quarantine.observe_day` does. An intrabar stop/target on the next bar is
  by construction not foreseen. Edge case: the engine checks barriers only
  after the entry bar, so a position entered at D's open whose stop/target
  D's close already crossed is predicted FLAT (the synthetic open gaps
  through the level); the real D+1 could still hold if it gapped back
  without touching the level. That is not in the parity check's excused
  class (stop/target exits) and was not observed there (3656 compared,
  2026-10-16). Tests: `test_signal_service.py`.
- **Freshness is per strategy (fix round 1; minors item 1, 2026-10-16).** Each
  `data/<asset>_1d.csv` is read ONCE; `data_digest` is the sha256 of exactly
  the bytes the bars were parsed from (`parse_daily_csv` = `screen.load_bars`'
  parse, test-pinned). bar_end is the latest last-bar date across every
  served asset. A strategy is published only if EVERY asset it trades has a
  price file, is non-empty and ends on bar_end; any other strategy is
  DEFERRED: listed under `deferred` with the reason (`<asset>: no price file
  ...`, `<asset> EMPTY`, `<asset> behind: last bar D, bar_end E`), printed,
  and absent from `strategies` -- never a flat or omitted asset under a fresh
  bar_end. The executor (`bot/execution/runner.py`) reads a strategy absent
  from a fresh file exactly like a stale signal: it holds that edge's last
  valid contribution <= 26 h from its bar's close ("signal stale, holding"),
  then flat ("signal stale"). When NO strategy can be published the run
  prints every asset's last date and every deferral, writes NOTHING and
  exits 2, and the Sentinel sees task 31 fail. **Today every served strategy
  trades BTCUSD+ETHUSD, so one lagging asset still refuses the whole run**
  (test-pinned).
- **bar_end must be EXACTLY the bar that closed at the start of the run's UTC
  day (fix wave I2; minors item 2).** `--as-of` - 1 day; `--as-of YYYY-MM-DD`
  defaults to the UTC date today and exists for tests. OLDER = stale:
  uniformly stale data (every asset on the same old bar) refuses instead of
  rewriting an old targets file and exiting 0. NEWER = a bar dated the run
  day or later, i.e. a still-open (partial) bar; `data_fetch` drops the open
  kline, so that is bad data and refuses (`partial:`). A missing registry
  file refuses too: `Registry.snapshot` reads a missing file as an empty
  chain, which used to print "nothing written" and exit 0. An existing chain
  with nothing in quarantine/live still exits 0.
- **Per-cell USDT strategies are served (Switch 1).** A crypto 1d strategy in
  quarantine/live is now a ONE-asset book on a USDT cell (`BTCUSDT`, file
  `data/BTCUSDT_1d.csv`, written by 33_CryptoGridRefresh); the pre-switch
  pooled BTCUSD+ETHUSD books are served as before. With per-strategy
  deferral (above), one lagging USDT file defers only the strategies that
  trade it; the rest publish. A MALFORMED CSV still refuses the whole run,
  which is why `crypto_data.fetch_symbol` validates the whole merged CSV
  with the same rules before it replaces a file (2b-3 plan 1 Task 13). The
  refresh's end cap (last complete UTC day) is what puts the USDT files on
  the same `bar_end` as the 08:20 BTCUSD/ETHUSD. The Sentinel channel for
  deferrals is `logs/signals/status.json` (bullet below; 2b-3 plan 1 Task 14,
  a Switch 1 merge precondition).
- **A malformed price CSV refuses the whole run (minors item 3 + review
  follow-up).** A header without every column (a BOM-prefixed `date`
  included), a short row, a row LONGER than the header (csv.DictReader files
  the extras under the `None` key), a non-numeric or non-finite price, a
  non-positive open/high/low/close (volume may be 0), a date that is not
  `YYYY-MM-DD` (Python 3.11+ `fromisoformat` alone would take `20260102`), a
  second row for the same day (time part ignored; the message names the
  first row), or bytes that are not UTF-8 raise `DataRefused` naming the file
  and the row (the physical line, header = row 1): exit 2, nothing written,
  never a traceback.
- **Deferrals reach the Ops Sentinel through `logs/signals/status.json`
  (2b-3 plan 1 Task 14).** Deferrals alone still exit 0 (the task result is
  green), so the status file is their channel. EVERY run that gets past
  argument parsing writes it, atomically (`status.json.tmp` + `os.replace`
  with the targets publish's PermissionError retry), after the targets
  publish: `{written_utc, bar_end (null when none was determined),
  exit_code (0 or 2), published (strategies in the targets file), deferred
  {sid: reason} (as computed; {} on a refusal that never got that far),
  deferred_since {sid: first-seen UTC date}}`. `deferred_since` keeps the
  previous status's date for a sid still deferred, gives a newly deferred
  sid the run's UTC day (the `--as-of` date, default today UTC: the
  `deferred_since` dates are `--as-of` dates, not `written_utc` dates), and
  drops a sid no longer deferred (it also restarts at an unreadable date for
  one sid). A missing previous
  status is silent; a malformed one (bad bytes/JSON, not an object,
  `deferred_since` not an object) counts as empty and prints a `WARNING`
  line, so every clock restarts. Refusals write it too (`exit_code` 2). A
  refusal BEFORE the deferral step (stale or partial `bar_end`, malformed
  CSV, missing registry) cannot compute deferrals, so it carries the previous
  status's `deferred` and `deferred_since` forward unchanged and sets
  `"deferrals_not_computed": true` (the key is `false` on every run that
  computed them, including an exit 2 for "nothing publishable"); a refusal
  day therefore never resets a sid's clock, and the next normal run
  recomputes and drops recovered sids. A status write that FAILS (a reader
  holding `status.json` past the retries, a full disk) prints `WARNING:
  could not write status.json: ...` and leaves the exit code alone (a good
  publish stays 0, a refusal stays 2); the Sentinel's 20 h staleness FAIL is
  the backstop. A crash (exit 1) writes NOTHING, so staleness is the
  signal. It lives beside
  the targets files but is not matched by the executor's `targets-*.json`
  glob or by retention. **Sentinel side** (sc-ops-sentinel,
  `sentinel/checks/signal_status.py`, manifest block `signal_status`
  `{path, max_age_hours: 20, warn_after_days: 2}`): FAIL when the file is
  missing, unreadable, older than 20 h by `written_utc`, or `exit_code` is not
  0 (2 = refused). 20, not 26: the 08:45 write is about 0.5 h old at the
  09:15 digest and yesterday's is 24.5 h old, so 26 h would pass a day on
  which the service wrote nothing (31_SignalService is only `observing`, its
  exit code is not read); a hand Sentinel run before 08:45 FAILs on
  yesterday's status, expected. WARN for any sid deferred on its 2nd
  calendar day or later (the first day counts: `(written_utc date -
  deferred_since).days + 1 >= 2`, so first deferred on D and still deferred
  on D+1 WARNs at the D+1 run); OK otherwise. **Merge order:** the Sentinel branch must
  merge only AFTER the first signal-service run on this code has written
  `status.json`, else the check FAILs "missing" (same ordering as the
  `crypto_refresh` ledger).
- **Retention (minors item 4).** After a successful publish,
  `targets-<YYYY-MM-DD>.json` files beyond the newest `KEEP_TARGETS` (30) by
  the date in the name are deleted. Never the file just written (even if a
  re-run for an older day left newer names); a name without a date and the
  `.json.tmp` are never touched; a file that cannot be deleted (a reader holds
  it) is printed (`could not prune ...`) and skipped. A refusal prunes nothing.
- **Exit codes (M8):** 0 written (or nothing served; deferrals alone are
  still 0, carried to the Sentinel by `status.json`, see above); **2 = REFUSED** (no strategy publishable, stale or partial
  bar_end, a malformed CSV row, missing registry), no targets file written
  (only `status.json`, `exit_code` 2); 1 = an
  unhandled crash (Python's own); 4 = the wrapper could not open its run log;
  124 / 125 = the task guard ended the run (wall / the task's cmd.exe
  stopped). The publish's `os.replace` is retried up to 8 times (0.25 s
  apart) on PermissionError, which Windows raises while a reader holds the
  target open, then raises (exit 1).
- **The wrapper runs under the task guard (minors item 5).**
  `tasks/run_signal_service.bat` re-runs itself under `python -m
  pipeline.taskguard --wall-s 1500 --log ...` (`RL_TASKGUARD=1` marks the
  inner copy; the run_gauntlet_worker.bat pattern), so an overrun is ended
  with its python and the run log handle is released for the next fire. No
  `--lock`/`--release-lock`/`--git-dir`: the service takes no lock and runs
  no git, so nothing delays the kill, which lands inside the task's PT30M.
  Any other exit code passes through unchanged. CRLF, `%PY%` by absolute
  path, no git (test-pinned).
- **The module is `signal_service.py`, NEVER `signal.py` (fix wave, C1).**
  A script run BY PATH puts its own directory first on sys.path, and
  trading-systems' `run_executor.bat` runs `pipeline\taskguard.py` by path:
  a `pipeline/signal.py` shadowed the stdlib `signal` that `subprocess`
  imports, so the guard died (ImportError) before it started. No
  `pipeline/*.py` may share a name with a stdlib module
  (`sys.stdlib_module_names`); `test_signal_service.py` pins it and runs
  `taskguard.py --help` by path.

