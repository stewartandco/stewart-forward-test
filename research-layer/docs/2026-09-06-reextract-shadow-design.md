# Re-extract shadow run — design

**Date:** 2026-09-06 · **Status:** approved by Coen, not yet built · **Phase:** measurement (pilot)

## The question

Does today's extractor produce **better** claims than August's, on source documents we
already own? Better means surviving the honesty guard and passing the triage panel —
not merely being more numerous.

This is a measurement, not a mining run. The deliverable is a number that decides
whether a full re-extraction (and the mechanism it would need) is worth building.

## Why this and not the alternatives

Coen ruled the purpose is **quality**, measurement-first. The two alternatives were
rejected on the evidence:

- **Volume** (mine 199 documents to unstick the loop) fails on arithmetic. It is a
  one-off burst, not a rate: it would fire each class once and leave intake back at
  ~5 cards/day. Near-duplicate cards also convert to near-duplicate strategies, which
  the composer's fingerprint guard already refuses. And manufacturing cards to trip
  the loop's own threshold sits badly beside the honest-zero claim.
- **Rescue** (re-mine the documents behind the 231 escalated cards) fails
  structurally. A card is escalated because a reviewer judged its claim asserts more
  than its quote supports; re-extracting the document produces a *different* claim —
  a new card — while the original stays pending regardless. Making it shrink the
  backlog would require auto-rejecting an escalated card when its document yields a
  replacement, which is exactly the "destroy research on a majority opinion" move
  `panel_verdict` is written to forbid.

## What the fetchability probe established (2026-09-06)

Measured with the pipeline's own path (`feeds.fetch_url` → `feeds.html_to_text` →
`common.quote_in_source`), over all 200 distinct source documents behind 1,359 cards:

| outcome | docs | cards behind them |
|---|---:|---:|
| fetched, original quote still present | 189 | 1,187 |
| HTTP 403 (all `ssrn.com`) | 10 | 167 |
| HTTP 504 (`quantpedia.com`) | 1 | 5 |

- **199 of 200 are re-mineable.** The ten 403s are the López de Prado lecture decks,
  whose PDFs are still on disk at `E:\Users\Coen\Desktop\AFML` and were read from
  there originally. Only one quantpedia page is genuinely unreachable.
- **Pages have not drifted:** 153 of the 189 still contain all three sampled quotes.
- **100% of the pending backlog is covered** — all 67 documents behind the 231
  pending cards fetch and match.
- Median document is ~12,000 characters, so extraction is cheap.

The concern that motivated this probe — that source text is retained nowhere, so
"re-extract" really means "re-fetch" and pages may have changed — turned out to be
largely unfounded. It was worth ~$0 to find that out before designing around it.

## Flow

For each sampled document:

