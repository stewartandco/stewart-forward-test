---
paths:
  - "pipeline/chain_mirror.py"
  - "pipeline/test_chain_mirror*.py"
  - "pipeline/test_verify_registry_segments.py"
  - "pipeline/test_loop_commit_mirror.py"
  - "pipeline/commit_list.py"
  - "verify_registry.py"
  - "registry_log.d/**"
  - ".gitignore"
  - "tasks/run_gauntlet_worker.bat"
  - "tasks/run_gauntlet_stats.bat"
  - "tasks/run_quarantine.bat"
  - "pipeline/loop.py"
---
## Chain mirror: git tracks registry_log.d/, never registry_log.jsonl (2026-10-09)
- **Why:** GitHub blocks any file over 100 MiB anywhere in pushed history; the
  live chain passed 81 MB on 2026-10-09 at ~2.9 MB/day. Design:
  `docs/2026-10-09-registry-segments-design.md`; vault note
  `project_registry_log_size_2026-10-09`.
- **The live file is unchanged** (one CRLF file, every writer and reader as
  before). `pipeline/chain_mirror.py` `sync()` copies its complete lines
  (CRLF -> LF) into `registry_log.d/000001.jsonl ...`: a segment seals at 40
  MiB on an entry boundary and is NEVER rewritten; `MANIFEST.json` records
  each sealed one (lines, bytes, sha256, first/last entry hash). Joined, the
  segments equal the live file with LF endings. `.gitattributes` stores them
  verbatim (`-text`).
- **All four committers sync first and stage `research-layer/registry_log.d`:**
  `loop.commit_cycle`, `run_gauntlet_worker.bat`, `run_gauntlet_stats.bat`,
  `run_quarantine.bat`. They detect change with `git status --porcelain` (new
  segments are untracked; `git diff` misses them). Only sync's exit 0 stages
  the mirror; a refusal (exit 3) or any other code leaves it out, everything
  else still commits, exit codes are unchanged. Refusals are items in
  `logs/degraded_commit.json` (writer `commit`, source `chain_mirror`), read
  by the Sentinel's `research_degraded` check (manifest key `commit_ledger`).
- **A refusal is a chain incident, not weather:** "prefix mismatch" / "not a
  prefix" means the live file was rewritten or truncated. Coen's call; never
  delete registry_log.d or hand-edit the manifest to make a sync pass.
- **Verify:** `python verify_registry.py registry_log.d` (manifest + boundary
  checks, then the normal walk; with NO path argument it reads
  `registry_log.jsonl`, or `registry_log.d` when only that exists, as in a
  public clone); `python -m pipeline.chain_mirror check` says
  MATCH / BEHIND / MISMATCH against the live file, read-only.
- **⚠ NEVER check out a commit from before the switch in the LIVE tree**
  (`checkout`/`switch`/`reset`/rebase): moving onto a commit that tracks
  registry_log.jsonl OVERWRITES the live chain with the old copy, and moving
  back DELETES it. A hand commit of the chain = `python -m pipeline.chain_mirror
  sync` then stage `research-layer/registry_log.d`; never `git add -f
  registry_log.jsonl`.
- **⚠ NEVER merge into the LIVE tree a branch that touched
  `registry_log.jsonl`.** Git sees a modify/delete conflict, writes the
  branch's copy over the (now untracked) live chain, and `git merge --abort`
  then DELETES the live file (reproduced 2026-10-09). Before ANY merge into
  live run `git diff --name-only $(git merge-base HEAD <branch>) <branch> --
  research-layer/registry_log.jsonl`: it must print NOTHING. Pipeline
  branches never touch it; one that does is never merged in the live tree.
  If the live file is ever lost, the recovery sources are the pre-switch
  backup `E:\Users\Coen\Claude\_backups\registry_log.jsonl.bak-<date>-pre-segments`
  and the segments: joined in order they are the live content, as of the last
  sync, with LF endings (LF -> CRLF gives the live bytes back).
- **⚠ NEVER run `git clean -x` or `git clean -X` in the LIVE tree.** Since the
  switch the live chain is an IGNORED file (as is all of `logs/`), and
  `-x`/`-X` delete ignored files.
- **⚠ NEVER revert or reset the switch commit** ("chain: git tracks
  registry_log.d segments; registry_log.jsonl is local"): while the ignore
  rule is active git overwrites the ignored live file with the switch-time
  blob. The rollback order is in the design doc s6: revert the MERGE, never
  the switch commit.
- **Sync refuses with "sealed segment ... on disk is missing or differs from
  the manifest":** restore that file, never delete the mirror. First `git
  checkout -- research-layer/registry_log.d/<seg>`; if the next sync still
  refuses, HEAD's copy was never the sealed one, so rebuild it from the live
  file, from `research-layer/` (the one-liner asserts the manifest's sha256
  before it writes), then sync again:
  `python -c "from pipeline import chain_mirror as cm; import hashlib, pathlib; m = cm.load_manifest('registry_log.d'); r = next(x for x in m['sealed'] if x['file'] == '<seg>'); L = cm.split_lines(cm.read_live('registry_log.jsonl')); d = b''.join(L[r['first_line'] - 1:r['last_line']]); assert hashlib.sha256(d).hexdigest() == r['sha256']; pathlib.Path('registry_log.d', r['file']).write_bytes(d)"`.
  Cause: a sync stalled more than 600 s (`LOCK_STALE_S`) lost a lock race and
  overwrote a segment another sync had just sealed. Rare, and it does NOT
  clear by itself: every later sync refuses until the file is restored.
- **Segment size has a hard ceiling, `HARD_MAX_BYTES` = 50 MiB**, that no
  manifest can raise: sync refuses a `segment_max_bytes` over it and
  `check_layout` (so the verifier) flags it and any segment over it.
- **Unpushed history still holds full-file `registry_log.jsonl` blobs up to
  81.5 MB.** They are under GitHub's 100 MiB limit, so the push works, and they
  stay in history forever. That is expected; never "fix" it with a history
  rewrite.
- The commit lists carry `research-layer/artifacts/` paths only (a line with a
  `..` segment or a backslash is dropped too: git resolves
  `artifacts/../registry_log.jsonl` to the live file), and the
  worker never hands git an empty pathspec file (`git commit
  --pathspec-from-file=<empty>` commits the WHOLE index). Wrappers test exit
  codes for EXACTLY 0 (a crash's NTSTATUS code is negative).
- Tests: `test_chain_mirror.py`, `test_verify_registry_segments.py`,
  `test_loop_commit_mirror.py`, `test_chain_mirror_wrappers.py` (RUNS copies
  of the three .bat files under cmd.exe against a scratch repo; Windows only).

