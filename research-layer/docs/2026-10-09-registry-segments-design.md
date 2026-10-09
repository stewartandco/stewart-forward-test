# Registry segments: a git-only split of registry_log.jsonl (design)

Date: 2026-10-09. Status: DRAFT for Coen's review. Decision record: Coen chose
option B (segment the chain) on 2026-10-09, then the **git-only** form of it.
Measurements and the rejected options: vault note
`project_registry_log_size_2026-10-09`. Stopgap in force until this ships:
`pipeline/commit_guard.py` (branch `fix/registry-commit-guard`).

## 1. Problem

GitHub blocks any file larger than 100 MiB anywhere in pushed history.
`registry_log.jsonl` is 81.6 MB on disk (81.5 MB as a git blob) and grows
about 2.9 MB a day, so it crosses the limit around 2026-10-16, or as early as
about 10-12 on gauntlet-heavy days. The stopgap stops it from ever being
committed above 95 MiB. That keeps the branch pushable, but it also stops the
chain from reaching git at all. This design lets the chain reach git again,
permanently, without one tracked file ever approaching the limit.

## 2. Decision and scope

- **The live file does not change.** `research-layer/registry_log.jsonl` keeps
  its path, its single-file format, its CRLF line endings, its writers
  (Registry, chainlock, every pipeline stage) and its readers (Registry
  snapshot/advance, verify_registry.py, the tools, and morpheus-hub's
  research_chain connector, services/pipeline.py and agent/runtime.py).
  Global line numbers such as "chain line 81246" keep naming the same entry.
- **Only git sees segments.** Git stops tracking `registry_log.jsonl` and
  tracks `research-layer/registry_log.d/` instead: numbered segment files plus
  a manifest. Concatenated in order, the segments are the live file with each
  line ending normalised to LF (section 3.2).
- **Out of scope:** any runtime reader or writer, Morpheus, the read
  performance of the growing live file, the size of `artifacts/` (a separate
  decision), and pushing.

## 3. Layout

### 3.1 Files

```
research-layer/registry_log.d/
  MANIFEST.json
  000001.jsonl   sealed
  000002.jsonl   sealed
  000003.jsonl   active (the only segment that may change)
```

- Segment names are zero-padded six-digit ordinals starting at 000001.
  Exactly one segment is active: the highest-numbered one.
- **SEGMENT_MAX_BYTES = 40 MiB (41,943,040 bytes).** A segment seals once
  adding the next entry would take it over that size. The cut always falls on
  an entry boundary, so no entry spans two segments. 40 MiB stays under
  GitHub's 50 MiB warning.
- **A sealed segment is never rewritten.** Its manifest record (3.3) fixes
  its bytes forever.
- An entry larger than SEGMENT_MAX_BYTES cannot be placed. Sync refuses
  (section 4.3). The largest entry today is about 7 KB.

### 3.2 Line endings (measured 2026-10-09)

The live file is CRLF: 104,350 CRLF line endings and no bare CR. Python's
text-mode writes on Windows produce that. With `core.autocrlf=true`, git
already stores the registry blob as LF, which is why the committed size is
104,350 bytes smaller than the file on disk. Segments therefore hold **LF line
endings**, and `.gitattributes` marks `research-layer/registry_log.d/** -text`
so git stores them verbatim and never converts them on checkout. Every size
and sha256 in this design is over those LF bytes. "Joined segments match the
live file" means **`concat(segments) == live_prefix.replace(b"\r\n", b"\n")`**.
That replace is exact: `canonical_json` escapes every control character inside
a value, so the only CR in the file is the one in each line terminator.
Entry hashes are computed from parsed JSON, so line endings never affect
chain validity.

### 3.3 MANIFEST.json

```json
{
  "format": "registry-segments-v1",
  "segment_max_bytes": 41943040,
  "line_terminator": "LF",
  "source": "research-layer/registry_log.jsonl (CRLF on disk)",
  "sealed": [
    {"file": "000001.jsonl", "first_line": 1, "last_line": 52011,
     "bytes": 41939876, "sha256": "...",
     "first_prev_entry_hash": "000...0", "last_entry_hash": "..."}
  ]
}
```

- `sealed` lists only sealed segments, in order. The active segment is not in
  the manifest; it is whatever the next-numbered file holds. So the manifest
  changes only when a segment seals.
- Line numbers are 1-based global line numbers of the live file, so a
  citation like "line 81246" maps to exactly one segment.
- The manifest is a convenience and a tamper check. **The proof of the chain
  is still the hash links.** The first entry of segment k+1 must carry
  `prev_entry_hash` equal to the hash of the last entry of segment k, which
  the verifier already checks when it walks the joined chain.

## 4. The mirror: `pipeline/chain_mirror.py`