1. **Get text the way production does.** Web sources: `feeds.fetch_url` +
   `feeds.html_to_text` (the scanner's path). Local PDFs: `reader.read_source_text`
   (the Reader's path). No new fetching or parsing code — a measurement made with a
   different extractor answers a different question.
2. **Chunk** with `reader.chunk_text`.
3. **Extract** with `reader.extract_claims_usage` (the metered form of
   `extract_claims`) at today's prompt and model, recording each call's spend under
   agent `pipeline` — the same agent the panel charges, so one meter sees both.
4. **Apply the honesty guard**: drop any claim whose quote is not in the text
   (`common.quote_in_source`), counting the drops. This is what production does —
   with one explicit tightening: **an empty or missing quote is a guard failure**,
   because `""` is a substring of every text and `quote_in_source` alone would wave
   an unsupported claim into `novel` (found in review, 2026-09-06).
5. **Fingerprint** every surviving claim with `triage_batch.claim_fingerprint` against
   **every card in the chain — accepted, rejected and pending alike.** This is
   deliberately wider than production's `find_duplicates`, which compares pending
   against accepted only: for this measurement a claim identical to one we already
   rejected is not novel either, and counting it as novel would flatter the result.
5b. **Semantic dedupe** (added 2026-09-06 after the first pilot, Coen's ruling). The
   fingerprint in step 5 is not semantic, and the first live pilot proved the point:
   0 duplicates in 41 claims from documents we already held 22 cards for — today's
   extractor simply phrases everything differently. So each fingerprint-novel claim is
   now put to one cheap model call (the PANEL model, sonnet, for consistency with the
   judge) with the claims of every card we already hold **from the same document**,
   any review state: "does this restate one of these?" A restatement is counted as
   `restated_existing` and never reaches the panel; only what survives is `novel`.
   A document with no held cards skips the call. ~USD 0.005 per claim. This is what
   makes the "novel accepted per document" figure mean novelty rather than rewording.
6. **Judge the novel ones** with the real panel: `triage_batch.build_decisions`, which
   its own docstring states "turns pending cards into a decision list without chaining
   anything". Synthetic (unchained) card dicts go in as `pending`; `accepted` is
   passed **empty** on purpose — step 5 has already deduped against every card in
   the chain on a wider rule, and handing the panel the real accepted set would make
   it re-run its narrower duplicate check and hide those duplicates from the novel
   figure.
7. **Record** everything to a dated JSON report. Nothing is chained at any point.

## Where it lives

`research-layer/tools/reextract_shadow.py` — deliberately **outside `pipeline/`** so
it can never be mistaken for a pipeline stage or picked up by the loop. It imports
production functions; it does not reimplement them.

## What it measures

Per document, and aggregated:

| field | source |
|---|---|
| `old_cards`, `old_accepted`, `old_rejected`, `old_pending` | the chain |
| `proposed` | claims returned by the extractor |
| `dropped_quote_guard` | failed `quote_in_source` |
| `duplicate_of_existing` | exact fingerprint collision with a held card |
| `restated_existing` | fingerprint-novel but judged a restatement of a held card from the
  same document (step 5b) — never sent to the panel |
| `novel` | survived the guard, the fingerprint AND the semantic check |
| `novel_accepted`, `novel_escalated` | the shadow panel |
| `novel_unjudged` | novel cards the panel never reached — production's
  `build_decisions` stops mid-batch when the meter refuses (`stopped="budget"`),
  and those cards are unjudged, not failed (found in review, 2026-09-06) |
| `escalation_reasons` | each dissenting reviewer's reason |
| `stopped` | set (e.g. `"budget"`) when the panel stopped before judging every novel
  card. A stop is NOT an error: the document was loaded, extracted and partly judged,
  so its counts stay in the aggregate. `error` means only "learned nothing" (found in
  review, 2026-09-06 — conflating the two dropped a partly-judged document entirely) |
| `text_source` | `local_pdf` or `fetched` — which path supplied the text (the SSRN
  decks come off disk; everything else is fetched) |
| `usd` | metered spend for that document — summed into `usd_total` for EVERY
  document, errored or not: money spent is spent (a document can pay for extraction
  and then fail at the panel) |

**Headline comparators:**

- novel accept rate vs the accept rate those same documents actually achieved, where
  **both rates are accepted over JUDGED cards**: old = `accepted / (accepted + rejected)`
  excluding still-pending cards, new = `accepted / (accepted + escalated)` excluding
  unjudged cards. In both, an undecided card is not a failure; counting it as one would
  understate whichever side it fell on. The pending share and the unjudged count are
  reported separately alongside;
- novel-accepted per document, and per dollar;
- overreach (escalation) share, new vs old.

## Document selection

Ten documents, drawn with a **recorded random seed**, stratified into two bands:

- documents whose existing cards mostly passed;
- documents whose existing cards mostly stalled in escalation.

The bands ask different questions (does the extractor improve on success, and does it
rescue failure?), and the seed makes the sample defensible rather than cherry-picked.
The seed and the resulting document list are written into the report.

## Decision rule — proposed, Coen may move the thresholds

Judged on novel cards that the panel **accepted**, per document:

- **Green — scale to all 199 and build `supersedes`:** ≥ 2 novel accepted per
  document, and a novel accept rate (as defined above) no worse than those documents'
  original rate.
- **Amber — revise the prompt or narrow the corpus, re-pilot:** 0.5 to 2 per document,
  or an accept rate materially below the original.
- **Red — close the question:** < 0.5 novel accepted per document. Fewer than one new
  usable card per two documents does not justify a mechanism.
- **No result:** zero documents completed (every one errored). No verdict is issued —
  a red on an empty sample would read as "close the question" when the question was
  never asked. Fix the fetch problem and re-run (found in review, 2026-09-06).

Stating this before the run is the point: it stops a disappointing result being
re-read as an encouraging one.

## Guards

- **No chain writes.** No `Registry` is opened for writing. The run proves it: the
  chain's size and sha256 are compared before and after, on every exit path — a crash
  that also moved the chain reports both, chained, never masking the original error.
- **The run holds `chain.lock` itself** (`ChainLock(holder="reextract-shadow")`) for its
  duration, exactly as the repo asks of any manual chain-adjacent session. Not to
  protect the chain — we never write it — but because the resident scanner and the
  quarantine daily append under their own brief locks, and a legitimate concurrent
  append would otherwise trip the no-write proof with a false accusation. Holding the
  lock makes them defer politely for the ~10 minutes the pilot runs. The guard's
  message names those writers anyway, in case a stale-lock break ever lets one through.
- **No `loop_state` writes**, and it refuses to start while a cycle is running (the
  loop reads state at start and saves the whole object at the end, so a concurrent
  write would be clobbered — and its own spend calibration reads spend deltas around
  its stages).
- **Budget ceiling $3**, enforced through the existing meter, not assumed. Checked
  before each document, so the true total can overshoot by at most one document's
  cost (cents) — a bound on where the last document starts, not an exact stop. September
  stands at $27.34 of the $40 cap with the batch-stop at $32; the loop parks itself on
  every fire past that line, so the pilot must not approach it.
- **Deterministic**: seed and Python version recorded, re-runnable, report written to
  `research-layer/docs/runs/<date>-reextract-shadow-seed<seed>.md` plus a JSON sidecar
  (the seed is in the filename so a same-day re-run cannot overwrite a paid result).
- **The report is written even on an abort** (Ctrl-C, a crash, the chain moving): it is
  produced from a `finally`, so paid work is never lost. A chain change during the run
  is recorded as a field in the report, not a lost report.
- **Every path derives from `--layer`**: the chain, the lock, the ledger and the report
  directory. A missing ledger is a refusal, never "zero spend this month" — in a
  worktree `logs/` is gitignored and absent, and an unguarded run there would have
  passed the cycle guard, locked the wrong tree and metered nothing (found in review).
- **Extraction checks the monthly cap** before every model call, not only the pilot
  ceiling between documents; a month at the cap refuses rather than spending through.
- **Exit codes:** 0 measured; 2 refused (lock held, no ledger); 3 no result.

## First pilot result (2026-09-06, `--sample 5`, USD 1.16)

AMBER by the rule — 6.8 novel accepted per document, novel accept rate 83% vs 86% old
(one card in 41). But `duplicate_of_existing` was 0 on 41 claims from documents holding
22 cards: the fingerprint saw no restatements because none were verbatim. The headline
was paraphrase-inflated, exactly as the limitation below warned. Clean signals: the
honesty guard dropped 0 of 41; escalations 7/41, all classic overreach; the stalled
rebalancing document yielded 9 accepted of 11. Ruling: add the semantic dedupe (step
5b) and re-pilot. Do not scale on the first pilot's numbers.

## What this does NOT isolate

The comparison is **today's extractor as a whole** versus August's — prompt, model
(`reader.DEFAULT_MODEL`, recorded in the report) and honesty guard together. **The
panel is held constant:** it runs on the model that judged every card in the chain
(`triage_batch`'s default, sonnet — the loop passes no `--model`), never on the
extractor's model. Judging shadow cards with a different panel would change the
measuring instrument inside the measurement (found in review, 2026-09-06). Both
models are recorded in the report. It is not
an ablation: a green result does not say which of the three improved, and a red result
does not exonerate any of them individually. That is the right trade for a $3
measurement, but it means the finding is "re-extraction is/isn't worth it today", not
"the new prompt is better".

## Honest limitation

`claim_fingerprint` is normalised but **not semantic** (its own docstring says so): it
catches restatements of the same sentence, not paraphrases. A paraphrase of an existing
claim therefore counts as novel and reaches the panel, inflating the novel count. The
panel is what catches it. The report shows duplicates and escalation reasons separately
so the novel figure is never read as more novelty than it is.

## Out of scope

- `supersedes` (declared at `SCHEMA.md:93`, never once written in pipeline code).
- Any change to the `known_claims` dedupe guard at `reader.py:230` / `scanner.py:192`.
- A card-addressable Reader entry point (the Reader's unit of work is a document).

Each becomes justified only if the pilot comes back green.

## Testing

TDD, per SOP phase 2:

- the harness never writes to the chain (entry count identical before/after);
- the sampler is deterministic given a seed, and respects the two strata;
- honesty-guard drops are counted, not silently discarded;
- duplicate classification matches `claim_fingerprint` semantics;
- the comparison arithmetic (rates, per-document, per-dollar) is right on fixtures;
- the budget ceiling aborts the run rather than overrunning.
