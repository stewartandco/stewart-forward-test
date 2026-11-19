---
paths:
  - "tools/reextract_shadow.py"
  - "tools/test_reextract_shadow.py"
---
## Re-extract shadow run (tools/, NOT a pipeline stage)

`python -m tools.reextract_shadow [--dry-run] [--layer DIR] [--seed N] [--sample N] [--model M] [--panel-model M]`
measures whether today's extractor beats August's on documents we already own.
Design: `docs/2026-09-06-reextract-shadow-design.md`; plan: `docs/plans/2026-09-06-reextract-shadow.md`.

- **It lives in `tools/`, never `pipeline/`, and must never be scheduled.** It is a
  measurement, not a stage.
- **Always run `--dry-run` first.** It selects and prints the seeded ten-document
  sample (five whose cards mostly passed, five that mostly stalled) with no model
  call, no spend, no lock and no file written. The sample is deterministic for a
  seed within one Python version, so the dry run shows exactly what the live run
  will read.
- **Run it from the LIVE tree, and only the live tree.** Every path derives from
  `--layer` (default: this directory): the chain, `logs/chain.lock`, the ledger and
  `docs/runs/`. A missing `logs/budget_ledger.jsonl` is a REFUSAL (exit 2), never
  "zero spend this month" - in a worktree `logs/` is gitignored and absent, and an
  unguarded run there would have passed the cycle guard, locked the wrong tree and
  metered nothing.
- **It never writes to the chain, and proves it:** size + sha256 before and after,
  on every exit path. It HOLDS `chain.lock` (holder `reextract-shadow`) for its
  duration so the resident scanner and the quarantine daily defer politely instead
  of appending mid-run; run it clear of 20:00-07:00 (the loop: one 20:00 fire,
  PT11H window, ending 07:00), 08:20 (quarantine daily) and 09:00-09:15 (Reader,
  Sentinel). The cycle guard refuses (exit 2) while `logs/loop.lock` OR
  `logs/chain.lock` exists: the loop holds loop.lock for the whole cycle, while
  screen takes chain.lock only per batch write (2026-10-08), so chain.lock alone
  no longer shows a running cycle. If the chain still moves, the report
  records it as a field and names the legitimate writers - read that before
  blaming the harness.
- **Money.** Pilot ceiling USD 3, checked before each document (the last document
  can overshoot by its own cost - cents); the monthly pipeline cap is checked
  before every extraction call and inside the panel. Spend is real and lands in the
  shared monthly pipeline cap under agent `pipeline`; a stop is reported as which
  ceiling stopped it.
- **The panel runs on `--panel-model` (default `claude-sonnet-5`, the model that
  judged every card in the chain), never on the extractor's model.** Judging with a
  different panel would change the measuring instrument inside the measurement.
  Both models are recorded in the report. `DEFAULT_PANEL_MODEL` is a hand-synced
  copy of `triage_batch.run`'s argparse default - change both or the comparison
  silently drifts.
- **The verdict is decided BEFORE the run** (design doc): green >= 2 novel accepted
  per document with a novel accept rate no worse than the old one, red < 0.5,
  amber between, NO RESULT when zero documents completed (exit 3). Both accept
  rates are over JUDGED cards: old excludes still-pending, new excludes cards a
  budget-stopped panel never reached (`novel_unjudged`).
- **The report survives an abort** (Ctrl-C, a crash, the chain moving): it is written
  from a `finally`, to `docs/runs/<date>-reextract-shadow-seed<seed>.md` plus a JSON
  sidecar, so paid work is never lost and a same-day re-run cannot overwrite it.
- **Duplicate detection is two-stage** (step 5b, added after the first pilot on
  2026-09-06 returned 0 fingerprint duplicates in 41 claims from documents holding 22
  cards): `claim_fingerprint` (normalised, not semantic) against EVERY chain card,
  then one call on the panel model per survivor asking whether it restates a card
  held from the SAME document. Restatements are counted (`restated_existing`) and
  never reach the panel. **Read `judge failures N` in the claims line first:** a
  failed judge call keeps the claim novel (fail open toward measuring) but is counted
  and WARNED, so an inert dedupe cannot masquerade as "0 restatements". Cross-document
  paraphrase remains the stated gap. An empty or missing quote is a guard FAILURE
  (`""` is a substring of everything), deliberately stricter than the Reader's
  crash-on-missing-key.
- Exit codes: 0 measured, 2 refused (lock held, no ledger), 3 no result.