### 4.1 `sync(registry_path, mirror_dir) -> SyncResult`

1. Take `FileLock(mirror_dir)`, the repo's existing lock helper. One sync at
   a time: the loop's commit and a worker run can overlap at 20:00.
2. Open the live file in binary mode. Read up to the **last complete line**
   within the size seen at open (the `_complete_lines` rule `snapshot()`
   uses), so a writer caught mid-append is never mirrored. Normalise CRLF to
   LF.
3. **Prefix check.** For every sealed segment in the manifest, the
   corresponding byte range of the normalised live prefix must have the
   recorded length and sha256. Any mismatch means the live file was rewritten
   or truncated: refuse, write nothing (4.3).
4. **Monotonic.** If the existing active segment is LONGER than what this read
   would produce (a concurrent sync with a later read already ran), keep it
   and write nothing. If it is not a prefix of what this read produces,
   refuse (4.3).
5. Split the remainder after the last sealed segment into SEGMENT_MAX_BYTES
   pieces on entry boundaries. Every full piece is sealed: write its file,
   then append its manifest record. The rest becomes the active segment.
   Every write goes to a temp file in the same directory followed by
   `os.replace`. The manifest is written last, so a crash between a segment
   write and the manifest write leaves an unsealed full-size file. The next
   sync repeats the seal identically and records it.
6. Return what changed (`sealed_new`, `active_bytes`, `lines_total`), for the
   committers' log lines.

`python -m pipeline.chain_mirror sync` is the CLI the wrappers call: exit 0
= mirror current, 3 = refused. `python -m pipeline.chain_mirror check`
reports, read-only, whether the mirror matches the live file. The switch-over
uses `check` (section 6).

### 4.2 Committers

The four paths the stopgap guards today change in the same commit:

| path | today (stopgap) | after |
|---|---|---|
| `loop._commit_cycle` | guard, then `registry_log.jsonl` in the pathspec | `sync()`, then `research-layer/registry_log.d` in the pathspec |
| `run_gauntlet_worker.bat` | guard, scope file line | `chain_mirror sync`, `research-layer/registry_log.d` in the scope file |
| `run_gauntlet_stats.bat` | guard, `commit -- registry_log.jsonl` | `chain_mirror sync`, `commit -- research-layer/registry_log.d` |
| `run_quarantine.bat` | guard, QPATHS | `chain_mirror sync`, QPATHS gets `research-layer/registry_log.d` |

- **Change detection must include untracked files.** A newly sealed segment
  and a newly started active segment are untracked, so `git diff --quiet`
  misses them. The wrappers' "is there anything to commit" test for the
  mirror becomes `git status --porcelain -- research-layer/registry_log.d`,
  where non-empty output means commit. The loop's preflight does the same
  through its runner.
- **The stopgap guard is removed from all four paths in the same change.**
  Left in, it would report the live file (over 95 MiB) as "not committed"
  forever, and its ledger item would turn the digest FAIL after 3 days.
  `commit_guard.py` and its tests are deleted, and the CLAUDE.md section is
  replaced by this design's.
- A sync that refuses or raises is a "no" for the mirror only. The rest of
  the commit (bundles, screen list, price CSVs) still commits, and no task's
  exit code changes. That is the stopgap's rule, kept.

### 4.3 Degraded ledger

`sync` rewrites `logs/degraded_commit.json` (writer `commit`, the ledger the
Sentinel already reads once `commit_ledger` is in its manifest) on every
call:
- current mirror: no items;
- refusal: one item, source `chain_mirror`, key `registry_log.d`, with the
  reason (`prefix mismatch at segment 000002`, `active segment not a prefix`,
  `entry larger than segment_max_bytes`, `cannot read registry`).

