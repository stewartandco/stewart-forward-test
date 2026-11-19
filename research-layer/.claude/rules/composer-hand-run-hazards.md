---
paths:
  - "pipeline/composer.py"
  - "pipeline/test_composer*.py"
---
## ⚠ Composer hazards for HAND RUNS in the live tree
- `--loop-state` defaults to `logs/loop_state.json` next to `--registry`, so a
  real (non-dry) `python -m pipeline.composer` in the live tree now READS AND
  DRAINS THE LIVE SIBLING QUEUE and can write to the loop's state file. The
  composer never touched that file before P2-T4. Pass an explicit
  `--loop-state` (or `--dry-run`, which never mutates it) for a hand run you
  do not want interacting with the loop's queue.
- `--data-dir` likewise defaults next to `--registry`; it is read-only (D9
  cell dating) but it means a hand run against a tmp registry silently gets a
  tmp data dir, which is the intended test isolation.

