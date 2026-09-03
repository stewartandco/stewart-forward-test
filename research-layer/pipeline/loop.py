"""Pipeline loop orchestrator: triage -> compose -> screen -> gauntlet when a
class accumulates enough new cards.

TRIGGER BASIS (amended by Coen 2026-08-29): a class fires on its
TRIGGERABLE count -- class-routable cards that are accepted OR pending,
never rejected -- minus its watermark. Not the accepted-only count: cards
are only ever accepted by the triage panel this module runs at step 4a,
INSIDE a cycle, after the step-2 trigger decision, and nothing else in the
system triages. Reading accepted-only therefore deadlocked the loop
outright (see _triggerable_counts). Both _triggerable_counts and _routable_counts
are reported to status; only the former decides.

Spec: docs/2026-08-27-pipeline-loop-design.md. Invoked by
\\StewartCo\\25_PipelineLoop (~3x daily) as `python -m pipeline.loop --once`.

Exit 0: cycle_complete | no_trigger | no_new_accepted_cards | deferred_lock |
        deferred_budget | deferred_instance | dry_run_would_fire
        (distinguished in logs/pipeline_status.json items.outcome)
Exit 1: stage_failed | chain_invalid | gauntlet_orphan | loop_crashed
        -- a real defect.

ACTIVATION: `python -m pipeline.loop --seed-watermarks` initialises every
LIVE_CLASSES watermark to the current triggerable count, so the FIRST
scheduled fire against a fresh loop_state.json only responds to genuinely
NEW cards rather than triggering a whole-corpus generation for every live
class at once. Mutually exclusive with --once (argparse enforcement); read-
only against the chain, writes only loop_state.json, runs no stages, writes
no status file, exits 0.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import contextlib
import re
import subprocess
import sys
import traceback

from . import deadline as _deadline
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import cells, loop_state, pipeline_budget, pipeline_status
from .budget import BudgetMeter, PIPELINE_CAP_USD
from .chainlock import ChainLock, ChainLockHeld
from .composer import expand_family, expander_for, routable_cards
from .registry import Registry

LAYER_DEFAULT = Path(__file__).resolve().parent.parent
Runner = Callable[..., object]

# Cards the triage stage may review per cycle. Sized to fit the scheduled
# task's ExecutionTimeLimit with room for the rest of the cycle. Raising this
# without raising the task's limit in quant/tasks/xml/25_PipelineLoop.xml
# re-creates the mid-flight-kill loop.
#
# 200 (Coen 2026-08-31, raised from 40). The 40 was sized against the PT2H the
# XML then declared; the live task is PT4H now, which 200 fits with roughly
# half the window in reserve. It is not merely a speed knob: the watermark is
# banked at the post-triage TRIGGERABLE count, so pending cards a cycle's
# TRIAGE_LIMIT never reached are banked too and stop counting toward the next
# trigger (see the note at the watermark advance). A limit far below the
# backlog therefore STRANDS cards behind the watermark rather than queueing
# them for the next cycle -- the backlog only moves as new cards arrive.
TRIAGE_LIMIT = 200

# Cycle-time model, kept in ONE place. MIN_TASK_WINDOW_S is DERIVED from
# TRIAGE_LIMIT deliberately: the two drifting apart is its own bug. At limit
# 200 a cycle needs ~129 min, so the old hardcoded PT2H threshold would have
# stayed SILENT on a task that could not finish -- the exact failure the
# startup WARN exists to catch.
_TRIAGE_S_PER_CARD = 3.85 * 3           # measured: 3.85 s/call x PANEL_SIZE 3
_REST_OF_CYCLE_S = 90 * 60              # composer dry+real, screen, gauntlet

# D6 sweep rotation (docs/2026-08-28-market-data-universe-design.md s5): a
# generation sweeps a ROTATING WINDOW of this many of its class's active
# assets, not all of them, with the cursor persisted per class in
# logs/loop_state.json. 12 is the spec's declared number, sized against the
# 100-asset crypto universe: full coverage in 9 generations (100/12, last
# window short).
#
# IT IS A SCHEDULE, NEVER A SELECTION. Every active cell is swept with equal
# frequency (pinned in test_sp5_p2t4.py); the window decides WHEN a cell is
# swept, never WHETHER. Nothing about N accounting changes: a cell outside
# this generation's window is not excluded, it is next.
ROTATION_SIZE = 12

# WHICH classes rotate, DECLARED rather than inferred -- the same convention
# cells.LIVE_CLASSES and cells.ACTIVE_CELLS follow, and for the same reason:
# a change to what a generation sweeps is a decision, never a side effect of
# an asset count.
#
# The tempting rule is "rotate whenever the active set is bigger than the
# window". It is WRONG here, and measurably so: equity_etf's active set is 16
# assets, above ROTATION_SIZE, so that rule would silently window equity_etf
# 12-of-16 and change what a live class sweeps -- inside the phase whose
# whole invariant is that nothing sweeps differently (SP5 Phase 2 is
# "behavior-frozen for sweeping"). Design s5 scopes D6 to crypto in its own
# words ("a CRYPTO generation sweeps a rotating window of 12 assets ... full
# coverage in 9 generations (100/12)"), which is the 100-asset universe the
# number was derived from; the four tradfi classes are 2-16 assets and have
# no cost problem to solve. Adding a class here is a reviewed commit, and
# ships with its own window size if 12 is not right for it.
#
# Crypto's own active set is EMPTY this phase (ACTIVE_CELLS["crypto"] is
# ((), ())), so rotation is inert everywhere today -- machinery built,
# behaviour frozen. Pinned by test_phase2_freeze.py.
ROTATION_CLASSES = ("crypto",)

# The scheduled task this module runs under, and the shortest execution
# window a full cycle can survive. A cycle is 75-90 min (triage + composer
# pair + screen + gauntlet); Windows hard-kills at ExecutionTimeLimit, and a
# killed cycle never reaches the watermark advance, so the class re-fires on
# the next tick and pays full freight again -- forever, silently, because a
# killed task leaves no failure the Sentinel can see. On 2026-08-29 the live
# task carried PT1H while its XML declared PT2H (apply_retry_settings.ps1 was
# overwriting it), which is exactly that trap.
TASK_NAME = r"\StewartCo\25_PipelineLoop"
MIN_TASK_WINDOW_S = int(TRIAGE_LIMIT * _TRIAGE_S_PER_CARD + _REST_OF_CYCLE_S)

# Phase 3 step 3: the cycle's deadline is start + the live task's window minus
# this margin, handed to screen and gauntlet as --deadline-utc so each stops
# BEFORE starting work it cannot finish (pipeline/deadline.py). The margin
# covers the post-gauntlet verify + commit and one gauntlet chunk of slack.
# The task's ExecutionTimeLimit stays the backstop; with the deadline inside
# it, hitting the wall becomes evidence of a bug rather than weather.
SAFETY_MARGIN_S = 15 * 60
FIX_WINDOW_CMD = (
    'schtasks /Create /TN "StewartCo\\25_PipelineLoop" /XML '
    '"E:\\Users\\Coen\\Claude\\quant\\tasks\\xml\\25_PipelineLoop.xml" /F'
    '  THEN  powershell -NoProfile -ExecutionPolicy Bypass -File '
    '"E:\\Users\\Coen\\Claude\\quant\\tasks\\apply_retry_settings.ps1" '
    '-Task 25_PipelineLoop   (both elevated)')


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_run_id(asset_class: str) -> str:
    """Second-resolution, not date-only: a same-day retry after a stage
    failure/deferral must get a DIFFERENT run_id, or composer's
    sibling_group_id = f"{family}-{run_id}" silently merges the retry's new
    specs into the earlier (aborted) run's sibling group, feeding
    plateau.qualifies() a neighbor set the spec was never actually swept
    with."""
    return (datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
           + f"-loop-{asset_class}")


def _routable_counts(registry: Registry) -> dict[str, int]:
    """The HONEST routable number per class: ACCEPTED cards only -- exactly
    the set the composer would consume today. Reported as status
    items.routable_<cls>. NOT the trigger basis (see _triggerable_counts);
    kept because the digest must still say how many cards are actually
    usable right now, separately from how many fired the cycle."""
    accepted = registry.cards(status="accepted")
    return {cls: len(routable_cards(accepted, cls)[0]) for cls in cells.LIVE_CLASSES}


def _triggerable_counts(registry: Registry) -> dict[str, int]:
    """THE TRIGGER BASIS (Coen 2026-08-29): per class, the routable cards a
    cycle COULD act on -- ACCEPTED (already routable) plus PENDING (triage,
    the cycle's own first stage, will resolve them). Rejected cards are
    settled work and NEVER count.

    Fixes a live deadlock. The trigger used to read _routable_counts, but the
    only thing that ever moves a card from pending to accepted is the D31
    triage panel at _run_locked_cycle's step 4a -- INSIDE a cycle, after the
    trigger decision at step 2. Nothing else triages: no scheduled task runs
    pipeline.triage_batch, and the resident scanner only registers PENDING
    cards. So the accepted count was frozen between fires, routable stayed
    equal to the watermark, every fire honestly reported no_trigger, and the
    pending backlog (539 cards by 2026-08-29) could never drain. The loop
    could not start a generation on its own -- ever.

    Routed through the SAME routable_cards() filter as _routable_counts so
    the two numbers differ ONLY in which review states they admit, never in
    routing semantics.

    Whatever this returns MUST also be what gets banked as the watermark
    after a cycle (loop_state's BASIS WARNING): comparing a triggerable count
    against an accepted-only watermark would re-fire forever."""
    live = {cid: c for cid, c in registry.cards().items()
            if (c.get("review") or {}).get("status") in ("accepted", "pending")}
    return {cls: len(routable_cards(live, cls)[0]) for cls in cells.LIVE_CLASSES}


def _parse_iso_duration_s(text: str) -> int | None:
    """Seconds from the ISO-8601 duration subset Task Scheduler emits
    (PT1H, PT4H30M, P1DT2H, PT0S). None on anything unrecognised -- this
    feeds a warning, never a decision, so a format surprise must go quiet
    rather than guess a number."""
    m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?",
                     (text or "").strip())
    if not m or not any(m.groups()):
        return None
    d, h, mi, sec = (int(g) if g else 0 for g in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + sec


def _live_task_window_s(task_name: str = TASK_NAME) -> int | None:
    """The LIVE registered task's ExecutionTimeLimit in seconds, or None if it
    cannot be determined for ANY reason.

    Deliberately total: not registered (tests, a manual run, a fresh clone),
    no schtasks on PATH, a non-Windows host, UTF-16 vs UTF-8 output, an odd
    duration string -- every one of those is None, never an exception. The
    loop must behave identically whether or not a scheduled task exists."""
    try:
        proc = subprocess.run(["schtasks", "/query", "/TN", task_name, "/XML"],
                              capture_output=True, timeout=30)
        if proc.returncode != 0:
            return None
        raw = proc.stdout
        for encoding in ("utf-16", "utf-8"):
            try:
                text = raw.decode(encoding)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        else:
            return None
        m = re.search(r"<ExecutionTimeLimit>([^<]+)</ExecutionTimeLimit>", text)
        return _parse_iso_duration_s(m.group(1)) if m else None
    except Exception:
        # Includes FileNotFoundError (no schtasks), TimeoutExpired, OSError.
        return None


def _warn_if_task_window_too_short(reader: Callable[[], int | None] | None = None
                                   ) -> str | None:
    """Print (and return) a loud WARN when the live task's execution window is
    too short for a full cycle. Returns None when there is nothing to say.

    `reader` defaults to _live_task_window_s resolved AT CALL TIME, not bound
    as a default argument -- a bound default would survive monkeypatching, and
    the test suite has to be able to stub this out globally (every loop.run()
    would otherwise spawn a real schtasks subprocess and make the tests
    depend on this machine's task registry).

    WARN, never a refusal: a nonzero exit here would be a brand-new failure
    path and the Ops Sentinel FAILs the digest on nonzero, so refusing would
    trade a silent problem for a noisy one that also stops all work. The
    warning goes to the run log, where the next reader of a suspiciously
    short cycle will find it."""
    try:
        window = (reader or _live_task_window_s)()
    except Exception:
        return None                     # a warning must never break a cycle
    if window is None or window >= MIN_TASK_WINDOW_S:
        return None
    msg = (f"WARN: {TASK_NAME} ExecutionTimeLimit is {window // 60} min, below "
           f"the {MIN_TASK_WINDOW_S // 60} min a full cycle needs. Windows will "
           f"hard-kill this cycle mid-flight; the watermark will NOT advance and "
           f"the class will re-fire and re-pay on every tick, with no failure "
           f"the Sentinel can see. Fix (elevated): {FIX_WINDOW_CMD}")
    print(msg, flush=True)
    return msg


TRIAGE_RESULT_NAME = "triage_result.json"


def _triage_reviewed(logs_dir: Path) -> tuple[int, int] | None:
    """(reviewed, skipped_escalated) from triage's result file, or None.

    None means triage did not report -- absent, torn or malformed. Never
    guess these here: inferring them from TRIAGE_LIMIT would duplicate
    triage's skip-set-then-slice selection rule in a second place, and the
    two would drift the first time either changed."""
    try:
        data = json.loads((logs_dir / TRIAGE_RESULT_NAME).read_text(encoding="utf-8"))
    except Exception:
        return None
    n = data.get("reviewed")
    m = data.get("skipped_escalated", 0)
    for v in (n, m):
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            return None
    return n, m


def _entry_count(registry_path: Path) -> int:
    """Raw chain-line count. Read-only, no lock -- read paths never take
    chain.lock (chainlock.py's own rule)."""
    if not registry_path.exists():
        return 0
    with registry_path.open("r", encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


# Windows caps a process's whole command line at 32,767 characters and refuses
# to launch beyond it (CreateProcess -> WinError 206). Budget the pathspec
# portion at well under that so the fixed tokens and the interpreter path
# never tip it over. Sized in characters, not paths: a path count would rot
# the moment artifact ids or the repo prefix changed length.
_PATHSPEC_ARGV_BUDGET = 20_000


def _chunk_by_argv_length(paths: list[str],
                          budget: int = _PATHSPEC_ARGV_BUDGET) -> list[list[str]]:
    """Split paths into runs whose space-joined length stays under budget.
    Preserves order; every path lands in exactly one chunk; a single path
    longer than the budget still gets its own chunk rather than vanishing."""
    chunks: list[list[str]] = []
    cur: list[str] = []
    cur_len = 0
    for p in paths:
        add = len(p) + (1 if cur else 0)
        if cur and cur_len + add > budget:
            chunks.append(cur)
            cur, cur_len = [], 0
            add = len(p)
        cur.append(p)
        cur_len += add
    if cur:
        chunks.append(cur)
    return chunks


@contextlib.contextmanager
def _pathspec_file(paths: list[str]):
    """A temp file holding one pathspec per line, for --pathspec-from-file.
    Written and CLOSED before the yield -- on Windows git cannot open a file
    another handle still holds -- and unlinked afterwards whatever happened."""
    fd, name = tempfile.mkstemp(prefix="loop-pathspec-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(paths) + "\n")
        yield name
    finally:
        try:
            os.unlink(name)
        except OSError:
            pass


def collect_commit_paths(registry_path: Path, start_line: int) -> list[str]:
    """Repo-relative paths for this cycle's chain delta: the registry plus
    artifacts/<sid> for every strategy_registered entry appended after
    start_line whose bundle exists on disk. start_line is the chain-line
    count taken by the SAME helper (_entry_count) that also measures
    chain_growth -- one counting rule, not two.

    Layout assumption: registry_path's parent is the research-layer root, a
    top-level directory of the git repo -- the "research-layer/..." prefix
    below is that hardcoded pathspec convention (mirrors run_quarantine.bat),
    not derived from the directory's actual name.

    Honesty note (mirrors chain_growth's): the registry line is included
    unconditionally and may carry foreign rows appended by another writer
    after start_line, not just this cycle's own stages. Committing them
    still commits a VALID chain state -- verify_registry.py already gated
    on that before this ever runs -- and per-row attribution lives in each
    entry's own run_id, not in the git commit boundary.

    Known best-effort gap: an artifact bundle that finishes landing on disk
    in the window between this scan and the `git add` below is simply
    missed this cycle. It is NOT picked up on a later cycle either -- the
    next cycle's start_line has already moved past its strategy_registered
    entry."""
    layer = registry_path.parent
    rel_root = "research-layer"
    paths = [f"{rel_root}/registry_log.jsonl"]
    with registry_path.open("r", encoding="utf-8") as f:
        lines = [ln for ln in f if ln.strip()]
    for ln in lines[start_line:]:
        try:
            entry = json.loads(ln)
        except json.JSONDecodeError:
            continue                # partial concurrent line; tail rules apply
        if entry.get("entry_type") != "strategy_registered":
            continue
        sid = entry.get("payload", {}).get("strategy_id")
        if sid and (layer / "artifacts" / sid).is_dir():
            paths.append(f"{rel_root}/artifacts/{sid}")
    return paths


def commit_cycle(registry_path: Path, start_line: int, run_id: str, runner: Runner) -> None:
    """Scoped commit of this cycle's chain delta (registry_log.jsonl plus the
    artifact bundles registered this cycle). Best-effort: a git failure is
    LOUD (printed) but never fails the cycle -- the chain itself is the
    trust asset, this commit is bookkeeping. Takes registry_path (not layer)
    so the caller's already-computed Path is reused rather than
    recomputed -- see collect_commit_paths for the research-layer/repo-
    layout assumption this implies.

    Two safety properties, both load-bearing on a shared working tree:
    - Preflight, run_quarantine.bat's exact pattern: `git diff --quiet`
      exits 1 when there ARE tracked changes -- that is the commit signal,
      NOT an error. A clean tracked diff AND no new (untracked) artifact
      dirs means nothing changed this cycle; skip silently -- no commit,
      no WARNING.
    - Both `git add` and `git commit` are pathspec-scoped to exactly this
      cycle's delta. A bare `git commit -q -m msg` commits the ENTIRE
      index, which would sweep a concurrent session's separately-staged
      work into this commit -- never do that.
    """
    layer = registry_path.parent
    repo = layer.parent
    paths = collect_commit_paths(registry_path, start_line)
    has_new_artifacts = len(paths) > 1   # more than just the registry line

    # The scope NEVER travels on the command line (2026-09-01). The fx cycle
    # registered 1,260 strategies, chained them, banked its watermark, and
    # then this function crashed with `[WinError 206] The filename or
    # extension is too long`: one repo path per bundle on argv, against
    # Windows' 32,767-character command-line cap. Nothing was lost -- the
    # chain is the trust asset -- but the task exited 1 and the Sentinel
    # reported FAIL for a cycle that had succeeded.
    #
    # `git diff` has no --pathspec-from-file, so the preflight is CHUNKED:
    # any chunk reporting a change is the commit signal. add and commit take
    # the whole scope from one file, so a single commit still covers exactly
    # this cycle's delta -- chunking THOSE would split one cycle into several
    # commits, or worse, commit a partial scope if a later chunk failed.
    changed = False
    for chunk in _chunk_by_argv_length(paths):
        diff = runner(["git", "diff", "--quiet", "--"] + chunk, cwd=str(repo))
        if diff.returncode != 0:
            changed = True
            break
    if not changed and not has_new_artifacts:
        return                            # nothing changed this cycle -- silent, no commit

    with _pathspec_file(paths) as ps:
        add = runner(["git", "add", f"--pathspec-from-file={ps}"], cwd=str(repo))
        if add.returncode != 0:
            print("loop: WARNING git add failed; chain delta left uncommitted", flush=True)
            return
        cm = runner(["git", "commit", "-q", "-m", f"loop: {run_id} chain delta",
                     f"--pathspec-from-file={ps}"], cwd=str(repo))
        if cm.returncode != 0:
            print("loop: WARNING git commit failed (possibly nothing staged)", flush=True)
            return
    print(f"loop: committed chain delta ({len(paths) - 1} artifact bundle(s))", flush=True)


def _spent(logs_dir: str | Path) -> float:
    """A fresh pipeline-scoped ledger read. Deliberately re-instantiates
    BudgetMeter every call instead of caching one: a subprocess stage may
    have appended to the ledger since the last read, and spent_usd on a
    status write must never lag reality. Cheap -- a ledger scan, not an
    API call."""
    meter = BudgetMeter(Path(logs_dir) / "budget_ledger.jsonl",
                        monthly_cap_usd=PIPELINE_CAP_USD, agent="pipeline")
    return meter.month_spend()


def _gauntlet_orphans(registry: Registry) -> list[str]:
    """Strategy ids in state 'gauntlet' that ALREADY carry a chained gauntlet
    verdict -- the exact condition pipeline/gauntlet.py refuses on (exit 1,
    "ORPHANED: gauntlet verdicts without state changes"), typically left by a
    mid-run crash.

    Detected here, next to the pre-spend chain verify, because the refusal is
    unconditional and happens at the END of the cycle: without this the loop
    pays for triage and BOTH composer calls (~$4.20 a fire, ~$12.60/day at
    three fires) to arrive at a guaranteed exit 1, every cycle, until a human
    repairs the chain. The condition is mirrored from gauntlet.py rather than
    imported because gauntlet computes it inside run() after argument
    parsing; if that check ever moves to a shared helper, both should use it.
    """
    states = registry.strategy_states()
    verdicted = {e["payload"]["strategy_id"] for e in registry.entries()
                 if e["entry_type"] == "verdict"
                 and e["payload"].get("stage") == "gauntlet"}
    return sorted(sid for sid, st in states.items()
                  if st == "gauntlet" and sid in verdicted)


def _budget_state(spent: float) -> str:
    """Which budget line the cycle is standing on, as a status item. The
    digest could previously only infer this from the presence of a
    budget_cap escalation, which conflates "parked at the 80% batch-stop
    line" (routine) with "parked at the hard cap" (urgent)."""
    if not pipeline_budget.may_spend(spent):
        return "hard_cap"
    if not pipeline_budget.may_start_batch(spent):
        return "batch_stop"
    return "ok"


def _budget_escalations(spent: float) -> list[str]:
    """budget_cap (a PUSH_TRIGGERS entry, interrupts the digest) only at the
    hard cap. The 80% batch-stop line parks work just as surely, but it is
    routine, not urgent -- escalating it would train Coen to ignore the
    channel exactly as pipeline_status.py's own docstring warns against."""
    return ["budget_cap"] if spent >= PIPELINE_CAP_USD else []


def _count_items(routable: dict[str, int], triggerable: dict[str, int]) -> dict[str, str]:
    """Status items for BOTH per-class counts. routable_<cls> is the
    accepted-only figure (what a composer could consume now); triggerable_
    <cls> is the accepted+pending figure the trigger actually compares
    against the watermark. Emitted together, always -- one without the other
    is how the 2026-08-28 deadlock read as healthy."""
    items = {f"routable_{c}": str(n) for c, n in routable.items()}
    items.update({f"triggerable_{c}": str(n) for c, n in triggerable.items()})
    return items


def _watermark_items(state: dict) -> dict[str, str]:
    return {f"watermark_{c}": str(e.get("watermark", 0))
            for c, e in state.get("classes", {}).items()}


def _queue_items(state: dict) -> dict[str, str]:
    """D10: sibling-queue depth per class, so a PARKED QUEUE IS VISIBLE.

    A queue that stops draining is the failure mode this mechanism can have --
    proposed variations sitting unregistered forever would be the silent drop
    the queue exists to replace, just slower. It has to be a number in the
    status file, not something a human has to open loop_state.json to see.
    Emitted for every class with a state entry, depth 0 included: a series
    that only appears when it is non-zero cannot show a queue draining."""
    items = {f"queue_{c}": str(n)
             for c, n in loop_state.queue_depths(state).items()}
    # sibling_queue_dead: queued specs the registry refuses outright (their
    # cited card lost its acceptance while they waited). Reported ONLY when
    # non-zero -- unlike queue_<cls>, whose zero is informative, a permanent
    # zero here would be noise on every class forever, and any non-zero value
    # is a human action item rather than a level to watch drain.
    items.update({f"queue_dead_{c}": str(n)
                  for c, n in loop_state.dead_queue_depths(state).items() if n})
    return items


def _rotation_assets(asset_class: str) -> list[str]:
    """This class's ACTIVE assets, deduplicated, in declared order.

    Order is part of the declaration (cells.py's _assert_gate_axes says so and
    enforces it at import): the rotation cursor indexes this list, so a
    reordering would move every window, not just rename it."""
    out: list[str] = []
    for asset, _tf in cells.active_cells(asset_class):
        if asset not in out:
            out.append(asset)
    return out


def _sweep_window(state: dict, asset_class: str) -> tuple[list[str], bool]:
    """(window, rotates) -- the assets this generation may sweep, and whether
    rotation is actually restricting anything.

    `rotates` False means the window IS the whole active set, so the loop
    passes no `--assets` at all and the composer invocation is byte-identical
    to its pre-D6 form. That is the state every class is in today: the four
    tradfi classes are not in ROTATION_CLASSES, and crypto's active set is
    empty.

    TWO gates, and the second one is not redundant (P2-T4 review F2).
    ROTATION_CLASSES says which classes SHOULD rotate; the routing dispatch
    says which classes CAN accept a window at all -- `--assets` is a view onto
    active cells, and the legacy pooled expander has none, so the composer
    refuses it. crypto is in ROTATION_CLASSES today AND still pooled, so
    without this second gate a Phase 3 commit that populated
    ACTIVE_CELLS["crypto"] without also switching expander_for would emit a
    window into a composer guaranteed to exit 1 -- stage_failed and a Sentinel
    FAIL on every crypto fire, three times a day. Reading the dispatch makes
    rotation switch on in the SAME commit that makes it legal. The loud
    failure for a half-landed Phase 3 lives in the test suite
    (test_phase2_freeze.py's activation-simulation tests), which is where a
    coupling error should be caught -- not in a live crash loop."""
    assets = _rotation_assets(asset_class)
    if (asset_class not in ROTATION_CLASSES
            or expander_for(asset_class) is expand_family):
        return assets, False
    window = loop_state.rotation_window(state, asset_class, assets, ROTATION_SIZE)
    return window, window != assets


def _deadline_items(registry_path: Path) -> dict[str, str]:
    """Status items from the stages' deadline reports. A stage that reported
    contributes deferred_<stage>; any stage that stopped at its deadline sets
    stopped_at_deadline to the stage name(s). A stage that did not report
    contributes nothing -- absence is not a claim either way."""
    items: dict[str, str] = {}
    stopped: list[str] = []
    for stage in ("screen", "gauntlet"):
        r = _deadline.read_result(registry_path, stage)
        if r is None:
            continue
        items[f"deferred_{stage}"] = str(int(r.get("deferred", 0)))
        if r.get("stopped_at_deadline"):
            stopped.append(stage)
    if stopped:
        items["stopped_at_deadline"] = ",".join(stopped)
    return items


def _write_status(logs_dir: str | Path, outcome: str, *, overall: str = "OK",
                  extra: dict[str, str] | None = None, spent: float = 0.0,
                  escalations: list[str] | None = None,
                  state: dict | None = None,
                  counts: tuple[dict[str, int], dict[str, int]] | None = None) -> None:
    """`counts` is (routable, triggerable). Passed on EVERY path that has
    them, not just no_trigger: CLAUDE.md documents both series as always
    present, and a digest that can only see them when nothing fired cannot
    tell an undrained pending backlog from a healthy one. Paths that run
    before the counts are computed (the lock probes, loop_crashed) legitimately
    omit them -- they have no chain read to report."""
    items: dict[str, str] = {"outcome": outcome,
                             # Emitted on EVERY path, not just the three
                             # budget-blocked ones -- otherwise "ok" is a
                             # documented value that never actually appears
                             # in a status file (2026-08-29 review).
                             "budget_state": _budget_state(spent)}
    if state is not None:
        items.update(_watermark_items(state))
        items.update(_queue_items(state))
    if counts is not None:
        items.update(_count_items(*counts))
    items.update(extra or {})
    payload = pipeline_status.build({"loop": overall}, spent, escalations)
    payload["items"] = {**payload.get("items", {}), **items}
    payload["overall"] = overall
    payload["summary"] = f"loop: {outcome}"
    pipeline_status.write(Path(logs_dir) / "pipeline_status.json", payload)


def _stage(runner: Runner, argv: list[str], cwd: str | Path) -> int:
    print(f"loop: running {' '.join(argv)}", flush=True)
    return runner(argv, cwd=str(cwd)).returncode


def _abort_stage_failed(logs_dir: str | Path, state: dict, asset_class: str,
                        module_key: str, rc: int, counts=None) -> int:
    print(f"loop: stage {module_key} failed rc={rc}, aborting cycle", flush=True)
    _write_status(logs_dir, "stage_failed", overall="FAIL",
                  extra={"asset_class": asset_class, "failed_stage": module_key,
                         "exit_code": str(rc)},
                  spent=_spent(logs_dir), escalations=["run_aborted"], state=state,
                  counts=counts)
    return 1


def _composer_rc_or_park(logs_dir: str | Path, state: dict, asset_class: str,
                         module_key: str, rc: int, state_path: Path | None = None,
                         counts=None) -> int:
    """After EITHER composer invocation (the --dry-run preflight or the real
    run) exits nonzero, decide whether this is composer's OWN hard-cap
    refusal (propose_families's meter.can_spend() check raises SystemExit,
    which the dry-run preflight hits too -- composer.run() calls the metered
    propose_families before it ever branches on --dry-run) -- which must map
    to a budget park, never a stage defect -- or a genuine composer crash.

    Deliberately narrower than the proactive pre-triage/post-triage gates
    (which use may_start_batch, the 80% batch-stop line, to decide whether
    to START new work): this is a REACTIVE check explaining why composer
    itself already failed, and composer's own guard only fires at the true
    100% hard cap (pipeline_budget.may_spend). A failure in the 80-100% band
    with no cap-crossing spend is a real defect, not a park."""
    post_spent = _spent(logs_dir)
    if not pipeline_budget.may_spend(post_spent):
        msg = (f"deferred_budget: {module_key} exited nonzero (rc={rc}) with "
               f"pipeline spend USD {post_spent:.2f} at/above the hard cap "
               f"-- treating as a budget park, not a stage defect")
        print(msg, flush=True)
        # Same starvation rule as the proactive gates: a park banks no
        # watermark, so without a rotation stamp this class monopolises every
        # subsequent pick until the budget frees up.
        if state_path is not None:
            loop_state.record_park(state, asset_class, ts_utc=_now_utc())
            loop_state.save(state_path, state)
        _write_status(logs_dir, "deferred_budget", overall="WARN",
                      extra={"asset_class": asset_class},
                      spent=post_spent, escalations=_budget_escalations(post_spent),
                      state=state, counts=counts)
        return 0
    return _abort_stage_failed(logs_dir, state, asset_class, module_key, rc, counts)


def _acquire_instance_lock_or_break_dead(instance_lock: ChainLock) -> bool:
    """True if the instance lock is now held by THIS run and the cycle may
    proceed; False means defer.

    A hard kill (the scheduled task's execution-time-limit), a reboot, or an
    acquire-then-crash can orphan loop.lock. Left unbroken, deferred_instance
    would recur forever at overall=OK -- a wedge that reads as healthy
    indefinitely. A LIVE holder still defers unconditionally however old the
    lock is: age alone is not evidence of death (a slow but legitimate
    cycle). Only a holder whose recorded pid is provably not running
    licenses a break, and a single strike suffices here -- unlike
    chain.lock's ambiguous holders, a dead pid is decisive, not merely
    suspicious."""
    try:
        instance_lock.acquire()
        return True
    except ChainLockHeld:
        pass
    if not (instance_lock.is_stale() and not instance_lock.holder_alive()):
        print("deferred_instance: another loop instance holds loop.lock, deferring",
             flush=True)
        return False
    try:
        instance_lock.break_stale()
    except ChainLockHeld:
        print("deferred_instance: loop.lock changed hands during a "
             "dead-holder break, deferring", flush=True)
        return False
    try:
        instance_lock.acquire()
        return True
    except ChainLockHeld:
        print("deferred_instance: lost the race to reclaim loop.lock after "
             "breaking a dead holder, deferring", flush=True)
        return False


def _break_stale_chain_lock_or_defer(probe: ChainLock, state: dict, state_path: Path,
                                     logs_dir: Path) -> bool:
    """Attempt to break a chain.lock the caller has already confirmed
    is_stale(), and clear the two-strike bookkeeping. Returns True when the
    cycle may proceed (broken cleanly, or vanished on its own); False means
    the caller must return 0 -- a race left a FRESH lock in its place
    (write_status is done here so every break-attempting caller, dead-pid
    fast path and two-strike alike, gets identical race handling)."""
    try:
        probe.break_stale()
        broke = True
    except ChainLockHeld:
        broke = False
    if broke:
        loop_state.clear_stale_lock(state)
        loop_state.save(state_path, state)
        return True
    # Race: something changed between our is_stale() read and break_stale()
    # (which re-checks is_stale() itself).
    fresh_info = probe.info()
    if fresh_info is None:
        # The stale holder released it entirely in the gap -- there is no
        # lock left to defer to; proceed exactly as a successful break
        # would have. NOT prefixed "deferred_lock:" -- this path does not
        # defer, and the greppable-token convention would miscount it.
        print("stale_lock_vanished: chain.lock vanished on its own before "
             "the break -- proceeding", flush=True)
        loop_state.clear_stale_lock(state)
        loop_state.save(state_path, state)
        return True
    holder = fresh_info.get("holder")
    msg = (f"deferred_lock: chain.lock changed hands mid-break "
           f"(now held by {holder!r}), deferring")
    print(msg, flush=True)
    _write_status(logs_dir, "deferred_lock",
                  extra={"lock_holder": str(holder), "lock_stale": "false"},
                  spent=_spent(logs_dir), state=state)
    return False


def _seed_watermarks(layer: Path, registry_path: Path, state_path: Path) -> int:
    """ACTIVATION step (--seed-watermarks): initialise every LIVE_CLASSES
    watermark to the CURRENT triggerable (accepted+pending) count. Without
    this, a fresh loop_state.json reads every class's watermark as 0, so the
    first scheduled fire after activation would treat the entire existing
    corpus as "new" and trigger a whole-corpus generation for every
    over-threshold class simultaneously -- this seeds the baseline so only
    genuinely NEW cards (registered after activation) ever count toward a
    trigger.

    Seeds on the TRIGGERABLE basis, not the accepted-only one, for the same
    reason the post-cycle watermark does (loop_state's BASIS WARNING): a
    watermark measured on a different basis than the trigger reads does not
    suppress anything -- an accepted-only seed against an accepted+pending
    trigger would leave the whole pending backlog counting as "new" and fire
    on the very first run it was meant to hold back.

    Deliberately narrow: a pure read of the registry (no chain.lock -- read
    paths never take it, chainlock.py's own rule) plus a loop_state.json
    write. No stage runs, no pipeline_status.json is written, and the run_id
    recorded is the literal string "seed" so a seeded watermark is always
    distinguishable from a real generation's run_id in loop_state.json."""
    registry = Registry(registry_path)
    counts = _triggerable_counts(registry)
    state = loop_state.load(state_path)
    now = _now_utc()
    for cls in cells.LIVE_CLASSES:
        n = counts.get(cls, 0)
        loop_state.record_generation(state, cls, run_id="seed",
                                     watermark_count=n, ts_utc=now)
        print(f"seeded {cls} watermark={n}", flush=True)
    loop_state.save(state_path, state)
    return 0


def run(argv: list[str] | None = None, runner: Runner = subprocess.run) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true",
                      help="run a single trigger-check cycle (the only mode "
                           "implemented; a daemon/scheduling loop is not this "
                           "module's job -- the OS scheduler owns cadence)")
    mode.add_argument("--seed-watermarks", action="store_true",
                      help="ACTIVATION: seed every LIVE_CLASSES watermark to "
                           "the current triggerable (accepted+pending) count "
                           "instead of running a cycle; see module docstring")
    ap.add_argument("--layer", type=Path, default=LAYER_DEFAULT,
                    help="research-layer root (holds registry_log.jsonl and logs/)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report whether a class would fire; run nothing")
    args = ap.parse_args(argv)

    layer = Path(args.layer)
    logs_dir = layer / "logs"
    registry_path = layer / "registry_log.jsonl"
    state_path = logs_dir / "loop_state.json"
    logs_dir.mkdir(parents=True, exist_ok=True)

    if args.seed_watermarks:
        return _seed_watermarks(layer, registry_path, state_path)

    # Window check BEFORE the cycle: a killed cycle leaves no evidence, so
    # the only place this can be said is up front, in the run log.
    _warn_if_task_window_too_short()

    # Deadline for this cycle's chain-writing stages (Phase 3 step 3). Only
    # when the live task window is known: no task (tests, a hand run, a
    # fresh clone) means no deadline and byte-identical stage argv.
    args.deadline_utc = None
    window_s = _live_task_window_s()
    if window_s is not None and window_s > SAFETY_MARGIN_S:
        from datetime import timedelta
        d = _deadline._wall_now_utc() + timedelta(seconds=window_s - SAFETY_MARGIN_S)
        args.deadline_utc = d.replace(microsecond=0).isoformat()
        print(f"loop: cycle deadline {args.deadline_utc} (task window "
              f"{window_s // 60} min - {SAFETY_MARGIN_S // 60} min margin)", flush=True)

    try:
        return _run_cycle(args, runner, layer, logs_dir, registry_path, state_path)
    except Exception as exc:                        # unattended -- never silent
        traceback.print_exc()
        print(f"loop_crashed: {exc}", flush=True)
        try:
            _write_status(logs_dir, "loop_crashed", overall="FAIL",
                          extra={"error": str(exc)[:200]},
                          spent=_spent(logs_dir), escalations=["run_aborted"])
        except Exception:
            # A corrupt ledger or an unwritable logs dir must not swallow
            # the ORIGINAL crash -- its traceback is already on record above
            # either way.
            traceback.print_exc()
        return 1


def _run_cycle(args, runner: Runner, layer: Path, logs_dir: Path,
               registry_path: Path, state_path: Path) -> int:
    # loop_state.json is a pure read (atomic tmp+replace writer, so a
    # concurrent writer never leaves a torn file to read) -- safe before the
    # instance lock below.
    state = loop_state.load(state_path)

    # -- 0. instance guard: one loop.run() at a time, for the WHOLE cycle --
    # Protects the supervised-session-vs-scheduled-fire overlap (a human
    # running `--once` by hand while the scheduled task also fires).
    instance_lock = ChainLock(logs_dir, holder="loop-instance",
                              purpose=f"run start {_now_utc()}",
                              name="loop.lock", stale_after_s=2 * 3600)
    if not _acquire_instance_lock_or_break_dead(instance_lock):
        # _acquire_instance_lock_or_break_dead is the single printer for
        # every sub-case (live holder, race-during-break, lost-reacquire) so
        # each defer logs once with its actual cause, not a generic line
        # here on top of it.
        # ALWAYS WARN, never OK: this is the one deferral that can persist
        # indefinitely (a live sibling instance is not a transient race like
        # a chain-write window), so it must never read as healthy.
        _write_status(logs_dir, "deferred_instance", overall="WARN",
                      spent=_spent(logs_dir), state=state)
        return 0
    try:
        return _run_locked_cycle(args, runner, layer, logs_dir, registry_path,
                                 state_path, state)
    finally:
        instance_lock.release()


def _run_locked_cycle(args, runner: Runner, layer: Path, logs_dir: Path,
                      registry_path: Path, state_path: Path, state: dict) -> int:
    # Normalise/create the state file up front so every return path below
    # (including a stage failure before any watermark mutation) leaves a
    # readable logs/loop_state.json on disk.
    loop_state.save(state_path, state)

    # -- 1. foreign chain-lock probe (pure info() read, never an acquire) --
    probe = ChainLock(logs_dir, holder="loop", purpose="probe")
    info = probe.info()
    if info is not None:
        if probe.is_stale():
            if not probe.holder_alive():
                # Dead-pid fast path (mirrors loop.lock's
                # _acquire_instance_lock_or_break_dead): a hard-killed loop
                # (the scheduled task's ExecutionTimeLimit), a reboot, or a crashed
                # writer orphans chain.lock with a provably-dead pid. Left to
                # the ordinary two-strike rule, that freezes scanner card
                # registration, quarantine's daily write phase, AND the
                # loop's own chain writes for up to 3h (STALE_AFTER_S) plus
                # two scheduled fires before anyone breaks it. A dead pid is
                # decisive, not merely suspicious -- break it on the FIRST
                # sighting instead, exactly like the loop.lock guard.
                if not _break_stale_chain_lock_or_defer(probe, state, state_path, logs_dir):
                    return 0
                # fall through: the cycle proceeds under a now-clear lock
            else:
                # Holder is alive (or liveness is unknown/unreadable) --
                # keep the existing, more cautious two-strike rule: a stale-
                # but-plausibly-legitimate writer only loses its lock after a
                # SECOND sighting, never the first.
                second_strike = loop_state.record_stale_lock(state, info)
                loop_state.save(state_path, state)
                if second_strike:
                    if not _break_stale_chain_lock_or_defer(probe, state, state_path, logs_dir):
                        return 0
                    # fall through: the cycle proceeds under a now-clear lock
                else:
                    msg = (f"deferred_lock: chain.lock STALE (holder="
                           f"{info.get('holder')!r}), first sighting -- WARN, "
                           f"deferring; will break on next stale sighting")
                    print(msg, flush=True)
                    _write_status(logs_dir, "deferred_lock", overall="WARN",
                                  extra={"lock_holder": str(info.get("holder")),
                                         "lock_stale": "true"},
                                  spent=_spent(logs_dir), state=state)
                    return 0
        else:
            # A fresh foreign lock at CYCLE START is routine (a human
            # session or the scanner happened to be mid-write when the loop
            # fired) -- overall stays OK, unlike the mid-cycle case below
            # where the loop itself unexpectedly loses a lock it believed
            # was free, which is WARN.
            msg = f"deferred_lock: chain.lock held by {info.get('holder')!r}, deferring"
            print(msg, flush=True)
            _write_status(logs_dir, "deferred_lock",
                          extra={"lock_holder": str(info.get("holder")),
                                 "lock_stale": "false"},
                          spent=_spent(logs_dir), state=state)
            return 0
    else:
        # No lock on disk: any previously-recorded stale-lock strike is moot
        # (that lock is gone, broken by us or released by its holder).
        if state.get("stale_lock") is not None:
            loop_state.clear_stale_lock(state)
            loop_state.save(state_path, state)

    # -- 2. trigger check ----------------------------------------------------
    registry = Registry(registry_path)
    # Two counts, two jobs: triggerable (accepted+pending) DECIDES, routable
    # (accepted-only) REPORTS. Both go to status so the digest never has to
    # guess which number fired the cycle -- and so a large routable/
    # triggerable gap (an undrained pending backlog) is visible rather than
    # inferred.
    routable_counts = _routable_counts(registry)
    triggerable_counts = _triggerable_counts(registry)
    # *** Reverting this to routable_counts re-introduces the 2026-08-28
    # deadlock: accepted-only counts cannot move between fires (nothing
    # outside a cycle triages), so every fire reports no_trigger at exit 0 --
    # a wedge that reads as healthy. Pinned by
    # test_pending_cards_alone_fire_a_cycle. ***
    asset_class = loop_state.pick_class(state, triggerable_counts)
    if asset_class is None:
        print("no_trigger: no live class is over threshold", flush=True)
        _write_status(logs_dir, "no_trigger",
                      spent=_spent(logs_dir), state=state,
                      counts=(routable_counts, triggerable_counts))
        return 0

    # -- 3. budget gate (before ANY metered stage may start) -----------------
    spent = _spent(logs_dir)
    if not pipeline_budget.may_start_batch(spent):
        msg = (f"deferred_budget: pipeline spend USD {spent:.2f} is at/above "
               f"the batch-start threshold -- parking the {asset_class} cycle")
        print(msg, flush=True)
        # Rotate this class to the back before writing status: a park banks no
        # watermark (no work was done), so without this the same class is
        # re-selected on every fire and every other over-threshold class
        # starves behind it for as long as the budget stays parked.
        loop_state.record_park(state, asset_class, ts_utc=_now_utc())
        loop_state.save(state_path, state)
        _write_status(logs_dir, "deferred_budget", overall="WARN",
                      extra={"asset_class": asset_class},
                      spent=spent, escalations=_budget_escalations(spent), state=state,
                      counts=(routable_counts, triggerable_counts))
        return 0

    # Counts as of the trigger decision. Re-read after any chain-writing
    # stage so a late status line reports what is on the chain NOW, not what
    # was there before triage wrote to it.
    trigger_counts = (routable_counts, triggerable_counts)

    def _fresh_counts():
        return (_routable_counts(registry), _triggerable_counts(registry))

    routable_count = routable_counts[asset_class]
    triggerable_count = triggerable_counts[asset_class]
    if args.dry_run:
        print(f"dry_run_would_fire: {asset_class} would trigger "
              f"(triggerable={triggerable_count}, routable={routable_count})",
              flush=True)
        _write_status(logs_dir, "dry_run_would_fire",
                      extra={"asset_class": asset_class,
                             "routable_count": str(routable_count),
                             "triggerable_count": str(triggerable_count)},
                      spent=spent, state=state, counts=trigger_counts)
        return 0

    # -- 4. run the cycle ------------------------------------------------------
    run_id = _make_run_id(asset_class)
    py = sys.executable
    reg_argv = ["--registry", str(registry_path)]
    data_argv = ["--data-dir", str(layer / "data"),
                "--artifacts-dir", str(layer / "artifacts")]

    # 4.0 chain verify BEFORE any spend (spec s6): a pre-existing invalid
    # chain must abort here, at zero metered cost, rather than let triage
    # and composer spend against a chain the loop is about to reject anyway.
    # The dirs are invariant 8's OFF-CHAIN evidence (D9 re-trial windows: the
    # burying cutoff lives in an artifact bundle, the cell data end in the
    # bars). They default to sitting beside the log, which is already true
    # here -- passed explicitly so the gate does not silently weaken to
    # "window not verifiable" if the layout ever moves.
    verify_argv = [py, str(layer / "verify_registry.py"), str(registry_path),
                   *data_argv]
    rc = _stage(runner, verify_argv, layer)
    if rc != 0:
        print(f"chain_invalid: verify_registry.py rc={rc} before triage -- "
             f"aborting, zero spend, watermark NOT advanced", flush=True)
        _write_status(logs_dir, "chain_invalid", overall="FAIL",
                      extra={"asset_class": asset_class, "exit_code": str(rc)},
                      spent=_spent(logs_dir), escalations=["chain_invalid"], state=state,
                      counts=trigger_counts)
        return 1

    # 4.0b gauntlet-orphan pre-flight, same zero-spend position as the chain
    # verify above. gauntlet.py refuses unconditionally on this condition at
    # the END of the cycle, so without this check the loop pays for triage and
    # both composer calls to reach a guaranteed exit 1 -- on every fire, until
    # a human repairs the chain.
    orphans = _gauntlet_orphans(registry)
    if orphans:
        print(f"gauntlet_orphan: {len(orphans)} strategy(ies) in state "
              f"'gauntlet' already carry a chained gauntlet verdict -- "
              f"gauntlet.py will refuse. Repair the chain before the loop can "
              f"run: {', '.join(orphans)}", flush=True)
        _write_status(logs_dir, "gauntlet_orphan", overall="FAIL",
                      extra={"asset_class": asset_class,
                             "orphans": ", ".join(orphans),
                             "orphan_count": str(len(orphans))},
                      spent=_spent(logs_dir), escalations=["chain_invalid"],
                      state=state, counts=trigger_counts)
        return 1

    entries_before = _entry_count(registry_path)
    routable_before_triage = routable_counts[asset_class]
    # How much of this class's trigger was PENDING work (vs already-accepted
    # cards). Only a pending-driven fire can produce the "triage accepted
    # nothing, composer sees an unchanged corpus" case the guard below
    # catches; a fire driven by accepted growth (a human T3 session, or a
    # previous cycle) already has new routable input and must proceed.
    pending_routable_before = (triggerable_counts[asset_class]
                               - routable_before_triage)

    def _lock_and_run(module_key: str, stage_argv: list[str]) -> tuple[int | None, bool]:
        """(rc, lock_lost). rc is None when the lock could not be acquired."""
        lock = ChainLock(logs_dir, holder="loop", purpose=f"{run_id} {module_key}")
        try:
            lock.acquire()
        except ChainLockHeld:
            return None, True
        try:
            return _stage(runner, stage_argv, layer), False
        finally:
            lock.release()

    def _defer_midcycle_lock(module_key: str) -> int:
        print(f"deferred_lock: chain.lock taken out from under the loop before "
             f"{module_key} -- deferring mid-cycle", flush=True)
        _write_status(logs_dir, "deferred_lock", overall="WARN",
                      extra={"asset_class": asset_class, "at_stage": module_key,
                             "lock_stale": "false"},
                      spent=_spent(logs_dir), state=state, counts=_fresh_counts())
        return 0

    # 4a. triage --apply (chain-writing, metered). --limit bounds worst-case
    # stage spend against an unbounded pending backlog -- unlimited, a single
    # stage could carry spend from just under the cap to the $20 hard cap in
    # one go; overflow just waits for the next fire (3x daily).
    #
    # 40, not 200 (2026-08-29): the limit must fit the scheduled task's
    # ExecutionTimeLimit, or Windows hard-kills the cycle mid-flight, the
    # watermark never advances, and the class re-fires forever paying full
    # freight each time. At the measured 3.85 s/call x 3-reviewer panel, 200
    # cards is ~38.5 min of triage alone -- marginal even inside the XML's
    # PT2H once the composer pair, screen and gauntlet are added, and fatal
    # against the PT1H the live task actually carries. 40 cards is ~7.7 min.
    # See TRIAGE_LIMIT.
    # Clear any previous cycle's result first: a stale file read as this
    # cycle's count would bank work that did not happen -- the same error as
    # banking un-reviewed cards, arrived at from the other side.
    try:
        (logs_dir / TRIAGE_RESULT_NAME).unlink()
    except FileNotFoundError:
        pass
    triage_argv = [py, "-m", "pipeline.triage_batch", *reg_argv, "--apply",
                   "--limit", str(TRIAGE_LIMIT)]
    rc, lock_lost = _lock_and_run("pipeline.triage_batch", triage_argv)
    if lock_lost:
        return _defer_midcycle_lock("pipeline.triage_batch")
    if rc != 0:
        return _abort_stage_failed(logs_dir, state, asset_class, "pipeline.triage_batch",
                                   rc, _fresh_counts())

    # Watermark truth, on the TRIGGERABLE basis -- it must match what
    # pick_class reads above or the comparison is apples-to-oranges (see
    # loop_state's BASIS WARNING). Measured right after triage, not after
    # screen/gauntlet (and possibly a foreign writer) have also run.
    #
    # What this number means: triage has just moved every card it handled out
    # of pending -- accepted ones stay in the triggerable set, rejected ones
    # leave it -- so this is the honest "cards seen and dispositioned as of
    # this cycle". The next fire therefore needs genuinely NEW cards to cross
    # the threshold again, which is the whole point of a watermark.
    #
    # Advance by what triage REVIEWED, never to the whole triggerable total
    # (fixed 2026-08-31). Triage reviews at most TRIAGE_LIMIT cards; banking
    # the rest put cards the panel never saw behind the watermark, where they
    # stopped counting toward the next trigger and only moved as genuinely
    # NEW cards arrived. Live shape when found: watermark 655, triggerable
    # 1250, limit 200 -- one cycle banked all 1250 and stranded ~395.
    #
    # Capped at the triggerable total so the watermark can never claim more
    # cards than exist: a rejected card leaves the triggerable set, so
    # before + reviewed can legitimately overshoot.
    triggerable_after_triage = _triggerable_counts(registry)[asset_class]
    reviewed = _triage_reviewed(logs_dir)
    if reviewed is None:
        # A successful --apply run always reports. Absent means the contract
        # broke, and the two failure directions are NOT symmetric: not
        # advancing costs a re-triage, which is loud and bounded, while
        # over-advancing strands cards silently and permanently. Bias to
        # re-work, and say so.
        print(f"WARN: pipeline.triage_batch reported no reviewed count for "
              f"{asset_class} ({TRIAGE_RESULT_NAME} absent or unreadable). "
              f"NOT advancing the watermark -- these cards will be re-triaged "
              f"next fire rather than stranded behind a watermark that never "
              f"saw them.", flush=True)
        watermark_after_triage = (state["classes"].get(asset_class, {})
                                  .get("watermark", 0))
    else:
        # Bank everything EXCEPT the pending cards no panel has seen.
        #
        # Not `watermark_before + reviewed`: a fire driven by already-ACCEPTED
        # growth (a human T3 session, or a prior cycle) has nothing for triage
        # to review, so that form would advance by zero and re-fire the class
        # forever on cards that are already dispositioned.
        #
        # Escalated cards are subtracted as reviewed, not as unseen: the panel
        # saw them and the cost was paid: they are deliberately left pending
        # for Coen's T3, and the skip-set stops them being re-reviewed. Left
        # in the unseen count they would re-fire the loop every cycle for work
        # nothing will do.
        n_reviewed, n_skipped = reviewed
        unseen = max(0, pending_routable_before - n_reviewed - n_skipped)
        watermark_after_triage = triggerable_after_triage - unseen

    # 4a-bis. "No new information, no new trials" (spec Decision 2). The
    # accepted+pending trigger basis means a class can fire on pending cards
    # that triage then rejects or escalates wholesale -- leaving the composer
    # exactly the routable corpus it already swept last time, for two metered
    # calls and a full screen+gauntlet pass. Stop here, at zero further cost.
    #
    # Gated on pending_routable_before: a class whose trigger came from
    # ALREADY-ACCEPTED growth (a human T3 session, or a prior cycle) has
    # genuinely new routable input for the composer even though this cycle's
    # triage added nothing, and must not be stopped.
    #
    # The baseline is "routable as of the last cycle whose composer actually
    # SWEPT", not this cycle's pre-triage count (2026-08-29 review). Scoping
    # it to one cycle stranded genuinely new cards: cycle 1 accepts 30 then
    # the composer fails (watermark not advanced), cycle 2 re-fires, triage
    # adds nothing, the guard fires on 30 -> 30 and banks the watermark, and
    # those 30 accepted cards then wait for 25 brand-new ones. Same shape
    # whenever the class-blind triage accepts cards for a class other than
    # the one that fired. Falls back to the pre-triage count when the key is
    # absent (a state file written before this change, or a class that has
    # never completed a generation).
    #
    # The watermark still advances: those cards WERE seen and dispositioned,
    # so they must not re-fire this class on the next tick. This is a
    # completed cycle that happened to produce no new routable input, not a
    # deferral -- overall stays OK and the exit code stays 0.
    routable_after_triage = _routable_counts(registry)[asset_class]
    swept_baseline = state.get("classes", {}).get(asset_class, {}).get(
        "routable_at_last_generation")
    if swept_baseline is None:
        swept_baseline = routable_before_triage
    #
    # D10 EXEMPTION: a class with a non-empty sibling queue is NOT stopped
    # here. "No new information, no new trials" is about proposing NEW
    # families off an unchanged corpus; a queued sibling is work already
    # proposed and already counted, waiting only for capacity, and draining it
    # needs no new card at all. Without this exemption a queue could sit
    # parked indefinitely behind a class whose corpus had gone quiet -- which
    # is the silent drop D10 exists to remove, just slower. No-op today: every
    # queue is empty until the first over-cap family lands.
    queued_for_class = loop_state.queue_depth(state, asset_class)
    if (pending_routable_before > 0 and routable_after_triage <= swept_baseline
            and not queued_for_class):
        print(f"no_new_accepted_cards: triage accepted nothing new for "
              f"{asset_class} (routable {routable_after_triage} vs "
              f"{swept_baseline} at its last swept generation) -- no new "
              f"information, no new trials; watermark advanced to "
              f"{watermark_after_triage}", flush=True)
        loop_state.record_generation(state, asset_class, run_id=run_id,
                                     watermark_count=watermark_after_triage,
                                     ts_utc=_now_utc())
        loop_state.save(state_path, state)
        _write_status(logs_dir, "no_new_accepted_cards",
                      extra={"asset_class": asset_class,
                             "watermark": str(watermark_after_triage),
                             "run_id": run_id,
                             "routable_before": str(routable_before_triage),
                             "routable_after": str(routable_after_triage),
                             "routable_at_last_generation": str(swept_baseline)},
                      spent=_spent(logs_dir), state=state, counts=_fresh_counts())
        commit_cycle(registry_path, entries_before, run_id, runner)
        return 0

    # Budget re-check (plan: "before triage AND before composer"): triage may
    # itself have spent against the cap; a composer batch must not start on
    # a stale pre-triage read.
    spent = _spent(logs_dir)
    if not pipeline_budget.may_start_batch(spent):
        msg = (f"deferred_budget: pipeline spend USD {spent:.2f} is at/above "
               f"the batch-start threshold after triage -- parking before "
               f"the {asset_class} composer run")
        print(msg, flush=True)
        loop_state.record_park(state, asset_class, ts_utc=_now_utc())
        loop_state.save(state_path, state)
        _write_status(logs_dir, "deferred_budget", overall="WARN",
                      extra={"asset_class": asset_class},
                      spent=spent, escalations=_budget_escalations(spent), state=state,
                      counts=_fresh_counts())
        return 0

    # 4b-pre. D6 sweep rotation: this generation's window of the class's
    # ACTIVE assets. `rotates` False (every class, today) means the window is
    # the whole active set and NO --assets is passed -- the composer argv is
    # byte-identical to its pre-D6 form, which is the Phase 2 sweep freeze.
    window, rotates = _sweep_window(state, asset_class)
    assets_argv = ["--assets", ",".join(window)] if rotates else []
    if rotates:
        print(f"loop: rotation window for {asset_class}: {len(window)} of "
              f"{len(_rotation_assets(asset_class))} active asset(s) this "
              f"generation (D6 -- a schedule, not a filter)", flush=True)

    # 4b. composer --dry-run preflight (no chain write, no lock). NOTE:
    # composer.run() calls the metered propose_families BEFORE it ever
    # branches on --dry-run, so a hard-cap refusal can surface HERE, not
    # only on the real run below -- _composer_rc_or_park covers both calls.
    # NB no --data-dir/--loop-state: the composer derives both from
    # --registry's parent, which IS `layer` here, so passing them would add
    # argv without adding meaning. Keeping the argv minimal is what makes the
    # not-rotating case byte-identical to the pre-D6 invocation.
    composer_dry_argv = [py, "-m", "pipeline.composer", *reg_argv, "--run-id",
                         run_id, "--asset-class", asset_class, *assets_argv,
                         "--dry-run"]
    rc = _stage(runner, composer_dry_argv, layer)
    if rc != 0:
        return _composer_rc_or_park(logs_dir, state, asset_class, "pipeline.composer", rc,
                                    state_path, _fresh_counts())

    # 4c. composer real run (chain-writing, metered)
    composer_argv = [py, "-m", "pipeline.composer", *reg_argv, "--run-id",
                     run_id, "--asset-class", asset_class, *assets_argv]
    rc, lock_lost = _lock_and_run("pipeline.composer", composer_argv)
    # D10: the composer runs as a SUBPROCESS and owns logs/loop_state.json's
    # sibling queues while it runs. Fold its writes back into the loop's older
    # in-memory copy NOW, BEFORE any branch below -- every one of them saves
    # state, and each would otherwise clobber exactly the queued work the
    # queue exists to preserve. Placed before the rc check on purpose: the
    # composer persists its queue only after its chain writes succeed, so a
    # nonzero exit AFTER that point still leaves a real queue on disk.
    loop_state.refresh_queues(state, state_path)
    if lock_lost:
        return _defer_midcycle_lock("pipeline.composer")
    if rc != 0:
        return _composer_rc_or_park(logs_dir, state, asset_class, "pipeline.composer", rc,
                                    state_path, _fresh_counts())

    # 4d. screen (chain-writing)
    deadline_argv = (["--deadline-utc", args.deadline_utc]
                     if getattr(args, "deadline_utc", None) else [])
    # A leftover result from a previous cycle must never read as this one's.
    for stage in ("screen", "gauntlet"):
        _deadline.result_path(registry_path, stage).unlink(missing_ok=True)
    screen_argv = [py, "-m", "pipeline.screen", *reg_argv, *data_argv, *deadline_argv]
    rc, lock_lost = _lock_and_run("pipeline.screen", screen_argv)
    if lock_lost:
        return _defer_midcycle_lock("pipeline.screen")
    if rc != 0:
        return _abort_stage_failed(logs_dir, state, asset_class, "pipeline.screen", rc,
                                   _fresh_counts())

    # 4e. gauntlet (chain-writing)
    gauntlet_argv = [py, "-m", "pipeline.gauntlet", *reg_argv, *data_argv, *deadline_argv]
    rc, lock_lost = _lock_and_run("pipeline.gauntlet", gauntlet_argv)
    if lock_lost:
        return _defer_midcycle_lock("pipeline.gauntlet")
    if rc != 0:
        return _abort_stage_failed(logs_dir, state, asset_class, "pipeline.gauntlet", rc,
                                   _fresh_counts())

    # 4f. chain verify again, post-gauntlet (spec s6): the loop's OWN writes
    # this cycle must satisfy the same invariants a human session's would.
    # Distinct from 4.0 above -- this one unambiguously attributes a break
    # to THIS cycle's stages, not to whatever was on disk before it started.
    # Read-only: no lock.
    rc = _stage(runner, verify_argv, layer)
    if rc != 0:
        print(f"chain_invalid: verify_registry.py rc={rc} after a clean "
             f"gauntlet -- aborting, watermark NOT advanced", flush=True)
        _write_status(logs_dir, "chain_invalid", overall="FAIL",
                      extra={"asset_class": asset_class, "exit_code": str(rc)},
                      spent=_spent(logs_dir), escalations=["chain_invalid"], state=state,
                      counts=_fresh_counts())
        return 1

    # -- 5. success: advance the watermark, report clean -----------------------
    # chain_growth counts ALL chain growth over the cycle (this cycle's own
    # writes AND any foreign append that landed in a gap between our own
    # stage lock windows, which are per-append-window, not whole-cycle) --
    # it is a chain-health signal, not an attribution of "specs this run
    # produced".
    chain_growth = _entry_count(registry_path) - entries_before
    # routable_at_generation is recorded ONLY here: this is the one path on
    # which the composer actually swept, so it is the only honest "last
    # swept" mark for the no_new_accepted_cards guard to compare against.
    loop_state.record_generation(state, asset_class, run_id=run_id,
                                 watermark_count=watermark_after_triage,
                                 ts_utc=_now_utc(),
                                 routable_at_generation=_routable_counts(
                                     registry)[asset_class])
    # D6: the cursor moves ONLY on a completed generation, and only when a
    # window was ACTUALLY EMITTED. A parked, failed or deferred cycle leaves it
    # exactly where it was -- advancing past a window that was never swept
    # would skip those assets silently, which is the one thing a schedule must
    # never do.
    #
    # Gated on `rotates` (the value _sweep_window returned at 4b-pre), NOT on
    # ROTATION_CLASSES membership (P2-T4 re-review, observation B). Those are
    # different questions: membership says the class SHOULD rotate, `rotates`
    # says a window was emitted this cycle. In a half-landed Phase 3 --
    # ACTIVE_CELLS["crypto"] populated, expander_for still pooled -- the
    # membership test alone walks the cursor 12 positions per completed
    # generation while `rotates` is False and nothing was ever swept from a
    # window. That is precisely the silent skip the paragraph above forbids,
    # committed by the code that forbids it.
    if rotates:
        loop_state.advance_rotation(state, asset_class,
                                    len(_rotation_assets(asset_class)),
                                    ROTATION_SIZE)
    loop_state.save(state_path, state)

    print(f"cycle_complete: {asset_class} watermark now {watermark_after_triage}",
         flush=True)
    _write_status(logs_dir, "cycle_complete",
                  extra={**_deadline_items(registry_path), "asset_class": asset_class,
                         "watermark": str(watermark_after_triage),
                         "run_id": run_id,
                         "chain_growth": str(chain_growth)},
                  spent=_spent(logs_dir), state=state, counts=_fresh_counts())

    # -- 6. scoped chain commit -- bookkeeping, never a cycle-failure cause --
    # Runs only after a clean cycle, only after the watermark and final
    # status are already on disk: a commit failure must never look like it
    # cost this cycle its success.
    commit_cycle(registry_path, entries_before, run_id, runner)

    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