Items keep their `since_utc` across runs (degraded.py's rule), so WARN turns
into FAIL after 3 days. A refusal never resolves itself: a rewritten live
file is a chain incident, and Coen decides what happens.

## 5. Verifier and public instructions

- `verify_registry.py PATH` accepts a directory. Given `registry_log.d`, it:
  1. checks `MANIFEST.json`: format, every sealed file present with its
     recorded bytes and sha256, files numbered consecutively, exactly one
     active segment after the last sealed one;
  2. checks each sealed boundary: the next segment's first entry links to the
     recorded `last_entry_hash`, and the recorded first and last line numbers
     match the cumulative line count;
  3. walks the joined segments through the existing chain walk and invariant
     checks, unchanged, with `--artifacts-dir` / `--data-dir` defaulting
     beside the directory's parent, which is where they default today.
  A file path behaves exactly as today.
- The README's public instruction becomes `python verify_registry.py
  registry_log.d`, with one paragraph on why the chain is split, and on the
  path change in history: commits before the switch carry
  `registry_log.jsonl`, commits after carry `registry_log.d/`.
- `constellation.json`'s "trust asset at registry_log.jsonl" wording gets one
  clause on the git layout (stewartandco-agents, separate commit).

## 6. Switch-over (one-time, live tree, Coen-gated)

The code merge and the switch are one deployment. Between them nothing
commits the live file, because the guard is gone and the committers stage
`registry_log.d`.

1. Merge the branch into the live tree under the live-tree guard
   (research-layer/CLAUDE.md: the worker's last log line is an `exit` line;
   no index.lock, gauntlet_worker.lock or chain.lock; nothing staged; the
   next :00/:30 fire more than a few minutes away). Daytime, clear of 20:00
   (loop) and 08:20 (quarantine).
2. Back up the live file:
   `logs/registry_log.jsonl.bak-<date>-pre-segments`.
3. `python -m pipeline.chain_mirror sync`: the first sync writes about two
   sealed segments plus the active one, and the manifest.
4. `python -m pipeline.chain_mirror check` must report MATCH. Then
   `python verify_registry.py registry_log.d` must report VALID with the same
   entry count as `python verify_registry.py registry_log.jsonl` run at the
   same moment.
5. `git rm --cached research-layer/registry_log.jsonl`. Add
   `research-layer/registry_log.jsonl` to `.gitignore`, and the `-text` rule
   to `.gitattributes`.
6. Commit only `research-layer/registry_log.jsonl` (the removal),
   `research-layer/registry_log.d`, `.gitignore` and `.gitattributes`, by
   pathspec. Nothing else.
7. Confirm with `git show --stat HEAD` that the live file still exists on
   disk and its size is unchanged. Then confirm the next worker run commits
   `registry_log.d` (its log line).

**Rollback** (before any push): revert the merge, then `git add` the live file
back, but only while it is under the guard size. Otherwise leave it
untracked and re-apply the stopgap.

## 7. Hazards this creates (each goes into research-layer/CLAUDE.md)

- **Never check out a commit from before the switch in the LIVE tree**
  (`checkout`, `switch`, `reset`, a rebase). Moving from a commit that does
  not track `registry_log.jsonl` to one that does would overwrite the live
  file with the old committed copy, and moving back would DELETE it. Feature
  worktrees are unaffected (they do not hold the live chain). The backup in
  step 2 is the recovery copy.
- **Merging a branch cut before the switch into live** is safe as long as
  that branch never touched `registry_log.jsonl`, which pipeline branches do
  not. A branch that did touch it conflicts. Resolve by keeping the deletion
  and never taking the branch's copy.
- **A hand commit of the chain** now means `chain_mirror sync` plus staging
  `registry_log.d`, never `git add registry_log.jsonl` (gitignored, so git
  refuses it without `-f`; never use `-f` on it).
- **Unpushed history still holds full-file blobs** up to 81.5 MB. They are
  under 100 MiB, so the push works, and they stay in history forever. That
  is expected.

## 8. Testing

- `test_chain_mirror.py`:
  - first sync from empty;
  - sync across one and several seal boundaries;
  - an entry exactly at the boundary;
  - a mid-append tail (no newline) is not mirrored;
  - CRLF in, LF out, and joined segments equal the normalised live file;
  - a rewritten sealed range is refused with a ledger item and no write;
  - a truncated live file is refused;
  - a longer existing active segment is left alone (concurrent sync);
  - a crash between a segment write and the manifest write repairs on the
    next sync;
  - an oversized entry is refused;
  - the lock serialises two syncs.
- `test_verify_registry` directory mode:
  - VALID on a good mirror;
  - FAIL on a flipped byte in a sealed segment;
  - FAIL on a missing segment;
  - FAIL on a renumbered or extra active segment;
  - FAIL on a broken cross-segment link;
  - entry count equals the file mode's.
- The loop commit tests and the wrapper tests (the stopgap's
  `test_commit_guard_wrappers.py` pattern: copies of the real `.bat` run under
  cmd.exe against a scratch repo) are re-pointed at the mirror:
  - untracked new segments are committed;
  - a refused sync still commits the bundles and CSVs;
  - exit codes are unchanged.
- **Mutation check before merge:** each property above must be killed by a
  mutant (vault rule).
- **Live-shape rehearsal on a copy:** sync a COPY of the live 104k-entry chain
  into a scratch dir; check, verify (directory and file modes agree), and time
  it (target well under the worker's 25-minute budget; expected about a
  second).

## 9. Open points for Coen

- SEGMENT_MAX_BYTES = 40 MiB (proposed). Smaller means more files and
  smaller diffs; larger means fewer files, and anything over 50 MiB sets off
  GitHub's warning.
- Timing: the switch should land before the stopgap first holds the chain
  back (around 10-12..10-16). If it lands later, the switch commit simply
  carries the held-back entries. Nothing is lost either way.
